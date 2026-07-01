"""Daemon PR-claim awareness tests for live_locks_watcher (#181).

Covers the watcher's claim-aware release path: never deleting an existing
``is_pr_claim`` row, promoting pushed-branch files to claims instead of releasing them,
retaining claims across a startup reconcile, and releasing claims once their branch is
merged/deleted.
"""

from __future__ import annotations

import logging

from ._helpers import load_watcher_module


class FakeQuery:
    """Fluent Supabase query stub that records the terminal operation."""

    def __init__(self, client):
        self.client = client
        self.op = None
        self.payload = None

    def select(self, *a, **k):
        self.op = "select"
        return self

    def update(self, payload, *a, **k):
        self.op = "update"
        self.payload = payload
        return self

    def delete(self, *a, **k):
        self.op = "delete"
        return self

    def insert(self, *a, **k):
        self.op = "insert"
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        self.client.calls.append((self.op, self.payload))
        if self.op in self.client.explode_on:
            raise RuntimeError("backend down")
        if self.op == "select":
            return type("R", (), {"data": list(self.client.select_data)})()
        return type("R", (), {"data": []})()


class FakeClient:
    """Fake Supabase client returning seeded rows and recording write ops."""

    def __init__(self, select_data=None, explode_on=()):
        self.select_data = list(select_data or [])
        self.explode_on = set(explode_on)
        self.calls = []

    def table(self, *a, **k):
        return FakeQuery(self)

    def ops(self):
        return [op for op, _payload in self.calls]


def _enable_claims(monkeypatch, *, changed=None, branch="feat/x", stale=None):
    """Patch the overlap seam used by the claim-aware paths."""
    from collab import overlap

    monkeypatch.setattr(overlap, "is_pr_claims_enabled", lambda: True)
    monkeypatch.setattr(
        overlap, "head_changed_files", lambda *_a, **_k: (branch, list(changed or []))
    )
    monkeypatch.setattr(
        overlap, "stale_claim_branches", lambda *_a, **_k: frozenset(stale or ())
    )


# --------------------------------------------------------------------------- #
# _fetch_developer_claim_paths
# --------------------------------------------------------------------------- #
def test_fetch_claims_returns_paths(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    client = FakeClient(select_data=[{"file_path": "a.py"}, {"file_path": "b.py"}])
    assert mod._fetch_developer_claim_paths(client) == {"a.py", "b.py"}


def test_fetch_claims_no_developer_is_empty(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", None)
    client = FakeClient(select_data=[{"file_path": "a.py"}])
    assert mod._fetch_developer_claim_paths(client) == set()
    assert client.calls == []  # short-circuits before any query


def test_fetch_claims_swallows_errors(monkeypatch, caplog):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    client = FakeClient(explode_on={"select"})
    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        assert mod._fetch_developer_claim_paths(client) == set()
    assert "Failed to fetch PR claims" in caplog.text


# --------------------------------------------------------------------------- #
# _promote_lock_to_claim
# --------------------------------------------------------------------------- #
def test_promote_updates_row(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    client = FakeClient()
    assert mod._promote_lock_to_claim(client, "a.py", "feat/x") is True
    assert client.ops() == ["update"]
    payload = client.calls[0][1]
    assert payload["is_pr_claim"] is True
    assert payload["claim_branch"] == "feat/x"
    assert "claimed_at" in payload


def test_promote_skips_ephemeral(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev_1")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: True)
    client = FakeClient()
    assert mod._promote_lock_to_claim(client, "a.py", "feat/x") is False
    assert client.calls == []


def test_promote_swallows_errors(monkeypatch, caplog):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    client = FakeClient(explode_on={"update"})
    with caplog.at_level(logging.ERROR, logger=mod.logger.name):
        assert mod._promote_lock_to_claim(client, "a.py", "feat/x") is False
    assert "Failed to promote a.py" in caplog.text


# --------------------------------------------------------------------------- #
# _process_releases claim-awareness
# --------------------------------------------------------------------------- #
def test_release_promotes_pushed_branch_file(monkeypatch, caplog):
    """A released file that is part of the PR diff is claimed, not deleted."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    mod._active_conflicts.clear()
    _enable_claims(monkeypatch, changed=["src/app.py"], branch="feat/x")

    client = FakeClient(select_data=[])  # no existing claims yet
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod._process_releases(client, {"src/app.py"}, "feat/x")

    ops = client.ops()
    assert "update" in ops and "delete" not in ops
    assert "[CLAIMED] src/app.py" in caplog.text


def test_release_keeps_existing_claim(monkeypatch, caplog):
    """An existing claim is retained (never deleted) by the watcher."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    mod._active_conflicts.clear()
    _enable_claims(monkeypatch, changed=[], branch="feat/x")

    client = FakeClient(select_data=[{"file_path": "src/app.py"}])
    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        mod._process_releases(client, {"src/app.py"}, "feat/x")

    # Only the claim-fetch select ran; no delete, no update.
    assert client.ops() == ["select"]
    assert "[CLAIM-KEEP]" in caplog.text


def test_release_non_pr_file_is_deleted(monkeypatch):
    """A released file not part of the PR diff is released normally."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    mod._active_conflicts.clear()
    _enable_claims(monkeypatch, changed=["other.py"], branch="feat/x")

    client = FakeClient(select_data=[])
    mod._process_releases(client, {"scratch.py"}, "feat/x")

    assert "delete" in client.ops()
    assert "update" not in client.ops()


def test_release_claims_disabled_deletes(monkeypatch):
    """With claims disabled the watcher deletes as before (no select/update)."""
    mod = load_watcher_module()
    from collab import overlap

    monkeypatch.setattr(overlap, "is_pr_claims_enabled", lambda: False)
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    mod._active_conflicts.clear()

    client = FakeClient()
    mod._process_releases(client, {"a.py"}, "feat/x")
    assert client.ops() == ["delete"]


def test_release_branch_none_resolves_current(monkeypatch):
    """When branch is omitted the watcher resolves the current branch."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    monkeypatch.setattr(mod, "_get_current_branch", lambda: "feat/resolved")
    mod._active_conflicts.clear()
    _enable_claims(monkeypatch, changed=["src/app.py"], branch="feat/resolved")

    client = FakeClient(select_data=[])
    mod._process_releases(client, {"src/app.py"})  # no branch arg
    payload = next(p for op, p in client.calls if op == "update")
    assert payload["claim_branch"] == "feat/resolved"


def test_release_diff_error_falls_back_to_release(monkeypatch, caplog):
    """If PR-diff resolution raises, files are released normally (fail-safe)."""
    mod = load_watcher_module()
    from collab import overlap

    monkeypatch.setattr(overlap, "is_pr_claims_enabled", lambda: True)
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    mod._active_conflicts.clear()

    def boom(*_a, **_k):
        raise RuntimeError("git down")

    monkeypatch.setattr(overlap, "head_changed_files", boom)
    client = FakeClient(select_data=[])
    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        mod._process_releases(client, {"a.py"}, "feat/x")
    assert "delete" in client.ops()
    assert "PR-claim diff resolution failed" in caplog.text


def test_release_empty_set_is_noop(monkeypatch):
    mod = load_watcher_module()
    client = FakeClient()
    mod._process_releases(client, set(), "feat/x")
    assert client.calls == []


def test_release_conflict_cleared_before_claim(monkeypatch, caplog):
    """A conflicted file is cleared and skipped (no delete/claim)."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    _enable_claims(monkeypatch, changed=["c.py"], branch="feat/x")
    mod._active_conflicts.clear()
    mod._active_conflicts.add("c.py")

    client = FakeClient(select_data=[])
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod._process_releases(client, {"c.py"}, "feat/x")
    assert "Conflict cleared: c.py" in caplog.text
    assert "c.py" not in mod._active_conflicts
    assert "delete" not in client.ops() and "update" not in client.ops()


# --------------------------------------------------------------------------- #
# _reconcile_pr_claims (daemon-side)
# --------------------------------------------------------------------------- #
def test_reconcile_disabled_is_noop(monkeypatch):
    mod = load_watcher_module()
    from collab import overlap

    monkeypatch.setattr(overlap, "is_pr_claims_enabled", lambda: False)
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    client = FakeClient()
    assert mod._reconcile_pr_claims(client) == 0
    assert client.calls == []


def test_reconcile_no_developer_is_noop(monkeypatch):
    mod = load_watcher_module()
    from collab import overlap

    monkeypatch.setattr(overlap, "is_pr_claims_enabled", lambda: True)
    monkeypatch.setattr(mod, "DEVELOPER_ID", None)
    client = FakeClient()
    assert mod._reconcile_pr_claims(client) == 0


def test_reconcile_ephemeral_is_noop(monkeypatch):
    mod = load_watcher_module()
    from collab import overlap

    monkeypatch.setattr(overlap, "is_pr_claims_enabled", lambda: True)
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev_1")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: True)
    client = FakeClient()
    assert mod._reconcile_pr_claims(client) == 0


def test_reconcile_fetch_error_is_noop(monkeypatch):
    mod = load_watcher_module()
    from collab import overlap

    monkeypatch.setattr(overlap, "is_pr_claims_enabled", lambda: True)
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    client = FakeClient(explode_on={"select"})
    assert mod._reconcile_pr_claims(client) == 0


def test_reconcile_no_claim_branches_is_noop(monkeypatch):
    mod = load_watcher_module()
    from collab import overlap

    monkeypatch.setattr(overlap, "is_pr_claims_enabled", lambda: True)
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    client = FakeClient(select_data=[{"file_path": "a.py", "claim_branch": None}])
    assert mod._reconcile_pr_claims(client) == 0


def test_reconcile_nothing_stale_is_noop(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    _enable_claims(monkeypatch, stale=[])
    client = FakeClient(select_data=[{"file_path": "a.py", "claim_branch": "feat/x"}])
    assert mod._reconcile_pr_claims(client) == 0
    assert "delete" not in client.ops()


def test_reconcile_stale_branches_raises_is_noop(monkeypatch):
    mod = load_watcher_module()
    from collab import overlap

    monkeypatch.setattr(overlap, "is_pr_claims_enabled", lambda: True)
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)

    def boom(*_a, **_k):
        raise RuntimeError("fetch failed")

    monkeypatch.setattr(overlap, "head_changed_files", lambda *_a, **_k: (None, []))
    monkeypatch.setattr(overlap, "stale_claim_branches", boom)
    client = FakeClient(select_data=[{"file_path": "a.py", "claim_branch": "feat/x"}])
    assert mod._reconcile_pr_claims(client) == 0


def test_reconcile_releases_stale_claims(monkeypatch, caplog):
    """Claims whose branch is stale (merged/deleted) are deleted."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    _enable_claims(monkeypatch, stale=["feat/x"])
    client = FakeClient(
        select_data=[
            {"file_path": "a.py", "claim_branch": "feat/x"},
            {"file_path": "b.py", "claim_branch": "feat/y"},  # not stale
        ]
    )
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        assert mod._reconcile_pr_claims(client) == 1
    assert client.ops().count("delete") == 1
    assert "[CLAIM-RELEASED] a.py" in caplog.text


def test_reconcile_skips_stale_claim_with_empty_path(monkeypatch):
    """A stale claim row with no file_path is skipped, not deleted (#181)."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    _enable_claims(monkeypatch, stale=["feat/x"])
    client = FakeClient(
        select_data=[{"file_path": "", "claim_branch": "feat/x"}],
    )
    assert mod._reconcile_pr_claims(client) == 0
    assert client.ops().count("delete") == 0


def test_reconcile_delete_error_counts_zero(monkeypatch, caplog):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    _enable_claims(monkeypatch, stale=["feat/x"])
    client = FakeClient(
        select_data=[{"file_path": "a.py", "claim_branch": "feat/x"}],
        explode_on={"delete"},
    )
    with caplog.at_level(logging.ERROR, logger=mod.logger.name):
        assert mod._reconcile_pr_claims(client) == 0
    assert "Failed to release stale claim for a.py" in caplog.text


# --------------------------------------------------------------------------- #
# startup reconcile retains claims
# --------------------------------------------------------------------------- #
def test_startup_reconcile_keeps_claim_for_clean_file(monkeypatch, caplog):
    """A clean file held as a PR claim is retained (not stale-released) at startup."""
    mod = load_watcher_module()
    from collab import overlap

    monkeypatch.setattr(overlap, "is_pr_claims_enabled", lambda: True)
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "_is_ephemeral_dev", lambda _: False)
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())
    monkeypatch.setattr(mod, "_get_current_branch", lambda: "feat/x")
    monkeypatch.setattr(mod, "_fetch_dev_other_identity_locks", lambda _c: {})
    mod._local_owned_locks.clear()

    client = FakeClient(
        select_data=[
            {
                "file_path": "src/app.py",
                "lock_token": "tok",
                "is_pr_claim": True,
                "acquired_at": None,
            }
        ]
    )
    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        mod._reconcile_on_startup(client)

    assert "delete" not in client.ops()
    assert "src/app.py" in mod._local_owned_locks
    assert "[CLAIM-KEEP]" in caplog.text


# --------------------------------------------------------------------------- #
# _warn_if_claims_migration_missing
# --------------------------------------------------------------------------- #
def test_migration_warning_disabled_is_noop(monkeypatch):
    mod = load_watcher_module()
    from collab import overlap

    monkeypatch.setattr(overlap, "is_pr_claims_enabled", lambda: False)
    assert mod._warn_if_claims_migration_missing(FakeClient()) is False


def test_migration_warning_present_is_noop(monkeypatch):
    mod = load_watcher_module()
    from collab import lock_client, overlap

    monkeypatch.setattr(overlap, "is_pr_claims_enabled", lambda: True)
    monkeypatch.setattr(lock_client, "probe_claim_columns", lambda _c: True)
    assert mod._warn_if_claims_migration_missing(FakeClient()) is False


def test_migration_warning_missing_warns(monkeypatch, caplog):
    mod = load_watcher_module()
    from collab import lock_client, overlap

    monkeypatch.setattr(overlap, "is_pr_claims_enabled", lambda: True)
    monkeypatch.setattr(lock_client, "probe_claim_columns", lambda _c: False)
    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        assert mod._warn_if_claims_migration_missing(FakeClient()) is True
    assert "claim migration is not applied" in caplog.text
