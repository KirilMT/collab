"""Release error/edge-case tests for LockClient.release().

NOTE: Standard ephemeral release and success release are covered in
test_lock_client_api.py (test_release_ephemeral_and_success).
All force_release / force_release_all tests are covered in
test_lock_client_force_release.py.
This file covers only unique error paths for release().
"""

from __future__ import annotations

from ._helpers import FakeResponse, load_lock_client_module, make_create_client

mod = load_lock_client_module()


def test_release_api_error(monkeypatch):
    """Release that returns a 500 API error."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod,
        "_get_create_client",
        lambda: make_create_client(
            FakeResponse(status=500, data=None, error={"message": "release fail"})
        ),
    )
    client = mod.LockClient(developer_id="releaser")
    ok, msg = client.release("tmp/x")
    assert ok is False


def test_release_api_exception(monkeypatch):
    """Release when supabase client raises an exception."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    class ExplodingClient:
        def table(self, *a, **k):
            return self

        def delete(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def execute(self):
            raise RuntimeError("Connection refused")

    monkeypatch.setattr(
        mod,
        "_get_create_client",
        lambda: (lambda url, key: ExplodingClient()),
    )
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    client = mod.LockClient(developer_id="releaser")
    ok, msg = client.release("tmp/x")
    assert ok is False


def test_release_not_acquired(monkeypatch):
    """Release when the file was not actually acquired (no supabase data)."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod,
        "_get_create_client",
        lambda: make_create_client(
            FakeResponse(status=404, data=None, error="not found")
        ),
    )
    client = mod.LockClient(developer_id="releaser")
    ok, msg = client.release("tmp/x")
    assert ok is False
    assert "API Error" in msg
