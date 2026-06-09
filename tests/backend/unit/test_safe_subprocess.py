"""Unit tests for safe_subprocess wrapper (Phase 5A)."""

from __future__ import annotations

import subprocess as sp
import sys
import types

import pytest

from collab import safe_subprocess
from collab.errors import SubprocessSecurityError
from tests.backend.subprocess_testing import patch_subprocess


def test_validate_git_argv_accepts_config():
    argv = safe_subprocess.validate_argv(["git", "config", "user.name"], policy="git")
    assert argv[1] == "config"


@pytest.mark.parametrize(
    "subcommand",
    ["merge-base", "for-each-ref", "rev-list"],
)
def test_validate_git_argv_accepts_overlap_subcommands(subcommand):
    argv = safe_subprocess.validate_argv(
        ["git", subcommand, "HEAD", "origin/main"],
        policy="git",
    )
    assert argv[1] == subcommand


def test_validate_git_argv_rejects_unknown_subcommand():
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(["git", "reset", "--hard"], policy="git")


def test_validate_watcher_argv_accepts_daemon_shape():
    argv = [
        sys.executable,
        "-m",
        "collab.lock_client",
        "watch",
        "--interval",
        "5",
        "--timeout",
        "0",
        "--daemon",
        "--pid-file",
        ".daemon.pid",
    ]
    resolved = safe_subprocess.validate_argv(argv, policy="watcher")
    assert resolved[2] == "collab.lock_client"
    assert resolved[3] == "watch"


def test_validate_watcher_argv_rejects_wrong_module():
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(
            [sys.executable, "-m", "os", "watch"],
            policy="watcher",
        )


def test_validate_agent_claim_argv_accepts_paths_and_flags():
    argv = [
        sys.executable,
        "-m",
        "collab",
        "claim",
        "collab/app.py",
        "README.md",
        "--label",
        "fix-ci",
        "--reason",
        "AI agent edit",
    ]
    resolved = safe_subprocess.validate_argv(argv, policy="agent_claim")
    assert resolved[1:4] == ("-m", "collab", "claim")


def test_validate_agent_claim_argv_rejects_wrong_module():
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(
            [sys.executable, "-m", "os", "claim", "a.py"],
            policy="agent_claim",
        )


def test_validate_agent_claim_argv_rejects_unknown_flag():
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(
            [sys.executable, "-m", "collab", "claim", "a.py", "--force"],
            policy="agent_claim",
        )


def test_validate_agent_claim_argv_requires_a_path():
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(
            [sys.executable, "-m", "collab", "claim", "--label", "x"],
            policy="agent_claim",
        )


def test_validate_agent_claim_argv_flag_requires_value():
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(
            [sys.executable, "-m", "collab", "claim", "a.py", "--label"],
            policy="agent_claim",
        )


def test_validate_agent_claim_argv_rejects_non_python():
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(
            ["bash", "-m", "collab", "claim", "a.py"],
            policy="agent_claim",
        )


def test_validate_taskkill_argv_requires_numeric_pid():
    safe_subprocess.validate_argv(
        ["taskkill", "/F", "/PID", "1234"],
        policy="taskkill",
    )
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(
            ["taskkill", "/F", "/PID", "not-a-pid"],
            policy="taskkill",
        )


def test_resolve_executable_in_test_mode(monkeypatch):
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    assert safe_subprocess.resolve_executable("definitely-not-on-path-xyz") == (
        "definitely-not-on-path-xyz"
    )


def test_capture_returns_nonzero_without_raising(monkeypatch):
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")

    def fake_check_output(argv, **kwargs):
        raise sp.CalledProcessError(1, argv)

    patch_subprocess(monkeypatch, check_output=fake_check_output)
    result = safe_subprocess.capture(["git", "status", "--porcelain"], policy="git")
    assert result.returncode == 1
    assert not result.ok


def test_validate_empty_argv_rejected():
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv([], policy="git")


def test_validate_git_requires_subcommand():
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(["git"], policy="git")


def test_validate_taskkill_too_short_and_bad_flags():
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(["taskkill", "/F"], policy="taskkill")
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(
            ["taskkill", "/X", "/PID", "1"],
            policy="taskkill",
        )
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(["taskkill", "/F", "1234"], policy="taskkill")
    with pytest.raises(SubprocessSecurityError, match="/PID"):
        safe_subprocess.validate_argv(
            ["taskkill", "/F", "9999", "extra"],
            policy="taskkill",
        )


def test_validate_watcher_argv_rejects_non_python_executable(monkeypatch):
    monkeypatch.setattr(safe_subprocess, "_is_python_executable", lambda _p: False)
    with pytest.raises(SubprocessSecurityError, match="python/pythonw"):
        safe_subprocess._validate_watcher_argv(
            ["/opt/python3", "-m", "collab.lock_client", "watch"],
        )


def test_validate_watcher_rejects_non_python_launcher():
    """Absolute non-python argv[0] must not be rewritten before validation."""
    launcher = r"C:\Windows\System32\cmd.exe"
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(
            [
                launcher,
                "-m",
                "collab.lock_client",
                "watch",
                "--daemon",
                "--pid-file",
                "p.pid",
            ],
            policy="watcher",
        )


def test_validate_auto_policy_selects_watcher_for_python_module(monkeypatch):
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    argv = safe_subprocess.validate_argv(
        [
            sys.executable,
            "-m",
            "collab.lock_client",
            "watch",
            "--daemon",
            "--pid-file",
            "p.pid",
        ],
    )
    assert argv[2] == "collab.lock_client"


def test_validate_auto_policy_generic_executable_in_test_mode(monkeypatch):
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    argv = safe_subprocess.validate_argv(["custom-tool", "run"])
    assert argv[0] == "custom-tool"


def test_validate_watcher_resolves_relative_python_to_sys_executable(monkeypatch):
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    argv = safe_subprocess.validate_argv(
        [
            "python",
            "-m",
            "collab.lock_client",
            "watch",
            "--daemon",
            "--pid-file",
            "p.pid",
        ],
        policy="watcher",
    )
    assert safe_subprocess.os.path.isabs(argv[0])


def test_validate_generic_resolves_path_when_executable_found(monkeypatch):
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    monkeypatch.setattr(
        safe_subprocess.shutil,
        "which",
        lambda name: f"C:/bin/{name}.exe",
    )
    monkeypatch.setattr(safe_subprocess.os.path, "abspath", lambda p: p)
    argv = safe_subprocess.validate_argv(["git", "status"], policy="generic")
    assert argv[0] == "C:/bin/git.exe"


def test_validate_watcher_rejects_bad_flags_and_short_argv():
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv([sys.executable, "-m"], policy="watcher")
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(
            [
                sys.executable,
                "-m",
                "collab.lock_client",
                "watch",
                "--evil",
            ],
            policy="watcher",
        )


def test_validate_auto_policy_detects_git_taskkill_and_platform(monkeypatch):
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")

    def _base(argv0: str) -> str:
        return safe_subprocess.os.path.basename(argv0).lower().replace(".exe", "")

    git_argv = safe_subprocess.validate_argv(["git", "status"])
    assert _base(git_argv[0]) == "git"
    tk_argv = safe_subprocess.validate_argv(["taskkill", "/F", "/PID", "9"])
    assert _base(tk_argv[0]) == "taskkill"
    plat_argv = safe_subprocess.validate_argv(["wmic", "process", "list"])
    assert _base(plat_argv[0]) == "wmic"


def test_validate_generic_requires_executable(monkeypatch):
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    monkeypatch.setattr(safe_subprocess.shutil, "which", lambda _name: None)
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(["missing-binary-xyz"], policy="generic")


def test_validate_watcher_pythonw_resolves_executable(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    fake_pythonw = tmp_path / "pythonw.exe"
    fake_pythonw.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        safe_subprocess.os.path,
        "exists",
        lambda p: str(p) == str(fake_pythonw),
    )
    monkeypatch.setattr(
        safe_subprocess.sys,
        "executable",
        str(tmp_path / "python.exe"),
    )
    argv = safe_subprocess.validate_argv(
        [
            "pythonw",
            "-m",
            "collab.lock_client",
            "watch",
            "--daemon",
        ],
        policy="watcher",
    )
    assert argv[0].endswith("pythonw.exe")


def test_capture_ignores_spoofed_sys_platform_on_non_windows_host(monkeypatch):
    """``creationflags`` must follow ``os.name``, not a leaked ``sys.platform``."""
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setattr(safe_subprocess.sys, "platform", "win32")
    monkeypatch.setattr(safe_subprocess.os, "name", "posix")
    seen: dict = {}

    def fake_check_output(argv, **kwargs):
        seen.update(kwargs)
        return b""

    patch_subprocess(monkeypatch, check_output=fake_check_output)
    safe_subprocess.capture(["git", "status"], policy="git")
    assert "creationflags" not in seen


def test_capture_timeout_and_env(monkeypatch):
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")

    def fake_check_output(argv, **kwargs):
        raise sp.TimeoutExpired(cmd=argv, timeout=1)

    patch_subprocess(monkeypatch, check_output=fake_check_output)
    result = safe_subprocess.capture(
        ["git", "status"],
        policy="git",
        env={"FOO": "bar"},
        text=True,
    )
    assert result.timed_out
    assert result.returncode == -1


def test_run_timeout(monkeypatch):
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")

    def fake_run(argv, **kwargs):
        raise sp.TimeoutExpired(cmd=argv, timeout=1)

    patch_subprocess(monkeypatch, run=fake_run)
    result = safe_subprocess.run(["git", "status"], policy="git")
    assert result.timed_out


def test_spawn_background_unix(monkeypatch):
    """Unix ``start_new_session`` path is selected on non-Windows hosts."""
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setattr(safe_subprocess.os, "name", "posix")
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        seen.update(kwargs)
        return types.SimpleNamespace(pid=999)

    patch_subprocess(monkeypatch, popen=fake_popen)
    proc = safe_subprocess.spawn_background(
        [
            sys.executable,
            "-m",
            "collab.lock_client",
            "watch",
            "--daemon",
            "--pid-file",
            "p.pid",
        ],
        start_new_session=True,
    )
    assert proc.pid == 999
    assert seen.get("start_new_session") is True
    assert "creationflags" not in seen


def test_decode_output():
    assert safe_subprocess.decode_output(b"ok") == "ok"


def test_spawn_background_windows(monkeypatch):
    """Windows ``creationflags`` path is selected on Windows hosts (``os.name``)."""
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setattr(safe_subprocess.os, "name", "nt")
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        seen.update(kwargs)
        return types.SimpleNamespace(pid=1001)

    patch_subprocess(monkeypatch, popen=fake_popen)
    proc = safe_subprocess.spawn_background(
        [
            sys.executable,
            "-m",
            "collab.lock_client",
            "watch",
            "--daemon",
            "--pid-file",
            "p.pid",
        ],
        creationflags=0x00000200,
    )
    assert proc.pid == 1001
    assert seen.get("creationflags") == 0x00000200
    assert "start_new_session" not in seen


def test_spawn_background_passes_env(monkeypatch):
    """An explicit ``env`` mapping is forwarded to Popen."""
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setattr(safe_subprocess.os, "name", "posix")
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        seen.update(kwargs)
        return types.SimpleNamespace(pid=7)

    patch_subprocess(monkeypatch, popen=fake_popen)
    safe_subprocess.spawn_background(
        [sys.executable, "-m", "collab", "claim", "a.py"],
        policy="agent_claim",
        env={"COLLAB_AGENT_MODE": "1"},
    )
    assert seen.get("env") == {"COLLAB_AGENT_MODE": "1"}
