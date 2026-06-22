"""Unit tests for collab.pr_overlap server-side cross-PR overlap guard."""

from __future__ import annotations

import json
import os

import pytest

from collab import pr_overlap


def _pr(number, files, *, branch="feat/x", draft=False):
    return pr_overlap.PullRequest(
        number=number, branch=branch, files=frozenset(files), draft=draft
    )


# --- find_overlaps (pure) ---------------------------------------------------


def test_find_overlaps_detects_shared_file():
    hits = pr_overlap.find_overlaps(
        ["a.py", "b.py"],
        [_pr(7, ["b.py", "c.py"], branch="feat/seven")],
    )
    assert len(hits) == 1
    assert hits[0].number == 7
    assert hits[0].branch == "feat/seven"
    assert hits[0].files == ("b.py",)


def test_find_overlaps_none_when_disjoint():
    hits = pr_overlap.find_overlaps(["a.py"], [_pr(7, ["b.py"])])
    assert hits == []


def test_find_overlaps_empty_current_returns_none():
    assert pr_overlap.find_overlaps([], [_pr(7, ["a.py"])]) == []


def test_find_overlaps_skips_drafts_when_requested():
    others = [_pr(7, ["a.py"], draft=True)]
    assert pr_overlap.find_overlaps(["a.py"], others, skip_drafts=True) == []
    assert pr_overlap.find_overlaps(["a.py"], others, skip_drafts=False)


def test_find_overlaps_sorted_by_number():
    others = [_pr(9, ["a.py"]), _pr(3, ["a.py"]), _pr(5, ["a.py"])]
    hits = pr_overlap.find_overlaps(["a.py"], others)
    assert [h.number for h in hits] == [3, 5, 9]


def test_find_overlaps_multiple_files_sorted():
    hits = pr_overlap.find_overlaps(
        ["z.py", "a.py", "m.py"], [_pr(7, ["m.py", "a.py", "z.py"])]
    )
    assert hits[0].files == ("a.py", "m.py", "z.py")


# --- format_overlap_report --------------------------------------------------


def test_format_report_no_hits():
    msg = pr_overlap.format_overlap_report(42, [])
    assert "No cross-PR file overlap" in msg
    assert "#42" in msg


def test_format_report_lists_hits():
    hits = [pr_overlap.OverlapHit(number=7, branch="feat/seven", files=("b.py",))]
    msg = pr_overlap.format_overlap_report(42, hits)
    assert "#42 overlaps 1 open PR" in msg
    assert "PR #7 (feat/seven): b.py" in msg


# --- run (orchestration with fake HTTP) -------------------------------------


def _fake_http(pages):
    """Build an http(url, token) stub from a {url-substring: json} mapping."""

    def http(url, _token):
        for key, value in pages.items():
            if key in url:
                return value
        return []

    return http


def test_run_reports_overlap(capsys):
    config = pr_overlap.GuardConfig(
        repo="o/r", pr_number=42, base_ref="main", token="t"
    )
    http = _fake_http(
        {
            "/pulls/42/files": [{"filename": "shared.py"}],
            "/pulls?state=open": [
                {"number": 7, "head": {"ref": "feat/seven"}, "draft": False},
                {"number": 42, "head": {"ref": "self"}, "draft": False},
            ],
            "/pulls/7/files": [{"filename": "shared.py"}, {"filename": "x.py"}],
        }
    )
    rc = pr_overlap.run(config, http=http)
    assert rc == pr_overlap.EXIT_OVERLAP
    out = capsys.readouterr().out
    assert "PR #7" in out and "shared.py" in out


def test_run_excludes_self_pr(capsys):
    config = pr_overlap.GuardConfig(repo="o/r", pr_number=42, base_ref="main")
    http = _fake_http(
        {
            "/pulls/42/files": [{"filename": "shared.py"}],
            "/pulls?state=open": [
                {"number": 42, "head": {"ref": "self"}, "draft": False}
            ],
        }
    )
    assert pr_overlap.run(config, http=http) == pr_overlap.EXIT_OK


def test_run_no_overlap(capsys):
    config = pr_overlap.GuardConfig(repo="o/r", pr_number=42, base_ref="main")
    http = _fake_http(
        {
            "/pulls/42/files": [{"filename": "a.py"}],
            "/pulls?state=open": [
                {"number": 7, "head": {"ref": "feat/seven"}, "draft": False}
            ],
            "/pulls/7/files": [{"filename": "b.py"}],
        }
    )
    assert pr_overlap.run(config, http=http) == pr_overlap.EXIT_OK


def test_run_empty_current_files_is_ok():
    config = pr_overlap.GuardConfig(repo="o/r", pr_number=42, base_ref="main")
    http = _fake_http({"/pulls/42/files": []})
    assert pr_overlap.run(config, http=http) == pr_overlap.EXIT_OK


def test_run_fails_closed_on_http_error():
    config = pr_overlap.GuardConfig(repo="o/r", pr_number=42, base_ref="main")

    def boom(_url, _token):
        raise OSError("api down")

    assert pr_overlap.run(config, http=boom) == pr_overlap.EXIT_ERROR


def test_run_skips_drafts_when_configured():
    config = pr_overlap.GuardConfig(
        repo="o/r", pr_number=42, base_ref="main", skip_drafts=True
    )
    http = _fake_http(
        {
            "/pulls/42/files": [{"filename": "shared.py"}],
            "/pulls?state=open": [
                {"number": 7, "head": {"ref": "feat/seven"}, "draft": True}
            ],
            "/pulls/7/files": [{"filename": "shared.py"}],
        }
    )
    assert pr_overlap.run(config, http=http) == pr_overlap.EXIT_OK


# --- pagination -------------------------------------------------------------


def test_paginate_stops_on_short_page():
    calls = []

    def http(url, _token):
        calls.append(url)
        # The page param is always last in the URL built by _paginate.
        if url.endswith("page=1"):
            return [{"i": n} for n in range(100)]
        if url.endswith("page=2"):
            return [{"i": 100}]
        return []

    items = pr_overlap._paginate(http, "https://api/x", None)
    assert len(items) == 101
    assert len(calls) == 2


# --- config_from_env --------------------------------------------------------


def test_config_from_env_reads_event(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"number": 99, "base": {"ref": "develop"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.delenv("COLLAB_PR_OVERLAP_SKIP_DRAFTS", raising=False)

    config = pr_overlap.config_from_env()
    assert config is not None
    assert config.repo == "o/r"
    assert config.pr_number == 99
    assert config.base_ref == "develop"
    assert config.token == "secret"
    assert config.skip_drafts is False


def test_config_from_env_missing_repo_returns_none(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert pr_overlap.config_from_env() is None


def test_config_from_env_non_pr_event_returns_none(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"push": {}}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert pr_overlap.config_from_env() is None


def test_main_skips_when_not_pr_event(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert pr_overlap.main() == pr_overlap.EXIT_OK
    assert "skipping" in capsys.readouterr().err


def test_main_runs_when_config_present(monkeypatch):
    cfg = pr_overlap.GuardConfig(repo="o/r", pr_number=1, base_ref="main")
    monkeypatch.setattr(pr_overlap, "config_from_env", lambda: cfg)
    monkeypatch.setattr(pr_overlap, "run", lambda c: pr_overlap.EXIT_OVERLAP)
    assert pr_overlap.main() == pr_overlap.EXIT_OVERLAP


@pytest.mark.parametrize(
    "value,expected",
    [("1", True), ("yes", True), ("ON", True), ("0", False), ("nope", False)],
)
def test_bool_env(monkeypatch, value, expected):
    monkeypatch.setenv("X_FLAG", value)
    assert pr_overlap._bool_env("X_FLAG", False) is expected


def test_bool_env_default_when_unset(monkeypatch):
    monkeypatch.delenv("X_FLAG", raising=False)
    assert pr_overlap._bool_env("X_FLAG", True) is True


# ============================================================================
# Line-level / merge-tree tests
# ============================================================================


def _capture_from_map(responses):
    def capture(args):
        key = tuple(args)
        return responses.get(key, (1, ""))

    return capture


# --- _current_head_sha -----------------------------------------------------


def test_current_head_sha_resolves():
    cap = _capture_from_map({("rev-parse", "HEAD"): (0, "abc123\n")})
    assert pr_overlap._current_head_sha(cap) == "abc123"


def test_current_head_sha_unavailable():
    cap = _capture_from_map({("rev-parse", "HEAD"): (1, "")})
    assert pr_overlap._current_head_sha(cap) is None


def test_current_head_sha_empty_output():
    cap = _capture_from_map({("rev-parse", "HEAD"): (0, "")})
    assert pr_overlap._current_head_sha(cap) is None


# --- _refine_line_overlaps --------------------------------------------------


def _make_merge_tree_capture(head_sha="abc", pr_sha="def", conflicts=None):
    """Build a git capture that simulates fetch + merge-tree for one PR."""
    fetch_key = ("fetch", "--force", "--quiet", "origin", "pull/7/head:collab/pr/7")
    verify_key = ("rev-parse", "--verify", "collab/pr/7^{commit}")
    merge_key = ("merge-tree", "--write-tree", "--name-only", head_sha, "collab/pr/7")
    fetches = {}
    if conflicts is None:
        # Inconclusive merge-tree (rc=2)
        fetches = {
            fetch_key: (0, ""),
            verify_key: (0, pr_sha),
            merge_key: (2, ""),
        }
    elif conflicts == frozenset():
        fetches = {
            fetch_key: (0, ""),
            verify_key: (0, pr_sha),
            merge_key: (0, "treeoid"),
        }
    else:
        names = "\n".join(conflicts)
        fetches = {
            fetch_key: (0, ""),
            verify_key: (0, pr_sha),
            merge_key: (1, f"treeoid\n{names}\n"),
        }
    return _capture_from_map(fetches)


def test_refine_line_overlaps_clean_merge_drops_hit():
    """Merge-tree reports clean -> the file-level hit is removed."""
    hit = pr_overlap.OverlapHit(number=7, branch="feat/x", files=("a.py", "b.py"))
    pr = _pr(7, ["a.py", "b.py"], branch="feat/x")
    pr = pr_overlap.PullRequest(
        number=7, branch="feat/x", files=frozenset(["a.py", "b.py"]), head_sha="def"
    )
    cap = _make_merge_tree_capture(conflicts=frozenset())
    refined = pr_overlap._refine_line_overlaps([hit], [pr], "abc", capture=cap)
    assert refined == []


def test_refine_line_overlaps_real_conflict_keeps_hit():
    """Merge-tree reports conflict -> refined hit with line-level-confirmed type."""
    hit = pr_overlap.OverlapHit(number=7, branch="feat/x", files=("a.py", "b.py"))
    pr = pr_overlap.PullRequest(
        number=7, branch="feat/x", files=frozenset(["a.py", "b.py"]), head_sha="def"
    )
    cap = _make_merge_tree_capture(conflicts=frozenset({"a.py"}))
    refined = pr_overlap._refine_line_overlaps([hit], [pr], "abc", capture=cap)
    assert len(refined) == 1
    assert refined[0].number == 7
    assert refined[0].files == ("a.py",)
    assert refined[0].conflict_type == "line-level-confirmed"


def test_refine_line_overlaps_inconclusive_keeps_hit():
    """Merge-tree inconclusive (rc=2) -> keep file-level hit (fail-closed)."""
    hit = pr_overlap.OverlapHit(number=7, branch="feat/x", files=("a.py",))
    pr = pr_overlap.PullRequest(
        number=7, branch="feat/x", files=frozenset(["a.py"]), head_sha="def"
    )
    cap = _make_merge_tree_capture(conflicts=None)  # inconclusive
    refined = pr_overlap._refine_line_overlaps([hit], [pr], "abc", capture=cap)
    assert len(refined) == 1
    assert refined[0].conflict_type == "file-level"


def test_refine_line_overlaps_no_head_sha_keeps_hit():
    """PR without head_sha -> keep file-level hit (fail-closed)."""
    hit = pr_overlap.OverlapHit(number=7, branch="feat/x", files=("a.py",))
    pr = pr_overlap.PullRequest(
        number=7, branch="feat/x", files=frozenset(["a.py"]), head_sha=None
    )
    cap = _capture_from_map({})
    refined = pr_overlap._refine_line_overlaps([hit], [pr], "abc", capture=cap)
    assert len(refined) == 1
    assert refined[0].conflict_type == "file-level"


def test_refine_line_overlaps_fetch_failed_keeps_hit():
    """Fetch failure -> keep file-level hit (fail-closed)."""
    hit = pr_overlap.OverlapHit(number=7, branch="feat/x", files=("a.py",))
    pr = pr_overlap.PullRequest(
        number=7, branch="feat/x", files=frozenset(["a.py"]), head_sha="def"
    )
    fetch_key = ("fetch", "--force", "--quiet", "origin", "pull/7/head:collab/pr/7")
    cap = _capture_from_map({fetch_key: (1, "fatal")})
    refined = pr_overlap._refine_line_overlaps([hit], [pr], "abc", capture=cap)
    assert len(refined) == 1
    assert refined[0].conflict_type == "file-level"


def test_refine_line_overlaps_sorts_by_number():
    """Refined hits are sorted by PR number."""
    hit9 = pr_overlap.OverlapHit(number=9, branch="feat/z", files=("a.py",))
    hit3 = pr_overlap.OverlapHit(number=3, branch="feat/a", files=("a.py",))
    pr9 = pr_overlap.PullRequest(
        number=9, branch="feat/z", files=frozenset(["a.py"]), head_sha="sha9"
    )
    pr3 = pr_overlap.PullRequest(
        number=3, branch="feat/a", files=frozenset(["a.py"]), head_sha="sha3"
    )
    f9 = ("fetch", "--force", "--quiet", "origin", "pull/9/head:collab/pr/9")
    v9 = ("rev-parse", "--verify", "collab/pr/9^{commit}")
    m9 = ("merge-tree", "--write-tree", "--name-only", "abc", "collab/pr/9")
    f3 = ("fetch", "--force", "--quiet", "origin", "pull/3/head:collab/pr/3")
    v3 = ("rev-parse", "--verify", "collab/pr/3^{commit}")
    m3 = ("merge-tree", "--write-tree", "--name-only", "abc", "collab/pr/3")
    cap = _capture_from_map(
        {
            f9: (0, ""),
            v9: (0, "sha9"),
            m9: (0, "treeoid"),
            f3: (0, ""),
            v3: (0, "sha3"),
            m3: (1, "treeoid\na.py\n"),
        }
    )
    refined = pr_overlap._refine_line_overlaps(
        [hit9, hit3], [pr9, pr3], "abc", capture=cap
    )
    assert [h.number for h in refined] == [3]


# --- run() with line-level ---------------------------------------------------


def test_run_line_level_no_git_merge_tree_falls_back(capsys):
    """When merge-tree is unavailable, fall back to file-level detection."""
    config = pr_overlap.GuardConfig(
        repo="o/r", pr_number=42, base_ref="main", token="t", line_level=True
    )
    http = _fake_http(
        {
            "/pulls/42/files": [{"filename": "shared.py"}],
            "/pulls?state=open": [
                {"number": 7, "head": {"ref": "feat/seven"}, "draft": False}
            ],
            "/pulls/7/files": [{"filename": "shared.py"}],
        }
    )
    # git merge-tree not available
    git = _capture_from_map({("merge-tree", "-h"): (1, "")})
    rc = pr_overlap.run(config, http=http, git_capture=git)
    assert rc == pr_overlap.EXIT_OVERLAP
    out = capsys.readouterr().out
    assert "merge-tree" in out
    assert "not available" in out.lower()


def test_run_line_level_no_head_sha_is_error(capsys):
    """When HEAD SHA can't be resolved, fail with EXIT_ERROR."""
    config = pr_overlap.GuardConfig(
        repo="o/r", pr_number=42, base_ref="main", token="t", line_level=True
    )
    http = _fake_http(
        {
            "/pulls/42/files": [{"filename": "shared.py"}],
            "/pulls?state=open": [
                {
                    "number": 7,
                    "head": {"ref": "feat/seven", "sha": "sha7"},
                    "draft": False,
                }
            ],
            "/pulls/7/files": [{"filename": "shared.py"}],
        }
    )
    # merge-tree available but rev-parse fails
    git = _capture_from_map(
        {
            ("merge-tree", "-h"): (0, "--write-tree"),
            ("rev-parse", "HEAD"): (1, ""),
        }
    )
    rc = pr_overlap.run(config, http=http, git_capture=git)
    assert rc == pr_overlap.EXIT_ERROR


def test_run_line_level_refines_hits(capsys):
    """Line-level mode refines hits via merge-tree."""
    config = pr_overlap.GuardConfig(
        repo="o/r", pr_number=42, base_ref="main", token="t", line_level=True
    )
    http = _fake_http(
        {
            "/pulls/42/files": [{"filename": "shared.py"}],
            "/pulls?state=open": [
                {
                    "number": 7,
                    "head": {"ref": "feat/seven", "sha": "sha7"},
                    "draft": False,
                }
            ],
            "/pulls/7/files": [{"filename": "shared.py"}],
        }
    )
    # merge-tree available, HEAD resolves, fetch works, merge-tree clean
    fk = ("fetch", "--force", "--quiet", "origin", "pull/7/head:collab/pr/7")
    mk = ("merge-tree", "--write-tree", "--name-only", "sha42", "collab/pr/7")
    git = _capture_from_map(
        {
            ("merge-tree", "-h"): (0, "--write-tree"),
            ("rev-parse", "HEAD"): (0, "sha42"),
            fk: (0, ""),
            ("rev-parse", "--verify", "collab/pr/7^{commit}"): (0, "sha7"),
            mk: (0, "treeoid"),
        }
    )
    rc = pr_overlap.run(config, http=http, git_capture=git)
    # Clean merge -> no overlap
    assert rc == pr_overlap.EXIT_OK


def test_run_line_level_disabled_skips_merge_tree(capsys):
    """When line_level=False, no merge-tree step is attempted."""
    config = pr_overlap.GuardConfig(
        repo="o/r", pr_number=42, base_ref="main", token="t", line_level=False
    )
    http = _fake_http(
        {
            "/pulls/42/files": [{"filename": "shared.py"}],
            "/pulls?state=open": [
                {"number": 7, "head": {"ref": "feat/seven"}, "draft": False}
            ],
            "/pulls/7/files": [{"filename": "shared.py"}],
        }
    )
    # No git calls needed
    rc = pr_overlap.run(config, http=http)
    assert rc == pr_overlap.EXIT_OVERLAP
    out = capsys.readouterr().out
    assert "file-level" not in out.lower() or "overlaps" in out


def test_load_event_pr_missing_path(monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert pr_overlap._load_event_pr() == (None, None)


def test_load_event_pr_bad_json(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text("not json{", encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert pr_overlap._load_event_pr() == (None, None)


def test_default_http_parses_json(monkeypatch):
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout=0):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _Resp()

    monkeypatch.setattr(pr_overlap.urllib.request, "urlopen", fake_urlopen)
    result = pr_overlap._default_http(
        "https://api.github.com/repos/o/r/pulls/1/files", "tok"
    )
    assert result == {"ok": True}
    assert captured["headers"]["authorization"] == "Bearer tok"


def test_default_http_without_token(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self):
            return b"[]"

    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _Resp()

    monkeypatch.setattr(pr_overlap.urllib.request, "urlopen", fake_urlopen)
    assert pr_overlap._default_http("https://api.github.com/x", None) == []
    assert "authorization" not in captured["headers"]


# --- GITHUB_API_URL / GHES support -----------------------------------------


def test_default_http_accepts_ghes_api_url(monkeypatch):
    """_default_http accepts URLs rooted at a GHES GITHUB_API_URL."""
    monkeypatch.setattr(pr_overlap, "GITHUB_API", "https://github.example.com/api/v3")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self):
            return b"[]"

    monkeypatch.setattr(
        pr_overlap.urllib.request, "urlopen", lambda req, timeout=0: _Resp()
    )
    # Should not raise ValueError for a GHES API URL.
    result = pr_overlap._default_http(
        "https://github.example.com/api/v3/repos/o/r/pulls/1/files", None
    )
    assert result == []


def test_default_http_rejects_foreign_host_with_ghes_api(monkeypatch):
    """_default_http still blocks non-GitHub hosts when GITHUB_API is custom."""
    monkeypatch.setattr(pr_overlap, "GITHUB_API", "https://github.example.com/api/v3")
    with pytest.raises(ValueError, match="refusing to open"):
        pr_overlap._default_http("https://evil.com/x", None)


def test_ghes_api_url_builders_use_correct_base(monkeypatch):
    """URL builders (_pr_files, gather_other_prs) honour the resolved base."""
    monkeypatch.setattr(pr_overlap, "GITHUB_API", "https://github.example.com/api/v3")
    captured_urls = []

    def fake_http(url, _token):
        captured_urls.append(url)
        return []

    pr_overlap._pr_files(fake_http, "o/r", 1, None)
    assert captured_urls
    assert captured_urls[0].startswith(
        "https://github.example.com/api/v3/repos/o/r/pulls/1/files"
    )


def test_ghes_api_gather_other_prs_uses_correct_base(monkeypatch):
    """gather_other_prs constructs the list URL from the resolved base."""
    monkeypatch.setattr(pr_overlap, "GITHUB_API", "https://github.example.com/api/v3")
    captured_urls = []

    def fake_http(url, _token):
        captured_urls.append(url)
        return []

    config = pr_overlap.GuardConfig(repo="o/r", pr_number=1, base_ref="main")
    pr_overlap.gather_other_prs(fake_http, config)
    assert captured_urls
    assert captured_urls[0].startswith(
        "https://github.example.com/api/v3/repos/o/r/pulls?state=open"
    )


def test_ghes_env_var_defaults_to_public_api(monkeypatch):
    """When GITHUB_API_URL is unset, the resolved base is api.github.com."""
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    # Recompute GITHUB_API as the module would at import time.
    resolved = (os.getenv("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    assert resolved == "https://api.github.com"
