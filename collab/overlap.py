"""Git-based cross-branch / cross-PR file overlap detection.

Compares changes on the current branch against other unmerged branches so developers
see likely merge conflicts before push. Also provides the server-side PR overlap guard
with merge-tree line-level conflict confirmation.

By default this is advisory (warnings only, never blocks). When
``COLLAB_OVERLAP_STRICT`` is enabled the ``warn_cross_branch_overlap`` entry point
returns a non-zero code so the pre-push hook can block the push. In strict mode the
checks fail *closed*: an unexpected error (or an inability to refresh remote state)
blocks the push rather than silently allowing it.

Accuracy:

* **Line-level** -- when ``git merge-tree`` is available (git >= 2.38), overlap is
  confirmed by performing a real in-memory 3-way merge, so two branches editing
  *different* regions of the same file do **not** conflict. Falls back to file-level
  overlap on older git. Toggle with ``COLLAB_OVERLAP_LINE_LEVEL``.
* **Remote-agnostic** -- the remote to compare against is resolved dynamically (the
  push target, the branch upstream, ``origin``, or the sole remote) rather than
  hard-coding ``origin``. Override with ``COLLAB_OVERLAP_REMOTE``.
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

_DEFAULT_REMOTE = "origin"
_DEFAULT_BASE_BRANCHES = ("main", "master")
_SKIP_REF_SUFFIXES = frozenset({"HEAD"})

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})

# Exit codes returned by ``warn_cross_branch_overlap`` (consumed by the pre-push
# hook so it can report an accurate reason).
EXIT_OK = 0
EXIT_OVERLAP = 1  # Overlap detected while strict mode is enabled.
EXIT_ERROR = 3  # Strict mode could not verify state (fail-closed).

_FETCH_TIMEOUT_S = 20.0


@dataclass(frozen=True)
class OverlapReport:
    """Files on the current branch that also differ on another unmerged branch."""

    branch: str
    files: tuple[str, ...]


def is_overlap_strict_enabled() -> bool:
    """Return True when ``COLLAB_OVERLAP_STRICT`` is enabled."""
    raw = os.getenv("COLLAB_OVERLAP_STRICT", "0").strip().lower()
    return raw in _TRUE_VALUES


def is_overlap_check_enabled() -> bool:
    """Return True unless ``COLLAB_OVERLAP_CHECK`` is explicitly disabled.

    Strict mode implies checking: enabling ``COLLAB_OVERLAP_STRICT`` forces the
    overlap check on even if ``COLLAB_OVERLAP_CHECK`` is set to a falsey value, so a
    user who opts into blocking can never be silently downgraded to "no checks".
    """
    if is_overlap_strict_enabled():
        return True
    raw = os.getenv("COLLAB_OVERLAP_CHECK", "1").strip().lower()
    return raw not in _FALSE_VALUES


def is_overlap_fetch_enabled() -> bool:
    """Return whether :func:`refresh_remote_state` should ``git fetch`` first.

    ``COLLAB_OVERLAP_FETCH`` is tri-state:

    * a truthy value (``1``/``true``/...) always fetches,
    * a falsey value (``0``/``false``/...) never fetches,
    * the default ``auto`` fetches **only in strict mode** -- where fresh remote
      state is required to block correctly -- so the advisory default does not add a
      network round-trip to every push.
    """
    raw = os.getenv("COLLAB_OVERLAP_FETCH", "auto").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return is_overlap_strict_enabled()


def is_line_level_enabled() -> bool:
    """Return True unless ``COLLAB_OVERLAP_LINE_LEVEL`` is explicitly disabled.

    Used by the client-side ``overlap.py`` pre-push hook.
    """
    raw = os.getenv("COLLAB_OVERLAP_LINE_LEVEL", "1").strip().lower()
    return raw not in _FALSE_VALUES


def is_pr_line_level_enabled() -> bool:
    """Return True unless ``COLLAB_PR_OVERLAP_LINE_LEVEL`` is explicitly disabled.

    Used by the server-side ``pr_overlap.py`` GitHub Actions guard. Default is ``1``
    (line-level on) per the v0.9.0+ design.
    """
    raw = os.getenv("COLLAB_PR_OVERLAP_LINE_LEVEL", "1").strip().lower()
    return raw not in _FALSE_VALUES


def is_pr_claims_enabled() -> bool:
    """Return True when ``COLLAB_PR_CLAIMS`` opt-in is enabled.

    When enabled, the pre-push hook retains the pushed branch's files as persistent
    claims (instead of releasing them) so cross-developer edit-time protection extends
    to open, pushed PR branches. Default OFF.
    """
    raw = os.getenv("COLLAB_PR_CLAIMS", "0").strip().lower()
    return raw in _TRUE_VALUES


def _default_git_capture(cwd: str, args: Sequence[str]) -> tuple[int, str]:
    timeout = _FETCH_TIMEOUT_S if args and args[0] == "fetch" else 30.0
    result = safe_subprocess.capture(
        ["git", *args],
        policy="git",
        cwd=cwd,
        timeout=timeout,
    )
    stdout = safe_subprocess.decode_output(result.stdout).strip()
    return result.returncode, stdout


def _looks_like_url(value: str) -> bool:
    """True when ``value`` is a remote URL rather than a configured remote name."""
    return "://" in value or value.endswith(".git") or value.startswith("git@")


def resolve_remote(capture: GitCapture, *, preferred: Optional[str] = None) -> str:
    """Resolve which git remote to compare against.

    Priority: ``COLLAB_OVERLAP_REMOTE`` env > ``preferred`` (e.g. the push target the
    pre-push hook was invoked with) > the current branch's upstream remote > a remote
    literally named ``origin`` > the sole configured remote > ``origin`` as a final
    convention fallback. Always returns a non-empty name.
    """
    env = os.getenv("COLLAB_OVERLAP_REMOTE", "").strip()
    if env:
        return env
    if preferred:
        candidate = preferred.strip()
        if candidate and not _looks_like_url(candidate):
            return candidate

    rc_up, upstream = capture(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
    )
    if rc_up == 0 and upstream and "/" in upstream:
        remote = upstream.split("/", 1)[0].strip()
        if remote:
            return remote

    rc_list, listing = capture(["remote"])
    names = (
        [n.strip() for n in listing.splitlines() if n.strip()] if rc_list == 0 else []
    )
    if _DEFAULT_REMOTE in names:
        return _DEFAULT_REMOTE
    if len(names) == 1:
        return names[0]
    return _DEFAULT_REMOTE


def refresh_remote_state(
    repo_root: str | Path,
    *,
    remote: Optional[str] = None,
    git_capture: Optional[GitCapture] = None,
) -> bool:
    """Best-effort ``git fetch`` so overlap detection sees freshly pushed branches.

    Without this, detection only compares against the remote-tracking refs the local
    clone already knows about, so an overlapping branch pushed from *another* clone is
    invisible. Returns ``True`` when the remote state is current (fetch succeeded or was
    disabled), ``False`` when a fetch was attempted but failed. Callers in strict mode
    treat ``False`` as fail-closed.
    """
    if not is_overlap_fetch_enabled():
        return True

    cwd = str(Path(repo_root).resolve())

    def capture(args: Sequence[str]) -> tuple[int, str]:
        runner = git_capture or (lambda a: _default_git_capture(cwd, a))
        return runner(args)

    try:
        target = remote or resolve_remote(capture)
        rc, _ = capture(["fetch", "--prune", "--quiet", target])
    except Exception:
        logger.debug("git fetch for overlap refresh failed", exc_info=True)
        return False
    return rc == 0


def _ref_exists(capture: GitCapture, ref: str) -> bool:
    rc, _ = capture(["rev-parse", "--verify", ref])
    return rc == 0


def resolve_base_ref(
    capture: GitCapture, remote: str = _DEFAULT_REMOTE
) -> Optional[str]:
    """Resolve the git base ref for overlap comparison (e.g. ``origin/main``)."""
    override = os.getenv("COLLAB_LOCK_BASE_REF", "").strip()
    if override and _ref_exists(capture, override):
        return override
    for branch in _DEFAULT_BASE_BRANCHES:
        candidate = f"{remote}/{branch}"
        if _ref_exists(capture, candidate):
            return candidate
    return None


def _branch_display_name(ref: str, remote: str = _DEFAULT_REMOTE) -> str:
    prefix = f"{remote}/"
    if ref.startswith(prefix):
        short = ref[len(prefix) :]
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


def _is_ancestor(capture: GitCapture, ancestor: str, descendant: str) -> bool:
    """Return True when ``ancestor`` is fully contained in ``descendant``."""
    rc, _ = capture(["merge-base", "--is-ancestor", ancestor, descendant])
    return rc == 0


def merge_tree_conflicts(
    capture: GitCapture, head_ref: str, other_ref: str
) -> Optional[frozenset[str]]:
    """Return the files that truly conflict when merging the two refs.

    Uses ``git merge-tree --write-tree`` (git >= 2.38) to perform a real
    in-memory 3-way merge -- so edits to *different* regions of a shared file
    are not reported.  Returns:

    * ``frozenset()`` -- the branches merge cleanly (no conflict),
    * a non-empty frozenset -- the conflicting file paths,
    * ``None`` -- line-level detection is unavailable or inconclusive; the
      caller should fall back to file-level overlap.
    """
    rc, out = capture(
        ["merge-tree", "--write-tree", "--name-only", head_ref, other_ref]
    )
    if rc == 0:
        return frozenset()
    if rc == 1:
        # stdout: line 0 is the tree OID; conflicted paths follow until a
        # blank line.
        lines = out.splitlines()
        conflicted = set()
        for line in lines[1:]:
            if not line.strip():
                break
            conflicted.add(line.strip())
        # rc==1 with nothing parseable -> inconclusive; fall back to file-level.
        return frozenset(conflicted) if conflicted else None
    return None


def git_version_supports_merge_tree(capture: GitCapture) -> bool:
    """Return True when the installed git supports ``merge-tree --write-tree``.

    Probes by running ``git merge-tree -h`` and checking for ``--write-tree`` in the
    help output.  The exit code of ``-h`` is deliberately ignored because newer git
    versions (>= 2.48) may return 129 even when the flag is fully supported and listed
    in the help text.
    """
    _, out = capture(["merge-tree", "-h"])
    return "--write-tree" in out


def fetch_pr_ref(
    capture: GitCapture,
    remote: str,
    pr_number: int,
    head_sha: str,
    *,
    timeout: float = 30.0,
) -> Optional[str]:
    """Fetch a single pull-request head into a local ref and return the ref name.

    The ref is fetched as ``collab/pr/<N>`` so multiple PRs can coexist in the
    same shallow CI checkout.  Returns ``None`` when the fetch fails (callers
    should treat this as fail-closed / ``EXIT_ERROR``).

    Args:
        capture: Git capture callable.
        remote: The git remote to fetch from (e.g. ``origin``).
        pr_number: GitHub PR number.
        head_sha: The expected HEAD SHA of the PR (from the GitHub API).
        timeout: Timeout in seconds for the fetch subprocess.
    """
    local_ref = f"collab/pr/{pr_number}"

    # --force in case a previous run left a stale ref with the same name.
    rc, _ = capture(
        [
            "fetch",
            "--force",
            "--quiet",
            remote,
            f"pull/{pr_number}/head:{local_ref}",
        ]
    )
    if rc != 0:
        logger.debug("Failed to fetch PR #%d ref from %s", pr_number, remote)
        return None

    # Verify the fetched ref points at the expected SHA.
    rc_verify, fetched_sha = capture(
        ["rev-parse", "--verify", f"{local_ref}^{{commit}}"]
    )
    if rc_verify != 0:
        logger.debug(
            "Ref %s didn't resolve after fetch for PR #%d", local_ref, pr_number
        )
        return None

    fetched_sha = fetched_sha.strip()
    if fetched_sha != head_sha:
        logger.debug(
            "Fetched SHA %s != expected %s for PR #%d", fetched_sha, head_sha, pr_number
        )
        # Still usable (the ref exists), but log the discrepancy.
    return local_ref


def _list_candidate_refs(
    capture: GitCapture, remote: str = _DEFAULT_REMOTE
) -> list[tuple[str, str]]:
    """Return ``(display_name, ref)`` pairs for local and ``<remote>/*`` branches."""
    remote_prefix = f"{remote}/"
    rc, output = capture(
        [
            "for-each-ref",
            "--format=%(refname:short) %(objectname)",
            "refs/heads",
            f"refs/remotes/{remote}",
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
        display = _branch_display_name(ref, remote)
        if not display or display == "HEAD":
            continue
        existing = by_commit.get(commit)
        if existing is None:
            by_commit[commit] = (display, ref)
            continue
        # Prefer local branch names over <remote>/* for the same commit.
        if existing[1].startswith(remote_prefix) and not ref.startswith(remote_prefix):
            by_commit[commit] = (display, ref)

    return list(by_commit.values())


def detect_cross_branch_overlaps(
    repo_root: str | Path,
    *,
    git_capture: Optional[GitCapture] = None,
    remote: Optional[str] = None,
    line_level: bool = False,
) -> list[OverlapReport]:
    """Detect file overlap between HEAD and other unmerged branches.

    Returns an empty list when overlap checking is disabled, git is unavailable, or no
    overlaps exist. Individual branch failures are skipped (fail-open).

    When ``line_level`` is True, a file-level overlap is confirmed with
    :func:`merge_tree_conflicts` (a real merge) so non-conflicting edits to the same
    file are dropped; if that is unavailable the file-level result is kept.
    """
    if not is_overlap_check_enabled():
        return []

    cwd = str(Path(repo_root).resolve())

    def capture(args: Sequence[str]) -> tuple[int, str]:
        runner = git_capture or (lambda a: _default_git_capture(cwd, a))
        return runner(args)

    if remote is None:
        remote = resolve_remote(capture)

    base_ref = resolve_base_ref(capture, remote)
    if not base_ref:
        return []

    current_branch, head_sha = _current_branch(capture)
    if head_sha is None:
        return []

    changed_head = _changed_files_since_base(capture, "HEAD", base_ref)
    if not changed_head:
        return []

    base_display = _branch_display_name(base_ref, remote)
    skip_names = {base_display, base_ref, "main", "master"}
    if current_branch:
        skip_names.add(current_branch)

    reports: list[OverlapReport] = []
    for display, ref in _list_candidate_refs(capture, remote):
        if display in skip_names or ref in skip_names:
            continue

        rc_tip, ref_sha = capture(["rev-parse", ref])
        if rc_tip != 0 or not ref_sha:
            continue
        if head_sha and ref_sha == head_sha:
            continue

        if not _is_unmerged(capture, ref, base_ref):
            continue

        # Skip branches HEAD is stacked on top of: if every commit on ``ref`` is
        # already contained in HEAD, merging HEAD also brings in ``ref`` — no
        # conflict can arise from this branch, so flagging it is a false positive.
        if _is_ancestor(capture, ref, "HEAD"):
            continue

        changed_other = _changed_files_since_base(capture, ref, base_ref)
        overlap = sorted(changed_head & changed_other)
        if not overlap:
            continue

        if line_level:
            # Confirm with a real merge: keep only files that actually conflict.
            conflicts = merge_tree_conflicts(capture, "HEAD", ref)
            if conflicts is not None:
                overlap = sorted(set(overlap) & conflicts)
                if not overlap:
                    continue

        reports.append(OverlapReport(branch=display, files=tuple(overlap)))

    reports.sort(key=lambda item: item.branch)
    return reports


def stale_claim_branches(
    repo_root: str | Path,
    branches: Sequence[str],
    *,
    remote: Optional[str] = None,
    git_capture: Optional[GitCapture] = None,
    fetch: bool = True,
) -> frozenset[str]:
    """Return the subset of ``branches`` whose PR claims should be released.

    A claim's branch is considered stale (safe to release) when, on the remote, the
    branch is **gone** (merged-and-deleted, or deleted) -- the primary, squash-merge-
    safe signal -- or fully **merged** into the base ref. Forces a pruning ``git fetch``
    first (unconditionally, unlike the advisory path) so deletions are seen. Git-only;
    never raises (returns what it could determine).
    """
    wanted = {b for b in branches if b}
    if not wanted:
        return frozenset()

    cwd = str(Path(repo_root).resolve())

    def capture(args: Sequence[str]) -> tuple[int, str]:
        runner = git_capture or (lambda a: _default_git_capture(cwd, a))
        return runner(args)

    try:
        if remote is None:
            remote = resolve_remote(capture)
        if fetch:
            try:
                capture(["fetch", "--prune", "--quiet", remote])
            except Exception:
                logger.debug("prune-fetch for claim reconcile failed", exc_info=True)

        base_ref = resolve_base_ref(capture, remote)
        stale: set[str] = set()
        for branch in wanted:
            ref = f"{remote}/{branch}"
            if not _ref_exists(capture, ref):
                stale.add(branch)  # gone on remote (deleted / merged-and-deleted)
                continue
            if base_ref and not _is_unmerged(capture, ref, base_ref):
                stale.add(branch)  # fully merged into the base
        return frozenset(stale)
    except Exception:
        logger.debug("stale_claim_branches failed", exc_info=True)
        return frozenset()


def head_changed_files(
    repo_root: str | Path,
    *,
    remote: Optional[str] = None,
    git_capture: Optional[GitCapture] = None,
) -> tuple[Optional[str], list[str]]:
    """Return ``(branch, files)`` changed on HEAD vs the resolved base ref.

    ``files`` is the sorted set of paths that make up the current branch's diff from the
    base (i.e. what an open PR for this branch would contain) -- the set to retain as PR
    claims on push. ``files`` is empty when no base ref resolves (caller should then
    fall back to ordinary release). ``branch`` is None on a detached HEAD.
    """
    cwd = str(Path(repo_root).resolve())

    def capture(args: Sequence[str]) -> tuple[int, str]:
        runner = git_capture or (lambda a: _default_git_capture(cwd, a))
        return runner(args)

    if remote is None:
        remote = resolve_remote(capture)
    branch, _ = _current_branch(capture)
    base_ref = resolve_base_ref(capture, remote)
    if not base_ref:
        return branch, []
    return branch, sorted(_changed_files_since_base(capture, "HEAD", base_ref))


# Plain-ASCII so the message renders identically on a Windows cp1252 console and a
# UTF-8 terminal (an emoji here previously raised UnicodeEncodeError on Windows).
def format_overlap_warning(report: OverlapReport) -> str:
    """Format a single overlap report as a user-facing warning line."""
    quoted = ", ".join(f"`{path}`" for path in report.files)
    verb = "is" if len(report.files) == 1 else "are"
    return (
        f"[collab] WARNING: cross-branch overlap: {quoted} {verb} also modified on "
        f"`{report.branch}` (unmerged). Merging both will likely conflict -- "
        f"coordinate merge order or rebase one onto the other."
    )


def format_warnings(reports: Sequence[OverlapReport]) -> list[str]:
    """Format all overlap reports as warning lines."""
    return [format_overlap_warning(report) for report in reports]


def _emit_strict_help(writer: Callable[[str], None]) -> None:
    """Tell the user how to proceed once strict mode has blocked them."""
    writer(
        "[collab] COLLAB_OVERLAP_STRICT=1 blocked this push. To proceed: "
        "rebase onto the other branch or coordinate merge order, then retry. "
        "To override for one push set COLLAB_OVERLAP_STRICT=0 (or, as a last "
        "resort, `git push --no-verify`)."
    )


def warn_cross_branch_overlap(
    repo_root: str | Path | None = None,
    *,
    emit: Optional[Callable[[str], None]] = None,
    remote: Optional[str] = None,
) -> int:
    """Run overlap detection, emit warnings, and return an exit code.

    ``remote`` is the push target the pre-push hook was invoked with; it is used to
    decide which remote's branches to compare against (see :func:`resolve_remote`).

    Returns :data:`EXIT_OK` (0) normally. In strict mode (``COLLAB_OVERLAP_STRICT=1``)
    it returns :data:`EXIT_OVERLAP` (1) when an overlap is found, or :data:`EXIT_ERROR`
    (3) when the check could not be completed (fail-closed) -- including when the pre-
    push ``git fetch`` could not refresh remote state. Outside strict mode it always
    returns ``EXIT_OK``.
    """
    if not is_overlap_check_enabled():
        return EXIT_OK

    root = Path(repo_root) if repo_root is not None else Path.cwd()
    writer = emit or (lambda message: print(message, flush=True))
    strict = is_overlap_strict_enabled()
    cwd = str(root.resolve())

    def capture(args: Sequence[str]) -> tuple[int, str]:
        return _default_git_capture(cwd, args)

    try:
        resolved_remote = resolve_remote(capture, preferred=remote)

        # Refresh remote tracking refs so a branch pushed from another clone is
        # visible. In strict mode an inability to refresh is fail-closed: we cannot
        # certify the absence of an overlap against stale data.
        if not refresh_remote_state(root, remote=resolved_remote, git_capture=capture):
            writer(
                "[collab] WARNING: could not 'git fetch' to refresh remote branch "
                "state; overlap results may be stale."
            )
            if strict:
                _emit_strict_help(writer)
                return EXIT_ERROR

        reports = detect_cross_branch_overlaps(
            root,
            git_capture=capture,
            remote=resolved_remote,
            line_level=is_line_level_enabled(),
        )
        for line in format_warnings(reports):
            writer(line)

        if reports and strict:
            _emit_strict_help(writer)
            return EXIT_OVERLAP
    except Exception as exc:
        writer(f"[collab] Warning: cross-branch overlap check failed: {exc}")
        logger.debug("Cross-branch overlap check failed", exc_info=True)
        # Fail closed under strict mode: an error must not silently allow a push.
        if strict:
            _emit_strict_help(writer)
            return EXIT_ERROR
    return EXIT_OK
