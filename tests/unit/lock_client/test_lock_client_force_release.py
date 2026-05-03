"""Force-release tests for LockClient (admin & non-admin flows)."""

from __future__ import annotations

from ._helpers import (
    FakeClient,
    FakeResponse,
    load_lock_client_module,
    make_create_client,
)

mod = load_lock_client_module()


def test_force_release(monkeypatch):
    """Test force-releasing a lock (admin operation)."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service_key")

    response = FakeResponse(status=200, data=[{"file_path": "src/app.py"}])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="admin")
    ok, msg = lc.force_release("src/app.py")
    assert isinstance(ok, bool)
    assert isinstance(msg, str)


def test_force_release_api_exception(monkeypatch):
    """Test force_release when API call raises an exception."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    class ExplodingClient(FakeClient):
        def execute(self):
            raise RuntimeError("Network timeout error")

    monkeypatch.setattr(
        mod,
        "_get_create_client",
        lambda: (lambda url, key: ExplodingClient(FakeResponse())),
    )
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    lc = mod.LockClient(developer_id="admin")
    ok, msg = lc.force_release("src/app.py")
    assert ok is False
    assert "API Error" in msg


def test_force_release_with_error(monkeypatch):
    """Test force_release when response has error."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(status=500, data=None, error="Server error")
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))

    lc = mod.LockClient(developer_id="admin")
    ok, msg = lc.force_release("src/app.py")
    assert ok is False
    assert "API Error" in msg


def test_force_release_no_lock(monkeypatch):
    """Test force_release when no lock exists to remove."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    # Need data to be truly None (not []) to hit the "No lock removed" path
    class NullDataResponse:
        status = 200
        data = None
        error = None

    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(NullDataResponse())
    )

    lc = mod.LockClient(developer_id="admin")
    ok, msg = lc.force_release("src/app.py")
    assert ok is False
    assert "No lock removed" in msg


def test_force_release_nonadmin_own_lock(monkeypatch):
    """Non-admin user can force-release their own lock."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setattr(mod, "SUPABASE_SERVICE_ROLE_KEY", None)

    status_resp = FakeResponse(
        status=200,
        data=[
            {
                "file_path": "src/app.py",
                "developer_id": "alice",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        ],
    )
    delete_resp = FakeResponse(status=200, data=[{"file_path": "src/app.py"}])

    call_log = []

    class TrackingClient(FakeClient):
        """Tracks method calls to verify developer_id filter is applied."""

        def __init__(self, resp):
            super().__init__(resp)
            self._current_resp = resp

        def select(self, *args, **kwargs):
            self._current_resp = status_resp
            return self

        def delete(self, *args, **kwargs):
            self._current_resp = delete_resp
            call_log.append("delete")
            return self

        def eq(self, col, val):
            call_log.append(("eq", col, val))
            return self

        def execute(self):
            return self._current_resp

    monkeypatch.setattr(
        mod,
        "_get_create_client",
        lambda: (lambda url, key: TrackingClient(status_resp)),
    )

    lc = mod.LockClient(developer_id="alice")
    ok, msg = lc.force_release("src/app.py")
    assert ok is True
    eq_calls = [c for c in call_log if isinstance(c, tuple) and c[0] == "eq"]
    dev_id_filters = [c for c in eq_calls if c[1] == "developer_id"]
    assert len(dev_id_filters) > 0, "Non-admin should filter by developer_id"
    assert dev_id_filters[-1][2] == "alice"


def test_force_release_nonadmin_other_dev_lock_denied(monkeypatch):
    """Non-admin user cannot force-release another developer's lock."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setattr(mod, "SUPABASE_SERVICE_ROLE_KEY", None)

    status_resp = FakeResponse(
        status=200,
        data=[
            {
                "file_path": "src/app.py",
                "developer_id": "bob",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        ],
    )
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(status_resp)
    )

    lc = mod.LockClient(developer_id="alice")
    ok, msg = lc.force_release("src/app.py")
    assert ok is False
    assert "Permission denied" in msg
    assert "@bob" in msg


def test_force_release_admin_other_dev_lock_succeeds(monkeypatch):
    """Admin user can force-release any developer's lock."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service_key")
    monkeypatch.setattr(mod, "SUPABASE_SERVICE_ROLE_KEY", "service_key")

    delete_resp = FakeResponse(status=200, data=[{"file_path": "src/app.py"}])

    call_log = []

    class TrackingClient(FakeClient):
        def delete(self, *args, **kwargs):
            call_log.append("delete")
            return self

        def eq(self, col, val):
            call_log.append(("eq", col, val))
            return self

    monkeypatch.setattr(
        mod,
        "_get_create_client",
        lambda: (lambda url, key: TrackingClient(delete_resp)),
    )

    lc = mod.LockClient(developer_id="admin_user")
    ok, msg = lc.force_release("src/app.py")
    assert ok is True


# ---------------------------------------------------------------------------
# force_release_all tests
# ---------------------------------------------------------------------------


def test_force_release_all_non_admin_returns_zero(monkeypatch):
    """force_release_all returns 0 immediately when user is not admin."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setattr(mod, "SUPABASE_SERVICE_ROLE_KEY", None)

    lc = mod.LockClient(developer_id="alice")
    assert not lc._is_admin
    result = lc.force_release_all()
    assert result == 0


def test_force_release_all_empty_locks_returns_zero(monkeypatch):
    """force_release_all returns 0 when there are no active locks."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    lc = mod.LockClient(local_only=True, developer_id="root")
    lc._is_admin = True
    monkeypatch.setattr(lc, "active", lambda: [])

    result = lc.force_release_all()
    assert result == 0


def test_force_release_all_counts_deleted_rows(monkeypatch):
    """force_release_all returns count from API-returned deleted rows."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    lc = mod.LockClient(local_only=True, developer_id="root")
    lc._is_admin = True

    locks = [{"file_path": f"src/file{i}.py"} for i in range(3)]
    monkeypatch.setattr(lc, "active", lambda: locks)

    class RespWithData:
        status = 200
        data = [
            {"file_path": "src/file0.py"},
            {"file_path": "src/file1.py"},
            {"file_path": "src/file2.py"},
        ]
        error = None

    class FakeDeleteClient:
        def table(self, *a, **k):
            return self

        def delete(self, *a, **k):
            return self

        def in_(self, *a, **k):
            return self

        def execute(self):
            return RespWithData()

    lc._client = FakeDeleteClient()
    result = lc.force_release_all()
    assert result == 3


def test_force_release_all_chunk_exception_returns_partial(monkeypatch):
    """force_release_all returns partial count when chunk delete raises."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    lc = mod.LockClient(local_only=True, developer_id="root")
    lc._is_admin = True

    locks = [{"file_path": f"src/file{i}.py"} for i in range(5)]
    monkeypatch.setattr(lc, "active", lambda: locks)

    class ExplodingClient:
        def table(self, *a, **k):
            return self

        def delete(self, *a, **k):
            return self

        def in_(self, *a, **k):
            return self

        def execute(self):
            raise RuntimeError("network error")

    lc._client = ExplodingClient()
    # Should return 0 (no chunks completed) without raising
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)
    result = lc.force_release_all()
    assert result == 0  # first chunk already fails


def test_force_release_all_api_error_returns_partial(monkeypatch):
    """force_release_all returns partial count when chunk returns error."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    lc = mod.LockClient(local_only=True, developer_id="root")
    lc._is_admin = True

    locks = [{"file_path": f"src/file{i}.py"} for i in range(3)]
    monkeypatch.setattr(lc, "active", lambda: locks)

    class ErrorResp:
        status = 500
        data = None
        error = "Server error"

    class ErrorClient:
        def table(self, *a, **k):
            return self

        def delete(self, *a, **k):
            return self

        def in_(self, *a, **k):
            return self

        def execute(self):
            return ErrorResp()

    lc._client = ErrorClient()
    result = lc.force_release_all()
    assert result == 0  # first chunk fails, 0 deleted so far


def test_force_release_all_locks_with_no_path_skipped(monkeypatch):
    """force_release_all skips lock entries that have empty or missing file_path."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    lc = mod.LockClient(local_only=True, developer_id="root")
    lc._is_admin = True

    # Only locks with empty/missing file_path - should result in 0 paths
    locks = [{"file_path": ""}, {"developer_id": "root"}, {"file_path": None}]
    monkeypatch.setattr(lc, "active", lambda: locks)

    result = lc.force_release_all()
    assert result == 0  # No valid paths -> count == 0 -> early return


# (test_force_release_permission_denied removed: duplicate of
#  test_force_release_nonadmin_other_dev_lock_denied above)
