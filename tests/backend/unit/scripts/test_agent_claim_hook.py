"""Tests for the thin copy-paste hook shim (scripts/agent-hooks).

The real logic lives in :mod:`collab.agent_hooks` (covered by
``tests/backend/unit/test_agent_hooks.py``). These tests only assert that the standalone
script correctly delegates to the package and fails open.
"""

from __future__ import annotations

import io

from ._helpers import load_script_module


def _load():
    return load_script_module("agent-hooks/collab_claim_hook.py", "collab_claim_hook")


def test_shim_disabled_is_noop(monkeypatch):
    """Without any enable signal the shim claims nothing."""
    mod = _load()
    from collab import agent_hooks

    monkeypatch.delenv("COLLAB_AGENT_HOOKS", raising=False)
    monkeypatch.setenv("COLLAB_AGENT_HOOKS_DEBUG", "0")
    called = {"run": False}
    monkeypatch.setattr(
        agent_hooks.safe_subprocess,
        "spawn_background",
        lambda *a, **k: called.__setitem__("run", True),
    )
    monkeypatch.setattr(mod.sys, "argv", ["collab_claim_hook.py"])
    monkeypatch.setattr(agent_hooks.sys, "stdin", io.StringIO('{"file_path": "a.py"}'))
    assert mod.main() == 0
    assert called["run"] is False


def test_shim_delegates_to_package(monkeypatch):
    """With the IDE-hook flag the shim invokes the packaged claim runner."""
    mod = _load()
    from collab import agent_hooks

    monkeypatch.setenv("COLLAB_AGENT_HOOKS_DEBUG", "0")
    captured = {}
    monkeypatch.setattr(
        agent_hooks.safe_subprocess,
        "spawn_background",
        lambda cmd, **k: captured.update(cmd=cmd),
    )
    monkeypatch.setattr(mod.sys, "argv", ["collab_claim_hook.py", "--from-ide-hook"])
    monkeypatch.setattr(
        agent_hooks.sys, "stdin", io.StringIO('{"file_path": "collab/app.py"}')
    )
    assert mod.main() == 0
    assert "claim" in captured["cmd"]
    assert "collab/app.py" in captured["cmd"]


def test_shim_fails_open_on_import_error(monkeypatch):
    """If the package cannot be imported, the shim must not block the edit."""
    mod = _load()
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "collab.agent_hooks" or name.startswith("collab.agent_hooks"):
            raise ImportError("simulated missing package")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert mod.main() == 0
