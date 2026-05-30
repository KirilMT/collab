"""Security regression tests for subprocess spawn invariants (Phase 5D)."""

from __future__ import annotations

import sys

import pytest

from collab import safe_subprocess
from collab.errors import SubprocessSecurityError


@pytest.mark.security
def test_spawn_paths_reject_shell_metacharacters_in_git_subcommand():
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(
            ["git", "config;rm -rf /", "user.name"],
            policy="git",
        )


@pytest.mark.security
def test_watcher_spawn_requires_resolved_python_executable():
    argv = safe_subprocess.validate_argv(
        [
            sys.executable,
            "-m",
            "collab.lock_client",
            "watch",
            "--daemon",
            "--pid-file",
            "x.pid",
        ],
        policy="watcher",
    )
    assert argv[0] == sys.executable or argv[0].endswith("python.exe")


@pytest.mark.security
def test_unknown_binary_rejected_outside_test_mode(monkeypatch):
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)

    def no_which(_name: str):
        return None

    monkeypatch.setattr(safe_subprocess.shutil, "which", no_which)
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(["totally-unknown-binary-xyz"], policy="generic")
