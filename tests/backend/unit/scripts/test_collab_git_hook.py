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
    monkeypatch.setattr("collab.lock_client.PID_FILE", str(pid_file))
    assert hook._read_pid_file() is None


def test_read_pid_file_json_and_plain(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr("collab.lock_client.PID_FILE", str(pid_file))

    pid_file.write_text(json.dumps({"pid": 123}), encoding="utf-8")
    assert hook._read_pid_file() == 123

    pid_file.write_text("456", encoding="utf-8")
    assert hook._read_pid_file() == 456

    pid_file.write_text("not-int", encoding="utf-8")
    assert hook._read_pid_file() is None

    # Malformed JSON object -> JSONDecodeError branch returns None.
    pid_file.write_text("{bad-json", encoding="utf-8")
    assert hook._read_pid_file() is None

    # Well-formed JSON with a non-int "pid" -> isinstance guard returns None.
    pid_file.write_text(json.dumps({"pid": "abc"}), encoding="utf-8")
    assert hook._read_pid_file() is None


def test_read_pid_file_empty_and_oserror(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr("collab.lock_client.PID_FILE", str(pid_file))

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
    err = io.StringIO()
    with redirect_stderr(err):
        rc = hook.acquire_staged()
    assert rc == 0
    assert "Watcher running" in err.getvalue()


def test_acquire_staged_strict_failure(monkeypatch):
    monkeypatch.setattr(hook, "_get_staged_files", lambda: ["a.py"])
    monkeypatch.setattr(hook, "_watcher_pid", lambda: None)

    class _BrokenClient:
        def __init__(self):
            raise RuntimeError("lock backend down")

    monkeypatch.setattr("collab.lock_client.LockClient", _BrokenClient)
    monkeypatch.setenv("LOCK_STRICT", "1")
    err = io.StringIO()
    with redirect_stderr(err):
        rc = hook.acquire_staged()
    assert rc == 1
    assert "lock check failed" in err.getvalue()


def test_acquire_staged_non_strict_failure_returns_zero(monkeypatch):
    """Lock backend failure with LOCK_STRICT unset must not block the commit."""
    monkeypatch.setattr(hook, "_get_staged_files", lambda: ["a.py"])
    monkeypatch.setattr(hook, "_watcher_pid", lambda: None)

    class _BrokenClient:
        def __init__(self):
            raise RuntimeError("lock backend down")

    monkeypatch.setattr("collab.lock_client.LockClient", _BrokenClient)
    monkeypatch.delenv("LOCK_STRICT", raising=False)
    err = io.StringIO()
    with redirect_stderr(err):
        rc = hook.acquire_staged()
    assert rc == 0
    assert "lock check failed" in err.getvalue()


def test_acquire_staged_conflict(monkeypatch):
    monkeypatch.setattr(hook, "_get_staged_files", lambda: ["a.py"])
    monkeypatch.setattr(hook, "_watcher_pid", lambda: None)

    class _Client:
        def acquire_multiple(self, *_a, **_k):
            return False, ["a.py"], "conflict"

        def get_lock_status(self, _f):
            return {"locked_by": "dev1"}

    monkeypatch.setattr("collab.lock_client.LockClient", lambda: _Client())

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

    monkeypatch.setattr("collab.lock_client.LockClient", lambda: _Client())

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

    monkeypatch.setattr("collab.lock_client.LockClient", lambda: _Client())
    err = io.StringIO()
    with redirect_stderr(err):
        rc = hook.acquire_staged()
    assert rc == 0
    out = err.getvalue()
    assert "Checking locks for 2 staged files" in out
    assert "Locks acquired" in out


def test_hook_log_flushes_stderr(monkeypatch):
    flushed = {"count": 0}
    real_stderr = sys.stderr

    class _Stderr:
        def write(self, text):
            real_stderr.write(text)

        def flush(self):
            flushed["count"] += 1

    monkeypatch.setattr(sys, "stderr", _Stderr())
    hook._hook_log("[collab] probe")
    assert flushed["count"] >= 1


def test_release_all_success_and_failure(monkeypatch):
    class _ClientOk:
        def release_all(self):
            return 3

    monkeypatch.setattr("collab.lock_client.LockClient", lambda: _ClientOk())
    err = io.StringIO()
    with redirect_stderr(err):
        assert hook.release_all() == 0
    assert "Released 3" in err.getvalue()

    class _ClientBad:
        def __init__(self):
            raise RuntimeError("fail")

    monkeypatch.setattr("collab.lock_client.LockClient", _ClientBad)
    err = io.StringIO()
    with redirect_stderr(err):
        assert hook.release_all() == 0
    assert "lock cleanup failed" in err.getvalue()


def test_validate_and_release_validation_failure_keeps_locks(monkeypatch):
    """A non-zero validation exit keeps locks and propagates the return code."""
    captured = {}

    def _fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        return SimpleNamespace(returncode=3)

    monkeypatch.setattr(hook.subprocess, "run", _fake_run)
    release_called = {"n": 0}
    monkeypatch.setattr(hook, "release_all", lambda: release_called.__setitem__("n", 1))

    err = io.StringIO()
    with redirect_stderr(err):
        rc = hook.validate_and_release()

    assert rc == 3
    assert release_called["n"] == 0  # locks kept on validation failure
    assert "keeping locks active" in err.getvalue()
    assert captured["argv"][0] == sys.executable
    assert captured["argv"][-1] == "--quick"
    assert str(captured["argv"][1]).endswith("validate_code.py")


def test_validate_and_release_success_releases_locks(monkeypatch):
    """A successful validation delegates to release_all and returns its code."""
    monkeypatch.setattr(
        hook.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0)
    )
    release_called = {"n": 0}

    def _fake_release_all():
        release_called["n"] += 1
        return 0

    monkeypatch.setattr(hook, "release_all", _fake_release_all)

    assert hook.validate_and_release() == 0
    assert release_called["n"] == 1


def test_main_command_dispatch(monkeypatch):
    monkeypatch.setattr(hook, "acquire_staged", lambda: 7)
    monkeypatch.setattr(hook, "release_all", lambda: 8)
    monkeypatch.setattr(hook, "validate_and_release", lambda: 9)

    monkeypatch.setattr(sys, "argv", ["collab_git_hook.py", "acquire-staged"])
    assert hook.main() == 7

    monkeypatch.setattr(sys, "argv", ["collab_git_hook.py", "release-all"])
    assert hook.main() == 8

    monkeypatch.setattr(sys, "argv", ["collab_git_hook.py", "validate-and-release"])
    assert hook.main() == 9

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
    # Execute the real ``if __name__ == "__main__": raise SystemExit(main())``
    # guard via an absolute, CWD-independent path. ``runpy`` runs a *fresh*
    # module, so patching ``hook.acquire_staged`` would not take effect there.
    # Instead, force "no staged files" by patching the shared ``subprocess``
    # module (the fresh module imports the same cached object), which drives
    # acquire_staged() down its zero-staged-files path -> exit code 0 without
    # touching git or the real LockClient.
    root = Path(__file__).resolve().parents[4]
    script = root / "scripts" / "collab_git_hook.py"

    monkeypatch.setattr(
        hook.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(sys, "argv", ["collab_git_hook.py", "acquire-staged"])

    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script), run_name="__main__")
    assert exc.value.code == 0
