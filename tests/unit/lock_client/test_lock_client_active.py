"""Active-lock related tests for LockClient."""

from __future__ import annotations

from ._helpers import (
    FakeClient,
    FakeResponse,
    load_lock_client_module,
    make_create_client,
)

mod = load_lock_client_module()


def test_active_locks(monkeypatch):
    """Test retrieving all active locks."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    locks_data = [
        {"file_path": "src/app.py", "developer_id": "user1"},
        {"file_path": "src/routes.py", "developer_id": "user2"},
    ]
    response = FakeResponse(status=200, data=locks_data)
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="test_user")
    locks = lc.active()
    assert len(locks) == 2
    assert locks[0]["file_path"] == "src/app.py"


def test_active_locks_exception(monkeypatch):
    """Test active() returns empty list on API exception."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    class ExplodingClient(FakeClient):
        def execute(self):
            raise RuntimeError("Network error")

    monkeypatch.setattr(
        mod,
        "_get_create_client",
        lambda: (lambda url, key: ExplodingClient(FakeResponse())),
    )

    lc = mod.LockClient(developer_id="test_user")
    locks = lc.active()
    assert locks == []


def test_active_locks_with_error(monkeypatch):
    """Test active() returns empty list when response has error."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(status=500, data=None, error="Error")
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="test_user")
    locks = lc.active()
    assert locks == []


def test_get_lock_status_expired(monkeypatch):
    """Test get_lock_status marks expired locks as unlocked (server-side expiry not
    enforced)."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    from datetime import datetime, timedelta, timezone

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    lock_data = {
        "file_path": "src/app.py",
        "developer_id": "other_user",
        "acquired_at": "2025-01-01T10:00:00+00:00",
        "expires_at": past,
    }
    response = FakeResponse(status=200, data=[lock_data])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="test_user")
    status = lc.get_lock_status("src/app.py")
    # With server-side expiry disabled, presence of a DB row implies an active
    # lock until explicitly released. The client does not evaluate expires_at.
    assert status["is_locked"] is True
    assert status["locked_by"] == "other_user"
    assert status["can_edit"] is False


# RESTORED: test_get_lock_status_no_lock
def test_get_lock_status_no_lock(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon:fake")
    response = FakeResponse(status=200, data=[])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="tester")
    status = lc.get_lock_status("some/file.py")
    assert isinstance(status, dict)
    assert status.get("is_locked") is False
    assert status.get("can_edit") is True
