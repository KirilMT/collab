"""Cover post-restart conflict abort branch in watcher prompt."""

from __future__ import annotations

import builtins
import sys

import pytest

from ._helpers import load_watcher_module

watcher = load_watcher_module()


def test_handle_post_restart_conflict_abort_choice(monkeypatch):
    shutdown_called = []

    class _Stdin:
        @staticmethod
        def isatty():
            return True

    monkeypatch.setattr(sys, "stdin", _Stdin())
    monkeypatch.setattr(builtins, "input", lambda _prompt="": "4")
    monkeypatch.setattr(
        watcher,
        "_graceful_shutdown",
        lambda: shutdown_called.append(True),
    )

    exits = []

    def _fake_exit(code):
        exits.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(sys, "exit", _fake_exit)

    with pytest.raises(SystemExit) as exc:
        watcher._handle_post_restart_conflict(
            client=object(),
            fp="collab/live_locks_watcher.py",
            lock_data={"owner": "dev", "branch": "main", "reason": "test"},
        )

    assert exc.value.code == 1
    assert exits == [1]
    assert shutdown_called == [True]


def test_handle_post_restart_conflict_dashboard_unavailable(monkeypatch, capsys):
    choices = iter(["3", "1"])

    class _Stdin:
        @staticmethod
        def isatty():
            return True

    monkeypatch.setattr(sys, "stdin", _Stdin())
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(choices))
    monkeypatch.setattr(watcher, "_dashboard_url", None)
    monkeypatch.setattr(watcher, "_start_dashboard_server", lambda: None)

    watcher._handle_post_restart_conflict(
        client=object(),
        fp="collab/live_locks_watcher.py",
        lock_data={"owner": "dev", "branch": "main", "reason": "test"},
    )

    out = capsys.readouterr().out
    assert "Dashboard unavailable" in out
