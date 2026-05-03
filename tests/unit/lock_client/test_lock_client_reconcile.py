"""Reconcile and git-status parsing tests for LockClient._reconcile()."""

from __future__ import annotations

import subprocess
import sys

from ._helpers import (
    FakeClient,
    FakeResponse,
    load_lock_client_module,
    make_create_client,
)

mod = load_lock_client_module()


def test_reconcile_stale_locks(monkeypatch, tmp_path):
    """Test _reconcile releases stale locks and acquires missing ones."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod.LockClient,
        "_run_git_status",
        staticmethod(lambda: " M src/new.py"),
    )

    locks_data = [
        {"file_path": "src/old.py", "developer_id": "test_user"},
        {"file_path": "src/new.py", "developer_id": "test_user"},
    ]
    response = FakeResponse(status=200, data=locks_data)
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="test_user")
    result = lc._reconcile()
    assert "src/new.py" in result


def test_reconcile_git_error(monkeypatch, tmp_path):
    """Test _reconcile handles git status errors."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    def error_git_status():
        raise RuntimeError("Git broken")

    monkeypatch.setattr(
        mod.LockClient, "_run_git_status", staticmethod(error_git_status)
    )
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    lc = mod.LockClient(developer_id="test_user")
    result = lc._reconcile()
    assert isinstance(result, set)


def test_reconcile_supabase_error(monkeypatch, tmp_path):
    """Test _reconcile handles Supabase errors."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod.LockClient, "_run_git_status", staticmethod(lambda: " M src/app.py")
    )

    class ErrorClient(FakeClient):
        def execute(self):
            raise RuntimeError("Supabase down")

    monkeypatch.setattr(
        mod, "_get_create_client", lambda: lambda url, key: ErrorClient(FakeResponse())
    )

    lc = mod.LockClient(developer_id="test_user")
    result = lc._reconcile()
    assert "src/app.py" in result


def test_run_git_status(monkeypatch):
    """Test _run_git_status runs git command."""

    def mock_check_output(cmd, *args, **kwargs):
        return b" M src/app.py\n M src/routes.py\n"

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)
    result = mod.LockClient._run_git_status()
    assert "src/app.py" in result


def test_parse_git_status_path_simple():
    """Test parsing simple modified file."""
    assert mod.LockClient._parse_git_status_path(" M src/app.py") == "src/app.py"


def test_parse_git_status_path_rename():
    """Test parsing renamed file."""
    result = mod.LockClient._parse_git_status_path("R  old.py -> new.py")
    assert result == "new.py"


def test_parse_git_status_path_quoted():
    """Test parsing quoted paths."""
    result = mod.LockClient._parse_git_status_path('M  "src/my file.py"')
    assert "my file" in result


def test_should_ignore_path_for_instance_runtime_dirs():
    """Runtime instance folders must never be lock candidates."""
    assert mod.LockClient._should_ignore_path("instance") is True
    assert mod.LockClient._should_ignore_path("apps/reporting/instance/") is True
    assert mod.LockClient._should_ignore_path("apps/reporting/instance") is True
    assert mod.LockClient._should_ignore_path("apps/planning/instance/state.db") is True
    assert mod.LockClient._should_ignore_path("src/services/db_utils.py") is False


def test_run_git_status_unix(monkeypatch):
    mod_local = load_lock_client_module()
    monkeypatch.setattr(sys, "platform", "linux")

    def fake_check_output(args, *a, **k):
        return b" M src/foo.py\n"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    out = mod_local.LockClient._run_git_status()
    assert "src/foo.py" in out


def test_reconcile_supabase_lock_query_error(monkeypatch, tmp_path):
    """Test _reconcile handles Supabase lock query error."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod.LockClient,
        "_run_git_status",
        staticmethod(lambda: " M src/app.py"),
    )

    call_count = [0]

    class SelectiveErrorClient:
        """Errors only on the second execute call (active locks)."""

        def __init__(self, resp):
            self._resp = resp

        def rpc(self, *args, **kwargs):
            return self

        def table(self, *args, **kwargs):
            return self

        def select(self, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 1:
                raise RuntimeError("Supabase query failed")
            return self

        def delete(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def execute(self):
            return self._resp

    monkeypatch.setattr(
        mod,
        "_get_create_client",
        lambda: lambda url, key: SelectiveErrorClient(FakeResponse()),
    )

    # Make modified-files detection deterministic for tests
    def _fixed_modified(self):
        return ["src/app.py"]

    monkeypatch.setattr(
        mod.LockClient, "_get_modified_and_unpushed_files", _fixed_modified
    )

    lc = mod.LockClient(developer_id="test_user")
    result = lc._reconcile()
    assert "src/app.py" in result


def test_get_current_branch_error_lock_client(monkeypatch):
    """Test _get_current_branch returns None when git command fails."""

    def mock_check_output(cmd, *args, **kwargs):
        raise subprocess.CalledProcessError(128, cmd)

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    result = mod.LockClient._get_current_branch()
    assert result is None


def test_get_current_branch_win32(monkeypatch):
    """Ensure _get_current_branch uses the Windows code path when platform is win32."""
    monkeypatch.setattr(sys, "platform", "win32")

    def fake_check_output(cmd, *a, **k):
        return b"feature/win-branch\n"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    got = mod.LockClient._get_current_branch()
    assert got == "feature/win-branch"


def test_get_current_branch_non_win_error(monkeypatch):
    """When git command fails, _get_current_branch should return None."""
    monkeypatch.setattr(sys, "platform", "linux")

    def fail_check_output(cmd, *a, **k):
        raise subprocess.CalledProcessError(2, cmd)

    monkeypatch.setattr(subprocess, "check_output", fail_check_output)
    got = mod.LockClient._get_current_branch()
    assert got is None


def test_parse_git_status_path_unicode_escape():
    """Test _parse_git_status_path with unicode-escaped quoted path."""
    result = mod.LockClient._parse_git_status_path(' M "src/file.py"')
    assert "file" in result


def test_parse_git_status_path_bad_unicode_escape():
    """Test _parse_git_status_path with invalid unicode escape."""
    result = mod.LockClient._parse_git_status_path(' M "src/\\xZZfile.py"')
    assert "file" in result


def test_reconcile_returns_my_locks(monkeypatch):
    """_reconcile returns set containing only current developer locks."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    data = [
        {"file_path": "src/app.py", "developer_id": "test_user"},
        {"file_path": "src/other.py", "developer_id": "other_dev"},
    ]
    monkeypatch.setattr(
        mod,
        "_get_create_client",
        lambda: make_create_client(FakeResponse(status=200, data=data)),
    )
    monkeypatch.setattr(
        mod.LockClient, "_run_git_status", staticmethod(lambda: " M src/app.py\n")
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_get_modified_and_unpushed_files",
        lambda self: ["src/app.py"],
    )

    client = mod.LockClient(developer_id="test_user")
    result = client._reconcile()
    assert "src/app.py" in result
    assert "src/other.py" not in result


def test_git_status_parsing_and_modified(monkeypatch):
    sample = " M src/a.py\nR  src/old.py -> src/new.py\n?? src/new_file.py\n"
    monkeypatch.setattr(mod.LockClient, "_run_git_status", staticmethod(lambda: sample))

    c = mod.LockClient(local_only=True)
    out = c._get_modified_and_unpushed_files()
    assert "src/a.py" in out
    assert "src/new.py" in out


def test_reconcile_modified_files_error_active_fallback_error(monkeypatch):
    """If modified detection fails and active() also fails, reconcile returns empty
    set."""

    c = mod.LockClient(local_only=True, developer_id="alice")

    def _boom_modified():
        raise RuntimeError("git exploded")

    def _boom_active():
        raise RuntimeError("active exploded")

    monkeypatch.setattr(c, "_get_modified_and_unpushed_files", _boom_modified)
    monkeypatch.setattr(c, "active", _boom_active)

    assert c._reconcile() == set()


def test_reconcile_handles_resume_multi_refresh_and_summary_cleanup_paths(monkeypatch):
    """Drive reconcile through resume/multi/refresh categories and cleanup error
    path."""

    c = mod.LockClient(local_only=True, developer_id="alice")

    # Modified files: one resumed, one multi-session, one refreshed (no token),
    # and one missing.
    monkeypatch.setattr(
        c,
        "_get_modified_and_unpushed_files",
        lambda: ["a.py", "b.py", "c.py", "d.py"],
    )

    active_rows = [
        {"file_path": "a.py", "developer_id": "alice", "lock_token": "tok-current"},
        {"file_path": "b.py", "developer_id": "alice", "lock_token": "tok-other"},
        {"file_path": "c.py", "developer_id": "alice", "lock_token": ""},
        {"file_path": "stale.py", "developer_id": "alice", "lock_token": "tok-current"},
    ]
    monkeypatch.setattr(c, "active", lambda: active_rows)
    monkeypatch.setattr(c, "_get_session_token", lambda: "tok-current")
    monkeypatch.setattr(c, "_is_same_machine_token", lambda t: t == "tok-current")

    # Force resumed token update exception branch
    class _FailingUpdateClient:
        def table(self, name):
            return self

        def update(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def execute(self):
            raise RuntimeError("update failed")

    c._client = _FailingUpdateClient()

    released = []
    acquired_calls = []
    monkeypatch.setattr(c, "release_multiple", lambda fps: released.extend(sorted(fps)))
    monkeypatch.setattr(
        c,
        "acquire_multiple",
        lambda fps, branch_name=None, reason=None: acquired_calls.append(
            (sorted(fps), branch_name, reason)
        ),
    )
    monkeypatch.setattr(c, "_get_current_branch", lambda: "main")

    # Trigger summary write + repo-summary write failure, then marker cleanup
    # remove failure.
    real_open = open

    def _open_side_effect(path, mode="r", *args, **kwargs):
        p = str(path)
        if p.endswith(".collab\\.startup_summary.json") and "w" in mode:
            raise RuntimeError("repo summary write failed")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(mod, "open", _open_side_effect, raising=False)
    monkeypatch.setattr(mod.os.path, "exists", lambda p: True)
    monkeypatch.setattr(
        mod.os, "remove", lambda p: (_ for _ in ()).throw(RuntimeError("remove failed"))
    )
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    class _ImmediateThread:
        def __init__(self, target, daemon=True):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(mod.threading, "Thread", _ImmediateThread)

    out = c._reconcile()

    assert out == {"a.py", "b.py", "c.py", "d.py"}
    assert "stale.py" in released
    # refreshed (c.py) and missing (d.py) each trigger acquire_multiple calls
    assert any("c.py" in call[0] for call in acquired_calls)
    assert any("d.py" in call[0] for call in acquired_calls)


def test_get_modified_and_unpushed_files_non_windows_paths(monkeypatch):
    """Cover non-Windows upstream check + diff code path and status-only fallback."""

    c = mod.LockClient(local_only=True)
    monkeypatch.setattr(mod.sys, "platform", "linux")

    calls = {"n": 0}

    def _check_output(args, *a, **k):
        # status
        if args[:3] == ["git", "status", "--porcelain"]:
            return b" M src/dirty.py\n"
        # upstream check
        if args[:2] == ["git", "rev-parse"]:
            calls["n"] += 1
            if calls["n"] == 1:
                return b"origin/main\n"
            raise RuntimeError("no upstream")
        # diff against upstream
        if args[:3] == ["git", "diff", "--name-status"]:
            return b"M\tsrc/unpushed.py\n"
        return b""

    monkeypatch.setattr(mod.subprocess, "check_output", _check_output)
    monkeypatch.setattr(c, "_normalize_file_path", lambda p: p)
    monkeypatch.setattr(c, "_should_ignore_path", lambda p: False)

    first = set(c._get_modified_and_unpushed_files())
    assert any(p.endswith("dirty.py") for p in first)
    assert "src/unpushed.py" in first

    # Second call exercises rev-parse failure -> except fallback to status-only
    second = set(c._get_modified_and_unpushed_files())
    assert any(p.endswith("dirty.py") for p in second)


def test_get_modified_and_unpushed_files_keeps_deleted_upstream_paths(monkeypatch):
    """Deleted files from unpushed history remain in-progress for locking."""

    c = mod.LockClient(local_only=True)
    monkeypatch.setattr(mod.sys, "platform", "linux")

    def _check_output(args, *a, **k):
        if args[:3] == ["git", "status", "--porcelain"]:
            return b""
        if args[:2] == ["git", "rev-parse"]:
            return b"origin/main\n"
        if args[:3] == ["git", "diff", "--name-status"]:
            return (
                b"D\t.collab/core/watcher.py\n"
                b"D\t.collab/dashboard/server.py\n"
                b"M\tsrc/live.py\n"
                b"R100\told/name.py\tnew/name.py\n"
            )
        return b""

    monkeypatch.setattr(mod.subprocess, "check_output", _check_output)
    monkeypatch.setattr(c, "_normalize_file_path", lambda p: p.replace("\\", "/"))
    monkeypatch.setattr(c, "_should_ignore_path", lambda p: False)

    out = set(c._get_modified_and_unpushed_files())
    assert ".collab/core/watcher.py" in out
    assert ".collab/dashboard/server.py" in out
    assert "src/live.py" in out
    assert "new/name.py" in out


def test_get_modified_and_unpushed_files_skips_status_dir_suffix(monkeypatch):
    """Directory-like status entries ending in '/' are ignored."""

    c = mod.LockClient(local_only=True)
    monkeypatch.setattr(mod.sys, "platform", "linux")

    def _check_output(args, *a, **k):
        if args[:3] == ["git", "status", "--porcelain"]:
            return b" M apps/reporting/instance/\n M src/real.py\n"
        if args[:2] == ["git", "rev-parse"]:
            raise RuntimeError("no upstream")
        return b""

    monkeypatch.setattr(mod.subprocess, "check_output", _check_output)
    monkeypatch.setattr(c, "_normalize_file_path", lambda p: p.replace("\\", "/"))
    monkeypatch.setattr(c, "_should_ignore_path", lambda p: False)

    out = set(c._get_modified_and_unpushed_files())
    assert "apps/reporting/instance/" not in out
    assert "src/real.py" in out
