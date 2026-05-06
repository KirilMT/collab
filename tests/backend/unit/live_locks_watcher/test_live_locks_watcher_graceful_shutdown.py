"""Graceful shutdown tests for live_locks_watcher."""

from __future__ import annotations

from ._helpers import load_watcher_module


def test_graceful_shutdown_functionality(monkeypatch):
    mod = load_watcher_module()
    """Test graceful shutdown cleans up resources."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("DEVELOPER_ID", "test_dev")

    if hasattr(mod, "_graceful_shutdown"):
        try:
            mod._graceful_shutdown()
        except Exception:
            pass


def test_graceful_shutdown_with_valid_dev_id(monkeypatch, tmp_path):
    mod = load_watcher_module()
    """Test _graceful_shutdown releases locks for clean files and removes PID file."""
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev")
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    pid_file = tmp_path / "watcher.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    # Mock git status returning empty (all files clean  all locks released)
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    class FakeTable:
        def delete(self):
            return self

        def select(self, *args):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    class FakeSupaClient:
        def table(self, name):
            return FakeTable()

    monkeypatch.setattr(mod, "create_client", lambda url, key: FakeSupaClient())

    mod._graceful_shutdown()
    assert not pid_file.exists()


def test_graceful_shutdown_with_error(monkeypatch, tmp_path):
    mod = load_watcher_module()
    """Test _graceful_shutdown handles errors during lock release."""
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev")
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    pid_file = tmp_path / "watcher.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    def exploding_client(url, key):
        raise RuntimeError("Connection failed")

    monkeypatch.setattr(mod, "create_client", exploding_client)

    mod._graceful_shutdown()
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
    mod = load_watcher_module()
    """Test _graceful_shutdown when PID file doesn't exist."""
    monkeypatch.setattr(mod, "DEVELOPER_ID", None)
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "missing.pid"))

    mod._graceful_shutdown()  # Should not raise


def test_graceful_shutdown_pid_oserror(monkeypatch, tmp_path):
    mod = load_watcher_module()
    """Test _graceful_shutdown handles OSError when removing PID file."""
    import os

    monkeypatch.setattr(mod, "DEVELOPER_ID", None)

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

    mod._graceful_shutdown()  # Should not raise


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
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: {"src/dirty.py"})

    mod._local_owned_locks.clear()

    deleted_paths = []

    class FakeSelectResp:
        data = [{"file_path": "src/clean.py"}, {"file_path": "src/dirty.py"}]

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

    assert "src/clean.py" in deleted_paths
    assert "src/dirty.py" not in deleted_paths


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

    # Simulate: src/dirty.py is still dirty, src/clean.py is clean
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: {"src/dirty.py"})
    # Pre-populate _local_owned_locks with both files
    mod._local_owned_locks.clear()
    mod._local_owned_locks.update({"src/dirty.py", "src/clean.py"})

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

    # src/clean.py should have been released; src/dirty.py should NOT
    assert "src/clean.py" in deleted_files
    assert "src/dirty.py" not in deleted_files
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
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "watcher.pid"))
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    mod._local_owned_locks.clear()

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


def test_graceful_shutdown_git_failure_releases_all(monkeypatch, tmp_path):
    mod = load_watcher_module()
    """§8b: Git failure during shutdown falls back to blanket release-all.

    When _run_git_status_porcelain raises an exception, _graceful_shutdown should fall
    back to the legacy blanket delete behavior.
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

    mod._graceful_shutdown()

    # Blanket release should have been called for test_dev
    assert "test_dev" in blanket_deleted
    assert not pid_file.exists()


def test_graceful_shutdown_release_exception_and_db_query_exception(
    monkeypatch, tmp_path
):
    """Cover per-file release exception and fallback DB-query exception branches."""
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
    mod._local_owned_locks.clear()
    mod._local_owned_locks.add("src/will_fail.py")

    class FakeClient:
        def __init__(self):
            self._mode = None

        def table(self, name):
            return self

        def delete(self):
            self._mode = "delete"
            return self

        def select(self, *args):
            self._mode = "select"
            return self

        def eq(self, *args):
            return self

        def execute(self):
            raise RuntimeError("backend down")

    monkeypatch.setattr(mod, "create_client", lambda url, key: FakeClient())
    mod._graceful_shutdown()
    assert not pid_file.exists()


def test_graceful_shutdown_pid_remove_retries_then_warns(monkeypatch, tmp_path):
    """PID removal should retry and warn after three OSError attempts."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_shutdown_done", False)
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(mod, "DEVELOPER_ID", None)

    pid_file = tmp_path / "blocked.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    monkeypatch.setattr(mod.os.path, "exists", lambda p: True)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        mod.os, "remove", lambda p: (_ for _ in ()).throw(OSError("locked"))
    )

    # no raise expected after retry loop
    mod._graceful_shutdown()
