"""Reconciliation startup tests for live_locks_watcher."""

from __future__ import annotations

import sys

from ._helpers import load_watcher_module

# ---- Auto-migrated from migrated_remaining ----


def test_reconcile_readopts_dirty_locked_file(monkeypatch):
    """§8c: Dirty file with existing lock is re-adopted, no acquire RPC.

    When startup reconciliation finds a file that is dirty AND already locked by this
    developer (same session token or no token), it should re-adopt the lock without
    calling acquire_lock RPC.
    """
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(watcher, "_is_ephemeral_dev", lambda d: False)

    # Clean up state
    mod._local_owned_locks.clear()
    mod._active_conflicts.clear()

    # Mock git status: src/app.py is dirty
    monkeypatch.setattr(watcher, "_run_git_status_porcelain", lambda: {"src/app.py"})
    monkeypatch.setattr(watcher, "_get_current_branch", lambda: "main")

    # Existing lock for src/app.py with matching SESSION_TOKEN
    current_token = mod.SESSION_TOKEN

    class FakeResponse:
        data = [
            {
                "file_path": "src/app.py",
                "developer_id": "alice",
                "lock_token": current_token,
                "branch_name": "main",
            }
        ]

    rpc_called = []

    class FakeClient:
        def table(self, name):
            return self

        def select(self, *args):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return FakeResponse()

        def rpc(self, name, params):
            rpc_called.append(name)
            return self

    client = FakeClient()
    mod._reconcile_on_startup(client)

    # File should be re-adopted (in _local_owned_locks)
    assert "src/app.py" in mod._local_owned_locks
    # acquire_lock RPC should NOT have been called
    assert "acquire_lock" not in rpc_called

    # Clean up
    mod._local_owned_locks.clear()


def test_reconcile_releases_stale_clean_lock(monkeypatch):
    """§8d: Locked file that is now clean is released as stale.

    When startup reconciliation finds a file locked by this developer but the file is
    NOT in git status (clean), it should delete the lock.
    """
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(watcher, "_is_ephemeral_dev", lambda d: False)

    mod._local_owned_locks.clear()
    mod._active_conflicts.clear()

    # Mock git status: NO dirty files
    monkeypatch.setattr(watcher, "_run_git_status_porcelain", lambda: set())
    monkeypatch.setattr(watcher, "_get_current_branch", lambda: "main")

    # Existing lock for src/old.py (stale)
    class FakeSelectResponse:
        data = [
            {
                "file_path": "src/old.py",
                "developer_id": "alice",
                "lock_token": "old-token",
                "branch_name": "main",
            }
        ]

    deleted_files = []

    class FakeTable:
        def __init__(self):
            self._file_path = None

        def select(self, *args):
            return self

        def delete(self):
            return self

        def eq(self, field, value):
            if field == "file_path":
                self._file_path = value
            return self

        def execute(self):
            if self._file_path:
                deleted_files.append(self._file_path)
                return None
            return FakeSelectResponse()

    class FakeClient:
        def table(self, name):
            return FakeTable()

    client = FakeClient()
    mod._reconcile_on_startup(client)

    # src/old.py should have been released
    assert "src/old.py" in deleted_files
    # Should NOT be in _local_owned_locks
    assert "src/old.py" not in mod._local_owned_locks

    mod._local_owned_locks.clear()


def test_reconcile_acquires_lock_for_new_dirty_file(monkeypatch):
    """§8e: Dirty file with no existing lock is acquired at startup.

    When startup reconciliation finds a dirty file that has no existing lock in
    Supabase, it should call acquire_lock RPC to lock it.
    """
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(watcher, "_is_ephemeral_dev", lambda d: False)

    mod._local_owned_locks.clear()
    mod._active_conflicts.clear()

    # Mock git status: src/new.py is dirty
    monkeypatch.setattr(watcher, "_run_git_status_porcelain", lambda: {"src/new.py"})
    monkeypatch.setattr(watcher, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(watcher, "_should_ignore_path", lambda p: False)

    # No existing locks
    class FakeSelectResponse:
        data = []

    class FakeRPCResponse:
        data = [{"status": "ok"}]

    rpc_calls = []

    class FakeRPCChain:
        def execute(self):
            return FakeRPCResponse()

    class FakeClient:
        def table(self, name):
            return self

        def select(self, *args):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return FakeSelectResponse()

        def rpc(self, name, params):
            rpc_calls.append({"name": name, "params": params})
            return FakeRPCChain()

    client = FakeClient()
    mod._reconcile_on_startup(client)

    # acquire_lock RPC should have been called for src/new.py
    assert len(rpc_calls) == 1
    assert rpc_calls[0]["name"] == "acquire_lock"
    assert rpc_calls[0]["params"]["p_file_path"] == "src/new.py"
    # File should now be in _local_owned_locks
    assert "src/new.py" in mod._local_owned_locks

    mod._local_owned_locks.clear()


def test_reconcile_post_restart_conflict_non_interactive(monkeypatch):
    """§8f: Post-restart conflict in non-TTY defaults to continue.

    When a dirty file is locked by another developer and stdin is not a TTY, the watcher
    should add the file to _active_conflicts and continue running (no interactive
    prompt).
    """
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(watcher, "_is_ephemeral_dev", lambda d: False)

    mod._local_owned_locks.clear()
    mod._active_conflicts.clear()

    # Mock git status: src/shared.py is dirty
    monkeypatch.setattr(watcher, "_run_git_status_porcelain", lambda: {"src/shared.py"})
    monkeypatch.setattr(watcher, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(watcher, "_should_ignore_path", lambda p: False)
    # Non-interactive
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: False})())

    # No existing locks for alice
    class FakeSelectResponse:
        data = []

    # RPC returns conflict
    class FakeConflictResponse:
        data = [{"status": "conflict", "owner": "bob"}]

    class FakeRPCChain:
        def execute(self):
            return FakeConflictResponse()

    notify_calls = []
    monkeypatch.setattr(watcher, "_notify", lambda t, m: notify_calls.append((t, m)))

    class FakeClient:
        def table(self, name):
            return self

        def select(self, *args):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return FakeSelectResponse()

        def rpc(self, name, params):
            return FakeRPCChain()

    client = FakeClient()
    mod._reconcile_on_startup(client)

    # File should be in _active_conflicts
    assert "src/shared.py" in mod._active_conflicts
    # Should NOT be in _local_owned_locks (conflict)
    assert "src/shared.py" not in mod._local_owned_locks
    # Notification should have been sent
    assert any("Post-restart" in t for t, m in notify_calls)

    mod._active_conflicts.clear()


def test_reconcile_multi_session_different_token_non_interactive(monkeypatch):
    """§8g: Different session token in non-TTY defaults to leave lock.

    When startup reconciliation finds a dirty file locked by this developer but with a
    different session token, and stdin is not a TTY, it should leave the lock untouched
    (safe default).
    """
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(watcher, "_is_ephemeral_dev", lambda d: False)

    mod._local_owned_locks.clear()
    mod._active_conflicts.clear()

    # Mock git status: src/multi.py is dirty
    monkeypatch.setattr(watcher, "_run_git_status_porcelain", lambda: {"src/multi.py"})
    monkeypatch.setattr(watcher, "_get_current_branch", lambda: "main")
    # Non-interactive
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: False})())

    # Existing lock with DIFFERENT token
    class FakeSelectResponse:
        data = [
            {
                "file_path": "src/multi.py",
                "developer_id": "alice",
                "lock_token": "other-machine-token-12345",
                "branch_name": "main",
            }
        ]

    rpc_calls = []

    class FakeClient:
        def table(self, name):
            return self

        def select(self, *args):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return FakeSelectResponse()

        def rpc(self, name, params):
            rpc_calls.append(name)
            return self

    client = FakeClient()
    mod._reconcile_on_startup(client)

    # Lock should NOT be re-adopted (different token, non-interactive → leave)
    assert "src/multi.py" not in mod._local_owned_locks
    assert "acquire_lock" not in rpc_calls

    mod._local_owned_locks.clear()


watcher = load_watcher_module()


def test_reconcile_stale_release_exception_and_acquire_exception(monkeypatch):
    """Reconcile should continue when stale-release or acquire raises exceptions."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda d: False)
    monkeypatch.setattr(mod, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: {"src/new.py"})
    monkeypatch.setattr(mod, "_should_ignore_path", lambda p: False)

    # Existing stale lock is clean (not in dirty set), and unlocking it raises.
    existing = [{"file_path": "src/stale.py", "lock_token": "x"}]

    class FakeClient:
        def __init__(self):
            self._mode = "select"

        def table(self, name):
            return self

        def select(self, *args):
            self._mode = "select"
            return self

        def update(self, *args, **kwargs):
            self._mode = "update"
            return self

        def delete(self):
            self._mode = "delete"
            return self

        def eq(self, *args):
            return self

        def execute(self):
            if self._mode == "select":
                return type("R", (), {"data": existing})()
            raise RuntimeError("db failure")

        def rpc(self, *args, **kwargs):
            return self

    # Should swallow both stale-release and acquire exceptions.
    mod._reconcile_on_startup(FakeClient())


def test_reconcile_skips_ignored_unlocked_dirty_files(monkeypatch):
    """Ignored dirty files should not trigger acquire_lock RPC."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda d: False)
    monkeypatch.setattr(mod, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: {".git/config"})
    monkeypatch.setattr(mod, "_should_ignore_path", lambda p: True)

    rpc_calls = []

    class FakeClient:
        def table(self, name):
            return self

        def select(self, *args):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return type("R", (), {"data": []})()

        def rpc(self, *args, **kwargs):
            rpc_calls.append(True)
            return self

    mod._reconcile_on_startup(FakeClient())
    assert rpc_calls == []


def test_get_modified_and_unpushed_files_status_exception(monkeypatch):
    """If git status fails, helper should continue and return best-effort set."""
    mod = load_watcher_module()

    def _check_output(cmd, *args, **kwargs):
        if cmd[:3] == ["git", "status", "--porcelain"]:
            raise RuntimeError("status fail")
        if cmd[:2] == ["git", "rev-parse"]:
            return b"origin/main\n"
        if cmd[:3] == ["git", "diff", "--name-only"]:
            return b"src/from_diff.py\n"
        return b""

    monkeypatch.setattr(mod.subprocess, "check_output", _check_output)
    monkeypatch.setattr(mod, "_normalize_path", lambda p, root: p)
    monkeypatch.setattr(mod, "_should_ignore_path", lambda p: False)
    out = mod._get_modified_and_unpushed_files()
    assert "src/from_diff.py" in out


def test_reconcile_on_startup_git_status_exception(monkeypatch):
    """_reconcile_on_startup exits cleanly when git status fails."""
    mod = load_watcher_module()
    monkeypatch.setattr(
        mod,
        "_run_git_status_porcelain",
        lambda: (_ for _ in ()).throw(RuntimeError("git fail")),
    )

    class _Client:
        def table(self, _name):
            return self

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    mod._reconcile_on_startup(_Client())  # should not raise


def test_reconcile_ephemeral_developer_short_circuit(monkeypatch):
    """Ephemeral developers should skip startup reconciliation entirely."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev_1")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda d: True)

    class FakeClient:
        def table(self, name):
            raise AssertionError("should not query DB for ephemeral dev")

    mod._reconcile_on_startup(FakeClient())


def test_reconcile_same_machine_re_adopt_update_exception(monkeypatch):
    """Same-machine token mismatch should re-adopt even if token update fails."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda d: False)
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: {"src/file.py"})
    monkeypatch.setattr(mod, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(mod, "_is_same_machine_token", lambda token: True)

    mod._local_owned_locks.clear()

    class FakeClient:
        def __init__(self):
            self._mode = "select"

        def table(self, name):
            return self

        def select(self, *args):
            self._mode = "select"
            return self

        def update(self, *args, **kwargs):
            self._mode = "update"
            return self

        def eq(self, *args):
            return self

        def execute(self):
            if self._mode == "select":
                return type(
                    "R",
                    (),
                    {
                        "data": [
                            {
                                "file_path": "src/file.py",
                                "developer_id": "alice",
                                "lock_token": "other-token",
                            }
                        ]
                    },
                )()
            raise RuntimeError("update failed")

        def rpc(self, *args, **kwargs):
            return self

    mod._reconcile_on_startup(FakeClient())
    assert "src/file.py" in mod._local_owned_locks
