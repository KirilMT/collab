"""Unit tests for :mod:`collab.path_filter`.

Covers structural ignores, built-in temp-suffix ignores, ``COLLAB_LOCK_IGNORE`` env
patterns, ``.collabignore`` file parsing/caching, directory patterns, and the fail-open
contract for #170.
"""

from __future__ import annotations

import os

import pytest

from collab import path_filter


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Ensure COLLAB_LOCK_IGNORE never leaks in from the host environment."""
    monkeypatch.delenv("COLLAB_LOCK_IGNORE", raising=False)
    yield


def test_empty_path_not_ignored():
    assert path_filter.should_ignore_lock_path("") is False


def test_normal_source_file_not_ignored():
    assert path_filter.should_ignore_lock_path("collab/lock_client.py") is False


@pytest.mark.parametrize(
    "path",
    [
        ".git",
        ".git/config",
        "sub/.git/HEAD",
        "instance",
        "instance/config.py",
        "app/instance",
        "a/instance/b.py",
        ".startup_summary.json",
        "nested/.shutdown_complete",
    ],
)
def test_structural_ignores(path):
    assert path_filter.should_ignore_lock_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "scratch.tmp",
        "dir/scratch.temp",
        "notes.bak",
        "notes.backup",
        "file.orig",
        "file.swp",
        "file.swo",
        "editor~",
        "module.isorted",
    ],
)
def test_builtin_temp_suffixes(path):
    assert path_filter.should_ignore_lock_path(path) is True


def test_backslash_paths_are_normalized():
    assert path_filter.should_ignore_lock_path("sub\\dir\\scratch.tmp") is True


def test_leading_dot_slash_stripped():
    assert path_filter.should_ignore_lock_path("./scratch.tmp") is True


def test_env_pattern_basename_match(monkeypatch):
    monkeypatch.setenv("COLLAB_LOCK_IGNORE", ".sim_*")
    assert path_filter.should_ignore_lock_path(".sim_verify_branch.py") is True
    assert path_filter.should_ignore_lock_path("real_code.py") is False


def test_env_pattern_multiple_separators(monkeypatch):
    monkeypatch.setenv("COLLAB_LOCK_IGNORE", f".sim_*,{'.tmp_*'}{os.linesep}*.draft")
    assert path_filter.should_ignore_lock_path(".tmp_sim_165.py") is True
    assert path_filter.should_ignore_lock_path("outline.draft") is True


def test_env_pattern_path_match(monkeypatch):
    monkeypatch.setenv("COLLAB_LOCK_IGNORE", "build/*.log")
    assert path_filter.should_ignore_lock_path("build/out.log") is True
    assert path_filter.should_ignore_lock_path("src/out.log") is False


def test_collabignore_file(tmp_path):
    (tmp_path / ".collabignore").write_text(
        "# comment\n\n.issue_body_temp.md\n*_scratch.py\n",
        encoding="utf-8",
    )
    root = str(tmp_path)
    assert path_filter.should_ignore_lock_path(".issue_body_temp.md", root) is True
    assert path_filter.should_ignore_lock_path("foo_scratch.py", root) is True
    assert path_filter.should_ignore_lock_path("foo.py", root) is False


def test_collabignore_directory_pattern(tmp_path):
    (tmp_path / ".collabignore").write_text("tmp/\nlogs/**\n", encoding="utf-8")
    root = str(tmp_path)
    assert path_filter.should_ignore_lock_path("tmp", root) is True
    assert path_filter.should_ignore_lock_path("tmp/x.py", root) is True
    assert path_filter.should_ignore_lock_path("logs/a/b.txt", root) is True
    assert path_filter.should_ignore_lock_path("other/x.py", root) is False


def test_collabignore_absent_is_noop(tmp_path):
    assert path_filter.should_ignore_lock_path("foo.py", str(tmp_path)) is False


def test_collabignore_cache_refreshes_on_mtime_change(tmp_path):
    ignore = tmp_path / ".collabignore"
    ignore.write_text("*.aaa\n", encoding="utf-8")
    root = str(tmp_path)
    assert path_filter.should_ignore_lock_path("x.aaa", root) is True
    assert path_filter.should_ignore_lock_path("x.bbb", root) is False

    # Rewrite with a newer mtime and confirm the cache picks up the change.
    new_mtime = os.path.getmtime(ignore) + 10
    ignore.write_text("*.bbb\n", encoding="utf-8")
    os.utime(ignore, (new_mtime, new_mtime))
    assert path_filter.should_ignore_lock_path("x.bbb", root) is True


def test_empty_project_root_skips_collabignore():
    # No project root -> no .collabignore lookup, but built-ins still apply.
    assert path_filter.should_ignore_lock_path("plain.py", "") is False
    assert path_filter.should_ignore_lock_path("plain.tmp", "") is True


def test_matches_skips_empty_pattern():
    # Empty patterns are skipped; a real pattern in the same list still matches.
    assert path_filter._matches("a.py", ["", "*.py"]) is True
    assert path_filter._matches("a.py", [""]) is False


def test_collabignore_read_error_is_fail_open(tmp_path, monkeypatch):
    # File exists (getmtime succeeds) but open() raises -> patterns fall back to
    # empty and locking is not disabled.
    (tmp_path / ".collabignore").write_text("*.aaa\n", encoding="utf-8")
    root = str(tmp_path)

    def _boom(*_args, **_kwargs):
        raise OSError("cannot read")

    monkeypatch.setattr("builtins.open", _boom)
    assert path_filter._collabignore_patterns(root) == ()
    assert path_filter.should_ignore_lock_path("x.aaa", root) is False


def test_should_ignore_fail_open_on_unexpected_error(monkeypatch):
    # Any unexpected error inside the checks yields "do not ignore".
    def _boom(_norm):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(path_filter, "_is_structural_ignore", _boom)
    assert path_filter.should_ignore_lock_path("foo.py", "") is False
