"""Unit tests for collab.overlap cross-branch file overlap detection."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from collab import overlap


def _capture_from_map(
    responses: dict[tuple[str, ...], tuple[int, str]],
) -> overlap.GitCapture:
    def capture(args):
        key = tuple(args)
        return responses.get(key, (1, ""))

    return capture


def test_is_overlap_check_enabled_defaults_on(monkeypatch):
    monkeypatch.delenv("COLLAB_OVERLAP_CHECK", raising=False)
    assert overlap.is_overlap_check_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_is_overlap_check_disabled_by_env(monkeypatch, value):
    monkeypatch.setenv("COLLAB_OVERLAP_CHECK", value)
    assert overlap.is_overlap_check_enabled() is False


def test_detect_overlap_disabled_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_OVERLAP_CHECK", "0")

    def boom(_args):
        raise AssertionError("git should not run when disabled")

    reports = overlap.detect_cross_branch_overlaps(
        tmp_path, git_capture=boom  # type: ignore[arg-type]
    )
    assert reports == []


def test_detect_overlap_finds_shared_files(monkeypatch, tmp_path):
    monkeypatch.delenv("COLLAB_OVERLAP_CHECK", raising=False)
    capture = _capture_from_map(
        {
            ("rev-parse", "--verify", "origin/main"): (0, "origin/main"),
            ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat/current"),
            ("rev-parse", "HEAD"): (0, "sha-head"),
            ("merge-base", "HEAD", "origin/main"): (0, "base-sha"),
            (
                "diff",
                "--name-only",
                "base-sha...HEAD",
            ): (0, "AGENTS.md\nREADME.md"),
            (
                "for-each-ref",
                "--format=%(refname:short) %(objectname)",
                "refs/heads",
                "refs/remotes/origin",
            ): (
                0,
                (
                    "feat/current sha-head\n"
                    "feat/other sha-other\n"
                    "origin/feat/other sha-other"
                ),
            ),
            ("rev-parse", "feat/other"): (0, "sha-other"),
            ("rev-list", "--count", "origin/main..feat/other"): (0, "3"),
            ("merge-base", "feat/other", "origin/main"): (0, "base-other"),
            (
                "diff",
                "--name-only",
                "base-other...feat/other",
            ): (0, "AGENTS.md\nGIT_WORKFLOW.md"),
        }
    )

    reports = overlap.detect_cross_branch_overlaps(tmp_path, git_capture=capture)
    assert len(reports) == 1
    assert reports[0].branch == "feat/other"
    assert reports[0].files == ("AGENTS.md",)


def test_detect_overlap_ignores_merged_branch(monkeypatch, tmp_path):
    capture = _capture_from_map(
        {
            ("rev-parse", "--verify", "origin/main"): (0, "origin/main"),
            ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat/current"),
            ("rev-parse", "HEAD"): (0, "sha-head"),
            ("merge-base", "HEAD", "origin/main"): (0, "base-sha"),
            ("diff", "--name-only", "base-sha...HEAD"): (0, "AGENTS.md"),
            (
                "for-each-ref",
                "--format=%(refname:short) %(objectname)",
                "refs/heads",
                "refs/remotes/origin",
            ): (0, "feat/merged sha-merged"),
            ("rev-parse", "feat/merged"): (0, "sha-merged"),
            ("rev-list", "--count", "origin/main..feat/merged"): (0, "0"),
        }
    )

    reports = overlap.detect_cross_branch_overlaps(tmp_path, git_capture=capture)
    assert reports == []


def test_detect_overlap_skips_same_commit_branch(monkeypatch, tmp_path):
    capture = _capture_from_map(
        {
            ("rev-parse", "--verify", "origin/main"): (0, "origin/main"),
            ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat/current"),
            ("rev-parse", "HEAD"): (0, "sha-head"),
            ("merge-base", "HEAD", "origin/main"): (0, "base-sha"),
            ("diff", "--name-only", "base-sha...HEAD"): (0, "AGENTS.md"),
            (
                "for-each-ref",
                "--format=%(refname:short) %(objectname)",
                "refs/heads",
                "refs/remotes/origin",
            ): (0, "origin/feat/current sha-head"),
            ("rev-parse", "origin/feat/current"): (0, "sha-head"),
        }
    )

    reports = overlap.detect_cross_branch_overlaps(tmp_path, git_capture=capture)
    assert reports == []


def test_detect_overlap_fail_open_on_missing_base(monkeypatch, tmp_path):
    capture = _capture_from_map(
        {
            ("rev-parse", "--verify", "origin/main"): (1, ""),
            ("rev-parse", "--verify", "origin/master"): (1, ""),
        }
    )
    reports = overlap.detect_cross_branch_overlaps(tmp_path, git_capture=capture)
    assert reports == []


def test_detect_overlap_fail_open_on_head_diff_error(monkeypatch, tmp_path):
    capture = _capture_from_map(
        {
            ("rev-parse", "--verify", "origin/main"): (0, "origin/main"),
            ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat/current"),
            ("rev-parse", "HEAD"): (0, "sha-head"),
            ("merge-base", "HEAD", "origin/main"): (1, ""),
        }
    )
    reports = overlap.detect_cross_branch_overlaps(tmp_path, git_capture=capture)
    assert reports == []


def test_resolve_base_ref_honors_env_override(monkeypatch):
    monkeypatch.setenv("COLLAB_LOCK_BASE_REF", "origin/develop")
    capture = _capture_from_map(
        {("rev-parse", "--verify", "origin/develop"): (0, "origin/develop")}
    )
    assert overlap.resolve_base_ref(capture) == "origin/develop"


def test_format_overlap_warning_singular_and_plural():
    one = overlap.format_overlap_warning(
        overlap.OverlapReport(branch="feat/a", files=("AGENTS.md",))
    )
    assert "AGENTS.md` is also modified" in one

    many = overlap.format_overlap_warning(
        overlap.OverlapReport(branch="feat/b", files=("AGENTS.md", "GIT_WORKFLOW.md"))
    )
    assert "are also modified" in many
    assert "feat/b" in many


def test_warn_cross_branch_overlap_advisory_returns_zero(monkeypatch, tmp_path):
    monkeypatch.delenv("COLLAB_OVERLAP_STRICT", raising=False)
    monkeypatch.setattr(overlap, "refresh_remote_state", lambda *_a, **_k: True)
    monkeypatch.setattr(
        overlap,
        "detect_cross_branch_overlaps",
        lambda *_a, **_k: [overlap.OverlapReport(branch="feat/x", files=("a.py",))],
    )
    emitted: list[str] = []
    assert overlap.warn_cross_branch_overlap(tmp_path, emit=emitted.append) == 0
    assert emitted


def test_warn_cross_branch_overlap_fail_open_on_error(monkeypatch, tmp_path):
    """Outside strict mode an error fails open (returns 0)."""
    monkeypatch.delenv("COLLAB_OVERLAP_STRICT", raising=False)
    monkeypatch.setattr(overlap, "refresh_remote_state", lambda *_a, **_k: True)

    def boom(_root):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(overlap, "detect_cross_branch_overlaps", boom)
    emitted: list[str] = []
    assert overlap.warn_cross_branch_overlap(tmp_path, emit=emitted.append) == 0
    assert any("failed" in line.lower() for line in emitted)


def test_warn_cross_branch_overlap_fail_closed_on_error_in_strict(
    monkeypatch, tmp_path
):
    """Strict mode must fail closed (EXIT_ERROR) on an unexpected error."""
    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", "1")
    monkeypatch.setattr(overlap, "refresh_remote_state", lambda *_a, **_k: True)

    def boom(_root):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(overlap, "detect_cross_branch_overlaps", boom)
    emitted: list[str] = []
    rc = overlap.warn_cross_branch_overlap(tmp_path, emit=emitted.append)
    assert rc == overlap.EXIT_ERROR
    assert any("failed" in line.lower() for line in emitted)


def test_warn_cross_branch_overlap_fail_closed_when_fetch_fails_in_strict(
    monkeypatch, tmp_path
):
    """Strict mode blocks when remote state cannot be refreshed."""
    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", "1")
    monkeypatch.setattr(overlap, "refresh_remote_state", lambda *_a, **_k: False)

    def must_not_run(*_a, **_k):
        raise AssertionError("detection should not run when fetch fails in strict mode")

    monkeypatch.setattr(overlap, "detect_cross_branch_overlaps", must_not_run)
    emitted: list[str] = []
    rc = overlap.warn_cross_branch_overlap(tmp_path, emit=emitted.append)
    assert rc == overlap.EXIT_ERROR
    assert any("git fetch" in line.lower() for line in emitted)


def test_warn_cross_branch_overlap_advisory_tolerates_fetch_failure(
    monkeypatch, tmp_path
):
    """Outside strict mode a fetch failure only warns; detection still runs."""
    monkeypatch.delenv("COLLAB_OVERLAP_STRICT", raising=False)
    monkeypatch.setattr(overlap, "refresh_remote_state", lambda *_a, **_k: False)
    monkeypatch.setattr(
        overlap,
        "detect_cross_branch_overlaps",
        lambda *_a, **_k: [overlap.OverlapReport(branch="feat/x", files=("a.py",))],
    )
    emitted: list[str] = []
    assert overlap.warn_cross_branch_overlap(tmp_path, emit=emitted.append) == 0
    assert any("git fetch" in line.lower() for line in emitted)


def test_branch_display_name_strips_origin_prefix():
    assert overlap._branch_display_name("origin/feat/x") == "feat/x"
    assert overlap._branch_display_name("feat/x") == "feat/x"


def test_current_branch_detached_head():
    capture = _capture_from_map(
        {
            ("rev-parse", "--abbrev-ref", "HEAD"): (0, "HEAD"),
            ("rev-parse", "HEAD"): (0, "detached-sha"),
        }
    )
    assert overlap._current_branch(capture) == (None, "detached-sha")


def test_current_branch_git_failure():
    capture = _capture_from_map({("rev-parse", "--abbrev-ref", "HEAD"): (1, "")})
    assert overlap._current_branch(capture) == (None, None)


def test_changed_files_since_base_handles_empty_diff():
    capture = _capture_from_map(
        {
            ("merge-base", "HEAD", "origin/main"): (0, "base"),
            ("diff", "--name-only", "base...HEAD"): (0, ""),
        }
    )
    assert (
        overlap._changed_files_since_base(capture, "HEAD", "origin/main") == frozenset()
    )


def test_is_unmerged_git_failure():
    capture = _capture_from_map(
        {("rev-list", "--count", "origin/main..feat/x"): (1, "")}
    )
    assert overlap._is_unmerged(capture, "feat/x", "origin/main") is False


def test_list_candidate_refs_git_failure():
    capture = _capture_from_map(
        {
            (
                "for-each-ref",
                "--format=%(refname:short) %(objectname)",
                "refs/heads",
                "refs/remotes/origin",
            ): (1, ""),
        }
    )
    assert overlap._list_candidate_refs(capture) == []


def test_list_candidate_refs_skips_empty_ref_or_commit():
    capture = _capture_from_map(
        {
            (
                "for-each-ref",
                "--format=%(refname:short) %(objectname)",
                "refs/heads",
                "refs/remotes/origin",
            ): (0, "HEAD \nfeat/x \n"),
        }
    )
    assert overlap._list_candidate_refs(capture) == []


def test_detect_overlap_returns_empty_when_head_unresolved(monkeypatch, tmp_path):
    capture = _capture_from_map(
        {
            ("rev-parse", "--verify", "origin/main"): (0, "origin/main"),
            ("rev-parse", "--abbrev-ref", "HEAD"): (1, ""),
            ("rev-parse", "HEAD"): (1, ""),
        }
    )
    reports = overlap.detect_cross_branch_overlaps(tmp_path, git_capture=capture)
    assert reports == []


def test_is_unmerged_invalid_count():
    capture = _capture_from_map(
        {("rev-list", "--count", "origin/main..feat/x"): (0, "not-a-number")}
    )
    assert overlap._is_unmerged(capture, "feat/x", "origin/main") is False


def test_list_candidate_refs_skips_noise_and_prefers_local():
    capture = _capture_from_map(
        {
            (
                "for-each-ref",
                "--format=%(refname:short) %(objectname)",
                "refs/heads",
                "refs/remotes/origin",
            ): (
                0,
                (
                    "origin/HEAD dead\n"
                    "bad-line\n"
                    "origin/feat/x sha1\n"
                    "feat/x sha1\n"
                    "  \n"
                ),
            ),
        }
    )
    refs = overlap._list_candidate_refs(capture)
    assert refs == [("feat/x", "feat/x")]


def test_detect_overlap_skips_current_and_base_branches(monkeypatch, tmp_path):
    capture = _capture_from_map(
        {
            ("rev-parse", "--verify", "origin/main"): (0, "origin/main"),
            ("rev-parse", "--abbrev-ref", "HEAD"): (0, "main"),
            ("rev-parse", "HEAD"): (0, "sha-head"),
            ("merge-base", "HEAD", "origin/main"): (0, "base-sha"),
            ("diff", "--name-only", "base-sha...HEAD"): (0, "AGENTS.md"),
            (
                "for-each-ref",
                "--format=%(refname:short) %(objectname)",
                "refs/heads",
                "refs/remotes/origin",
            ): (0, "main sha-head\norigin/main sha-base"),
            ("rev-parse", "main"): (0, "sha-head"),
            ("rev-parse", "origin/main"): (0, "sha-base"),
            ("rev-list", "--count", "origin/main..origin/main"): (0, "0"),
        }
    )
    reports = overlap.detect_cross_branch_overlaps(tmp_path, git_capture=capture)
    assert reports == []


def test_detect_overlap_skips_branch_when_other_diff_empty(monkeypatch, tmp_path):
    capture = _capture_from_map(
        {
            ("rev-parse", "--verify", "origin/main"): (0, "origin/main"),
            ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat/current"),
            ("rev-parse", "HEAD"): (0, "sha-head"),
            ("merge-base", "HEAD", "origin/main"): (0, "base-sha"),
            ("diff", "--name-only", "base-sha...HEAD"): (0, "AGENTS.md"),
            (
                "for-each-ref",
                "--format=%(refname:short) %(objectname)",
                "refs/heads",
                "refs/remotes/origin",
            ): (0, "feat/other sha-other"),
            ("rev-parse", "feat/other"): (0, "sha-other"),
            ("rev-list", "--count", "origin/main..feat/other"): (0, "2"),
            ("merge-base", "feat/other", "origin/main"): (0, "base-other"),
            ("diff", "--name-only", "base-other...feat/other"): (0, "OTHER.md"),
        }
    )
    reports = overlap.detect_cross_branch_overlaps(tmp_path, git_capture=capture)
    assert reports == []


def test_detect_overlap_skips_branch_when_rev_parse_fails(monkeypatch, tmp_path):
    capture = _capture_from_map(
        {
            ("rev-parse", "--verify", "origin/main"): (0, "origin/main"),
            ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat/current"),
            ("rev-parse", "HEAD"): (0, "sha-head"),
            ("merge-base", "HEAD", "origin/main"): (0, "base-sha"),
            ("diff", "--name-only", "base-sha...HEAD"): (0, "AGENTS.md"),
            (
                "for-each-ref",
                "--format=%(refname:short) %(objectname)",
                "refs/heads",
                "refs/remotes/origin",
            ): (0, "feat/broken sha-broken"),
            ("rev-parse", "feat/broken"): (1, ""),
        }
    )
    reports = overlap.detect_cross_branch_overlaps(tmp_path, git_capture=capture)
    assert reports == []


def test_detect_overlap_uses_default_git_capture(monkeypatch, tmp_path):
    monkeypatch.delenv("COLLAB_OVERLAP_CHECK", raising=False)
    calls: list[tuple[str, ...]] = []

    def fake_capture(argv, **kwargs):
        calls.append(tuple(argv))
        return type(
            "R",
            (),
            {"returncode": 1, "stdout": b"", "stderr": b"", "timed_out": False},
        )()

    monkeypatch.setattr(overlap.safe_subprocess, "capture", fake_capture)
    overlap.detect_cross_branch_overlaps(tmp_path)
    assert calls and calls[0][0] == "git"


def test_warn_cross_branch_overlap_disabled_returns_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_OVERLAP_CHECK", "0")
    emitted: list[str] = []
    assert overlap.warn_cross_branch_overlap(tmp_path, emit=emitted.append) == 0
    assert emitted == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git executable not available")
def test_detect_overlap_real_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "overlap@test"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Overlap Tester"],
        cwd=repo,
        check=True,
    )

    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    (repo / "only-main.txt").write_text("main\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["git", "branch", "feat/other"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "feat/current"],
        cwd=repo,
        check=True,
    )
    (repo / "shared.txt").write_text("current branch\n", encoding="utf-8")
    subprocess.run(["git", "add", "shared.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "current change"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["git", "checkout", "feat/other"],
        cwd=repo,
        check=True,
    )
    (repo / "shared.txt").write_text("other branch\n", encoding="utf-8")
    subprocess.run(["git", "add", "shared.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "other change"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["git", "checkout", "feat/current"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(repo)],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "main"],
        cwd=repo,
        check=True,
    )

    reports = overlap.detect_cross_branch_overlaps(repo)
    assert len(reports) == 1
    assert reports[0].branch == "feat/other"
    assert reports[0].files == ("shared.txt",)


def test_detect_overlap_excludes_stacked_ancestor_branch(tmp_path):
    """A branch HEAD is stacked on top of is not a false-positive overlap."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "overlap@test")
    git("config", "user.name", "Overlap Tester")
    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "initial")

    # feat/base edits shared.txt; feat/stack branches FROM feat/base and edits it
    # again, so HEAD (feat/stack) fully contains feat/base.
    git("checkout", "-b", "feat/base")
    (repo / "shared.txt").write_text("base edit\n", encoding="utf-8")
    git("commit", "-am", "base edit")
    git("checkout", "-b", "feat/stack")
    (repo / "shared.txt").write_text("base edit\nstack edit\n", encoding="utf-8")
    git("commit", "-am", "stack edit")

    git("remote", "add", "origin", str(repo))
    git("update-ref", "refs/remotes/origin/main", "main")

    reports = overlap.detect_cross_branch_overlaps(repo)
    # feat/base is an ancestor of HEAD -> excluded (no false positive).
    assert reports == []


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "ON", "  Yes  "])
def test_is_overlap_strict_enabled_truthy(monkeypatch, value):
    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", value)
    assert overlap.is_overlap_strict_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "maybe"])
def test_is_overlap_strict_disabled(monkeypatch, value):
    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", value)
    assert overlap.is_overlap_strict_enabled() is False


def test_is_overlap_strict_enabled_default(monkeypatch):
    monkeypatch.delenv("COLLAB_OVERLAP_STRICT", raising=False)
    assert overlap.is_overlap_strict_enabled() is False


def test_strict_implies_check_even_when_check_disabled(monkeypatch):
    """Setting STRICT=1 forces checking on even if CHECK is falsey (no silent trap)."""
    monkeypatch.setenv("COLLAB_OVERLAP_CHECK", "0")
    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", "1")
    assert overlap.is_overlap_check_enabled() is True

    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", "0")
    assert overlap.is_overlap_check_enabled() is False


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_is_overlap_fetch_disabled_by_env(monkeypatch, value):
    monkeypatch.setenv("COLLAB_OVERLAP_FETCH", value)
    assert overlap.is_overlap_fetch_enabled() is False


def test_is_overlap_fetch_auto_follows_strict(monkeypatch):
    """Default 'auto' fetches only in strict mode (no per-push network otherwise)."""
    monkeypatch.delenv("COLLAB_OVERLAP_FETCH", raising=False)
    monkeypatch.delenv("COLLAB_OVERLAP_STRICT", raising=False)
    assert overlap.is_overlap_fetch_enabled() is False

    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", "1")
    assert overlap.is_overlap_fetch_enabled() is True


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_is_overlap_fetch_forced_on(monkeypatch, value):
    monkeypatch.delenv("COLLAB_OVERLAP_STRICT", raising=False)
    monkeypatch.setenv("COLLAB_OVERLAP_FETCH", value)
    assert overlap.is_overlap_fetch_enabled() is True


def test_warn_cross_branch_overlap_returns_overlap_code_in_strict_mode(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(overlap, "refresh_remote_state", lambda *_a, **_k: True)
    monkeypatch.setattr(
        overlap,
        "detect_cross_branch_overlaps",
        lambda *_a, **_k: [overlap.OverlapReport(branch="feat/x", files=("a.py",))],
    )
    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", "1")
    emitted: list[str] = []
    assert (
        overlap.warn_cross_branch_overlap(tmp_path, emit=emitted.append)
        == overlap.EXIT_OVERLAP
    )
    # Escape-hatch guidance must be surfaced when strict mode blocks.
    assert any("COLLAB_OVERLAP_STRICT=0" in line for line in emitted)

    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", "0")
    emitted.clear()
    assert overlap.warn_cross_branch_overlap(tmp_path, emit=emitted.append) == 0


def test_format_overlap_warning_is_ascii_only():
    """Warning text must encode under cp1252 (no emoji/em-dash that crash on
    Windows)."""
    line = overlap.format_overlap_warning(
        overlap.OverlapReport(branch="feat/a", files=("a.py",))
    )
    line.encode("cp1252")  # must not raise
    assert line.isascii()


def test_refresh_remote_state_disabled_skips_fetch(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_OVERLAP_FETCH", "0")

    def must_not_run(_args):
        raise AssertionError("fetch should not run when COLLAB_OVERLAP_FETCH=0")

    assert overlap.refresh_remote_state(tmp_path, git_capture=must_not_run) is True


def test_refresh_remote_state_runs_fetch(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_OVERLAP_FETCH", "1")
    seen: list[tuple[str, ...]] = []

    def capture(args):
        seen.append(tuple(args))
        return (0, "")

    # Pass an explicit remote so resolve_remote() does not add probe calls.
    assert (
        overlap.refresh_remote_state(tmp_path, remote="origin", git_capture=capture)
        is True
    )
    assert seen == [("fetch", "--prune", "--quiet", "origin")]


def test_refresh_remote_state_fetches_resolved_remote(monkeypatch, tmp_path):
    """With no explicit remote, the upstream's remote is fetched."""
    monkeypatch.setenv("COLLAB_OVERLAP_FETCH", "1")
    monkeypatch.delenv("COLLAB_OVERLAP_REMOTE", raising=False)
    seen: list[tuple[str, ...]] = []

    def capture(args):
        seen.append(tuple(args))
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return (0, "upstream/main")
        return (0, "")

    assert overlap.refresh_remote_state(tmp_path, git_capture=capture) is True
    assert ("fetch", "--prune", "--quiet", "upstream") in seen


def test_refresh_remote_state_reports_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_OVERLAP_FETCH", "1")
    assert (
        overlap.refresh_remote_state(
            tmp_path, remote="origin", git_capture=lambda _a: (1, "")
        )
        is False
    )


def test_refresh_remote_state_handles_exception(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_OVERLAP_FETCH", "1")

    def boom(_args):
        raise RuntimeError("network down")

    assert overlap.refresh_remote_state(tmp_path, git_capture=boom) is False


# --- PR claims: env + git helpers ------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "ON"])
def test_is_pr_claims_enabled_truthy(monkeypatch, value):
    monkeypatch.setenv("COLLAB_PR_CLAIMS", value)
    assert overlap.is_pr_claims_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "maybe"])
def test_is_pr_claims_disabled(monkeypatch, value):
    monkeypatch.setenv("COLLAB_PR_CLAIMS", value)
    assert overlap.is_pr_claims_enabled() is False


def test_is_pr_claims_default_off(monkeypatch):
    monkeypatch.delenv("COLLAB_PR_CLAIMS", raising=False)
    assert overlap.is_pr_claims_enabled() is False


def test_stale_claim_branches_empty_input():
    assert (
        overlap.stale_claim_branches("/x", [], git_capture=lambda _a: (1, ""))
        == frozenset()
    )


def test_stale_claim_branches_classifies(monkeypatch):
    cap = _capture_from_map(
        {
            ("fetch", "--prune", "--quiet", "origin"): (0, ""),
            ("rev-parse", "--verify", "origin/main"): (0, "origin/main"),
            # gone: ref does not resolve
            ("rev-parse", "--verify", "origin/feat/gone"): (1, ""),
            # merged: ref resolves, 0 commits beyond base
            ("rev-parse", "--verify", "origin/feat/merged"): (0, "sha-m"),
            ("rev-list", "--count", "origin/main..origin/feat/merged"): (0, "0"),
            # open: ref resolves, commits beyond base
            ("rev-parse", "--verify", "origin/feat/open"): (0, "sha-o"),
            ("rev-list", "--count", "origin/main..origin/feat/open"): (0, "4"),
        }
    )
    stale = overlap.stale_claim_branches(
        "/x",
        ["feat/gone", "feat/merged", "feat/open"],
        remote="origin",
        git_capture=cap,
    )
    assert stale == frozenset({"feat/gone", "feat/merged"})


def test_stale_claim_branches_fetches_by_default():
    seen: list[tuple[str, ...]] = []

    def cap(args):
        seen.append(tuple(args))
        if args[:2] == ["rev-parse", "--verify"]:
            return (1, "")  # everything gone -> stale
        return (0, "")

    overlap.stale_claim_branches("/x", ["feat/x"], remote="origin", git_capture=cap)
    assert ("fetch", "--prune", "--quiet", "origin") in seen


def test_stale_claim_branches_skip_fetch():
    seen: list[tuple[str, ...]] = []

    def cap(args):
        seen.append(tuple(args))
        return (1, "")

    overlap.stale_claim_branches(
        "/x", ["feat/x"], remote="origin", git_capture=cap, fetch=False
    )
    assert not any(a and a[0] == "fetch" for a in seen)


def test_stale_claim_branches_resolves_remote_when_none(monkeypatch):
    monkeypatch.delenv("COLLAB_OVERLAP_REMOTE", raising=False)
    cap = _capture_from_map(
        {
            ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                0,
                "origin/main",
            ),
            ("fetch", "--prune", "--quiet", "origin"): (0, ""),
            ("rev-parse", "--verify", "origin/main"): (0, "origin/main"),
            ("rev-parse", "--verify", "origin/feat/gone"): (1, ""),
        }
    )
    # remote not passed -> resolve_remote() is exercised.
    result = overlap.stale_claim_branches("/x", ["feat/gone"], git_capture=cap)
    assert result == frozenset({"feat/gone"})


def test_stale_claim_branches_swallows_errors():
    def boom(_args):
        raise RuntimeError("git down")

    # Any failure -> empty set (never raises, never wrongly releases a claim).
    assert (
        overlap.stale_claim_branches("/x", ["feat/x"], git_capture=boom) == frozenset()
    )


def test_stale_claim_branches_tolerates_fetch_error():
    def cap(args):
        if args and args[0] == "fetch":
            raise RuntimeError("fetch boom")
        return (1, "")  # refs do not resolve -> gone -> stale

    result = overlap.stale_claim_branches(
        "/x", ["feat/x"], remote="origin", git_capture=cap
    )
    assert result == frozenset({"feat/x"})


def test_head_changed_files_returns_branch_and_sorted_files():
    cap = _capture_from_map(
        {
            ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat/cur"),
            ("rev-parse", "HEAD"): (0, "sha-head"),
            ("rev-parse", "--verify", "origin/main"): (0, "origin/main"),
            ("merge-base", "HEAD", "origin/main"): (0, "base"),
            ("diff", "--name-only", "base...HEAD"): (0, "z.py\na.py"),
        }
    )
    branch, files = overlap.head_changed_files("/x", remote="origin", git_capture=cap)
    assert branch == "feat/cur"
    assert files == ["a.py", "z.py"]


def test_head_changed_files_resolves_remote_when_none(monkeypatch):
    monkeypatch.delenv("COLLAB_OVERLAP_REMOTE", raising=False)
    cap = _capture_from_map(
        {
            ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                0,
                "origin/main",
            ),
            ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat/cur"),
            ("rev-parse", "HEAD"): (0, "sha-head"),
            ("rev-parse", "--verify", "origin/main"): (0, "origin/main"),
            ("merge-base", "HEAD", "origin/main"): (0, "base"),
            ("diff", "--name-only", "base...HEAD"): (0, "a.py"),
        }
    )
    # remote not passed -> resolve_remote() path exercised.
    branch, files = overlap.head_changed_files("/x", git_capture=cap)
    assert branch == "feat/cur"
    assert files == ["a.py"]


def test_head_changed_files_empty_when_no_base():
    cap = _capture_from_map(
        {
            ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat/cur"),
            ("rev-parse", "HEAD"): (0, "sha-head"),
            ("rev-parse", "--verify", "origin/main"): (1, ""),
            ("rev-parse", "--verify", "origin/master"): (1, ""),
        }
    )
    branch, files = overlap.head_changed_files("/x", remote="origin", git_capture=cap)
    assert files == []


# ============================================================================
# Tests for functions merged from overlap_merge.py
# ============================================================================


# --- is_pr_line_level_enabled ----------------------------------------------


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_is_pr_line_level_disabled(monkeypatch, value):
    monkeypatch.setenv("COLLAB_PR_OVERLAP_LINE_LEVEL", value)
    assert overlap.is_pr_line_level_enabled() is False


def test_is_pr_line_level_defaults_on(monkeypatch):
    monkeypatch.delenv("COLLAB_PR_OVERLAP_LINE_LEVEL", raising=False)
    assert overlap.is_pr_line_level_enabled() is True


# --- git_version_supports_merge_tree ---------------------------------------


def test_git_supports_merge_tree_yes():
    cap = _capture_from_map(
        {("merge-tree", "-h"): (0, "usage: git merge-tree ...\n  --write-tree")}
    )
    assert overlap.git_version_supports_merge_tree(cap) is True


def test_git_supports_merge_tree_no_flag():
    cap = _capture_from_map(
        {("merge-tree", "-h"): (0, "usage: git merge-tree ...\n  --trivial-merge")}
    )
    assert overlap.git_version_supports_merge_tree(cap) is False


def test_git_supports_merge_tree_error():
    cap = _capture_from_map({("merge-tree", "-h"): (128, "")})
    assert overlap.git_version_supports_merge_tree(cap) is False


# --- fetch_pr_ref ----------------------------------------------------------


def test_fetch_pr_ref_success():
    cap = _capture_from_map(
        {
            (
                "fetch",
                "--force",
                "--quiet",
                "origin",
                "pull/7/head:collab/pr/7",
            ): (0, ""),
            ("rev-parse", "--verify", "collab/pr/7^{commit}"): (0, "abc123"),
        }
    )
    ref = overlap.fetch_pr_ref(cap, "origin", 7, "abc123")
    assert ref == "collab/pr/7"


def test_fetch_pr_ref_fails_on_fetch_error():
    cap = _capture_from_map(
        {
            (
                "fetch",
                "--force",
                "--quiet",
                "origin",
                "pull/7/head:collab/pr/7",
            ): (1, "fatal"),
        }
    )
    assert overlap.fetch_pr_ref(cap, "origin", 7, "abc123") is None


def test_fetch_pr_ref_fails_on_verify_error():
    cap = _capture_from_map(
        {
            (
                "fetch",
                "--force",
                "--quiet",
                "origin",
                "pull/7/head:collab/pr/7",
            ): (0, ""),
            ("rev-parse", "--verify", "collab/pr/7^{commit}"): (1, ""),
        }
    )
    assert overlap.fetch_pr_ref(cap, "origin", 7, "abc123") is None


def test_fetch_pr_ref_sha_mismatch_still_returns_ref():
    """SHA mismatch logs warning but still returns the ref (usable)."""
    cap = _capture_from_map(
        {
            (
                "fetch",
                "--force",
                "--quiet",
                "origin",
                "pull/7/head:collab/pr/7",
            ): (0, ""),
            ("rev-parse", "--verify", "collab/pr/7^{commit}"): (0, "different-sha"),
        }
    )
    ref = overlap.fetch_pr_ref(cap, "origin", 7, "abc123")
    assert ref == "collab/pr/7"  # Still returned despite mismatch


# --- resolve_remote ---------------------------------------------------------


def test_resolve_remote_env_override(monkeypatch):
    monkeypatch.setenv("COLLAB_OVERLAP_REMOTE", "fork")
    assert overlap.resolve_remote(lambda _a: (1, ""), preferred="origin") == "fork"


def test_resolve_remote_preferred_used(monkeypatch):
    monkeypatch.delenv("COLLAB_OVERLAP_REMOTE", raising=False)
    assert (
        overlap.resolve_remote(lambda _a: (1, ""), preferred="upstream") == "upstream"
    )


def test_resolve_remote_ignores_url_preferred(monkeypatch):
    monkeypatch.delenv("COLLAB_OVERLAP_REMOTE", raising=False)
    cap = _capture_from_map({("remote",): (0, "origin")})
    # A URL push target is not a remote name -> fall through to detection.
    assert overlap.resolve_remote(cap, preferred="https://x/y.git") == "origin"


def test_resolve_remote_from_upstream(monkeypatch):
    monkeypatch.delenv("COLLAB_OVERLAP_REMOTE", raising=False)
    cap = _capture_from_map(
        {
            ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                0,
                "upstream/main",
            )
        }
    )
    assert overlap.resolve_remote(cap) == "upstream"


def test_resolve_remote_sole_remote(monkeypatch):
    monkeypatch.delenv("COLLAB_OVERLAP_REMOTE", raising=False)
    cap = _capture_from_map({("remote",): (0, "fork")})
    assert overlap.resolve_remote(cap) == "fork"


def test_resolve_remote_prefers_origin_among_many(monkeypatch):
    monkeypatch.delenv("COLLAB_OVERLAP_REMOTE", raising=False)
    cap = _capture_from_map({("remote",): (0, "upstream\norigin\nfork")})
    assert overlap.resolve_remote(cap) == "origin"


def test_resolve_remote_fallback_origin(monkeypatch):
    monkeypatch.delenv("COLLAB_OVERLAP_REMOTE", raising=False)
    assert overlap.resolve_remote(lambda _a: (1, "")) == "origin"


# --- merge_tree_conflicts --------------------------------------------------


def test_merge_tree_clean_returns_empty():
    cap = _capture_from_map(
        {
            ("merge-tree", "--write-tree", "--name-only", "HEAD", "feat/x"): (
                0,
                "treeoid",
            )
        }
    )
    assert overlap.merge_tree_conflicts(cap, "HEAD", "feat/x") == frozenset()


def test_merge_tree_conflict_lists_files():
    cap = _capture_from_map(
        {
            ("merge-tree", "--write-tree", "--name-only", "HEAD", "feat/x"): (
                1,
                "treeoid\nfoo.py\nbar.py\n\nAuto-merging foo.py",
            )
        }
    )
    assert overlap.merge_tree_conflicts(cap, "HEAD", "feat/x") == frozenset(
        {"foo.py", "bar.py"}
    )


def test_merge_tree_conflict_unparseable_returns_none():
    # rc==1 but no parseable names -> inconclusive -> fall back to file-level.
    cap = _capture_from_map(
        {("merge-tree", "--write-tree", "--name-only", "HEAD", "feat/x"): (1, "")}
    )
    assert overlap.merge_tree_conflicts(cap, "HEAD", "feat/x") is None


def test_merge_tree_unsupported_returns_none():
    # Old git: unknown option -> rc 128/129 -> None (fall back to file-level).
    cap = _capture_from_map(
        {("merge-tree", "--write-tree", "--name-only", "HEAD", "feat/x"): (128, "")}
    )
    assert overlap.merge_tree_conflicts(cap, "HEAD", "feat/x") is None


# --- line-level refinement in detect ---------------------------------------


def _line_level_map(merge_tree_result):
    return _capture_from_map(
        {
            ("rev-parse", "--verify", "origin/main"): (0, "origin/main"),
            ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat/current"),
            ("rev-parse", "HEAD"): (0, "sha-head"),
            ("merge-base", "HEAD", "origin/main"): (0, "base-sha"),
            ("diff", "--name-only", "base-sha...HEAD"): (0, "shared.py"),
            (
                "for-each-ref",
                "--format=%(refname:short) %(objectname)",
                "refs/heads",
                "refs/remotes/origin",
            ): (0, "feat/other sha-other"),
            ("rev-parse", "feat/other"): (0, "sha-other"),
            ("rev-list", "--count", "origin/main..feat/other"): (0, "2"),
            ("merge-base", "feat/other", "origin/main"): (0, "base-other"),
            ("diff", "--name-only", "base-other...feat/other"): (0, "shared.py"),
            (
                "merge-tree",
                "--write-tree",
                "--name-only",
                "HEAD",
                "feat/other",
            ): merge_tree_result,
        }
    )


def test_detect_line_level_drops_clean_merge(monkeypatch):
    """File overlaps but merges cleanly (disjoint lines) -> not reported."""
    cap = _line_level_map((0, "treeoid"))
    reports = overlap.detect_cross_branch_overlaps(
        "/x", git_capture=cap, remote="origin", line_level=True
    )
    assert reports == []


def test_detect_line_level_keeps_real_conflict(monkeypatch):
    cap = _line_level_map((1, "treeoid\nshared.py"))
    reports = overlap.detect_cross_branch_overlaps(
        "/x", git_capture=cap, remote="origin", line_level=True
    )
    assert len(reports) == 1
    assert reports[0].files == ("shared.py",)


def test_detect_line_level_falls_back_when_unsupported(monkeypatch):
    """Merge-tree unavailable -> keep file-level overlap (no silent miss)."""
    cap = _line_level_map((128, ""))
    reports = overlap.detect_cross_branch_overlaps(
        "/x", git_capture=cap, remote="origin", line_level=True
    )
    assert len(reports) == 1
    assert reports[0].files == ("shared.py",)


def test_detect_file_level_unaffected_by_merge_tree(monkeypatch):
    """Default (line_level=False) never calls merge-tree; file overlap reported."""
    # merge-tree maps to clean, but it must NOT be consulted in file-level mode.
    cap = _line_level_map((0, "treeoid"))
    reports = overlap.detect_cross_branch_overlaps(
        "/x", git_capture=cap, remote="origin", line_level=False
    )
    assert len(reports) == 1
    assert reports[0].files == ("shared.py",)


def test_detect_remote_agnostic(monkeypatch):
    """Detection works against a non-'origin' remote."""
    cap = _capture_from_map(
        {
            ("rev-parse", "--verify", "upstream/main"): (0, "upstream/main"),
            ("rev-parse", "--abbrev-ref", "HEAD"): (0, "feat/current"),
            ("rev-parse", "HEAD"): (0, "sha-head"),
            ("merge-base", "HEAD", "upstream/main"): (0, "base-sha"),
            ("diff", "--name-only", "base-sha...HEAD"): (0, "shared.py"),
            (
                "for-each-ref",
                "--format=%(refname:short) %(objectname)",
                "refs/heads",
                "refs/remotes/upstream",
            ): (0, "upstream/feat/other sha-other"),
            ("rev-parse", "upstream/feat/other"): (0, "sha-other"),
            ("rev-list", "--count", "upstream/main..upstream/feat/other"): (0, "2"),
            ("merge-base", "upstream/feat/other", "upstream/main"): (0, "base-o"),
            ("diff", "--name-only", "base-o...upstream/feat/other"): (0, "shared.py"),
        }
    )
    reports = overlap.detect_cross_branch_overlaps(
        "/x", git_capture=cap, remote="upstream"
    )
    assert len(reports) == 1
    assert reports[0].branch == "feat/other"
    assert reports[0].files == ("shared.py",)


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_is_line_level_disabled_by_env(monkeypatch, value):
    monkeypatch.setenv("COLLAB_OVERLAP_LINE_LEVEL", value)
    assert overlap.is_line_level_enabled() is False


def test_is_line_level_enabled_default(monkeypatch):
    monkeypatch.delenv("COLLAB_OVERLAP_LINE_LEVEL", raising=False)
    assert overlap.is_line_level_enabled() is True
