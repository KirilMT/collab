"""Tests for scripts/collab_git_hook.py."""

from __future__ import annotations

import io
import json
import os
import runpy
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.backend.unit.scripts._helpers import load_script_module

hook = load_script_module("collab_git_hook.py", "collab_git_hook_under_test")


def test_git_output_success(monkeypatch):
    monkeypatch.setattr(
        hook.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="a\n", stderr=""),
    )
    assert hook._git_output("status") == "a"


def test_git_output_error_raises(monkeypatch):
    monkeypatch.setattr(
        hook.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    try:
        hook._git_output("status")
        assert False
    except RuntimeError as exc:
        assert "boom" in str(exc)


def test_get_staged_files(monkeypatch):
    monkeypatch.setattr(hook, "_git_output", lambda *a: "a.py\n\n b.py \n")
    assert hook._get_staged_files() == ["a.py", "b.py"]


def test_read_pid_file_missing(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr("src.lock_client.PID_FILE", str(pid_file))
    assert hook._read_pid_file() is None


def test_read_pid_file_json_and_plain(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr("src.lock_client.PID_FILE", str(pid_file))

    pid_file.write_text(json.dumps({"pid": 123}), encoding="utf-8")
    assert hook._read_pid_file() == 123

    pid_file.write_text("456", encoding="utf-8")
    assert hook._read_pid_file() == 456

    pid_file.write_text("not-int", encoding="utf-8")
    assert hook._read_pid_file() is None


def test_read_pid_file_empty_and_oserror(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr("src.lock_client.PID_FILE", str(pid_file))

    pid_file.write_text("\n", encoding="utf-8")
    assert hook._read_pid_file() is None

    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *a, **k: (_ for _ in ()).throw(OSError("x")),
    )
    assert hook._read_pid_file() is None


def test_pid_is_running_psutil_branch(monkeypatch):
    class _Psutil:
        @staticmethod
        def pid_exists(_pid):
            return True

    monkeypatch.setitem(sys.modules, "psutil", _Psutil)
    assert hook._pid_is_running(1) is True


def test_pid_is_running_fallback_kill(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(pid_exists=lambda _pid: (_ for _ in ()).throw(RuntimeError())),
    )

    called = {"count": 0}

    def _fake_kill(pid, sig):
        called["count"] += 1
        if pid == 10:
            raise OSError("gone")

    monkeypatch.setattr(os, "kill", _fake_kill)
    assert hook._pid_is_running(9) is True
    assert hook._pid_is_running(10) is False
    assert called["count"] >= 2


def test_pid_is_running_permission_error(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(pid_exists=lambda _pid: (_ for _ in ()).throw(RuntimeError())),
    )

    def _fake_kill(_pid, _sig):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "kill", _fake_kill)
    assert hook._pid_is_running(123) is True


def test_watcher_pid(monkeypatch):
    monkeypatch.setattr(hook, "_read_pid_file", lambda: 77)
    monkeypatch.setattr(hook, "_pid_is_running", lambda pid: True)
    assert hook._watcher_pid() == 77

    monkeypatch.setattr(hook, "_pid_is_running", lambda pid: False)
    assert hook._watcher_pid() is None


def test_watcher_pid_none_branch(monkeypatch):
    monkeypatch.setattr(hook, "_read_pid_file", lambda: None)
    assert hook._watcher_pid() is None


def test_acquire_staged_no_files(monkeypatch):
    monkeypatch.setattr(hook, "_get_staged_files", lambda: [])
    assert hook.acquire_staged() == 0


def test_acquire_staged_skips_when_watcher_running(monkeypatch):
    monkeypatch.setattr(hook, "_get_staged_files", lambda: ["a.py"])
    monkeypatch.setattr(hook, "_watcher_pid", lambda: 999)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = hook.acquire_staged()
    assert rc == 0
    assert "Watcher running" in out.getvalue()


def test_acquire_staged_strict_failure(monkeypatch):
    monkeypatch.setattr(hook, "_get_staged_files", lambda: ["a.py"])
    monkeypatch.setattr(hook, "_watcher_pid", lambda: None)

    class _BrokenClient:
        def __init__(self):
            raise RuntimeError("lock backend down")

    monkeypatch.setattr("src.lock_client.LockClient", _BrokenClient)
    monkeypatch.setenv("LOCK_STRICT", "1")
    err = io.StringIO()
    with redirect_stderr(err):
        rc = hook.acquire_staged()
    assert rc == 1
    assert "lock check failed" in err.getvalue()


def test_acquire_staged_conflict(monkeypatch):
    monkeypatch.setattr(hook, "_get_staged_files", lambda: ["a.py"])
    monkeypatch.setattr(hook, "_watcher_pid", lambda: None)

    class _Client:
        def acquire_multiple(self, *_a, **_k):
            return False, ["a.py"], "conflict"

        def get_lock_status(self, _f):
            return {"locked_by": "dev1"}

    monkeypatch.setattr("src.lock_client.LockClient", lambda: _Client())

    err = io.StringIO()
    with redirect_stderr(err):
        rc = hook.acquire_staged()
    assert rc == 1
    assert "Commit blocked" in err.getvalue()
    assert "@dev1" in err.getvalue()


def test_acquire_staged_conflict_status_exception(monkeypatch):
    monkeypatch.setattr(hook, "_get_staged_files", lambda: ["a.py"])
    monkeypatch.setattr(hook, "_watcher_pid", lambda: None)

    class _Client:
        def acquire_multiple(self, *_a, **_k):
            return False, ["a.py"], "conflict"

        def get_lock_status(self, _f):
            raise RuntimeError("boom")

    monkeypatch.setattr("src.lock_client.LockClient", lambda: _Client())

    err = io.StringIO()
    with redirect_stderr(err):
        rc = hook.acquire_staged()
    assert rc == 1
    assert "@unknown" in err.getvalue()


def test_acquire_staged_success(monkeypatch):
    monkeypatch.setattr(hook, "_get_staged_files", lambda: ["a.py", "b.py"])
    monkeypatch.setattr(hook, "_watcher_pid", lambda: None)

    class _Client:
        def acquire_multiple(self, *_a, **_k):
            return True, [], "ok"

    monkeypatch.setattr("src.lock_client.LockClient", lambda: _Client())
    out = io.StringIO()
    with redirect_stdout(out):
        rc = hook.acquire_staged()
    assert rc == 0
    assert "Locks acquired" in out.getvalue()


def test_release_all_success_and_failure(monkeypatch):
    class _ClientOk:
        def release_all(self):
            return 3

    monkeypatch.setattr("src.lock_client.LockClient", lambda: _ClientOk())
    out = io.StringIO()
    with redirect_stdout(out):
        assert hook.release_all() == 0
    assert "Released 3" in out.getvalue()

    class _ClientBad:
        def __init__(self):
            raise RuntimeError("fail")

    monkeypatch.setattr("src.lock_client.LockClient", _ClientBad)
    err = io.StringIO()
    with redirect_stderr(err):
        assert hook.release_all() == 0
    assert "lock cleanup failed" in err.getvalue()


def test_main_command_dispatch(monkeypatch):
    monkeypatch.setattr(hook, "acquire_staged", lambda: 7)
    monkeypatch.setattr(hook, "release_all", lambda: 8)

    monkeypatch.setattr(sys, "argv", ["collab_git_hook.py", "acquire-staged"])
    assert hook.main() == 7

    monkeypatch.setattr(sys, "argv", ["collab_git_hook.py", "release-all"])
    assert hook.main() == 8

    monkeypatch.setattr(sys, "argv", ["collab_git_hook.py", "unknown"])
    err = io.StringIO()
    with redirect_stderr(err):
        assert hook.main() == 2
    assert "Unknown command" in err.getvalue()

    monkeypatch.setattr(sys, "argv", ["collab_git_hook.py"])
    out = io.StringIO()
    with redirect_stdout(out):
        assert hook.main() == 2
    assert "Usage" in out.getvalue()


def test_collab_git_hook_dunder_main(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["collab_git_hook.py", "acquire-staged"])
    monkeypatch.setattr(hook, "acquire_staged", lambda: 0)
    with pytest.raises(SystemExit) as exc:
        runpy.run_path("scripts/collab_git_hook.py", run_name="__main__")
    assert exc.value.code == 0
