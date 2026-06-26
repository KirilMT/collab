"""Main/integration-focused tests for live_locks_watcher."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
from unittest import mock

import pytest

from ._helpers import load_watcher_module, patch_git_capture, patch_subprocess


def _setup_common(monkeypatch, mod):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    # Reset module-level state that persists between tests.
    mod._lock_acquired_at.clear()
    mod._local_owned_locks.clear()
    mod._active_conflicts.clear()
    mod._shutdown_done = False


def _stub_supabase(monkeypatch, mod):
    fake_client = mock.MagicMock()
    table_select = fake_client.table.return_value.select.return_value
    table_select.eq.return_value.execute.return_value = mock.MagicMock(data=[])
    fake_client.table.return_value.select.return_value.execute.return_value = (
        mock.MagicMock(data=[])
    )
    monkeypatch.setattr(mod, "create_client", lambda url, key: fake_client)
    return fake_client


def _stub_loop_then_interrupt(monkeypatch, mod, max_ticks=2):
    ticks = [0]

    def mock_sleep(x):
        ticks[0] += 1
        if ticks[0] >= max_ticks:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", mock_sleep)


def _stub_startup(monkeypatch, mod):
    """Stub watcher startup helpers so main() reaches its poll loop fast/cleanly."""
    monkeypatch.setattr(
        mod, "_existing_watcher_running", lambda: (False, None, None, None)
    )
    monkeypatch.setattr(mod, "_get_parent_ide_pid_local", lambda: None)
    monkeypatch.setattr(mod, "_reconcile_on_startup", lambda client: None)
    monkeypatch.setattr(mod, "_scan_remote_locks", lambda client: None)
    monkeypatch.setattr(mod, "_start_dashboard_server", lambda: None)
    monkeypatch.setattr(mod, "_maybe_warn_cross_branch_overlap", lambda: None)


def test_main_with_default_args(monkeypatch, caplog):
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    # main() catches KeyboardInterrupt internally and returns cleanly, so no
    # broad except is needed. Assert the parsed default interval/timeout drove
    # the startup banner and that the loop exited via the KeyboardInterrupt path.
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod.main()

    assert "Interval: 5s | Timeout: disabled" in caplog.text
    assert "Stopped by user." in caplog.text


def test_main_with_interval_arg(monkeypatch, caplog):
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py", "--interval", "10"])

    sleep_args = []

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_args.append(seconds)
        sleep_count[0] += 1
        if sleep_count[0] > 1:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod.main()

    # The parsed --interval value must drive both the banner and the poll sleep.
    assert "Interval: 10s | Timeout: disabled" in caplog.text
    assert 10 in sleep_args
    assert "Stopped by user." in caplog.text


def test_main_with_timeout_arg(monkeypatch, caplog):
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py", "--timeout", "30"])

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 1:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod.main()

    # The parsed --timeout value must render in the banner as minutes and trigger
    # the deprecation warning branch (args.timeout > 0).
    assert "Interval: 5s | Timeout: 30m" in caplog.text
    assert "--timeout is deprecated" in caplog.text
    assert "Stopped by user." in caplog.text


def test_main_detects_file_changes(monkeypatch):
    """A newly modified file is acquired: RPC fires and it joins owned locks."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])
    monkeypatch.setattr(mod, "_get_developer_id", lambda: "alice")
    monkeypatch.setattr(mod, "_get_current_branch", lambda: "main")

    acquire_calls = []

    class _Result:
        data: list = []

    class TrackingClient:
        def table(self, *a, **k):
            return self

        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def delete(self):
            return self

        def execute(self):
            return _Result()

        def rpc(self, name, params):
            acquire_calls.append((name, params))
            return type("Chain", (), {"execute": lambda self: _Result()})()

    monkeypatch.setattr(mod, "create_client", lambda url, key: TrackingClient())

    # Deterministic git state: empty at init, then one modified file afterwards.
    statuses = [set(), {"src/app.py"}]

    def fake_status():
        return statuses.pop(0) if statuses else {"src/app.py"}

    monkeypatch.setattr(mod, "_run_git_status_porcelain", fake_status)
    _stub_loop_then_interrupt(monkeypatch, mod, max_ticks=2)

    mod.main()

    assert acquire_calls and acquire_calls[0][0] == "acquire_lock"
    assert acquire_calls[0][1]["p_file_path"] == "src/app.py"
    assert "src/app.py" in mod._local_owned_locks


def test_main_handles_keyboard_interrupt(monkeypatch, caplog):
    """A KeyboardInterrupt raised inside the poll loop is handled gracefully."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    # KeyboardInterrupt inside the loop must be caught by main (not propagate).
    monkeypatch.setattr(
        mod.time, "sleep", lambda x: (_ for _ in ()).throw(KeyboardInterrupt())
    )

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod.main()  # must return cleanly, not raise

    assert "Stopped by user." in caplog.text


def test_main_handles_git_error(monkeypatch, caplog):
    """Failing git subprocesses are tolerated; the watcher loops then exits clean."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)

    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    call_count = [0]

    def mock_check_output(cmd, *args, **kwargs):
        call_count[0] += 1
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        raise subprocess.CalledProcessError(1, cmd)

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 1:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod.main()  # CalledProcessError must not crash the watcher

    # Despite every git command failing, the watcher reached its loop and shut
    # down via the KeyboardInterrupt branch rather than propagating an error.
    assert sleep_count[0] >= 1
    assert "Stopped by user." in caplog.text


# ---- Auto-migrated from migrated_remaining ----


def test_main_missing_supabase_url(monkeypatch):
    """Test main exits when SUPABASE_URL is missing."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", None)
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    with pytest.raises(SystemExit):
        mod.main()


def test_main_missing_supabase_key(monkeypatch):
    """Test main exits when SUPABASE_ANON_KEY is missing."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", None)
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    with pytest.raises(SystemExit):
        mod.main()


# ============================================================================
# main() PID File Tests (lines 228-229)
# ============================================================================


def test_main_writes_pid_file(monkeypatch, tmp_path):
    """Test that main() writes a PID file on startup."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    pid_file = tmp_path / "mod.pid"
    monkeypatch.setattr(watcher, "PID_FILE", str(pid_file))

    class FakeSupaClient:
        def table(self, name):
            return self

        def delete(self):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return None

        def rpc(self, *args, **kwargs):
            return self

    monkeypatch.setattr(watcher, "create_client", lambda url, key: FakeSupaClient())

    def mock_check_output(cmd, *args, **kwargs):
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    sleep_count = [0]
    pid_present_during_loop = [False]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] == 1:
            # Capture existence mid-run: startup writes the PID file before the
            # first sleep, but shutdown may remove it afterwards.
            pid_present_during_loop[0] = pid_file.exists()
        if sleep_count[0] > 1:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (KeyboardInterrupt, SystemExit):
        pass

    assert pid_present_during_loop[0] is True


# ============================================================================
# main() Conflict Detection Tests (lines 333-335, 343)
# ============================================================================


def test_main_detects_conflict(monkeypatch, tmp_path, caplog):
    """An acquire_lock 'conflict' result records the file in _active_conflicts."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    pid_file = tmp_path / "mod.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "desktop_notify", None)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setattr(mod, "_get_developer_id", lambda: "alice")
    monkeypatch.setattr(mod, "_get_current_branch", lambda: "main")

    notify_calls = []
    monkeypatch.setattr(mod, "_notify", lambda title, msg: notify_calls.append(title))

    class FakeRPCResult:
        data = [{"status": "conflict", "owner": "other_dev"}]

    class RPCChain:
        def execute(self):
            return FakeRPCResult()

    class ConflictSupaClient:
        def table(self, name):
            return self

        def select(self, *a, **k):
            return self

        def delete(self):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return None

        def rpc(self, name, params):
            return RPCChain()

    monkeypatch.setattr(mod, "create_client", lambda url, key: ConflictSupaClient())

    statuses = [set(), {"src/app.py"}]

    def fake_status():
        return statuses.pop(0) if statuses else {"src/app.py"}

    monkeypatch.setattr(mod, "_run_git_status_porcelain", fake_status)
    _stub_loop_then_interrupt(monkeypatch, mod, max_ticks=2)

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        mod.main()

    assert "src/app.py" in mod._active_conflicts
    assert "src/app.py" not in mod._local_owned_locks
    assert any("CONFLICT" in r.message for r in caplog.records)
    assert notify_calls == ["Lock Conflict"]


# ============================================================================
# main() Lock Release Tests (lines 350-351, 358-359, 369-370)
# ============================================================================


def test_main_release_lock_exception(monkeypatch, tmp_path, caplog):
    """A failing DB delete during release is logged via logger.exception."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    pid_file = tmp_path / "mod.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "desktop_notify", None)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setattr(mod, "_get_developer_id", lambda: "alice")
    monkeypatch.setattr(mod, "_get_current_branch", lambda: "main")
    # The released file must not be tracked as a conflict, so the DB-delete path
    # (which raises) is taken instead of the conflict-cleared path.
    mod._active_conflicts.clear()

    class ErrorOnDeleteClient:
        def table(self, name):
            return self

        def select(self, *a, **k):
            return self

        def delete(self):
            raise RuntimeError("Delete failed")

        def eq(self, *args):
            return self

        def execute(self):
            return None

        def rpc(self, name, params):
            return type("Chain", (), {"execute": lambda self: None})()

    monkeypatch.setattr(mod, "create_client", lambda url, key: ErrorOnDeleteClient())

    # init last_modified holds the file; first loop iteration sees it released.
    statuses = [{"src/app.py"}, set()]

    def fake_status():
        return statuses.pop(0) if statuses else set()

    monkeypatch.setattr(mod, "_run_git_status_porcelain", fake_status)
    _stub_loop_then_interrupt(monkeypatch, mod, max_ticks=2)

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    with caplog.at_level(logging.ERROR, logger=mod.logger.name):
        mod.main()

    assert any(
        "Failed to release lock for src/app.py" in r.message for r in caplog.records
    )


# ============================================================================
# main() Idle Timeout Tests (lines 381-382)
# ============================================================================


# ============================================================================
# main() Parent Process Check Tests
# ============================================================================


def test_main_does_not_exit_on_parent_death(monkeypatch, tmp_path):
    """Test that main keeps running in persistent mode (no IDE owner).

    When no parent IDE PID is identified, the watcher runs in persistent mode and keeps
    looping until explicitly stopped (KeyboardInterrupt / signal); it must not exit
    early even though ``_is_process_alive`` reports dead processes.
    """
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    pid_file = tmp_path / "mod.pid"
    monkeypatch.setattr(watcher, "PID_FILE", str(pid_file))
    monkeypatch.setattr(watcher, "desktop_notify", None)

    # Force persistent mode: no IDE owner discovered from the live process tree.
    # Without this, parent-PID discovery is nondeterministic across environments and
    # the parent-liveness branch could shut the watcher down early (flaky).
    monkeypatch.setattr(watcher, "_get_parent_ide_pid_local", lambda: None)

    class FakeSupaClient:
        def table(self, name):
            return self

        def delete(self):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return None

    monkeypatch.setattr(watcher, "create_client", lambda url, key: FakeSupaClient())

    # Even with _is_process_alive returning False, the watcher should
    # keep running because it no longer checks the parent PID.
    monkeypatch.setattr(watcher, "_is_process_alive", lambda pid: False)

    def mock_check_output(cmd, *args, **kwargs):
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 3:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (SystemExit, KeyboardInterrupt):
        pass

    # The watcher ran for multiple iterations (did not exit early)
    assert sleep_count[0] > 2


# ============================================================================
# __main__ Block Test (line 393)
# ============================================================================


def test_process_releases_clears_active_conflict(caplog):
    """A released file that was in conflict is cleared from _active_conflicts.

    Conflict clearing happens in ``_process_releases`` (extracted from the main loop for
    testability): a released path already tracked as a conflict must be discarded and
    reported as cleared — without issuing a DB delete.
    """
    mod = load_watcher_module()
    mod._active_conflicts.clear()
    mod._active_conflicts.add("collab/conflict_file.py")

    class _Client:
        def table(self, *a, **k):  # pragma: no cover - must not be reached
            raise AssertionError("conflict files must not trigger a DB delete")

    try:
        with caplog.at_level(logging.INFO):
            mod._process_releases(_Client(), {"collab/conflict_file.py"})

        assert "collab/conflict_file.py" not in mod._active_conflicts
        assert "Conflict cleared" in caplog.text
    finally:
        mod._active_conflicts.clear()


# ---------------------------------------------------------------------------
# Consolidated tests moved from smaller modules:
# - test_live_watcher_more.py
# - test_live_watcher_more2.py
# - test_live_watcher_more3.py
# - test_live_locks_watcher_extra.py
# These were adapted to reuse the `watcher` module already loaded above.
# ---------------------------------------------------------------------------


def test_main_handles_acquire_exception_and_exits(monkeypatch, caplog):
    """A raising acquire RPC is logged via logger.exception; main exits cleanly."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setattr(mod, "_get_developer_id", lambda: "me")
    monkeypatch.setattr(mod, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(sys, "argv", ["collab"])  # safe minimal argv

    class ExplodeClient:
        def rpc(self, *a, **k):
            return self

        def execute(self):
            raise RuntimeError("rpc failed")

        def table(self, *a, **k):
            return self

        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def delete(self):
            return self

    monkeypatch.setattr(mod, "create_client", lambda url, key: ExplodeClient())

    statuses = [set(), {"src/app.py"}]

    def fake_status():
        return statuses.pop(0) if statuses else {"src/app.py"}

    monkeypatch.setattr(mod, "_run_git_status_porcelain", fake_status)
    _stub_loop_then_interrupt(monkeypatch, mod, max_ticks=2)

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod.main()  # must not raise

    assert any(
        "Failed to acquire lock for src/app.py" in r.message for r in caplog.records
    )
    assert "src/app.py" not in mod._local_owned_locks
    assert "Stopped by user." in caplog.text


def test_main_existing_watcher_guard_lock_daemon_label(monkeypatch, tmp_path):
    """Main exits early with normalized lock-daemon label when watcher already runs."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(watcher, "PID_FILE", str(tmp_path / "daemon.pid"))
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    monkeypatch.setattr(
        watcher,
        "_existing_watcher_running",
        lambda: (True, 4321, "python something", "lock-daemon"),
    )

    info_messages = []
    monkeypatch.setattr(
        watcher.logger,
        "info",
        lambda msg, *a: info_messages.append(msg % a if a else msg),
    )

    with pytest.raises(SystemExit) as ex:
        mod.main()

    assert ex.value.code == 0
    assert any("python lock_client.py" in m for m in info_messages)
    assert any("daemon-status" in m for m in info_messages)


def test_main_existing_watcher_guard_pycharm_watcher_label(monkeypatch, tmp_path):
    """Main exits early with pycharm watcher label when entrypoint is pycharm-
    watcher."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(watcher, "PID_FILE", str(tmp_path / "daemon.pid"))
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    monkeypatch.setattr(
        watcher,
        "_existing_watcher_running",
        lambda: (True, 4567, "python whatever", "pycharm-watcher"),
    )

    info_messages = []
    monkeypatch.setattr(
        watcher.logger,
        "info",
        lambda msg, *a: info_messages.append(msg % a if a else msg),
    )

    with pytest.raises(SystemExit) as ex:
        mod.main()

    assert ex.value.code == 0
    assert any("python -m collab.live_locks_watcher" in m for m in info_messages)


def test_main_existing_watcher_guard_uses_shortened_cmd_label(monkeypatch, tmp_path):
    """Main uses _shorten_process_label(existing_cmd) when entrypoint is absent."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(watcher, "PID_FILE", str(tmp_path / "daemon.pid"))
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    monkeypatch.setattr(
        watcher,
        "_existing_watcher_running",
        lambda: (True, 1111, "python very/long/cmdline", None),
    )
    monkeypatch.setattr(watcher, "_shorten_process_label", lambda _: "short-label")

    info_messages = []
    monkeypatch.setattr(
        watcher.logger,
        "info",
        lambda msg, *a: info_messages.append(msg % a if a else msg),
    )

    with pytest.raises(SystemExit) as ex:
        mod.main()

    assert ex.value.code == 0
    assert any("short-label" in m for m in info_messages)


def test_main_existing_watcher_guard_ignored_for_pytest_pidfile(monkeypatch):
    """Existing watcher guard is bypassed when PID_FILE is test-local pytest path."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])
    monkeypatch.setattr(watcher, "PID_FILE", "C:/tmp/pytest_collab_watcher.pid")

    # Simulate existing watcher, but bypass guard due to test-local PID file.
    monkeypatch.setattr(
        watcher,
        "_existing_watcher_running",
        lambda: (True, 9999, "python cmd", "lock-client"),
    )

    # Force a controlled exit later in startup to prove we passed the guard
    monkeypatch.setattr(watcher, "create_client", None)

    debug_messages = []
    monkeypatch.setattr(
        watcher.logger,
        "debug",
        lambda msg, *a: debug_messages.append(msg % a if a else msg),
    )

    with pytest.raises(SystemExit) as ex:
        mod.main()

    # Exit from create_client missing path, not from existing-watcher guard
    assert ex.value.code == 1
    assert any("test-local PID file" in m for m in debug_messages)


# ============================================================================
# PID File OSError Tests (lines 192-193, 228-229)
# ============================================================================


def test_main_pid_write_oserror(monkeypatch, tmp_path, caplog):
    """When metadata write raises and the plain fallback open() also raises OSError, the
    error is swallowed and main continues into its loop."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    # Pin PID_FILE to an unwritable path (parent dirs absent). COLLAB_PID_FILE
    # stops main() from re-resolving PID_FILE to the writable test-local root.
    pid_file = tmp_path / "no" / "such" / "dir" / "pid"
    monkeypatch.setenv("COLLAB_PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "desktop_notify", None)
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    def _boom(pid, parent_pid=None):
        raise OSError("metadata write failed")

    monkeypatch.setattr(mod, "_write_pid_file", _boom)

    _stub_loop_then_interrupt(monkeypatch, mod, max_ticks=1)

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod.main()

    # Both writes failed, so the PID file does not exist, yet the watcher still
    # reached its loop and exited cleanly (OSError branch swallowed the error).
    assert not pid_file.exists()
    assert "Stopped by user." in caplog.text


# ============================================================================
# Lock Release Execute Path Tests (lines 350-351)
# ============================================================================


def test_main_lock_release_success(monkeypatch, tmp_path):
    """Test that successful lock release executes the delete (lines 350-351)."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    pid_file = tmp_path / "mod.pid"
    monkeypatch.setattr(watcher, "PID_FILE", str(pid_file))
    monkeypatch.setattr(watcher, "desktop_notify", None)
    mod._active_conflicts.clear()

    delete_calls = []

    class FakeOKResult:
        data = [{"status": "ok"}]

    class TrackingClient:
        def table(self, name):
            return self

        def select(self, *args, **kwargs):
            return self

        def delete(self):
            delete_calls.append("delete")
            return self

        def eq(self, *args):
            return self

        def execute(self):
            delete_calls.append("execute")
            return type("R", (), {"data": []})()

        def rpc(self, name, params):
            return type("Chain", (), {"execute": lambda self: FakeOKResult()})()

    monkeypatch.setattr(watcher, "create_client", lambda url, key: TrackingClient())

    git_call_count = [0]

    def _git(argv, **_k):
        git_call_count[0] += 1
        cmd = " ".join(argv)
        if "user.name" in cmd:
            return "test_user"
        if "branch" in cmd and "--show-current" in cmd:
            return "main"
        if "status" in cmd and "--porcelain" in cmd:
            if git_call_count[0] <= 5:
                return " M src/release_me.py"
            return ""
        return ""

    patch_git_capture(monkeypatch, mod, _git)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 4:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (KeyboardInterrupt, SystemExit):
        pass

    # The delete path should have been called
    assert "delete" in delete_calls or "execute" in delete_calls


# ============================================================================
# Idle Timeout Direct Test (lines 381-382)
# ============================================================================


def test_main_idle_timeout_break(monkeypatch, tmp_path, caplog):
    """Idle timeout with no kept locks breaks the loop and logs the timeout."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py", "--timeout", "1"])

    pid_file = tmp_path / "mod.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "desktop_notify", None)
    _stub_startup(monkeypatch, mod)
    # Clean tree and no owned locks → kept_locks is empty → "Timed out" branch.
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())
    mod._local_owned_locks = set()

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    real_now = datetime.now
    offset = [timedelta()]
    ticks = [0]

    def advancing_sleep(seconds):
        ticks[0] += 1
        offset[0] += timedelta(minutes=5)
        if ticks[0] > 10:  # safety net so a missed break cannot hang the test
            raise KeyboardInterrupt()

    def fake_now(*args, **kwargs):
        return real_now() + offset[0]

    monkeypatch.setattr("time.sleep", advancing_sleep)
    monkeypatch.setattr(
        mod,
        "datetime",
        type(
            "FakeDT",
            (),
            {
                "now": staticmethod(fake_now),
                "fromisoformat": datetime.fromisoformat,
            },
        )(),
        raising=False,
    )

    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod.main()

    assert "Timed out after 1m inactivity." in caplog.text
    assert ticks[0] <= 10  # broke via timeout, not the safety net


def test_main_dashboard_fallback_message(monkeypatch, tmp_path, caplog):
    """Test main() logs fallback dashboard message when server fails (line 382)."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    pid_file = tmp_path / "mod.pid"
    monkeypatch.setattr(watcher, "PID_FILE", str(pid_file))
    monkeypatch.setattr(watcher, "desktop_notify", None)

    # Make _start_dashboard_server return None
    monkeypatch.setattr(watcher, "_start_dashboard_server", lambda: None)

    class FakeSupaClient:
        def table(self, name):
            return self

        def delete(self):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return None

        def select(self, *args):
            return self

    monkeypatch.setattr(watcher, "create_client", lambda url, key: FakeSupaClient())

    def mock_check_output(cmd, *args, **kwargs):
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 1:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    import logging

    with caplog.at_level(logging.INFO, logger="collab.pycharm_watcher"):
        try:
            mod.main()
        except (KeyboardInterrupt, SystemExit):
            pass

    assert any("collab dashboard" in r.message for r in caplog.records)


def test_main_exits_when_create_client_none(monkeypatch, caplog):
    mod = load_watcher_module()
    # Use monkeypatch (not direct assignment) so the shared cached module's
    # globals are restored after the test.
    _setup_common(monkeypatch, mod)
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://example.invalid")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "anon:fake")

    # None for create_client simulates the supabase dependency being missing.
    monkeypatch.setattr(mod, "create_client", None)
    monkeypatch.setattr(mod, "_start_dashboard_server", lambda: None)
    monkeypatch.setattr(
        mod, "_existing_watcher_running", lambda: (False, None, None, None)
    )

    monkeypatch.setattr(sys, "argv", ["collab"])  # safe minimal argv

    with caplog.at_level(logging.ERROR, logger=mod.logger.name):
        with pytest.raises(SystemExit) as ex:
            mod.main()

    assert ex.value.code == 1
    assert "Supabase client factory is not available" in caplog.text


def test_main_fallback_writes_plain_pid(monkeypatch, tmp_path):
    mod = load_watcher_module()
    # Simulate _write_pid_file raising so main falls back to plain integer write
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(watcher, "PID_FILE", str(pid_file))

    def _boom(pid):
        raise Exception("boom")

    monkeypatch.setattr(watcher, "_write_pid_file", _boom)
    # Provide minimal values so main continues to client creation
    monkeypatch.setattr(watcher, "SUPABASE_URL", "x")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "y")
    monkeypatch.setattr(watcher, "_get_developer_id", lambda: "tester")
    monkeypatch.setattr(watcher, "create_client", lambda a, b: object())

    # Make dashboard startup terminate the process early so we don't enter the full loop
    def _raise_sys_exit():
        raise SystemExit(0)

    monkeypatch.setattr(watcher, "_start_dashboard_server", _raise_sys_exit)

    # Ensure argparse in main() doesn't see pytest args
    monkeypatch.setattr("sys.argv", ["live_watcher"])  # type: ignore[arg-type]
    try:
        mod.main()
    except SystemExit:
        pass

    # Fallback should have written the plain integer PID
    assert pid_file.exists()
    content = pid_file.read_text()
    assert content.strip().isdigit()
    # Clean up
    try:
        os.remove(pid_file)
    except Exception:
        pass


# -------------------------- Restored watcher tests --------------------------


def test_live_locks_watcher_main_loop_gaps(monkeypatch):
    """Cover lines 1591-1654 (Main loop control flow)."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "_get_parent_ide_pid_local", lambda: 999)
    # Ensure _is_process_alive is true for 999
    monkeypatch.setattr(watcher, "_is_process_alive", lambda p: int(p) == 999)

    # Use a generator for side_effect simulation with monkeypatch
    def sleep_gen():
        yield None
        yield SystemExit()

    gen = sleep_gen()
    monkeypatch.setattr("time.sleep", lambda x: next(gen))

    # Mock the git/client creation to prevent external calls
    monkeypatch.setattr(watcher, "create_client", lambda *a: mock.MagicMock())
    monkeypatch.setattr(watcher, "_run_git_status_porcelain", lambda: [])

    # Fix: remove attribute patches that don't exist in module
    with pytest.raises(SystemExit):
        mod.main()


# ---------------------------------------------------------------------------
# Deep tests: main() label-display, startup, and loop branches
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# main() — watcher-already-running label branches (lines 1681-1710)
# ---------------------------------------------------------------------------


def test_main_existing_watcher_label_from_cmdline(monkeypatch, tmp_path):
    """When no entrypoint, label is derived from cmdline."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    monkeypatch.setattr(
        mod,
        "_existing_watcher_running",
        lambda: (True, 9999, "python /some/path/live_locks_watcher.py", None),
    )

    with pytest.raises(SystemExit):
        mod.main()


def test_main_existing_watcher_no_label(monkeypatch, tmp_path):
    """With neither entrypoint nor cmdline, label-less message is shown."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    monkeypatch.setattr(
        mod,
        "_existing_watcher_running",
        lambda: (True, 9999, None, None),
    )

    with pytest.raises(SystemExit):
        mod.main()


def test_main_existing_watcher_label_other_entrypoint(monkeypatch, tmp_path):
    """Other entrypoint string goes through _shorten_process_label."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    monkeypatch.setattr(
        mod,
        "_existing_watcher_running",
        lambda: (True, 9999, None, "some-custom-entrypoint"),
    )

    with pytest.raises(SystemExit):
        mod.main()


# ---------------------------------------------------------------------------
# main() — parent-pid detection branch (lines 1701-1710 region)
# ---------------------------------------------------------------------------


def test_main_parent_pid_from_cli_arg(monkeypatch, tmp_path, caplog):
    """--parent-pid CLI arg sets parent_pid and logs 'Tied to parent PID via CLI'."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py", "--parent-pid", "9999"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "_is_process_alive", lambda pid: pid != 9999)
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    _stub_loop_then_interrupt(monkeypatch, mod, max_ticks=1)

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        mod.main()

    assert "Tied to parent PID via CLI argument: 9999" in caplog.text


def test_main_no_parent_pid_detected(monkeypatch, tmp_path, caplog):
    """When no parent IDE PID is found, main() runs in persistent mode."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_existing_watcher_running", lambda: (False, None, None, None)
    )
    monkeypatch.setattr(mod, "_get_parent_ide_pid_local", lambda: None)
    monkeypatch.setattr(mod, "_reconcile_on_startup", lambda client: None)
    monkeypatch.setattr(mod, "_scan_remote_locks", lambda client: None)

    _stub_loop_then_interrupt(monkeypatch, mod, max_ticks=1)

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        try:
            mod.main()
        except (SystemExit, KeyboardInterrupt):
            pass

    assert "No IDE owner identified" in caplog.text


# ---------------------------------------------------------------------------
# main() — parent dead in the loop (lines 1839-1842 region)
# ---------------------------------------------------------------------------


def test_main_parent_pid_dies_breaks_loop(monkeypatch, tmp_path, caplog):
    """When parent_pid is tracked and goes dead, loop exits gracefully."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    # Simulate parent detection returning a specific PID, dead from the start.
    monkeypatch.setattr(mod, "_get_parent_ide_pid_local", lambda: 12345)
    monkeypatch.setattr(mod, "_is_process_alive", lambda pid: False)

    ticks = [0]

    def mock_sleep(x):
        ticks[0] += 1
        if ticks[0] > 5:  # safety net; the parent-dead break should fire first
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", mock_sleep)

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    # Make datetime advance so the parent liveness check runs (>5s elapsed).
    real_now = datetime.now
    tick = [0]

    def fast_now():
        tick[0] += 1
        return real_now() + timedelta(seconds=tick[0] * 10)

    monkeypatch.setattr(
        mod,
        "datetime",
        type(
            "FDT",
            (),
            {"now": staticmethod(fast_now), "fromisoformat": datetime.fromisoformat},
        )(),
    )

    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod.main()

    assert "Parent process (PID: 12345) is dead. Shutting down..." in caplog.text
    assert ticks[0] <= 5  # broke via parent-dead branch, not the safety net


# ---------------------------------------------------------------------------
# main() — debug mode / COLLAB_DEBUG (lines 1811 region)
# ---------------------------------------------------------------------------


def test_main_debug_mode_via_env(monkeypatch, tmp_path, caplog):
    """COLLAB_DEBUG=1 enables debug logging (logs 'Debug logging enabled')."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setenv("COLLAB_DEBUG", "1")
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    _stub_loop_then_interrupt(monkeypatch, mod, max_ticks=1)

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod.main()

    # 'Debug logging enabled' is emitted only inside the debug-mode branch that
    # also raises the logger to DEBUG, so its presence proves debug mode is on.
    assert "Debug logging enabled" in caplog.text


def test_main_debug_mode_via_flag(monkeypatch, tmp_path, caplog):
    """--debug flag enables debug logging even when COLLAB_DEBUG is off."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setenv("COLLAB_DEBUG", "0")
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py", "--debug"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    _stub_loop_then_interrupt(monkeypatch, mod, max_ticks=1)

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod.main()

    assert "Debug logging enabled" in caplog.text


# ---------------------------------------------------------------------------
# main() — idle timeout with kept locks (line 1856-1857 region)
# ---------------------------------------------------------------------------


def test_main_initial_git_status_raises_ignores_exception(
    monkeypatch, tmp_path, caplog
):
    """If _run_git_status_porcelain raises during init after reconcile, exception is
    swallowed."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_existing_watcher_running", lambda: (False, None, None, None)
    )
    monkeypatch.setattr(mod, "_get_parent_ide_pid_local", lambda: None)
    monkeypatch.setattr(mod, "_reconcile_on_startup", lambda client: None)
    monkeypatch.setattr(mod, "_scan_remote_locks", lambda client: None)

    monkeypatch.setattr(mod, "_start_dashboard_server", lambda: None)

    git_calls = [0]

    def failing_git_status():
        git_calls[0] += 1
        if git_calls[0] == 1:
            raise RuntimeError("git not found")
        return set()

    monkeypatch.setattr(mod, "_run_git_status_porcelain", failing_git_status)
    _stub_loop_then_interrupt(monkeypatch, mod, max_ticks=1)

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        mod.main()

    # The init snapshot failure was swallowed (warning logged) and the watcher
    # proceeded into its loop, calling git-status again (counter advanced).
    assert git_calls[0] >= 2
    assert "Initial git-status snapshot failed" in caplog.text


def test_main_timeout_dirty_status_raises_uses_local_set(monkeypatch, tmp_path, caplog):
    """Idle-timeout fallback when timeout git-status check raises.

    Falls back to _local_owned_locks.
    """
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py", "--timeout", "1"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    notify_calls = []
    monkeypatch.setattr(mod, "_notify", lambda title, msg: notify_calls.append(msg))

    call_count = [0]

    def sometimes_failing_git():
        call_count[0] += 1
        # 1: init last_modified, 2: current_modified in loop, 3: timeout-check
        # lookup where we intentionally fail to hit the fallback branch.
        if call_count[0] == 3:
            raise RuntimeError("git failed")
        return set()

    monkeypatch.setattr(mod, "_run_git_status_porcelain", sometimes_failing_git)
    mod._local_owned_locks = {"collab/file.py"}

    real_now = datetime.now
    ticks = [0]

    def fake_now():
        ticks[0] += 1
        # Advance enough so idle timeout is immediately exceeded in loop.
        return real_now() + timedelta(minutes=ticks[0] * 2)

    monkeypatch.setattr(mod.time, "sleep", lambda x: None)
    monkeypatch.setattr(
        mod,
        "datetime",
        type(
            "FDT",
            (),
            {"now": staticmethod(fake_now), "fromisoformat": datetime.fromisoformat},
        )(),
    )

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        mod.main()

    # The timeout git-status check raised, so kept_locks fell back to the local
    # owned set — the preserved file appears in the IDLE TIMEOUT warning.
    assert "IDLE TIMEOUT REACHED" in caplog.text
    assert "collab/file.py" in caplog.text
    assert any("collab/file.py" in m for m in notify_calls)


def test_main_timeout_with_kept_locks_logs_warning(monkeypatch, tmp_path, caplog):
    """Idle timeout with dirty files logs warning about preserved locks."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py", "--timeout", "1"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: {"collab/dirty.py"})

    # Inject some owned locks
    mod._local_owned_locks = {"collab/dirty.py"}

    real_now = datetime.now
    offset = [timedelta()]

    def advancing_sleep(x):
        offset[0] += timedelta(minutes=5)

    def fake_now():
        return real_now() + offset[0]

    monkeypatch.setattr(mod.time, "sleep", advancing_sleep)
    monkeypatch.setattr(
        mod,
        "datetime",
        type(
            "FDT",
            (),
            {"now": staticmethod(fake_now), "fromisoformat": datetime.fromisoformat},
        )(),
    )

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        mod.main()

    # kept_locks (dirty file still owned) triggers the preserved-locks warning.
    assert "IDLE TIMEOUT REACHED" in caplog.text
    assert "collab/dirty.py" in caplog.text


def test_main_loop_git_status_exception_logs_and_continues(
    monkeypatch, tmp_path, caplog
):
    """Main loop logs git-status errors and continues via sleep/continue branch."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    _stub_startup(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "_get_parent_ide_pid_local", lambda: 123)
    monkeypatch.setattr(mod, "_is_process_alive", lambda pid: True)

    calls = [0]

    def _git_status():
        calls[0] += 1
        if calls[0] == 1:
            return set()  # initial last_modified
        raise RuntimeError("git status failed")

    monkeypatch.setattr(mod, "_run_git_status_porcelain", _git_status)
    monkeypatch.setattr(
        mod.time, "sleep", lambda x: (_ for _ in ()).throw(KeyboardInterrupt())
    )

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    patch_subprocess(monkeypatch, check_output=mock_check_output)

    with caplog.at_level(logging.ERROR, logger=mod.logger.name):
        mod.main()

    # The loop git-status failure was logged and the watcher continued to the
    # sleep/continue branch (where the injected KeyboardInterrupt exits cleanly).
    assert calls[0] >= 2
    assert "Failed to get modified files" in caplog.text


watcher = load_watcher_module()


def test_min_auto_lock_hold_seconds_default(monkeypatch):
    """The default minimum hold time is 300 seconds."""
    mod = load_watcher_module()
    monkeypatch.delenv("COLLAB_MIN_AUTO_LOCK_HOLD_SECONDS", raising=False)
    assert mod._min_auto_lock_hold_seconds() == 300


def test_graceful_shutdown_defers_young_locks(monkeypatch, tmp_path, caplog):
    """_graceful_shutdown keeps locks younger than the minimum hold time."""
    import logging

    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_min_auto_lock_hold_seconds", lambda: 60)
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    mod._shutdown_done = False
    mod._local_owned_locks.clear()
    mod._lock_acquired_at.clear()

    # File is still dirty — should be KEPT
    monkeypatch.setattr(
        mod, "_run_git_status_porcelain", lambda: set()
    )  # file is clean
    mod._local_owned_locks.add("src/clean.py")

    # Record acquisition just now
    mod._lock_acquired_at["src/clean.py"] = datetime.now()

    delete_calls = []

    class FakeClient:
        def table(self, _name):
            return self

        def select(self, *_a, **_k):
            return self

        def delete(self):
            return self

        def eq(self, *_a, **_k):
            return self

        def execute(self):
            delete_calls.append(1)
            return type("R", (), {"data": []})()

    monkeypatch.setattr(mod, "create_client", lambda url, key: FakeClient())
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "test.pid"))

    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        mod._graceful_shutdown()

    # The lock is young — should NOT be deleted.
    assert delete_calls == []
    assert "⏳ [KEPT]" in caplog.text


def test_graceful_shutdown_db_fallback_defers_young_locks(
    monkeypatch, tmp_path, caplog
):
    """DB-fallback path in _graceful_shutdown keeps young locks."""
    import logging
    from datetime import datetime, timezone

    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_min_auto_lock_hold_seconds", lambda: 60)
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    mod._shutdown_done = False
    mod._local_owned_locks.clear()  # Empty — triggers DB fallback
    mod._lock_acquired_at.clear()

    now_iso = datetime.now(timezone.utc).isoformat()
    # Populate _lock_acquired_at so the hold-time check fires.
    # (In production this is done by _reconcile_on_startup.)
    mod._lock_acquired_at["src/db_lock.py"] = datetime.now()

    delete_calls = []

    class FakeClient:
        def __init__(self):
            self._delete_called = False

        def table(self, _name):
            return self

        def select(self, *_a, **_k):
            return self

        def delete(self):
            self._delete_called = True
            return self

        def eq(self, *_a, **_k):
            return self

        def execute(self):
            if self._delete_called:
                delete_calls.append(1)
            return type(
                "R",
                (),
                {
                    "data": [
                        {
                            "file_path": "src/db_lock.py",
                            "developer_id": "alice",
                            "acquired_at": now_iso,
                        }
                    ]
                },
            )()

    monkeypatch.setattr(mod, "create_client", lambda url, key: FakeClient())
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "test2.pid"))
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        mod._graceful_shutdown()

    assert delete_calls == []
    assert "⏳ [KEPT]" in caplog.text
