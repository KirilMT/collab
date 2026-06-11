"""Unit tests for collab.agent_identity."""

from __future__ import annotations

import os

from collab import agent_identity


def test_resolve_agent_id_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_AGENT_MODE", "1")
    monkeypatch.delenv("COLLAB_AGENT_ID", raising=False)
    state = str(tmp_path / "state")
    os.makedirs(state, exist_ok=True)

    resolved = agent_identity.resolve_agent_id(
        state, explicit_agent_id="my-agent-1", agent_mode=True
    )
    assert resolved == "my-agent-1"
    assert agent_identity.load_persisted_agent_id(state) == "my-agent-1"


def test_resolve_agent_id_auto_generate_and_persist(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_AGENT_MODE", "1")
    monkeypatch.delenv("COLLAB_AGENT_ID", raising=False)
    state = str(tmp_path / "state")

    first = agent_identity.resolve_agent_id(state, agent_mode=True)
    second = agent_identity.resolve_agent_id(state, agent_mode=True)
    assert first is not None
    assert first == second
    assert first.startswith("agent-")


def test_resolve_agent_id_human_mode_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("COLLAB_AGENT_ID", raising=False)
    monkeypatch.delenv("COLLAB_AGENT_MODE", raising=False)
    monkeypatch.delenv("CURSOR_TRACE_ID", raising=False)

    assert agent_identity.resolve_agent_id(str(tmp_path), agent_mode=False) is None


def test_agent_ids_match_treats_null_as_distinct():
    assert agent_identity.agent_ids_match(None, None)
    assert not agent_identity.agent_ids_match(None, "agent-a")
    assert not agent_identity.agent_ids_match("agent-a", None)
    assert agent_identity.agent_ids_match("agent-a", "agent-a")


def test_session_token_seed_includes_agent():
    without = agent_identity.session_token_seed("Alice", None, "host", "/proj")
    with_agent = agent_identity.session_token_seed("Alice", "agent-1", "host", "/proj")
    assert without != with_agent


def test_session_token_stable_for_same_seed():
    seed = agent_identity.session_token_seed("alice", "a1", "host", "/p")
    t1 = agent_identity.session_token_from_seed(seed)
    t2 = agent_identity.session_token_from_seed(seed)
    assert t1 == t2


def test_daemon_pid_basename_namespaces_agent():
    assert agent_identity.daemon_pid_basename(None) == ".daemon.pid"
    assert agent_identity.daemon_pid_basename("agent-abc") == ".daemon.agent-abc.pid"


def test_format_lock_owner_with_agent():
    text = agent_identity.format_lock_owner("alice", "agent-1", "refactor-auth")
    assert "@alice" in text
    assert "refactor-auth" in text


def test_format_lock_owner_prefers_kind_and_task():
    # kind + task -> "kind · task"; the raw agent_id is never shown.
    text = agent_identity.format_lock_owner(
        "alice", "agent-3538afba", "fix-bug", "cursor"
    )
    assert text == "@alice (agent: cursor · fix-bug)"
    assert "agent-3538afba" not in text


def test_format_lock_owner_kind_only_when_no_task():
    text = agent_identity.format_lock_owner("alice", "agent-xyz", None, "cursor")
    assert text == "@alice (agent: cursor)"
    assert "agent-xyz" not in text


def test_format_lock_owner_other_kind_falls_back_to_id():
    # Unknown runtime ("other") with no task -> last-resort agent_id.
    text = agent_identity.format_lock_owner("alice", "agent-xyz", None, "other")
    assert text == "@alice (agent: agent-xyz)"


def test_format_lock_owner_no_kind_no_task_falls_back_to_id():
    text = agent_identity.format_lock_owner("alice", "agent-xyz")
    assert text == "@alice (agent: agent-xyz)"


def test_format_conflict_message_includes_kind_and_task():
    msg = agent_identity.format_conflict_message(
        "a.py", "alice", "agent-b", "task-x", "cursor"
    )
    assert "cursor · task-x" in msg
    assert "agent-b" not in msg


def test_lock_owned_by_client_requires_both_human_and_agent():
    lock = {"developer_id": "alice", "agent_id": "agent-a"}
    assert agent_identity.lock_owned_by_client(lock, "alice", "agent-a")
    assert not agent_identity.lock_owned_by_client(lock, "alice", "agent-b")
    assert not agent_identity.lock_owned_by_client(lock, "alice", None)
    assert not agent_identity.lock_owned_by_client(lock, "bob", "agent-a")


def test_detect_agent_runtime_label_cursor(monkeypatch):
    monkeypatch.setenv("CURSOR_TRACE_ID", "trace-123")
    assert agent_identity.detect_agent_runtime_label() == "cursor"


def test_identity_summary_modes():
    human = agent_identity.identity_summary("alice", None, None)
    assert human["mode"] == "human"
    assert human["agent_kind"] is None
    agent = agent_identity.identity_summary("alice", "agent-1", "task", "cursor")
    assert agent["mode"] == "agent"
    assert agent["agent_id"] == "agent-1"
    assert agent["agent_kind"] == "cursor"


def test_resolve_agent_kind_precedence(monkeypatch):
    monkeypatch.delenv("COLLAB_AGENT_KIND", raising=False)
    for env_name, _ in agent_identity._AGENT_RUNTIME_MARKERS:
        monkeypatch.delenv(env_name, raising=False)

    # Explicit wins.
    assert (
        agent_identity.resolve_agent_kind(explicit_kind="Cursor", agent_id="a")
        == "cursor"
    )
    # Detected runtime marker.
    monkeypatch.setenv("CURSOR_TRACE_ID", "t")
    assert agent_identity.resolve_agent_kind(agent_id="a") == "cursor"
    monkeypatch.delenv("CURSOR_TRACE_ID", raising=False)
    # Unknown runtime but an agent id → generic "other".
    assert agent_identity.resolve_agent_kind(agent_id="agent-x") == "other"
    # Human (no agent) → None.
    assert agent_identity.resolve_agent_kind(agent_id=None) is None


def test_resolve_agent_kind_env(monkeypatch):
    monkeypatch.setenv("COLLAB_AGENT_KIND", "claude-code")
    assert agent_identity.resolve_agent_kind(agent_id="a") == "claude-code"


def test_resolve_origin():
    assert agent_identity.resolve_origin(None) == "human"
    assert agent_identity.resolve_origin("agent-1") == "agent"


def test_resolve_agent_label_no_runtime_fallback(monkeypatch):
    """The task label must not be polluted with the runtime name."""
    monkeypatch.delenv("COLLAB_AGENT_LABEL", raising=False)
    monkeypatch.setenv("CURSOR_TRACE_ID", "trace")
    assert agent_identity.resolve_agent_label() is None
    assert agent_identity.resolve_agent_label(explicit_label="fix-bug") == "fix-bug"


def test_read_clean_env_strips_inline_comment(monkeypatch):
    monkeypatch.setenv("COLLAB_AGENT_LABEL", "task-id  # local note")
    assert agent_identity._read_clean_env("COLLAB_AGENT_LABEL") == "task-id"


def test_sanitize_agent_id_rejects_invalid():
    assert agent_identity._sanitize_agent_id("") is None
    assert agent_identity._sanitize_agent_id("bad id!") is None


def test_is_agent_mode_requested_from_env(monkeypatch):
    monkeypatch.delenv("COLLAB_AGENT_MODE", raising=False)
    monkeypatch.delenv("CURSOR_TRACE_ID", raising=False)
    monkeypatch.setenv("COLLAB_AGENT_ID", "agent-x")
    assert agent_identity.is_agent_mode_requested() is True


def test_load_persisted_agent_id_os_error(monkeypatch, tmp_path):
    state = str(tmp_path / "state")
    os.makedirs(state, exist_ok=True)
    path = agent_identity._agent_id_file(state)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("agent-ok")

    def boom(*_args, **_kwargs):
        raise OSError("denied")

    monkeypatch.setattr(agent_identity.os.path, "isfile", boom)
    assert agent_identity.load_persisted_agent_id(state) is None


def test_persist_agent_id_fsync_os_error(monkeypatch, tmp_path):
    state = str(tmp_path / "state")
    monkeypatch.setattr(
        agent_identity.os,
        "fsync",
        lambda _fd: (_ for _ in ()).throw(OSError("disk")),
    )
    agent_identity.persist_agent_id(state, "agent-1")
    assert agent_identity.load_persisted_agent_id(state) == "agent-1"


def test_apply_agent_filter_null_uses_is_when_available():
    class Q:
        def __init__(self):
            self.called = None

        def is_(self, col, val):
            self.called = (col, val)
            return self

        def eq(self, col, val):
            self.called = ("eq", col, val)
            return self

    q = Q()
    agent_identity.apply_agent_filter(q, None)
    assert q.called == ("agent_id", "null")


def test_apply_agent_filter_null_falls_back_to_eq():
    class Q:
        def __init__(self):
            self.called = None

        def eq(self, col, val):
            self.called = (col, val)
            return self

    q = Q()
    agent_identity.apply_agent_filter(q, None)
    assert q.called == ("agent_id", None)


def test_resolve_agent_label_explicit():
    label = agent_identity.resolve_agent_label(explicit_label="  my-task  ")
    assert label == "my-task"


def test_format_conflict_message():
    msg = agent_identity.format_conflict_message("a.py", "alice", "agent-b", "task")
    assert "a.py" in msg
    assert "alice" in msg


def test_read_clean_env_rejects_hash_only_and_empty(monkeypatch):
    monkeypatch.setenv("COLLAB_AGENT_LABEL", "# comment only")
    assert agent_identity._read_clean_env("COLLAB_AGENT_LABEL") is None
    monkeypatch.setenv("COLLAB_AGENT_LABEL", "   ")
    assert agent_identity._read_clean_env("COLLAB_AGENT_LABEL") is None


def test_is_truthy_env_and_agent_mode_from_collab_agent_mode(monkeypatch):
    monkeypatch.delenv("COLLAB_AGENT_ID", raising=False)
    monkeypatch.delenv("CURSOR_TRACE_ID", raising=False)
    monkeypatch.setenv("COLLAB_AGENT_MODE", "yes")
    assert agent_identity._is_truthy_env("COLLAB_AGENT_MODE") is True
    assert agent_identity.is_agent_mode_requested() is True


def test_is_agent_mode_requested_ignores_runtime_marker(monkeypatch):
    """Strict attribution: a runtime marker alone must NOT enable agent mode.

    Only an explicit COLLAB_AGENT_ID / COLLAB_AGENT_MODE turns on agent attribution; the
    detected runtime is used for display only.
    """
    monkeypatch.delenv("COLLAB_AGENT_ID", raising=False)
    monkeypatch.delenv("COLLAB_AGENT_MODE", raising=False)
    monkeypatch.setenv("COMPOSER_SESSION_ID", "sess-1")
    assert agent_identity.is_agent_mode_requested() is False


def test_format_lock_owner_human_only():
    assert agent_identity.format_lock_owner("alice") == "@alice"


def test_session_token_seed_defensive_fallbacks():
    class BadStr:
        def __str__(self):
            raise RuntimeError("bad")

    class BadLower:
        def lower(self):
            raise RuntimeError("bad lower")

    seed = agent_identity.session_token_seed(BadStr(), BadStr(), BadLower(), BadLower())
    assert "unknown" in seed
    assert "localhost" in seed
    assert "project" in seed

    seed_agent = agent_identity.session_token_seed("alice", BadStr(), "host", "/p")
    assert ":agent:" in seed_agent or seed_agent.endswith("host:/p")


def test_resolve_daemon_pid_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_PID_FILE", "/custom/daemon.pid")
    path = agent_identity.resolve_daemon_pid_path(str(tmp_path), "agent-1")
    assert path == "/custom/daemon.pid"


def test_apply_agent_filter_scopes_named_agent():
    class Q:
        def __init__(self):
            self.called = None

        def eq(self, col, val):
            self.called = (col, val)
            return self

    q = Q()
    agent_identity.apply_agent_filter(q, "agent-a")
    assert q.called == ("agent_id", "agent-a")


def test_resolve_agent_id_invalid_explicit_falls_through(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_AGENT_MODE", "1")
    state = str(tmp_path / "state")
    resolved = agent_identity.resolve_agent_id(
        state, explicit_agent_id="bad id!", agent_mode=True
    )
    assert resolved is not None
    assert resolved.startswith("agent-")
