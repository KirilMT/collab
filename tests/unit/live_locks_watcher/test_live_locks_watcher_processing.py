"""Processing helpers tests for live_locks_watcher."""

from __future__ import annotations

import subprocess

from ._helpers import load_watcher_module


def test_process_new_files_handles_local_add_exception(monkeypatch):
    mod = load_watcher_module()

    # Replace _local_owned_locks with an object whose add() raises
    class BadSet:
        def add(self, *a, **k):
            raise RuntimeError("boom add")

    old = mod._local_owned_locks
    mod._local_owned_locks = BadSet()

    # Fake client returns success (no conflict)
    class RpcClient:
        def rpc(self, *a, **k):
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    client = RpcClient()
    mod.DEVELOPER_ID = "tester"

    # Should not raise even though add() raises inside
    mod._process_new_files(client, "main", {"src/a.py"})

    # restore
    mod._local_owned_locks = old


def test_process_releases_handles_discard_exception(monkeypatch):
    mod = load_watcher_module()

    # Replace _local_owned_locks with object whose discard raises
    class BadSet:
        def discard(self, *a, **k):
            raise RuntimeError("boom discard")

    old = mod._local_owned_locks
    mod._local_owned_locks = BadSet()

    # Fake client for delete.execute()
    class FakeClientLocal2:
        """Fake Supabase client with fluent CRUD interface for testing."""

        def __init__(self, data=None, explode=False):
            self._data = data if data is not None else []
            self._explode = explode
            self._rows = list(self._data)

        def table(self, *a, **k):
            return self

        def select(self, *a, **k):
            return self

        def insert(self, *a, **k):
            return self

        def update(self, *a, **k):
            return self

        def delete(self):
            return self

        def eq(self, *a, **k):
            return self

        def ilike(self, *a, **k):
            return self

        def order(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def rpc(self, *a, **k):
            return self

        def execute(self):
            if self._explode:
                raise RuntimeError("backend down")

            class R:
                data = self._data

            return R()

    fake = FakeClientLocal2(data=[])
    mod.DEVELOPER_ID = "tester"

    # Should not raise even though discard() raises inside
    mod._process_releases(fake, {"src/b.py"})

    mod._local_owned_locks = old


# RESTORED: test_process_new_files_and_releases_moved (from HEAD)
def test_process_new_files_and_releases_moved(monkeypatch):
    mod = load_watcher_module()

    # prepare a fake client that returns conflict for a specific file
    class Res:
        def __init__(self, data):
            self.data = data

        def execute(self):
            return self

    class Client:
        def rpc(self, name, params):
            return Res([{"status": "conflict", "owner": "bob"}])

        def table(self, name):
            class Q:
                def delete(self):
                    return self

                def eq(self, *a, **k):
                    return self

                def execute(self):
                    return None

            return Q()

    mod.DEVELOPER_ID = "alice"
    # ensure sets are clean
    mod._active_conflicts.clear()
    mod._local_owned_locks.clear()
    client = Client()
    mod._process_new_files(client, "main", {"a.txt"})
    assert "a.txt" in mod._active_conflicts

    # test _process_releases for ephemeral dev
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev_1")
    mod._process_releases(client, {"a.txt"})


def test_get_modified_and_unpushed_files_status_and_diff_migrated(
    monkeypatch, tmp_path
):
    mod = load_watcher_module()
    # Simulate git status output by creating a fake repo structure
    repo = tmp_path / "repo"
    repo.mkdir()
    # Create a fake file and ensure _get_modified_and_unpushed_files handles it
    f = repo / "src" / "new.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("print('hi')")

    # Monkeypatch subprocess.check_output used by watcher to get git status
    def fake_check_output(cmd, *a, **k):
        if "status" in cmd:
            return b" M src/new.py\n"
        if "rev-list" in cmd:
            return b""
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    # The watcher implementation reads from module-level _PROJECT_ROOT
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(repo))
    changed = mod._get_modified_and_unpushed_files()
    assert isinstance(changed, set)
