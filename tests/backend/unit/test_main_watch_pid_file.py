"""Targeted tests for watch command branches in src/main.py."""

from __future__ import annotations

import sys


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
