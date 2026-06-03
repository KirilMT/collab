"""Acquire payload attribution tests for live_locks_watcher."""

from __future__ import annotations

from ._helpers import load_watcher_module


def test_acquire_payload_human_origin(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "AGENT_ID", None)
    monkeypatch.setattr(mod, "AGENT_LABEL", None)
    monkeypatch.setattr(mod, "AGENT_KIND", None)
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda d: False)

    payload = mod._acquire_rpc_payload("collab/app.py", "main", "Auto-Watch", "tok")

    assert payload["p_origin"] == "human"
    assert payload["p_agent_id"] is None
    assert payload["p_agent_kind"] is None


def test_acquire_payload_agent_origin(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "AGENT_ID", "agent-a")
    monkeypatch.setattr(mod, "AGENT_LABEL", "fix-ci")
    monkeypatch.setattr(mod, "AGENT_KIND", "cursor")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda d: False)

    payload = mod._acquire_rpc_payload("collab/app.py", "main", "Auto-Watch", "tok")

    assert payload["p_origin"] == "agent"
    assert payload["p_agent_id"] == "agent-a"
    assert payload["p_agent_label"] == "fix-ci"
    assert payload["p_agent_kind"] == "cursor"
