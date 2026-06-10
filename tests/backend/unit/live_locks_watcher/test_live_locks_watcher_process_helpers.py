"""PID and process helper tests for live_locks_watcher."""

from __future__ import annotations

import json
import logging
import os
import sys
import types
from unittest import mock

import pytest

from ._helpers import load_watcher_module, patch_git_capture, patch_subprocess


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific process helper")
def test_get_cmdline_for_pid_local_wmic_and_powershell(monkeypatch):
    mod = load_watcher_module()
    fake_psutil = types.SimpleNamespace()

    class _FailingProc:
        def cmdline(self):
            raise OSError("psutil unavailable in test")

    fake_psutil.Process = lambda _pid: _FailingProc()
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(
        mod.platform_probe,
        "get_cmdline",
        lambda pid: "python watch.exe" if pid == 1234 else None,
    )
    got = mod._get_cmdline_for_pid_local(1234)
    assert got is not None
    assert "watch.exe" in got


def test_write_pid_file_and_get_developer_and_branch(monkeypatch, tmp_path):
    mod = load_watcher_module()
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    mod._write_pid_file(4242)
    assert pid_file.exists()
    raw = pid_file.read_text(encoding="utf-8")
    obj = __import__("json").loads(raw)
    assert obj["pid"] == 4242

    patch_subprocess(monkeypatch, check_output=lambda *a, **k: b"devname\n")
    dev = mod._get_developer_id()
    assert isinstance(dev, str)
    branch = mod._get_current_branch()
    assert isinstance(branch, str)


def test_touch_pid_file_heartbeat_updates_mtime(monkeypatch, tmp_path):
    mod = load_watcher_module()
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text('{"pid": 1}', encoding="utf-8")
    stale_mtime = 1_000_000.0
    os.utime(pid_file, (stale_mtime, stale_mtime))
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    mod._touch_pid_file_heartbeat()

    assert os.path.getmtime(pid_file) > stale_mtime


def test_touch_pid_file_heartbeat_missing_file_is_noop(monkeypatch, tmp_path):
    mod = load_watcher_module()
    missing = tmp_path / "missing.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(missing))
    mod._touch_pid_file_heartbeat()
    # No-op: a heartbeat touch must not create a PID file that does not exist.
    assert not missing.exists()


def test_touch_pid_file_heartbeat_oserror_is_swallowed(monkeypatch, tmp_path):
    mod = load_watcher_module()
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text('{"pid": 1}', encoding="utf-8")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    def boom(_path, _times):
        raise OSError("permission denied")

    monkeypatch.setattr(mod.os, "utime", boom)
    mod._touch_pid_file_heartbeat()


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
    """Cover the Windows exception branches in process helpers deterministically."""
    mod = load_watcher_module()
    # Force the Windows code paths so the assertions exercise the real
    # exception branches on every host (not the non-win32 early-return guard).
    monkeypatch.setattr(mod.sys, "platform", "win32")

    # _get_process_info_local: the wmic probe raising -> (None, None).
    def _wmic_boom(_pid):
        raise Exception("wmic fail")

    monkeypatch.setattr(mod.platform_probe, "wmic_process_name_and_ppid", _wmic_boom)
    assert mod._get_process_info_local(123) == (None, None)

    # _is_process_alive: psutil present but reports the pid as not existing.
    mock_psutil = mock.MagicMock()
    mock_psutil.pid_exists.return_value = False
    mock_psutil.Process.side_effect = Exception("psutil fail")
    with mock.patch.dict(sys.modules, {"psutil": mock_psutil}):
        assert mod._is_process_alive(123) is False

    # _get_cmdline_for_pid_local: psutil.Process raises and the platform
    # fallback yields nothing -> None.
    monkeypatch.setattr(mod.platform_probe, "get_cmdline", lambda _pid: None)
    with mock.patch.dict(sys.modules, {"psutil": mock_psutil}):
        assert mod._get_cmdline_for_pid_local(123) is None


# ---- Auto-migrated from migrated_remaining ----


def test_get_current_branch_success(monkeypatch):
    """Test getting current branch on the current platform."""
    mod = load_watcher_module()

    def _git(argv, **_k):
        if "branch" in argv and "--show-current" in argv:
            return "feature/test-branch"
        return ""

    patch_git_capture(monkeypatch, mod, _git)
    result = mod._get_current_branch()
    assert result == "feature/test-branch"


def test_get_current_branch_error(monkeypatch):
    """Test getting current branch returns 'unknown' on error (lines 112-113)."""
    mod = load_watcher_module()
    patch_git_capture(monkeypatch, mod, lambda *_a, **_k: "")
    result = mod._get_current_branch()
    assert result == "unknown"


def test_get_current_branch_when_git_helper_raises(monkeypatch):
    """Cover exception guard when _git_capture_text raises unexpectedly."""
    mod = load_watcher_module()

    def _boom(*_a, **_k):
        raise RuntimeError("git helper failed")

    monkeypatch.setattr(mod, "_git_capture_text", _boom)
    assert mod._get_current_branch() == "unknown"


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
    patch_subprocess(
        monkeypatch,
        check_output=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("git fail")),
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
    patch_subprocess(monkeypatch, check_output=lambda *a, **k: b"bob\n")

    assert mod._is_same_machine_token("0000000000000000") is False


# New test: malformed PID JSON should be treated as no existing watcher


def test_existing_watcher_running_with_malformed_json(monkeypatch, tmp_path):
    mod = load_watcher_module()
    # Write malformed JSON to PID file and ensure helper treats it as no watcher
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text("{not: json}")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    running, pid, cmd, entry = mod._existing_watcher_running()
    assert running is False and pid is None


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
    monkeypatch.setattr(
        mod.platform_probe,
        "wmic_process_name_and_ppid",
        lambda pid: ("code.exe", 456) if pid == 999 else (None, None),
    )
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


def test_get_cmdline_for_pid_local_non_windows_without_psutil(monkeypatch):
    """When psutil is unavailable on non-Windows, cmdline lookup should return None."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod.sys, "platform", "linux")

    import builtins as _builtins

    real_import = _builtins.__import__

    def _no_psutil(name, *a, **k):
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *a, **k)

    monkeypatch.setattr(_builtins, "__import__", _no_psutil)
    assert mod._get_cmdline_for_pid_local(12345) is None


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
    monkeypatch, tmp_path, caplog
):
    """Stale watcher PID with a stored dead parent emits root-cause diagnostics."""
    mod = load_watcher_module()
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(
        json.dumps(
            {
                "pid": 4321,
                "entrypoint": "not-watcher",
                "parent_pid": 9876,
                "started_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    # cmdline probe must not match a watcher, so we don't short-circuit
    monkeypatch.setattr(mod, "_get_cmdline_for_pid_local", lambda pid: None)
    # Both the watcher PID and the stored parent PID are dead.
    monkeypatch.setattr(mod, "_is_process_alive", lambda _p: False)

    with caplog.at_level(logging.INFO):
        running, pid, cmdline, entry = mod._existing_watcher_running()

    assert running is False
    assert pid == 4321
    # The dead-parent diagnostics path must actually run and log the root cause.
    assert "parent_pid=9876" in caplog.text
    assert "Root cause" in caplog.text
    # Stale PID file should have been cleaned up.
    assert not pid_file.exists()


# ============================================================================
# _git_capture_text logging level (issue #72)
# ============================================================================


def test_git_capture_text_warns_on_failure(monkeypatch, caplog):
    """_git_capture_text logs a warning (not just debug) when git fails."""
    mod = load_watcher_module()

    def _fail(*_a, **_k):
        raise RuntimeError("simulated git failure")

    monkeypatch.setattr(mod.safe_subprocess, "capture", _fail)

    with caplog.at_level("WARNING"):
        result = mod._git_capture_text(["git", "status"])

    assert result == ""
    assert "simulated git failure" in caplog.text


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
    monkeypatch.setattr(mod.sys, "platform", "win32")

    import builtins as _builtins

    real_import = _builtins.__import__

    def _no_psutil(name, *a, **k):
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *a, **k)

    monkeypatch.setattr(_builtins, "__import__", _no_psutil)
    monkeypatch.setattr(
        mod.platform_probe, "is_pid_alive_tasklist", lambda pid: pid == 99999
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
    """_get_current_branch returns git output on non-win32 platforms."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod.sys, "platform", "linux")
    patch_git_capture(
        monkeypatch,
        mod,
        lambda argv, **_k: (
            "main" if "branch" in argv and "--show-current" in argv else ""
        ),
    )
    assert mod._get_current_branch() == "main"


watcher = load_watcher_module()
