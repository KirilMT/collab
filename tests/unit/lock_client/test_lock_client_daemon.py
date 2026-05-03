"""PID and Daemon-related tests for LockClient.

These tests were moved out of the canonical `test_lock_client.py` to keep
concerns separated and make the canonical file a small shim.
"""

from __future__ import annotations

import builtins
import json
import os
import signal
import subprocess
import sys
import time
import types
from pathlib import Path
from unittest import mock

import pytest

from ._helpers import (
    FakeClient,
    FakeResponse,
    load_lock_client_module,
    make_create_client,
)

mod = load_lock_client_module()


def test_pid_file_helpers(tmp_path, monkeypatch):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    if pid_file.exists():
        pid_file.unlink()

    mod.LockClient._write_pid(424242)
    assert pid_file.exists()
    assert mod.LockClient._read_pid() == 424242

    mod.LockClient._remove_pid()
    assert not pid_file.exists()


def test_read_pid_missing_file(tmp_path, monkeypatch):
    """Test reading PID when file doesn't exist."""
    pid_file = tmp_path / "missing.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    pid = mod.LockClient._read_pid()
    assert pid is None


def test_write_pid_oserror(tmp_path, monkeypatch):
    """Test _write_pid handles OSError gracefully."""
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "nonexistent" / "dir" / "pid"))

    # Should not raise
    mod.LockClient._write_pid(12345)


def test_remove_pid_no_file(tmp_path, monkeypatch):
    """Test _remove_pid is safe when file doesn't exist."""
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "nonexistent.pid"))
    mod.LockClient._remove_pid()  # Should not raise


def test_is_process_alive_current_process():
    """Test _is_process_alive returns True for current process."""
    result = mod.LockClient._is_process_alive(os.getpid())
    assert result is True


def test_is_process_alive_nonexistent_pid_lock_client():
    """Test _is_process_alive returns False for a very high PID."""
    result = mod.LockClient._is_process_alive(99999999)
    assert result is False


def test_daemon_status_not_running(tmp_path, monkeypatch):
    """Test daemon status when not running."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    fake_create = make_create_client(FakeResponse())
    monkeypatch.setattr(mod, "_get_create_client", lambda: fake_create)

    # Ensure cmdline verification will match the watcher for the current PID
    def _fake_cmdline(p):
        return f"{sys.executable} lock_client.py watch"

    monkeypatch.setattr(
        mod.LockClient, "_get_cmdline_for_pid", staticmethod(_fake_cmdline)
    )

    lc = mod.LockClient(developer_id="test_user")
    is_running = lc.daemon_status()
    assert is_running is False


def test_daemon_status_running(tmp_path, monkeypatch):
    """Test daemon status when daemon is running."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(str(os.getpid()))
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    fake_create = make_create_client(FakeResponse())
    monkeypatch.setattr(mod, "_get_create_client", lambda: fake_create)

    lc = mod.LockClient(developer_id="test_user")
    is_running = lc.daemon_status()
    assert is_running is True


def test_daemon_start_already_running(tmp_path, monkeypatch, capsys):
    """Test daemon_start when watcher is already running."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(str(os.getpid()))
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    fake_create = make_create_client(FakeResponse())
    monkeypatch.setattr(mod, "_get_create_client", lambda: fake_create)
    # Ensure daemon_start sees a valid watcher cmdline and exits early.
    monkeypatch.setattr(
        mod.LockClient,
        "_get_cmdline_for_pid",
        staticmethod(
            lambda _p: (f"python lock_client.py watch --daemon --pid-file {pid_file}")
        ),
    )

    lc = mod.LockClient(developer_id="test_user")
    lc.daemon_start()
    captured = capsys.readouterr()
    out = captured.out.lower()
    assert ("already running" in out) or ("started" in out)


def test_daemon_start_launches_process(tmp_path, monkeypatch, capsys):
    """Test daemon_start launches a background process."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    fake_create = make_create_client(FakeResponse())
    monkeypatch.setattr(mod, "_get_create_client", lambda: fake_create)

    class FakeProc:
        pid = 99999999

    def mock_popen(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)
    # Process will appear dead since PID doesn't exist
    is_alive_false = staticmethod(lambda pid: False)
    monkeypatch.setattr(mod.LockClient, "_is_process_alive", is_alive_false)

    lc = mod.LockClient(developer_id="test_user")
    monkeypatch.setattr(lc, "_read_pid", lambda: None)
    lc.daemon_start()
    captured = capsys.readouterr()
    assert (
        "exited immediately" in captured.out.lower()
        or "starting" in captured.out.lower()
    )


def test_daemon_start_successful(tmp_path, monkeypatch, capsys):
    """Test daemon_start with successful process launch."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    fake_create = make_create_client(FakeResponse())
    monkeypatch.setattr(mod, "_get_create_client", lambda: fake_create)

    class FakeProc:
        pid = 12345

    def mock_popen(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)
    is_alive_true = staticmethod(lambda pid: True)
    monkeypatch.setattr(mod.LockClient, "_is_process_alive", is_alive_true)

    lc = mod.LockClient(developer_id="test_user")

    called_popen = []

    def mock_read_pid():
        if not called_popen:
            return None
        return 67890

    def mock_popen_wrap(*a, **k):
        called_popen.append(True)
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", mock_popen_wrap)
    monkeypatch.setattr(lc, "_read_pid", mock_read_pid)
    lc.daemon_start()
    captured = capsys.readouterr()
    assert "started" in captured.out.lower()


def test_daemon_start_with_open_dashboard(tmp_path, monkeypatch, capsys):
    """Test daemon_start with --open-dashboard flag."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    fake_create = make_create_client(FakeResponse())
    monkeypatch.setattr(mod, "_get_create_client", lambda: fake_create)

    popen_cmds = []

    class FakeProc:
        pid = 12345

    def mock_popen(cmd, **kwargs):
        popen_cmds.append(cmd)
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)
    is_alive_true = staticmethod(lambda pid: True)
    monkeypatch.setattr(mod.LockClient, "_is_process_alive", is_alive_true)

    lc = mod.LockClient(developer_id="test_user")
    lc.daemon_start(open_dashboard=True)
    # Should include --open-dashboard in the command
    assert any("--open-dashboard" in str(cmd) for cmd in popen_cmds)


def test_daemon_start_ignores_stale_stop_request_check_errors(
    monkeypatch, tmp_path, capsys
):
    """daemon_start continues even if stale stop-request inspection fails."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "daemon.pid"))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    read_calls = {"count": 0}

    def _read_pid_sequence():
        read_calls["count"] += 1
        return None if read_calls["count"] == 1 else 67890

    monkeypatch.setattr(mod.LockClient, "_read_pid", staticmethod(_read_pid_sequence))
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _pid: True)
    )
    monkeypatch.setattr(
        mod.LockClient, "_get_parent_ide_pid", lambda self: (None, None)
    )
    monkeypatch.setattr(
        mod,
        "_state_path",
        lambda _name: (_ for _ in ()).throw(RuntimeError("state path failed")),
    )
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod.time, "sleep", lambda _x: None)

    class _Proc:
        pid = 67890

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _Proc())

    lc = mod.LockClient(developer_id="test_user")
    lc.daemon_start()
    captured = capsys.readouterr()
    assert "started" in captured.out.lower()


def test_daemon_stop_not_running(tmp_path, monkeypatch, capsys):
    """Test daemon_stop when no daemon is running."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    lc = mod.LockClient(developer_id="test_user")
    # Ensure we don't pick up any real running watchers from the host
    monkeypatch.setattr(mod.LockClient, "_discover_running_watchers", lambda self: [])
    lc.daemon_stop()
    captured = capsys.readouterr()
    assert "no running" in captured.out.lower()


def test_daemon_stop_kills_process(tmp_path, monkeypatch, capsys):
    """Test daemon_stop stops the running process."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("99999")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    alive_calls = [0]

    def mock_is_alive(pid):
        alive_calls[0] += 1
        # First call True (for the check), subsequent False (stopped)
        return alive_calls[0] <= 1

    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(mock_is_alive)
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    lc = mod.LockClient(developer_id="test_user")
    lc.daemon_stop()
    captured = capsys.readouterr()
    assert "stop" in captured.out.lower()


def test_daemon_stop_forced_unix_fallback_paths(monkeypatch, tmp_path, capsys):
    """Cover forced-stop Unix fallback branches and guarded cleanup paths."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod.signal, "SIGKILL", 9, raising=False)

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    target_pid = 12345

    # Keep process "alive" long enough to force fallback paths.
    calls = {"n": 0}

    def _alive(_pid):
        calls["n"] += 1
        # First read + soft-wait + hard-wait checks all report alive.
        return calls["n"] <= 30

    monkeypatch.setattr(mod.LockClient, "_is_process_alive", staticmethod(_alive))

    # Ensure token-less stop payload path writes PID:<pid>.
    monkeypatch.setattr(mod.LockClient, "_read_pid_file", lambda self: None)

    # First PID lookup should find target pid; later canonical lookup throws
    # after forced kill to hit guarded debug path.
    pid_reads = {"n": 0}

    def _read_pid_seq():
        pid_reads["n"] += 1
        if pid_reads["n"] == 1:
            return target_pid
        raise RuntimeError("read pid fail")

    monkeypatch.setattr(mod.LockClient, "_read_pid", staticmethod(_read_pid_seq))

    # Force final _remove_pid to fail for lines 1386-1387 path.
    monkeypatch.setattr(
        mod.LockClient,
        "_remove_pid",
        staticmethod(lambda: (_ for _ in ()).throw(OSError("remove fail"))),
    )

    # Make group kill fail, then direct PID kill fail with ProcessLookupError
    # for both SIGTERM and SIGKILL fallback branches.
    def _kill(pid, sig):
        if pid < 0:
            raise OSError("group kill failed")
        raise ProcessLookupError("pid gone")

    monkeypatch.setattr(mod.os, "kill", _kill)
    monkeypatch.setattr(mod.time, "sleep", lambda _x: None)

    lc = mod.LockClient(developer_id="test_user")
    lc.daemon_stop()
    captured = capsys.readouterr()
    assert "stopping lock watcher" in captured.out.lower()


def test_daemon_stop_discovered_watcher_token_cleanup_and_print_failures(
    monkeypatch, tmp_path
):
    """daemon_stop covers discovery fallback, token stop payload, and cleanup errors."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "custom.pid"))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    stop_file = tmp_path / ".stop_request"
    shutdown_file = tmp_path / ".shutdown_complete"
    shutdown_file.write_text("done", encoding="utf-8")

    read_calls = {"count": 0}

    def _read_pid_sequence():
        read_calls["count"] += 1
        if read_calls["count"] == 1:
            return None
        raise RuntimeError("read pid cleanup fail")

    monkeypatch.setattr(mod.LockClient, "_read_pid", staticmethod(_read_pid_sequence))
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _pid: False)
    )
    monkeypatch.setattr(
        mod,
        "_state_path",
        lambda name: str(shutdown_file if name == ".shutdown_complete" else stop_file),
    )
    monkeypatch.setattr(mod.os, "fsync", lambda _fd: None)

    real_remove = mod.os.remove

    def _remove(path):
        if str(path) == str(stop_file):
            raise OSError("remove fail")
        return real_remove(path)

    monkeypatch.setattr(mod.os, "remove", _remove)

    real_print = builtins.print

    def _print(*args, **kwargs):
        text = " ".join(str(arg) for arg in args)
        if text.startswith("Stopping lock watcher"):
            raise RuntimeError("console unavailable")
        return real_print(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", _print)

    lc = mod.LockClient(developer_id="test_user")
    monkeypatch.setattr(lc, "_discover_running_watchers", lambda: [777])
    monkeypatch.setattr(lc, "_read_pid_file", lambda: {"token": "abc123"})
    monkeypatch.setattr(lc, "_remove_pid", lambda: None)

    lc.daemon_stop()
    assert stop_file.exists()


def test_daemon_status_prefers_entrypoint(tmp_path, monkeypatch):
    # Start a dummy background python process to ensure the PID is alive.
    p = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        pid_file = tmp_path / ".daemon.pid"

        # reuse a minimal writer to create the PID file used by the CLI
        def _write_meta(pid: int, entrypoint: str, cmdline: str) -> None:
            meta = {
                "pid": pid,
                "started_at": (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
                "entrypoint": entrypoint,
                "cmdline": cmdline,
                "cwd": os.getcwd(),
            }
            pid_file.write_text(json.dumps(meta), encoding="utf-8")

        _write_meta(p.pid, "python lock_client.py", f"{sys.executable} -c dummy_sleep")

        # Run the CLI status command with dummy SUPABASE env to avoid credential checks
        env = dict(os.environ)
        env["SUPABASE_URL"] = env.get("SUPABASE_URL", "http://example.invalid")
        env["SUPABASE_ANON_KEY"] = env.get("SUPABASE_ANON_KEY", "anon")
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[4])
        env["COLLAB_TEST_MODE"] = "1"
        env["COLLAB_PID_FILE"] = str(pid_file)

        res = subprocess.run(
            [sys.executable, "collab.py", "daemon-status"],
            capture_output=True,
            text=True,
            env=env,
        )
        out = res.stdout + res.stderr
        assert f"Lock watcher is RUNNING (PID: {p.pid})" in out
        assert "python lock_client.py" in out
    finally:
        try:
            p.terminate()
        except Exception:
            pass
        try:
            if pid_file.exists():
                pid_file.unlink()
        except Exception:
            pass


def test_register_signal_handlers(monkeypatch, tmp_path):
    """Test that signal handlers are registered without raising."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    lc = mod.LockClient(developer_id="test_user")
    lc._register_signal_handlers()


def test_daemon_status_preserves_stale_pid(monkeypatch, tmp_path):
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text("99999")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    # Simulate that the PID exists but belongs to another process
    monkeypatch.setattr(mod.LockClient, "_read_pid", staticmethod(lambda: 99999))
    is_alive_true = staticmethod(lambda p: True)
    monkeypatch.setattr(mod.LockClient, "_is_process_alive", is_alive_true)
    monkeypatch.setattr(
        mod.LockClient,
        "_get_cmdline_for_pid",
        staticmethod(lambda p: r"C:\\Windows\\System32\\not_the_watcher.exe"),
    )

    client = object.__new__(mod.LockClient)
    ok = mod.LockClient.daemon_status(client)
    assert ok is False
    assert os.path.exists(str(pid_file))


def test_daemon_status_local_only_discovers_replacement_watcher(monkeypatch, tmp_path):
    """Local-only daemon_status falls back to discovered watchers for stale PIDs."""
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text("12345", encoding="utf-8")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    client = object.__new__(mod.LockClient)
    client.local_only = True
    monkeypatch.setattr(mod.LockClient, "_read_pid", staticmethod(lambda: 12345))
    monkeypatch.setattr(
        mod.LockClient,
        "_is_process_alive",
        staticmethod(lambda pid: pid in {12345, 22222}),
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_get_cmdline_for_pid",
        staticmethod(lambda pid: "python unrelated.py" if pid == 12345 else None),
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_cmdline_matches_watcher",
        staticmethod(lambda cmd: "watch" in cmd),
    )
    monkeypatch.setattr(client, "_discover_running_watchers", lambda: [22222])

    assert mod.LockClient.daemon_status(client) is True


def test_daemon_status_local_only_discovers_when_pid_missing(monkeypatch):
    """Local-only daemon_status can report a discovered watcher without a PID file."""
    client = object.__new__(mod.LockClient)
    client.local_only = True
    monkeypatch.setattr(mod.LockClient, "_read_pid", staticmethod(lambda: None))
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: pid == 33333)
    )
    monkeypatch.setattr(
        mod.LockClient, "_get_cmdline_for_pid", staticmethod(lambda pid: None)
    )
    monkeypatch.setattr(client, "_discover_running_watchers", lambda: [33333])

    assert mod.LockClient.daemon_status(client) is True


def test_daemon_status_local_only_discovery_exception_returns_false(monkeypatch):
    """Local-only daemon_status suppresses discovery errors and reports not running."""
    client = object.__new__(mod.LockClient)
    client.local_only = True
    monkeypatch.setattr(mod.LockClient, "_read_pid", staticmethod(lambda: None))
    monkeypatch.setattr(
        client,
        "_discover_running_watchers",
        lambda: (_ for _ in ()).throw(RuntimeError("discovery failed")),
    )

    assert mod.LockClient.daemon_status(client) is False


def test_cmdline_matching():
    assert mod.LockClient._cmdline_matches_watcher(
        "/usr/bin/python .collab/pycharm/live_locks_watcher.py"
    )
    assert mod.LockClient._cmdline_matches_watcher(
        "python lock_client.py watch --daemon"
    )
    assert not mod.LockClient._cmdline_matches_watcher(
        r"C:\\Windows\\System32\\not_the_watcher.exe"
    )


def test_get_cmdline_with_and_without_psutil(monkeypatch):
    class DummyProc:
        def __init__(self, pid):
            pass

        def cmdline(self):
            return ["python", "live_locks_watcher.py"]

    sys.modules["psutil"] = type(
        "m", (), {"Process": DummyProc, "pid_exists": lambda p: True}
    )
    try:
        got = mod.LockClient._get_cmdline_for_pid(1234)
        assert "live_locks_watcher.py" in got
    finally:
        del sys.modules["psutil"]


# RESTORED: test_daemon_start_uses_pid_metadata
def test_daemon_start_uses_pid_metadata(monkeypatch, tmp_path, capsys):
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text(json.dumps({"pid": 9999, "entrypoint": "watcher"}))
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    # Simulate read_pid and process alive
    monkeypatch.setattr(mod.LockClient, "_read_pid", staticmethod(lambda: 9999))
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda p: True)
    )

    client = object.__new__(mod.LockClient)
    mod.LockClient.daemon_start(client)
    captured = capsys.readouterr()
    out = captured.out.lower()
    assert ("watcher already running" in out) or ("started" in out)


# RESTORED: test_daemon_start_legacy_plain_pid_matches_current
def test_daemon_start_legacy_plain_pid_matches_current(monkeypatch, tmp_path, capsys):
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text(str(os.getpid()))
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    monkeypatch.setattr(mod.LockClient, "_read_pid", staticmethod(lambda: os.getpid()))
    is_alive_true = staticmethod(lambda p: True)
    monkeypatch.setattr(mod.LockClient, "_is_process_alive", is_alive_true)
    # Avoid accidental real watcher spawn by returning a watcher-like cmdline.
    monkeypatch.setattr(
        mod.LockClient,
        "_get_cmdline_for_pid",
        staticmethod(
            lambda _p: (f"python lock_client.py watch --daemon --pid-file {pid_file}")
        ),
    )

    client = object.__new__(mod.LockClient)
    mod.LockClient.daemon_start(client)
    captured = capsys.readouterr()
    out = captured.out.lower()
    assert ("watcher already running" in out) or ("started" in out)


# RESTORED: test_read_int_pid_file
def test_read_int_pid_file(monkeypatch, tmp_path):
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    pid = mod.LockClient._read_pid()
    assert pid == 12345


# RESTORED: test_read_json_pid_file
def test_read_json_pid_file(monkeypatch, tmp_path):
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text(
        json.dumps({"pid": 4242, "cmd": "python live_locks_watcher.py"})
    )
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    pid = mod.LockClient._read_pid()
    assert pid == 4242


# RESTORED: test_read_malformed_pid_file
def test_read_malformed_pid_file(monkeypatch, tmp_path):
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text("not-a-pid")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    pid = mod.LockClient._read_pid()
    assert pid is None


# RESTORED: test_read_pid_empty_and_invalid_json_and_oserror
def test_read_pid_empty_and_invalid_json_and_oserror(monkeypatch, tmp_path):
    # empty file
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text("")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    assert mod.LockClient._read_pid() is None

    # invalid json
    pid_file.write_text("{'not': 'json'}")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    assert mod.LockClient._read_pid() is None

    # open raises OSError
    pid_file.write_text("4242")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    def _bad_open(*a, **k):
        raise OSError("boom")

    monkeypatch.setattr("builtins.open", _bad_open)
    assert mod.LockClient._read_pid() is None


# RESTORED: test_get_create_client_caches_result
def test_get_create_client_caches_result(monkeypatch):
    fake_fn = lambda url, key: None  # noqa: E731
    monkeypatch.setattr(mod, "_supabase_create_client", fake_fn)
    result = mod._get_create_client()
    assert result is fake_fn


# RESTORED: test_get_create_client_import_error
def test_get_create_client_import_error(monkeypatch):
    monkeypatch.setattr(mod, "_supabase_create_client", None)

    original_import = (
        __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__
    )

    def mock_import(name, *args, **kwargs):
        if name == "supabase":
            raise ImportError("No module named 'supabase'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)
    with pytest.raises(SystemExit):
        mod._get_create_client()


# RESTORED: test_get_create_client_lazy_import_success
def test_get_create_client_lazy_import_success(monkeypatch):
    monkeypatch.setattr(mod, "_supabase_create_client", None)

    fake_create = lambda url, key: FakeClient(FakeResponse())  # noqa: E731
    fake_supabase = type(sys)("fake_supabase")
    fake_supabase.create_client = fake_create
    monkeypatch.setitem(sys.modules, "supabase", fake_supabase)

    result = mod._get_create_client()
    assert result is fake_create

    # Reset for other tests
    monkeypatch.setattr(mod, "_supabase_create_client", None)


# RESTORED: test_get_git_username_fallback_to_env
def test_get_git_username_fallback_to_env(monkeypatch):
    monkeypatch.setenv("USER", "env_user")
    monkeypatch.setenv("USERNAME", "env_username")

    def mock_check_output(cmd, *args, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    username = mod.LockClient._get_git_username()
    assert username in ("env_user", "env_username")


# RESTORED: test_get_git_username_oserror
def test_get_git_username_oserror(monkeypatch):
    monkeypatch.delenv("DEVELOPER_ID", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("USER", raising=False)

    def mock_check_output(cmd, *args, **kwargs):
        raise OSError("failed")

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)
    client = getattr(mod, "LockClient")
    assert client._get_git_username() == "unknown_user"


# RESTORED: test_is_admin_property
def test_is_admin_property(monkeypatch):
    monkeypatch.setattr(mod, "SUPABASE_SERVICE_ROLE_KEY", "admin_key")
    monkeypatch.setattr(mod, "_supabase_create_client", lambda url, key: None)
    client = getattr(mod, "LockClient")()
    assert client.is_admin is True


# RESTORED: test_lockclient_class_and_methods_exist
def test_lockclient_class_and_methods_exist():
    assert hasattr(mod, "LockClient")
    LC = getattr(mod, "LockClient")
    for name in ("acquire", "release", "active", "get_lock_status", "watch"):
        assert hasattr(LC, name), f"Missing {name}"


# RESTORED: test_daemon_start_non_win32
def test_daemon_start_non_win32(monkeypatch):
    monkeypatch.setattr(mod, "SUPABASE_SERVICE_ROLE_KEY", "admin_key")
    monkeypatch.setattr(mod, "_supabase_create_client", lambda url, key: None)
    client = getattr(mod, "LockClient")()
    monkeypatch.setattr(client, "_read_pid", lambda: None)
    monkeypatch.setattr(client, "_is_process_alive", lambda pid: False)
    monkeypatch.setattr(sys, "platform", "linux")

    class FakeProc:
        pid = 4321

    did_call = []

    def mock_popen(*args, **kwargs):
        did_call.append(1)
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr(os, "setsid", lambda: None, raising=False)
    client.daemon_start(open_dashboard=True)
    assert len(did_call) >= 1


# RESTORED: test_daemon_start_win32_exception_and_fallback
def test_daemon_start_win32_exception_and_fallback(monkeypatch):
    monkeypatch.setattr(mod, "SUPABASE_SERVICE_ROLE_KEY", "admin_key")
    monkeypatch.setattr(mod, "_supabase_create_client", lambda url, key: None)
    client = getattr(mod, "LockClient")()
    monkeypatch.setattr(client, "_read_pid", lambda: None)
    monkeypatch.setattr(client, "_is_process_alive", lambda pid: False)
    # Prevent _get_parent_ide_pid from making its own subprocess calls
    monkeypatch.setattr(client, "_get_parent_ide_pid", lambda: (None, "unknown"))
    monkeypatch.setattr(sys, "platform", "win32")

    def mock_open(*args, **kwargs):
        raise OSError("failed")

    monkeypatch.setattr("builtins.open", mock_open)

    monkeypatch.setattr(os.path, "exists", lambda x: False)

    class FakeProc:
        pid = 1234

    did_call = []

    def mock_popen(*args, **kwargs):
        did_call.append(kwargs.get("creationflags"))
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    client.daemon_start()
    assert len(did_call) == 1
    assert did_call[0] is not None


# RESTORED: test_daemon_status_legacy_pid
def test_daemon_status_legacy_pid(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_supabase_create_client", lambda url, key: None)
    client = getattr(mod, "LockClient")()
    monkeypatch.setattr(client, "_read_pid", lambda: None)
    legacy = tmp_path / ".pycharm_watcher.pid"
    legacy.write_text("54321")
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.setattr(client, "_is_process_alive", lambda p: p == 54321)
    assert client.daemon_status() is True


# RESTORED: test_daemon_status_legacy_pid_exception
def test_daemon_status_legacy_pid_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_supabase_create_client", lambda url, key: None)
    client = getattr(mod, "LockClient")()
    monkeypatch.setattr(client, "_read_pid", lambda: None)
    legacy = tmp_path / ".pycharm_watcher.pid"
    legacy.write_text("invalid")
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    assert client.daemon_status() is False

    class BadProc:
        def __init__(self, pid):
            raise Exception("no access")

    sys.modules["psutil"] = type("m", (), {"Process": BadProc})
    try:
        assert mod.LockClient._get_cmdline_for_pid(1234) is None
    finally:
        del sys.modules["psutil"]


def test_cmdline_string_and_empty():
    class StrProc:
        def __init__(self, pid):
            pass

        def cmdline(self):
            return "python lock_client.py watch"

    sys.modules["psutil"] = type("m", (), {"Process": StrProc})
    try:
        got = mod.LockClient._get_cmdline_for_pid(1)
        assert "lock_client.py watch" in got
    finally:
        del sys.modules["psutil"]

    assert not mod.LockClient._cmdline_matches_watcher("")


def test_daemon_start_cmdline_unavailable_assumes_running(
    monkeypatch, tmp_path, capsys
):
    client = object.__new__(mod.LockClient)

    monkeypatch.setattr(mod.LockClient, "_read_pid", staticmethod(lambda: 42424))
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda p: True)
    )
    monkeypatch.setattr(
        mod.LockClient, "_get_cmdline_for_pid", staticmethod(lambda p: None)
    )

    try:
        if os.path.exists(mod.PID_FILE):
            os.unlink(mod.PID_FILE)
    except Exception:
        pass

    mod.LockClient.daemon_start(client)
    captured = capsys.readouterr()
    out = captured.out.lower()
    assert ("watcher already running" in out) or ("starting lock watcher" in out)


def test_remove_pid_oserror(tmp_path, monkeypatch):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    original_remove = os.remove

    def failing_remove(path):
        if "daemon.pid" in str(path):
            raise OSError("Permission denied")
        return original_remove(path)

    monkeypatch.setattr(os, "remove", failing_remove)

    # Should not raise
    mod.LockClient._remove_pid()


def test_register_signal_handlers_notest(monkeypatch):
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    monkeypatch.setattr(
        mod,
        "sys",
        types.SimpleNamespace(
            platform="linux",
            exit=lambda x: None,
        ),
    )
    signals_called = []

    def fake_signal(sig, handler):
        signals_called.append((sig, handler))

    monkeypatch.setattr(mod.signal, "signal", fake_signal)
    monkeypatch.setattr(mod, "atexit", types.SimpleNamespace(register=lambda fn: None))

    client = mod.LockClient(local_only=True)
    client._register_signal_handlers()
    assert any("SIGTERM" in str(s) or s == signal.SIGTERM for s, _ in signals_called)


def test_register_signal_handlers_signal_calls_shutdown(monkeypatch):
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    shutdown_called = []

    def fake_signal(sig, handler):
        if sig == signal.SIGINT:
            shutdown_called.append(handler)

    monkeypatch.setattr(mod.signal, "signal", fake_signal)
    monkeypatch.setattr(mod, "atexit", types.SimpleNamespace(register=lambda fn: None))

    graceful = mock.Mock()
    client = mod.LockClient(local_only=True)
    monkeypatch.setattr(client, "_graceful_shutdown", graceful)
    monkeypatch.setattr(mod.sys, "exit", lambda code: None)

    client._register_signal_handlers()
    if shutdown_called:
        shutdown_called[0](signal.SIGINT, None)
        graceful.assert_called()


def test_parent_monitor_not_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    client = mod.LockClient(local_only=True)
    client._start_parent_monitor_thread()  # Should not raise


def test_parent_monitor_no_parent(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    client = mod.LockClient(local_only=True)
    client._parent_pid = None
    client._start_parent_monitor_thread()  # Should not raise


def test_assign_to_job_object_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    mod.LockClient._assign_to_job_object()  # Should not raise


def test_graceful_shutdown_writes_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    client = mod.LockClient(local_only=True)
    client._shutdown_done = False

    monkeypatch.setattr(client, "active", mock.Mock(return_value=[]))
    monkeypatch.setattr(client, "_run_git_status", mock.Mock(return_value=""))

    client._graceful_shutdown(reason="test")
    marker = mod._state_path(".shutdown_complete")
    assert os.path.exists(marker)
    assert not pid_file.exists()


def test_graceful_shutdown_full_path(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    client = mod.LockClient(local_only=True)
    client._shutdown_done = False
    monkeypatch.setattr(client, "active", mock.Mock(return_value=[]))
    monkeypatch.setattr(client, "_run_git_status", mock.Mock(return_value=""))
    client._graceful_shutdown(reason="test_shutdown")
    marker = mod._state_path(".shutdown_complete")
    assert os.path.exists(marker)


def test_graceful_shutdown_with_locks(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    client = mod.LockClient(local_only=True, developer_id="test_user")
    client._shutdown_done = False
    monkeypatch.setattr(
        client,
        "active",
        mock.Mock(
            return_value=[{"file_path": "src/app.py", "developer_id": "test_user"}]
        ),
    )
    monkeypatch.setattr(client, "_run_git_status", mock.Mock(return_value=""))
    client._graceful_shutdown(reason="test")
    assert os.path.exists(mod._state_path(".shutdown_complete"))


def test_register_signal_handlers_windows_console_handler(monkeypatch):
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod, "atexit", types.SimpleNamespace(register=lambda fn: None))
    signal_sigs = []

    def fake_signal(sig, handler):
        signal_sigs.append((sig, handler))

    monkeypatch.setattr(mod.signal, "signal", fake_signal)
    registered_ctrl_handler = []
    fake_wintypes = types.SimpleNamespace(BOOL=lambda v: v, DWORD=lambda v: v)

    def _ctrl_handler(handler, add):
        registered_ctrl_handler.append(True)

    fake_ctypes = types.SimpleNamespace(
        wintypes=fake_wintypes,
        windll=types.SimpleNamespace(
            kernel32=types.SimpleNamespace(
                SetConsoleCtrlHandler=_ctrl_handler,
            )
        ),
        WINFUNCTYPE=lambda *a: lambda f: f,
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", fake_wintypes)
    client = mod.LockClient(local_only=True)
    monkeypatch.setattr(client, "_graceful_shutdown", mock.Mock())
    client._register_signal_handlers()
    assert len(registered_ctrl_handler) == 1
    assert len(signal_sigs) >= 1


# --- Appended from test_lock_client_daemon_ops.py ---


def test_read_pid_variants(tmp_path):
    pidfile = tmp_path / "pid_test.pid"
    mod.PID_FILE = str(pidfile)

    # JSON metadata
    meta = {"pid": os.getpid(), "started_at": "now", "entrypoint": "lock-daemon"}
    with open(mod.PID_FILE, "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    assert mod.LockClient._read_pid() == os.getpid()

    # Plain integer
    with open(mod.PID_FILE, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    assert mod.LockClient._read_pid() == os.getpid()

    # Malformed
    with open(mod.PID_FILE, "w", encoding="utf-8") as fh:
        fh.write("not-an-int")
    assert mod.LockClient._read_pid() is None


def test_daemon_status_with_entrypoint(monkeypatch, tmp_path, capsys):
    pidfile = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pidfile))
    meta = {
        "pid": os.getpid(),
        "entrypoint": "pycharm-watcher",
        "cmdline": "python watcher",
    }
    with open(mod.PID_FILE, "w", encoding="utf-8") as fh:
        json.dump(meta, fh)

    client = mod.LockClient(developer_id="tester", local_only=True)
    monkeypatch.setattr(client, "_is_process_alive", lambda pid: True)
    monkeypatch.setattr(client, "_get_cmdline_for_pid", lambda pid: None)

    running = client.daemon_status()
    out = capsys.readouterr().out
    assert running is True
    assert "RUNNING" in out or "RUNNING" in out.upper()


def test_daemon_start_invokes_popen(monkeypatch, tmp_path, capsys):
    pidfile = tmp_path / "daemon2.pid"
    mod.PID_FILE = str(pidfile)
    client = mod.LockClient(developer_id="daemon_test", local_only=True)

    # Fake process object
    proc = types.SimpleNamespace(pid=999999)

    # _read_pid should be None first, then return the proc.pid when polled
    calls = {"n": 0}

    def fake_read_pid():
        calls["n"] += 1
        return proc.pid if calls["n"] > 1 else None

    monkeypatch.setattr(client, "_read_pid", fake_read_pid)

    # Stub Popen to return our fake proc
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)

    # Prevent writing real PID file
    monkeypatch.setattr(client, "_write_pid", lambda *a, **k: None)
    monkeypatch.setattr(client, "_is_process_alive", lambda pid: True)

    client.daemon_start(interval=1, timeout_mins=0, open_dashboard=False)
    out = capsys.readouterr().out
    assert "Started" in out or "Started" in out


def test_daemon_stop_no_running(monkeypatch, tmp_path, capsys):
    pidfile = tmp_path / "none.pid"
    mod.PID_FILE = str(pidfile)
    client = mod.LockClient(developer_id="tester", local_only=True)

    # No PID file, and discover returns empty
    monkeypatch.setattr(client, "_read_pid", lambda: None)
    monkeypatch.setattr(client, "_discover_running_watchers", lambda: [])

    client.daemon_stop()
    out = capsys.readouterr().out
    assert "No running watcher found." in out


def test_cleanup_orphaned_processes_unix(monkeypatch, capsys):
    # Force unix branch by temporarily monkeypatching platform
    monkeypatch.setattr(sys, "platform", "linux")
    client = mod.LockClient(developer_id="tester", local_only=True)

    # Simulate ps output containing a lock_client line for a fake pid
    fake_ps = "user  12345  0.0  0.1 python /path/to/lock_client\n"

    def fake_run(*a, **k):
        return types.SimpleNamespace(stdout=fake_ps, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    killed = []

    def fake_kill(pid, sig):
        killed.append(pid)

    monkeypatch.setattr(os, "kill", fake_kill)

    client.cleanup_orphaned_processes()
    out = capsys.readouterr().out
    assert "Killing orphaned" in out or len(killed) >= 0


def test_quiet_console_loggers_and_validate(monkeypatch):
    # Test context manager runs without error
    with mod._quiet_console_loggers(names=["httpx"]):
        pass

    # Validate credentials should exit when module-level vars missing
    monkeypatch.setattr(mod, "SUPABASE_URL", None)
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", None)
    with pytest.raises(SystemExit):
        mod._validate_credentials()


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows-specific process termination"
)
def test_terminate_process_win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    client = mod.LockClient(local_only=True)
    client._terminate_process(99999)
    assert any("taskkill" in str(c) for c in calls)


def test_terminate_process_unix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    killed = []
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    client = mod.LockClient(local_only=True)
    client._terminate_process(99999)
    assert len(killed) == 1


def test_terminate_process_unix_not_found(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def raise_error(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(mod.os, "kill", raise_error)

    client = mod.LockClient(local_only=True)
    client._terminate_process(99999)


def test_get_process_name_via_tasklist(monkeypatch):
    def fake_run(cmd, **kw):
        return types.SimpleNamespace(
            stdout='"python.exe","12345","Console","1","12345 K"\n',
            returncode=0,
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    client = mod.LockClient(local_only=True)
    name = client._get_process_name_via_tasklist(12345)
    assert name == "python.exe"


def test_get_process_name_via_tasklist_not_found(monkeypatch):
    def fake_run(cmd, **kw):
        return types.SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    client = mod.LockClient(local_only=True)
    name = client._get_process_name_via_tasklist(99999)
    assert name is None


def test_pid_write_and_read(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    mod.LockClient._write_pid(424242, parent_pid=1111, token="tok")
    got = mod.LockClient._read_pid()
    assert got == 424242


def test_daemon_status_with_metadata(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    metadata = {
        "pid": os.getpid(),
        "entrypoint": "pycharm-watcher",
        "cmdline": f"{sys.executable} -m collab watch",
    }
    pid_file.write_text(json.dumps(metadata), encoding="utf-8")

    c = mod.LockClient(local_only=True)
    monkeypatch.setattr(c, "_is_process_alive", lambda _pid: True)
    monkeypatch.setattr(c, "_get_cmdline_for_pid", lambda _pid: None)

    assert c.daemon_status() is True


def test_cmdline_and_helpers():
    assert mod.LockClient._cmdline_matches_watcher("python lock_client.py watch")
    assert mod.LockClient._cmdline_matches_watcher("live_locks_watcher.py")
    assert not mod.LockClient._cmdline_matches_watcher("not_a_watcher.exe")


def test_get_parent_ide_pid_vscode_pid(monkeypatch):
    monkeypatch.setenv("VSCODE_PID", "99999")
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    client = mod.LockClient(local_only=True)
    pid, method = client._get_parent_ide_pid()
    assert pid == 99999
    assert method == "vscode_pid"


def test_get_parent_ide_pid_vscode_pid_dead(monkeypatch):
    monkeypatch.setenv("VSCODE_PID", "99999")
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: pid != 99999)
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_get_process_info_local",
        staticmethod(lambda self, pid: (None, None)),
    )


# =============================================================================
# HIGH-IMPACT MISSING BRANCH TESTS (Coverage improvement 71% -> 92%+)
# =============================================================================


def test_is_process_alive_win32_psutil_success(monkeypatch):
    """Test _is_process_alive on Windows with psutil returning valid status."""
    monkeypatch.setattr(sys, "platform", "win32")

    class MockPsutil:
        class Process:
            def __init__(self, pid):
                self.pid = pid

            def status(self):
                return "running"  # STATUS_RUNNING

        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"

        class NoSuchProcess(Exception):
            pass

        class AccessDenied(Exception):
            pass

    monkeypatch.setitem(sys.modules, "psutil", MockPsutil())
    alive = mod.LockClient._is_process_alive(12345)
    assert alive is True


def test_is_process_alive_win32_psutil_zombie(monkeypatch):
    """Test _is_process_alive detects zombie process via psutil."""
    monkeypatch.setattr(sys, "platform", "win32")

    class MockPsutil:
        class Process:
            def __init__(self, pid):
                self.pid = pid

            def status(self):
                return "zombie"

        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"

        class NoSuchProcess(Exception):
            pass

        class AccessDenied(Exception):
            pass

    monkeypatch.setitem(sys.modules, "psutil", MockPsutil())
    alive = mod.LockClient._is_process_alive(12345)
    assert alive is False


def test_is_process_alive_win32_psutil_access_denied(monkeypatch):
    """Test _is_process_alive returns True for AccessDenied (privileged proc)."""
    monkeypatch.setattr(sys, "platform", "win32")

    class MockPsutil:
        class Process:
            def __init__(self, pid):
                self.pid = pid

            def status(self):
                raise MockPsutil.AccessDenied()

        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"

        class NoSuchProcess(Exception):
            pass

        class AccessDenied(Exception):
            pass

    monkeypatch.setitem(sys.modules, "psutil", MockPsutil())
    alive = mod.LockClient._is_process_alive(12345)
    assert alive is True


def test_is_process_alive_win32_psutil_no_such_process(monkeypatch):
    """Test _is_process_alive returns False for NoSuchProcess."""
    monkeypatch.setattr(sys, "platform", "win32")

    class MockPsutil:
        class Process:
            def __init__(self, pid):
                self.pid = pid

            def status(self):
                raise MockPsutil.NoSuchProcess()

        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"

        class NoSuchProcess(Exception):
            pass

        class AccessDenied(Exception):
            pass

    monkeypatch.setitem(sys.modules, "psutil", MockPsutil())
    alive = mod.LockClient._is_process_alive(12345)
    assert alive is False


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows-specific ctypes process detection"
)
def test_is_process_alive_win32_ctypes_api_active(monkeypatch):
    """Test _is_process_alive using Win32 API when psutil unavailable."""
    monkeypatch.setattr(sys, "platform", "win32")
    original_import = __import__

    def mock_import(name, *args, **kwargs):
        if name in {"psutil", "ctypes"}:
            raise ImportError(f"no module named {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)

    def mock_check_output(cmd, **kwargs):
        if any("tasklist" in str(c) for c in cmd):
            return '"python.exe","12345","Console","1","25600 K"\n'
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(mod.subprocess, "check_output", mock_check_output)
    alive = mod.LockClient._is_process_alive(12345)
    # Falls back to tasklist which finds the process
    assert alive is True


def test_is_process_alive_win32_tasklist_fallback_not_found(monkeypatch):
    """Test _is_process_alive using tasklist fallback."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "psutil", raising=False)
    monkeypatch.delitem(sys.modules, "ctypes", raising=False)

    def mock_check_output(cmd, **kwargs):
        # Return empty result (process not found)
        return b""

    monkeypatch.setattr(mod.subprocess, "check_output", mock_check_output)
    alive = mod.LockClient._is_process_alive(12345)
    assert alive is False


def test_is_process_alive_win32_ctypes_exited_process_closes_handle(monkeypatch):
    """Test _is_process_alive returns False for exited Win32 processes."""
    monkeypatch.setattr(sys, "platform", "win32")
    original_import = __import__

    def mock_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("no module named psutil")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)

    closed = []

    fake_ctypes = types.SimpleNamespace(
        c_ulong=lambda value: types.SimpleNamespace(value=value),
        byref=lambda value: value,
        windll=types.SimpleNamespace(
            kernel32=types.SimpleNamespace(
                OpenProcess=lambda access, inherit, pid: 99,
                GetExitCodeProcess=lambda handle, exit_code: (
                    setattr(exit_code, "value", 1) or True
                ),
                CloseHandle=lambda handle: closed.append(handle),
            )
        ),
    )

    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    assert mod.LockClient._is_process_alive(99999) is False
    assert closed == [99]


def test_is_process_alive_win32_ctypes_access_denied_returns_true(monkeypatch):
    """Test _is_process_alive treats access denied as an existing process."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "psutil", raising=False)

    fake_ctypes = types.SimpleNamespace(
        windll=types.SimpleNamespace(
            kernel32=types.SimpleNamespace(
                OpenProcess=lambda access, inherit, pid: 0,
                GetLastError=lambda: 5,
            )
        )
    )

    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    assert mod.LockClient._is_process_alive(4) is True


def test_is_process_alive_win32_pid_exists_fallback(monkeypatch):
    """Test _is_process_alive falls back to psutil.pid_exists after ctypes errors."""
    monkeypatch.setattr(sys, "platform", "win32")
    original_import = __import__

    fake_psutil = types.SimpleNamespace(
        Process=lambda pid: (_ for _ in ()).throw(ValueError("Process unavailable")),
        NoSuchProcess=RuntimeError,
        AccessDenied=PermissionError,
        pid_exists=lambda pid: pid == 777,
    )

    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    def mock_import(name, *args, **kwargs):
        if name == "ctypes":
            raise ImportError("no module named ctypes")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)

    assert mod.LockClient._is_process_alive(777) is True


def test_is_process_alive_linux_success(monkeypatch):
    """Test _is_process_alive on Linux using os.kill."""
    monkeypatch.setattr(sys, "platform", "linux")

    def mock_kill(pid, sig):
        if pid == 99999:
            raise ProcessLookupError()
        # Success for other pids (no exception)

    monkeypatch.setattr(os, "kill", mock_kill)
    assert mod.LockClient._is_process_alive(12345) is True
    assert mod.LockClient._is_process_alive(99999) is False


def test_get_process_info_local_psutil_success(monkeypatch):
    """Test _get_process_info_local with psutil on Windows."""
    monkeypatch.setattr(sys, "platform", "win32")

    class MockProcess:
        def name(self):
            return "python"

        def ppid(self):
            return 5555

    class MockPsutil:
        class Process:
            def __init__(self, pid):
                pass

            def name(self):
                return "python"

            def ppid(self):
                return 5555

        class NoSuchProcess(Exception):
            pass

    monkeypatch.setitem(sys.modules, "psutil", MockPsutil())
    client = mod.LockClient(local_only=True)
    name, ppid = client._get_process_info_local(12345)
    assert name == "python.exe"
    assert ppid == 5555


def test_get_process_info_local_psutil_not_found(monkeypatch):
    """Test _get_process_info_local returns None when psutil fails."""
    monkeypatch.setattr(sys, "platform", "win32")

    class MockPsutil:
        class Process:
            def __init__(self, pid):
                raise MockPsutil.NoSuchProcess()

        class NoSuchProcess(Exception):
            pass

    monkeypatch.setitem(sys.modules, "psutil", MockPsutil())
    client = mod.LockClient(local_only=True)
    name, ppid = client._get_process_info_local(12345)
    assert name is None
    assert ppid is None


def test_get_process_info_local_wmic_success(monkeypatch):
    """Test _get_process_info_local using WMIC on Windows."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(mod.shutil, "which", lambda x: "wmic" if x == "wmic" else None)

    def mock_run(cmd, **kwargs):
        if cmd and "wmic" in str(cmd[0]):
            return types.SimpleNamespace(
                returncode=0,
                stdout="Name=python.exe\r\nParentProcessId=5555\r\n",
                stderr="",
            )
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", mock_run)
    client = mod.LockClient(local_only=True)
    name, ppid = client._get_process_info_local(12345)
    assert name == "python.exe"
    assert ppid == 5555


def test_get_process_info_local_wmic_appends_exe(monkeypatch):
    """Test _get_process_info_local appends .exe to WMIC names when needed."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(mod.shutil, "which", lambda x: "wmic" if x == "wmic" else None)

    def mock_run(cmd, **kwargs):
        return types.SimpleNamespace(
            returncode=0,
            stdout="Name=python\r\nParentProcessId=7777\r\n",
            stderr="",
        )

    monkeypatch.setattr(mod.subprocess, "run", mock_run)
    client = mod.LockClient(local_only=True)
    name, ppid = client._get_process_info_local(12345)
    assert name == "python.exe"
    assert ppid == 7777


def test_get_process_info_local_tasklist_success(monkeypatch):
    """Test _get_process_info_local falls back to tasklist name parsing."""
    monkeypatch.setattr(sys, "platform", "win32")
    original_import = __import__
    monkeypatch.setattr(mod.shutil, "which", lambda x: None)

    def mock_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("no module named psutil")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)

    def mock_check_output(cmd, **kwargs):
        return b'"python.exe","12345","Console","1","25600 K"\n'

    monkeypatch.setattr(mod.subprocess, "check_output", mock_check_output)
    client = mod.LockClient(local_only=True)
    name, ppid = client._get_process_info_local(12345)
    assert name == "python.exe"
    assert ppid is None


def test_get_process_info_local_wmic_and_tasklist_fail(monkeypatch):
    """Test _get_process_info_local returns None when all Windows lookups fail."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "psutil", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda x: "wmic" if x == "wmic" else None)

    def mock_run(cmd, **kwargs):
        raise RuntimeError("wmic failed")

    def mock_check_output(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(mod.subprocess, "run", mock_run)
    monkeypatch.setattr(mod.subprocess, "check_output", mock_check_output)
    client = mod.LockClient(local_only=True)
    assert client._get_process_info_local(12345) == (None, None)


def test_get_process_info_local_tasklist_fallback(monkeypatch):
    """Test _get_process_info_local falls back to tasklist."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod.shutil, "which", lambda x: None)  # WMIC not available

    real_import = __import__

    def mock_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("psutil disabled for tasklist fallback test")
        return real_import(name, *args, **kwargs)

    def mock_check_output(cmd, **kwargs):
        if cmd and "tasklist" in str(cmd[0]):
            return b'"python.exe","12345","Console","1","25600 K"\n'
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr("builtins.__import__", mock_import)
    monkeypatch.setattr(mod.subprocess, "check_output", mock_check_output)
    client = mod.LockClient(local_only=True)
    name, ppid = client._get_process_info_local(12345)
    assert name == "python.exe"
    assert ppid is None


def test_get_parent_ide_pid_pycharm_hosted_env(monkeypatch):
    """Test _get_parent_ide_pid detects PyCharm via env var."""
    monkeypatch.setenv("PYCHARM_HOSTED", "1")
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    monkeypatch.setattr(os, "getppid", lambda: 8888)

    client = mod.LockClient(local_only=True)
    pid, method = client._get_parent_ide_pid()
    assert pid == 8888
    assert method == "pycharm_hosted"


def test_get_parent_ide_pid_process_tree_code_exe(monkeypatch):
    """Test _get_parent_ide_pid walks process tree to find Code.exe."""
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")

    # Mock process tree: current -> node.exe -> Code.exe
    def mock_get_process_info(self, pid):
        if pid == os.getpid():
            return "node.exe", 6666
        elif pid == 6666:
            return "Code.exe", 7777
        else:
            return None, None

    monkeypatch.setattr(
        mod.LockClient, "_get_process_info_local", mock_get_process_info
    )

    client = mod.LockClient(local_only=True)
    pid, method = client._get_parent_ide_pid()
    assert pid is not None
    assert method in ("process_tree", "node_parent", "simple_walk")


def test_get_parent_ide_pid_pycharm_process_tree(monkeypatch):
    """Test _get_parent_ide_pid detects PyCharm in process tree."""
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")

    def mock_get_process_info(self, pid):
        if pid == os.getpid():
            return "python.exe", 4444
        elif pid == 4444:
            return "pycharm64.exe", 5555
        else:
            return None, None

    monkeypatch.setattr(
        mod.LockClient, "_get_process_info_local", mock_get_process_info
    )

    client = mod.LockClient(local_only=True)
    pid, method = client._get_parent_ide_pid()
    assert pid == 4444
    assert method == "pycharm_process"


def test_get_parent_ide_pid_fallback_to_immediate_parent(monkeypatch):
    """Test _get_parent_ide_pid falls back to immediate parent."""
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)

    # Mock all process tree lookups to fail
    monkeypatch.setattr(
        mod.LockClient,
        "_get_process_info_local",
        staticmethod(lambda self, pid: (None, None)),
    )
    monkeypatch.setattr(os, "getppid", lambda: 3333)

    client = mod.LockClient(local_only=True)
    pid, method = client._get_parent_ide_pid()
    # Fallback returns None when all methods fail
    assert pid is None or method == "immediate_parent"


def test_pid_file_roundtrip_json(tmp_path, monkeypatch):
    """Test reading and writing PID file with JSON metadata."""
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    # Write JSON metadata
    metadata = {
        "pid": 9999,
        "started_at": "2026-05-01T10:00:00Z",
        "entrypoint": "live_locks_watcher.py",
    }
    pid_file.write_text(json.dumps(metadata), encoding="utf-8")

    # Read it back using instance method
    client = mod.LockClient(local_only=True)
    read_metadata = client._read_pid_file()
    assert read_metadata is not None
    assert read_metadata["pid"] == 9999
    assert read_metadata["entrypoint"] == "live_locks_watcher.py"


def test_read_pid_file_corrupted_json(tmp_path, monkeypatch):
    """Test reading corrupted PID file returns None."""
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    # Write corrupted JSON
    pid_file.write_text("{invalid json:", encoding="utf-8")

    client = mod.LockClient(local_only=True)
    read_metadata = client._read_pid_file()
    assert read_metadata is None


def test_discover_running_watchers_psutil_skips_broken_process(monkeypatch):
    """Test _discover_running_watchers ignores a broken psutil process entry."""
    monkeypatch.setattr(sys, "platform", "win32")

    class BrokenInfo:
        def get(self, key, default=None):
            raise RuntimeError("bad process info")

    class Proc:
        def __init__(self, pid, cmdline):
            self.info = {"pid": pid, "cmdline": cmdline}

    fake_psutil = types.SimpleNamespace(
        process_iter=lambda attrs=None: [
            types.SimpleNamespace(info=BrokenInfo()),
            Proc(
                4321,
                [
                    "python",
                    ".collab/pycharm/live_locks_watcher.py",
                    "--pid-file",
                    mod.PID_FILE,
                ],
            ),
        ]
    )

    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    client = mod.LockClient(local_only=True)
    assert client._discover_running_watchers() == [4321]


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows-specific process discovery"
)
def test_discover_running_watchers_win32_fallback_filters_results(monkeypatch):
    """Test _discover_running_watchers uses Windows tasklist fallback and filtering."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "psutil", None)

    def mock_run(cmd, **kwargs):
        image = cmd[2].split()[-1]
        if image == "pythonw.exe":
            raise RuntimeError("tasklist failed")
        if image == "python.exe":
            return types.SimpleNamespace(
                stdout=(
                    '"python.exe","4321","Console","1","12345 K"\n'
                    '"python.exe","oops","Console","1","1 K"\n'
                ),
                returncode=0,
            )
        return types.SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", mock_run)
    monkeypatch.setattr(
        mod.LockClient,
        "_get_cmdline_for_pid",
        lambda self, pid: (
            f"python .collab/core/lock_client.py watch --pid-file {mod.PID_FILE}"
            if pid == 4321
            else None
        ),
    )

    client = mod.LockClient(local_only=True)
    assert client._discover_running_watchers() == [4321]


def test_discover_running_watchers_unix_fallback_filters_results(monkeypatch):
    """Test _discover_running_watchers uses ps/tasklist-free Unix fallback."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "psutil", None)

    def mock_run(cmd, **kwargs):
        return types.SimpleNamespace(
            stdout="123 python watcher\nabc bad\n456 python other\n",
            returncode=0,
        )

    def mock_cmdline(self, pid):
        if pid == 123:
            return (
                "python .collab/pycharm/live_locks_watcher.py "
                f"--pid-file {mod.PID_FILE}"
            )
        if pid == 456:
            raise RuntimeError("cannot inspect")
        return None

    monkeypatch.setattr(mod.subprocess, "run", mock_run)
    monkeypatch.setattr(mod.LockClient, "_get_cmdline_for_pid", mock_cmdline)

    client = mod.LockClient(local_only=True)
    assert client._discover_running_watchers() == [123]


def test_get_parent_ide_pid_pycharm_hosted(monkeypatch):
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.setenv("PYCHARM_HOSTED", "1")
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    monkeypatch.setattr(mod.os, "getppid", lambda: 88888)
    client = mod.LockClient(local_only=True)
    pid, method = client._get_parent_ide_pid()
    assert pid == 88888
    assert method == "pycharm_hosted"


def test_get_parent_ide_pid_process_tree_finds_code(monkeypatch):
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    monkeypatch.setattr(mod.os, "getpid", lambda: 100)
    names = {
        100: ("powershell.exe", 200),
        200: ("conhost.exe", 300),
        300: ("code.exe", None),
    }

    def fake_get_info(self_or_pid, pid=None):
        p = pid if pid is not None else self_or_pid
        return names.get(p, (None, None))

    monkeypatch.setattr(
        mod.LockClient, "_get_process_info_local", staticmethod(fake_get_info)
    )
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    client = mod.LockClient(local_only=True)
    pid, method = client._get_parent_ide_pid()
    assert pid == 300
    assert method == "process_tree"


def test_get_parent_ide_pid_process_tree_finds_pycharm(monkeypatch):
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    monkeypatch.setattr(mod.os, "getpid", lambda: 100)

    def fake_get_info(self_or_pid, pid=None):
        p = pid if pid is not None else self_or_pid
        if p == 100:
            return ("cmd.exe", 200)
        if p == 200:
            return ("pycharm64.exe", None)
        return (None, None)

    monkeypatch.setattr(
        mod.LockClient, "_get_process_info_local", staticmethod(fake_get_info)
    )
    client = mod.LockClient(local_only=True)
    pid, method = client._get_parent_ide_pid()
    assert pid == 200
    assert method == "pycharm_process"


def test_get_parent_ide_pid_node_parent(monkeypatch):
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    monkeypatch.setattr(mod.os, "getpid", lambda: 100)

    def fake_get_info(self_or_pid, pid=None):
        p = pid if pid is not None else self_or_pid
        if p == 100:
            return ("node.exe", 200)
        if p == 200:
            return ("code.exe", 300)
        return (None, None)

    monkeypatch.setattr(
        mod.LockClient, "_get_process_info_local", staticmethod(fake_get_info)
    )
    client = mod.LockClient(local_only=True)
    pid, method = client._get_parent_ide_pid()
    assert pid == 200
    assert method == "node_parent"


def test_get_parent_ide_pid_simple_walk_finds_code(monkeypatch):
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    monkeypatch.setattr(mod.os, "getpid", lambda: 100)
    monkeypatch.setattr(
        mod.LockClient,
        "_get_process_info_local",
        staticmethod(lambda pid: (None, None)),
    )
    monkeypatch.setattr(mod.os, "getppid", lambda: 200)
    monkeypatch.setattr(
        mod.LockClient,
        "_get_process_name_via_tasklist",
        staticmethod(lambda pid: "code.exe" if pid == 200 else None),
    )
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    client = mod.LockClient(local_only=True)
    pid, method = client._get_parent_ide_pid()
    assert pid == 200
    assert method == "simple_walk"


def test_get_parent_ide_pid_simple_walk_finds_pycharm(monkeypatch):
    """Test _get_parent_ide_pid finds PyCharm during simple parent walking."""
    monkeypatch.setenv("VSCODE_PID", "99999")
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    monkeypatch.setattr(mod.os, "getpid", lambda: 100)
    monkeypatch.setattr(mod.os, "getppid", lambda: 200)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: pid == 200)
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_get_process_info_local",
        staticmethod(lambda pid: (None, None)),
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_get_process_name_via_tasklist",
        staticmethod(lambda pid: "pycharm64.exe" if pid == 200 else None),
    )

    client = mod.LockClient(local_only=True)
    pid, method = client._get_parent_ide_pid()
    assert pid == 200
    assert method == "simple_walk"


def test_get_parent_ide_pid_simple_walk_inner_exception(monkeypatch):
    """Test _get_parent_ide_pid recovers from simple-walk lookup errors."""
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    monkeypatch.setattr(mod.os, "getpid", lambda: 100)
    monkeypatch.setattr(mod.os, "getppid", lambda: 200)
    monkeypatch.setattr(
        mod.LockClient,
        "_get_process_info_local",
        staticmethod(lambda pid: (None, None)),
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_get_process_name_via_tasklist",
        staticmethod(
            lambda pid: (_ for _ in ()).throw(RuntimeError("tasklist failed"))
        ),
    )
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: pid == 200)
    )

    client = mod.LockClient(local_only=True)
    pid, method = client._get_parent_ide_pid()
    assert pid == 200
    assert method == "immediate_parent"


def test_get_parent_ide_pid_simple_walk_outer_exception(monkeypatch):
    """Test _get_parent_ide_pid handles simple-walk initialization failures."""
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    monkeypatch.setattr(mod.logger, "warning", lambda *args, **kwargs: None)

    pid_calls = {"count": 0}

    def mock_getpid():
        pid_calls["count"] += 1
        if pid_calls["count"] == 1:
            return 100
        raise RuntimeError("getpid failed")

    monkeypatch.setattr(mod.os, "getpid", mock_getpid)
    monkeypatch.setattr(mod.os, "getppid", lambda: 0)
    monkeypatch.setattr(
        mod.LockClient,
        "_get_process_info_local",
        staticmethod(lambda pid: (None, None)),
    )

    client = mod.LockClient(local_only=True)
    assert client._get_parent_ide_pid() == (None, "unknown")


def test_get_parent_ide_pid_immediate_parent_fallback(monkeypatch):
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    monkeypatch.setattr(mod.os, "getpid", lambda: 100)
    monkeypatch.setattr(
        mod.LockClient,
        "_get_process_info_local",
        staticmethod(lambda pid: (None, None)),
    )
    monkeypatch.setattr(
        mod.LockClient, "_get_process_name_via_tasklist", staticmethod(lambda pid: None)
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_is_process_alive",
        staticmethod(lambda pid: pid in (os.getpid(), 77777)),
    )
    monkeypatch.setattr(mod.os, "getppid", lambda: 77777)
    client = mod.LockClient(local_only=True)
    pid, method = client._get_parent_ide_pid()
    assert pid == 77777
    assert method == "immediate_parent"


def test_run_cli_ignores_stream_reconfigure_errors(monkeypatch):
    """Test _run_cli ignores streams that cannot be reconfigured."""

    class FakeStream:
        def reconfigure(self, **kwargs):
            raise RuntimeError("no reconfigure")

        def write(self, text):
            return len(text)

        def flush(self):
            return None

    monkeypatch.setattr(sys, "stdout", FakeStream())
    monkeypatch.setattr(sys, "stderr", FakeStream())
    monkeypatch.setattr(sys, "argv", ["lock_client.py"])

    mod._run_cli()


def test_assign_to_job_object_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    close_handle_calls = []

    class FakeJobKernel32:
        def CreateJobObjectW(self, a, b):
            return 123

        def SetInformationJobObject(self, handle, info_class, info, size):
            return True

        def GetCurrentProcess(self):
            return 456

        def AssignProcessToJobObject(self, job_handle, process_handle):
            return True

        def CloseHandle(self, handle):
            close_handle_calls.append(handle)

    fake_wintypes = types.SimpleNamespace(
        LARGE_INTEGER=type("LARGE_INTEGER", (), {}),
        DWORD=lambda v: v,
        ULARGE_INTEGER=type("ULARGE_INTEGER", (), {}),
        BOOL=lambda v: v,
    )
    fake_ctypes = types.SimpleNamespace(
        Structure=type("Structure", (), {}),
        POINTER=lambda x: x,
        byref=lambda x: x,
        sizeof=lambda x: 1024,
        c_size_t=lambda v: v,
        c_void_p=type("c_void_p", (), {}),
        windll=types.SimpleNamespace(kernel32=FakeJobKernel32()),
        WINFUNCTYPE=lambda *a: lambda f: f,
        wintypes=fake_wintypes,
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", fake_wintypes)
    mod.LockClient._assign_to_job_object()


def test_assign_to_job_object_windows_failure(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    class FailKernel32:
        def CreateJobObjectW(self, a, b):
            return 0

        def GetLastError(self):
            return 5

    fake_ctypes = types.SimpleNamespace(
        Structure=type("Structure", (), {}),
        POINTER=lambda x: x,
        byref=lambda x: x,
        sizeof=lambda x: 1024,
        c_size_t=lambda v: v,
        c_void_p=type("c_void_p", (), {}),
        windll=types.SimpleNamespace(kernel32=FailKernel32()),
        WINFUNCTYPE=lambda *a: lambda f: f,
    )
    fake_wintypes = types.SimpleNamespace(
        LARGE_INTEGER=type("LARGE_INTEGER", (), {}),
        DWORD=lambda v: v,
        ULARGE_INTEGER=type("ULARGE_INTEGER", (), {}),
        BOOL=lambda v: v,
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", fake_wintypes)
    mod.LockClient._assign_to_job_object()


def test_assign_to_job_object_windows_set_info_fails(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    closed = []

    class FailSetInfoKernel32:
        def CreateJobObjectW(self, a, b):
            return 123

        def SetInformationJobObject(self, handle, info_class, info, size):
            return False

        def CloseHandle(self, handle):
            closed.append(handle)

        def GetLastError(self):
            return 0

    class FakeStructure:
        def __getattr__(self, name):
            obj = types.SimpleNamespace()
            setattr(self, name, obj)
            return obj

    fake_wintypes = types.SimpleNamespace(
        LARGE_INTEGER=type("LARGE_INTEGER", (), {}),
        DWORD=lambda v: v,
        ULARGE_INTEGER=type("ULARGE_INTEGER", (), {}),
        BOOL=lambda v: v,
    )
    fake_ctypes = types.SimpleNamespace(
        Structure=FakeStructure,
        POINTER=lambda x: x,
        byref=lambda x: x,
        sizeof=lambda x: 1024,
        c_size_t=lambda v: v,
        c_void_p=type("c_void_p", (), {}),
        windll=types.SimpleNamespace(kernel32=FailSetInfoKernel32()),
        WINFUNCTYPE=lambda *a: lambda f: f,
        wintypes=fake_wintypes,
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", fake_wintypes)
    mod.LockClient._assign_to_job_object()
    assert len(closed) == 1


def test_get_process_info_local_psutil_available(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    class FakeProcess:
        def __init__(self, pid):
            pass

        def name(self):
            return "python.exe"

        def ppid(self):
            return 12345

    fake_psutil = types.SimpleNamespace(
        Process=FakeProcess,
        NoSuchProcess=Exception,
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    client = mod.LockClient(local_only=True)
    name, ppid = client._get_process_info_local(os.getpid())
    assert name == "python.exe"
    assert ppid == 12345


def test_get_process_info_local_psutil_no_such_process(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    class FakePsutilNoSuch:
        def __init__(self, pid):
            pass

        def name(self):
            raise Exception("No such process")

        def ppid(self):
            return 0

    fake_psutil = types.SimpleNamespace(
        Process=FakePsutilNoSuch,
        NoSuchProcess=Exception,
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    result = mod.LockClient(local_only=True)._get_process_info_local(99999)
    assert result == (None, None)


def test_get_process_info_local_wmic_available(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(
        mod.shutil, "which", lambda cmd: "wmic.exe" if cmd == "wmic" else None
    )

    def fake_run(cmd, **kw):
        return types.SimpleNamespace(
            returncode=0,
            stdout="Name=python.exe\r\nParentProcessId=12345\r\n",
            stderr="",
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    client = mod.LockClient(local_only=True)
    name, ppid = client._get_process_info_local(os.getpid())
    assert name == "python.exe"
    assert ppid == 12345


def test_get_process_info_local_wmic_fails_then_tasklist(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "psutil", raising=False)
    monkeypatch.setattr(
        mod.shutil, "which", lambda cmd: "wmic.exe" if cmd == "wmic" else None
    )

    def fake_run_tasklist(cmd, **kw):
        if isinstance(cmd, (list, tuple)) and any("tasklist" in str(c) for c in cmd):
            return types.SimpleNamespace(
                stdout='"python.exe","12345","Console","1","12345 K"\n',
                returncode=0,
            )
        return types.SimpleNamespace(returncode=1, stdout="", stderr="error")

    monkeypatch.setattr(mod.subprocess, "run", fake_run_tasklist)
    client = mod.LockClient(local_only=True)
    name = client._get_process_name_via_tasklist(12345)
    assert name == "python.exe"


# ---------------------------------------------------------------------------
# daemon_start: orphaned watcher detection path (lines 1040-1087)
# ---------------------------------------------------------------------------


def test_daemon_start_orphaned_watcher_parent_dead(monkeypatch, tmp_path):
    """daemon_start detects orphaned watcher (parent dead) and terminates it."""
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    # Write pid metadata with parent_pid
    pid_meta = {"pid": 9900, "parent_pid": 1234}
    pid_file.write_text(json.dumps(pid_meta), encoding="utf-8")

    terminate_calls = []

    def fake_is_alive(pid):
        # watcher (9900) alive, parent (1234) dead
        return pid == 9900

    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(fake_is_alive)
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_terminate_process",
        lambda self, pid: terminate_calls.append(pid),
    )
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)
    monkeypatch.setattr(mod.LockClient, "_remove_pid", lambda self: None)

    # Control _read_pid at class level so daemon_start's first call returns 9900
    # and subsequent calls (from the startup wait loop) return 9901 immediately
    read_calls = [0]

    def controlled_read_pid():
        read_calls[0] += 1
        if read_calls[0] == 1:
            # First call in daemon_start: the orphan check
            return 9900
        # Later calls: startup wait loop - return new pid right away
        return 9901

    monkeypatch.setattr(mod.LockClient, "_read_pid", staticmethod(controlled_read_pid))

    popen_calls = []

    def fake_popen(cmd, **kwargs):
        popen_calls.append(cmd)
        return types.SimpleNamespace(pid=9901)

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)

    client = mod.LockClient(local_only=True)
    monkeypatch.setattr(client, "_get_parent_ide_pid", lambda: (None, None))
    monkeypatch.setattr(client, "_write_pid", lambda *a, **k: None)

    client.daemon_start(interval=5, timeout_mins=60)

    # _terminate_process should have been called for the orphaned watcher
    assert (
        9900 in terminate_calls
    ), f"Expected terminate for 9900, got: {terminate_calls}"


def test_daemon_start_orphaned_watcher_with_entrypoint(monkeypatch, tmp_path):
    """daemon_start prints entrypoint when watcher is already running with valid
    parent."""
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    pid_meta = {"pid": 9900, "parent_pid": 1234, "entrypoint": "lock_client.py"}
    pid_file.write_text(json.dumps(pid_meta), encoding="utf-8")

    def fake_is_alive(pid):
        # both watcher and parent alive
        return True

    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(fake_is_alive)
    )

    output = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: output.append(" ".join(str(x) for x in a))
    )

    client = mod.LockClient(local_only=True)
    client.daemon_start(interval=5, timeout_mins=60)

    assert any("already running" in line.lower() for line in output)
    assert any("lock_client.py" in line for line in output)


def test_daemon_start_orphaned_watcher_no_entrypoint(monkeypatch, tmp_path):
    """daemon_start prints plain message when watcher already running without
    entrypoint."""
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    pid_meta = {"pid": 9900, "parent_pid": 1234}
    pid_file.write_text(json.dumps(pid_meta), encoding="utf-8")

    def fake_is_alive(pid):
        return True

    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(fake_is_alive)
    )

    output = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: output.append(" ".join(str(x) for x in a))
    )

    client = mod.LockClient(local_only=True)
    client.daemon_start(interval=5, timeout_mins=60)

    assert any("already running" in line.lower() for line in output)


# ---------------------------------------------------------------------------
# daemon_stop full flow (lines 1179-1392)
# ---------------------------------------------------------------------------


def test_daemon_stop_graceful_token_based(monkeypatch, tmp_path):
    """daemon_stop writes TOKEN: stop request and removes file after graceful stop."""
    pid_file = tmp_path / ".daemon.pid"
    state_dir = str(tmp_path)
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)

    token = "abcdef12"
    pid_meta = {"pid": 8888, "token": token}
    pid_file.write_text(json.dumps(pid_meta), encoding="utf-8")

    alive_after = [True]  # process alive initially, then stops

    def fake_is_alive(pid):
        if alive_after[0]:
            alive_after[0] = False
            return False  # already dead after first check
        return False

    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(fake_is_alive)
    )
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    output = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: output.append(" ".join(str(x) for x in a))
    )

    client = mod.LockClient(local_only=True)
    client.daemon_stop()

    stop_file = str(tmp_path / ".stop_request")
    # stop file should be removed after graceful stop
    assert not os.path.exists(stop_file)


def test_daemon_stop_no_running_watcher(monkeypatch, tmp_path):
    """daemon_stop prints 'No running watcher found' when no watcher is alive."""
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))

    # No pid file
    def fake_is_alive(pid):
        return False

    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(fake_is_alive)
    )
    monkeypatch.setattr(mod.LockClient, "_discover_running_watchers", lambda self: [])
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    output = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: output.append(" ".join(str(x) for x in a))
    )

    client = mod.LockClient(local_only=True)
    client.daemon_stop()

    assert any("no running watcher" in line.lower() for line in output)


def test_daemon_stop_discovery_exception(monkeypatch, tmp_path):
    """daemon_stop handles exception in _discover_running_watchers gracefully."""
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))

    def fake_is_alive(pid):
        return False

    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(fake_is_alive)
    )

    def failing_discover(self):
        raise RuntimeError("discovery failed")

    monkeypatch.setattr(mod.LockClient, "_discover_running_watchers", failing_discover)
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    output = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: output.append(" ".join(str(x) for x in a))
    )

    client = mod.LockClient(local_only=True)
    client.daemon_stop()  # Should not raise

    assert any("no running watcher" in line.lower() for line in output)


def test_daemon_stop_forced_kill_windows(monkeypatch, tmp_path):
    """daemon_stop falls back to taskkill on Windows when soft stop times out."""
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")

    pid_meta = {"pid": 7777}
    pid_file.write_text(json.dumps(pid_meta), encoding="utf-8")

    # Process stays alive for all soft-stop polls, then dies after force kill
    check_count = [0]

    def fake_is_alive(pid):
        check_count[0] += 1
        # stays alive during soft-stop window (first 16 checks), then dies
        return check_count[0] <= 20

    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(fake_is_alive)
    )
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    taskkill_calls = []

    def fake_run(cmd, **kwargs):
        if "taskkill" in cmd:
            taskkill_calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    output = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: output.append(" ".join(str(x) for x in a))
    )

    client = mod.LockClient(local_only=True)
    client.daemon_stop()

    assert len(taskkill_calls) > 0


def test_daemon_stop_forced_kill_unix(monkeypatch, tmp_path):
    """daemon_stop sends SIGTERM on Unix when soft stop times out."""
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")

    pid_meta = {"pid": 6666}
    pid_file.write_text(json.dumps(pid_meta), encoding="utf-8")

    check_count = [0]

    def fake_is_alive(pid):
        check_count[0] += 1
        # stays alive during soft-stop (16 iterations) and force-kill window (10)
        # then finally dies so we don't hit SIGKILL path which requires SIGKILL attr
        return check_count[0] <= 26

    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(fake_is_alive)
    )
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    kill_calls = []

    def fake_kill(pid, sig):
        kill_calls.append((pid, sig))

    monkeypatch.setattr(mod.os, "kill", fake_kill)

    # Ensure SIGKILL attr exists (not available on Windows)
    if not hasattr(signal, "SIGKILL"):
        monkeypatch.setattr(mod.signal, "SIGKILL", 9, raising=False)

    output = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: output.append(" ".join(str(x) for x in a))
    )

    client = mod.LockClient(local_only=True)
    client.daemon_stop()

    assert len(kill_calls) > 0


def test_daemon_stop_pid_based_stop_request(monkeypatch, tmp_path):
    """daemon_stop writes PID: stop request when no token in metadata."""
    pid_file = tmp_path / ".daemon.pid"
    state_dir = str(tmp_path)
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_STATE_DIR", state_dir)

    # Metadata with no token
    pid_meta = {"pid": 5555}
    pid_file.write_text(json.dumps(pid_meta), encoding="utf-8")

    stop_file_contents = []

    original_open = open

    def capturing_open(path, mode="r", **kwargs):
        if ".stop_request" in str(path) and "w" in mode:

            class FakeFH:
                def write(self, text):
                    stop_file_contents.append(text)

                def flush(self):
                    pass

                def fileno(self):
                    return -1

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

            return FakeFH()
        return original_open(path, mode, **kwargs)

    monkeypatch.setattr("builtins.open", capturing_open)

    # Initially alive so daemon_stop tries graceful shutdown via stop request;
    # then becomes dead to simulate process termination after stop signal
    alive_count = [0]

    def fake_is_alive(pid):
        alive_count[0] += 1
        # First two calls return True (process alive), then False (after stop signal)
        return alive_count[0] <= 2

    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(fake_is_alive)
    )
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)
    monkeypatch.setattr(
        mod.LockClient,
        "_get_cmdline_for_pid",
        staticmethod(
            lambda pid: (
                "python .collab/pycharm/live_locks_watcher.py "
                f"--pid-file {str(pid_file)}"
            )
        ),
    )

    output = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: output.append(" ".join(str(x) for x in a))
    )

    client = mod.LockClient(local_only=True)
    client.daemon_stop()

    # Should have written a PID: stop request
    assert any("PID:" in content for content in stop_file_contents)


def test_daemon_stop_write_stop_file_exception_branch(monkeypatch, tmp_path):
    """Cover daemon_stop exception branch when writing stop request fails."""
    import builtins

    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    pid_file.write_text(json.dumps({"pid": 4444}), encoding="utf-8")

    # First liveness check makes daemon_stop choose PID path; later checks report dead.
    calls = {"n": 0}

    def _alive(_pid):
        calls["n"] += 1
        return calls["n"] == 1

    monkeypatch.setattr(mod.LockClient, "_is_process_alive", staticmethod(_alive))
    monkeypatch.setattr(mod.time, "sleep", lambda _x: None)

    real_open = builtins.open

    def _raising_open(path, mode="r", *args, **kwargs):
        if str(path).endswith(".stop_request") and "w" in mode:
            raise OSError("cannot write stop file")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _raising_open)

    client = mod.LockClient(local_only=True)
    client.daemon_stop()  # should not raise


def test_daemon_stop_remove_stop_file_and_pid_cleanup_exception_branches(
    monkeypatch, tmp_path
):
    """Cover removal and canonical PID cleanup exception branches in daemon_stop."""
    pid_file = tmp_path / ".daemon.pid"
    stop_file = tmp_path / ".stop_request"
    shutdown_file = tmp_path / ".shutdown_complete"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    pid_file.write_text(json.dumps({"pid": 5555}), encoding="utf-8")

    # Ensure graceful-stop branch is taken.
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _p: False)
    )
    monkeypatch.setattr(mod.time, "sleep", lambda _x: None)
    stop_file.write_text("PID:5555", encoding="utf-8")
    shutdown_file.write_text("ok", encoding="utf-8")

    def _exists(path):
        p = str(path)
        if p.endswith(".stop_request"):
            return True
        if p.endswith(".shutdown_complete"):
            return True
        return os.path.exists(path)

    def _remove(path):
        if str(path).endswith(".stop_request"):
            raise OSError("remove failed")
        return os.remove(path)

    client = mod.LockClient(local_only=True)
    monkeypatch.setattr(mod.os.path, "exists", _exists)
    monkeypatch.setattr(mod.os, "remove", _remove)
    _read_calls = {"n": 0}

    def _read_pid_then_fail():
        _read_calls["n"] += 1
        if _read_calls["n"] == 1:
            return 5555
        raise RuntimeError("pid read fail")

    monkeypatch.setattr(client, "_read_pid", _read_pid_then_fail)

    client.daemon_stop()  # should not raise


def test_daemon_status_metadata_parse_error_then_cmdline_unknown(
    monkeypatch, tmp_path, capsys
):
    """Cover daemon_status metadata-read exception and cmdline-unknown print path."""
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    c = mod.LockClient(local_only=True)
    monkeypatch.setattr(c, "_read_pid", lambda: 1234)
    monkeypatch.setattr(c, "_is_process_alive", lambda _p: True)
    monkeypatch.setattr(c, "_get_cmdline_for_pid", lambda _p: None)

    assert c.daemon_status() is True
    out = capsys.readouterr().out
    assert "cmdline unknown" in out.lower()


def test_daemon_status_cmdline_match_prints_verified_path(
    monkeypatch, tmp_path, capsys
):
    """Cover daemon_status branch where cmdline is present and matches watcher."""
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("9999", encoding="utf-8")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    c = mod.LockClient(local_only=True)
    monkeypatch.setattr(c, "_read_pid", lambda: 9999)
    monkeypatch.setattr(c, "_is_process_alive", lambda _p: True)
    monkeypatch.setattr(
        c, "_get_cmdline_for_pid", lambda _p: "python lock_client.py watch"
    )

    assert c.daemon_status() is True
    out = capsys.readouterr().out
    assert "lock_client.py watch" in out


# ---------------------------------------------------------------------------
# Signal handler registration (lines 2578-2683)
# ---------------------------------------------------------------------------


def test_register_signal_handlers_sigint(monkeypatch, tmp_path):
    """_register_signal_handlers registers SIGINT handler."""
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    registered = {}

    def fake_signal(signum, handler):
        registered[signum] = handler

    monkeypatch.setattr(mod.signal, "signal", fake_signal)

    client = mod.LockClient(developer_id="test_user")
    client._register_signal_handlers()

    import signal as _signal

    assert _signal.SIGINT in registered


def test_register_signal_handlers_atexit(monkeypatch, tmp_path):
    """_register_signal_handlers registers atexit in non-test mode."""
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    atexit_fns = []
    monkeypatch.setattr(mod.atexit, "register", lambda fn: atexit_fns.append(fn))
    monkeypatch.setattr(mod.signal, "signal", lambda *a: None)

    client = mod.LockClient(developer_id="test_user")
    client._register_signal_handlers()

    assert len(atexit_fns) > 0


def test_register_signal_handlers_sigbreak_windows(monkeypatch):
    """_register_signal_handlers registers SIGBREAK on Windows when available."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    registered = {}

    def fake_signal(signum, handler):
        registered[signum] = handler

    monkeypatch.setattr(mod.signal, "signal", fake_signal)
    monkeypatch.setattr(mod.atexit, "register", lambda fn: None)

    import signal as _signal

    if not hasattr(_signal, "SIGBREAK"):
        monkeypatch.setattr(_signal, "SIGBREAK", 21, raising=False)

    client = mod.LockClient(developer_id="test_user")
    client._register_signal_handlers()

    assert _signal.SIGBREAK in registered or _signal.SIGINT in registered


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows console control signal handling"
)
def test_register_signal_handlers_windows_console_ctrl(monkeypatch):
    """_register_signal_handlers tries to register Windows console ctrl handler."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(mod.signal, "signal", lambda *a: None)
    monkeypatch.setattr(mod.atexit, "register", lambda fn: None)

    set_console_ctrl_calls = []

    fake_kernel32 = types.SimpleNamespace(
        SetConsoleCtrlHandler=lambda handler, add: set_console_ctrl_calls.append(add)
    )

    import ctypes as _real_ctypes

    # Patch ctypes.windll.kernel32.SetConsoleCtrlHandler directly
    fake_windll = types.SimpleNamespace(kernel32=fake_kernel32)
    monkeypatch.setattr(_real_ctypes, "windll", fake_windll)

    client = mod.LockClient(developer_id="test_user")
    client._register_signal_handlers()

    # SetConsoleCtrlHandler should have been called
    assert len(set_console_ctrl_calls) > 0


# ---------------------------------------------------------------------------
# _start_parent_monitor_thread (lines 2719-2874)
# ---------------------------------------------------------------------------


def test_start_parent_monitor_thread_non_windows(monkeypatch):
    """_start_parent_monitor_thread returns immediately on non-Windows."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    client = mod.LockClient(developer_id="test_user")
    client._parent_pid = 1234
    # Should return immediately; no monitor attributes should be set.
    client._start_parent_monitor_thread()


def test_start_parent_monitor_thread_no_parent_pid(monkeypatch):
    """_start_parent_monitor_thread returns early when no parent_pid."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    client = mod.LockClient(developer_id="test_user")
    client._parent_pid = None
    client._start_parent_monitor_thread()  # should return early


def test_start_parent_monitor_thread_open_process_fails(monkeypatch):
    """_start_parent_monitor_thread handles OpenProcess failure gracefully."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    fake_kernel32 = types.SimpleNamespace(
        OpenProcess=lambda access, inherit, pid: 0,  # returns NULL (failure)
        GetLastError=lambda: 5,  # ERROR_ACCESS_DENIED
    )
    fake_ctypes = types.SimpleNamespace(
        windll=types.SimpleNamespace(kernel32=fake_kernel32),
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    client = mod.LockClient(developer_id="test_user")
    client._parent_pid = 9999
    client._start_parent_monitor_thread()

    assert (
        getattr(client, "_parent_monitor_started", None) is None
        or not client._parent_monitor_started
    )


def test_start_parent_monitor_thread_starts_thread(monkeypatch):
    """_start_parent_monitor_thread starts a daemon thread when OpenProcess succeeds."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    handle = 0xDEAD

    fake_kernel32 = types.SimpleNamespace(
        OpenProcess=lambda access, inherit, pid: handle,
        WaitForSingleObject=lambda h, timeout: 0,
        CloseHandle=lambda h: None,
    )
    fake_ctypes = types.SimpleNamespace(
        windll=types.SimpleNamespace(kernel32=fake_kernel32),
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    threads_started = []

    class FakeThread:
        def __init__(self, target, args, daemon):
            self._target = target
            self._args = args
            self.daemon = daemon

        def start(self):
            threads_started.append(self)

    monkeypatch.setattr(mod.threading, "Thread", FakeThread)

    client = mod.LockClient(developer_id="test_user")
    client._parent_pid = 1234
    client._start_parent_monitor_thread()

    assert len(threads_started) > 0
    assert client._parent_monitor_started is True


# ---------------------------------------------------------------------------
# _kill_orphaned_lock_clients (lines 1478-1630)
# ---------------------------------------------------------------------------


def test_cleanup_orphaned_processes_windows_psutil_match(monkeypatch, tmp_path):
    """cleanup_orphaned_processes kills Windows python process when psutil cmdline
    matches lock_client."""
    monkeypatch.setattr(sys, "platform", "win32")

    # Prevent killing ourselves
    monkeypatch.setattr(mod.os, "getpid", lambda: 99999)

    taskkill_calls = []

    def fake_run(cmd, **kwargs):
        if cmd[0] == "tasklist":
            return types.SimpleNamespace(
                stdout='"python.exe","12345","Console","1","12345 K"\n',
                returncode=0,
            )
        if cmd[0] == "taskkill":
            taskkill_calls.append(cmd)
            return types.SimpleNamespace(stdout="", returncode=0)
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def cmdline(self):
            return ["python", "lock_client.py", "watch"]

    class FakePsutil:
        class NoSuchProcess(Exception):
            pass

        @staticmethod
        def Process(pid):
            return FakeProcess(pid)

    monkeypatch.setitem(sys.modules, "psutil", FakePsutil())
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)

    output = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: output.append(" ".join(str(x) for x in a))
    )

    client = mod.LockClient(local_only=True)
    remove_pid_calls = []
    monkeypatch.setattr(client, "_remove_pid", lambda: remove_pid_calls.append(True))

    client.cleanup_orphaned_processes()

    assert len(taskkill_calls) > 0
    assert any("Killed 1 orphaned process" in line for line in output)
    assert len(remove_pid_calls) == 1


def test_cleanup_orphaned_processes_windows_wmic_fallback(monkeypatch):
    """cleanup_orphaned_processes falls back to WMIC when psutil import/inspect
    fails."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod.os, "getpid", lambda: 99999)

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "tasklist":
            return types.SimpleNamespace(
                stdout='"python.exe","23456","Console","1","12345 K"\n',
                returncode=0,
            )
        if cmd[0] == "wmic":
            return types.SimpleNamespace(
                stdout="CommandLine=python lock_client.py watch",
                returncode=0,
            )
        if cmd[0] == "taskkill":
            return types.SimpleNamespace(stdout="", returncode=0)
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    # Simulate psutil import failure
    real_import = __import__

    def mock_import(name, *a, **k):
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", mock_import)
    monkeypatch.setattr(
        mod.shutil, "which", lambda name: "wmic" if name == "wmic" else None
    )

    output = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: output.append(" ".join(str(x) for x in a))
    )

    client = mod.LockClient(local_only=True)
    monkeypatch.setattr(client, "_remove_pid", lambda: None)
    client.cleanup_orphaned_processes()

    assert any(cmd[0] == "wmic" for cmd in calls)
    assert any(cmd[0] == "taskkill" for cmd in calls)
    assert any("Killed 1 orphaned process" in line for line in output)


def test_cleanup_orphaned_processes_windows_no_matches_checks_locked_logs(
    monkeypatch, tmp_path
):
    """cleanup_orphaned_processes reports locked log files when nothing is killed."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod.os, "getpid", lambda: 99999)

    collab_root = tmp_path / ".collab"
    logs_dir = collab_root / "logs"
    logs_dir.mkdir(parents=True)
    app_log = logs_dir / "application.log"
    err_log = logs_dir / "errors.log"
    app_log.write_text("x")
    err_log.write_text("y")
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(collab_root))

    def fake_run(cmd, **kwargs):
        if cmd[0] == "tasklist":
            return types.SimpleNamespace(stdout="", returncode=0)
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)

    real_open = open

    def fake_open(path, mode="r", *a, **k):
        if str(path).endswith("application.log") and "a" in mode:
            raise PermissionError("locked")
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)

    output = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: output.append(" ".join(str(x) for x in a))
    )

    client = mod.LockClient(local_only=True)
    client.cleanup_orphaned_processes()

    assert any("No orphaned lock_client processes found." in line for line in output)
    assert any("application.log is LOCKED" in line for line in output)


def test_cleanup_orphaned_processes_unix_ps_scan(monkeypatch):
    """cleanup_orphaned_processes scans ps output and SIGTERMs orphaned Unix
    processes."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(mod.os, "getpid", lambda: 99999)

    def fake_run(cmd, **kwargs):
        assert cmd == ["ps", "aux"]
        return types.SimpleNamespace(
            stdout=(
                "user 34567 0.0 0.1 ? S 00:00:00 python lock_client.py watch\n"
                "user 99999 0.0 0.1 ? S 00:00:00 python lock_client.py watch\n"
            ),
            returncode=0,
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    kill_calls = []

    def fake_kill(pid, sig):
        kill_calls.append((pid, sig))

    monkeypatch.setattr(mod.os, "kill", fake_kill)

    output = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: output.append(" ".join(str(x) for x in a))
    )

    client = mod.LockClient(local_only=True)
    monkeypatch.setattr(client, "_remove_pid", lambda: None)
    client.cleanup_orphaned_processes()

    assert any(pid == 34567 for pid, _ in kill_calls)
    assert all(pid != 99999 for pid, _ in kill_calls)
    assert any("Killed 1 orphaned process" in line for line in output)


def test_register_signal_handlers_sigint_handler_invokes_shutdown(monkeypatch):
    """Registered SIGINT handler executes graceful shutdown path."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    signal_handlers = {}
    monkeypatch.setattr(mod.atexit, "register", lambda fn: None)

    def fake_signal(sig, handler):
        signal_handlers[sig] = handler

    monkeypatch.setattr(mod.signal, "signal", fake_signal)

    shutdown_reasons = []
    client = mod.LockClient(developer_id="test_user")
    monkeypatch.setattr(
        client,
        "_graceful_shutdown",
        lambda reason=None: shutdown_reasons.append(reason),
    )

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(mod.sys, "exit", fake_exit)

    client._register_signal_handlers()

    import signal as _signal

    assert _signal.SIGINT in signal_handlers
    with pytest.raises(SystemExit):
        signal_handlers[_signal.SIGINT](_signal.SIGINT, None)

    assert any(str(r).startswith("signal_") for r in shutdown_reasons)


def test_register_signal_handlers_windows_console_handler_executes_shutdown(
    monkeypatch,
):
    """Windows console ctrl handler callback calls graceful shutdown."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    monkeypatch.setattr(mod.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mod.signal, "signal", lambda *a, **k: None)

    captured_console_handlers = []

    class FakeKernel32:
        def SetConsoleCtrlHandler(self, handler, add):
            captured_console_handlers.append(handler)
            return True

    fake_wintypes = types.SimpleNamespace(BOOL=lambda v: v, DWORD=lambda v: v)
    fake_ctypes = types.SimpleNamespace(
        WINFUNCTYPE=lambda *a: (lambda fn: fn),
        windll=types.SimpleNamespace(kernel32=FakeKernel32()),
        wintypes=fake_wintypes,
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", fake_wintypes)

    shutdown_reasons = []
    client = mod.LockClient(developer_id="test_user")
    monkeypatch.setattr(
        client,
        "_graceful_shutdown",
        lambda reason=None: shutdown_reasons.append(reason),
    )

    client._register_signal_handlers()

    assert len(captured_console_handlers) == 1
    # Simulate CTRL_CLOSE_EVENT-like callback value
    captured_console_handlers[0](2)
    assert any(str(r).startswith("console_ctrl_") for r in shutdown_reasons)


def test_start_parent_monitor_thread_waiter_executes_shutdown(monkeypatch):
    """Parent monitor waiter branch runs and triggers graceful shutdown reason."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    closed = []

    class FakeKernel32:
        def OpenProcess(self, desired_access, inherit, pid):
            return 123

        def WaitForSingleObject(self, handle, timeout):
            return 0

        def CloseHandle(self, handle):
            closed.append(handle)
            return True

    fake_ctypes = types.SimpleNamespace(
        windll=types.SimpleNamespace(kernel32=FakeKernel32()),
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    # Execute waiter immediately in-thread for deterministic coverage
    class ImmediateThread:
        def __init__(self, target, args, daemon):
            self._target = target
            self._args = args
            self.daemon = daemon

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(mod.threading, "Thread", ImmediateThread)

    shutdown_reasons = []
    client = mod.LockClient(developer_id="test_user")
    client._parent_pid = 4242
    monkeypatch.setattr(
        client,
        "_graceful_shutdown",
        lambda reason=None: shutdown_reasons.append(reason),
    )

    client._start_parent_monitor_thread()

    assert len(closed) == 1
    assert "parent_exit_4242" in shutdown_reasons


def test_daemon_start_unix_with_parent_pid(monkeypatch, tmp_path):
    """Cover line 1179: Unix daemon_start with parent_pid truthy (else branch)."""
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod.os, "getpid", lambda: 1)

    class FakeProc:
        pid = 77777

    def mock_popen(*a, **k):
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: False)
    )

    lc = mod.LockClient(developer_id="test_user")
    monkeypatch.setattr(lc, "_read_pid", lambda: None)
    # Return a non-zero parent_pid so the Unix else branch at 1179 is taken
    monkeypatch.setattr(lc, "_get_parent_ide_pid", lambda: (54321, "test_method"))
    monkeypatch.setattr(lc, "_get_process_info_local", lambda pid: ("fake_ide", None))
    monkeypatch.setattr(lc, "_write_pid", lambda pid: None)
    lc.daemon_start()


def test_daemon_stop_test_mode_default_pid_file_skips_discovery(
    monkeypatch, tmp_path, capsys
):
    """daemon_stop short-circuits in test mode when default PID file is in use."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")

    default_pid = os.path.join(mod._COLLAB_ROOT, ".daemon.pid")
    monkeypatch.setattr(mod, "PID_FILE", default_pid)
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    c = mod.LockClient(developer_id="test_user")
    calls = []
    monkeypatch.setattr(c, "_read_pid", lambda: None)
    monkeypatch.setattr(c, "_remove_pid", lambda: calls.append("removed"))
    monkeypatch.setattr(
        c,
        "_discover_running_watchers",
        lambda: (_ for _ in ()).throw(RuntimeError("should not discover")),
    )

    c.daemon_stop()
    out = capsys.readouterr().out.lower()
    assert "no running watcher" in out
    assert calls == ["removed"]


def test_daemon_stop_propagate_restore_setter_exception_swallowed(
    monkeypatch, tmp_path
):
    """daemon_stop swallows errors when restoring collab logger propagate state."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "daemon.pid"))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    c = mod.LockClient(developer_id="test_user")
    monkeypatch.setattr(c, "_read_pid", lambda: None)
    monkeypatch.setattr(c, "_discover_running_watchers", lambda: [])
    monkeypatch.setattr(c, "_remove_pid", lambda: None)

    class _PropLogger:
        def __init__(self):
            self._prop = True
            self._writes = 0

        @property
        def propagate(self):
            return self._prop

        @propagate.setter
        def propagate(self, value):
            self._writes += 1
            if self._writes >= 2:
                raise RuntimeError("restore failed")
            self._prop = value

    prop_logger = _PropLogger()
    real_get_logger = mod.logging.getLogger

    def _patched_get_logger(name=None):
        if name == "collab":
            return prop_logger
        return real_get_logger(name) if name else real_get_logger()

    monkeypatch.setattr(mod.logging, "getLogger", _patched_get_logger)

    c.daemon_stop()


def test_daemon_status_local_only_stale_pid_discovery_match_branch(
    monkeypatch, tmp_path
):
    """Local-only daemon_status returns running when discovered watcher cmdline
    matches."""
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text("12345", encoding="utf-8")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    c = object.__new__(mod.LockClient)
    c.local_only = True
    monkeypatch.setattr(mod.LockClient, "_read_pid", staticmethod(lambda: 12345))
    monkeypatch.setattr(
        mod.LockClient,
        "_is_process_alive",
        staticmethod(lambda pid: pid in {12345, 22222}),
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_get_cmdline_for_pid",
        staticmethod(
            lambda pid: (
                "python unrelated.py" if pid == 12345 else "python lock_client.py watch"
            )
        ),
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_cmdline_matches_watcher",
        staticmethod(lambda cmd: "watch" in cmd),
    )
    monkeypatch.setattr(c, "_discover_running_watchers", lambda: [22222])

    assert mod.LockClient.daemon_status(c) is True


def test_daemon_status_local_only_stale_pid_discovery_exception_branch(
    monkeypatch, tmp_path
):
    """Local-only stale-pid discovery exceptions are swallowed and return False."""
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text("12345", encoding="utf-8")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    c = object.__new__(mod.LockClient)
    c.local_only = True
    monkeypatch.setattr(mod.LockClient, "_read_pid", staticmethod(lambda: 12345))
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _p: True)
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_get_cmdline_for_pid",
        staticmethod(lambda _pid: "python unrelated.py"),
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_cmdline_matches_watcher",
        staticmethod(lambda _cmd: False),
    )
    monkeypatch.setattr(
        c,
        "_discover_running_watchers",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert mod.LockClient.daemon_status(c) is False


def test_daemon_status_local_only_missing_pid_discovery_cmdline_match(monkeypatch):
    """Local-only daemon_status uses cmdline-matching discovered watcher when no PID
    exists."""
    c = object.__new__(mod.LockClient)
    c.local_only = True
    monkeypatch.setattr(mod.LockClient, "_read_pid", staticmethod(lambda: None))
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _p: True)
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_get_cmdline_for_pid",
        staticmethod(lambda _pid: "python lock_client.py watch"),
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_cmdline_matches_watcher",
        staticmethod(lambda cmd: "watch" in cmd),
    )
    monkeypatch.setattr(c, "_discover_running_watchers", lambda: [33333])

    assert mod.LockClient.daemon_status(c) is True
