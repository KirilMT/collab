"""Phase 5 lifecycle and subprocess boundary coverage for lock_client."""

from __future__ import annotations

import logging
import sys
from unittest import mock

import pytest

from collab.errors import (
    ConfigurationError,
    LockServiceUnavailableError,
    SubprocessSecurityError,
)
from collab.safe_subprocess import CaptureResult

from ._helpers import FakeResponse, load_lock_client_module, make_create_client

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
    assert mod.LockClient._run_git_status() == ("", False)


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
                "python -m collab.lock_client watch"
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


def test_lock_service_hostname_empty_and_invalid(monkeypatch):
    monkeypatch.setattr(mod, "_current_supabase_url", lambda: "")
    assert mod._lock_service_hostname() == ""

    def _boom(_url):
        raise ValueError("bad url")

    monkeypatch.setattr(mod, "_current_supabase_url", lambda: "not-a-url")
    monkeypatch.setattr(mod, "urlparse", _boom)
    assert mod._lock_service_hostname() == ""


def test_ensure_lock_service_reachable_configuration_errors(monkeypatch):
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.setattr(mod, "SUPABASE_URL", None)
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", None)
    with pytest.raises(ConfigurationError, match="not configured"):
        mod._ensure_lock_service_reachable()

    monkeypatch.setenv("SUPABASE_URL", "://no-host")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "key")
    with pytest.raises(ConfigurationError, match="invalid"):
        mod._ensure_lock_service_reachable()


def test_ensure_lock_service_reachable_socket_failure(monkeypatch):
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setenv("SUPABASE_URL", "https://offline.example")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "key")

    def _fail(*_a, **_k):
        raise OSError("network down")

    monkeypatch.setattr(mod.socket, "create_connection", _fail)
    with pytest.raises(LockServiceUnavailableError, match="Cannot reach lock service"):
        mod._ensure_lock_service_reachable()


def test_is_lock_service_error_typed_and_token_match(monkeypatch):
    assert mod._is_lock_service_error(LockServiceUnavailableError("down"))
    assert mod._is_lock_service_error(RuntimeError("connection refused"))

    client = mod.LockClient(developer_id="alice", local_only=True)
    token = client._get_session_token()
    assert client._is_same_machine_token(token)

    monkeypatch.setattr(
        mod.safe_subprocess,
        "capture",
        lambda *a, **k: CaptureResult(
            argv=("git", "config", "user.name"),
            returncode=0,
            stdout=b"Git User\n",
            stderr=b"",
        ),
    )
    assert client._is_same_machine_token(token)


def test_get_git_username_and_branch_from_capture(monkeypatch):
    monkeypatch.setattr(
        mod.safe_subprocess,
        "capture",
        lambda argv, **k: CaptureResult(
            argv=tuple(argv),
            returncode=0,
            stdout=(b"branch-name\n" if "branch" in argv else b"Config User\n"),
            stderr=b"",
        ),
    )
    assert mod.LockClient._get_git_username() == "Config User"
    assert mod.LockClient._get_current_branch() == "branch-name"


def test_acquire_returns_false_when_lock_service_unreachable(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    f = tmp_path / "collab" / "app.py"
    f.parent.mkdir(parents=True)
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(
        mod,
        "_ensure_lock_service_reachable",
        lambda: (_ for _ in ()).throw(
            LockServiceUnavailableError("offline", detail="probe failed")
        ),
    )
    client = mod.LockClient(developer_id="tester")
    ok, msg = client.acquire(str(f))
    assert ok is False
    assert "offline" in msg
    assert "probe failed" in msg


def test_active_sandbox_returns_empty_on_supabase_error(monkeypatch):
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setenv("SUPABASE_URL", "http://localhost:54321")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "_ensure_lock_service_reachable", lambda: None)
    err_response = FakeResponse(status=500, data=None, error="stub unavailable")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(err_response)
    )
    client = mod.LockClient(developer_id="tester")
    assert client.active() == []


def test_release_all_returns_zero_when_active_unavailable(monkeypatch):
    client = mod.LockClient(local_only=True)
    monkeypatch.setattr(
        client,
        "active",
        lambda: (_ for _ in ()).throw(LockServiceUnavailableError("down")),
    )
    assert client.release_all() == 0


def test_run_git_status_timeout_and_failure(monkeypatch):
    monkeypatch.setattr(
        mod.safe_subprocess,
        "capture",
        lambda *a, **k: CaptureResult(
            argv=("git", "status", "--porcelain"),
            returncode=-1,
            stdout=b"",
            stderr=b"",
            timed_out=True,
        ),
    )
    assert mod.LockClient._run_git_status() == ("", False)

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
    assert mod.LockClient._run_git_status() == ("", False)


def test_run_git_status_preserves_leading_status_space(monkeypatch):
    """Regression: the leading porcelain status space must survive.

    ``git status --porcelain`` prefixes each line with a 2-column status field (XY). For
    worktree-only changes the first column is a space (e.g. ``" M path"``). A full
    ``.strip()`` of the output blob would remove the leading space of the FIRST line,
    shifting the ``line[3:]`` parse and dropping the first character of that path
    (``collab/...`` -> ``ollab/...``).
    """
    raw = b" M collab/dashboard_server.py\n M pyproject.toml\n"
    monkeypatch.setattr(
        mod.safe_subprocess,
        "capture",
        lambda *a, **k: CaptureResult(
            argv=("git", "status", "--porcelain"),
            returncode=0,
            stdout=raw,
            stderr=b"",
        ),
    )

    out, _ok = mod.LockClient._run_git_status()
    first_line = out.splitlines()[0]

    # Leading status space must be intact so the fixed-width parse is correct.
    assert first_line.startswith(" M ")
    assert (
        mod.LockClient._parse_git_status_path(first_line)
        == "collab/dashboard_server.py"
    )


def test_reconcile_lock_service_unavailable_fallbacks(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    f = tmp_path / "dirty.py"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    client = mod.LockClient(developer_id="tester")

    def _git_modified_fail():
        raise RuntimeError("git exploded")

    def _active_unavailable():
        raise LockServiceUnavailableError("down")

    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", _git_modified_fail)
    monkeypatch.setattr(client, "active", _active_unavailable)
    assert client._reconcile() == set()

    monkeypatch.setattr(
        client,
        "_get_modified_and_unpushed_files",
        lambda: ([str(f)], True),
    )
    monkeypatch.setattr(client, "active", _active_unavailable)
    assert client._reconcile() == {str(f)}


def test_daemon_status_local_only_multiple_discovered_watchers(
    monkeypatch, caplog, tmp_path
):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    client = mod.LockClient(local_only=True)
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(
        mod.LockClient, "_read_pid", staticmethod(lambda strict=False: None)
    )
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    monkeypatch.setattr(
        client,
        "_discover_running_watchers",
        lambda: [101, 202],
    )
    monkeypatch.setattr(
        client,
        "_get_cmdline_for_pid",
        lambda pid: "python -m collab.lock_client watch",
    )
    assert client.daemon_status() is True
    assert any("multiple watcher" in r.message.lower() for r in caplog.records)


def test_discover_running_watchers_win32_tasklist_fallback(monkeypatch):
    monkeypatch.setattr(mod.sys, "platform", "win32")
    collab_root = mod._COLLAB_ROOT

    import psutil as _psutil

    def _boom(*_a, **_k):
        raise RuntimeError("psutil unavailable")

    monkeypatch.setattr(_psutil, "process_iter", _boom)
    monkeypatch.setattr(mod.platform_probe, "iter_tasklist_python_pids", lambda: [555])
    client = mod.LockClient(local_only=True)
    monkeypatch.setattr(
        client,
        "_get_cmdline_for_pid",
        lambda pid: (
            f"python -m collab.lock_client watch {collab_root}" if pid == 555 else None
        ),
    )
    monkeypatch.setattr(
        client,
        "_cmdline_matches_watcher",
        lambda cmd: "watch" in cmd,
    )
    monkeypatch.setattr(
        client,
        "_cmdline_matches_current_pid_namespace",
        lambda cmd: True,
    )
    found = client._discover_running_watchers()
    assert 555 in found


def test_discover_running_watchers_psutil_failure_unix(monkeypatch):
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "psutil", mock.MagicMock())
    sys.modules["psutil"].process_iter.side_effect = RuntimeError("no psutil")

    def _ps_csv():
        return "777 python -m collab.lock_client watch\n"

    monkeypatch.setattr(mod.platform_probe, "ps_pid_cmd_csv", _ps_csv)
    client = mod.LockClient(local_only=True)
    monkeypatch.setattr(
        client,
        "_get_cmdline_for_pid",
        lambda pid: "python -m collab.lock_client watch .collab",
    )
    monkeypatch.setattr(client, "_cmdline_matches_watcher", lambda cmd: True)
    monkeypatch.setattr(
        client, "_cmdline_matches_current_pid_namespace", lambda cmd: True
    )
    assert 777 in client._discover_running_watchers()
