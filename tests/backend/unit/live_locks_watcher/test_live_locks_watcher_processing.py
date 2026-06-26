"""Processing helpers tests for live_locks_watcher."""

from __future__ import annotations

from ._helpers import load_watcher_module, patch_git_capture


def test_process_new_files_handles_local_add_exception(monkeypatch, caplog):
    """An error while recording a newly acquired lock is logged, not raised."""
    import logging

    mod = load_watcher_module()

    # Replace _local_owned_locks with an object whose add() raises. monkeypatch
    # restores the original module global automatically after the test.
    class BadSet:
        def add(self, *a, **k):
            raise RuntimeError("boom add")

    monkeypatch.setattr(mod, "_local_owned_locks", BadSet())
    monkeypatch.setattr(mod, "DEVELOPER_ID", "tester")
    monkeypatch.setattr(mod, "_maybe_warn_cross_branch_overlap", lambda: None)

    # Fake client returns success (no conflict)
    class RpcClient:
        def rpc(self, *a, **k):
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    with caplog.at_level(logging.ERROR, logger=mod.logger.name):
        # Should not raise even though add() raises inside
        mod._process_new_files(RpcClient(), "main", {"collab/a.py"})

    assert "Failed to acquire lock for collab/a.py" in caplog.text


def test_process_releases_handles_discard_exception(monkeypatch, caplog):
    """An error while discarding a released lock is logged, not raised."""
    import logging

    mod = load_watcher_module()

    # Replace _local_owned_locks with object whose discard raises. monkeypatch
    # restores the original module global automatically after the test.
    class BadSet:
        def discard(self, *a, **k):
            raise RuntimeError("boom discard")

    monkeypatch.setattr(mod, "_local_owned_locks", BadSet())
    monkeypatch.setattr(mod, "DEVELOPER_ID", "tester")

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

    with caplog.at_level(logging.ERROR, logger=mod.logger.name):
        # Should not raise even though discard() raises inside
        mod._process_releases(fake, {"collab/b.py"})

    assert "Failed to release lock for collab/b.py" in caplog.text


# RESTORED: test_process_new_files_and_releases_moved (from HEAD)
def test_process_new_files_and_releases_moved(monkeypatch, caplog):
    """Conflicts are tracked on acquire; ephemeral releases skip the DB delete."""
    import logging

    mod = load_watcher_module()

    delete_calls = []

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
                    delete_calls.append("delete")
                    return self

                def eq(self, *a, **k):
                    return self

                def execute(self):
                    delete_calls.append("execute")
                    return None

            return Q()

    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_active_conflicts", set())
    monkeypatch.setattr(mod, "_local_owned_locks", set())
    monkeypatch.setattr(mod, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_maybe_warn_cross_branch_overlap", lambda: None)

    client = Client()
    mod._process_new_files(client, "main", {"a.txt"})
    assert "a.txt" in mod._active_conflicts

    # Ephemeral developer release: logs EPHEMERAL-RELEASE and never deletes in DB.
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev_1")
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod._process_releases(client, {"b.txt"})

    assert "[EPHEMERAL-RELEASE]" in caplog.text
    assert delete_calls == []


def test_process_new_files_triggers_overlap_warning(monkeypatch, caplog):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_last_overlap_warn_at", 0.0)
    monkeypatch.setattr(mod, "_OVERLAP_WARN_INTERVAL_S", 0.0)

    from collab import overlap

    monkeypatch.setattr(overlap, "is_overlap_check_enabled", lambda: True)
    monkeypatch.setattr(
        overlap,
        "detect_cross_branch_overlaps",
        lambda *_a, **_k: [
            overlap.OverlapReport(branch="feat/other", files=("shared.py",))
        ],
    )

    class Client:
        def rpc(self, *_a, **_k):
            return type(
                "R", (), {"execute": lambda self: type("E", (), {"data": []})()}
            )()

    import logging

    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        mod._process_new_files(Client(), "main", {"shared.py"})

    assert "cross-branch overlap" in caplog.text.lower()


def test_maybe_warn_cross_branch_overlap_import_failure(monkeypatch):
    """If the overlap module import fails, the check is a no-op (throttle intact)."""
    import sys

    import collab

    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_last_overlap_warn_at", 0.0)

    # Force ``from collab import overlap`` to raise ImportError: drop the cached
    # attribute and poison the submodule entry in sys.modules.
    monkeypatch.delattr(collab, "overlap", raising=False)
    monkeypatch.setitem(sys.modules, "collab.overlap", None)

    mod._maybe_warn_cross_branch_overlap()


def test_process_releases_defers_young_locks(monkeypatch, caplog):
    """Locks younger than _min_auto_lock_hold_seconds are NOT released."""
    import logging

    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_min_auto_lock_hold_seconds", lambda: 60)
    monkeypatch.setattr(mod, "DEVELOPER_ID", "tester")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    mod._active_conflicts.clear()
    mod._lock_acquired_at.clear()

    from datetime import datetime

    mod._lock_acquired_at["src/app.py"] = datetime.now()

    delete_calls = []

    class FakeClient:
        def table(self, _name):
            return self

        def delete(self):
            return self

        def eq(self, *_a, **_k):
            return self

        def execute(self):
            delete_calls.append(1)
            return type("R", (), {"data": []})()

    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        mod._process_releases(FakeClient(), {"src/app.py"})

    assert delete_calls == []
    assert "⏳ [KEPT]" in caplog.text

    # Import failure returns before the throttle timestamp is advanced.
    assert mod._last_overlap_warn_at == 0.0


def test_maybe_warn_cross_branch_overlap_disabled(monkeypatch):
    mod = load_watcher_module()
    from collab import overlap

    monkeypatch.setattr(overlap, "is_overlap_check_enabled", lambda: False)
    calls = {"count": 0}
    monkeypatch.setattr(
        overlap,
        "detect_cross_branch_overlaps",
        lambda *_a, **_k: calls.__setitem__("count", calls["count"] + 1) or [],
    )
    mod._maybe_warn_cross_branch_overlap()
    assert calls["count"] == 0


def test_maybe_warn_cross_branch_overlap_fail_open_on_error(monkeypatch, caplog):
    """Errors in overlap detection are swallowed and the throttle stays unset."""
    import logging

    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_last_overlap_warn_at", 0.0)
    monkeypatch.setattr(mod, "_OVERLAP_WARN_INTERVAL_S", 0.0)
    from collab import overlap

    monkeypatch.setattr(overlap, "is_overlap_check_enabled", lambda: True)

    def boom(*_a, **_k):
        raise RuntimeError("git down")

    monkeypatch.setattr(overlap, "detect_cross_branch_overlaps", boom)

    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        mod._maybe_warn_cross_branch_overlap()

    # Throttle is only advanced on a successful detection, never on error.
    assert mod._last_overlap_warn_at == 0.0
    assert "Cross-branch overlap check failed" in caplog.text


def test_maybe_warn_cross_branch_overlap_respects_throttle(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_last_overlap_warn_at", mod.time.time())
    calls = {"count": 0}

    from collab import overlap

    monkeypatch.setattr(overlap, "is_overlap_check_enabled", lambda: True)
    monkeypatch.setattr(
        overlap,
        "detect_cross_branch_overlaps",
        lambda *_a, **_k: calls.__setitem__("count", calls["count"] + 1) or [],
    )
    mod._maybe_warn_cross_branch_overlap()
    assert calls["count"] == 0


def test_get_modified_and_unpushed_files_status_and_diff_migrated(monkeypatch):
    """Git status --porcelain output is parsed into the in-progress file set."""
    mod = load_watcher_module()
    monkeypatch.delenv("COLLAB_LOCK_BASE_REF", raising=False)

    # `git status --porcelain` reports one worktree-modified file. The porcelain
    # helper is the git I/O seam; the real parse/normalize/ignore logic still runs.
    monkeypatch.setattr(
        mod, "_git_capture_status_porcelain", lambda: " M collab/new.py"
    )
    # Neutralize the committed-but-unpushed diff path so only status contributes.
    patch_git_capture(monkeypatch, mod, lambda argv, **_k: "")

    changed = mod._get_modified_and_unpushed_files()
    assert changed == {"collab/new.py"}
