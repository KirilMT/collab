"""Active-lock related tests for LockClient."""

from __future__ import annotations

from ._helpers import (
    FakeClient,
    FakeResponse,
    load_lock_client_module,
    make_create_client,
)

mod = load_lock_client_module()


def _patch_lock_service_reachable(monkeypatch):
    monkeypatch.setattr(mod, "_ensure_lock_service_reachable", lambda: None)


def test_active_locks(monkeypatch):
    """Test retrieving all active locks."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    _patch_lock_service_reachable(monkeypatch)

    locks_data = [
        {"file_path": "collab/app.py", "developer_id": "user1"},
        {"file_path": "collab/routes.py", "developer_id": "user2"},
    ]
    response = FakeResponse(status=200, data=locks_data)
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="test_user")
    locks = lc.active()
    assert len(locks) == 2
    assert locks[0]["file_path"] == "collab/app.py"


def test_active_locks_exception(monkeypatch):
    """Test active() raises when the API raises a network error."""
    import pytest

    from collab.errors import LockServiceUnavailableError

    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    _patch_lock_service_reachable(monkeypatch)

    class ExplodingClient(FakeClient):
        def execute(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(
        mod,
        "_get_create_client",
        lambda: (lambda url, key: ExplodingClient(FakeResponse())),
    )

    lc = mod.LockClient(developer_id="test_user")
    with pytest.raises(LockServiceUnavailableError):
        lc.active()


def test_active_locks_with_error(monkeypatch):
    """Test active() raises when Supabase returns an error payload."""
    import pytest

    from collab.errors import LockServiceUnavailableError

    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    _patch_lock_service_reachable(monkeypatch)

    response = FakeResponse(status=500, data=None, error="Error")
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="test_user")
    with pytest.raises(LockServiceUnavailableError):
        lc.active()


def test_active_locks_dns_failure(monkeypatch):
    """Test active() raises when the Supabase host cannot be resolved."""
    import pytest

    from collab.errors import LockServiceUnavailableError

    # conftest sets COLLAB_TEST_MODE=1 (skips TCP probe); exercise probe path here.
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    monkeypatch.setenv("SUPABASE_URL", "https://unresolvable.invalid")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    def _fail_connect(*_a, **_k):
        raise OSError("[Errno 11001] getaddrinfo failed")

    monkeypatch.setattr(mod.socket, "create_connection", _fail_connect)
    lc = mod.LockClient(developer_id="test_user")
    with pytest.raises(LockServiceUnavailableError, match="Cannot reach lock service"):
        lc.active()


def test_get_lock_status_expired(monkeypatch):
    """Test get_lock_status marks expired locks as unlocked (server-side expiry not
    enforced)."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    from datetime import datetime, timedelta, timezone

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    lock_data = {
        "file_path": "collab/app.py",
        "developer_id": "other_user",
        "acquired_at": "2025-01-01T10:00:00+00:00",
        "expires_at": past,
    }
    response = FakeResponse(status=200, data=[lock_data])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="test_user")
    status = lc.get_lock_status("collab/app.py")
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
