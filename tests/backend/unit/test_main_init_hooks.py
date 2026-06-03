"""Tests for the `collab init-hooks` CLI command in collab/main.py."""

from __future__ import annotations

import sys

import pytest


def _stub_runtime(monkeypatch):
    """Silence logging/stream side effects so init-hooks runs in isolation."""
    import collab.lock_client as lc
    import collab.logging_config as logging_config
    import collab.main as main_mod

    monkeypatch.setattr(
        main_mod, "setup_collab_logging", lambda **_k: None, raising=False
    )
    monkeypatch.setattr(lc, "_COLLAB_ROOT", ".collab", raising=False)
    monkeypatch.setattr(logging_config, "setup_collab_logging", lambda **_k: None)


def test_init_hooks_command_installs_and_exits_zero(monkeypatch, capsys):
    import collab.githooks as githooks
    import collab.main as main_mod

    _stub_runtime(monkeypatch)

    captured = {}

    def _fake(force=False):
        captured["force"] = force
        return {
            "installed": ["pre-commit", "pre-push"],
            "skipped": ["commit-msg"],
            "hooks_dir": "/repo/.git/hooks",
        }

    monkeypatch.setattr(githooks, "install_hooks", _fake)
    monkeypatch.setattr(sys, "argv", ["collab", "init-hooks"])

    with pytest.raises(SystemExit) as exc:
        main_mod._run_cli()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "Installed collab git hooks into /repo/.git/hooks" in out
    assert "pre-commit, pre-push" in out
    assert "Skipped" in out
    assert captured["force"] is False


def test_init_hooks_command_forwards_force(monkeypatch, capsys):
    import collab.githooks as githooks
    import collab.main as main_mod

    _stub_runtime(monkeypatch)

    captured = {}

    def _fake(force=False):
        captured["force"] = force
        return {
            "installed": ["pre-commit"],
            "skipped": [],
            "hooks_dir": "/repo/.git/hooks",
        }

    monkeypatch.setattr(githooks, "install_hooks", _fake)
    monkeypatch.setattr(sys, "argv", ["collab", "init-hooks", "--force"])

    with pytest.raises(SystemExit) as exc:
        main_mod._run_cli()
    assert exc.value.code == 0
    assert captured["force"] is True
    out = capsys.readouterr().out
    assert "Skipped" not in out
