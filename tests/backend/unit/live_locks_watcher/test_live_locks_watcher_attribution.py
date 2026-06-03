"""Strict-attribution parity tests for live_locks_watcher (PyCharm watcher).

These verify that the PyCharm watcher behaves like ``collab watch``:

* bulk auto-locks are attributed to the human unless a dedicated agent watcher
  opts in via ``COLLAB_WATCHER_AGENT_ID``;
* the human watcher never fights the same developer's AI-agent locks (it skips
  acquiring them) and cleans them up once the file is no longer in progress.
"""

from __future__ import annotations

from types import SimpleNamespace

from ._helpers import load_watcher_module


class _FakeQuery:
    """Minimal PostgREST query double that honours eq/is_ filters and delete."""

    def __init__(self, client: "_FakeClient") -> None:
        self.client = client
        self.op = "select"
        self.filters: list[tuple[str, str, object]] = []

    def select(self, *_args):
        self.op = "select"
        return self

    def delete(self):
        self.op = "delete"
        return self

    def update(self, _vals):
        self.op = "update"
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self.filters.append(("is", col, val))
        return self

    def _match(self, row: dict) -> bool:
        for kind, col, val in self.filters:
            cur = row.get(col)
            if kind == "is":
                if val == "null" and cur is not None:
                    return False
            else:  # eq (eq(col, None) is treated as IS NULL)
                if val is None:
                    if cur is not None:
                        return False
                elif cur != val:
                    return False
        return True

    def execute(self):
        matched = [r for r in self.client.rows if self._match(r)]
        if self.op == "delete":
            for r in list(matched):
                self.client.rows.remove(r)
        return SimpleNamespace(data=list(matched))


class _FakeClient:
    def __init__(self, rows=None, conflict_paths=None) -> None:
        self.rows = [dict(r) for r in (rows or [])]
        self.rpc_calls: list[tuple[str, dict]] = []
        self.conflict_paths = set(conflict_paths or [])

    def table(self, _name):
        return _FakeQuery(self)

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        fp = params.get("p_file_path")
        status = "conflict" if fp in self.conflict_paths else "ok"
        return SimpleNamespace(
            execute=lambda: SimpleNamespace(data=[{"status": status, "owner": "alice"}])
        )


def _human(mod, monkeypatch) -> None:
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "AGENT_ID", None)
    monkeypatch.setattr(mod, "AGENT_LABEL", None)
    monkeypatch.setattr(mod, "AGENT_KIND", None)
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda d: False)


# --------------------------------------------------------------------------- #
# _resolve_watcher_identity
# --------------------------------------------------------------------------- #


def test_resolve_watcher_identity_forces_human_by_default(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.delenv("COLLAB_WATCHER_AGENT_ID", raising=False)
    assert mod._resolve_watcher_identity("agent-x", "task", "cursor") == (
        None,
        None,
        None,
    )


def test_resolve_watcher_identity_respects_dedicated_agent(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setenv("COLLAB_WATCHER_AGENT_ID", "1")
    assert mod._resolve_watcher_identity("agent-x", "task", "cursor") == (
        "agent-x",
        "task",
        "cursor",
    )


# --------------------------------------------------------------------------- #
# _fetch_dev_other_identity_locks
# --------------------------------------------------------------------------- #


def test_fetch_dev_other_identity_locks_returns_only_agent_rows(monkeypatch):
    mod = load_watcher_module()
    _human(mod, monkeypatch)
    client = _FakeClient(
        rows=[
            {"file_path": "human.py", "developer_id": "alice", "agent_id": None},
            {"file_path": "agent.py", "developer_id": "alice", "agent_id": "agent-x"},
            {"file_path": "bob.py", "developer_id": "bob", "agent_id": None},
        ]
    )
    out = mod._fetch_dev_other_identity_locks(client)
    assert set(out) == {"agent.py"}


def test_fetch_dev_other_identity_locks_handles_errors(monkeypatch):
    mod = load_watcher_module()
    _human(mod, monkeypatch)

    class Boom:
        def table(self, _name):
            raise RuntimeError("network down")

    assert mod._fetch_dev_other_identity_locks(Boom()) == {}


# --------------------------------------------------------------------------- #
# _release_developer_scope
# --------------------------------------------------------------------------- #


def test_release_developer_scope_deletes_dev_lock(monkeypatch):
    mod = load_watcher_module()
    _human(mod, monkeypatch)
    client = _FakeClient(
        rows=[{"file_path": "agent.py", "developer_id": "alice", "agent_id": "agent-x"}]
    )
    mod._local_owned_locks.add("agent.py")

    assert mod._release_developer_scope(client, "agent.py") is True
    assert client.rows == []
    assert "agent.py" not in mod._local_owned_locks


def test_release_developer_scope_skips_ephemeral(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "ci-bot")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda d: True)
    client = _FakeClient(rows=[{"file_path": "x.py", "developer_id": "ci-bot"}])

    assert mod._release_developer_scope(client, "x.py") is False
    assert client.rows  # nothing deleted


def test_release_developer_scope_handles_errors(monkeypatch):
    mod = load_watcher_module()
    _human(mod, monkeypatch)

    class Boom:
        def table(self, _name):
            raise RuntimeError("boom")

    assert mod._release_developer_scope(Boom(), "x.py") is False


# --------------------------------------------------------------------------- #
# _filter_agent_held_new_files
# --------------------------------------------------------------------------- #


def test_filter_agent_held_new_files_drops_agent_paths(monkeypatch):
    mod = load_watcher_module()
    _human(mod, monkeypatch)
    client = _FakeClient(
        rows=[{"file_path": "agent.py", "developer_id": "alice", "agent_id": "agent-x"}]
    )
    out = mod._filter_agent_held_new_files(client, {"agent.py", "fresh.py"})
    assert out == {"fresh.py"}


def test_filter_agent_held_new_files_empty_is_noop(monkeypatch):
    mod = load_watcher_module()
    _human(mod, monkeypatch)
    client = _FakeClient(rows=[])
    assert mod._filter_agent_held_new_files(client, set()) == set()


def test_filter_agent_held_new_files_no_overlap(monkeypatch):
    mod = load_watcher_module()
    _human(mod, monkeypatch)
    client = _FakeClient(
        rows=[{"file_path": "agent.py", "developer_id": "alice", "agent_id": "agent-x"}]
    )
    out = mod._filter_agent_held_new_files(client, {"fresh.py"})
    assert out == {"fresh.py"}


# --------------------------------------------------------------------------- #
# reconcile integration: skip + cleanup
# --------------------------------------------------------------------------- #


def test_reconcile_skips_and_cleans_dev_agent_locks(monkeypatch):
    mod = load_watcher_module()
    _human(mod, monkeypatch)
    mod._local_owned_locks.clear()
    mod._active_conflicts.clear()
    monkeypatch.setattr(mod, "_should_ignore_path", lambda p: False)
    monkeypatch.setattr(mod, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(mod, "_notify", lambda *a, **k: None)
    # a.py: fresh dirty file (no lock) -> acquire as human.
    # b.py: dirty file already held by this developer's agent -> SKIP.
    # c.py: clean file still held by the agent -> STALE-RELEASE (dev scope).
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: {"a.py", "b.py"})
    client = _FakeClient(
        rows=[
            {"file_path": "b.py", "developer_id": "alice", "agent_id": "agent-x"},
            {"file_path": "c.py", "developer_id": "alice", "agent_id": "agent-x"},
        ]
    )

    mod._reconcile_on_startup(client)

    # Fresh file acquired; agent-held dirty file skipped.
    assert "a.py" in mod._local_owned_locks
    assert "b.py" not in mod._local_owned_locks
    # Only a.py was acquired via RPC (b.py/c.py never fought).
    acquired = [p["p_file_path"] for n, p in client.rpc_calls if n == "acquire_lock"]
    assert acquired == ["a.py"]
    # Clean agent lock (c.py) cleaned up; dirty agent lock (b.py) preserved.
    remaining = {r["file_path"] for r in client.rows}
    assert "c.py" not in remaining
    assert "b.py" in remaining

    mod._local_owned_locks.clear()
