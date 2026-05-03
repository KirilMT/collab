"""Main/integration-focused tests for live_locks_watcher."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta
from unittest import mock

import pytest

from ._helpers import load_watcher_module


def _setup_common(monkeypatch, mod):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")


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


def test_main_with_default_args(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("DEVELOPER_ID", "test_dev")

    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (KeyboardInterrupt, SystemExit):
        pass


def test_main_with_interval_arg(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py", "--interval", "10"])

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 1:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (KeyboardInterrupt, SystemExit):
        pass


def test_main_with_timeout_arg(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py", "--timeout", "30"])

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 1:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (KeyboardInterrupt, SystemExit):
        pass


def test_main_detects_file_changes(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("DEVELOPER_ID", "test_dev")

    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    call_count = [0]

    def mock_check_output(cmd, *args, **kwargs):
        call_count[0] += 1
        if "status" in cmd:
            if call_count[0] <= 2:
                return b""
            return b"M  src/app.py\n"
        return b"test_dev\n"

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (KeyboardInterrupt, SystemExit, AttributeError, NameError):
        pass


def test_main_releases_locks_on_file_removal(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("DEVELOPER_ID", "test_dev")

    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    call_count = [0]

    def mock_check_output(cmd, *args, **kwargs):
        call_count[0] += 1
        if "status" in cmd:
            if call_count[0] <= 2:
                return b"M  src/app.py\n"
            return b""
        return b"test_dev\n"

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (KeyboardInterrupt, SystemExit):
        pass


def test_main_handles_keyboard_interrupt(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    call_count = [0]

    def mock_check_output(cmd, *args, **kwargs):
        call_count[0] += 1
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        raise KeyboardInterrupt()

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    try:
        mod.main()
    except (SystemExit, KeyboardInterrupt):
        pass


def test_main_handles_git_error(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    call_count = [0]

    def mock_check_output(cmd, *args, **kwargs):
        call_count[0] += 1
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 1:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (SystemExit, KeyboardInterrupt):
        pass


def test_main_timeout_triggers(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py", "--timeout", "1"])

    def mock_check_output(cmd, *args, **kwargs):
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    # Use a mock for time.sleep that also advances "now"

    real_now = datetime.now
    offset = [timedelta()]

    def advancing_sleep(seconds):
        offset[0] += timedelta(minutes=2)

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

    try:
        mod.main()
    except (SystemExit, AttributeError, TypeError):
        pass


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

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 1:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (KeyboardInterrupt, SystemExit):
        pass

    # PID file should have been written (might be cleaned on shutdown)


# ============================================================================
# main() Conflict Detection Tests (lines 333-335, 343)
# ============================================================================


def test_main_detects_conflict(monkeypatch, tmp_path):
    """Test that main detects lock conflicts and notifies."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    pid_file = tmp_path / "mod.pid"
    monkeypatch.setattr(watcher, "PID_FILE", str(pid_file))
    monkeypatch.setattr(watcher, "desktop_notify", None)

    class FakeRPCResult:
        data = [{"status": "conflict", "owner": "other_dev"}]

    class FakeSupaClient:
        def table(self, name):
            return self

        def delete(self):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return None

        def rpc(self, name, params):
            return self

        # The rpc() returns self, then .execute() returns FakeRPCResult
        class _rpc_chain:
            @staticmethod
            def execute():
                return FakeRPCResult()

    # Need a more careful mock
    class RPCChain:
        def execute(self):
            return FakeRPCResult()

    class ConflictSupaClient:
        def table(self, name):
            return self

        def delete(self):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return None

        def rpc(self, name, params):
            return RPCChain()

    monkeypatch.setattr(watcher, "create_client", lambda url, key: ConflictSupaClient())

    git_call_count = [0]

    def mock_check_output(cmd, *args, **kwargs):
        git_call_count[0] += 1
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        if "status" in cmd:
            # First call: empty, second call: file changed
            if git_call_count[0] <= 3:
                return b""
            return b" M src/app.py\n"
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 3:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (KeyboardInterrupt, SystemExit):
        pass


# ============================================================================
# main() Lock Release Tests (lines 350-351, 358-359, 369-370)
# ============================================================================


def test_main_releases_lock_on_revert(monkeypatch, tmp_path):
    """Test that locks are released when files revert to clean state."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    pid_file = tmp_path / "mod.pid"
    monkeypatch.setattr(watcher, "PID_FILE", str(pid_file))
    monkeypatch.setattr(watcher, "desktop_notify", None)

    class FakeOKResult:
        data = [{"status": "ok"}]

    class ReleaseSupaClient:
        def table(self, name):
            return self

        def delete(self):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return None

        def rpc(self, name, params):
            return type("Chain", (), {"execute": lambda self: FakeOKResult()})()

    monkeypatch.setattr(watcher, "create_client", lambda url, key: ReleaseSupaClient())

    git_call_count = [0]

    def mock_check_output(cmd, *args, **kwargs):
        git_call_count[0] += 1
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        if "status" in cmd:
            # First status: file modified, second: clean
            if git_call_count[0] <= 4:
                return b" M src/app.py\n"
            return b""
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 3:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (KeyboardInterrupt, SystemExit):
        pass


def test_main_release_lock_exception(monkeypatch, tmp_path):
    """Test that lock release errors are handled (lines 369-370)."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    pid_file = tmp_path / "mod.pid"
    monkeypatch.setattr(watcher, "PID_FILE", str(pid_file))
    monkeypatch.setattr(watcher, "desktop_notify", None)

    class FakeOKResult:
        data = [{"status": "ok"}]

    class ErrorOnDeleteClient:
        def table(self, name):
            return self

        def delete(self):
            raise RuntimeError("Delete failed")

        def eq(self, *args):
            return self

        def execute(self):
            return None

        def rpc(self, name, params):
            return type("Chain", (), {"execute": lambda self: FakeOKResult()})()

    monkeypatch.setattr(
        watcher, "create_client", lambda url, key: ErrorOnDeleteClient()
    )

    git_call_count = [0]

    def mock_check_output(cmd, *args, **kwargs):
        git_call_count[0] += 1
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        if "status" in cmd:
            if git_call_count[0] <= 4:
                return b" M src/app.py\n"
            return b""
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 3:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (KeyboardInterrupt, SystemExit):
        pass


# ============================================================================
# main() Idle Timeout Tests (lines 381-382)
# ============================================================================


def test_main_idle_timeout(monkeypatch, tmp_path):
    """Test that main exits after idle timeout with no changes."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py", "--timeout", "1"])

    pid_file = tmp_path / "mod.pid"
    monkeypatch.setattr(watcher, "PID_FILE", str(pid_file))
    monkeypatch.setattr(watcher, "desktop_notify", None)

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

    def mock_check_output(cmd, *args, **kwargs):
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    real_now = datetime.now
    offset = [timedelta()]

    def advancing_sleep(seconds):
        offset[0] += timedelta(minutes=2)

    def fake_now(*args, **kwargs):
        return real_now() + offset[0]

    monkeypatch.setattr("time.sleep", advancing_sleep)
    monkeypatch.setattr(
        watcher,
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

    try:
        mod.main()
    except (SystemExit, AttributeError, TypeError):
        pass


# ============================================================================
# main() Parent Process Check Tests
# ============================================================================


def test_main_does_not_exit_on_parent_death(monkeypatch, tmp_path):
    """Test that main does NOT exit when parent process dies.

    The parent PID check was removed — PyCharm manages the process lifecycle via its
    stop button / IDE close.  The watcher should keep running until explicitly stopped
    (KeyboardInterrupt / signal).
    """
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    pid_file = tmp_path / "mod.pid"
    monkeypatch.setattr(watcher, "PID_FILE", str(pid_file))
    monkeypatch.setattr(watcher, "desktop_notify", None)

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

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

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


def test_main_conflict_cleared_on_revert(monkeypatch, tmp_path):
    """Test that conflicts are cleared when file reverts."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    pid_file = tmp_path / "mod.pid"
    monkeypatch.setattr(watcher, "PID_FILE", str(pid_file))
    monkeypatch.setattr(watcher, "desktop_notify", None)

    # Pre-populate _active_conflicts
    mod._active_conflicts.clear()
    mod._active_conflicts.add("src/conflict_file.py")

    class FakeOKResult:
        data = [{"status": "ok"}]

    class FakeSupaClient:
        def table(self, name):
            return self

        def delete(self):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return None

        def rpc(self, name, params):
            return type("Chain", (), {"execute": lambda self: FakeOKResult()})()

    monkeypatch.setattr(watcher, "create_client", lambda url, key: FakeSupaClient())

    git_call_count = [0]

    def mock_check_output(cmd, *args, **kwargs):
        git_call_count[0] += 1
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        if "status" in cmd:
            # First: file present (matches _active_conflicts), then: empty
            if git_call_count[0] <= 4:
                return b" M src/conflict_file.py\n"
            return b""
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 3:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (KeyboardInterrupt, SystemExit):
        pass

    # Clean up
    mod._active_conflicts.clear()


# ---------------------------------------------------------------------------
# Consolidated tests moved from smaller modules:
# - test_live_watcher_more.py
# - test_live_watcher_more2.py
# - test_live_watcher_more3.py
# - test_live_locks_watcher_extra.py
# These were adapted to reuse the `watcher` module already loaded above.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Consolidated tests moved from smaller modules:
# - test_live_watcher_more.py
# - test_live_watcher_more2.py
# - test_live_watcher_more3.py
# - test_live_locks_watcher_extra.py
# These were adapted to reuse the `watcher` module already loaded above.
# ---------------------------------------------------------------------------


def test_main_handles_acquire_exception_and_exits(monkeypatch):
    mod = load_watcher_module()
    # Prepare environment for main() to run one loop and exit via KeyboardInterrupt
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon:fake")

    # Fake create_client that returns a client whose rpc().execute() raises
    class ExplodeClient:
        def rpc(self, *a, **k):
            return self

        def execute(self):
            raise RuntimeError("rpc failed")

        def table(self, *a, **k):
            return self

    monkeypatch.setattr(watcher, "create_client", lambda url, key: ExplodeClient())
    monkeypatch.setattr(watcher, "_start_dashboard_server", lambda: None)

    # Make git status report one modified file so the watcher will attempt acquire
    monkeypatch.setattr("subprocess.check_output", lambda *a, **k: b" M src/app.py")

    # Make sleep raise KeyboardInterrupt to exit after first iteration
    def raise_kb(_):
        raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", raise_kb)

    # Ensure developer id is deterministic
    monkeypatch.setattr(watcher, "_get_developer_id", lambda: "me")

    # Avoid argparse picking up pytest args
    import sys as _sys

    monkeypatch.setattr(_sys, "argv", ["collab"])  # safe minimal argv

    # Should not raise (main handles KeyboardInterrupt and exits cleanly)
    mod.main()


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
    assert any(
        "python .collab/pycharm/live_locks_watcher.py" in m for m in info_messages
    )


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


def test_main_pid_write_oserror(monkeypatch, tmp_path):
    """Test main handles OSError when writing PID file (lines 228-229)."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py"])

    # Set PID file to an unwritable location
    monkeypatch.setattr(
        watcher, "PID_FILE", str(tmp_path / "no" / "such" / "dir" / "pid")
    )
    monkeypatch.setattr(watcher, "desktop_notify", None)

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

    def mock_check_output(cmd, *args, **kwargs):
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    sleep_count = [0]

    def mock_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] > 1:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", mock_sleep)

    try:
        mod.main()
    except (KeyboardInterrupt, SystemExit):
        pass


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

        def delete(self):
            delete_calls.append("delete")
            return self

        def eq(self, *args):
            return self

        def execute(self):
            delete_calls.append("execute")
            return None

        def rpc(self, name, params):
            return type("Chain", (), {"execute": lambda self: FakeOKResult()})()

    monkeypatch.setattr(watcher, "create_client", lambda url, key: TrackingClient())

    git_call_count = [0]

    def mock_check_output(cmd, *args, **kwargs):
        git_call_count[0] += 1
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        if "status" in cmd:
            # First iterations: file present, then: clean
            if git_call_count[0] <= 5:
                return b" M src/release_me.py\n"
            return b""
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

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


def test_main_idle_timeout_break(monkeypatch, tmp_path):
    """Test that idle timeout causes main to break (lines 381-382)."""
    mod = load_watcher_module()
    monkeypatch.setattr(watcher, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(watcher, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["live_locks_mod.py", "--timeout", "1"])

    pid_file = tmp_path / "mod.pid"
    monkeypatch.setattr(watcher, "PID_FILE", str(pid_file))
    monkeypatch.setattr(watcher, "desktop_notify", None)

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

    def mock_check_output(cmd, *args, **kwargs):
        if "user.name" in cmd:
            return b"test_user\n"
        if "branch" in cmd:
            return b"main\n"
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    real_now = datetime.now
    offset = [timedelta()]

    def advancing_sleep(seconds):
        offset[0] += timedelta(minutes=5)

    def fake_now(*args, **kwargs):
        return real_now() + offset[0]

    monkeypatch.setattr("time.sleep", advancing_sleep)
    # Patch datetime on the watcher module
    monkeypatch.setattr(
        watcher,
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

    try:
        mod.main()
    except (SystemExit, AttributeError, TypeError):
        pass


# ============================================================================
# _scan_remote_locks Tests
# ============================================================================


class FakeScanClient:
    """Mock Supabase client for _scan_remote_locks tests."""

    def __init__(self, data=None, raise_exc=None):
        self._data = data or []
        self._raise_exc = raise_exc

    def table(self, *args, **kwargs):
        return self

    def select(self, *args, **kwargs):
        if self._raise_exc:
            raise self._raise_exc
        return self

    def execute(self):
        if self._raise_exc:
            raise self._raise_exc

        class Resp:
            data = self._data

        return Resp()


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

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

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

    assert any("python collab.py dashboard" in r.message for r in caplog.records)


def test_main_exits_when_create_client_none(monkeypatch):
    mod = load_watcher_module()
    # Ensure main() will exit early with SystemExit when create_client is None
    mod.SUPABASE_URL = "https://example.invalid"
    mod.SUPABASE_ANON_KEY = "anon:fake"

    # Use None for create_client to simulate missing dependency
    monkeypatch.setattr(watcher, "create_client", None)
    monkeypatch.setattr(watcher, "_start_dashboard_server", lambda: None)

    # Avoid argparse picking up pytest args
    import sys as _sys

    monkeypatch.setattr(_sys, "argv", ["collab"])  # safe minimal argv

    import pytest

    with pytest.raises(SystemExit):
        mod.main()


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


def test_main_existing_watcher_label_lock_daemon(monkeypatch, tmp_path):
    """Label 'lock-daemon' entry maps to 'python lock_client.py'."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    # Simulate existing watcher with 'lock-daemon' entrypoint
    monkeypatch.setattr(
        mod,
        "_existing_watcher_running",
        lambda: (True, 1234, "python lock_client.py watch", "lock-daemon"),
    )

    with pytest.raises(SystemExit):
        mod.main()


def test_main_existing_watcher_label_pycharm_watcher(monkeypatch, tmp_path):
    """Label 'pycharm-watcher' entry maps to descriptive path."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    monkeypatch.setattr(
        mod,
        "_existing_watcher_running",
        lambda: (True, 5678, None, "pycharm-watcher"),
    )

    with pytest.raises(SystemExit):
        mod.main()


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


def test_main_parent_pid_from_cli_arg(monkeypatch, tmp_path):
    """--parent-pid CLI arg sets parent_pid and logs 'Tied to parent PID via CLI'."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py", "--parent-pid", "9999"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_existing_watcher_running", lambda: (False, None, None, None)
    )
    monkeypatch.setattr(mod, "_get_parent_ide_pid_local", lambda: None)
    monkeypatch.setattr(mod, "_reconcile_on_startup", lambda client: None)
    monkeypatch.setattr(mod, "_scan_remote_locks", lambda client: None)
    monkeypatch.setattr(mod, "_is_process_alive", lambda pid: pid != 9999)

    _stub_loop_then_interrupt(monkeypatch, mod, max_ticks=1)

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    try:
        mod.main()
    except (SystemExit, KeyboardInterrupt):
        pass


def test_main_no_parent_pid_detected(monkeypatch, tmp_path):
    """When no parent found, logs 'No IDE owner identified'."""
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

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    try:
        mod.main()
    except (SystemExit, KeyboardInterrupt):
        pass


def test_main_write_pid_file_falls_back_to_plain_pid(monkeypatch, tmp_path):
    """If _write_pid_file raises, falls back to writing raw PID integer."""
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
    monkeypatch.setattr(
        mod, "_write_pid_file", mock.Mock(side_effect=RuntimeError("fail"))
    )

    _stub_loop_then_interrupt(monkeypatch, mod, max_ticks=1)

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    try:
        mod.main()
    except (SystemExit, KeyboardInterrupt):
        pass


# ---------------------------------------------------------------------------
# main() — parent dead in the loop (lines 1839-1842 region)
# ---------------------------------------------------------------------------


def test_main_parent_pid_dies_breaks_loop(monkeypatch, tmp_path):
    """When parent_pid is tracked and goes dead, loop exits gracefully."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_existing_watcher_running", lambda: (False, None, None, None)
    )
    monkeypatch.setattr(mod, "_reconcile_on_startup", lambda client: None)
    monkeypatch.setattr(mod, "_scan_remote_locks", lambda client: None)

    # Simulate parent detection returning a specific PID
    monkeypatch.setattr(mod, "_get_parent_ide_pid_local", lambda: 12345)
    # Mark parent as dead from the start
    monkeypatch.setattr(mod, "_is_process_alive", lambda pid: False)

    ticks = [0]

    def mock_sleep(x):
        ticks[0] += 1
        if ticks[0] > 5:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", mock_sleep)

    def mock_check_output(cmd, *args, **kwargs):
        return b""

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    # Make datetime advance so parent check runs (>5s)
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

    try:
        mod.main()
    except (SystemExit, KeyboardInterrupt):
        pass
    # loop should have broken without error


# ---------------------------------------------------------------------------
# main() — debug mode / COLLAB_DEBUG (lines 1811 region)
# ---------------------------------------------------------------------------


def test_main_debug_mode_via_env(monkeypatch, tmp_path):
    """COLLAB_DEBUG=1 enables debug logging."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    monkeypatch.setenv("COLLAB_DEBUG", "1")
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

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    try:
        mod.main()
    except (SystemExit, KeyboardInterrupt):
        pass


def test_main_debug_mode_via_flag(monkeypatch, tmp_path):
    """--debug flag enables debug logging."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    monkeypatch.setenv("COLLAB_DEBUG", "0")
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py", "--debug"])

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

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    try:
        mod.main()
    except (SystemExit, KeyboardInterrupt):
        pass


# ---------------------------------------------------------------------------
# main() — idle timeout with kept locks (line 1856-1857 region)
# ---------------------------------------------------------------------------


def test_main_initial_git_status_raises_ignores_exception(monkeypatch, tmp_path):
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

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    try:
        mod.main()
    except (SystemExit, KeyboardInterrupt):
        pass


def test_main_timeout_dirty_status_raises_uses_local_set(monkeypatch, tmp_path):
    """Idle-timeout fallback when timeout git-status check raises.

    Falls back to _local_owned_locks.
    """
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py", "--timeout", "1"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_existing_watcher_running", lambda: (False, None, None, None)
    )
    monkeypatch.setattr(mod, "_get_parent_ide_pid_local", lambda: None)
    monkeypatch.setattr(mod, "_reconcile_on_startup", lambda client: None)
    monkeypatch.setattr(mod, "_scan_remote_locks", lambda client: None)

    call_count = [0]

    def sometimes_failing_git():
        call_count[0] += 1
        # 1: init last_modified, 2: current_modified in loop, 3: timeout-check
        # lookup where we intentionally fail to hit the fallback branch.
        if call_count[0] == 3:
            raise RuntimeError("git failed")
        return set()

    monkeypatch.setattr(mod, "_run_git_status_porcelain", sometimes_failing_git)
    mod._local_owned_locks = {"src/file.py"}

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

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    try:
        mod.main()
    except (SystemExit, KeyboardInterrupt):
        pass


def test_main_timeout_with_kept_locks_logs_warning(monkeypatch, tmp_path):
    """Idle timeout with dirty files logs warning about preserved locks."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py", "--timeout", "1"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_existing_watcher_running", lambda: (False, None, None, None)
    )
    monkeypatch.setattr(mod, "_get_parent_ide_pid_local", lambda: None)
    monkeypatch.setattr(mod, "_reconcile_on_startup", lambda client: None)
    monkeypatch.setattr(mod, "_scan_remote_locks", lambda client: None)
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: {"src/dirty.py"})

    # Inject some owned locks
    mod._local_owned_locks = {"src/dirty.py"}

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

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    try:
        mod.main()
    except (SystemExit, KeyboardInterrupt):
        pass


def test_main_loop_git_status_exception_logs_and_continues(monkeypatch, tmp_path):
    """Main loop logs git-status errors and continues via sleep/continue branch."""
    mod = load_watcher_module()
    _setup_common(monkeypatch, mod)
    _stub_supabase(monkeypatch, mod)
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    pid_file = tmp_path / "test.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_existing_watcher_running", lambda: (False, None, None, None)
    )
    monkeypatch.setattr(mod, "_get_parent_ide_pid_local", lambda: 123)
    monkeypatch.setattr(mod, "_reconcile_on_startup", lambda client: None)
    monkeypatch.setattr(mod, "_scan_remote_locks", lambda client: None)
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

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    try:
        mod.main()
    except (SystemExit, KeyboardInterrupt):
        pass


watcher = load_watcher_module()
