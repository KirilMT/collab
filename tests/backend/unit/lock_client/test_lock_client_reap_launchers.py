"""Tests for orphaned collab launcher-wrapper discovery and reaping.

Covers the Windows defense-in-depth that lets ``daemon_stop`` clean up ``collab.exe`` /
``collab-watcher.exe`` console-script wrappers which keep the virtualenv ``.exe`` image
locked after the underlying watcher has exited.
"""

import os
import sys
import types

from ._helpers import load_lock_client_module


def _new_client():
    mod = load_lock_client_module()
    return mod, mod.LockClient(local_only=True)


# ---------------------------------------------------------------------------
# _launcher_cmdline_in_namespace
# ---------------------------------------------------------------------------
def test_launcher_cmdline_in_namespace_rejects_non_watch():
    mod, client = _new_client()
    assert client._launcher_cmdline_in_namespace("") is False
    assert client._launcher_cmdline_in_namespace("collab.exe dashboard") is False


def test_launcher_cmdline_in_namespace_matches_pid_file(monkeypatch, tmp_path):
    mod, _ = _new_client()
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    client = mod.LockClient(local_only=True)

    matching = f"collab.exe watch --pid-file {pid_file}"
    assert client._launcher_cmdline_in_namespace(matching) is True

    other = tmp_path / "other.pid"
    non_matching = f"collab.exe watch --pid-file {other}"
    assert client._launcher_cmdline_in_namespace(non_matching) is False


# ---------------------------------------------------------------------------
# _discover_collab_launcher_pids
# ---------------------------------------------------------------------------
def test_discover_launchers_non_windows(monkeypatch):
    mod, client = _new_client()
    monkeypatch.setattr(mod.sys, "platform", "linux")
    assert client._discover_collab_launcher_pids() == []


def test_discover_launchers_psutil_path(monkeypatch, tmp_path):
    mod, _ = _new_client()
    monkeypatch.setattr(mod.sys, "platform", "win32")
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    class FakeProc:
        def __init__(self, pid, name, cmdline):
            self.info = {"pid": pid, "name": name, "cmdline": cmdline}

    def fake_iter(attrs=None):
        return [
            FakeProc(
                321, "collab.exe", ["collab.exe", "watch", "--pid-file", str(pid_file)]
            ),
            # Current process must be skipped.
            FakeProc(
                os.getpid(),
                "collab.exe",
                ["collab.exe", "watch", "--pid-file", str(pid_file)],
            ),
            # Not a launcher image name.
            FakeProc(
                999, "python.exe", ["python.exe", "watch", "--pid-file", str(pid_file)]
            ),
            # Launcher but no watch invocation.
            FakeProc(555, "collab.exe", ["collab.exe", "dashboard"]),
            # A process that raises while being inspected.
            _RaisingProc(),
        ]

    monkeypatch.setitem(
        sys.modules, "psutil", types.SimpleNamespace(process_iter=fake_iter)
    )
    client = mod.LockClient(local_only=True)
    assert client._discover_collab_launcher_pids() == [321]


class _RaisingProc:
    @property
    def info(self):
        raise RuntimeError("broken process entry")


def test_discover_launchers_tasklist_fallback(monkeypatch, tmp_path):
    mod, _ = _new_client()
    monkeypatch.setattr(mod.sys, "platform", "win32")
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    # Force the psutil fast-path to fail so the tasklist fallback runs.
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(
        mod.platform_probe,
        "iter_collab_launcher_pids",
        lambda: [321, os.getpid()],
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_get_cmdline_for_pid",
        staticmethod(lambda pid: f"collab.exe watch --pid-file {pid_file}"),
    )
    client = mod.LockClient(local_only=True)
    assert client._discover_collab_launcher_pids() == [321]


def test_discover_launchers_fallback_swallows_errors(monkeypatch, tmp_path):
    mod, _ = _new_client()
    monkeypatch.setattr(mod.sys, "platform", "win32")
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    monkeypatch.setitem(sys.modules, "psutil", None)

    def _boom():
        raise RuntimeError("tasklist failed")

    monkeypatch.setattr(mod.platform_probe, "iter_collab_launcher_pids", _boom)
    client = mod.LockClient(local_only=True)
    assert client._discover_collab_launcher_pids() == []


# ---------------------------------------------------------------------------
# _reap_collab_launchers
# ---------------------------------------------------------------------------
def test_reap_noop_non_windows(monkeypatch):
    mod, client = _new_client()
    monkeypatch.setattr(mod.sys, "platform", "linux")
    assert client._reap_collab_launchers() == 0


def test_reap_noop_in_test_mode(monkeypatch):
    mod, client = _new_client()
    monkeypatch.setattr(mod.sys, "platform", "win32")
    # _is_test_mode() is True under pytest, so reaping must be skipped.
    assert client._reap_collab_launchers() == 0


def test_reap_terminates_orphaned_launcher(monkeypatch):
    mod, client = _new_client()
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(mod, "_is_test_mode", lambda: False)
    monkeypatch.setattr(client, "_discover_collab_launcher_pids", lambda: [4321])

    calls = {"alive": 0}

    def fake_alive(pid):
        calls["alive"] += 1
        # Alive for the initial check, dead afterwards (terminated).
        return calls["alive"] == 1

    monkeypatch.setattr(mod.LockClient, "_is_process_alive", staticmethod(fake_alive))
    killed = []
    monkeypatch.setattr(
        mod.platform_probe,
        "taskkill_force",
        lambda pid, tree=False: killed.append((pid, tree)),
    )
    monkeypatch.setattr(mod.time, "sleep", lambda _x: None)

    assert client._reap_collab_launchers() == 1
    assert killed == [(4321, True)]


def test_reap_skips_self_and_parent(monkeypatch):
    mod, client = _new_client()
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(mod, "_is_test_mode", lambda: False)
    monkeypatch.setattr(client, "_discover_collab_launcher_pids", lambda: [os.getpid()])
    killed = []
    monkeypatch.setattr(
        mod.platform_probe,
        "taskkill_force",
        lambda *a, **k: killed.append(a),
    )
    assert client._reap_collab_launchers() == 0
    assert killed == []


def test_reap_skips_already_dead_launcher(monkeypatch):
    mod, client = _new_client()
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(mod, "_is_test_mode", lambda: False)
    monkeypatch.setattr(client, "_discover_collab_launcher_pids", lambda: [4321])
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: False)
    )
    killed = []
    monkeypatch.setattr(
        mod.platform_probe, "taskkill_force", lambda *a, **k: killed.append(a)
    )
    assert client._reap_collab_launchers() == 0
    assert killed == []


def test_reap_logs_when_launcher_survives(monkeypatch):
    mod, client = _new_client()
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(mod, "_is_test_mode", lambda: False)
    monkeypatch.setattr(client, "_discover_collab_launcher_pids", lambda: [4321])
    # Always alive: the wrapper stubbornly survives the reap attempt.
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    killed = []
    monkeypatch.setattr(
        mod.platform_probe,
        "taskkill_force",
        lambda pid, tree=False: killed.append(pid),
    )
    monkeypatch.setattr(mod.time, "sleep", lambda _x: None)
    assert client._reap_collab_launchers() == 0
    assert killed == [4321]


def test_reap_handles_getppid_failure(monkeypatch):
    mod, client = _new_client()
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(mod, "_is_test_mode", lambda: False)
    monkeypatch.setattr(client, "_discover_collab_launcher_pids", lambda: [])

    def _boom():
        raise OSError("no ppid")

    monkeypatch.setattr(mod.os, "getppid", _boom)
    assert client._reap_collab_launchers() == 0


def test_reap_handles_discovery_exception(monkeypatch):
    mod, client = _new_client()
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(mod, "_is_test_mode", lambda: False)

    def _boom():
        raise RuntimeError("discovery exploded")

    monkeypatch.setattr(client, "_discover_collab_launcher_pids", _boom)
    assert client._reap_collab_launchers() == 0


# ---------------------------------------------------------------------------
# daemon_stop integration with reaping
# ---------------------------------------------------------------------------
def test_daemon_stop_reports_reaped_launchers(monkeypatch, tmp_path, capsys):
    mod, _ = _new_client()
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod.LockClient, "_read_pid", staticmethod(lambda strict=False: None)
    )
    monkeypatch.setattr(mod.LockClient, "_discover_running_watchers", lambda self: [])
    monkeypatch.setattr(mod.LockClient, "_reap_collab_launchers", lambda self: 2)
    monkeypatch.setattr(mod.LockClient, "_remove_pid", staticmethod(lambda: None))

    client = mod.LockClient(local_only=True)
    client.daemon_stop()
    out = capsys.readouterr().out
    assert "Cleaned up 2 leftover collab launcher" in out
    assert "No running watcher found." not in out


def test_daemon_stop_discovery_exception_then_no_watcher(monkeypatch, tmp_path, capsys):
    mod, _ = _new_client()
    pid_file = tmp_path / ".daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod.LockClient, "_read_pid", staticmethod(lambda strict=False: None)
    )

    def _boom(self):
        raise RuntimeError("discovery failed")

    monkeypatch.setattr(mod.LockClient, "_discover_running_watchers", _boom)
    monkeypatch.setattr(mod.LockClient, "_reap_collab_launchers", lambda self: 0)
    monkeypatch.setattr(mod.LockClient, "_remove_pid", staticmethod(lambda: None))

    client = mod.LockClient(local_only=True)
    client.daemon_stop()
    out = capsys.readouterr().out
    assert "No running watcher found." in out
