"""Server-side cross-PR file overlap guard (GitHub Actions).

Client git hooks (``overlap.py`` + the ``pre-push`` template) can always be skipped with
``git push --no-verify``. This module is the *bulletproof* layer: it runs in CI on
``pull_request`` events and fails the check when the PR's changed files overlap another
**open** PR targeting the same base branch. Combined with a branch-protection rule that
requires this check, overlapping PRs cannot be merged without an explicit human
override.

The overlap math (:func:`find_overlaps`) is pure and unit-tested. Network access is
isolated behind an injectable ``http`` callable so tests never hit GitHub.

As of v0.9.0 the guard also supports **line-level** conflict confirmation via ``git
merge-tree`` (default on, opt-out with ``COLLAB_PR_OVERLAP_LINE_LEVEL=0``). When
enabled, two PRs touching the same file but non-overlapping line ranges no longer block
each other — only files with real merge-tree conflicts are flagged.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from collab import overlap, safe_subprocess

logger = logging.getLogger(__name__)

GITHUB_API = (os.getenv("GITHUB_API_URL") or "https://api.github.com").rstrip("/")

# Exit codes (consumed by the workflow / branch protection).
EXIT_OK = 0
EXIT_OVERLAP = 1  # An overlapping open PR was found.
EXIT_ERROR = 2  # Could not complete the check (fail-closed in CI).

# An injectable HTTP getter: (url, token) -> parsed JSON (list or dict).
HttpGetter = Callable[[str, Optional[str]], object]

# An injectable git capture: (args) -> (returncode, stdout).
GitCapture = Callable[[Sequence[str]], tuple[int, str]]

_MAX_PRS_DEFAULT = 50


@dataclass(frozen=True)
class PullRequest:
    """The minimal PR shape this guard reasons about."""

    number: int
    branch: str
    files: frozenset[str]
    draft: bool = False
    head_sha: Optional[str] = None


@dataclass(frozen=True)
class OverlapHit:
    """An open PR that shares changed files with the PR under review."""

    number: int
    branch: str
    files: tuple[str, ...]
    conflict_type: str = "file-level"


@dataclass
class GuardConfig:
    """Resolved runtime configuration for a single guard run."""

    repo: str
    pr_number: int
    base_ref: str
    token: Optional[str] = None
    skip_drafts: bool = field(default=False)
    line_level: bool = field(default=True)
    max_prs: int = field(default=_MAX_PRS_DEFAULT)


def find_overlaps(
    current_files: Iterable[str],
    others: Iterable[PullRequest],
    *,
    skip_drafts: bool = False,
) -> list[OverlapHit]:
    """Return open PRs whose changed files intersect ``current_files``.

    Pure function: no I/O. ``others`` should already exclude the PR under review.
    """
    current = frozenset(current_files)
    hits: list[OverlapHit] = []
    if not current:
        return hits
    for pr in others:
        if skip_drafts and pr.draft:
            continue
        shared = sorted(current & pr.files)
        if shared:
            hits.append(
                OverlapHit(number=pr.number, branch=pr.branch, files=tuple(shared))
            )
    hits.sort(key=lambda hit: hit.number)
    return hits


def _refine_line_overlaps(
    hits: list[OverlapHit],
    others: list[PullRequest],
    current_head_ref: str,
    *,
    capture: GitCapture,
) -> list[OverlapHit]:
    """Refine file-level overlap hits with merge-tree confirmation.

    For each hit whose PR has a ``head_sha``, run ``merge_tree_conflicts`` and intersect
    the result with the file-level overlap.  PRs that merge cleanly are dropped.  When
    merge-tree is inconclusive the original file-level hit is kept (fail-closed).

    Returns a new list of refined hits (may be shorter than ``hits``).
    """
    by_number: dict[int, PullRequest] = {pr.number: pr for pr in others}
    refined: list[OverlapHit] = []

    for hit in hits:
        pr = by_number.get(hit.number)
        if pr is None or not pr.head_sha:
            # No head_sha → can't confirm; keep file-level hit (fail-closed).
            refined.append(hit)
            continue

        other_ref = overlap.fetch_pr_ref(capture, "origin", pr.number, pr.head_sha)
        if other_ref is None:
            # Fetch failed → can't confirm; keep file-level hit (fail-closed).
            logger.warning(
                "Could not fetch ref for PR #%d; keeping file-level overlap.",
                pr.number,
            )
            refined.append(hit)
            continue

        conflicts = overlap.merge_tree_conflicts(capture, current_head_ref, other_ref)
        if conflicts is None:
            # Inconclusive → keep file-level hit (fail-closed).
            logger.warning(
                "merge-tree inconclusive for PR #%d; keeping file-level overlap.",
                pr.number,
            )
            refined.append(hit)
            continue

        # Intersect file overlap with merge-tree-confirmed conflicts.
        real_overlap = sorted(set(hit.files) & conflicts)
        if real_overlap:
            refined.append(
                OverlapHit(
                    number=hit.number,
                    branch=hit.branch,
                    files=tuple(real_overlap),
                    conflict_type="line-level-confirmed",
                )
            )
        # else: merges cleanly → not a hit; drop it.

    refined.sort(key=lambda h: h.number)
    return refined


def format_overlap_report(current_number: int, hits: list[OverlapHit]) -> str:
    """Render a human-readable summary for the CI log / check output."""
    if not hits:
        return f"[collab] No cross-PR file overlap for PR #{current_number}."
    lines = [
        f"[collab] PR #{current_number} overlaps {len(hits)} open PR(s); "
        "merging both risks a conflict:",
    ]
    for hit in hits:
        flist = ", ".join(hit.files)
        ctype = f" ({hit.conflict_type})" if hit.conflict_type != "file-level" else ""
        lines.append(f"  - PR #{hit.number} ({hit.branch}): {flist}{ctype}")
    lines.append(
        "[collab] Resolve by rebasing/merging one PR first, splitting the shared "
        "files, or coordinating merge order before this check can pass."
    )
    return "\n".join(lines)


def _default_http(url: str, token: Optional[str]) -> object:
    """Fetch and parse JSON from the GitHub REST API.

    Only GitHub-API HTTPS URLs are accepted; this guard makes the urlopen provably
    scheme/host-safe (no local-file or custom schemes) -- the concern behind bandit B310
    / ruff S310, which are suppressed below because this guard enforces it.
    """
    if not url.startswith(GITHUB_API + "/"):
        raise ValueError(f"refusing to open non-GitHub-API URL: {url!r}")
    req = urllib.request.Request(url)  # noqa: S310  # nosec B310
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "collab-pr-overlap-guard")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310  # nosec B310
        return json.loads(resp.read().decode("utf-8"))


def _paginate(
    http: HttpGetter, url: str, token: Optional[str], *, max_pages: int = 20
) -> list[dict]:
    """Collect items across up to ``max_pages`` of a GitHub list endpoint."""
    items: list[dict] = []
    sep = "&" if "?" in url else "?"
    for page in range(1, max_pages + 1):
        page_url = f"{url}{sep}per_page=100&page={page}"
        batch = http(page_url, token)
        if not isinstance(batch, list) or not batch:
            break
        items.extend(b for b in batch if isinstance(b, dict))
        if len(batch) < 100:
            break
    return items


def _pr_files(
    http: HttpGetter, repo: str, number: int, token: Optional[str]
) -> frozenset[str]:
    url = f"{GITHUB_API}/repos/{repo}/pulls/{number}/files"
    rows = _paginate(http, url, token)
    return frozenset(
        str(row["filename"]) for row in rows if isinstance(row.get("filename"), str)
    )


def gather_other_prs(http: HttpGetter, config: GuardConfig) -> list[PullRequest]:
    """List open PRs (same base, excluding the one under review) with their files."""
    url = f"{GITHUB_API}/repos/{config.repo}/pulls?state=open&base={config.base_ref}"
    raw = _paginate(http, url, config.token)
    others: list[PullRequest] = []
    for row in raw:
        number = row.get("number")
        if not isinstance(number, int) or number == config.pr_number:
            continue
        branch = ""
        head_sha = None
        head = row.get("head")
        if isinstance(head, dict):
            if isinstance(head.get("ref"), str):
                branch = head["ref"]
            if isinstance(head.get("sha"), str):
                head_sha = head["sha"]
        files = _pr_files(http, config.repo, number, config.token)
        others.append(
            PullRequest(
                number=number,
                branch=branch,
                files=files,
                draft=bool(row.get("draft", False)),
                head_sha=head_sha,
            )
        )
    # Respect max_prs cap.
    if len(others) > config.max_prs:
        logger.warning(
            "Truncating open PR list from %d to %d (COLLAB_PR_OVERLAP_MAX_PRS=%d)",
            len(others),
            config.max_prs,
            config.max_prs,
        )
        others = others[: config.max_prs]
    return others


def _default_git_capture(cwd: str, args: Sequence[str]) -> tuple[int, str]:
    """Run a git command and return (returncode, stdout)."""
    result = safe_subprocess.capture(
        ["git", *args],
        policy="git",
        cwd=cwd,
        timeout=60.0,
    )
    stdout = safe_subprocess.decode_output(result.stdout).strip()
    return result.returncode, stdout


def _current_head_sha(capture: GitCapture) -> Optional[str]:
    """Return the SHA of HEAD, or None if git is unavailable."""
    rc, sha = capture(["rev-parse", "HEAD"])
    if rc != 0 or not sha:
        return None
    return sha.strip()


def run(
    config: GuardConfig,
    *,
    http: HttpGetter = _default_http,
    git_capture: Optional[GitCapture] = None,
) -> int:
    """Execute the guard; return an :data:`EXIT_OK`/``OVERLAP``/``ERROR`` code.

    When ``config.line_level`` is True (the default), file-level overlaps are confirmed
    with ``git merge-tree`` so non-conflicting edits to the same file are not flagged.
    The git working directory is the current working directory (the CI checkout).
    """
    try:
        current_files = _pr_files(http, config.repo, config.pr_number, config.token)
        if not current_files:
            print(
                f"[collab] PR #{config.pr_number} has no changed files; "
                "nothing to check."
            )
            return EXIT_OK
        others = gather_other_prs(http, config)
        hits = find_overlaps(current_files, others, skip_drafts=config.skip_drafts)
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        KeyError,
    ) as exc:
        # Fail-closed: an unverifiable result must not silently pass the gate.
        print(f"[collab] PR overlap guard could not complete: {exc}", file=sys.stderr)
        return EXIT_ERROR

    # --- line-level refinement (only when hits exist) ----------------------
    if hits and config.line_level:
        cwd = str(Path.cwd())

        def capture(args: Sequence[str]) -> tuple[int, str]:
            runner = git_capture or (lambda a: _default_git_capture(cwd, a))
            return runner(args)

        # Verify git supports merge-tree before proceeding.
        if not overlap.git_version_supports_merge_tree(capture):
            print(
                "[collab] git merge-tree --write-tree not available; "
                "falling back to file-level overlap detection."
            )
        else:
            head_ref = _current_head_sha(capture)
            if head_ref is None:
                print(
                    "[collab] Could not resolve HEAD SHA; "
                    "cannot perform line-level overlap refinement.",
                    file=sys.stderr,
                )
                return EXIT_ERROR

            print(
                f"[collab] Line-level refinement enabled; "
                f"verifying {len(hits)} file-level hit(s) with merge-tree..."
            )
            hits = _refine_line_overlaps(hits, others, head_ref, capture=capture)
    # -----------------------------------------------------------------------

    print(format_overlap_report(config.pr_number, hits))
    return EXIT_OVERLAP if hits else EXIT_OK


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_event_pr() -> tuple[Optional[int], Optional[str]]:
    """Read ``(pr_number, base_ref)`` from the GitHub Actions event payload."""
    path = os.getenv("GITHUB_EVENT_PATH")
    if not path or not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as handle:
            event = json.load(handle)
    except (OSError, ValueError):
        return None, None
    pr = event.get("pull_request")
    if not isinstance(pr, dict):
        return None, None
    number = pr.get("number")
    base = pr.get("base")
    base_ref = base.get("ref") if isinstance(base, dict) else None
    return (
        number if isinstance(number, int) else None,
        base_ref if isinstance(base_ref, str) else None,
    )


def config_from_env() -> Optional[GuardConfig]:
    """Build a :class:`GuardConfig` from the Actions environment, or ``None``."""
    repo = os.getenv("GITHUB_REPOSITORY")
    if not repo:
        return None
    pr_number, base_ref = _load_event_pr()
    if pr_number is None or base_ref is None:
        return None
    return GuardConfig(
        repo=repo,
        pr_number=pr_number,
        base_ref=base_ref,
        token=os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN"),
        skip_drafts=_bool_env("COLLAB_PR_OVERLAP_SKIP_DRAFTS", False),
        line_level=_bool_env("COLLAB_PR_OVERLAP_LINE_LEVEL", True),
        max_prs=int(os.getenv("COLLAB_PR_OVERLAP_MAX_PRS", str(_MAX_PRS_DEFAULT))),
    )


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for ``python -m collab.pr_overlap`` (GitHub Actions)."""
    config = config_from_env()
    if config is None:
        print(
            "[collab] PR overlap guard: not a pull_request event or missing "
            "GITHUB_REPOSITORY/GITHUB_EVENT_PATH; skipping.",
            file=sys.stderr,
        )
        return EXIT_OK
    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())
