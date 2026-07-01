"""Strict user-vs-agent attribution tests for LockClient.

Covers: origin/agent_kind propagation through ``acquire``, developer-scoped release for
watcher cleanup, and the reconcile rules that keep the human watcher from fighting or
downgrading an agent's locks.
"""

from __future__ import annotations

from ._helpers import FakeClient, FakeResponse, load_lock_client_module

mod = load_lock_client_module()


class _RpcRecorder(FakeClient):
    def __init__(self, resp):
        super().__init__(resp)
        self.rpc_params = None

    def rpc(self, name, params=None, *a, **k):
        self.rpc_params = params
        return self


def _make_client(monkeypatch, tmp_path, recorder, **kwargs):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(mod, "_ensure_lock_service_reachable", lambda: None)
    monkeypatch.setattr(mod, "_get_state_dir", lambda: str(tmp_path / ".collab"))
    monkeypatch.setattr(mod, "_get_create_client", lambda: (lambda url, key: recorder))
    return mod.LockClient(developer_id="alice", **kwargs)


def test_acquire_sends_agent_origin_and_kind(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_AGENT_KIND", "cursor")
    recorder = _RpcRecorder(
        FakeResponse(status=200, data=[{"status": "ok", "lock_token": "t"}])
    )
    test_file = tmp_path / "app.py"
    test_file.write_text("# code")

    client = _make_client(
        monkeypatch,
        tmp_path,
        recorder,
        agent_id="agent-a",
        agent_label="fix-ci",
    )
    ok, _ = client.acquire(str(test_file))

    assert ok is True
    params = recorder.rpc_params
    assert params["p_origin"] == "agent"
    assert params["p_agent_id"] == "agent-a"
    assert params["p_agent_label"] == "fix-ci"
    assert params["p_agent_kind"] == "cursor"


def test_acquire_human_sends_human_origin(monkeypatch, tmp_path):
    recorder = _RpcRecorder(
        FakeResponse(status=200, data=[{"status": "ok", "lock_token": "t"}])
    )
    test_file = tmp_path / "app.py"
    test_file.write_text("# code")

    client = _make_client(monkeypatch, tmp_path, recorder)
    ok, _ = client.acquire(str(test_file))

    assert ok is True
    params = recorder.rpc_params
    assert params["p_origin"] == "human"
    assert params["p_agent_id"] is None
    assert params["p_agent_kind"] is None


def test_release_developer_scope_ignores_agent(monkeypatch, tmp_path):
    captured: dict = {}

    class RecordingTable:
        def __init__(self):
            self._filters: list[tuple] = []

        def delete(self):
            return self

        def eq(self, col, val):
            self._filters.append((col, val))
            return self

        def execute(self):
            captured["filters"] = list(self._filters)
            return FakeResponse(status=200, data=[{"file_path": "x"}])

    class RecordingClient(FakeClient):
        def table(self, name):
            captured["table"] = name
            return RecordingTable()

    recorder = RecordingClient(FakeResponse())
    client = _make_client(monkeypatch, tmp_path, recorder, agent_id="agent-a")

    assert client._release_developer_scope("collab/app.py") is True
    filters = dict(captured["filters"])
    assert filters.get("developer_id") == "alice"
    # Developer-scoped release must NOT constrain by agent_id.
    assert "agent_id" not in filters


def test_release_developer_scope_ephemeral_noop(monkeypatch, tmp_path):
    recorder = FakeClient(FakeResponse())
    client = _make_client(monkeypatch, tmp_path, recorder)
    client._is_ephemeral = True
    assert client._release_developer_scope("collab/app.py") is True


def test_release_developer_scope_handles_exception(monkeypatch, tmp_path):
    class BoomClient(FakeClient):
        def table(self, name):
            raise RuntimeError("network down")

    recorder = BoomClient(FakeResponse())
    client = _make_client(monkeypatch, tmp_path, recorder)
    assert client._release_developer_scope("collab/app.py") is False


def test_release_developer_scope_handles_error_response(monkeypatch, tmp_path):
    recorder = FakeClient(FakeResponse(status=400, data=None, error="boom"))
    client = _make_client(monkeypatch, tmp_path, recorder)
    assert client._release_developer_scope("collab/app.py") is False


def test_reconcile_skips_and_cleans_agent_locks(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_SILENT_DAEMON", "1")
    recorder = FakeClient(FakeResponse())
    client = _make_client(monkeypatch, tmp_path, recorder)  # human watcher

    # a.py: in progress AND held by this developer's agent -> skip (no re-lock).
    # b.py: agent-held but no longer in progress -> developer-scoped cleanup.
    active_rows = [
        {
            "file_path": "a.py",
            "developer_id": "alice",
            "agent_id": "x",
            "origin": "agent",
        },
        {
            "file_path": "b.py",
            "developer_id": "alice",
            "agent_id": "x",
            "origin": "agent",
        },
        # Defensive: a row with an empty file_path must be skipped.
        {
            "file_path": "",
            "developer_id": "alice",
            "agent_id": "x",
            "origin": "agent",
        },
    ]
    acquired: list = []
    released: list = []

    monkeypatch.setattr(
        client, "_get_modified_and_unpushed_files", lambda: (["a.py"], True)
    )
    monkeypatch.setattr(client, "active", lambda: active_rows)
    monkeypatch.setattr(client, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(client, "_get_session_token", lambda: "tok")
    monkeypatch.setattr(
        client,
        "acquire_multiple",
        lambda paths, **k: acquired.extend(paths) or (True, [], "ok"),
    )
    monkeypatch.setattr(
        client,
        "_release_developer_scope",
        lambda fp: released.append(fp) or True,
    )

    client._reconcile()

    assert "a.py" not in acquired  # never fights the agent's in-progress lock
    assert released == ["b.py"]  # cleans up the agent's stale lock on push


def test_acquire_human_renewal_reflects_preserved_agent_attribution(
    monkeypatch, tmp_path, caplog
):
    """Sticky attribution (#169): a human auto-lock that renews an existing agent lock
    gets an ``ok`` whose returned row still carries the agent identity.

    The client must not claim a human "Auto-Watch Sync" reason for it — it logs that the
    AI-agent lock was preserved.
    """
    import logging

    recorder = _RpcRecorder(
        FakeResponse(
            status=200,
            data=[
                {
                    "status": "ok",
                    "lock_token": "t",
                    "developer_id": "alice",
                    "agent_id": "agent-x",
                    "agent_label": "fix-ci",
                    "agent_kind": "cursor",
                }
            ],
        )
    )
    test_file = tmp_path / "app.py"
    test_file.write_text("# code")

    # Human client (no agent identity) — the background watcher case.
    client = _make_client(monkeypatch, tmp_path, recorder)

    with caplog.at_level(logging.INFO, logger="collab.lock_client"):
        ok, _ = client.acquire(str(test_file), reason="Auto-Watch Sync")

    assert ok is True
    # The human watcher sends human origin ...
    assert recorder.rpc_params["p_origin"] == "human"
    assert recorder.rpc_params["p_agent_id"] is None
    # ... but the log reflects that the stored lock stayed an AI-agent lock.
    assert any("preserved AI agent lock" in rec.getMessage() for rec in caplog.records)


def test_acquire_human_ok_without_agent_uses_plain_reason(
    monkeypatch, tmp_path, caplog
):
    """A human lock on a file with no agent owner logs the plain reason (no spurious
    "preserved AI agent lock" note)."""
    import logging

    recorder = _RpcRecorder(
        FakeResponse(
            status=200,
            data=[
                {
                    "status": "ok",
                    "lock_token": "t",
                    "developer_id": "alice",
                    "agent_id": None,
                }
            ],
        )
    )
    test_file = tmp_path / "app.py"
    test_file.write_text("# code")

    client = _make_client(monkeypatch, tmp_path, recorder)

    with caplog.at_level(logging.INFO, logger="collab.lock_client"):
        ok, _ = client.acquire(str(test_file), reason="Auto-Watch Sync")

    assert ok is True
    assert not any(
        "preserved AI agent lock" in rec.getMessage() for rec in caplog.records
    )
