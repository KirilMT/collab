"""Acquire-related tests for LockClient."""

from __future__ import annotations

from ._helpers import (
    FakeClient,
    FakeResponse,
    load_lock_client_module,
    make_create_client,
    make_get_create_client,
)

mod = load_lock_client_module()


def test_acquire_multiple_success(monkeypatch, tmp_path):
    """Test batch lock acquisition."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    file1 = tmp_path / "src" / "app.py"
    file2 = tmp_path / "src" / "routes.py"
    file1.parent.mkdir(parents=True)
    file1.write_text("# code")
    file2.write_text("# code")

    response = FakeResponse(status=200, data=[{"status": "ok", "lock_id": "batch123"}])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="test_user")
    ok, failed, msg = lc.acquire_multiple([str(file1), str(file2)])
    assert isinstance(ok, bool)
    assert isinstance(failed, list)


# RESTORED: test_acquire_and_release_ephemeral (migrated from additional)
def test_acquire_and_release_ephemeral(tmp_path, monkeypatch):
    # Create a temp file to acquire
    f = tmp_path / "file.txt"
    f.write_text("x")

    client = object.__new__(mod.LockClient)
    # Simulate ephemeral developer
    client.developer_id = "test_dev_123"
    client._is_ephemeral = True

    ok, token = mod.LockClient.acquire(client, str(f))
    assert ok and token.startswith("ephemeral-")

    ok_rel, msg = mod.LockClient.release(client, str(f))
    assert ok_rel and "ephemeral" in msg


# RESTORED: test_acquire_api_exception (migrated from monolith)
def test_acquire_api_exception(monkeypatch, tmp_path):
    """Test acquire when API call raises an exception."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    test_file = tmp_path / "src" / "app.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("# code")

    class ExplodingClient(FakeClient):
        def execute(self):
            raise RuntimeError("API exploded")

    monkeypatch.setattr(
        mod,
        "_get_create_client",
        lambda: lambda url, key: ExplodingClient(FakeResponse()),
    )
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))

    lc = mod.LockClient(developer_id="test_user")
    ok, msg = lc.acquire(str(test_file))
    assert ok is False
    assert "API Error" in msg


# RESTORED: test_acquire_missing_file_returns_false (migrated from monolith)
def test_acquire_missing_file_returns_false(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon:fake")
    monkeypatch.setattr(
        mod,
        "_get_create_client",
        make_get_create_client({"status": 200, "data": []}),
    )

    lc = mod.LockClient(developer_id="tester")
    ok, msg = lc.acquire("this/path/does/not/exist.file")
    assert not ok
    assert "does not exist" in msg


def test_acquire_missing_file_allowed_when_in_progress(monkeypatch):
    """Deleted paths remain lockable when git marks them in progress."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(status=200, data=[{"status": "ok"}])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="tester")
    target = ".github/workflows/validate-on-pr.yml"
    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", lambda: [target])

    ok, token = lc.acquire(target)
    assert ok is True
    assert isinstance(token, str) and len(token) > 0


def test_acquire_missing_file_in_progress_lookup_error(monkeypatch):
    """If in-progress detection fails, missing paths are rejected safely."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod,
        "_get_create_client",
        make_get_create_client({"status": 200, "data": []}),
    )

    lc = mod.LockClient(developer_id="tester")

    def _boom():
        raise RuntimeError("git scan failed")

    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", _boom)
    ok, msg = lc.acquire("deleted/path.py")
    assert ok is False
    assert "does not exist" in msg


def test_acquire_directory_returns_false(monkeypatch, tmp_path):
    """Directory paths are not lockable."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", make_get_create_client({"status": 200, "data": []})
    )

    instance_dir = tmp_path / "apps" / "reporting" / "instance"
    instance_dir.mkdir(parents=True)
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))

    lc = mod.LockClient(developer_id="tester")
    ok, msg = lc.acquire("apps/reporting/instance")
    assert ok is False
    assert "directory" in msg.lower()


def test_acquire_ephemeral(tmp_path):
    """Acquire a file with ephemeral developer ID (local_only)."""
    mod = load_lock_client_module()
    f = tmp_path / "a.txt"
    f.write_text("x")
    client = mod.LockClient(local_only=True, developer_id="test_dev_joe")
    ok, token = client.acquire(str(f))
    assert ok is True
    assert isinstance(token, str) and token.startswith("ephemeral-")


def test_acquire_success_and_conflict(tmp_path):
    """Acquire success and conflict scenarios via supabase client."""
    mod = load_lock_client_module()
    f = tmp_path / "b.txt"
    f.write_text("x")

    # Success
    client = mod.LockClient(local_only=True, developer_id="alice")
    client._client = FakeClient(FakeResponse(status=200, data=[{"status": "ok"}]))
    ok, token = client.acquire(str(f))
    assert ok is True
    assert isinstance(token, str) and len(token) == 16

    # Conflict
    client2 = mod.LockClient(local_only=True, developer_id="bob")
    client2._client = FakeClient(
        FakeResponse(status=200, data=[{"status": "conflict", "owner": "eve"}])
    )
    ok2, msg2 = client2.acquire(str(f))
    assert ok2 is False
    assert "@eve" in msg2


def test_acquire_api_error(tmp_path):
    """Acquire when supabase returns a 500 error."""
    mod = load_lock_client_module()
    f = tmp_path / "c.txt"
    f.write_text("x")
    client = mod.LockClient(local_only=True, developer_id="carol")
    client._client = FakeClient(
        FakeResponse(status=500, data=None, error={"message": "rpc fail"})
    )
    ok, msg = client.acquire(str(f))
    assert ok is False
    assert "API Error" in msg


def test_acquire_unexpected_response(tmp_path):
    """Cover line 738 unexpected-response path.

    acquire returns "Unexpected response" when status is not 200/201 and data is empty.
    """
    mod = load_lock_client_module()
    f = tmp_path / "u.txt"
    f.write_text("x")
    client = mod.LockClient(local_only=True, developer_id="user1")
    # Return empty data list and non-200/201 status to trigger line 738
    client._client = FakeClient(FakeResponse(status=503, data=[], error=None))
    ok, msg = client.acquire(str(f))
    assert ok is False
    assert "Unexpected response" in msg
