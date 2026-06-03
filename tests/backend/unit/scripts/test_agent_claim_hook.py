"""Tests for the runtime-agnostic agent claim hook (scripts/agent-hooks)."""

from __future__ import annotations

import io

from ._helpers import load_script_module


def _load():
    return load_script_module("agent-hooks/collab_claim_hook.py", "collab_claim_hook")


def test_extract_paths_from_varied_keys():
    mod = _load()
    event = {
        "file_path": "a.py",
        "edits": [{"path": "b/c.py"}],
        "nested": {"absolutePath": "d.txt"},
        "uri": "file:///C:/repo/e.py",
        "ignored": "not-a-path",
    }
    paths = mod._extract_paths(event)
    assert "a.py" in paths
    assert "b/c.py" in paths
    assert "d.txt" in paths
    assert any(p.endswith("e.py") for p in paths)
    assert "not-a-path" not in paths


def test_normalize_path_strips_file_scheme():
    mod = _load()
    assert mod._normalize_path("file:///C:/repo/x.py").endswith("x.py")
    assert mod._normalize_path("plainword") is None
    assert mod._normalize_path("dir/sub/file.py") == "dir/sub/file.py"


def test_stable_agent_id_precedence(monkeypatch):
    mod = _load()
    monkeypatch.setenv("COLLAB_AGENT_ID", "agent-explicit")
    assert mod._stable_agent_id({}) == "agent-explicit"

    monkeypatch.delenv("COLLAB_AGENT_ID", raising=False)
    a = mod._stable_agent_id({"conversationId": "conv-1"})
    b = mod._stable_agent_id({"conversationId": "conv-1"})
    assert a == b and a.startswith("agent-")
    # Different session -> different id.
    assert mod._stable_agent_id({"conversationId": "conv-2"}) != a


def test_detect_kind(monkeypatch):
    mod = _load()
    monkeypatch.delenv("COLLAB_AGENT_KIND", raising=False)
    for env_name in ("CURSOR_TRACE_ID", "CLAUDE_CODE", "GITHUB_COPILOT_AGENT_ID"):
        monkeypatch.delenv(env_name, raising=False)
    assert mod._detect_kind() == "other"
    monkeypatch.setenv("CURSOR_TRACE_ID", "t")
    assert mod._detect_kind() == "cursor"
    monkeypatch.setenv("COLLAB_AGENT_KIND", "claude-code")
    assert mod._detect_kind() == "claude-code"


def test_resolve_label(monkeypatch):
    mod = _load()
    monkeypatch.setenv("COLLAB_AGENT_LABEL", "from-env")
    assert mod._resolve_label({"title": "x"}) == "from-env"
    monkeypatch.delenv("COLLAB_AGENT_LABEL", raising=False)
    assert mod._resolve_label({"title": "from-event"}) == "from-event"
    assert mod._resolve_label({}) is None


def test_main_disabled_is_noop(monkeypatch):
    mod = _load()
    monkeypatch.delenv("COLLAB_AGENT_HOOKS", raising=False)
    called = {"run": False}
    monkeypatch.setattr(
        mod.subprocess, "run", lambda *a, **k: called.__setitem__("run", True)
    )
    monkeypatch.setattr(mod.sys, "stdin", io.StringIO('{"file_path": "a.py"}'))
    assert mod.main() == 0
    assert called["run"] is False


def test_main_enabled_invokes_claim(monkeypatch):
    mod = _load()
    monkeypatch.setenv("COLLAB_AGENT_HOOKS", "1")
    monkeypatch.setenv("COLLAB_AGENT_LABEL", "fix-ci")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.sys, "stdin", io.StringIO('{"file_path": "collab/app.py"}'))

    assert mod.main() == 0
    assert "claim" in captured["cmd"]
    assert "collab/app.py" in captured["cmd"]
    assert captured["env"]["COLLAB_AGENT_MODE"] == "1"
    assert captured["env"]["COLLAB_AGENT_ID"]
    assert "--label" in captured["cmd"]


def test_main_enabled_no_paths_is_noop(monkeypatch):
    mod = _load()
    monkeypatch.setenv("COLLAB_AGENT_HOOKS", "1")
    called = {"run": False}
    monkeypatch.setattr(
        mod.subprocess, "run", lambda *a, **k: called.__setitem__("run", True)
    )
    monkeypatch.setattr(mod.sys, "stdin", io.StringIO("{}"))
    assert mod.main() == 0
    assert called["run"] is False


def test_read_event_edge_cases(monkeypatch):
    mod = _load()

    class Boom:
        def read(self):
            raise OSError("no stdin")

    monkeypatch.setattr(mod.sys, "stdin", Boom())
    assert mod._read_event() == {}

    monkeypatch.setattr(mod.sys, "stdin", io.StringIO("   "))
    assert mod._read_event() == {}

    monkeypatch.setattr(mod.sys, "stdin", io.StringIO("not-json{"))
    assert mod._read_event() == {}

    monkeypatch.setattr(mod.sys, "stdin", io.StringIO("[1, 2]"))
    assert mod._read_event() == {}


def test_looks_like_path_rejects_empty_and_newline():
    mod = _load()
    assert mod._normalize_path("") is None
    assert mod._normalize_path("line\nbreak.py") is None


def test_extract_paths_handles_list_of_strings():
    mod = _load()
    event = {"path": ["one.py", "two/three.py", "skip"]}
    paths = mod._extract_paths(event)
    assert "one.py" in paths
    assert "two/three.py" in paths


def test_main_subprocess_error_fails_open(monkeypatch):
    mod = _load()
    monkeypatch.setenv("COLLAB_AGENT_HOOKS", "1")

    def boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    monkeypatch.setattr(mod.sys, "stdin", io.StringIO('{"path": "a.py"}'))
    assert mod.main() == 0
