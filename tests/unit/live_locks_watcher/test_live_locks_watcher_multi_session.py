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

    mod._handle_multi_session_lock(client, "src/multi.py", "old-token")

    assert "update" in update_called
    assert "src/multi.py" in mod._local_owned_locks


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

    mod._handle_multi_session_lock(client, "src/multi.py", "old-token")

    assert "delete" in delete_called
    assert "src/multi.py" not in mod._local_owned_locks


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

    mod._handle_multi_session_lock(client, "src/multi.py", "old-token")

    assert not touched_db
    assert "src/multi.py" not in mod._local_owned_locks


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
        mod._handle_post_restart_conflict(None, "src/conflict.py", {"owner": "bob"})

    assert shutdown_called
    assert exit_called == [1]


def test_handle_post_restart_conflict_interactive_show_diff_then_continue(monkeypatch):
    """Choice 2 shows git diff, then choice 1 continues and tracks conflict."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: True})())

    # First select "show diff", then continue
    inputs = iter(["2", "1"])
    import builtins

    monkeypatch.setattr(builtins, "input", lambda p: next(inputs))
    monkeypatch.setattr(
        mod.subprocess,
        "check_output",
        lambda *a, **k: b"diff --git a/src/conflict.py b/src/conflict.py\n",
    )
    monkeypatch.setattr(mod, "_notify", lambda t, m: None)

    mod._active_conflicts.clear()
    mod._handle_post_restart_conflict(
        None,
        "src/conflict.py",
        {"owner": "bob", "branch": "main", "reason": "test"},
    )

    assert "src/conflict.py" in mod._active_conflicts


def test_handle_post_restart_conflict_interactive_diff_failure_then_continue(
    monkeypatch,
):
    """Choice 2 handles git diff failure and still allows continue."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: True})())

    # First select "show diff" (fails), then continue
    inputs = iter(["2", "1"])
    import builtins

    monkeypatch.setattr(builtins, "input", lambda p: next(inputs))

    def _boom(*args, **kwargs):
        raise RuntimeError("git diff unavailable")

    monkeypatch.setattr(mod.subprocess, "check_output", _boom)
    monkeypatch.setattr(mod, "_notify", lambda t, m: None)

    mod._active_conflicts.clear()
    mod._handle_post_restart_conflict(None, "src/conflict.py", {"owner": "bob"})

    assert "src/conflict.py" in mod._active_conflicts


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
    mod._handle_post_restart_conflict(None, "src/eof_conflict.py", {"owner": "bob"})

    assert "src/eof_conflict.py" in mod._active_conflicts


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

    mod._handle_multi_session_lock(FakeClient(), "src/multi.py", "old-token")
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

    mod._handle_multi_session_lock(FakeClient(), "src/err_update.py", "old-token")
    assert "src/err_update.py" in mod._local_owned_locks


def test_handle_multi_session_choice3_delete_exception(monkeypatch):
    """Delete failure on choice 3 should be caught without crashing."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(sys, "stdin", type("F", (), {"isatty": lambda s: True})())

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

    # no raise expected
    mod._handle_multi_session_lock(FakeClient(), "src/err_delete.py", "old-token")
