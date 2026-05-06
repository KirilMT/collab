"""Tests for _scan_remote_locks behavior in live_locks_watcher."""

from __future__ import annotations

from ._helpers import load_watcher_module


class FakeScanClient:
    """Mock Supabase client for _scan_remote_locks tests."""

    def __init__(self, data=None, raise_exc=None):
        self._data = data or []
        self._raise_exc = raise_exc

    def table(self, *args, **kwargs):
        return self

    def select(self, *args, **kwargs):
        if self._raise_exc:
            raise self._raise_exc
        return self

    def execute(self):
        if self._raise_exc:
            raise self._raise_exc

        class Resp:
            data = self._data

        return Resp()


def test_scan_remote_locks_warns_about_other_devs(monkeypatch, caplog):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    mod._warned_remote_locks.clear()
    monkeypatch.setattr(mod, "_notify", lambda *a, **k: None)

    client = FakeScanClient(
        data=[
            {"file_path": "src/app.py", "developer_id": "bob"},
        ]
    )

    import logging

    with caplog.at_level(logging.WARNING, logger="collab.pycharm_watcher"):
        mod._scan_remote_locks(client)

    assert "src/app.py" in mod._warned_remote_locks
    assert any("REMOTE LOCK" in r.message for r in caplog.records)
    mod._warned_remote_locks.clear()


def test_scan_remote_locks_skips_own_locks(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    mod._warned_remote_locks.clear()

    client = FakeScanClient(data=[{"file_path": "src/app.py", "developer_id": "alice"}])
    mod._scan_remote_locks(client)

    assert "src/app.py" not in mod._warned_remote_locks
    mod._warned_remote_locks.clear()


def test_scan_remote_locks_clears_released_warnings(monkeypatch, caplog):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    mod._warned_remote_locks.clear()
    monkeypatch.setattr(mod, "_notify", lambda *a, **k: None)

    # First scan: bob holds a lock
    client_with_lock = FakeScanClient(
        data=[{"file_path": "src/app.py", "developer_id": "bob"}]
    )
    mod._scan_remote_locks(client_with_lock)
    assert "src/app.py" in mod._warned_remote_locks

    # Second scan: lock released
    import logging

    with caplog.at_level(logging.INFO, logger="collab.pycharm_watcher"):
        client_empty = FakeScanClient(data=[])
        mod._scan_remote_locks(client_empty)

    assert "src/app.py" not in mod._warned_remote_locks
    assert any("Remote lock cleared" in r.message for r in caplog.records)
    mod._warned_remote_locks.clear()


def test_scan_remote_locks_no_duplicate_warnings(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    mod._warned_remote_locks.clear()
    notify_calls = []
    monkeypatch.setattr(mod, "_notify", lambda t, m: notify_calls.append((t, m)))

    client = FakeScanClient(data=[{"file_path": "src/app.py", "developer_id": "bob"}])

    mod._scan_remote_locks(client)
    mod._scan_remote_locks(client)  # second call — should NOT notify again

    assert len(notify_calls) == 1
    mod._warned_remote_locks.clear()


def test_scan_remote_locks_handles_exception(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    mod._warned_remote_locks.clear()

    client = FakeScanClient(raise_exc=RuntimeError("network down"))
    mod._scan_remote_locks(client)  # should not raise

    assert len(mod._warned_remote_locks) == 0


def test_scan_remote_locks_skips_empty_file_path(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    mod._warned_remote_locks.clear()
    monkeypatch.setattr(mod, "_notify", lambda *a, **k: None)

    client = FakeScanClient(data=[{"file_path": "", "developer_id": "bob"}])
    mod._scan_remote_locks(client)

    assert len(mod._warned_remote_locks) == 0


def test_scan_remote_locks_client_exception(monkeypatch):
    mod = load_watcher_module()

    class FakeResp:
        def __init__(self, data=None):
            self.data = data

    class FakeClient:
        def __init__(self, data=None, explode=False):
            self._data = data
            self._explode = explode

        def table(self, *a, **k):
            return self

        def select(self, *a, **k):
            return self

        def execute(self):
            if self._explode:
                raise RuntimeError("backend down")
            return FakeResp(self._data)

    fake = FakeClient(explode=True)
    mod._warned_remote_locks.clear()
    mod._known_remote_locks.clear()
    mod._scan_remote_locks(fake)


def test_scan_remote_locks_warns_for_other_owner(monkeypatch):
    mod = load_watcher_module()
    fake_data = [
        {
            "developer_id": "other_user",
            "file_path": "src/locked.txt",
            "branch_name": None,
            "reason": None,
        }
    ]
    fake = type(
        "C",
        (),
        {
            "_data": fake_data,
            "table": lambda self, *a, **k: self,
            "select": lambda self, *a, **k: self,
            "execute": lambda self: type("R", (), {"data": self._data})(),
        },
    )()
    mod.DEVELOPER_ID = "me"
    mod._warned_remote_locks.clear()
    mod._known_remote_locks.clear()
    mod._scan_remote_locks(fake)
    assert "src/locked.txt" in mod._warned_remote_locks


def test_scan_remote_locks_same_owner_updates_known():
    mod = load_watcher_module()
    fake_data = [
        {
            "developer_id": "me",
            "file_path": "src/mine.txt",
            "branch_name": None,
            "reason": None,
        }
    ]

    class FakeClient3:
        def __init__(self, data):
            self._data = data

        def table(self, *a, **k):
            return self

        def select(self, *a, **k):
            return self

        def execute(self):
            class R:
                data = self._data

            return R()

    fake = FakeClient3(fake_data)
    mod.DEVELOPER_ID = "me"
    mod._known_remote_locks.clear()
    mod._warned_remote_locks.clear()
    mod._scan_remote_locks(fake)
    assert "src/mine.txt" in mod._known_remote_locks


# ---- Auto-migrated from migrated_remaining ----


def test_scan_remote_locks_skips_local_owned(monkeypatch):
    mod = load_watcher_module()
    # If a remote lock entry belongs to our developer and we already have it in
    # _local_owned_locks, the scan should skip notifications for it.
    fake_data = [
        {
            "developer_id": "me",
            "file_path": "src/owned.txt",
            "branch_name": None,
            "reason": None,
        }
    ]

    class FakeClientLocal:
        def __init__(self, data=None):
            self._data = data

        def table(self, *a, **k):
            return self

        def select(self, *a, **k):
            return self

        def execute(self):
            class R:
                data = self._data

            return R()

    fake = FakeClientLocal(data=fake_data)
    mod.DEVELOPER_ID = "me"
    mod._local_owned_locks.clear()
    mod._local_owned_locks.add("src/owned.txt")
    mod._warned_remote_locks.clear()
    mod._known_remote_locks.clear()

    # Should not raise and should not add to _warned_remote_locks
    mod._scan_remote_locks(fake)
    assert "src/owned.txt" not in mod._warned_remote_locks


def test_scan_remote_locks_removed_discards_local_owned(monkeypatch):
    mod = load_watcher_module()
    # Simulate a previously-known remote lock that was released; if we had it
    # recorded locally, the code path should discard it from _local_owned_locks.
    mod._known_remote_locks.clear()
    mod._known_remote_locks.add("src/released.txt")
    mod._local_owned_locks.clear()
    mod._local_owned_locks.add("src/released.txt")

    # Fake client returns no locks (empty list)
    class EmptyClient:
        def table(self, *a, **k):
            return self

        def select(self, *a, **k):
            return self

        def execute(self):
            class R:
                data = []

            return R()

    fake = EmptyClient()

    mod._scan_remote_locks(fake)
    # After scanning, released lock should be removed from local-owned set
    assert "src/released.txt" not in mod._local_owned_locks


watcher = load_watcher_module()
