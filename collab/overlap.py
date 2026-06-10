"""Git-based cross-branch file overlap detection (advisory warnings only).

Compares files changed on the current branch against other unmerged branches so
developers see likely merge conflicts before push. Never blocks git operations.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from collab import safe_subprocess

logger = logging.getLogger(__name__)

GitCapture = Callable[[Sequence[str]], tuple[int, str]]

_DEFAULT_BASE_CANDIDATES = ("origin/main", "origin/master")
_SKIP_REF_SUFFIXES = frozenset({"HEAD"})


@dataclass(frozen=True)
class OverlapReport:
    """Files on the current branch that also differ on another unmerged branch."""

    branch: str
    files: tuple[str, ...]


def is_overlap_check_enabled() -> bool:
    """Return True unless ``COLLAB_OVERLAP_CHECK`` is explicitly disabled."""
    raw = os.getenv("COLLAB_OVERLAP_CHECK", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def is_overlap_strict_enabled() -> bool:
    """Return True when ``COLLAB_OVERLAP_STRICT`` is enabled."""
    raw = os.getenv("COLLAB_OVERLAP_STRICT", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _default_git_capture(cwd: str, args: Sequence[str]) -> tuple[int, str]:
    result = safe_subprocess.capture(
        ["git", *args],
        policy="git",
        cwd=cwd,
        timeout=30.0,
    )
    stdout = safe_subprocess.decode_output(result.stdout).strip()
    return result.returncode, stdout


def _ref_exists(capture: GitCapture, ref: str) -> bool:
    rc, _ = capture(["rev-parse", "--verify", ref])
    return rc == 0


def resolve_base_ref(capture: GitCapture) -> Optional[str]:
    """Resolve the git base ref for overlap comparison."""
    override = os.getenv("COLLAB_LOCK_BASE_REF", "").strip()
    if override and _ref_exists(capture, override):
        return override
    for candidate in _DEFAULT_BASE_CANDIDATES:
        if _ref_exists(capture, candidate):
            return candidate
    return None


def _branch_display_name(ref: str) -> str:
    if ref.startswith("origin/"):
        short = ref[len("origin/") :]
        if short:
            return short
    return ref


def _current_branch(capture: GitCapture) -> tuple[Optional[str], Optional[str]]:
    """Return ``(abbrev_ref, head_sha)`` for the checked-out branch."""
    rc_branch, branch = capture(["rev-parse", "--abbrev-ref", "HEAD"])
    rc_sha, sha = capture(["rev-parse", "HEAD"])
    if rc_branch != 0 or rc_sha != 0:
        return None, None
    if branch == "HEAD":
        return None, sha
    return branch, sha


def _changed_files_since_base(
    capture: GitCapture, tip_ref: str, base_ref: str
) -> frozenset[str]:
    rc_base, merge_base = capture(["merge-base", tip_ref, base_ref])
    if rc_base != 0 or not merge_base:
        return frozenset()
    rc_diff, names = capture(["diff", "--name-only", f"{merge_base}...{tip_ref}"])
    if rc_diff != 0 or not names:
        return frozenset()
    return frozenset(line.strip() for line in names.splitlines() if line.strip())


def _is_unmerged(capture: GitCapture, ref: str, base_ref: str) -> bool:
    rc, count = capture(["rev-list", "--count", f"{base_ref}..{ref}"])
    if rc != 0 or not count:
        return False
    try:
        return int(count.strip()) > 0
    except ValueError:
        return False


def _list_candidate_refs(capture: GitCapture) -> list[tuple[str, str]]:
    """Return ``(display_name, ref)`` pairs for local and origin branches."""
    rc, output = capture(
        [
            "for-each-ref",
            "--format=%(refname:short) %(objectname)",
            "refs/heads",
            "refs/remotes/origin",
        ]
    )
    if rc != 0 or not output:
        return []

    by_commit: dict[str, tuple[str, str]] = {}
    for line in output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        ref, commit = parts[0].strip(), parts[1].strip()
        if not ref or not commit:
            continue
        if ref.endswith("/HEAD") or ref in _SKIP_REF_SUFFIXES:
            continue
        display = _branch_display_name(ref)
        if not display or display == "HEAD":
            continue
        existing = by_commit.get(commit)
        if existing is None:
            by_commit[commit] = (display, ref)
            continue
        # Prefer local branch names over origin/* for the same commit.
        if existing[1].startswith("origin/") and not ref.startswith("origin/"):
            by_commit[commit] = (display, ref)

    return list(by_commit.values())


def detect_cross_branch_overlaps(
    repo_root: str | Path,
    *,
    git_capture: Optional[GitCapture] = None,
) -> list[OverlapReport]:
    """Detect file overlap between HEAD and other unmerged branches.

    Returns an empty list when overlap checking is disabled, git is unavailable, or no
    overlaps exist. Individual branch failures are skipped (fail-open).
    """
    if not is_overlap_check_enabled():
        return []

    cwd = str(Path(repo_root).resolve())

    def capture(args: Sequence[str]) -> tuple[int, str]:
        runner = git_capture or (lambda a: _default_git_capture(cwd, a))
        return runner(args)

    base_ref = resolve_base_ref(capture)
    if not base_ref:
        return []

    current_branch, head_sha = _current_branch(capture)
    if head_sha is None:
        return []

    changed_head = _changed_files_since_base(capture, "HEAD", base_ref)
    if not changed_head:
        return []

    base_display = _branch_display_name(base_ref)
    skip_names = {base_display, base_ref, "main", "master"}
    if current_branch:
        skip_names.add(current_branch)

    reports: list[OverlapReport] = []
    for display, ref in _list_candidate_refs(capture):
        if display in skip_names or ref in skip_names:
            continue

        rc_tip, ref_sha = capture(["rev-parse", ref])
        if rc_tip != 0 or not ref_sha:
            continue
        if head_sha and ref_sha == head_sha:
            continue

        if not _is_unmerged(capture, ref, base_ref):
            continue

        changed_other = _changed_files_since_base(capture, ref, base_ref)
        overlap = sorted(changed_head & changed_other)
        if overlap:
            reports.append(OverlapReport(branch=display, files=tuple(overlap)))

    reports.sort(key=lambda item: item.branch)
    return reports


def format_overlap_warning(report: OverlapReport) -> str:
    """Format a single overlap report as a user-facing warning line."""
    quoted = ", ".join(f"`{path}`" for path in report.files)
    verb = "is" if len(report.files) == 1 else "are"
    return (
        f"⚠️ Cross-branch overlap: {quoted} {verb} also modified on "
        f"`{report.branch}` (unmerged). Merging both will likely conflict — "
        f"coordinate merge order or rebase one onto the other."
    )


def format_warnings(reports: Sequence[OverlapReport]) -> list[str]:
    """Format all overlap reports as warning lines."""
    return [format_overlap_warning(report) for report in reports]


def warn_cross_branch_overlap(
    repo_root: str | Path | None = None,
    *,
    emit: Optional[Callable[[str], None]] = None,
) -> int:
    """Run overlap detection and emit warnings.

    Returns non-zero when overlaps exist and ``COLLAB_OVERLAP_STRICT`` is enabled.
    """
    if not is_overlap_check_enabled():
        return 0

    root = Path(repo_root) if repo_root is not None else Path.cwd()
    writer = emit or (lambda message: print(message, flush=True))

    try:
        reports = detect_cross_branch_overlaps(root)
        for line in format_warnings(reports):
            writer(line)

        if reports and is_overlap_strict_enabled():
            return 1
    except Exception as exc:
        writer(f"[collab] Warning: cross-branch overlap check failed: {exc}")
        logger.debug("Cross-branch overlap check failed", exc_info=True)
    return 0
