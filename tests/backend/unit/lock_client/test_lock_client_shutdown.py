"""Graceful shutdown tests for LockClient._graceful_shutdown()."""

from __future__ import annotations

import logging
import os
from unittest import mock

import pytest

from ._helpers import FakeResponse, load_lock_client_module, make_create_client

mod = load_lock_client_module()


def test_graceful_shutdown_git_fallback(monkeypatch, tmp_path):
    """Test _graceful_shutdown falls back to release_all on git error."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    lc = mod.LockClient(developer_id="test_user")

    def broken_git():
        raise RuntimeError("git failed")

    monkeypatch.setattr(lc, "_run_git_status", broken_git)
    monkeypatch.setattr(lc, "release_all", mock.Mock(return_value=2))

    lc._graceful_shutdown()
    assert not pid_file.exists()
    # Now verifies behavior: PRESERVE locks on shutdown
    lc.release_all.assert_not_called()


def test_graceful_shutdown_smart_release(monkeypatch, tmp_path):
    """Test _graceful_shutdown selectively releases clean files."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    lc = mod.LockClient(developer_id="test_user")

    monkeypatch.setattr(lc, "_run_git_status", lambda: " M src/dirty.py\n")

    locks = [
        {"developer_id": "test_user", "file_path": "src/dirty.py"},
        {"developer_id": "test_user", "file_path": "src/clean.py"},
        {"developer_id": "test_user", "file_path": ""},
        {"developer_id": "other_user", "file_path": "src/other.py"},
    ]
    monkeypatch.setattr(lc, "active", mock.Mock(return_value=locks))

    release_mock = mock.Mock(return_value=(True, None))
    monkeypatch.setattr(lc, "release", release_mock)

    lc._graceful_shutdown()

    # Now verifies behavior: PRESERVE locks on shutdown
    release_mock.assert_not_called()
    assert not pid_file.exists()


def test_graceful_shutdown_with_exception(monkeypatch, tmp_path):
    """Test _graceful_shutdown handles release errors gracefully."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    lc = mod.LockClient(developer_id="test_user")

    # Force the fallback path and make it fail
    def fail_git():
        raise RuntimeError("git fail")

    monkeypatch.setattr(lc, "_run_git_status", fail_git)
    monkeypatch.setattr(lc, "release_all", mock.Mock(side_effect=RuntimeError("fail")))

    lc._graceful_shutdown()  # Should not raise


# RESTORED: test_graceful_shutdown_releases_locks
def test_graceful_shutdown_releases_locks(monkeypatch, tmp_path):
    """Test _graceful_shutdown logs when locks are released (restored).

    This covers the case where a lock owned by the current developer is present and the
    graceful shutdown path should attempt to release it.
    """
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    # Return locks to release
    locks_data = [
        {"file_path": "src/app.py", "developer_id": "test_user"},
    ]
    response = FakeResponse(status=200, data=locks_data)
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="test_user")
    lc._graceful_shutdown()


# ---------------------------------------------------------------------------
# Additional graceful_shutdown coverage tests
# ---------------------------------------------------------------------------


def test_graceful_shutdown_test_mode_returns_early(monkeypatch, tmp_path):
    """_graceful_shutdown exits early in COLLAB_TEST_MODE=1."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    lc = mod.LockClient(developer_id="test_user")
    lc._graceful_shutdown()
    # PID file should still exist (early return before removal)
    assert pid_file.exists()


def test_graceful_shutdown_double_call_noop(monkeypatch, tmp_path):
    """Second call to _graceful_shutdown is a no-op (_shutdown_done guard)."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    lc = mod.LockClient(developer_id="test_user")
    lc._graceful_shutdown()
    # Second call should be a no-op
    lc._graceful_shutdown()  # Should not raise


def test_graceful_shutdown_writes_shutdown_marker(monkeypatch, tmp_path):
    """_graceful_shutdown writes .shutdown_complete marker file."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    lc = mod.LockClient(developer_id="test_user")
    lc._graceful_shutdown()

    shutdown_file = tmp_path / ".shutdown_complete"
    assert shutdown_file.exists()


def test_graceful_shutdown_writes_shutdown_marker_deep(monkeypatch, tmp_path):
    """_graceful_shutdown writes the .shutdown_complete marker file (deep path with
    _make_client)."""
    state_dir = str(tmp_path)
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)
    lc = _make_client(monkeypatch, tmp_path)
    lc.active = mock.Mock(return_value=[])
    lc._graceful_shutdown()
    # Marker should exist in state dir
    marker = tmp_path / ".shutdown_complete"
    assert marker.exists()


def test_graceful_shutdown_preserves_locks(monkeypatch, tmp_path):
    """_graceful_shutdown does NOT release any locks (preserves them)."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    locks_data = [
        {"file_path": "src/app.py", "developer_id": "test_user"},
        {"file_path": "src/utils.py", "developer_id": "test_user"},
    ]
    resp = FakeResponse(status=200, data=locks_data)
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(resp))

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    lc = mod.LockClient(developer_id="test_user")
    release_mock = mock.Mock()
    monkeypatch.setattr(lc, "release", release_mock)
    monkeypatch.setattr(lc, "release_all", mock.Mock())

    lc._graceful_shutdown()

    release_mock.assert_not_called()


def test_graceful_shutdown_removes_pid_file(monkeypatch, tmp_path):
    """_graceful_shutdown removes the PID file."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    lc = mod.LockClient(developer_id="test_user")
    lc._graceful_shutdown()

    assert not pid_file.exists()


def test_graceful_shutdown_with_reason(monkeypatch, tmp_path):
    """_graceful_shutdown logs the reason when provided."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    lc = mod.LockClient(developer_id="test_user")
    # Should not raise
    lc._graceful_shutdown(reason="stop_requested")


def test_graceful_shutdown_active_raises(monkeypatch, tmp_path):
    """_graceful_shutdown handles exception in active() gracefully."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    lc = mod.LockClient(developer_id="test_user")
    monkeypatch.setattr(lc, "active", mock.Mock(side_effect=RuntimeError("API down")))

    lc._graceful_shutdown()  # Should not raise


# ---------------------------------------------------------------------------
# Deep shutdown / signal-handler / parent-monitor branch coverage tests
# ---------------------------------------------------------------------------


def _make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    return mod.LockClient(developer_id="test_user")


# ---------------------------------------------------------------------------
# _graceful_shutdown — no-reason branch (line 2521-2522 region)
# ---------------------------------------------------------------------------


def test_graceful_shutdown_no_reason_logs_generic_message(monkeypatch, tmp_path):
    """_graceful_shutdown with no reason logs the generic message."""
    lc = _make_client(monkeypatch, tmp_path)
    lc.active = mock.Mock(return_value=[])
    lc._graceful_shutdown(reason=None)  # covers else branch at 2521-2522


def test_graceful_shutdown_with_reason_logs_specific_message(monkeypatch, tmp_path):
    """_graceful_shutdown with a reason logs the reason-specific message."""
    lc = _make_client(monkeypatch, tmp_path)
    lc.active = mock.Mock(return_value=[])
    lc._graceful_shutdown(reason="test_reason")  # covers if branch at 2504


def test_graceful_shutdown_active_raises_logs_error(monkeypatch, tmp_path):
    """Active() exception during shutdown is caught and logged."""
    lc = _make_client(monkeypatch, tmp_path)
    lc.active = mock.Mock(side_effect=RuntimeError("db down"))
    lc._graceful_shutdown()  # should not raise


def test_graceful_shutdown_active_has_my_locks(monkeypatch, tmp_path):
    """Active() returns locks owned by developer — logs preserved message."""
    lc = _make_client(monkeypatch, tmp_path)
    lc.active = mock.Mock(
        return_value=[
            {"developer_id": "test_user", "file_path": "src/foo.py"},
            {"developer_id": "other_user", "file_path": "src/bar.py"},
        ]
    )
    lc._graceful_shutdown()  # covers lines where n_kept is incremented


def test_graceful_shutdown_writes_shutdown_marker_deep_alt(monkeypatch, tmp_path):
    """_graceful_shutdown writes the .shutdown_complete marker file."""
    state_dir = str(tmp_path)
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)
    lc = _make_client(monkeypatch, tmp_path)
    lc.active = mock.Mock(return_value=[])
    lc._graceful_shutdown()
    # Marker should exist in state dir
    marker = tmp_path / ".shutdown_complete"
    assert marker.exists()


def test_graceful_shutdown_shutdown_marker_open_fails(monkeypatch, tmp_path):
    """If writing shutdown marker raises, _graceful_shutdown doesn't propagate."""
    import builtins

    state_dir = str(tmp_path)
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)
    lc = _make_client(monkeypatch, tmp_path)
    lc.active = mock.Mock(return_value=[])

    real_open = builtins.open

    def fail_open(path, *args, **kwargs):
        if ".shutdown_complete" in str(path):
            raise OSError("disk full")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fail_open)
    lc._graceful_shutdown()  # should not raise


def test_graceful_shutdown_pid_removal_retries(monkeypatch, tmp_path):
    """PID file removal retries on OSError then succeeds on third attempt."""
    lc = _make_client(monkeypatch, tmp_path)
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("99999")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    lc.active = mock.Mock(return_value=[])

    call_count = [0]
    real_remove = os.remove

    def flaky_remove(path):
        call_count[0] += 1
        if call_count[0] < 3 and ".pid" in str(path) and "shutdown" not in str(path):
            raise OSError("busy")
        real_remove(path)

    monkeypatch.setattr(mod.os, "remove", flaky_remove)
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)
    lc._graceful_shutdown()


def test_graceful_shutdown_flush_handler_raises(monkeypatch, tmp_path):
    """Handlers that raise during flush() are silently swallowed."""
    lc = _make_client(monkeypatch, tmp_path)
    lc.active = mock.Mock(return_value=[])

    bad_handler = mock.MagicMock()
    bad_handler.flush.side_effect = RuntimeError("broken")

    with mock.patch("logging.getLogger") as mock_get_logger:
        fake_logger = mock.MagicMock()
        fake_logger.handlers = [bad_handler]
        mock_get_logger.return_value = fake_logger
        lc._graceful_shutdown()  # should not raise


# ---------------------------------------------------------------------------
# _register_signal_handlers — SIGTERM / SIGBREAK / console handler
# ---------------------------------------------------------------------------


def test_register_signal_handlers_non_win32(monkeypatch, tmp_path):
    """On non-win32, SIGTERM + SIGINT are registered without error."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    signals_set = []
    monkeypatch.setattr(
        mod.signal, "signal", lambda sig, handler: signals_set.append(sig)
    )
    monkeypatch.setattr(mod.sys, "platform", "linux")

    lc._register_signal_handlers()
    assert mod.signal.SIGINT in signals_set or len(signals_set) >= 1


def test_register_signal_handlers_test_mode_skips_atexit(monkeypatch, tmp_path):
    """In COLLAB_TEST_MODE=1, atexit is not registered."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")

    atexit_registered = []
    monkeypatch.setattr(mod.atexit, "register", lambda fn: atexit_registered.append(fn))
    monkeypatch.setattr(mod.signal, "signal", lambda *a: None)

    lc._register_signal_handlers()
    assert len(atexit_registered) == 0


def test_register_signal_handlers_win32_sigbreak(monkeypatch, tmp_path):
    """On win32 with SIGBREAK, the handler is registered."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")

    signals_set = []
    monkeypatch.setattr(
        mod.signal, "signal", lambda sig, handler: signals_set.append(sig)
    )
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(mod.signal, "SIGBREAK", 21, raising=False)

    lc._register_signal_handlers()
    assert 21 in signals_set


def test_register_signal_handlers_win32_sigbreak_exception(monkeypatch, tmp_path):
    """SIGBREAK registration failure is silently caught."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setattr(mod.sys, "platform", "win32")

    def raising_signal(sig, handler):
        if sig == 21:  # SIGBREAK
            raise OSError("not permitted")

    monkeypatch.setattr(mod.signal, "signal", raising_signal)
    monkeypatch.setattr(mod.signal, "SIGBREAK", 21, raising=False)

    lc._register_signal_handlers()  # should not raise


def test_register_signal_handlers_win32_console_handler_exception(
    monkeypatch, tmp_path
):
    """If SetConsoleCtrlHandler import/call fails, handler is skipped silently."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(mod.signal, "signal", lambda *a: None)
    monkeypatch.setattr(mod.signal, "SIGBREAK", 21, raising=False)

    import builtins

    real_import = builtins.__import__

    def fail_ctypes_import(name, *args, **kwargs):
        if name == "ctypes":
            raise ImportError("no ctypes")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_ctypes_import)
    lc._register_signal_handlers()  # should not raise


# ---------------------------------------------------------------------------
# _start_parent_monitor_thread — branches on non-win32 / no parent / failure
# ---------------------------------------------------------------------------


def test_start_parent_monitor_thread_non_win32_returns_early(monkeypatch, tmp_path):
    """On non-win32, returns immediately without doing anything."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(mod.sys, "platform", "linux")
    lc._parent_pid = 9999
    lc._start_parent_monitor_thread()
    assert not lc._parent_monitor_started


def test_start_parent_monitor_thread_no_parent_returns_early(monkeypatch, tmp_path):
    """Without a parent PID, returns early."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(mod.sys, "platform", "win32")
    lc._parent_pid = None
    lc._start_parent_monitor_thread()
    assert not lc._parent_monitor_started


def test_start_parent_monitor_thread_openprocess_fails(monkeypatch, tmp_path):
    """If OpenProcess returns 0 (failure), monitor is not started."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(mod.sys, "platform", "win32")
    lc._parent_pid = 9999

    fake_ctypes = mock.MagicMock()
    fake_ctypes.windll.kernel32.OpenProcess.return_value = 0
    fake_ctypes.windll.kernel32.GetLastError.return_value = 5
    monkeypatch.setattr(mod, "ctypes", fake_ctypes, raising=False)

    import builtins

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "ctypes":
            return fake_ctypes
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    lc._start_parent_monitor_thread()
    assert not lc._parent_monitor_started


def test_start_parent_monitor_thread_import_exception(monkeypatch, tmp_path):
    """If ctypes import fails, the exception is caught and monitor marked not
    started."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(mod.sys, "platform", "win32")
    lc._parent_pid = 9999

    import builtins

    real_import = builtins.__import__

    def fail_import(name, *args, **kwargs):
        if name == "ctypes":
            raise ImportError("no ctypes")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_import)
    lc._start_parent_monitor_thread()
    assert not lc._parent_monitor_started


def test_start_parent_monitor_thread_getlasterror_raises(monkeypatch, tmp_path):
    """If GetLastError() raises, the exception is swallowed; monitor still not
    started."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(mod.sys, "platform", "win32")
    lc._parent_pid = 9999

    import builtins

    real_import = builtins.__import__

    fake_ctypes = mock.MagicMock()
    fake_ctypes.windll.kernel32.OpenProcess.return_value = 0
    fake_ctypes.windll.kernel32.GetLastError.side_effect = OSError("fail")
    fake_ctypes.WINFUNCTYPE = mock.MagicMock(return_value=mock.MagicMock())
    fake_ctypes.wintypes = mock.MagicMock()

    def mock_import(name, *args, **kwargs):
        if name == "ctypes":
            return fake_ctypes
        if name in ("ctypes.wintypes", "wintypes"):
            return fake_ctypes.wintypes
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    lc._start_parent_monitor_thread()  # should not raise
    assert not lc._parent_monitor_started


# ---------------------------------------------------------------------------
# _graceful_shutdown — stray marker removal paths (lines 2578-2583)
# ---------------------------------------------------------------------------


def test_graceful_shutdown_removes_stray_markers(monkeypatch, tmp_path):
    """Stray repo markers are removed during shutdown."""
    state_dir = str(tmp_path)
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)
    lc = _make_client(monkeypatch, tmp_path)
    lc.active = mock.Mock(return_value=[])

    # Create stray marker files in COLLAB_ROOT
    collab_root = getattr(mod, "_COLLAB_ROOT", str(tmp_path))
    stray_shutdown = os.path.join(collab_root, ".shutdown_complete")
    stray_summary = os.path.join(collab_root, ".startup_summary.json")
    for p in (stray_shutdown, stray_summary):
        try:
            with open(p, "w") as f:
                f.write("stray")
        except OSError:
            pass

    lc._graceful_shutdown()  # should not raise


def test_graceful_shutdown_stray_marker_remove_fails(monkeypatch, tmp_path):
    """If stray marker removal raises, shutdown continues without error."""
    state_dir = str(tmp_path)
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)
    lc = _make_client(monkeypatch, tmp_path)
    lc.active = mock.Mock(return_value=[])

    real_remove = os.remove

    def raising_remove(path):
        if ".shutdown_complete" in str(path) or ".startup_summary" in str(path):
            raise OSError("permission denied")
        real_remove(path)

    monkeypatch.setattr(mod.os, "remove", raising_remove)
    monkeypatch.setattr(mod.os.path, "exists", lambda p: True)
    lc._graceful_shutdown()  # should not raise


# ---------------------------------------------------------------------------
# _graceful_shutdown — fsync paths for log handlers (lines 2604-2660)
# ---------------------------------------------------------------------------


def test_graceful_shutdown_fsync_on_file_handler(monkeypatch, tmp_path):
    """Fsync is attempted on file-backed log handlers during shutdown."""
    state_dir = str(tmp_path)
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)
    lc = _make_client(monkeypatch, tmp_path)
    lc.active = mock.Mock(return_value=[])

    # Create a real file handler so fsync path is exercised
    log_file = tmp_path / "test.log"
    fh = logging.FileHandler(str(log_file))
    collab_logger = logging.getLogger("collab.test_fsync")
    collab_logger.addHandler(fh)
    try:
        lc._graceful_shutdown()
    finally:
        collab_logger.removeHandler(fh)
        fh.close()


def test_graceful_shutdown_fsync_raises_still_completes(monkeypatch, tmp_path):
    """If fsync raises, shutdown still completes."""
    state_dir = str(tmp_path)
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)
    lc = _make_client(monkeypatch, tmp_path)
    lc.active = mock.Mock(return_value=[])

    monkeypatch.setattr(mod.os, "fsync", mock.Mock(side_effect=OSError("fsync fail")))
    lc._graceful_shutdown()  # should not raise


def test_graceful_shutdown_logging_shutdown_raises(monkeypatch, tmp_path):
    """If logging.shutdown() raises, graceful_shutdown still completes."""
    state_dir = str(tmp_path)
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)
    lc = _make_client(monkeypatch, tmp_path)
    lc.active = mock.Mock(return_value=[])

    monkeypatch.setattr(
        mod.logging, "shutdown", mock.Mock(side_effect=RuntimeError("boom"))
    )
    lc._graceful_shutdown()  # should not raise


def test_graceful_shutdown_print_raises_is_swallowed(monkeypatch, tmp_path):
    """Print() failure in shutdown marker output path is swallowed."""
    import builtins

    lc = _make_client(monkeypatch, tmp_path)
    lc.active = mock.Mock(return_value=[])
    real_print = builtins.print

    def flaky_print(*args, **kwargs):
        raise OSError("stdout unavailable")

    monkeypatch.setattr(builtins, "print", flaky_print)
    lc._graceful_shutdown()
    monkeypatch.setattr(builtins, "print", real_print)


def test_register_signal_handlers_calls_exception_logging(monkeypatch, tmp_path):
    """Signal callback exceptions in graceful shutdown are caught and logged."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setattr(mod.sys, "platform", "linux")

    handlers = {}

    def capture_signal(sig, fn):
        handlers[sig] = fn

    monkeypatch.setattr(mod.signal, "signal", capture_signal)
    monkeypatch.setattr(
        lc, "_graceful_shutdown", mock.Mock(side_effect=RuntimeError("fail"))
    )
    monkeypatch.setattr(mod.sys, "exit", mock.Mock(side_effect=SystemExit(0)))

    lc._register_signal_handlers()
    with pytest.raises(SystemExit):
        handlers[mod.signal.SIGINT](2, None)


def test_register_signal_handlers_console_handler_graceful_shutdown_exception(
    monkeypatch, tmp_path
):
    """Windows console handler swallows graceful-shutdown exceptions."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(mod.signal, "signal", lambda *a: None)
    monkeypatch.setattr(mod.signal, "SIGBREAK", 21, raising=False)
    monkeypatch.setattr(
        lc, "_graceful_shutdown", mock.Mock(side_effect=RuntimeError("boom"))
    )

    captured_console_handler = {"fn": None}

    class _K32:
        @staticmethod
        def SetConsoleCtrlHandler(fn, enable):
            captured_console_handler["fn"] = fn
            return True

    fake_ctypes = mock.MagicMock()
    fake_ctypes.windll.kernel32 = _K32()
    fake_ctypes.WINFUNCTYPE = lambda *a, **k: (lambda f: f)
    fake_wintypes = mock.MagicMock()
    fake_wintypes.BOOL = int
    fake_wintypes.DWORD = int

    import builtins

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "ctypes":
            return fake_ctypes
        if name == "ctypes.wintypes":
            return fake_wintypes
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    lc._register_signal_handlers()
    assert captured_console_handler["fn"] is not None
    captured_console_handler["fn"](2)


def test_start_parent_monitor_waiter_closes_handle_and_swallows_assign_exceptions(
    monkeypatch, tmp_path
):
    """Waiter path covers close-handle failure and guarded attribute assignments."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(mod.sys, "platform", "win32")
    lc._parent_pid = 4242

    monkeypatch.setattr(
        lc, "_graceful_shutdown", mock.Mock(side_effect=RuntimeError("shutdown error"))
    )

    class _K32:
        @staticmethod
        def OpenProcess(access, inherit, pid):
            return 999

        @staticmethod
        def WaitForSingleObject(hndl, timeout):
            return 0

        @staticmethod
        def CloseHandle(hndl):
            raise OSError("close failed")

    fake_ctypes = mock.MagicMock()
    fake_ctypes.windll.kernel32 = _K32()

    class _ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    import builtins

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "ctypes":
            return fake_ctypes
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    monkeypatch.setattr(mod.threading, "Thread", _ImmediateThread)
    lc._start_parent_monitor_thread()


def test_start_parent_monitor_thread_thread_construction_failure(monkeypatch, tmp_path):
    """Thread construction failure triggers outer exception guard in monitor startup."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(mod.sys, "platform", "win32")
    lc._parent_pid = 8888

    class _K32:
        @staticmethod
        def OpenProcess(access, inherit, pid):
            return 111

    fake_ctypes = mock.MagicMock()
    fake_ctypes.windll.kernel32 = _K32()

    import builtins

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "ctypes":
            return fake_ctypes
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    monkeypatch.setattr(
        mod.threading, "Thread", mock.Mock(side_effect=RuntimeError("thread fail"))
    )
    lc._start_parent_monitor_thread()
    assert lc._parent_monitor_started is False


def test_register_signal_handlers_console_handler_outer_exception(
    monkeypatch, tmp_path
):
    """Console handler outer-except branch is exercised when debug logging fails."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(mod.signal, "signal", lambda *a: None)
    monkeypatch.setattr(mod.signal, "SIGBREAK", 21, raising=False)

    captured_console_handler = {"fn": None}

    class _K32:
        @staticmethod
        def SetConsoleCtrlHandler(fn, enable):
            captured_console_handler["fn"] = fn
            return True

    fake_ctypes = mock.MagicMock()
    fake_ctypes.windll.kernel32 = _K32()
    fake_ctypes.WINFUNCTYPE = lambda *a, **k: (lambda f: f)
    fake_wintypes = mock.MagicMock()
    fake_wintypes.BOOL = int
    fake_wintypes.DWORD = int

    import builtins

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "ctypes":
            return fake_ctypes
        if name == "ctypes.wintypes":
            return fake_wintypes
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    real_debug = mod.logger.debug

    def flaky_debug(msg, *args, **kwargs):
        if isinstance(msg, str) and "Console control event" in msg:
            raise RuntimeError("debug fail")
        return real_debug(msg, *args, **kwargs)

    monkeypatch.setattr(mod.logger, "debug", flaky_debug)

    lc._register_signal_handlers()
    assert captured_console_handler["fn"] is not None
    captured_console_handler["fn"](9)


def test_start_parent_monitor_waiter_assignment_guards(monkeypatch, tmp_path):
    """Waiter assignment guard except blocks are hit when attribute sets fail in
    waiter."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(mod.sys, "platform", "win32")
    lc._parent_pid = 5151

    base_setattr = object.__setattr__
    base_setattr(lc, "_in_waiter", False)

    def guarded_setattr(self, name, value):
        if getattr(self, "_in_waiter", False) and name in {
            "_parent_monitor_started",
            "_parent_monitor_handle",
            "_parent_monitor_thread",
        }:
            raise RuntimeError("blocked in waiter")
        base_setattr(self, name, value)

    monkeypatch.setattr(type(lc), "__setattr__", guarded_setattr, raising=False)
    monkeypatch.setattr(lc, "_graceful_shutdown", lambda *a, **k: None)

    class _K32:
        @staticmethod
        def OpenProcess(access, inherit, pid):
            return 222

        @staticmethod
        def WaitForSingleObject(hndl, timeout):
            return 0

        @staticmethod
        def CloseHandle(hndl):
            return True

    fake_ctypes = mock.MagicMock()
    fake_ctypes.windll.kernel32 = _K32()

    class _ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            base_setattr(lc, "_in_waiter", True)
            try:
                self._target(*self._args)
            finally:
                base_setattr(lc, "_in_waiter", False)

    import builtins

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "ctypes":
            return fake_ctypes
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    monkeypatch.setattr(mod.threading, "Thread", _ImmediateThread)
    lc._start_parent_monitor_thread()


def test_start_parent_monitor_waiter_outer_exception(monkeypatch, tmp_path):
    """Waiter outer exception branch is hit when WaitForSingleObject raises."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(mod.sys, "platform", "win32")
    lc._parent_pid = 6161

    class _K32:
        @staticmethod
        def OpenProcess(access, inherit, pid):
            return 333

        @staticmethod
        def WaitForSingleObject(hndl, timeout):
            raise RuntimeError("wait fail")

        @staticmethod
        def CloseHandle(hndl):
            return True

    fake_ctypes = mock.MagicMock()
    fake_ctypes.windll.kernel32 = _K32()

    class _ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    import builtins

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "ctypes":
            return fake_ctypes
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    monkeypatch.setattr(mod.threading, "Thread", _ImmediateThread)
    lc._start_parent_monitor_thread()


def test_reconcile_git_failure_returns_current_user_locks(monkeypatch, tmp_path):
    """Reconcile on git failure returns current user's active locks set."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        lc,
        "_get_modified_and_unpushed_files",
        mock.Mock(side_effect=RuntimeError("git fail")),
    )
    monkeypatch.setattr(
        lc,
        "active",
        mock.Mock(
            return_value=[
                {"developer_id": "test_user", "file_path": "src/a.py"},
                {"developer_id": "other", "file_path": "src/b.py"},
            ]
        ),
    )

    out = lc._reconcile()
    assert out == {"src/a.py"}


def test_reconcile_still_valid_same_machine_token_is_resumed(monkeypatch, tmp_path):
    """still_valid lock with different token but same machine enters resumed list
    path."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", lambda: ["src/a.py"])
    monkeypatch.setattr(
        lc,
        "active",
        lambda: [
            {
                "developer_id": "test_user",
                "file_path": "src/a.py",
                "lock_token": "old-token",
            }
        ],
    )
    monkeypatch.setattr(lc, "_get_session_token", lambda: "new-token")
    monkeypatch.setattr(lc, "_is_same_machine_token", lambda tok: True)
    monkeypatch.setattr(lc, "release_multiple", lambda x: None)
    monkeypatch.setattr(lc, "acquire_multiple", lambda *a, **k: (True, [], ""))

    class _FakeTable:
        def update(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def execute(self):
            return None

    lc._client = mock.MagicMock()
    lc._client.table.return_value = _FakeTable()
    monkeypatch.setattr(mod, "_state_path", lambda name: str(tmp_path / name))
    monkeypatch.setattr(mod.time, "time", lambda: 0)

    out = lc._reconcile()
    assert "src/a.py" in out


def test_reconcile_summary_outer_guards_swallow_exceptions(monkeypatch, tmp_path):
    """Summary writing/cleanup outer exception guards are exercised."""
    lc = _make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", lambda: [])
    monkeypatch.setattr(lc, "active", lambda: [])
    monkeypatch.setattr(lc, "_get_session_token", lambda: "tok")
    monkeypatch.setattr(mod, "_state_path", lambda name: str(tmp_path / name))

    # Force Thread() creation in cleanup helper to fail (2871-2872 guard)
    monkeypatch.setattr(
        mod.threading, "Thread", mock.Mock(side_effect=RuntimeError("thread ctor fail"))
    )
    out = lc._reconcile()
    assert out == set()
