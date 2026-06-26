"""Reconciliation startup tests for live_locks_watcher."""

from __future__ import annotations

import sys

from ._helpers import load_watcher_module, patch_git_capture

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

    # Mock git status: collab/app.py is dirty
    monkeypatch.setattr(watcher, "_run_git_status_porcelain", lambda: {"collab/app.py"})
    monkeypatch.setattr(watcher, "_get_current_branch", lambda: "main")

    # Existing lock for collab/app.py with matching SESSION_TOKEN
    current_token = mod.SESSION_TOKEN

    class FakeResponse:
        data = [
            {
                "file_path": "collab/app.py",
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
    assert "collab/app.py" in mod._local_owned_locks
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

    # Existing lock for collab/old.py (stale)
    class FakeSelectResponse:
        data = [
            {
                "file_path": "collab/old.py",
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

    # collab/old.py should have been released
    assert "collab/old.py" in deleted_files
    # Should NOT be in _local_owned_locks
    assert "collab/old.py" not in mod._local_owned_locks

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

    # Mock git status: collab/new.py is dirty
    monkeypatch.setattr(watcher, "_run_git_status_porcelain", lambda: {"collab/new.py"})
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

    # acquire_lock RPC should have been called for collab/new.py
    assert len(rpc_calls) == 1
    assert rpc_calls[0]["name"] == "acquire_lock"
    assert rpc_calls[0]["params"]["p_file_path"] == "collab/new.py"
    # File should now be in _local_owned_locks
    assert "collab/new.py" in mod._local_owned_locks

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

    # Mock git status: collab/shared.py is dirty
    monkeypatch.setattr(
        watcher, "_run_git_status_porcelain", lambda: {"collab/shared.py"}
    )
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
    assert "collab/shared.py" in mod._active_conflicts
    # Should NOT be in _local_owned_locks (conflict)
    assert "collab/shared.py" not in mod._local_owned_locks
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

    # Mock git status: collab/multi.py is dirty
    monkeypatch.setattr(
        watcher, "_run_git_status_porcelain", lambda: {"collab/multi.py"}
    )
    monkeypatch.setattr(watcher, "_get_current_branch", lambda: "main")
    # Non-interactive
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: False})())

    # Existing lock with DIFFERENT token
    class FakeSelectResponse:
        data = [
            {
                "file_path": "collab/multi.py",
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
    assert "collab/multi.py" not in mod._local_owned_locks
    assert "acquire_lock" not in rpc_calls

    mod._local_owned_locks.clear()


watcher = load_watcher_module()


def test_reconcile_stale_release_exception_and_acquire_exception(monkeypatch, caplog):
    """Reconcile continues and logs when stale-release and acquire both raise."""
    import logging

    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda d: False)
    monkeypatch.setattr(mod, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: {"collab/new.py"})
    monkeypatch.setattr(mod, "_should_ignore_path", lambda p: False)
    monkeypatch.setattr(mod, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_local_owned_locks", set())

    # Existing stale lock is clean (not in dirty set), and unlocking it raises.
    existing = [{"file_path": "collab/stale.py", "lock_token": "x"}]

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

    with caplog.at_level(logging.ERROR, logger=mod.logger.name):
        # Should swallow both stale-release and acquire exceptions.
        mod._reconcile_on_startup(FakeClient())

    # Neither file ends up owned locally because both operations failed.
    assert "collab/stale.py" not in mod._local_owned_locks
    assert "collab/new.py" not in mod._local_owned_locks
    assert "Failed to release stale lock for collab/stale.py" in caplog.text
    assert (
        "Failed to acquire lock for collab/new.py during reconciliation" in caplog.text
    )


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

    def _git(argv, **_k):
        if len(argv) >= 2 and argv[1] == "status":
            return ""
        if len(argv) >= 2 and argv[1] == "rev-parse":
            return "origin/main"
        if len(argv) >= 2 and argv[1] == "diff":
            return "collab/from_diff.py"
        return ""

    patch_git_capture(monkeypatch, mod, _git)
    monkeypatch.setattr(mod, "_normalize_path", lambda p, root: p)
    monkeypatch.setattr(mod, "_should_ignore_path", lambda p: False)
    out = mod._get_modified_and_unpushed_files()
    assert "collab/from_diff.py" in out


def test_get_modified_and_unpushed_files_origin_main_fallback(monkeypatch):
    """With no upstream, the watcher diffs committed work against origin/main."""
    mod = load_watcher_module()
    monkeypatch.delenv("COLLAB_LOCK_BASE_REF", raising=False)

    def _git(argv, **_k):
        joined = " ".join(argv)
        if argv[1] == "status":
            return ""
        # No upstream configured.
        if "symbolic-full-name" in joined and "@{u}" in joined:
            return ""
        # Current branch lookup.
        if argv[1] == "rev-parse" and argv[2:] == ["--abbrev-ref", "HEAD"]:
            return "feat/no-upstream"
        # origin/<branch> missing, origin/main present.
        if "--verify" in joined and "origin/feat/no-upstream" in joined:
            return ""
        if "--verify" in joined and "origin/main" in joined:
            return "abc123"
        if argv[1] == "diff":
            assert "origin/main...HEAD" in joined
            return "collab/lock_client.py"
        return ""

    patch_git_capture(monkeypatch, mod, _git)
    monkeypatch.setattr(mod, "_normalize_path", lambda p, root: p)
    monkeypatch.setattr(mod, "_should_ignore_path", lambda p: False)
    out = mod._get_modified_and_unpushed_files()
    assert "collab/lock_client.py" in out


def test_resolve_lock_diff_base_ref_none_when_no_remote(monkeypatch):
    """When no upstream/branch/remote base exists, return None (status-only)."""
    mod = load_watcher_module()
    monkeypatch.delenv("COLLAB_LOCK_BASE_REF", raising=False)
    patch_git_capture(monkeypatch, mod, lambda argv, **_k: "")
    assert mod._resolve_lock_diff_base_ref() is None


def test_resolve_lock_diff_base_ref_env_override(monkeypatch):
    """COLLAB_LOCK_BASE_REF is used when set, present, and no upstream exists."""
    mod = load_watcher_module()
    monkeypatch.setenv("COLLAB_LOCK_BASE_REF", "origin/develop")

    def _git(argv, **_k):
        joined = " ".join(argv)
        if "symbolic-full-name" in joined:
            return ""
        if "--verify" in joined and "origin/develop" in joined:
            return "sha"
        return ""

    patch_git_capture(monkeypatch, mod, _git)
    assert mod._resolve_lock_diff_base_ref() == "origin/develop"


def test_resolve_lock_diff_base_ref_origin_branch(monkeypatch):
    """Origin/<branch> is used when present and there is no upstream/override."""
    mod = load_watcher_module()
    monkeypatch.delenv("COLLAB_LOCK_BASE_REF", raising=False)

    def _git(argv, **_k):
        joined = " ".join(argv)
        if "symbolic-full-name" in joined:
            return ""
        if argv[1:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "feat/x"
        if "--verify" in joined and "origin/feat/x" in joined:
            return "sha"
        return ""

    patch_git_capture(monkeypatch, mod, _git)
    assert mod._resolve_lock_diff_base_ref() == "origin/feat/x"


def test_reconcile_on_startup_git_status_exception(monkeypatch):
    """_reconcile_on_startup exits cleanly (no acquire) when git status fails."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda d: False)
    monkeypatch.setattr(mod, "_local_owned_locks", set())
    monkeypatch.setattr(
        mod,
        "_run_git_status_porcelain",
        lambda: (_ for _ in ()).throw(RuntimeError("git fail")),
    )

    rpc_calls = []

    class _Client:
        def table(self, _name):
            return self

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def execute(self):
            return type("R", (), {"data": []})()

        def rpc(self, *a, **k):
            rpc_calls.append((a, k))
            return self

    mod._reconcile_on_startup(_Client())  # should not raise

    # git status failure short-circuits before any acquire RPC.
    assert rpc_calls == []
    assert mod._local_owned_locks == set()


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
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: {"collab/file.py"})
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
                                "file_path": "collab/file.py",
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
    assert "collab/file.py" in mod._local_owned_locks


def test_reconcile_releases_stale_locks_immediately(monkeypatch, caplog):
    """Stale locks (clean files) are released immediately regardless of age (#150,
    #151)."""
    import logging
    from datetime import datetime, timezone

    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    monkeypatch.setattr(mod, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())
    monkeypatch.setattr(mod, "_fetch_dev_other_identity_locks", lambda c: {})
    monkeypatch.setattr(mod, "_should_ignore_path", lambda p: False)
    monkeypatch.setattr(mod, "_handle_multi_session_lock", lambda c, f, t: None)
    monkeypatch.setattr(mod, "_notify", lambda t, m: None)
    mod._local_owned_locks.clear()
    mod._active_conflicts.clear()
    mod._lock_acquired_at.clear()

    now_iso = datetime.now(timezone.utc).isoformat()

    class FakeClient:
        def table(self, _name):
            return self

        def select(self, *_a, **_k):
            return self

        def delete(self):
            return self

        def update(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def execute(self):
            # Track delete calls separately
            return type(
                "R",
                (),
                {
                    "data": [
                        {
                            "file_path": "collab/app.py",
                            "developer_id": "alice",
                            "lock_token": mod.SESSION_TOKEN,
                            "acquired_at": now_iso,
                        }
                    ]
                },
            )()

        def rpc(self, *_a, **_k):
            return type(
                "R", (), {"execute": lambda self: type("E", (), {"data": []})()}
            )()

    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        mod._reconcile_on_startup(FakeClient())

    # The file is clean — should be STALE-RELEASED immediately, not kept.
    assert "STALE-RELEASED" in caplog.text
    assert "collab/app.py" not in mod._local_owned_locks


def test_reconcile_handles_malformed_acquired_at(monkeypatch, caplog):
    """Malformed acquired_at timestamps are silently skipped (except clause)."""
    import logging

    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_min_auto_lock_hold_seconds", lambda: 60)
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    monkeypatch.setattr(mod, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())
    monkeypatch.setattr(mod, "_fetch_dev_other_identity_locks", lambda c: {})
    monkeypatch.setattr(mod, "_should_ignore_path", lambda p: False)
    monkeypatch.setattr(mod, "_handle_multi_session_lock", lambda c, f, t: None)
    monkeypatch.setattr(mod, "_notify", lambda t, m: None)
    mod._local_owned_locks.clear()
    mod._active_conflicts.clear()
    mod._lock_acquired_at.clear()

    class FakeClient:
        def table(self, _name):
            return self

        def select(self, *_a, **_k):
            return self

        def delete(self):
            return self

        def update(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def execute(self):
            return type(
                "R",
                (),
                {
                    "data": [
                        {
                            "file_path": "collab/bad.py",
                            "developer_id": "alice",
                            "lock_token": mod.SESSION_TOKEN,
                            "acquired_at": "not-a-valid-date",
                        }
                    ]
                },
            )()

        def rpc(self, *_a, **_k):
            return type("R", (), {"execute": lambda s: type("E", (), {"data": []})()})()

    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        mod._reconcile_on_startup(FakeClient())

    assert "[STALE-RELEASED]" in caplog.text
