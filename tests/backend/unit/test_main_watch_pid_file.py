"""Targeted tests for watch command branches in src/main.py."""

from __future__ import annotations

import sys
from contextlib import nullcontext


def test_run_cli_watch_pid_file_sets_namespace(monkeypatch):
    import src.lock_client as lc
    import src.main as main_mod

    class _Client:
        def __init__(self, local_only=False):
            self.local_only = local_only

        def watch(self, **kwargs):
            called["kwargs"] = kwargs

    called = {"kwargs": None}

    monkeypatch.setattr(
        main_mod,
        "setup_collab_logging",
        lambda **_k: None,
        raising=False,
    )
    monkeypatch.setattr(main_mod, "_quiet_console_loggers", lambda: None, raising=False)
    monkeypatch.setattr(main_mod, "LockClient", _Client, raising=False)

    # _run_cli lazily imports these symbols from lock_client each invocation.
    monkeypatch.setattr(lc, "LockClient", _Client)
    monkeypatch.setattr(lc, "_quiet_console_loggers", lambda: None)
    monkeypatch.setattr(lc, "_COLLAB_ROOT", ".collab")

    import src.logging_config as logging_config

    monkeypatch.setattr(logging_config, "setup_collab_logging", lambda **_k: None)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collab",
            "watch",
            "--pid-file",
            "tmp.daemon.pid",
            "--interval",
            "2",
            "--timeout",
            "1",
        ],
    )

    main_mod._run_cli()
    assert lc.PID_FILE == "tmp.daemon.pid"
    assert called["kwargs"] is not None
    assert called["kwargs"]["interval"] == 2


def test_run_cli_active_auto_starts_and_reconciles(monkeypatch, capsys):
    import src.lock_client as lc
    import src.main as main_mod

    called = {"status": 0, "start": 0, "reconcile": 0}

    class _Client:
        def __init__(self, local_only=False):
            self.local_only = local_only

        def daemon_status(self):
            called["status"] += 1
            return False

        def daemon_start(self):
            called["start"] += 1

        def _reconcile(self):
            called["reconcile"] += 1

        def active(self):
            return []

    monkeypatch.setattr(
        main_mod,
        "setup_collab_logging",
        lambda **_k: None,
        raising=False,
    )
    monkeypatch.setattr(
        main_mod,
        "_quiet_console_loggers",
        lambda: nullcontext(),
        raising=False,
    )
    monkeypatch.setattr(main_mod, "LockClient", _Client, raising=False)

    monkeypatch.setattr(lc, "LockClient", _Client)
    monkeypatch.setattr(lc, "_quiet_console_loggers", lambda: nullcontext())
    monkeypatch.setattr(lc, "_COLLAB_ROOT", ".collab")

    import src.logging_config as logging_config

    monkeypatch.setattr(logging_config, "setup_collab_logging", lambda **_k: None)

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("COLLAB_AUTO_START_WATCHER", "1")
    monkeypatch.setattr(sys, "argv", ["collab", "active"])

    main_mod._run_cli()
    out = capsys.readouterr().out

    assert called["status"] == 1
    assert called["start"] == 1
    assert called["reconcile"] == 1
    assert "No active locks." in out


def test_run_cli_active_auto_start_can_be_disabled(monkeypatch, capsys):
    import src.lock_client as lc
    import src.main as main_mod

    called = {"status": 0, "start": 0, "reconcile": 0}

    class _Client:
        def __init__(self, local_only=False):
            self.local_only = local_only

        def daemon_status(self):
            called["status"] += 1
            return False

        def daemon_start(self):
            called["start"] += 1

        def _reconcile(self):
            called["reconcile"] += 1

        def active(self):
            return []

    monkeypatch.setattr(
        main_mod,
        "setup_collab_logging",
        lambda **_k: None,
        raising=False,
    )
    monkeypatch.setattr(
        main_mod,
        "_quiet_console_loggers",
        lambda: nullcontext(),
        raising=False,
    )
    monkeypatch.setattr(main_mod, "LockClient", _Client, raising=False)

    monkeypatch.setattr(lc, "LockClient", _Client)
    monkeypatch.setattr(lc, "_quiet_console_loggers", lambda: nullcontext())
    monkeypatch.setattr(lc, "_COLLAB_ROOT", ".collab")

    import src.logging_config as logging_config

    monkeypatch.setattr(logging_config, "setup_collab_logging", lambda **_k: None)

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("COLLAB_AUTO_START_WATCHER", "0")
    monkeypatch.setattr(sys, "argv", ["collab", "active"])

    main_mod._run_cli()
    out = capsys.readouterr().out

    assert called["status"] == 0
    assert called["start"] == 0
    assert called["reconcile"] == 0
    assert "No active locks." in out


def test_is_truthy_env_uses_default_when_missing(monkeypatch):
    import src.main as main_mod

    monkeypatch.delenv("COLLAB_TEST_BOOL", raising=False)
    assert main_mod._is_truthy_env("COLLAB_TEST_BOOL", default=True) is True
    assert main_mod._is_truthy_env("COLLAB_TEST_BOOL", default=False) is False


def test_ensure_watcher_running_non_target_command(monkeypatch):
    import src.main as main_mod

    class _Client:
        pass

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("COLLAB_AUTO_START_WATCHER", "1")
    assert main_mod._ensure_watcher_running(_Client(), "release") is False


def test_ensure_watcher_running_skips_when_daemon_alive(monkeypatch):
    import src.main as main_mod

    class _Client:
        def daemon_status(self):
            return True

        def daemon_start(self):
            raise AssertionError("daemon_start should not be called")

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("COLLAB_AUTO_START_WATCHER", "1")
    assert main_mod._ensure_watcher_running(_Client(), "active") is False


def test_ensure_watcher_running_start_failure(monkeypatch):
    import src.main as main_mod

    class _Client:
        def daemon_status(self):
            raise RuntimeError("status failed")

        def daemon_start(self):
            raise RuntimeError("start failed")

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("COLLAB_AUTO_START_WATCHER", "1")
    assert main_mod._ensure_watcher_running(_Client(), "status") is False


def test_run_cli_active_reconcile_exception_is_non_fatal(monkeypatch, capsys):
    import src.lock_client as lc
    import src.main as main_mod

    called = {"start": 0}

    class _Client:
        def __init__(self, local_only=False):
            self.local_only = local_only

        def daemon_status(self):
            return False

        def daemon_start(self):
            called["start"] += 1

        def _reconcile(self):
            raise RuntimeError("reconcile boom")

        def active(self):
            return []

    monkeypatch.setattr(
        main_mod,
        "setup_collab_logging",
        lambda **_k: None,
        raising=False,
    )
    monkeypatch.setattr(
        main_mod,
        "_quiet_console_loggers",
        lambda: nullcontext(),
        raising=False,
    )
    monkeypatch.setattr(main_mod, "LockClient", _Client, raising=False)

    monkeypatch.setattr(lc, "LockClient", _Client)
    monkeypatch.setattr(lc, "_quiet_console_loggers", lambda: nullcontext())
    monkeypatch.setattr(lc, "_COLLAB_ROOT", ".collab")

    import src.logging_config as logging_config

    monkeypatch.setattr(logging_config, "setup_collab_logging", lambda **_k: None)

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("COLLAB_AUTO_START_WATCHER", "1")
    monkeypatch.setattr(sys, "argv", ["collab", "active"])

    main_mod._run_cli()
    out = capsys.readouterr().out

    assert called["start"] == 1
    assert "No active locks." in out
