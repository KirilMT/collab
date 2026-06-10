"""Reconcile and git-status parsing tests for LockClient._reconcile()."""

from __future__ import annotations

import subprocess
import sys

from ._helpers import (
    FakeClient,
    FakeResponse,
    load_lock_client_module,
    make_create_client,
    patch_subprocess,
)

mod = load_lock_client_module()


def test_reconcile_stale_locks(monkeypatch, tmp_path):
    """Test _reconcile releases stale locks and acquires missing ones."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod.LockClient,
        "_run_git_status",
        staticmethod(lambda: (" M src/new.py", True)),
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


def test_reconcile_git_error_preserves_current_locks(monkeypatch, tmp_path):
    """A failure computing modified files must NOT release locks.

    When ``_get_modified_and_unpushed_files`` raises, returning an empty set would make
    reconcile release everything. Instead _reconcile must degrade to a no-op by
    returning the locks this developer currently holds.
    """
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    def _boom(self):
        raise RuntimeError("Git broken")

    monkeypatch.setattr(mod.LockClient, "_get_modified_and_unpushed_files", _boom)

    locks_data = [{"file_path": "src/held.py", "developer_id": "test_user"}]
    response = FakeResponse(status=200, data=locks_data)
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="test_user")
    result = lc._reconcile()
    # Safety: degradation keeps the held lock rather than releasing everything.
    assert result == {"src/held.py"}


def test_reconcile_supabase_error(monkeypatch, tmp_path):
    """_reconcile degrades to git-modified when active() re-raises a non-service error.

    Drives the real ``active()`` query through an ``execute()`` that raises a generic
    ``RuntimeError`` (not a lock-service error), exercising the bare ``raise`` re-raise
    branch before reconcile falls back to git-modified files.
    """
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod.LockClient, "_run_git_status", staticmethod(lambda: (" M src/app.py", True))
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

    patch_subprocess(monkeypatch, check_output=mock_check_output)
    result, _ok = mod.LockClient._run_git_status()
    assert "src/app.py" in result


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


def test_get_current_branch_error_lock_client(monkeypatch):
    """Test _get_current_branch returns None when git command fails."""

    def mock_check_output(cmd, *args, **kwargs):
        raise subprocess.CalledProcessError(128, cmd)

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    result = mod.LockClient._get_current_branch()
    assert result is None


def test_get_current_branch_win32(monkeypatch):
    """Ensure _get_current_branch uses the Windows code path when platform is win32."""
    monkeypatch.setattr(sys, "platform", "win32")

    def fake_check_output(cmd, *a, **k):
        return b"feature/win-branch\n"

    patch_subprocess(monkeypatch, check_output=fake_check_output)
    got = mod.LockClient._get_current_branch()
    assert got == "feature/win-branch"


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
        mod.LockClient,
        "_run_git_status",
        staticmethod(lambda: (" M src/app.py\n", True)),
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_get_modified_and_unpushed_files",
        lambda self: (["src/app.py"], True),
    )

    client = mod.LockClient(developer_id="test_user")
    result = client._reconcile()
    assert "src/app.py" in result
    assert "src/other.py" not in result


def test_git_status_parsing_and_modified(monkeypatch):
    sample = " M src/a.py\nR  src/old.py -> src/new.py\n?? src/new_file.py\n"
    monkeypatch.setattr(
        mod.LockClient, "_run_git_status", staticmethod(lambda: (sample, True))
    )

    c = mod.LockClient(local_only=True)
    out, _git_ok = c._get_modified_and_unpushed_files()
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
        lambda: (["a.py", "b.py", "c.py", "d.py"], True),
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
    # Reconcile should acquire missing dirty files (d.py) and refresh missing
    # token locks through acquire RPC (c.py).
    assert len(acquired_calls) == 2


def test_get_modified_and_unpushed_files_non_windows_paths(monkeypatch):
    """Cover non-Windows upstream check + diff code path and status-only fallback."""
    from tests.backend.subprocess_testing import argv_subcommand

    c = mod.LockClient(local_only=True)
    monkeypatch.setattr(mod.sys, "platform", "linux")

    calls = {"n": 0}

    def _check_output(args, *a, **k):
        # status
        if argv_subcommand(args, "git", "status", "--porcelain"):
            return b" M src/dirty.py\n"
        # upstream check
        if argv_subcommand(args, "git", "rev-parse"):
            calls["n"] += 1
            if calls["n"] == 1:
                return b"origin/main\n"
            raise RuntimeError("no upstream")
        # diff against upstream
        if argv_subcommand(args, "git", "diff", "--name-status"):
            return b"M\tsrc/unpushed.py\n"
        return b""

    patch_subprocess(monkeypatch, check_output=_check_output)
    monkeypatch.setattr(c, "_normalize_file_path", lambda p: p)
    monkeypatch.setattr(c, "_should_ignore_path", lambda p: False)

    first_list, _ = c._get_modified_and_unpushed_files()
    first = set(first_list)
    assert any(p.endswith("dirty.py") for p in first)
    assert "src/unpushed.py" in first

    # Second call exercises rev-parse failure -> except fallback to status-only
    second_list, _ = c._get_modified_and_unpushed_files()
    second = set(second_list)
    assert any(p.endswith("dirty.py") for p in second)


def test_get_modified_and_unpushed_files_keeps_deleted_upstream_paths(monkeypatch):
    """Deleted files from unpushed history remain in-progress for locking."""
    from tests.backend.subprocess_testing import argv_subcommand

    c = mod.LockClient(local_only=True)
    monkeypatch.setattr(mod.sys, "platform", "linux")

    def _check_output(args, *a, **k):
        if argv_subcommand(args, "git", "status", "--porcelain"):
            return b""
        if argv_subcommand(args, "git", "rev-parse"):
            return b"origin/main\n"
        if argv_subcommand(args, "git", "diff", "--name-status"):
            return (
                b"D\t.collab/core/watcher.py\n"
                b"D\t.collab/dashboard/server.py\n"
                b"M\tsrc/live.py\n"
                b"R100\told/name.py\tnew/name.py\n"
            )
        return b""

    patch_subprocess(monkeypatch, check_output=_check_output)
    monkeypatch.setattr(c, "_normalize_file_path", lambda p: p.replace("\\", "/"))
    monkeypatch.setattr(c, "_should_ignore_path", lambda p: False)

    out_list, _ = c._get_modified_and_unpushed_files()
    out = set(out_list)
    assert ".collab/core/watcher.py" in out
    assert ".collab/dashboard/server.py" in out
    assert "src/live.py" in out
    assert "new/name.py" in out


class _Cap:
    def __init__(self, stdout=b"", ok=True, timed_out=False):
        self.stdout = stdout
        self.ok = ok
        self.timed_out = timed_out


def test_resolve_lock_diff_base_ref_uses_env_override(monkeypatch):
    """COLLAB_LOCK_BASE_REF wins when @{u} is unset and the ref exists."""
    monkeypatch.setenv("COLLAB_LOCK_BASE_REF", "origin/develop")

    def fake_capture(args, **_k):
        joined = " ".join(args)
        if "symbolic-full-name" in joined:
            return _Cap(b"")  # no upstream
        if "--verify" in joined and "origin/develop" in joined:
            return _Cap(b"sha")
        return _Cap(b"", ok=False)

    monkeypatch.setattr(mod.safe_subprocess, "capture", fake_capture)
    monkeypatch.setattr(mod.safe_subprocess, "decode_output", lambda b: b.decode())
    assert mod.LockClient._resolve_lock_diff_base_ref() == "origin/develop"


def test_resolve_lock_diff_base_ref_uses_origin_branch(monkeypatch):
    """Origin/<branch> is used when it exists and there is no upstream/override."""
    monkeypatch.delenv("COLLAB_LOCK_BASE_REF", raising=False)

    def fake_capture(args, **_k):
        joined = " ".join(args)
        if "symbolic-full-name" in joined:
            return _Cap(b"")
        if args[1:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return _Cap(b"feat/x")
        if "--verify" in joined and "origin/feat/x" in joined:
            return _Cap(b"sha")
        return _Cap(b"", ok=False)

    monkeypatch.setattr(mod.safe_subprocess, "capture", fake_capture)
    monkeypatch.setattr(mod.safe_subprocess, "decode_output", lambda b: b.decode())
    assert mod.LockClient._resolve_lock_diff_base_ref() == "origin/feat/x"


def test_resolve_lock_diff_base_ref_returns_none(monkeypatch):
    """No upstream, override, or remote base -> None (status-only locking)."""
    monkeypatch.delenv("COLLAB_LOCK_BASE_REF", raising=False)
    monkeypatch.setattr(
        mod.safe_subprocess, "capture", lambda *a, **k: _Cap(b"", ok=False)
    )
    monkeypatch.setattr(mod.safe_subprocess, "decode_output", lambda b: b.decode())
    assert mod.LockClient._resolve_lock_diff_base_ref() is None


def test_paths_from_git_diff_name_status_parsing(monkeypatch):
    """Parse handles blank lines, malformed rows, renames, and directories."""
    payload = (
        b"M\tcollab/a.py\n"
        b"\n"
        b"BADLINE\n"
        b"R100\told/b.py -> collab/b.py\n"
        b"A\tsome/dir/\n"
    )
    monkeypatch.setattr(mod.safe_subprocess, "capture", lambda *a, **k: _Cap(payload))
    monkeypatch.setattr(mod.safe_subprocess, "decode_output", lambda b: b.decode())
    out = mod.LockClient._paths_from_git_diff_name_status("origin/main...HEAD")
    assert "collab/a.py" in out
    assert "collab/b.py" in out
    assert all(not p.endswith("/") for p in out)


def test_paths_from_git_diff_name_status_capture_failure(monkeypatch):
    """A failed diff capture yields an empty list."""
    monkeypatch.setattr(
        mod.safe_subprocess, "capture", lambda *a, **k: _Cap(b"", ok=False)
    )
    assert mod.LockClient._paths_from_git_diff_name_status("@{u}..HEAD") == []


def test_format_acquire_failure_pgrst202_adds_schema_hint():
    """PGRST202 on acquire_lock gets an actionable schema-reload hint."""
    msg = "API Error: {'code': 'PGRST202'} for acquire_lock"
    out = mod.LockClient._format_acquire_failure(msg)
    assert "reload" in out.lower()
    assert "schema.sql" in out


def test_format_acquire_failure_passthrough():
    """Unrelated failures are returned unchanged."""
    assert mod.LockClient._format_acquire_failure("boom") == "boom"
    assert mod.LockClient._format_acquire_failure("") == ""


def test_get_modified_and_unpushed_files_falls_back_to_origin_main(monkeypatch):
    """When @{u} is unset, diff against origin/main...HEAD for branch-only commits."""
    c = mod.LockClient(local_only=True)

    def fake_capture(args, **_kwargs):
        joined = " ".join(args)

        class R:
            ok = False
            timed_out = False
            stdout = b""

        if "symbolic-full-name" in joined and "@{u}" in joined:
            return R
        if joined.endswith("HEAD") and "abbrev-ref" in joined:
            R.ok = True
            R.stdout = b"feat/no-upstream"
            return R
        if "--verify" in joined and "origin/main" in joined:
            R.ok = True
            return R
        if "--verify" in joined:
            return R
        if "diff" in joined and "--name-status" in joined:
            R.ok = True
            R.stdout = b"M\tcollab/lock_client.py\n"
            return R
        if "status" in joined and "porcelain" in joined:
            R.ok = True
            return R
        return R

    monkeypatch.setattr(mod.safe_subprocess, "capture", fake_capture)
    monkeypatch.setattr(c, "_normalize_file_path", lambda p: p.replace("\\", "/"))
    monkeypatch.setattr(c, "_should_ignore_path", lambda _p: False)

    out_list, _ = c._get_modified_and_unpushed_files()
    out = set(out_list)
    assert "collab/lock_client.py" in out


def test_get_modified_and_unpushed_files_skips_status_dir_suffix(monkeypatch):
    """Directory-like status entries ending in '/' are ignored."""
    from tests.backend.subprocess_testing import argv_subcommand

    c = mod.LockClient(local_only=True)
    monkeypatch.setattr(mod.sys, "platform", "linux")

    def _check_output(args, *a, **k):
        if argv_subcommand(args, "git", "status", "--porcelain"):
            return b" M apps/reporting/instance/\n M src/real.py\n"
        if argv_subcommand(args, "git", "rev-parse"):
            raise RuntimeError("no upstream")
        return b""

    patch_subprocess(monkeypatch, check_output=_check_output)
    monkeypatch.setattr(c, "_normalize_file_path", lambda p: p.replace("\\", "/"))
    monkeypatch.setattr(c, "_should_ignore_path", lambda p: False)

    out_list, _ = c._get_modified_and_unpushed_files()
    out = set(out_list)
    assert "apps/reporting/instance/" not in out
    assert "src/real.py" in out
