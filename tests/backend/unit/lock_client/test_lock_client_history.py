"""History-related tests for LockClient."""

from __future__ import annotations

import pytest

from ._helpers import FakeResponse, load_lock_client_module, make_create_client

mod = load_lock_client_module()


def test_history_all_files(monkeypatch):
    """Test fetching lock history for all files."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    history_data = [
        {"file_path": "src/app.py", "developer_id": "user1", "action": "acquired"},
        {"file_path": "src/app.py", "developer_id": "user1", "action": "released"},
    ]
    response = FakeResponse(status=200, data=history_data)
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="test_user")
    history = lc.history()
    assert isinstance(history, list)


def test_history_specific_file(monkeypatch):
    """Test fetching lock history for a specific file."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    history_data = [
        {"file_path": "src/app.py", "developer_id": "user1", "action": "acquired"}
    ]
    response = FakeResponse(status=200, data=history_data)
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="test_user")
    history = lc.history(file_path="src/app.py", limit=10)
    assert isinstance(history, list)


def test_history_exception(monkeypatch):
    """Test history returns empty list when API raises exception."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    class ExplodingClient:
        def __init__(self, resp):
            self._resp = resp

        def table(self, *a, **k):
            raise RuntimeError("DB error")

    monkeypatch.setattr(
        mod, "_get_create_client", lambda: (lambda url, key: ExplodingClient(None))
    )

    lc = mod.LockClient(developer_id="test_user")
    history = lc.history()
    assert history == []


# RESTORED: test_history_fallback_exception
def test_history_fallback_exception(monkeypatch):
    """Test history partial exception."""
    monkeypatch.setattr(mod, "SUPABASE_SERVICE_ROLE_KEY", "admin_key")
    monkeypatch.setattr(mod, "_supabase_create_client", lambda url, key: None)

    class FakeQuery:
        def select(self, *args):
            return self

        def eq(self, *args):
            return self

        def is_(self, *args):
            return self

        def ilike(self, *args):
            return self

        def order(self, *args, **kwargs):
            return self

        def limit(self, *args):
            return self

        def execute(self):
            return None

    class FakeClient:
        def table(self, *args):
            return FakeQuery()

    client = getattr(mod, "LockClient")()
    client._client = FakeClient()
    monkeypatch.setattr(client, "_parse_response", lambda res: (False, [], None))

    # Pass an object that throws when string methods are called
    class Exploder:
        def replace(self, *args, **kwargs):
            raise Exception("boom")

    res = client.history(limit=5, file_path=Exploder())
    assert res == []


def test_prune_history_rejects_invalid_retention(monkeypatch):
    """prune_history should reject retention values < 1."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    lc = mod.LockClient(developer_id="test_user")
    ok, deleted, msg = lc.prune_history(retention_days=0)

    assert ok is False
    assert deleted == 0
    assert ">= 1" in msg


def test_prune_history_rpc_success(monkeypatch):
    """prune_history should return deleted count from RPC when available."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    class RpcClient:
        def rpc(self, *args, **kwargs):
            return self

        def execute(self):
            return None

    lc = mod.LockClient(developer_id="test_user")
    lc._client = RpcClient()
    monkeypatch.setattr(
        lc, "_parse_response", lambda _res: (200, [{"prune_lock_history": 7}], None)
    )
    monkeypatch.setattr(mod, "_retry_on_network_error", lambda fn: fn())

    ok, deleted, msg = lc.prune_history(retention_days=30)

    assert ok is True
    assert deleted == 7
    assert msg == "history-pruned"


def test_prune_history_fallback_success(monkeypatch):
    """prune_history should fall back to REST delete when RPC is unavailable."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    class FallbackQuery:
        def __init__(self):
            self.lt_called = False

        def delete(self):
            return self

        def lt(self, *_args, **_kwargs):
            self.lt_called = True
            return self

        def execute(self):
            return None

    class FallbackClient:
        def __init__(self):
            self.query = FallbackQuery()

        def rpc(self, *args, **kwargs):
            raise RuntimeError("missing RPC")

        def table(self, *args, **kwargs):
            return self.query

    lc = mod.LockClient(developer_id="test_user")
    fake_client = FallbackClient()
    lc._client = fake_client
    monkeypatch.setattr(mod, "_retry_on_network_error", lambda fn: fn())
    monkeypatch.setattr(
        lc, "_parse_response", lambda _res: (200, [{"id": 1}, {"id": 2}], None)
    )

    ok, deleted, msg = lc.prune_history(retention_days=30)

    assert ok is True
    assert deleted == 2
    assert msg == "history-pruned-fallback"
    assert fake_client.query.lt_called is True


def test_prune_history_fallback_api_error(monkeypatch):
    """prune_history should surface API errors from fallback delete path."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    class FallbackQuery:
        def delete(self):
            return self

        def lt(self, *_args, **_kwargs):
            return self

        def execute(self):
            return None

    class FallbackClient:
        def rpc(self, *args, **kwargs):
            raise RuntimeError("missing RPC")

        def table(self, *args, **kwargs):
            return FallbackQuery()

    lc = mod.LockClient(developer_id="test_user")
    lc._client = FallbackClient()
    monkeypatch.setattr(mod, "_retry_on_network_error", lambda fn: fn())
    monkeypatch.setattr(
        lc, "_parse_response", lambda _res: (500, None, {"message": "boom"})
    )

    ok, deleted, msg = lc.prune_history(retention_days=30)

    assert ok is False
    assert deleted == 0
    assert "API Error" in msg


@pytest.mark.parametrize(
    ("rpc_data", "expected_deleted"),
    [
        ([{"prune_lock_history": "not-an-int", "count": "3"}], 3),
        ([11], 11),
        (9, 9),
    ],
)
def test_prune_history_rpc_data_shapes(monkeypatch, rpc_data, expected_deleted):
    """prune_history should parse multiple RPC return shapes robustly."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    class RpcClient:
        def rpc(self, *args, **kwargs):
            return self

        def execute(self):
            return None

    lc = mod.LockClient(developer_id="test_user")
    lc._client = RpcClient()
    monkeypatch.setattr(lc, "_parse_response", lambda _res: (200, rpc_data, None))
    monkeypatch.setattr(mod, "_retry_on_network_error", lambda fn: fn())

    ok, deleted, msg = lc.prune_history(retention_days=30)

    assert ok is True
    assert deleted == expected_deleted
    assert msg == "history-pruned"


def test_prune_history_fallback_exception_path(monkeypatch):
    """prune_history should return API Error when fallback delete raises."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    class FallbackClient:
        def rpc(self, *args, **kwargs):
            raise RuntimeError("missing RPC")

        def table(self, *args, **kwargs):
            raise RuntimeError("delete boom")

    lc = mod.LockClient(developer_id="test_user")
    lc._client = FallbackClient()
    monkeypatch.setattr(mod, "_retry_on_network_error", lambda fn: fn())

    ok, deleted, msg = lc.prune_history(retention_days=30)

    assert ok is False
    assert deleted == 0
    assert "delete boom" in msg
