"""Parsing and ignore-path tests for live_locks_watcher."""

from __future__ import annotations

import os

from ._helpers import load_watcher_module


def test_parse_git_status_path_rename_and_quotes():
    mod = load_watcher_module()
    sample = 'R  "src/old.py -> src/new.py"'
    p = mod._parse_git_status_path(sample)
    assert p.strip('"') == "src/new.py"

    sample2 = " M src/some_file.py"
    p2 = mod._parse_git_status_path(sample2)
    assert p2 == "src/some_file.py"


def test_parse_git_status_path_modifications():
    mod = load_watcher_module()
    assert mod._parse_git_status_path("M  src/app.py") == "src/app.py"
    assert mod._parse_git_status_path("M  src/routes.py") == "src/routes.py"
    assert mod._parse_git_status_path("A  src/new_file.py") == "src/new_file.py"


def test_parse_git_status_path_deleted_files():
    mod = load_watcher_module()
    assert mod._parse_git_status_path("D  src/old_file.py") == "src/old_file.py"


def test_parse_git_status_path_untracked_files():
    mod = load_watcher_module()
    assert mod._parse_git_status_path("?? src/temp.py") == "src/temp.py"


def test_parse_git_status_path_renames():
    mod = load_watcher_module()
    p = mod._parse_git_status_path("R  src/old.py -> src/new.py")
    assert p == "src/new.py"


def test_parse_git_status_path_spaces_in_path():
    mod = load_watcher_module()
    p = mod._parse_git_status_path('M  "src/my file.py"')
    assert p == "src/my file.py"


def test_parse_git_status_path_staged_and_unstaged():
    mod = load_watcher_module()
    assert mod._parse_git_status_path("MM src/app.py") == "src/app.py"
    assert mod._parse_git_status_path("A  src/new.py") == "src/new.py"
    assert mod._parse_git_status_path(" M src/other.py") == "src/other.py"


def test_parse_git_status_path_copy():
    mod = load_watcher_module()
    p = mod._parse_git_status_path("C  src/original.py -> src/copy.py")
    assert p == "src/copy.py"


def test_parse_git_status_path_type_change():
    mod = load_watcher_module()
    p = mod._parse_git_status_path("T  src/app.py")
    assert p == "src/app.py"


# _should_ignore_path tests


def test_should_ignore_path_git_and_collab():
    mod = load_watcher_module()
    assert mod._should_ignore_path(".git/objects/abc") is True
    assert mod._should_ignore_path(".collab/somefile") is False
    assert mod._should_ignore_path("src/app.py") is False


def test_should_ignore_path_instance_runtime_dirs():
    mod = load_watcher_module()
    assert mod._should_ignore_path("instance") is True
    assert mod._should_ignore_path("instance/") is True
    assert mod._should_ignore_path("apps/reporting/instance") is True
    assert mod._should_ignore_path("apps/planning/instance/tmp.db") is True


def test_should_ignore_path_accepts_normal_dirs():
    mod = load_watcher_module()
    assert mod._should_ignore_path(".venv/lib/python.py") is False
    assert mod._should_ignore_path("node_modules/package/index.js") is False
    assert mod._should_ignore_path("src/__pycache__/app.pyc") is False


def test_should_ignore_path_valid_files():
    mod = load_watcher_module()
    assert mod._should_ignore_path("src/app.py") is False
    assert mod._should_ignore_path("tests/test_app.py") is False
    assert mod._should_ignore_path("README.md") is False


def test_should_ignore_path_edge_cases():
    mod = load_watcher_module()
    result_empty = mod._should_ignore_path("")
    assert isinstance(result_empty, bool)
    result_slash = mod._should_ignore_path("/")
    assert isinstance(result_slash, bool)


def test_should_ignore_path_with_mixed_case():
    mod = load_watcher_module()
    result = mod._should_ignore_path(".GIT/config")
    assert isinstance(result, bool)


def test_parse_git_status_path_and_normalize_migrated(tmp_path, monkeypatch):
    mod = load_watcher_module()
    # Parse a renaming line and quoted path
    line = 'R  "old/path.txt" -> "new/path.txt"'
    p = mod._parse_git_status_path(line)
    assert p == "new/path.txt"

    # Normalize absolute path relative to project root
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))
    ap = os.path.join(str(tmp_path), "src", "a.txt")
    os.makedirs(os.path.dirname(ap), exist_ok=True)
    open(ap, "w").close()
    got = mod._normalize_path(ap, str(tmp_path))
    assert got.replace("\\", "/").startswith("src/")


def test_normalize_path_strips_dot_slash_prefix(tmp_path):
    """_normalize_path strips leading ./ from relative paths."""
    mod = load_watcher_module()
    result = mod._normalize_path("./src/app.py", str(tmp_path))
    assert result == "src/app.py"


def test_normalize_path_canonicalises_collab_prefix(tmp_path):
    """_normalize_path converts collab/ prefix to .collab/."""
    mod = load_watcher_module()
    result = mod._normalize_path("collab/core/lock_client.py", str(tmp_path))
    assert result == ".collab/core/lock_client.py"


def test_normalize_path_exception_falls_back_to_slash_replace(monkeypatch):
    """_normalize_path returns path with backslash replacement when relpath raises."""
    mod = load_watcher_module()

    def _raising_relpath(path, start):
        raise OSError("relpath failed")

    monkeypatch.setattr(mod.os.path, "isabs", lambda p: True)
    monkeypatch.setattr(mod.os.path, "relpath", _raising_relpath)
    result = mod._normalize_path("src\\app.py", "/project")
    assert result == "src/app.py"
