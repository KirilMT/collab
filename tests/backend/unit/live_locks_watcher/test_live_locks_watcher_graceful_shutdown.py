"""Graceful shutdown tests for live_locks_watcher."""

from __future__ import annotations

from ._helpers import load_watcher_module


def test_graceful_shutdown_functionality(monkeypatch, tmp_path):
    """Graceful shutdown removes the PID file when invoked."""
    mod = load_watcher_module()
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(mod, "_shutdown_done", False)
    monkeypatch.setattr(mod, "DEVELOPER_ID", None)

    pid_file = tmp_path / "watcher.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    mod._graceful_shutdown()

    assert not pid_file.exists()


def test_graceful_shutdown_with_valid_dev_id(monkeypatch, tmp_path):
    """_graceful_shutdown releases locks for clean files and removes the PID file."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev")
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "_shutdown_done", False)
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    pid_file = tmp_path / "watcher.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    # Mock git status returning empty (all files clean  all locks released)
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())
    monkeypatch.setattr(mod, "_local_owned_locks", {"collab/clean.py"})

    deleted_paths = []

    class FakeTable:
        def __init__(self):
            self._file_path = None
            self._is_delete = False

        def delete(self):
            self._is_delete = True
            return self

        def select(self, *args):
            return self

        def eq(self, field, value):
            if field == "file_path" and self._is_delete:
                self._file_path = value
            return self

        def execute(self):
            if self._file_path and self._is_delete:
                deleted_paths.append(self._file_path)
            return type("R", (), {"data": []})()

    class FakeSupaClient:
        def table(self, name):
            return FakeTable()

    monkeypatch.setattr(mod, "create_client", lambda url, key: FakeSupaClient())

    mod._graceful_shutdown()

    assert "collab/clean.py" in deleted_paths
    assert not pid_file.exists()


def test_graceful_shutdown_create_client_failure_still_removes_pid(
    monkeypatch, tmp_path, caplog
):
    """A client-construction failure is logged but the PID file is still removed."""
    import logging

    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev")
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "_shutdown_done", False)
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    pid_file = tmp_path / "watcher.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    def exploding_client(url, key):
        raise RuntimeError("Connection failed")

    monkeypatch.setattr(mod, "create_client", exploding_client)

    with caplog.at_level(logging.ERROR, logger=mod.logger.name):
        mod._graceful_shutdown()

    assert "Error releasing locks during shutdown" in caplog.text
    assert not pid_file.exists()


def test_graceful_shutdown_no_dev_id(monkeypatch, tmp_path):
    mod = load_watcher_module()
    """Test _graceful_shutdown when DEVELOPER_ID is None."""
    monkeypatch.setattr(mod, "DEVELOPER_ID", None)

    pid_file = tmp_path / "watcher.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    mod._graceful_shutdown()
    assert not pid_file.exists()


def test_graceful_shutdown_pid_file_missing(monkeypatch, tmp_path):
    """Test _graceful_shutdown when PID file doesn't exist."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", None)
    monkeypatch.setattr(mod, "_shutdown_done", False)
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "missing.pid"))

    mod._graceful_shutdown()  # Should not raise
    assert mod._shutdown_done is True


def test_graceful_shutdown_pid_oserror(monkeypatch, tmp_path):
    """Test _graceful_shutdown handles OSError when removing PID file."""
    import os

    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", None)
    monkeypatch.setattr(mod, "_shutdown_done", False)
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    # Create a PID file path that will fail on os.remove
    pid_file = tmp_path / "locked.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    original_remove = os.remove

    def failing_remove(path):
        if "locked.pid" in str(path):
            raise OSError("Permission denied")
        return original_remove(path)

    monkeypatch.setattr(os, "remove", failing_remove)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    mod._graceful_shutdown()  # Should not raise

    # All three remove attempts fail, so the file remains on disk.
    assert pid_file.exists()


def test_graceful_shutdown_guard_prevents_double_run(monkeypatch, tmp_path):
    mod = load_watcher_module()
    """_graceful_shutdown runs only once; second call is a no-op."""
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev")
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_tag")
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "pid"))
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    # Mock git status  all clean
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    call_count = [0]

    class FakeTable:
        def delete(self):
            return self

        def select(self, *args):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            call_count[0] += 1
            return type("R", (), {"data": []})()

    class FakeClient:
        def table(self, name):
            return FakeTable()

    monkeypatch.setattr(mod, "create_client", lambda url, key: FakeClient())

    mod._graceful_shutdown()  # first call  runs
    first_count = call_count[0]
    mod._graceful_shutdown()  # second call  guard returns immediately

    assert call_count[0] == first_count  # no additional calls after guard


def test_graceful_shutdown_dev_id_without_credentials(monkeypatch, tmp_path):
    mod = load_watcher_module()
    """_graceful_shutdown skips lock release when credentials are missing."""
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev")
    monkeypatch.setattr(mod, "SUPABASE_URL", None)  # missing
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    pid_file = tmp_path / "watcher.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    mod._graceful_shutdown()  # should not attempt API call
    assert not pid_file.exists()


def test_graceful_shutdown_queries_supabase_when_local_empty(monkeypatch, tmp_path):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev")
    monkeypatch.setattr(mod, "SUPABASE_URL", "http://test")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "key")
    monkeypatch.setattr(mod, "_shutdown_done", False)
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "pid.txt"))
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: {"collab/dirty.py"})

    mod._local_owned_locks.clear()

    deleted_paths = []

    class FakeSelectResp:
        data = [{"file_path": "collab/clean.py"}, {"file_path": "collab/dirty.py"}]

    class FakeTable:
        def select(self, *args):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return FakeSelectResp()

        def delete(self):
            return self

    # For delete eq chaining
    class DeleteFakeTable:
        def __init__(self):
            self.p = None

        def delete(self):
            return self

        def eq(self, field, value):
            if field == "file_path":
                self.p = value
            return self

        def execute(self):
            if self.p:
                deleted_paths.append(self.p)

    class FakeClient:
        def table(self, name):
            if not getattr(self, "selected", False):
                self.selected = True
                return FakeTable()
            return DeleteFakeTable()

    monkeypatch.setattr(mod, "create_client", lambda url, key: FakeClient())

    mod._graceful_shutdown()

    assert "collab/clean.py" in deleted_paths
    assert "collab/dirty.py" not in deleted_paths


def test_graceful_shutdown_keeps_dirty_locks(monkeypatch, tmp_path):
    mod = load_watcher_module()
    """§8a: Dirty files are NOT released during shutdown.

    When _graceful_shutdown runs and git status shows files still dirty, those files'
    locks must be preserved in Supabase (not deleted).
    """
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev")
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    pid_file = tmp_path / "watcher.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    # Simulate: collab/dirty.py is still dirty, collab/clean.py is clean
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: {"collab/dirty.py"})
    # Pre-populate _local_owned_locks with both files
    mod._local_owned_locks.clear()
    mod._local_owned_locks.update({"collab/dirty.py", "collab/clean.py"})

    deleted_files = []

    class FakeTable:
        def __init__(self):
            self._file_path = None
            self._is_delete = False

        def delete(self):
            self._is_delete = True
            return self

        def select(self, *args):
            return self

        def eq(self, field, value):
            if field == "file_path" and self._is_delete:
                self._file_path = value
            return self

        def execute(self):
            if self._file_path and self._is_delete:
                deleted_files.append(self._file_path)
            return type("R", (), {"data": []})()

    class FakeSupaClient:
        def table(self, name):
            return FakeTable()

    monkeypatch.setattr(mod, "create_client", lambda url, key: FakeSupaClient())

    mod._graceful_shutdown()

    # collab/clean.py should have been released; collab/dirty.py should NOT
    assert "collab/clean.py" in deleted_files
    assert "collab/dirty.py" not in deleted_files
    assert not pid_file.exists()

    # Clean up
    mod._local_owned_locks.clear()


def test_graceful_shutdown_local_empty_query_exception(monkeypatch, tmp_path):
    """When local lock set is empty and query fails, shutdown continues safely."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev")
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "_shutdown_done", False)
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    pid_file = tmp_path / "watcher.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    monkeypatch.setattr(mod, "_local_owned_locks", set())

    class _BrokenTable:
        def select(self, *args):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            raise RuntimeError("query fail")

    class _Client:
        def table(self, _name):
            return _BrokenTable()

    monkeypatch.setattr(mod, "create_client", lambda url, key: _Client())
    mod._graceful_shutdown()  # should not raise

    assert not pid_file.exists()
    assert mod._local_owned_locks == set()


def test_graceful_shutdown_git_failure_preserves_all(monkeypatch, tmp_path):
    mod = load_watcher_module()
    """§8b: Git failure during shutdown preserves locks (fail-closed).

    When _run_git_status_porcelain raises an exception, _graceful_shutdown should
    preserve all locks rather than releasing them. This matches lock_client daemon
    semantics and avoids unexpected lock loss when Git is temporarily unavailable.
    """
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev")
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    pid_file = tmp_path / "watcher.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    # Make git status fail
    def failing_git_status():
        raise RuntimeError("git not available")

    monkeypatch.setattr(mod, "_run_git_status_porcelain", failing_git_status)

    blanket_deleted = []

    class FakeTable:
        def __init__(self):
            self._eq_args = []

        def delete(self):
            return self

        def eq(self, field, value):
            self._eq_args.append((field, value))
            return self

        def execute(self):
            # Track that blanket delete was called (developer_id only)
            dev_eq = [a for a in self._eq_args if a[0] == "developer_id"]
            if dev_eq:
                blanket_deleted.append(dev_eq[0][1])
            return None

    class FakeSupaClient:
        def table(self, name):
            return FakeTable()

    monkeypatch.setattr(mod, "create_client", lambda url, key: FakeSupaClient())

    # Pre-populate _local_owned_locks so we can verify locks are preserved
    monkeypatch.setattr(mod, "_local_owned_locks", {"collab/a.py", "collab/b.py"})

    mod._graceful_shutdown()

    # Blanket release should NOT have been called (fail-closed)
    assert "test_dev" not in blanket_deleted
    assert not pid_file.exists()


def test_graceful_shutdown_per_file_release_exception(monkeypatch, tmp_path, caplog):
    """A per-file release error is logged and the lock is retained locally."""
    import logging

    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev")
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "_shutdown_done", False)
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    pid_file = tmp_path / "watcher.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())
    monkeypatch.setattr(mod, "_local_owned_locks", {"collab/will_fail.py"})

    class FakeClient:
        def table(self, name):
            return self

        def delete(self):
            return self

        def select(self, *args):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            raise RuntimeError("backend down")

    monkeypatch.setattr(mod, "create_client", lambda url, key: FakeClient())

    with caplog.at_level(logging.ERROR, logger=mod.logger.name):
        mod._graceful_shutdown()

    # The failing file's lock is preserved (never discarded) and the error logged.
    assert "collab/will_fail.py" in mod._local_owned_locks
    assert (
        "Failed to release lock for collab/will_fail.py during shutdown" in caplog.text
    )
    assert not pid_file.exists()


def test_graceful_shutdown_pid_remove_retries_then_warns(monkeypatch, tmp_path, caplog):
    """PID removal should retry twice and warn after three OSError attempts."""
    import logging

    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_shutdown_done", False)
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(mod, "DEVELOPER_ID", None)

    pid_file = tmp_path / "blocked.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    sleep_calls = []
    monkeypatch.setattr(mod.os.path, "exists", lambda p: True)
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleep_calls.append(s))
    monkeypatch.setattr(
        mod.os, "remove", lambda p: (_ for _ in ()).throw(OSError("locked"))
    )

    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        mod._graceful_shutdown()  # no raise expected after retry loop

    assert "Could not remove PID file after 3 attempts" in caplog.text
    # Two retries (after the 1st and 2nd failures) before the final warning.
    assert len(sleep_calls) == 2
    assert pid_file.exists()
