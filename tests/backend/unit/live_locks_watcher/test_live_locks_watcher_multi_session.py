"""Multi-session and post-restart conflict handling tests for live_locks_watcher."""

from __future__ import annotations

import sys

import pytest

from ._helpers import load_watcher_module


def test_handle_multi_session_interactive_readopt_choice_1(monkeypatch):
    """Test interactive multi-session lock re-adopt (choice 1)."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: True})())

    import builtins

    monkeypatch.setattr(builtins, "input", lambda p: "1")

    update_called = []

    class FakeTable:
        def update(self, *args):
            update_called.append("update")
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return None

    class FakeClient:
        def table(self, name):
            return FakeTable()

    client = FakeClient()
    mod._local_owned_locks.clear()

    mod._handle_multi_session_lock(client, "collab/multi.py", "old-token")

    assert "update" in update_called
    assert "collab/multi.py" in mod._local_owned_locks


def test_handle_multi_session_interactive_release_choice_3(monkeypatch):
    """Test interactive multi-session lock release (choice 3)."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: True})())

    import builtins

    monkeypatch.setattr(builtins, "input", lambda p: "3")

    delete_called = []

    class FakeTable:
        def delete(self):
            delete_called.append("delete")
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return None

    class FakeClient:
        def table(self, name):
            return FakeTable()

    client = FakeClient()
    mod._local_owned_locks.clear()

    mod._handle_multi_session_lock(client, "collab/multi.py", "old-token")

    assert "delete" in delete_called
    assert "collab/multi.py" not in mod._local_owned_locks


def test_handle_multi_session_interactive_leave_choice_2(monkeypatch):
    """Test interactive multi-session lock leave (choice 2)."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: True})())

    import builtins

    monkeypatch.setattr(builtins, "input", lambda p: "2")

    touched_db = []

    class FakeTable:
        def update(self, *args):
            touched_db.append(True)
            return self

        def delete(self):
            touched_db.append(True)
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return None

    class FakeClient:
        def table(self, name):
            return FakeTable()

    client = FakeClient()
    mod._local_owned_locks.clear()

    mod._handle_multi_session_lock(client, "collab/multi.py", "old-token")

    assert not touched_db
    assert "collab/multi.py" not in mod._local_owned_locks


def test_handle_post_restart_conflict_interactive_abort_choice_4(monkeypatch):
    """Test interactive post-restart conflict aborts on choice 4."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: True})())

    inputs = iter(["4"])
    import builtins

    monkeypatch.setattr(builtins, "input", lambda p: next(inputs))

    exit_called = []

    def mock_exit(code):
        exit_called.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(sys, "exit", mock_exit)

    shutdown_called = []
    monkeypatch.setattr(mod, "_graceful_shutdown", lambda: shutdown_called.append(True))
    monkeypatch.setattr(mod, "_notify", lambda t, m: None)

    mod._active_conflicts.clear()

    with pytest.raises(SystemExit):
        mod._handle_post_restart_conflict(None, "collab/conflict.py", {"owner": "bob"})

    assert shutdown_called
    assert exit_called == [1]


def test_handle_post_restart_conflict_interactive_show_diff_then_continue(
    monkeypatch, capsys
):
    """Choice 2 runs git diff for the file and prints it; choice 1 then continues."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: True})())

    # First select "show diff", then continue
    inputs = iter(["2", "1"])
    import builtins

    monkeypatch.setattr(builtins, "input", lambda p: next(inputs))

    diff_calls = []

    class _Captured:
        stdout = b"diff --git a/collab/conflict.py b/collab/conflict.py\n+changed\n"
        returncode = 0
        timed_out = False

        @property
        def ok(self):
            return True

    def fake_capture(argv, **kwargs):
        diff_calls.append(list(argv))
        return _Captured()

    monkeypatch.setattr(mod.safe_subprocess, "capture", fake_capture)
    monkeypatch.setattr(mod, "_notify", lambda t, m: None)
    monkeypatch.setattr(mod, "_active_conflicts", set())

    mod._handle_post_restart_conflict(
        None,
        "collab/conflict.py",
        {"owner": "bob", "branch": "main", "reason": "test"},
    )

    # A git diff was invoked for the conflicting file and printed to the console.
    assert ["git", "diff", "collab/conflict.py"] in diff_calls
    out = capsys.readouterr().out
    assert "git diff collab/conflict.py" in out
    assert "collab/conflict.py" in mod._active_conflicts


def test_handle_post_restart_conflict_interactive_diff_failure_then_continue(
    monkeypatch, capsys
):
    """Choice 2 prints the git diff failure message and choice 1 still continues."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: True})())

    # First select "show diff" (fails), then continue
    inputs = iter(["2", "1"])
    import builtins

    monkeypatch.setattr(builtins, "input", lambda p: next(inputs))

    def boom_capture(argv, **kwargs):
        raise RuntimeError("git diff unavailable")

    monkeypatch.setattr(mod.safe_subprocess, "capture", boom_capture)
    monkeypatch.setattr(mod, "_notify", lambda t, m: None)
    monkeypatch.setattr(mod, "_active_conflicts", set())

    mod._handle_post_restart_conflict(None, "collab/conflict.py", {"owner": "bob"})

    out = capsys.readouterr().out
    assert "(git diff failed:" in out
    assert "collab/conflict.py" in mod._active_conflicts


def test_handle_post_restart_conflict_tty_input_eof_defaults_continue(monkeypatch):
    """EOF/interrupt in prompt defaults to choice 1 (continue)."""
    mod = load_watcher_module()
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: True})())

    import builtins

    def _raise_eof(prompt):
        raise EOFError()

    monkeypatch.setattr(builtins, "input", _raise_eof)
    monkeypatch.setattr(mod, "_notify", lambda t, m: None)

    mod._active_conflicts.clear()
    mod._handle_post_restart_conflict(None, "collab/eof_conflict.py", {"owner": "bob"})

    assert "collab/eof_conflict.py" in mod._active_conflicts


def test_handle_multi_session_interactive_eof_defaults_leave(monkeypatch):
    """EOF in interactive prompt should fall back to leave-lock option."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: True})())

    import builtins

    def _raise_eof(prompt):
        raise EOFError()

    monkeypatch.setattr(builtins, "input", _raise_eof)
    touched = []

    class FakeTable:
        def update(self, *args):
            touched.append("update")
            return self

        def delete(self):
            touched.append("delete")
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return None

    class FakeClient:
        def table(self, name):
            return FakeTable()

    mod._handle_multi_session_lock(FakeClient(), "collab/multi.py", "old-token")
    assert touched == []


def test_handle_multi_session_choice1_update_exception(monkeypatch):
    """Update failure on choice 1 should be caught and still re-adopt locally."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: True})())

    import builtins

    monkeypatch.setattr(builtins, "input", lambda p: "1")
    mod._local_owned_locks.clear()

    class FakeTable:
        def update(self, *args):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            raise RuntimeError("db down")

    class FakeClient:
        def table(self, name):
            return FakeTable()

    mod._handle_multi_session_lock(FakeClient(), "collab/err_update.py", "old-token")
    assert "collab/err_update.py" in mod._local_owned_locks


def test_handle_multi_session_choice3_delete_exception(monkeypatch, caplog):
    """Delete failure on choice 3 is logged and the lock is not adopted locally."""
    import logging

    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: True})())
    monkeypatch.setattr(mod, "_local_owned_locks", set())

    import builtins

    monkeypatch.setattr(builtins, "input", lambda p: "3")

    class FakeTable:
        def delete(self):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            raise RuntimeError("db down")

    class FakeClient:
        def table(self, name):
            return FakeTable()

    with caplog.at_level(logging.ERROR, logger=mod.logger.name):
        # no raise expected
        mod._handle_multi_session_lock(
            FakeClient(), "collab/err_delete.py", "old-token"
        )

    assert "collab/err_delete.py" not in mod._local_owned_locks
    assert "Failed to release lock for collab/err_delete.py" in caplog.text
