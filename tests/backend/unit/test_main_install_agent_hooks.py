"""Tests for the `collab install-agent-hooks` CLI command in collab/main.py."""

from __future__ import annotations

import sys

import pytest


def _stub_runtime(monkeypatch):
    """Silence logging/stream side effects so the command runs in isolation."""
    import collab.lock_client as lc
    import collab.logging_config as logging_config
    import collab.main as main_mod

    monkeypatch.setattr(
        main_mod, "setup_collab_logging", lambda **_k: None, raising=False
    )
    monkeypatch.setattr(lc, "_COLLAB_ROOT", ".collab", raising=False)
    monkeypatch.setattr(logging_config, "setup_collab_logging", lambda **_k: None)


def test_install_agent_hooks_command(monkeypatch, capsys, tmp_path):
    import collab.agent_hooks as agent_hooks
    import collab.main as main_mod

    _stub_runtime(monkeypatch)

    captured = {}

    def _fake(force=False):
        captured["force"] = force
        return {
            "root": str(tmp_path),
            "command": "py -m collab.agent_hooks run-hook --from-ide-hook",
            "results": {
                "cursor": "installed",
                "claude": "updated",
                "junie": "current",
            },
        }

    monkeypatch.setattr(agent_hooks, "install_agent_hooks", _fake)
    monkeypatch.setattr(sys, "argv", ["collab", "install-agent-hooks"])

    with pytest.raises(SystemExit) as exc:
        main_mod._run_cli()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "Agent attribution hooks configured" in out
    assert ".cursor/hooks.json" in out
    assert captured["force"] is False


def test_install_agent_hooks_command_forwards_force(monkeypatch, capsys):
    import collab.agent_hooks as agent_hooks
    import collab.main as main_mod

    _stub_runtime(monkeypatch)

    captured = {}

    def _fake(force=False):
        captured["force"] = force
        return {"root": ".", "command": "x", "results": {}}

    monkeypatch.setattr(agent_hooks, "install_agent_hooks", _fake)
    monkeypatch.setattr(sys, "argv", ["collab", "install-agent-hooks", "--force"])

    with pytest.raises(SystemExit) as exc:
        main_mod._run_cli()
    assert exc.value.code == 0
    assert captured["force"] is True
