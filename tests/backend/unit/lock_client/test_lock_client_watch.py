"""Watch-related tests for LockClient.watch().

Moved from the main `test_lock_client.py` for clarity.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from unittest import mock

from ._helpers import FakeResponse, load_lock_client_module, make_create_client

mod = load_lock_client_module()


def test_watch_idle_timeout(monkeypatch, tmp_path):
    """Test watch() exits on idle timeout."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    # Make _run_git_status return empty (no changes)
    monkeypatch.setattr(mod.LockClient, "_run_git_status", staticmethod(lambda: ""))

    # Make _reconcile return empty set
    monkeypatch.setattr(mod.LockClient, "_reconcile", lambda self: set())

    # Advance time to trigger timeout quickly
    time_offset = [0]
    real_now = datetime.now

    def advancing_now():
        return real_now() + timedelta(minutes=time_offset[0])

    monkeypatch.setattr(
        mod,
        "datetime",
        type(
            "FakeDT",
            (),
            {
                "now": staticmethod(advancing_now),
                "fromisoformat": datetime.fromisoformat,
            },
        )(),
    )
    monkeypatch.setattr(
        mod.time, "sleep", lambda x: time_offset.__setitem__(0, time_offset[0] + 2)
    )

    lc = mod.LockClient(developer_id="test_user")
    lc.watch(interval=1, timeout_mins=1)  # Should exit due to timeout


def test_watch_with_file_changes(monkeypatch, tmp_path):
    """Test watch() detects file changes and acquires locks."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    response = FakeResponse(status=200, data=[{"status": "ok"}])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    # First reconcile returns empty, then git status returns changes
    git_call_count = [0]

    def mock_git_status():
        git_call_count[0] += 1
        if git_call_count[0] <= 1:
            return ""
        if git_call_count[0] == 2:
            return " M src/app.py"
        return ""

    monkeypatch.setattr(
        mod.LockClient, "_run_git_status", staticmethod(mock_git_status)
    )
    monkeypatch.setattr(mod.LockClient, "_reconcile", lambda self: set())

    loop_count = [0]

    def mock_sleep(x):
        loop_count[0] += 1
        if loop_count[0] > 3:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", mock_sleep)

    lc = mod.LockClient(developer_id="test_user")
    lc.watch(interval=1, timeout_mins=60)  # Will exit via KeyboardInterrupt


def test_watch_keyboard_interrupt(monkeypatch, tmp_path):
    """Test watch() handles KeyboardInterrupt gracefully."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(mod.LockClient, "_run_git_status", staticmethod(lambda: ""))
    monkeypatch.setattr(mod.LockClient, "_reconcile", lambda self: set())
    monkeypatch.setattr(mod.time, "sleep", mock.Mock(side_effect=KeyboardInterrupt))

    lc = mod.LockClient(developer_id="test_user")
    # Should not raise
    lc.watch(
        interval=1,
        timeout_mins=60,
        daemon_mode=True,
        parent_pid=4242,
    )


def test_watch_error_in_loop(monkeypatch, tmp_path):
    """Test watch() handles errors in main loop gracefully."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(mod.LockClient, "_reconcile", lambda self: set())

    call_count = [0]

    def error_git_status():
        call_count[0] += 1
        if call_count[0] <= 2:
            raise RuntimeError("Git broken")
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        mod.LockClient, "_run_git_status", staticmethod(error_git_status)
    )
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    lc = mod.LockClient(developer_id="test_user")
    lc.watch(interval=1, timeout_mins=60)


def test_watch_only_reconciles_on_startup(monkeypatch, tmp_path):
    """Watch() performs reconciliation once at startup, not periodically."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    reconcile_calls = [0]

    def _reconcile_once(self):
        reconcile_calls[0] += 1
        return set()

    monkeypatch.setattr(mod.LockClient, "_reconcile", _reconcile_once)
    monkeypatch.setattr(mod.LockClient, "_run_git_status", staticmethod(lambda: ""))
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _p: True)
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_get_process_info_local",
        lambda self, pid: ("Code.exe", None),
    )

    tick = [0]
    real_now = datetime.now

    def fast_now():
        tick[0] += 1
        return real_now() + timedelta(minutes=tick[0] * 20)

    monkeypatch.setattr(
        mod,
        "datetime",
        type(
            "FDT",
            (),
            {
                "now": staticmethod(fast_now),
                "fromisoformat": datetime.fromisoformat,
            },
        )(),
    )

    loop_ticks = [0]

    def _sleep(_x):
        loop_ticks[0] += 1
        if loop_ticks[0] > 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)
    monkeypatch.setattr(mod.os, "getppid", lambda: 1111)

    lc = mod.LockClient(developer_id="test_user")
    lc.watch(interval=1, timeout_mins=0, daemon_mode=True, parent_pid=4242)

    assert reconcile_calls == [1]


def test_watch_parent_process_dead(monkeypatch, tmp_path):
    """Test watch() exits when parent process dies."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(mod.LockClient, "_run_git_status", staticmethod(lambda: ""))
    monkeypatch.setattr(mod.LockClient, "_reconcile", lambda self: set())

    # Make parent check trigger immediately
    check_count = [0]
    real_now = datetime.now

    def advancing_now():
        check_count[0] += 1
        return real_now() + timedelta(seconds=check_count[0] * 31)

    monkeypatch.setattr(
        mod,
        "datetime",
        type(
            "FakeDT",
            (),
            {
                "now": staticmethod(advancing_now),
                "fromisoformat": datetime.fromisoformat,
            },
        )(),
    )

    # Parent is dead
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: False)
    )
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    lc = mod.LockClient(developer_id="test_user")
    lc.watch(interval=1, timeout_mins=60)


def test_watch_open_dashboard(monkeypatch, tmp_path):
    """Test watch() opens dashboard when requested."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(mod.LockClient, "_run_git_status", staticmethod(lambda: ""))
    monkeypatch.setattr(mod.LockClient, "_reconcile", lambda self: set())
    monkeypatch.setattr(mod.time, "sleep", mock.Mock(side_effect=KeyboardInterrupt))

    dashboard_called = [False]

    def mock_dashboard(self):
        dashboard_called[0] = True

    monkeypatch.setattr(mod.LockClient, "dashboard", mock_dashboard)

    lc = mod.LockClient(developer_id="test_user")
    lc.watch(interval=1, timeout_mins=60, open_dashboard=True)
    assert dashboard_called[0]


# ---------------------------------------------------------------------------
# watch() loop: stop-request file handling
# ---------------------------------------------------------------------------


def _make_watch_client(monkeypatch, tmp_path):
    """Helper to create a client with minimal mocking for watch() tests."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(mod.LockClient, "_reconcile", lambda self: set())
    monkeypatch.setattr(mod.LockClient, "_run_git_status", staticmethod(lambda: ""))
    return mod.LockClient(developer_id="test_user")


def test_watch_stop_request_token_based(monkeypatch, tmp_path):
    """Watch() exits when TOKEN: stop request matches session token."""
    lc = _make_watch_client(monkeypatch, tmp_path)

    state_dir = str(tmp_path)
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)

    # Make session token predictable
    token = "mytoken123"
    monkeypatch.setattr(lc, "_get_session_token", lambda: token)
    monkeypatch.setattr(lc, "_read_pid", lambda: os.getpid())
    monkeypatch.setattr(lc, "_register_signal_handlers", lambda: None)
    monkeypatch.setattr(lc, "_start_parent_monitor_thread", lambda: None)
    monkeypatch.setattr(lc, "_scan_remote_locks", lambda: None)
    monkeypatch.setattr(lc, "_prepare_dashboard_server", lambda: (None, None))
    monkeypatch.setattr(lc, "_write_pid", lambda *a, **k: None)

    shutdown_called = [False]

    def mock_shutdown(*a, **k):
        shutdown_called[0] = True

    monkeypatch.setattr(lc, "_graceful_shutdown", mock_shutdown)

    # Write stop request file before watch runs
    stop_file = os.path.join(state_dir, ".stop_request")
    with open(stop_file, "w") as f:
        f.write(f"TOKEN:{token}")

    # Make time advance so parent checks run (>2s)
    call_count = [0]
    real_now = datetime.now

    def fast_now():
        call_count[0] += 1
        return real_now() + timedelta(seconds=call_count[0] * 5)

    monkeypatch.setattr(
        mod,
        "datetime",
        type(
            "FDT",
            (),
            {
                "now": staticmethod(fast_now),
                "fromisoformat": datetime.fromisoformat,
            },
        )(),
    )
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)
    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", lambda: [])

    lc._parent_pid = None
    lc._initial_ppid = os.getppid()
    lc.watch(interval=1, timeout_mins=60)

    assert shutdown_called[0]


def test_watch_stop_request_pid_based(monkeypatch, tmp_path):
    """Watch() exits when PID: stop request matches current PID."""
    lc = _make_watch_client(monkeypatch, tmp_path)

    state_dir = str(tmp_path)
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)

    monkeypatch.setattr(lc, "_get_session_token", lambda: "some_token")
    monkeypatch.setattr(lc, "_read_pid", lambda: os.getpid())
    monkeypatch.setattr(lc, "_register_signal_handlers", lambda: None)
    monkeypatch.setattr(lc, "_start_parent_monitor_thread", lambda: None)
    monkeypatch.setattr(lc, "_scan_remote_locks", lambda: None)
    monkeypatch.setattr(lc, "_prepare_dashboard_server", lambda: (None, None))
    monkeypatch.setattr(lc, "_write_pid", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", lambda: [])

    shutdown_called = [False]

    def mock_shutdown(*a, **k):
        shutdown_called[0] = True

    monkeypatch.setattr(lc, "_graceful_shutdown", mock_shutdown)

    # Write PID-based stop request
    stop_file = os.path.join(state_dir, ".stop_request")
    with open(stop_file, "w") as f:
        f.write(f"PID:{os.getpid()}")

    call_count = [0]
    real_now = datetime.now

    def fast_now():
        call_count[0] += 1
        return real_now() + timedelta(seconds=call_count[0] * 5)

    monkeypatch.setattr(
        mod,
        "datetime",
        type(
            "FDT",
            (),
            {
                "now": staticmethod(fast_now),
                "fromisoformat": datetime.fromisoformat,
            },
        )(),
    )
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    lc._parent_pid = None
    lc._initial_ppid = os.getppid()
    lc.watch(interval=1, timeout_mins=60)

    assert shutdown_called[0]


def test_watch_heartbeat_missing_after_grace(monkeypatch, tmp_path):
    """Watch() exits when heartbeat file is missing after startup grace period."""
    lc = _make_watch_client(monkeypatch, tmp_path)

    state_dir = str(tmp_path)
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)
    heartbeat_file = str(tmp_path / ".heartbeat")
    # heartbeat does NOT exist

    monkeypatch.setattr(lc, "_get_session_token", lambda: "tok")
    monkeypatch.setattr(lc, "_read_pid", lambda: os.getpid())
    monkeypatch.setattr(lc, "_register_signal_handlers", lambda: None)
    monkeypatch.setattr(lc, "_start_parent_monitor_thread", lambda: None)
    monkeypatch.setattr(lc, "_scan_remote_locks", lambda: None)
    monkeypatch.setattr(lc, "_prepare_dashboard_server", lambda: (None, None))
    monkeypatch.setattr(lc, "_write_pid", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", lambda: [])

    shutdown_called = [False]

    def mock_shutdown(*a, **k):
        shutdown_called[0] = True

    monkeypatch.setattr(lc, "_graceful_shutdown", mock_shutdown)

    # Simulate time well past grace window (startup_time >> 3s ago)
    import time as _t

    start_ts = _t.time() - 10  # 10s in the past

    call_count = [0]
    real_now = datetime.now

    def fast_now():
        call_count[0] += 1
        return real_now() + timedelta(seconds=call_count[0] * 5)

    monkeypatch.setattr(
        mod,
        "datetime",
        type(
            "FDT",
            (),
            {
                "now": staticmethod(fast_now),
                "fromisoformat": datetime.fromisoformat,
            },
        )(),
    )
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    # Override time.time to return a large value (past grace)
    def fake_time():
        return start_ts + call_count[0] * 5

    monkeypatch.setattr(mod.time, "time", fake_time)

    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    monkeypatch.setattr(lc, "_get_process_info_local", lambda pid: ("Code.exe", None))
    monkeypatch.setattr(mod.os, "getppid", lambda: 999)

    lc._initial_ppid = 999
    lc.watch(
        interval=1,
        timeout_mins=60,
        daemon_mode=True,
        parent_pid=4242,
        heartbeat_file=heartbeat_file,
        heartbeat_grace_seconds=30,
    )

    assert shutdown_called[0]


def test_watch_parent_pid_dead_shuts_down(monkeypatch, tmp_path):
    """Watch() calls _graceful_shutdown when parent_pid is set but process is dead."""
    lc = _make_watch_client(monkeypatch, tmp_path)

    state_dir = str(tmp_path)
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)

    monkeypatch.setattr(lc, "_get_session_token", lambda: "tok")
    monkeypatch.setattr(lc, "_read_pid", lambda: os.getpid())
    monkeypatch.setattr(lc, "_register_signal_handlers", lambda: None)
    monkeypatch.setattr(lc, "_start_parent_monitor_thread", lambda: None)
    monkeypatch.setattr(lc, "_scan_remote_locks", lambda: None)
    monkeypatch.setattr(lc, "_prepare_dashboard_server", lambda: (None, None))
    monkeypatch.setattr(lc, "_write_pid", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", lambda: [])

    shutdown_called = [False]

    def mock_shutdown(*a, **k):
        shutdown_called[0] = True

    monkeypatch.setattr(lc, "_graceful_shutdown", mock_shutdown)
    monkeypatch.setattr(lc, "_get_process_info_local", lambda pid: ("testide", None))

    # Parent is dead
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: False)
    )

    call_count = [0]
    real_now = datetime.now

    def fast_now():
        call_count[0] += 1
        return real_now() + timedelta(seconds=call_count[0] * 5)

    monkeypatch.setattr(
        mod,
        "datetime",
        type(
            "FDT",
            (),
            {
                "now": staticmethod(fast_now),
                "fromisoformat": datetime.fromisoformat,
            },
        )(),
    )
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    lc._initial_ppid = os.getppid()
    lc.watch(interval=1, timeout_mins=60, daemon_mode=True, parent_pid=9999)

    assert shutdown_called[0]


def _configure_watch_loop_common(monkeypatch, lc):
    """Common deterministic watch-loop wiring for deep parent/heartbeat branches."""
    monkeypatch.setattr(lc, "_get_session_token", lambda: "tok")
    monkeypatch.setattr(lc, "_read_pid", lambda: os.getpid())
    monkeypatch.setattr(lc, "_register_signal_handlers", lambda: None)
    monkeypatch.setattr(lc, "_start_parent_monitor_thread", lambda: None)
    monkeypatch.setattr(lc, "_scan_remote_locks", lambda: None)
    monkeypatch.setattr(lc, "_prepare_dashboard_server", lambda: (None, None))
    monkeypatch.setattr(lc, "_write_pid", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", lambda: [])
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    tick = [0]
    real_now = datetime.now

    def fast_now():
        tick[0] += 1
        return real_now() + timedelta(seconds=tick[0] * 5)

    monkeypatch.setattr(
        mod,
        "datetime",
        type(
            "FDT",
            (),
            {"now": staticmethod(fast_now), "fromisoformat": datetime.fromisoformat},
        )(),
    )


def test_watch_heartbeat_stale_softskip_then_shutdown(monkeypatch, tmp_path):
    """Heartbeat stale path: one soft-skip while parent alive, then shutdown."""
    lc = _make_watch_client(monkeypatch, tmp_path)
    _configure_watch_loop_common(monkeypatch, lc)

    heartbeat = tmp_path / ".heartbeat"
    heartbeat.write_text("alive")
    monkeypatch.setattr(mod.os.path, "getmtime", lambda p: 0.0)
    # Make now_ts very large so age >> grace + soft_extra
    monkeypatch.setattr(mod.time, "time", lambda: 100.0)

    # Parent alive enables one-time soft skip first, then second check should shut down.
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    monkeypatch.setattr(lc, "_get_process_info_local", lambda pid: ("parent.exe", None))
    monkeypatch.setattr(mod.os, "getppid", lambda: 12345)

    shutdown_reasons = []

    def _shutdown(reason=None):
        shutdown_reasons.append(reason)

    monkeypatch.setattr(lc, "_graceful_shutdown", _shutdown)

    lc._parent_pid = 9999
    lc._initial_ppid = 12345

    lc.watch(
        interval=1,
        timeout_mins=60,
        heartbeat_file=str(heartbeat),
        heartbeat_grace_seconds=1,
    )

    assert "heartbeat_stale" in shutdown_reasons


def test_watch_parent_zombie_name_unresolvable_shutdown(monkeypatch, tmp_path):
    """Parent alive + unknown name streak>=2 triggers zombie-shutdown branch."""
    lc = _make_watch_client(monkeypatch, tmp_path)
    _configure_watch_loop_common(monkeypatch, lc)

    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    monkeypatch.setattr(lc, "_get_process_info_local", lambda pid: (None, None))
    monkeypatch.setattr(mod.os, "getppid", lambda: 7777)

    shutdown_called = [False]
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda *a, **k: shutdown_called.__setitem__(0, True)
    )

    lc._initial_ppid = 7777
    lc.watch(interval=1, timeout_mins=60, daemon_mode=True, parent_pid=4242)

    assert shutdown_called[0]


def test_watch_adoption_detected_shutdown(monkeypatch, tmp_path):
    """If current ppid changes from initial, watch performs graceful shutdown."""
    lc = _make_watch_client(monkeypatch, tmp_path)
    _configure_watch_loop_common(monkeypatch, lc)

    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    monkeypatch.setattr(lc, "_get_process_info_local", lambda pid: ("ide.exe", None))

    # Simulate adoption by different parent PID
    ppid_values = iter([1000, 2000])
    monkeypatch.setattr(mod.os, "getppid", lambda: next(ppid_values, 2000))

    shutdown_called = [False]
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda *a, **k: shutdown_called.__setitem__(0, True)
    )

    lc._initial_ppid = 1000
    lc.watch(interval=1, timeout_mins=60, daemon_mode=True, parent_pid=4242)

    assert shutdown_called[0]


def test_watch_orphaned_windows_low_ppid_shutdown(monkeypatch, tmp_path):
    """No explicit parent on Windows and ppid<=4 is treated as orphaned."""
    lc = _make_watch_client(monkeypatch, tmp_path)
    _configure_watch_loop_common(monkeypatch, lc)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod.os, "getppid", lambda: 4)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: False)
    )

    shutdown_called = [False]
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda *a, **k: shutdown_called.__setitem__(0, True)
    )

    lc._initial_ppid = 4
    lc.watch(interval=1, timeout_mins=60, daemon_mode=True, parent_pid=None)

    assert shutdown_called[0]


def test_watch_orphaned_unix_init_ppid_shutdown(monkeypatch, tmp_path):
    """No explicit parent on Unix and ppid==1 is treated as orphaned."""
    lc = _make_watch_client(monkeypatch, tmp_path)
    _configure_watch_loop_common(monkeypatch, lc)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(mod.os, "getppid", lambda: 1)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: False)
    )

    shutdown_called = [False]
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda *a, **k: shutdown_called.__setitem__(0, True)
    )

    lc._initial_ppid = 1
    lc.watch(interval=1, timeout_mins=60, daemon_mode=True, parent_pid=None)

    assert shutdown_called[0]


def test_watch_stop_request_numeric_payload_and_remove_exception(monkeypatch, tmp_path):
    """Cover numeric-only stop payload parsing and remove-failure branch."""
    lc = _make_watch_client(monkeypatch, tmp_path)
    _configure_watch_loop_common(monkeypatch, lc)

    state_dir = str(tmp_path)
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)
    stop_file = os.path.join(state_dir, ".stop_request")
    with open(stop_file, "w", encoding="utf-8") as f:
        # Numeric-only payload covers backward-compatible parse path.
        f.write(str(os.getpid()))

    monkeypatch.setattr(lc, "_get_session_token", lambda: "tok")
    monkeypatch.setattr(lc, "_read_pid", lambda: os.getpid())
    monkeypatch.setattr(lc, "_register_signal_handlers", lambda: None)
    monkeypatch.setattr(lc, "_start_parent_monitor_thread", lambda: None)
    monkeypatch.setattr(lc, "_scan_remote_locks", lambda: None)
    monkeypatch.setattr(lc, "_prepare_dashboard_server", lambda: (None, None))
    monkeypatch.setattr(lc, "_write_pid", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", lambda: [])

    # Force os.remove(stop_file) failure to hit guarded branch.
    real_remove = os.remove

    def _remove(path):
        if str(path).endswith(".stop_request"):
            raise OSError("cannot remove stop file")
        return real_remove(path)

    monkeypatch.setattr(mod.os, "remove", _remove)

    reasons = []
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda reason=None: reasons.append(reason)
    )

    lc._parent_pid = None
    lc._initial_ppid = os.getppid()
    lc.watch(interval=1, timeout_mins=60)
    assert "stop_requested" in reasons


def test_watch_stop_request_invalid_payload_and_open_exception(monkeypatch, tmp_path):
    """Cover invalid numeric payload parse and outer stop-file exception guard."""
    lc = _make_watch_client(monkeypatch, tmp_path)
    _configure_watch_loop_common(monkeypatch, lc)

    state_dir = str(tmp_path)
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)
    stop_file = os.path.join(state_dir, ".stop_request")
    with open(stop_file, "w", encoding="utf-8") as f:
        f.write("not-a-number")

    monkeypatch.setattr(lc, "_get_session_token", lambda: "tok")
    monkeypatch.setattr(lc, "_read_pid", lambda: os.getpid())
    monkeypatch.setattr(lc, "_register_signal_handlers", lambda: None)
    monkeypatch.setattr(lc, "_start_parent_monitor_thread", lambda: None)
    monkeypatch.setattr(lc, "_scan_remote_locks", lambda: None)
    monkeypatch.setattr(lc, "_prepare_dashboard_server", lambda: (None, None))
    monkeypatch.setattr(lc, "_write_pid", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", lambda: [])

    # Raise once when reading stop request to hit outer stop-file exception guard.
    import builtins

    real_open = builtins.open
    gate = {"n": 0}

    def _open(path, mode="r", *args, **kwargs):
        if str(path).endswith(".stop_request") and "r" in mode and gate["n"] == 0:
            gate["n"] += 1
            raise OSError("cannot read stop file")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)

    # Exit loop deterministically after the guarded exception branch.
    ticks = {"n": 0}

    def _sleep(_x):
        ticks["n"] += 1
        if ticks["n"] > 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)

    lc._parent_pid = None
    lc._initial_ppid = os.getppid()
    lc.watch(interval=1, timeout_mins=60)


def test_watch_heartbeat_stale_read_exception_and_parent_name_resolution(
    monkeypatch, tmp_path
):
    """Cover heartbeat stale read-exception and parent-name transition log branches."""
    lc = _make_watch_client(monkeypatch, tmp_path)
    _configure_watch_loop_common(monkeypatch, lc)

    heartbeat = tmp_path / ".heartbeat"
    heartbeat.write_text("alive", encoding="utf-8")

    monkeypatch.setattr(lc, "_get_session_token", lambda: "tok")
    monkeypatch.setattr(lc, "_read_pid", lambda: os.getpid())
    monkeypatch.setattr(lc, "_register_signal_handlers", lambda: None)
    monkeypatch.setattr(lc, "_start_parent_monitor_thread", lambda: None)
    monkeypatch.setattr(lc, "_scan_remote_locks", lambda: None)
    monkeypatch.setattr(lc, "_prepare_dashboard_server", lambda: (None, None))
    monkeypatch.setattr(lc, "_write_pid", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", lambda: [])

    # age > grace + soft_extra to hit stale shutdown branch quickly.
    monkeypatch.setattr(mod.time, "time", lambda: 100.0)
    monkeypatch.setattr(mod.os.path, "getmtime", lambda _p: 0.0)

    # Parent alive and process-info transitions unknown->unknown->resolved.
    sequence = iter([(None, None), (None, None), ("Code.exe", "wmic")])

    def _proc_info(_pid):
        return next(sequence, ("Code.exe", "wmic"))

    monkeypatch.setattr(lc, "_get_process_info_local", _proc_info)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _p: True)
    )
    monkeypatch.setattr(mod.os, "getppid", lambda: 9001)

    # Reading heartbeat content raises to cover guarded read-failure branch.
    import builtins

    real_open = builtins.open

    def _open(path, mode="r", *args, **kwargs):
        if str(path).endswith(".heartbeat") and "r" in mode:
            raise OSError("heartbeat unreadable")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)

    reasons = []
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda reason=None: reasons.append(reason)
    )

    lc._parent_pid = 9999
    lc._initial_ppid = 9001
    lc.watch(
        interval=1,
        timeout_mins=60,
        heartbeat_file=str(heartbeat),
        heartbeat_grace_seconds=1,
    )

    assert "heartbeat_stale" in reasons
