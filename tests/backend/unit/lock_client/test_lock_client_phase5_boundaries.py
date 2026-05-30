"""Phase 5 lifecycle and subprocess boundary coverage for lock_client."""

from __future__ import annotations

import logging
import sys

import pytest

from src.errors import ConfigurationError, SubprocessSecurityError
from src.safe_subprocess import CaptureResult

from ._helpers import load_lock_client_module

mod = load_lock_client_module()


def test_require_client_raises_configuration_error():
    client = mod.LockClient(local_only=True)
    client._client = None
    with pytest.raises(ConfigurationError, match="not initialized"):
        client._require_client()


def test_get_current_branch_swallows_subprocess_security_error(monkeypatch):
    def _blocked(*_a, **_k):
        raise SubprocessSecurityError("git blocked")

    monkeypatch.setattr(mod.safe_subprocess, "capture", _blocked)
    assert mod.LockClient._get_current_branch() is None


def test_run_git_status_returns_empty_when_capture_not_ok(monkeypatch):
    monkeypatch.setattr(
        mod.safe_subprocess,
        "capture",
        lambda *a, **k: CaptureResult(
            argv=("git", "status", "--porcelain"),
            returncode=1,
            stdout=b"",
            stderr=b"",
        ),
    )
    assert mod.LockClient._run_git_status() == ""


def test_daemon_status_unavailable_on_pid_parse_error(capsys, monkeypatch, tmp_path):
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text("not-a-pid", encoding="utf-8")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    client = object.__new__(mod.LockClient)
    client.local_only = False
    assert mod.LockClient.daemon_status(client) is False
    out = capsys.readouterr().out
    assert "unavailable" in out


def test_daemon_status_warns_when_multiple_watchers_discovered(
    monkeypatch, tmp_path, caplog
):
    pid_file = tmp_path / ".daemon.pid"
    pid_file.write_text("99999", encoding="utf-8")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    client = object.__new__(mod.LockClient)
    client.local_only = True
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(
        mod.LockClient, "_read_pid", staticmethod(lambda strict=False: 99999)
    )
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_get_cmdline_for_pid",
        staticmethod(
            lambda pid: (
                "python -m src.lock_client watch"
                if pid in {111, 222}
                else "python unrelated.py"
            )
        ),
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_cmdline_matches_watcher",
        staticmethod(lambda cmd: "watch" in cmd),
    )
    monkeypatch.setattr(client, "_discover_running_watchers", lambda: [111, 222])
    assert mod.LockClient.daemon_status(client) is True
    assert any("multiple watcher" in r.message.lower() for r in caplog.records)


def test_daemon_start_refuses_insecure_spawn_windows(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "daemon.pid"))
    client = mod.LockClient(local_only=True)
    monkeypatch.setattr(
        mod.LockClient, "_read_pid", staticmethod(lambda strict=False: None)
    )
    monkeypatch.setattr(
        mod.LockClient, "_get_parent_ide_pid", lambda self: (None, None)
    )

    def _blocked(*_a, **_k):
        raise SubprocessSecurityError("spawn blocked")

    monkeypatch.setattr(mod.safe_subprocess, "spawn_background", _blocked)
    client.daemon_start()
    assert "Refusing to start watcher" in capsys.readouterr().out


def test_daemon_start_refuses_insecure_spawn_unix(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "daemon.pid"))
    client = mod.LockClient(local_only=True)
    monkeypatch.setattr(
        mod.LockClient, "_read_pid", staticmethod(lambda strict=False: None)
    )
    monkeypatch.setattr(
        mod.LockClient, "_get_parent_ide_pid", lambda self: (None, None)
    )

    def _blocked(*_a, **_k):
        raise SubprocessSecurityError("spawn blocked")

    monkeypatch.setattr(mod.safe_subprocess, "spawn_background", _blocked)
    client.daemon_start()
    assert "Refusing to start watcher" in capsys.readouterr().out


def test_discover_running_watchers_survives_tasklist_failure(monkeypatch):
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "psutil", raising=False)

    def _boom():
        raise RuntimeError("tasklist failed")

    monkeypatch.setattr(mod.platform_probe, "iter_tasklist_python_pids", _boom)
    client = mod.LockClient(local_only=True)
    assert client._discover_running_watchers() == []


def test_cleanup_orphaned_processes_survives_tasklist_failure(monkeypatch):
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "psutil", raising=False)

    def _boom():
        raise RuntimeError("tasklist failed")

    monkeypatch.setattr(mod.platform_probe, "iter_tasklist_python_pids", _boom)
    client = mod.LockClient(local_only=True)
    client.cleanup_orphaned_processes()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows taskkill path")
def test_terminate_process_swallows_taskkill_security_error(monkeypatch):
    def _blocked(*_a, **_k):
        raise SubprocessSecurityError("taskkill blocked")

    monkeypatch.setattr(mod.safe_subprocess, "run", _blocked)
    client = mod.LockClient(local_only=True)
    client._terminate_process(4242)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows taskkill path")
def test_terminate_process_swallows_taskkill_runtime_error(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("taskkill failed")

    monkeypatch.setattr(mod.safe_subprocess, "run", _boom)
    client = mod.LockClient(local_only=True)
    client._terminate_process(4242)
