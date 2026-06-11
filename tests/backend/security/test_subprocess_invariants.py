"""Security regression tests for subprocess spawn invariants (Phase 5D)."""

from __future__ import annotations

import os
import sys

import pytest

from collab import safe_subprocess
from collab.errors import SubprocessSecurityError


@pytest.mark.security
def test_validate_git_argv_rejects_disallowed_subcommand():
    # A git subcommand outside the allowlist (here a shell-metacharacter-laden
    # token) must be rejected before the process is ever spawned.
    with pytest.raises(SubprocessSecurityError) as exc:
        safe_subprocess.validate_argv(
            ["git", "config;rm -rf /", "user.name"],
            policy="git",
        )
    assert "subcommand not allowed" in str(exc.value)


@pytest.mark.security
def test_validate_watcher_argv_resolves_python_executable():
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
    # The watcher launcher must be resolved to an absolute python interpreter.
    assert os.path.isabs(argv[0])
    assert safe_subprocess._is_python_executable(argv[0])


@pytest.mark.security
def test_unknown_binary_rejected_outside_test_mode(monkeypatch):
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)

    def no_which(_name: str):
        return None

    monkeypatch.setattr(safe_subprocess.shutil, "which", no_which)
    with pytest.raises(SubprocessSecurityError):
        safe_subprocess.validate_argv(["totally-unknown-binary-xyz"], policy="generic")
