"""Unit tests for LockClient.prune_orphan_locks (#182)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

import pytest

from collab import lock_client as mod
from collab.errors import LockServiceUnavailableError


def _iso_age(seconds: float) -> str:
    return (datetime.now().astimezone() - timedelta(seconds=seconds)).isoformat()


def _lock(
    path: str,
    *,
    developer_id: str = "me",
    reason: str = "Auto-Watch Sync",
    age_s: float = 600,
    is_pr_claim: bool = False,
    agent_id: str | None = None,
) -> Dict[str, Any]:
    return {
        "file_path": path,
        "developer_id": developer_id,
        "reason": reason,
        "acquired_at": _iso_age(age_s),
        "is_pr_claim": is_pr_claim,
        "agent_id": agent_id,
    }


@pytest.fixture
def client(monkeypatch):
    c = mod.LockClient(local_only=True, developer_id="me")
    monkeypatch.setattr(c, "_normalize_file_path", lambda p: p)
    return c


def test_prune_refuses_when_git_unreliable(client, monkeypatch):
    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", lambda: ([], False))
    summary = client.prune_orphan_locks()
    assert summary["git_ok"] is False
    assert summary["count"] == 0


def test_prune_releases_own_stale_auto_watch(client, monkeypatch):
    active = [
        _lock("orphan.py", age_s=600),
        _lock("still_dirty.py", age_s=600),
    ]
    released: List[str] = []

    monkeypatch.setattr(
        client, "_get_modified_and_unpushed_files", lambda: (["still_dirty.py"], True)
    )
    monkeypatch.setattr(client, "active", lambda: active)
    monkeypatch.setattr(mod, "_min_auto_lock_hold_seconds", lambda: 300)
    monkeypatch.setattr(
        client,
        "_release_developer_scope",
        lambda fp: released.append(fp) or True,
    )

    summary = client.prune_orphan_locks()
    assert summary["git_ok"] is True
    assert summary["released"] == ["orphan.py"]
    assert "still_dirty.py" not in summary["released"]
    assert released == ["orphan.py"]


def test_prune_respects_min_hold_unless_aggressive_auto(client, monkeypatch):
    active = [_lock("young.py", age_s=60, reason="Auto-Watch Sync")]
    released: List[str] = []

    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", lambda: ([], True))
    monkeypatch.setattr(client, "active", lambda: active)
    monkeypatch.setattr(mod, "_min_auto_lock_hold_seconds", lambda: 300)
    monkeypatch.setattr(
        client,
        "_release_developer_scope",
        lambda fp: released.append(fp) or True,
    )

    summary = client.prune_orphan_locks(aggressive=False)
    assert summary["count"] == 0
    assert released == []

    summary2 = client.prune_orphan_locks(aggressive=True)
    assert summary2["released"] == ["young.py"]
    assert released == ["young.py"]


def test_prune_skips_pr_claims(client, monkeypatch):
    active = [_lock("claimed.py", age_s=9999, is_pr_claim=True)]
    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", lambda: ([], True))
    monkeypatch.setattr(client, "active", lambda: active)

    def _must_not_release(_fp: str) -> bool:
        raise AssertionError("PR claims must not be released")

    monkeypatch.setattr(client, "_release_developer_scope", _must_not_release)

    summary = client.prune_orphan_locks(aggressive=True)
    assert summary["count"] == 0
    assert any(s.get("reason") == "pr_claim" for s in summary["skipped"])


def test_prune_dry_run_does_not_release(client, monkeypatch):
    active = [_lock("x.py", age_s=900)]
    called = {"n": 0}

    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", lambda: ([], True))
    monkeypatch.setattr(client, "active", lambda: active)
    monkeypatch.setattr(mod, "_min_auto_lock_hold_seconds", lambda: 1)

    def _rel(fp):
        called["n"] += 1
        return True

    monkeypatch.setattr(client, "_release_developer_scope", _rel)
    summary = client.prune_orphan_locks(dry_run=True)
    assert summary["released"] == ["x.py"]
    assert summary["dry_run"] is True
    assert called["n"] == 0


def test_prune_foreign_auto_watch_requires_admin(client, monkeypatch):
    active = [
        _lock("foreign.py", developer_id="other", age_s=100_000),
    ]
    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", lambda: ([], True))
    monkeypatch.setattr(client, "active", lambda: active)
    client._is_admin = False

    summary = client.prune_orphan_locks(
        include_foreign_auto_watch=True, max_age_hours=1
    )
    assert summary["count"] == 0
    assert any(s.get("reason") == "foreign_needs_admin" for s in summary["skipped"])


def test_prune_foreign_auto_watch_admin_old_enough(client, monkeypatch):
    active = [
        _lock("foreign.py", developer_id="other", age_s=100_000),
        _lock("foreign_young.py", developer_id="other", age_s=60),
        _lock("foreign_manual.py", developer_id="other", age_s=100_000, reason="WIP"),
    ]
    forced: List[str] = []

    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", lambda: ([], True))
    monkeypatch.setattr(client, "active", lambda: active)
    client._is_admin = True
    monkeypatch.setattr(
        client,
        "force_release",
        lambda fp: (forced.append(fp) or True, "ok"),
    )

    summary = client.prune_orphan_locks(
        include_foreign_auto_watch=True, max_age_hours=1
    )
    assert summary["released"] == ["foreign.py"]
    assert forced == ["foreign.py"]


def test_prune_service_unavailable(client, monkeypatch):
    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", lambda: ([], True))

    def _boom():
        raise LockServiceUnavailableError("down")

    monkeypatch.setattr(client, "active", _boom)
    summary = client.prune_orphan_locks()
    assert summary["git_ok"] is False


def test_helpers_auto_watch_and_age():
    assert mod.LockClient._is_auto_watch_lock({"reason": "Auto-Watch Sync"})
    assert mod.LockClient._is_auto_watch_lock({"reason": "autowatch"})
    assert mod.LockClient._is_auto_watch_lock({"reason": "auto watch sync"})
    # Empty/NULL reason is a manual acquire, NOT auto-watch (no false positives).
    assert not mod.LockClient._is_auto_watch_lock({"reason": None})
    assert not mod.LockClient._is_auto_watch_lock({"reason": ""})
    assert not mod.LockClient._is_auto_watch_lock({})
    assert not mod.LockClient._is_auto_watch_lock({"reason": "Manual claim"})
    assert mod.LockClient._is_pr_claim_lock({"is_pr_claim": True})
    assert mod.LockClient._is_pr_claim_lock({"is_pr_claim": "true"})
    assert mod.LockClient._is_pr_claim_lock({"is_pr_claim": 1})
    assert not mod.LockClient._is_pr_claim_lock({"is_pr_claim": False})
    age = mod.LockClient._lock_age_seconds({"acquired_at": _iso_age(10)})
    assert age is not None and 5 <= age <= 30
    assert mod.LockClient._lock_age_seconds({}) is None
    assert mod.LockClient._lock_age_seconds({"acquired_at": "not-a-date"}) is None


def test_prune_invalid_env_max_age_falls_back(client, monkeypatch):
    monkeypatch.setenv("COLLAB_ORPHAN_LOCK_MAX_AGE_HOURS", "not-a-float")
    # Force the float() path to raise TypeError/ValueError via a broken getenv
    # is hard; instead monkeypatch float on the env parse by injecting via
    # max_age_hours=None and a bad env that float() rejects.
    monkeypatch.setenv("COLLAB_ORPHAN_LOCK_MAX_AGE_HOURS", "???")
    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", lambda: ([], True))
    monkeypatch.setattr(client, "active", lambda: [])
    summary = client.prune_orphan_locks()
    assert summary["git_ok"] is True
    assert summary["count"] == 0


def test_prune_git_scan_raises(client, monkeypatch):
    def _boom():
        raise RuntimeError("git dead")

    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", _boom)
    summary = client.prune_orphan_locks()
    assert summary["git_ok"] is False


def test_prune_active_generic_exception(client, monkeypatch):
    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", lambda: ([], True))

    def _boom():
        raise RuntimeError("active boom")

    monkeypatch.setattr(client, "active", _boom)
    summary = client.prune_orphan_locks()
    assert summary["git_ok"] is False


def test_prune_skips_empty_file_path(client, monkeypatch):
    active = [
        {"file_path": "", "developer_id": "me", "reason": "Auto-Watch Sync"},
        {"file_path": None, "developer_id": "me", "reason": "Auto-Watch Sync"},
        _lock("ok.py", age_s=900),
    ]
    released: List[str] = []
    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", lambda: ([], True))
    monkeypatch.setattr(client, "active", lambda: active)
    monkeypatch.setattr(mod, "_min_auto_lock_hold_seconds", lambda: 1)
    monkeypatch.setattr(
        client, "_release_developer_scope", lambda fp: released.append(fp) or True
    )
    summary = client.prune_orphan_locks()
    assert summary["released"] == ["ok.py"]


def test_prune_aggressive_skips_very_young_auto(client, monkeypatch):
    active = [_lock("brand_new.py", age_s=5, reason="Auto-Watch Sync")]
    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", lambda: ([], True))
    monkeypatch.setattr(client, "active", lambda: active)
    monkeypatch.setattr(mod, "_min_auto_lock_hold_seconds", lambda: 300)
    monkeypatch.setenv("COLLAB_ORPHAN_AUTO_WATCH_GRACE_SECONDS", "30")
    monkeypatch.setattr(
        client,
        "_release_developer_scope",
        lambda fp: (_ for _ in ()).throw(AssertionError("too young")),
    )
    summary = client.prune_orphan_locks(aggressive=True)
    assert summary["count"] == 0
    assert any(s.get("reason") == "young_auto_watch" for s in summary["skipped"])


def test_prune_release_failed_is_skipped(client, monkeypatch):
    active = [_lock("x.py", age_s=900)]
    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", lambda: ([], True))
    monkeypatch.setattr(client, "active", lambda: active)
    monkeypatch.setattr(mod, "_min_auto_lock_hold_seconds", lambda: 1)
    monkeypatch.setattr(client, "_release_developer_scope", lambda fp: False)
    summary = client.prune_orphan_locks()
    assert summary["count"] == 0
    assert any(s.get("reason") == "release_failed" for s in summary["skipped"])


def test_prune_foreign_not_requested_skipped(client, monkeypatch):
    active = [_lock("f.py", developer_id="other", age_s=100_000)]
    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", lambda: ([], True))
    monkeypatch.setattr(client, "active", lambda: active)
    summary = client.prune_orphan_locks(include_foreign_auto_watch=False)
    assert any(s.get("reason") == "foreign_not_requested" for s in summary["skipped"])


def test_prune_foreign_dry_run_and_force_fail(client, monkeypatch):
    active = [_lock("f.py", developer_id="other", age_s=100_000)]
    monkeypatch.setattr(client, "_get_modified_and_unpushed_files", lambda: ([], True))
    monkeypatch.setattr(client, "active", lambda: active)
    client._is_admin = True

    summary = client.prune_orphan_locks(
        include_foreign_auto_watch=True, max_age_hours=1, dry_run=True
    )
    assert summary["released"] == ["f.py"]

    monkeypatch.setattr(client, "force_release", lambda fp: (False, "nope"))
    summary2 = client.prune_orphan_locks(
        include_foreign_auto_watch=True, max_age_hours=1, dry_run=False
    )
    assert summary2["count"] == 0
    assert any(s.get("reason") == "force_release_failed" for s in summary2["skipped"])
