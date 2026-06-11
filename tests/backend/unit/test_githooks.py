"""Unit tests for collab.githooks (packaged git hook runtime)."""

from __future__ import annotations

import io
import json
import os
import runpy
import sys
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace

import pytest

from collab import githooks


def test_run_git_success(monkeypatch, tmp_path):
    captured = {}

    def _fake_capture(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        return SimpleNamespace(returncode=0, stdout=b"top\n")

    monkeypatch.setattr(githooks.safe_subprocess, "capture", _fake_capture)
    rc, out = githooks._run_git(tmp_path, "rev-parse", "--show-toplevel")
    assert (rc, out) == (0, "top")
    assert captured["argv"] == ["git", "rev-parse", "--show-toplevel"]
    assert captured["cwd"] == str(tmp_path)


def test_run_git_handles_exception(monkeypatch):
    def _boom(*_a, **_k):
        raise OSError("git missing")

    monkeypatch.setattr(githooks.safe_subprocess, "capture", _boom)
    assert githooks._run_git(None, "status") == (1, "")


def test_git_toplevel_success(monkeypatch, tmp_path):
    monkeypatch.setattr(githooks, "_run_git", lambda *a: (0, str(tmp_path)))
    assert githooks._git_toplevel() == Path(str(tmp_path))


def test_git_toplevel_fallback_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(githooks, "_run_git", lambda *a: (128, ""))
    assert githooks._git_toplevel(start=tmp_path) == tmp_path


def test_git_toplevel_fallback_to_cwd(monkeypatch):
    monkeypatch.setattr(githooks, "_run_git", lambda *a: (1, ""))
    assert githooks._git_toplevel() == Path.cwd()


def test_git_output_success(monkeypatch, tmp_path):
    monkeypatch.setattr(githooks, "_run_git", lambda *a: (0, "line"))
    assert githooks._git_output(tmp_path, "status") == "line"


def test_git_output_error(monkeypatch, tmp_path):
    monkeypatch.setattr(githooks, "_run_git", lambda *a: (1, "boom"))
    with pytest.raises(RuntimeError, match="boom"):
        githooks._git_output(tmp_path, "status")


def test_get_staged_files(monkeypatch, tmp_path):
    monkeypatch.setattr(githooks, "_git_output", lambda *a: "a.py\n\n b.py \n")
    assert githooks._get_staged_files(tmp_path) == ["a.py", "b.py"]


def test_read_pid_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("collab.lock_client.PID_FILE", str(tmp_path / "daemon.pid"))
    assert githooks._read_pid_file() is None


def test_read_pid_file_json_and_plain(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr("collab.lock_client.PID_FILE", str(pid_file))

    pid_file.write_text(json.dumps({"pid": 123}), encoding="utf-8")
    assert githooks._read_pid_file() == 123

    pid_file.write_text("456", encoding="utf-8")
    assert githooks._read_pid_file() == 456

    pid_file.write_text("not-int", encoding="utf-8")
    assert githooks._read_pid_file() is None

    pid_file.write_text("{bad json", encoding="utf-8")
    assert githooks._read_pid_file() is None


def test_read_pid_file_empty_and_oserror(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr("collab.lock_client.PID_FILE", str(pid_file))

    pid_file.write_text("\n", encoding="utf-8")
    assert githooks._read_pid_file() is None

    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *a, **k: (_ for _ in ()).throw(OSError("x")),
    )
    assert githooks._read_pid_file() is None


def test_pid_is_running_psutil(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "psutil", SimpleNamespace(pid_exists=lambda _pid: True)
    )
    assert githooks._pid_is_running(1) is True


def test_pid_is_running_fallback_kill(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(pid_exists=lambda _pid: (_ for _ in ()).throw(RuntimeError())),
    )

    def _fake_kill(pid, _sig):
        if pid == 10:
            raise OSError("gone")

    monkeypatch.setattr(os, "kill", _fake_kill)
    assert githooks._pid_is_running(9) is True
    assert githooks._pid_is_running(10) is False


def test_pid_is_running_permission_error(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(pid_exists=lambda _pid: (_ for _ in ()).throw(RuntimeError())),
    )

    def _fake_kill(_pid, _sig):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "kill", _fake_kill)
    assert githooks._pid_is_running(123) is True


def test_watcher_pid(monkeypatch):
    monkeypatch.setattr(githooks, "_read_pid_file", lambda: 77)
    monkeypatch.setattr(githooks, "_pid_is_running", lambda pid: True)
    assert githooks._watcher_pid() == 77

    monkeypatch.setattr(githooks, "_pid_is_running", lambda pid: False)
    assert githooks._watcher_pid() is None

    monkeypatch.setattr(githooks, "_read_pid_file", lambda: None)
    assert githooks._watcher_pid() is None


def test_load_env_reads_dotenv(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setitem(
        sys.modules,
        "dotenv",
        SimpleNamespace(load_dotenv=lambda p: captured.setdefault("path", p)),
    )
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    githooks._load_env(tmp_path)
    assert captured["path"] == tmp_path / ".env"


def test_load_env_missing_file(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "dotenv",
        SimpleNamespace(
            load_dotenv=lambda _p: (_ for _ in ()).throw(
                AssertionError("should not load")
            )
        ),
    )
    githooks._load_env(tmp_path)


def test_load_env_import_failure(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *a, **k):
        if name == "dotenv":
            raise ImportError("no dotenv")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    githooks._load_env(tmp_path)


def _patch_acquire_env(monkeypatch, tmp_path):
    monkeypatch.setattr(githooks, "_git_toplevel", lambda *a, **k: tmp_path)
    monkeypatch.setattr(githooks, "_load_env", lambda _root: None)


def test_acquire_staged_no_files(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)
    monkeypatch.setattr(githooks, "_get_staged_files", lambda _root: [])
    assert githooks.acquire_staged() == 0


def test_acquire_staged_skips_when_watcher_running(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)
    monkeypatch.setattr(githooks, "_get_staged_files", lambda _root: ["a.py"])
    monkeypatch.setattr(githooks, "_watcher_pid", lambda: 999)
    err = io.StringIO()
    with redirect_stderr(err):
        assert githooks.acquire_staged() == 0
    assert "Watcher running" in err.getvalue()


def test_acquire_staged_success(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)
    monkeypatch.setattr(githooks, "_get_staged_files", lambda _root: ["a.py", "b.py"])
    monkeypatch.setattr(githooks, "_watcher_pid", lambda: None)

    class _Client:
        def acquire_multiple(self, *_a, **_k):
            return True, [], "ok"

    monkeypatch.setattr("collab.lock_client.LockClient", lambda: _Client())
    err = io.StringIO()
    with redirect_stderr(err):
        assert githooks.acquire_staged() == 0
    out = err.getvalue()
    assert "Checking locks for 2 staged files" in out
    assert "Locks acquired for 2 staged files" in out


def test_acquire_staged_single_file_pluralization(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)
    monkeypatch.setattr(githooks, "_get_staged_files", lambda _root: ["a.py"])
    monkeypatch.setattr(githooks, "_watcher_pid", lambda: None)

    class _Client:
        def acquire_multiple(self, *_a, **_k):
            return True, [], "ok"

    monkeypatch.setattr("collab.lock_client.LockClient", lambda: _Client())
    err = io.StringIO()
    with redirect_stderr(err):
        assert githooks.acquire_staged() == 0
    assert "1 staged file." in err.getvalue()


def test_acquire_staged_strict_failure(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)
    monkeypatch.setattr(githooks, "_get_staged_files", lambda _root: ["a.py"])
    monkeypatch.setattr(githooks, "_watcher_pid", lambda: None)

    class _Broken:
        def __init__(self):
            raise RuntimeError("backend down")

    monkeypatch.setattr("collab.lock_client.LockClient", _Broken)
    monkeypatch.setenv("LOCK_STRICT", "1")
    err = io.StringIO()
    with redirect_stderr(err):
        assert githooks.acquire_staged() == 1
    assert "lock check failed" in err.getvalue()


def test_acquire_staged_soft_failure(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)
    monkeypatch.setattr(githooks, "_get_staged_files", lambda _root: ["a.py"])
    monkeypatch.setattr(githooks, "_watcher_pid", lambda: None)

    class _Broken:
        def __init__(self):
            raise RuntimeError("backend down")

    monkeypatch.setattr("collab.lock_client.LockClient", _Broken)
    monkeypatch.delenv("LOCK_STRICT", raising=False)
    err = io.StringIO()
    with redirect_stderr(err):
        assert githooks.acquire_staged() == 0
    assert "lock check failed" in err.getvalue()


def test_acquire_staged_conflict(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)
    monkeypatch.setattr(githooks, "_get_staged_files", lambda _root: ["a.py"])
    monkeypatch.setattr(githooks, "_watcher_pid", lambda: None)

    class _Client:
        def acquire_multiple(self, *_a, **_k):
            return False, ["a.py"], "conflict"

        def get_lock_status(self, _f):
            return {"locked_by": "dev1"}

    monkeypatch.setattr("collab.lock_client.LockClient", lambda: _Client())
    err = io.StringIO()
    with redirect_stderr(err):
        assert githooks.acquire_staged() == 1
    out = err.getvalue()
    assert "Commit blocked" in out
    assert "@dev1" in out


def test_acquire_staged_conflict_status_exception(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)
    monkeypatch.setattr(githooks, "_get_staged_files", lambda _root: ["a.py"])
    monkeypatch.setattr(githooks, "_watcher_pid", lambda: None)

    class _Client:
        def acquire_multiple(self, *_a, **_k):
            return False, ["a.py"], "conflict"

        def get_lock_status(self, _f):
            raise RuntimeError("boom")

    monkeypatch.setattr("collab.lock_client.LockClient", lambda: _Client())
    err = io.StringIO()
    with redirect_stderr(err):
        assert githooks.acquire_staged() == 1
    assert "@unknown" in err.getvalue()


def test_release_all_success(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)

    class _Client:
        def release_all(self):
            return 3

    monkeypatch.setattr("collab.lock_client.LockClient", lambda: _Client())
    err = io.StringIO()
    with redirect_stderr(err):
        assert githooks.release_all() == 0
    assert "Released 3 lock(s)." in err.getvalue()


def test_release_all_retains_pr_claims_when_enabled(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLAB_PR_CLAIMS", "1")
    calls: dict = {}

    class _Client:
        def reconcile_pr_claims(self):
            calls["reconcile"] = True
            return 0

        def release_all(self, **k):
            calls["release_all"] = True
            return 0

        def release_all_except(self, keep, branch):
            calls["except"] = (tuple(keep), branch)
            return 2

    monkeypatch.setattr("collab.lock_client.LockClient", lambda: _Client())
    monkeypatch.setattr(
        githooks.overlap,
        "head_changed_files",
        lambda root: ("feat/x", ["a.py", "b.py"]),
    )
    err = io.StringIO()
    with redirect_stderr(err):
        assert githooks.release_all() == 0
    assert calls.get("reconcile") is True
    assert calls.get("except") == (("a.py", "b.py"), "feat/x")
    assert "release_all" not in calls  # claims path, not plain release
    assert "Released 2 lock(s)." in err.getvalue()


def test_release_all_falls_back_when_no_changed_files(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLAB_PR_CLAIMS", "1")
    calls: dict = {}

    class _Client:
        def reconcile_pr_claims(self):
            return 0

        def release_all(self, **k):
            calls["release_all"] = True
            return 4

        def release_all_except(self, keep, branch):
            calls["except"] = True
            return 0

    monkeypatch.setattr("collab.lock_client.LockClient", lambda: _Client())
    monkeypatch.setattr(
        githooks.overlap, "head_changed_files", lambda root: ("feat/x", [])
    )
    with redirect_stderr(io.StringIO()):
        assert githooks.release_all() == 0
    assert calls.get("release_all") is True
    assert "except" not in calls


def test_release_all_disabled_uses_plain_release(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)
    monkeypatch.delenv("COLLAB_PR_CLAIMS", raising=False)
    calls: dict = {}

    class _Client:
        def release_all(self, **k):
            calls["release_all"] = True
            return 1

        def release_all_except(self, *a):
            calls["except"] = True
            return 0

        def reconcile_pr_claims(self):
            calls["reconcile"] = True
            return 0

    monkeypatch.setattr("collab.lock_client.LockClient", lambda: _Client())
    with redirect_stderr(io.StringIO()):
        assert githooks.release_all() == 0
    assert calls.get("release_all") is True
    assert "except" not in calls and "reconcile" not in calls


def test_warn_cross_branch_overlap_emits_warnings(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLAB_OVERLAP_FETCH", "0")
    monkeypatch.setattr(
        githooks.overlap,
        "detect_cross_branch_overlaps",
        lambda *_a, **_k: [
            githooks.overlap.OverlapReport(branch="feat/x", files=("a.py",))
        ],
    )
    err = io.StringIO()
    with redirect_stderr(err):
        assert githooks.warn_cross_branch_overlap() == 0
    assert "cross-branch overlap" in err.getvalue().lower()


def test_main_check_overlap_command(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)
    seen = {}

    def fake(remote=None):
        seen["remote"] = remote
        return 0

    monkeypatch.setattr(githooks, "warn_cross_branch_overlap", fake)
    assert githooks.main(["check-overlap"]) == 0
    assert seen["remote"] is None
    # The push remote ($1) is forwarded through to the overlap check.
    assert githooks.main(["check-overlap", "upstream"]) == 0
    assert seen["remote"] == "upstream"


def test_warn_cross_branch_overlap_blocks_in_strict(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLAB_OVERLAP_FETCH", "0")
    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", "1")
    monkeypatch.setattr(
        githooks.overlap,
        "detect_cross_branch_overlaps",
        lambda *_a, **_k: [
            githooks.overlap.OverlapReport(branch="feat/x", files=("a.py",))
        ],
    )
    err = io.StringIO()
    with redirect_stderr(err):
        rc = githooks.warn_cross_branch_overlap()
    assert rc == githooks.overlap.EXIT_OVERLAP
    assert rc != 0


def test_warn_cross_branch_overlap_toplevel_failure_fails_closed_in_strict(
    monkeypatch,
):
    monkeypatch.setenv("COLLAB_OVERLAP_STRICT", "1")

    def boom(*_a, **_k):
        raise RuntimeError("not a git repo")

    monkeypatch.setattr(githooks, "_git_toplevel", boom)
    err = io.StringIO()
    with redirect_stderr(err):
        rc = githooks.warn_cross_branch_overlap()
    assert rc == githooks.overlap.EXIT_ERROR


def test_warn_cross_branch_overlap_toplevel_failure_fails_open_advisory(monkeypatch):
    monkeypatch.delenv("COLLAB_OVERLAP_STRICT", raising=False)

    def boom(*_a, **_k):
        raise RuntimeError("not a git repo")

    monkeypatch.setattr(githooks, "_git_toplevel", boom)
    err = io.StringIO()
    with redirect_stderr(err):
        rc = githooks.warn_cross_branch_overlap()
    assert rc == githooks.overlap.EXIT_OK


def test_release_all_failure(monkeypatch, tmp_path):
    _patch_acquire_env(monkeypatch, tmp_path)

    class _Broken:
        def __init__(self):
            raise RuntimeError("nope")

    monkeypatch.setattr("collab.lock_client.LockClient", _Broken)
    err = io.StringIO()
    with redirect_stderr(err):
        assert githooks.release_all() == 0
    assert "lock cleanup failed" in err.getvalue()


def test_read_template_returns_known_hooks():
    for name in githooks.HOOK_NAMES:
        text = githooks._read_template(name)
        assert text.startswith("#!/bin/sh")
    assert "collab.githooks acquire-staged" in githooks._read_template("pre-commit")
    assert "collab.githooks check-overlap" in githooks._read_template("pre-push")
    assert "collab.githooks release-all" in githooks._read_template("pre-push")
    assert "install -e ." in githooks._read_template("post-merge")
    assert "install -e ." in githooks._read_template("post-checkout")
    # Verify orphan cleanup is present in both hooks
    post_merge = githooks._read_template("post-merge")
    post_checkout = githooks._read_template("post-checkout")
    assert "daemon-stop" in post_merge
    assert "daemon-stop" in post_checkout
    assert "daemon-start" in post_merge
    assert "daemon-start" in post_checkout
    assert "rm -rf" in post_merge
    assert "rm -rf" in post_checkout
    assert "site_pkgs" in post_merge
    assert "site_pkgs" in post_checkout
    assert "~ollab_runtime" in post_merge
    assert "~ollab_runtime" in post_checkout


def test_hooks_dir_honors_core_hooks_path(monkeypatch, tmp_path):
    custom = tmp_path / "myhooks"

    def _fake_run(_root, *args):
        if args[:2] == ("config", "--get"):
            return 0, str(custom)
        return 1, ""

    monkeypatch.setattr(githooks, "_run_git", _fake_run)
    assert githooks._hooks_dir(tmp_path) == custom


def test_hooks_dir_relative_core_hooks_path(monkeypatch, tmp_path):
    def _fake_run(_root, *args):
        if args[:2] == ("config", "--get"):
            return 0, "rel/hooks"
        return 1, ""

    monkeypatch.setattr(githooks, "_run_git", _fake_run)
    assert githooks._hooks_dir(tmp_path) == tmp_path / "rel" / "hooks"


def test_hooks_dir_uses_git_path(monkeypatch, tmp_path):
    resolved = tmp_path / ".git" / "hooks"

    def _fake_run(_root, *args):
        if args[:2] == ("config", "--get"):
            return 1, ""
        if args[0] == "rev-parse":
            return 0, str(resolved)
        return 1, ""

    monkeypatch.setattr(githooks, "_run_git", _fake_run)
    assert githooks._hooks_dir(tmp_path) == resolved


def test_hooks_dir_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(githooks, "_run_git", lambda *a: (1, ""))
    assert githooks._hooks_dir(tmp_path) == tmp_path / ".git" / "hooks"


def test_install_hooks_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(githooks, "_hooks_dir", lambda root: root / ".git" / "hooks")
    summary = githooks.install_hooks(project_root=tmp_path)
    assert sorted(summary["installed"]) == sorted(githooks.HOOK_NAMES)
    assert summary["skipped"] == []
    pre_commit = (tmp_path / ".git" / "hooks" / "pre-commit").read_text(
        encoding="utf-8"
    )
    assert "collab.githooks acquire-staged" in pre_commit
    # Hooks must use LF endings for POSIX sh.
    raw = (tmp_path / ".git" / "hooks" / "pre-commit").read_bytes()
    assert b"\r\n" not in raw


def test_install_hooks_skips_non_collab(monkeypatch, tmp_path):
    monkeypatch.setattr(githooks, "_hooks_dir", lambda root: root / ".git" / "hooks")
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\necho custom\n", encoding="utf-8")

    summary = githooks.install_hooks(project_root=tmp_path)
    assert "pre-commit" in summary["skipped"]
    assert "pre-commit" not in summary["installed"]
    # Custom hook preserved.
    assert "echo custom" in (hooks_dir / "pre-commit").read_text(encoding="utf-8")


def test_install_hooks_force_overwrites(monkeypatch, tmp_path):
    monkeypatch.setattr(githooks, "_hooks_dir", lambda root: root / ".git" / "hooks")
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\necho custom\n", encoding="utf-8")

    summary = githooks.install_hooks(project_root=tmp_path, force=True)
    assert "pre-commit" in summary["installed"]
    assert "collab.githooks" in (hooks_dir / "pre-commit").read_text(encoding="utf-8")


def test_install_hooks_overwrites_existing_collab_hook(monkeypatch, tmp_path):
    monkeypatch.setattr(githooks, "_hooks_dir", lambda root: root / ".git" / "hooks")
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "pre-commit").write_text("# old collab hook\n", encoding="utf-8")

    summary = githooks.install_hooks(project_root=tmp_path)
    assert "pre-commit" in summary["installed"]


def test_install_hooks_read_error_treated_as_replaceable(monkeypatch, tmp_path):
    monkeypatch.setattr(githooks, "_hooks_dir", lambda root: root / ".git" / "hooks")
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "pre-commit").write_text("# old collab\n", encoding="utf-8")

    original_read_text = Path.read_text

    def _maybe_boom(self, *a, **k):
        if self.name == "pre-commit" and "hooks" in str(self):
            raise OSError("unreadable")
        return original_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _maybe_boom)
    summary = githooks.install_hooks(project_root=tmp_path)
    assert "pre-commit" in summary["skipped"]


def test_main_no_args():
    err = io.StringIO()
    with redirect_stderr(err):
        assert githooks.main([]) == 2
    assert "Usage" in err.getvalue()


def test_main_dispatch(monkeypatch):
    monkeypatch.setattr(githooks, "acquire_staged", lambda: 7)
    monkeypatch.setattr(githooks, "release_all", lambda: 8)
    assert githooks.main(["acquire-staged"]) == 7
    assert githooks.main(["release-all"]) == 8


def test_main_unknown(monkeypatch):
    err = io.StringIO()
    with redirect_stderr(err):
        assert githooks.main(["bogus"]) == 2
    assert "Unknown command" in err.getvalue()


def test_main_init(monkeypatch, capsys):
    monkeypatch.setattr(
        githooks,
        "install_hooks",
        lambda force=False: {
            "installed": ["pre-commit"],
            "skipped": ["commit-msg"],
            "hooks_dir": "/tmp/repo/.git/hooks",
        },
    )
    assert githooks.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "Installed git hooks" in out
    assert "pre-commit" in out
    assert "Skipped" in out


def test_main_init_force_flag(monkeypatch):
    captured = {}

    def _fake(force=False):
        captured["force"] = force
        return {"installed": [], "skipped": [], "hooks_dir": "x"}

    monkeypatch.setattr(githooks, "install_hooks", _fake)
    githooks.main(["init", "--force"])
    assert captured["force"] is True


def test_dunder_main_runs(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["githooks"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("collab.githooks", run_name="__main__")
    assert exc.value.code == 2
