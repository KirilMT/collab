"""PID and process helper tests for live_locks_watcher."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from unittest import mock

import pytest

from ._helpers import load_watcher_module


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific process helper")
def test_get_cmdline_for_pid_local_wmic_and_powershell(monkeypatch):
    mod = load_watcher_module()
    if "psutil" in sys.modules:
        del sys.modules["psutil"]

    def fake_check_output(cmd, stderr=None, text=None, creationflags=None):
        if cmd[0] == "wmic":
            return "CommandLine\npython watch.exe\n"
        if cmd[0] == "powershell":
            return "python powershell_watch.exe"
        raise RuntimeError("unexpected")

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    got = mod._get_cmdline_for_pid_local(1234)
    assert "watch.exe" in got or "powershell_watch" in got


def test_write_pid_file_and_get_developer_and_branch(monkeypatch, tmp_path):
    mod = load_watcher_module()
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    mod._write_pid_file(4242)
    assert pid_file.exists()
    raw = pid_file.read_text(encoding="utf-8")
    obj = __import__("json").loads(raw)
    assert obj["pid"] == 4242

    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: b"devname\n")
    dev = mod._get_developer_id()
    assert isinstance(dev, str)
    branch = mod._get_current_branch()
    assert isinstance(branch, str)


def test_is_process_alive_current_pid():
    mod = load_watcher_module()

    result = mod._is_process_alive(os.getpid())
    assert result is True


def test_is_process_alive_nonexistent_pid():
    mod = load_watcher_module()
    # Use a very large PID that is unlikely to exist
    assert mod._is_process_alive(99999999) is False


def test_is_process_alive_fallback_without_psutil(monkeypatch):
    mod = load_watcher_module()
    import builtins as _builtins

    real_import = _builtins.__import__

    def fake_import(name, *a, **k):
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *a, **k)

    monkeypatch.setattr(_builtins, "__import__", fake_import)

    def fake_check_output(*a, **k):
        raise Exception("tasklist failed")

    monkeypatch.setattr("subprocess.check_output", fake_check_output)

    # Should return False when both psutil unavailable and tasklist fails
    assert mod._is_process_alive(999999) is False


def test_live_locks_watcher_get_parent_ide_pid_traversal_gap(monkeypatch):
    mod = load_watcher_module()
    """Cover IDE ancestor search fallbacks."""
    tree = {
        100: ("python.exe", 99),
        99: ("language_server_windows_x64.exe", 98),
        98: ("Antigravity.exe", 1),
    }

    def mock_info_local(p):
        return tree.get(p, (None, None))

    monkeypatch.setattr(mod, "_get_process_info_local", mock_info_local)

    # Use monkeypatch for getpid for the watcher module's os reference
    monkeypatch.setattr(mod.os, "getpid", lambda: 100)

    # Path A: Directly ties to IDE
    assert mod._get_parent_ide_pid_local() == 98

    # Path: getppid fallback
    monkeypatch.setattr(mod, "_get_process_info_local", lambda p: (None, None))
    monkeypatch.setattr(mod.os.path, "exists", lambda x: False)
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    monkeypatch.setattr(mod.os, "getppid", lambda: 777)
    assert mod._get_parent_ide_pid_local() == 777


def test_live_locks_watcher_process_helpers_error_gaps(monkeypatch):
    mod = load_watcher_module()
    """Cover various exception branches in process helpers."""
    # _get_process_info_local exception
    with mock.patch("subprocess.check_output", side_effect=Exception("cmd fail")):
        assert mod._get_process_info_local(123) == (None, None)

    # Simulate psutil failures
    mock_psutil = mock.MagicMock()
    mock_psutil.pid_exists.return_value = False
    mock_psutil.Process.side_effect = Exception("psutil fail")

    with mock.patch.dict(sys.modules, {"psutil": mock_psutil}):
        assert mod._is_process_alive(123) is False

    # _get_cmdline_for_pid_local error path
    with mock.patch.dict(sys.modules, {"psutil": mock_psutil}):
        mock_psutil.Process.side_effect = Exception("psutil fail")
        assert mod._get_cmdline_for_pid_local(123) is None


# ---- Auto-migrated from migrated_remaining ----


def test_get_current_branch_success(monkeypatch):
    """Test getting current branch on the current platform."""
    mod = load_watcher_module()

    def mock_check_output(cmd, *args, **kwargs):
        if "branch" in cmd and "--show-current" in cmd:
            return b"feature/test-branch\n"
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)
    result = mod._get_current_branch()
    assert result == "feature/test-branch"


def test_get_current_branch_error(monkeypatch):
    """Test getting current branch returns 'unknown' on error (lines 112-113)."""
    mod = load_watcher_module()

    def mock_check_output(cmd, *args, **kwargs):
        raise subprocess.CalledProcessError(128, cmd)

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)
    result = mod._get_current_branch()
    assert result == "unknown"


# ============================================================================
# _is_process_alive Tests (lines 158, 170-176)
# ============================================================================


def test_shorten_process_label_and_cmdline_match_moved():
    mod = load_watcher_module()
    long = "/usr/bin/python /very/long/path/to/some/script.py arg1 arg2 arg3 arg4 arg5"
    s = mod._shorten_process_label(long, max_tokens=4, max_len=50)
    assert s is not None
    assert "python" in s
    assert mod._cmdline_matches_watcher_local(
        "python .collab/pycharm/live_locks_mod.py"
    )
    assert not mod._cmdline_matches_watcher_local("C:/Windows/not_mod.exe")


def test_should_ignore_and_cmdline_helpers_migrated():
    mod = load_watcher_module()
    assert mod._should_ignore_path(".git/objects/abc") is True
    assert mod._should_ignore_path("src/app.py") is False

    assert mod._cmdline_matches_watcher_local("python live_locks_watcher") is True
    assert mod._cmdline_matches_watcher_local(None) is False

    shortened = mod._shorten_process_label(
        "C:/some/very/long/path/python.exe script.py token1 token2 token3 token4 token5"
    )
    assert shortened is not None
    assert ("..." in shortened) or (len(shortened) <= 80)


def test_write_and_existing_watcher_running_migrated(monkeypatch, tmp_path):
    mod = load_watcher_module()
    pid_file = tmp_path / f"pytest_collab_{os.getpid()}.daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    meta = {
        "pid": os.getpid(),
        "cmdline": "python live_locks_watcher",
        "entrypoint": "pycharm-watcher",
    }
    with open(mod.PID_FILE, "w", encoding="utf-8") as fh:
        json.dump(meta, fh)

    monkeypatch.setattr(
        mod, "_get_cmdline_for_pid_local", lambda pid: "python live_locks_watcher"
    )
    monkeypatch.setattr(mod, "_is_process_alive", lambda pid: True)

    running, pid, cmdline, entry = mod._existing_watcher_running()
    assert running is True
    assert pid == os.getpid()


def test_write_pid_file_and_read_migrated(monkeypatch, tmp_path):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "pidfile.pid"))
    mod._write_pid_file(os.getpid(), parent_pid=os.getppid())
    with open(mod.PID_FILE, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    assert raw.get("pid") == os.getpid()


def test_existing_watcher_running_json_and_plain_moved(tmp_path, monkeypatch):
    mod = load_watcher_module()
    pid_file = tmp_path / ".daemon.pid"
    # JSON metadata with entrypoint
    pid_file.write_text(
        __import__("json").dumps(
            {"pid": 1111, "cmdline": "python foo", "entrypoint": "pycharm-watcher"}
        )
    )
    monkeypatch.setattr(watcher, "PID_FILE", str(pid_file))
    # simulate get_cmdline returning a matching string
    monkeypatch.setattr(
        watcher,
        "_get_cmdline_for_pid_local",
        staticmethod(lambda p: "python .collab/pycharm/live_locks_mod.py"),
    )
    ok, pid, cmd, entry = mod._existing_watcher_running()
    assert ok and pid == 1111

    # plain integer pid
    pid_file.write_text(str(2222))
    monkeypatch.setattr(
        watcher, "_get_cmdline_for_pid_local", staticmethod(lambda p: None)
    )
    ok2, pid2, cmd2, entry2 = mod._existing_watcher_running()
    # Without cmdline match, should return False but pid present
    assert (ok2 is False) and pid2 == 2222


def test_get_session_token_handles_component_exceptions(monkeypatch):
    """_get_session_token should use safe fallbacks if component derivation fails."""
    mod = load_watcher_module()

    class BadDev:
        def __str__(self):
            raise RuntimeError("bad str")

    monkeypatch.setattr(
        mod.socket,
        "gethostname",
        lambda: (_ for _ in ()).throw(RuntimeError("no host")),
    )
    monkeypatch.setattr(
        mod.os.path, "abspath", lambda p: (_ for _ in ()).throw(RuntimeError("no path"))
    )

    token = mod._get_session_token(BadDev())
    assert isinstance(token, str)
    assert len(token) == 16


def test_is_same_machine_token_matches_env_user_when_git_fails(monkeypatch):
    """_is_same_machine_token can match using env-user candidate when git lookup
    fails."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", None)
    monkeypatch.setenv("USERNAME", "alice")

    monkeypatch.setattr(mod.socket, "gethostname", lambda: "hostA")
    monkeypatch.setattr(mod.os.path, "abspath", lambda p: "C:/repo")

    # Force git-config path to fail
    monkeypatch.setattr(
        mod.subprocess,
        "check_output",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("git fail")),
    )

    import hashlib

    seed = "alice:hosta:c:/repo"
    expected = hashlib.sha256(seed.encode()).hexdigest()[:16]
    assert mod._is_same_machine_token(expected) is True


def test_is_same_machine_token_returns_false_for_unknown_token(monkeypatch):
    """_is_same_machine_token returns False when no candidate seed matches."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "bob")
    monkeypatch.setenv("USERNAME", "bob")
    monkeypatch.setattr(mod.socket, "gethostname", lambda: "hostB")
    monkeypatch.setattr(mod.os.path, "abspath", lambda p: "C:/repo")

    # Keep git deterministic too
    monkeypatch.setattr(mod.subprocess, "check_output", lambda *a, **k: b"bob\n")

    assert mod._is_same_machine_token("0000000000000000") is False


# New test: malformed PID JSON should be treated as no existing watcher


def test_existing_watcher_running_with_malformed_json(tmp_path):
    mod = load_watcher_module()
    # Write malformed JSON to PID file and ensure helper treats it as no watcher
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text("{not: json}")
    orig = mod.PID_FILE
    try:
        mod.PID_FILE = str(pid_file)
        running, pid, cmd, entry = mod._existing_watcher_running()
        assert running is False and pid is None
    finally:
        mod.PID_FILE = orig


def test_is_process_alive_fallback_without_psutil_moved(monkeypatch):
    mod = load_watcher_module()
    # Simulate ImportError for psutil and make tasklist command fail
    import builtins as _builtins

    real_import = _builtins.__import__

    def fake_import(name, *a, **k):
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *a, **k)

    monkeypatch.setattr(_builtins, "__import__", fake_import)

    def fake_check_output(*a, **k):
        raise Exception("tasklist failed")

    monkeypatch.setattr("subprocess.check_output", fake_check_output)

    # Should return False when both psutil unavailable and tasklist fails
    assert mod._is_process_alive(999999) is False


def test_get_cmdline_for_pid_local_uses_psutil(monkeypatch):
    mod = load_watcher_module()
    fake_psutil = types.SimpleNamespace()

    class FakeProc:
        def __init__(self, pid):
            pass

        def cmdline(self):
            return [sys.executable, "-c", "print(1)"]

    fake_psutil.Process = FakeProc
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    out = mod._get_cmdline_for_pid_local(os.getpid())
    assert out and "python" in out.lower()


def test_get_process_info_local_non_windows(monkeypatch):
    """Non-Windows platforms should skip WMIC lookup."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod.sys, "platform", "linux")
    assert mod._get_process_info_local(123) == (None, None)


def test_get_process_info_local_parses_wmic_output(monkeypatch):
    """Windows WMIC output with process row should be parsed."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod.sys, "platform", "win32")

    def _wmic(*args, **kwargs):
        return b"Name  ParentProcessId\ncode.exe 456\n"

    monkeypatch.setattr(mod.subprocess, "check_output", _wmic)
    assert mod._get_process_info_local(999) == ("code.exe", 456)


def test_get_parent_ide_pid_node_promotes_to_code(monkeypatch):
    """When the current process is node.exe under Code, return Code PID."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod.os, "getpid", lambda: 100)

    def _info(pid):
        if pid == 100:
            return ("node.exe", 200)
        if pid == 200:
            return ("Code.exe", 1)
        return (None, None)

    monkeypatch.setattr(mod, "_get_process_info_local", _info)
    assert mod._get_parent_ide_pid_local() == 200


def test_get_parent_ide_pid_env_and_pycharm_fallbacks(monkeypatch):
    """Cover VSCODE_PID alive path and PYCHARM_HOSTED fallback."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod.os, "getpid", lambda: 10)
    monkeypatch.setattr(mod, "_get_process_info_local", lambda pid: (None, None))

    monkeypatch.setenv("VSCODE_PID", "4321")
    monkeypatch.setattr(mod, "_is_process_alive", lambda pid: pid == 4321)
    assert mod._get_parent_ide_pid_local() == 4321

    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.setenv("PYCHARM_HOSTED", "1")
    monkeypatch.setattr(mod.os, "getppid", lambda: 777)
    assert mod._get_parent_ide_pid_local() == 777


def test_get_parent_ide_pid_returns_none_when_no_candidates(monkeypatch):
    """If no ancestor, env PID, or parent shell exists, return None."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod.os, "getpid", lambda: 10)
    monkeypatch.setattr(mod, "_get_process_info_local", lambda pid: (None, None))
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    monkeypatch.setattr(mod.os, "getppid", lambda: 0)
    assert mod._get_parent_ide_pid_local() is None


def test_get_cmdline_for_pid_local_psutil_scalar_cmdline(monkeypatch):
    """Psutil cmdline() returning scalar should be stringified."""
    mod = load_watcher_module()
    fake_psutil = types.SimpleNamespace()

    class FakeProc:
        def __init__(self, pid):
            pass

        def cmdline(self):
            return "python watcher"

    fake_psutil.Process = FakeProc
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    out = mod._get_cmdline_for_pid_local(1)
    assert out == "python watcher"


def test_existing_watcher_running_handles_cmdline_probe_exception(
    monkeypatch, tmp_path
):
    """Failure during cmdline probe should not crash watcher detection."""
    mod = load_watcher_module()
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(
        json.dumps({"pid": 321, "entrypoint": "not-watcher"}), encoding="utf-8"
    )
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    calls = {"n": 0}

    def _cmd_probe(pid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("probe failed")
        return None

    monkeypatch.setattr(mod, "_get_cmdline_for_pid_local", _cmd_probe)
    monkeypatch.setattr(mod, "_is_process_alive", lambda pid: True)

    running, pid, cmdline, entry = mod._existing_watcher_running()
    assert running is False
    assert pid == 321
    assert entry == "not-watcher"


def test_existing_watcher_running_stale_pid_with_dead_parent_details(
    monkeypatch, tmp_path
):
    """Stale watcher PID with stored parent should emit dead-parent diagnostics path."""
    mod = load_watcher_module()
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(
        json.dumps(
            {
                "pid": 9999,
                "entrypoint": "pycharm-watcher",
                "parent_pid": 1111,
                "started_at": "2025-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    def _alive(pid):
        return False

    monkeypatch.setattr(mod, "_is_process_alive", _alive)

    running, pid, cmdline, entry = mod._existing_watcher_running()
    assert running is False
    assert pid == 9999
    assert cmdline is None
    assert entry is None
    assert not pid_file.exists()


def test_existing_watcher_running_detects_orphaned_parent(monkeypatch, tmp_path):
    """Alive watcher with dead stored parent should be treated as orphaned."""
    mod = load_watcher_module()
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(
        json.dumps({"pid": 7777, "cmdline": "python something", "parent_pid": 8888}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "_get_cmdline_for_pid_local", lambda pid: None)

    def _alive(pid):
        if pid == 7777:
            return True
        if pid == 8888:
            return False
        return False

    monkeypatch.setattr(mod, "_is_process_alive", _alive)
    running, pid, cmdline, entry = mod._existing_watcher_running()
    assert running is False
    assert pid == 7777
    assert cmdline == "python something"
    assert entry is None


def test_get_parent_ide_pid_returns_direct_ide_and_handles_ancestor_exception(
    monkeypatch,
):
    """Cover direct IDE return and ancestor-walk exception fallback logging path."""
    mod = load_watcher_module()

    monkeypatch.setattr(mod.os, "getpid", lambda: 42)
    monkeypatch.setattr(
        mod, "_get_process_info_local", lambda pid: ("pycharm64.exe", 10)
    )
    assert mod._get_parent_ide_pid_local() == 42

    # Avoid logging internals calling os.getpid() while we force getpid to fail.
    monkeypatch.setattr(mod.logger, "debug", lambda *a, **k: None)
    monkeypatch.setattr(
        mod.os, "getpid", lambda: (_ for _ in ()).throw(RuntimeError("pid fail"))
    )
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    monkeypatch.setattr(mod.os, "getppid", lambda: 555)
    assert mod._get_parent_ide_pid_local() == 555


def test_existing_watcher_running_stale_pid_remove_oserror(monkeypatch, tmp_path):
    """OSError during stale PID removal should be swallowed and still return stale
    state."""
    mod = load_watcher_module()
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(
        json.dumps({"pid": 2468, "entrypoint": "pycharm-watcher"}), encoding="utf-8"
    )
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "_is_process_alive", lambda pid: False)

    def _rm(path):
        raise OSError("cannot remove")

    monkeypatch.setattr(mod.os, "remove", _rm)
    running, pid, cmdline, entry = mod._existing_watcher_running()
    assert running is False
    assert pid == 2468
    assert cmdline is None
    assert entry is None


# (removed duplicate moved variant; canonical version retained below)


# Restored archived-only original-name test (non-destructive restore)


def test_is_process_alive_win32_tasklist_success(monkeypatch):
    """_is_process_alive returns True on win32 when tasklist finds the PID (no
    psutil)."""
    mod = load_watcher_module()
    monkeypatch.setattr(sys, "platform", "win32")

    import builtins as _builtins

    real_import = _builtins.__import__

    def _no_psutil(name, *a, **k):
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *a, **k)

    monkeypatch.setattr(_builtins, "__import__", _no_psutil)
    monkeypatch.setattr(
        subprocess, "check_output", lambda *a, **k: "Image  PID\npython.exe  99999"
    )
    assert mod._is_process_alive(99999) is True


def test_is_process_alive_non_win32_process_alive(monkeypatch):
    """_is_process_alive returns True on non-win32 when os.kill succeeds."""
    mod = load_watcher_module()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: None)
    assert mod._is_process_alive(12345) is True


def test_is_process_alive_non_win32_process_lookup_error(monkeypatch):
    """_is_process_alive returns False on non-win32 when process does not exist."""
    mod = load_watcher_module()
    monkeypatch.setattr(sys, "platform", "linux")

    def _kill_not_found(pid, sig):
        raise ProcessLookupError("no such process")

    monkeypatch.setattr(mod.os, "kill", _kill_not_found)
    assert mod._is_process_alive(12345) is False


def test_is_process_alive_non_win32_permission_error(monkeypatch):
    """_is_process_alive returns True on non-win32 when PermissionError (process exists
    but not owned by this user)."""
    mod = load_watcher_module()
    monkeypatch.setattr(sys, "platform", "linux")

    def _kill_permission_denied(pid, sig):
        raise PermissionError("access denied")

    monkeypatch.setattr(mod.os, "kill", _kill_permission_denied)
    assert mod._is_process_alive(12345) is True


def test_get_current_branch_non_win32(monkeypatch):
    """_get_current_branch uses subprocess without creationflags on non-win32."""
    mod = load_watcher_module()
    monkeypatch.setattr(sys, "platform", "linux")

    def _check_output(cmd, **kwargs):
        assert (
            "creationflags" not in kwargs
        ), "creationflags must not be passed on non-win32"
        return b"main\n"

    monkeypatch.setattr(subprocess, "check_output", _check_output)
    assert mod._get_current_branch() == "main"


watcher = load_watcher_module()
