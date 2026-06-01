"""Multi-agent lock ownership tests for LockClient."""

from __future__ import annotations

from unittest.mock import MagicMock

from ._helpers import (
    FakeClient,
    FakeResponse,
    load_lock_client_module,
    make_create_client,
)

mod = load_lock_client_module()


def test_acquire_conflict_same_developer_different_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_AGENT_MODE", "1")
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(mod, "_ensure_lock_service_reachable", lambda: None)

    test_file = tmp_path / "app.py"
    test_file.write_text("# code")

    conflict = FakeResponse(
        status=200,
        data=[
            {
                "status": "conflict",
                "owner": "alice",
                "agent_id": "agent-other",
                "lock_token": "tok",
            }
        ],
    )
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(conflict))

    state = str(tmp_path / ".collab")
    monkeypatch.setattr(mod, "_get_state_dir", lambda: state)

    client_a = mod.LockClient(developer_id="alice", agent_id="agent-a")
    ok, msg = client_a.acquire(str(test_file))
    assert ok is False
    assert "agent-other" in msg or "alice" in msg


def test_release_scoped_to_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    captured: dict = {}

    class RecordingTable:
        def __init__(self, name: str):
            self.name = name
            self._filters: list[tuple[str, str]] = []

        def delete(self):
            return self

        def eq(self, col, val):
            self._filters.append((col, val))
            return self

        def is_(self, col, op):
            self._filters.append((col, op))
            return self

        def execute(self):
            captured["filters"] = list(self._filters)
            return FakeResponse(status=200, data=[{"file_path": "x"}])

    class RecordingClient(FakeClient):
        def table(self, name):
            captured["table"] = RecordingTable(name)
            return captured["table"]

    monkeypatch.setattr(
        mod,
        "_get_create_client",
        lambda: lambda url, key: RecordingClient(FakeResponse()),
    )
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))

    client = mod.LockClient(developer_id="alice", agent_id="agent-a")
    client.release("collab/app.py")

    filters = dict(captured.get("filters", []))
    assert filters.get("developer_id") == "alice"
    assert filters.get("agent_id") == "agent-a"


def test_force_release_same_developer_different_agent_allowed(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    test_file = tmp_path / "app.py"
    test_file.write_text("# code")
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))

    status_row = {
        "is_locked": True,
        "locked_by": "alice",
        "locked_by_agent_id": "agent-other",
        "can_edit": False,
    }

    client = mod.LockClient(developer_id="alice", agent_id="agent-a")
    client._is_admin = False
    client.get_lock_status = MagicMock(  # type: ignore[method-assign]
        return_value=status_row
    )

    second_eq = MagicMock()
    second_eq.execute.return_value = FakeResponse(status=200, data=[{}])
    first_eq = MagicMock()
    first_eq.eq.return_value = second_eq
    table_mock = MagicMock()
    table_mock.delete.return_value.eq.return_value = first_eq
    client._client = MagicMock()
    client._client.table.return_value = table_mock

    ok, msg = client.force_release(str(test_file))
    assert ok is True
    assert "force-released" in msg


def test_refresh_pid_file_respects_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_PID_FILE", str(tmp_path / "custom.pid"))
    before = mod.PID_FILE
    mod._refresh_pid_file("agent-a")
    assert mod.PID_FILE == before


def test_lock_owned_by_me_helper():
    client = mod.LockClient.__new__(mod.LockClient)
    client.developer_id = "alice"
    client.agent_id = "agent-a"

    assert client._lock_owned_by_me({"developer_id": "alice", "agent_id": "agent-a"})
    assert not client._lock_owned_by_me(
        {"developer_id": "alice", "agent_id": "agent-b"}
    )
    assert not client._lock_owned_by_me({"developer_id": "bob", "agent_id": "agent-a"})
