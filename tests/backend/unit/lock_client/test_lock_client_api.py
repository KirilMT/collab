from ._helpers import FakeClient, FakeResponse, load_lock_client_module

# acquire tests moved to test_lock_client_acquire.py
# force_release tests moved to test_lock_client_force_release.py
# release tests moved to test_lock_client_release.py
# This file now covers only active() and get_lock_status()


def test_active_and_get_lock_status(monkeypatch):
    mod = load_lock_client_module()
    client = mod.LockClient(local_only=True, developer_id="frank")
    client._client = FakeClient(
        FakeResponse(
            status=200,
            data=[
                {
                    "file_path": "a",
                    "developer_id": "frank",
                    "acquired_at": "t",
                    "reason": "r",
                }
            ],
        )
    )
    act = client.active()
    assert isinstance(act, list) and act
    info = client.get_lock_status("a")
    assert info.get("is_locked") is True
    assert info.get("locked_by") == "frank"


def test_force_release_all_chunking(monkeypatch):
    mod = load_lock_client_module()
    client = mod.LockClient(local_only=True, developer_id="root")
    client._is_admin = True

    # Simulate many active locks
    monkeypatch.setattr(
        client, "active", lambda: [{"file_path": f"f{i}"} for i in range(5)]
    )

    class RespNone:
        status = 200
        data = None
        error = None

    class CustomClient:
        def table(self, *a, **k):
            return self

        def delete(self, *a, **k):
            return self

        def in_(self, *a, **k):
            return self

        def execute(self):
            return RespNone()

    client._client = CustomClient()
    released = client.force_release_all()
    assert isinstance(released, int)
    assert released >= 0
