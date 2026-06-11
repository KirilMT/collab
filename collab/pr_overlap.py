"""Server-side cross-PR file overlap guard (GitHub Actions).

Client git hooks (``overlap.py`` + the ``pre-push`` template) can always be skipped with
``git push --no-verify``. This module is the *bulletproof* layer: it runs in CI on
``pull_request`` events and fails the check when the PR's changed files overlap another
**open** PR targeting the same base branch. Combined with a branch-protection rule that
requires this check, overlapping PRs cannot be merged without an explicit human
override.

The overlap math (:func:`find_overlaps`) is pure and unit-tested. Network access is
isolated behind an injectable ``http`` callable so tests never hit GitHub.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

GITHUB_API = "https://api.github.com"

# Exit codes (consumed by the workflow / branch protection).
EXIT_OK = 0
EXIT_OVERLAP = 1  # An overlapping open PR was found.
EXIT_ERROR = 2  # Could not complete the check (fail-closed in CI).

# An injectable HTTP getter: (url, token) -> parsed JSON (list or dict).
HttpGetter = Callable[[str, Optional[str]], object]


@dataclass(frozen=True)
class PullRequest:
    """The minimal PR shape this guard reasons about."""

    number: int
    branch: str
    files: frozenset[str]
    draft: bool = False


@dataclass(frozen=True)
class OverlapHit:
    """An open PR that shares changed files with the PR under review."""

    number: int
    branch: str
    files: tuple[str, ...]


@dataclass
class GuardConfig:
    """Resolved runtime configuration for a single guard run."""

    repo: str
    pr_number: int
    base_ref: str
    token: Optional[str] = None
    skip_drafts: bool = field(default=False)


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
        lines.append(f"  - PR #{hit.number} ({hit.branch}): {flist}")
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
        head = row.get("head")
        if isinstance(head, dict) and isinstance(head.get("ref"), str):
            branch = head["ref"]
        files = _pr_files(http, config.repo, number, config.token)
        others.append(
            PullRequest(
                number=number,
                branch=branch,
                files=files,
                draft=bool(row.get("draft", False)),
            )
        )
    return others


def run(config: GuardConfig, *, http: HttpGetter = _default_http) -> int:
    """Execute the guard; return an :data:`EXIT_OK`/``OVERLAP``/``ERROR`` code."""
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
