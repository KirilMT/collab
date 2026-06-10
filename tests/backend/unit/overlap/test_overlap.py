"""Unit tests for collab.overlap cross-branch file overlap detection."""

from __future__ import annotations

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


def test_warn_cross_branch_overlap_always_returns_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(
        overlap,
        "detect_cross_branch_overlaps",
        lambda *_a, **_k: [overlap.OverlapReport(branch="feat/x", files=("a.py",))],
    )
    emitted: list[str] = []
    assert overlap.warn_cross_branch_overlap(tmp_path, emit=emitted.append) == 0
    assert emitted


def test_warn_cross_branch_overlap_fail_open_on_error(monkeypatch, tmp_path):
    def boom(_root):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(overlap, "detect_cross_branch_overlaps", boom)
    emitted: list[str] = []
    assert overlap.warn_cross_branch_overlap(tmp_path, emit=emitted.append) == 0
    assert emitted and "failed" in emitted[0].lower()


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


def test_is_overlap_strict_enabled_by_env(monkeypatch):
    monkeypatch.delenv("COLLAB_OVERLAP_STRICT", raising=False)
    assert overlap.is_overlap_strict_enabled() is False

    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", "1")
    assert overlap.is_overlap_strict_enabled() is True

    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", "true")
    assert overlap.is_overlap_strict_enabled() is True


def test_warn_cross_branch_overlap_returns_one_in_strict_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(
        overlap,
        "detect_cross_branch_overlaps",
        lambda *_a, **_k: [overlap.OverlapReport(branch="feat/x", files=("a.py",))],
    )
    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", "1")
    emitted: list[str] = []
    # Should return 1 because overlaps exist and strict mode is on
    assert overlap.warn_cross_branch_overlap(tmp_path, emit=emitted.append) == 1
    assert emitted

    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", "0")
    # Should return 0 because strict mode is off
    assert overlap.warn_cross_branch_overlap(tmp_path, emit=emitted.append) == 0
