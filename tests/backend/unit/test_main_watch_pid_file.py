"""Targeted tests for watch command branches in collab/main.py."""

from __future__ import annotations

import sys
from contextlib import nullcontext


def test_run_cli_watch_pid_file_sets_namespace(monkeypatch):
    import collab.lock_client as lc
    import collab.main as main_mod

    class _Client:
        def __init__(self, local_only=False, agent_id=None, agent_label=None, **kwargs):
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

    # _run_cli mutates module-level lc.PID_FILE for the --pid-file namespace.
    # Register the current value with monkeypatch so it is restored on teardown
    # and the mutation does not leak into other tests.
    monkeypatch.setattr(lc, "PID_FILE", lc.PID_FILE)

    import collab.logging_config as logging_config

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
    import collab.lock_client as lc
    import collab.main as main_mod

    called = {"status": 0, "start": 0, "reconcile": 0}

    class _Client:
        def __init__(self, local_only=False, agent_id=None, agent_label=None, **kwargs):
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

    import collab.logging_config as logging_config

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
    import collab.lock_client as lc
    import collab.main as main_mod

    called = {"status": 0, "start": 0, "reconcile": 0}

    class _Client:
        def __init__(self, local_only=False, agent_id=None, agent_label=None, **kwargs):
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

    import collab.logging_config as logging_config

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


def _wire_cli_client(monkeypatch, client_cls):
    """Shared wiring so _run_cli() constructs *client_cls* without network/logging."""
    import collab.lock_client as lc
    import collab.logging_config as logging_config
    import collab.main as main_mod

    monkeypatch.setattr(
        main_mod, "setup_collab_logging", lambda **_k: None, raising=False
    )
    monkeypatch.setattr(
        main_mod, "_quiet_console_loggers", lambda: nullcontext(), raising=False
    )
    monkeypatch.setattr(main_mod, "LockClient", client_cls, raising=False)
    monkeypatch.setattr(lc, "LockClient", client_cls)
    monkeypatch.setattr(lc, "_quiet_console_loggers", lambda: nullcontext())
    monkeypatch.setattr(lc, "_COLLAB_ROOT", ".collab")
    monkeypatch.setattr(logging_config, "setup_collab_logging", lambda **_k: None)
    return main_mod


def test_run_cli_daemon_stop_worktree_flag_calls_unregister(monkeypatch):
    """`daemon-stop --worktree PATH` targets a specific worktree, not the current
    one."""
    calls = {"stop": 0, "unregister": []}

    class _Client:
        def __init__(self, local_only=False, **kwargs):
            self.local_only = local_only

        def daemon_stop(self):
            calls["stop"] += 1

        def worktree_unregister(self, path):
            calls["unregister"].append(path)
            return True

    main_mod = _wire_cli_client(monkeypatch, _Client)
    monkeypatch.setattr(
        sys, "argv", ["collab", "daemon-stop", "--worktree", r"C:\repo\wt-a"]
    )
    main_mod._run_cli()
    assert calls["stop"] == 0
    assert calls["unregister"] == [r"C:\repo\wt-a"]


def test_run_cli_daemon_stop_without_worktree_calls_daemon_stop(monkeypatch):
    """Plain `daemon-stop` still stops the current worktree in-process."""
    calls = {"stop": 0, "unregister": 0}

    class _Client:
        def __init__(self, local_only=False, **kwargs):
            self.local_only = local_only

        def daemon_stop(self):
            calls["stop"] += 1

        def worktree_unregister(self, path):
            calls["unregister"] += 1
            return True

    main_mod = _wire_cli_client(monkeypatch, _Client)
    monkeypatch.setattr(sys, "argv", ["collab", "daemon-stop"])
    main_mod._run_cli()
    assert calls["stop"] == 1
    assert calls["unregister"] == 0


def test_run_cli_worktree_unregister_command_exit_codes(monkeypatch):
    """`worktree-unregister PATH` forwards the path and exits 0 on success."""
    seen = {"path": None}

    class _Client:
        def __init__(self, local_only=False, **kwargs):
            self.local_only = local_only

        def worktree_unregister(self, path):
            seen["path"] = path
            return True

    main_mod = _wire_cli_client(monkeypatch, _Client)
    monkeypatch.setattr(sys, "argv", ["collab", "worktree-unregister", r"C:\repo\wt-b"])
    try:
        main_mod._run_cli()
    except SystemExit as exc:
        assert exc.code == 0
    assert seen["path"] == r"C:\repo\wt-b"


def test_run_cli_worktree_unregister_defaults_to_cwd_and_exit1(monkeypatch):
    """No path → defaults to cwd; a not-found result exits non-zero."""
    import os

    seen = {"path": None}

    class _Client:
        def __init__(self, local_only=False, **kwargs):
            self.local_only = local_only

        def worktree_unregister(self, path):
            seen["path"] = path
            return False

    main_mod = _wire_cli_client(monkeypatch, _Client)
    monkeypatch.setattr(sys, "argv", ["collab", "worktree-unregister"])
    code = 0
    try:
        main_mod._run_cli()
    except SystemExit as exc:
        code = exc.code
    assert seen["path"] == os.getcwd()
    assert code == 1


def test_is_truthy_env_uses_default_when_missing(monkeypatch):
    import collab.main as main_mod

    monkeypatch.delenv("COLLAB_TEST_BOOL", raising=False)
    assert main_mod._is_truthy_env("COLLAB_TEST_BOOL", default=True) is True
    assert main_mod._is_truthy_env("COLLAB_TEST_BOOL", default=False) is False


def test_ensure_watcher_running_non_target_command(monkeypatch):
    import collab.main as main_mod

    class _Client:
        pass

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("COLLAB_AUTO_START_WATCHER", "1")
    assert main_mod._ensure_watcher_running(_Client(), "release") is False


def test_ensure_watcher_running_skips_when_daemon_alive(monkeypatch):
    import collab.main as main_mod

    class _Client:
        def daemon_status(self):
            return True

        def daemon_start(self):
            raise AssertionError("daemon_start should not be called")

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("COLLAB_AUTO_START_WATCHER", "1")
    assert main_mod._ensure_watcher_running(_Client(), "active") is False


def test_ensure_watcher_running_start_failure(monkeypatch):
    import collab.main as main_mod

    class _Client:
        def daemon_status(self):
            raise RuntimeError("status failed")

        def daemon_start(self):
            raise RuntimeError("start failed")

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("COLLAB_AUTO_START_WATCHER", "1")
    assert main_mod._ensure_watcher_running(_Client(), "status") is False


def test_run_cli_active_reconcile_exception_is_non_fatal(monkeypatch, capsys):
    import collab.lock_client as lc
    import collab.main as main_mod

    called = {"start": 0}

    class _Client:
        def __init__(self, local_only=False, agent_id=None, agent_label=None, **kwargs):
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

    import collab.logging_config as logging_config

    monkeypatch.setattr(logging_config, "setup_collab_logging", lambda **_k: None)

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("COLLAB_AUTO_START_WATCHER", "1")
    monkeypatch.setattr(sys, "argv", ["collab", "active"])

    main_mod._run_cli()
    out = capsys.readouterr().out

    assert called["start"] == 1
    assert "No active locks." in out
