"""Developer identity tests for live_locks_watcher."""

from __future__ import annotations

import subprocess

from ._helpers import load_watcher_module


def test_get_developer_id_from_env(monkeypatch):
    mod = load_watcher_module()
    # Force git to fail and ensure environment fallback is used
    monkeypatch.setenv("USERNAME", "test_developer")

    def mock_check_output(cmd, *a, **k):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    result = mod._get_developer_id()
    assert result == "test_developer"


def test_get_developer_id_from_git(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.delenv("DEVELOPER_ID", raising=False)

    def mock_check_output(cmd, *a, **k):
        if "user.name" in cmd:
            return b"git_user\n"
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    result = mod._get_developer_id()
    assert result == "git_user"


# ---- Auto-migrated from migrated_remaining ----


def test_is_ephemeral_dev_empty():
    mod = load_watcher_module()
    # Cover the branch where dev_id is falsy and returns False
    assert mod._is_ephemeral_dev("") is False


def test_is_ephemeral_dev_matches_prefix():
    """_is_ephemeral_dev returns True when dev_id starts with an ephemeral prefix."""
    mod = load_watcher_module()
    assert mod._is_ephemeral_dev("test_dev_42") is True
    assert mod._is_ephemeral_dev("ci-runner") is True
    assert mod._is_ephemeral_dev("regular_user") is False


watcher = load_watcher_module()
