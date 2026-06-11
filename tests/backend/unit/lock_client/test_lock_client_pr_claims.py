"""Tests for PR-aware persistent claims: release_all_except + reconcile_pr_claims."""

from __future__ import annotations

from ._helpers import FakeResponse, load_lock_client_module, make_create_client

mod = load_lock_client_module()


def _client(monkeypatch, resp):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(resp))
    return mod.LockClient(developer_id="dev_a")


# --- release_all_except -----------------------------------------------------


def test_release_all_except_empty_keep_falls_back_to_release_all(monkeypatch):
    client = _client(monkeypatch, FakeResponse(data=0))
    monkeypatch.setattr(mod.LockClient, "release_all", lambda self, **k: 5)
    # Empty keep set -> nothing to retain -> ordinary release_all.
    assert client.release_all_except([], "feat/x") == 5


def test_release_all_except_returns_rpc_count(monkeypatch):
    client = _client(monkeypatch, FakeResponse(data=3))
    assert client.release_all_except(["a.py", "b.py"], "feat/x") == 3


def test_release_all_except_rpc_count_in_list(monkeypatch):
    # supabase may wrap a scalar return in a list.
    client = _client(monkeypatch, FakeResponse(data=[2]))
    assert client.release_all_except(["a.py"], "feat/x") == 2


def test_release_all_except_falls_back_when_rpc_errors(monkeypatch):
    # RPC missing/migration not applied -> error -> graceful fallback to release_all.
    client = _client(
        monkeypatch, FakeResponse(status=404, data=None, error={"message": "no fn"})
    )
    monkeypatch.setattr(mod.LockClient, "release_all", lambda self, **k: 9)
    assert client.release_all_except(["a.py"], "feat/x") == 9


def test_release_all_except_digit_string_count(monkeypatch):
    client = _client(monkeypatch, FakeResponse(data="2"))
    assert client.release_all_except(["a.py"], "feat/x") == 2


def test_release_all_except_unparseable_count_is_zero(monkeypatch):
    client = _client(monkeypatch, FakeResponse(data="not-a-number"))
    assert client.release_all_except(["a.py"], "feat/x") == 0


def test_release_all_except_bool_count_is_zero(monkeypatch):
    # A bool is an int subclass; guard against treating True as 1.
    client = _client(monkeypatch, FakeResponse(data=True))
    assert client.release_all_except(["a.py"], "feat/x") == 0


def test_release_all_except_ephemeral_noop(monkeypatch):
    client = _client(monkeypatch, FakeResponse(data=0))
    client._is_ephemeral = True
    assert client.release_all_except(["a.py"], "feat/x") == 0


# --- reconcile_pr_claims ----------------------------------------------------


def test_reconcile_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("COLLAB_PR_CLAIMS", "0")
    client = _client(monkeypatch, FakeResponse(data=[]))

    def _boom(self):
        raise AssertionError("active() must not be called when claims disabled")

    monkeypatch.setattr(mod.LockClient, "active", _boom)
    assert client.reconcile_pr_claims() == 0


def test_reconcile_lock_service_down_returns_zero(monkeypatch):
    monkeypatch.setenv("COLLAB_PR_CLAIMS", "1")
    client = _client(monkeypatch, FakeResponse(data=[]))

    def _down(self):
        raise mod.LockServiceUnavailableError("down")

    monkeypatch.setattr(mod.LockClient, "active", _down)
    assert client.reconcile_pr_claims() == 0


def test_reconcile_no_claims_returns_zero(monkeypatch):
    monkeypatch.setenv("COLLAB_PR_CLAIMS", "1")
    client = _client(monkeypatch, FakeResponse(data=[]))
    monkeypatch.setattr(
        mod.LockClient,
        "active",
        lambda self: [
            {"file_path": "a.py", "developer_id": "dev_a", "is_pr_claim": False}
        ],
    )
    assert client.reconcile_pr_claims() == 0


def test_reconcile_releases_stale_claims(monkeypatch):
    monkeypatch.setenv("COLLAB_PR_CLAIMS", "1")
    client = _client(monkeypatch, FakeResponse(data=[]))
    monkeypatch.setattr(
        mod.LockClient,
        "active",
        lambda self: [
            {
                "file_path": "a.py",
                "developer_id": "dev_a",
                "is_pr_claim": True,
                "claim_branch": "feat/done",
            },
            {
                "file_path": "b.py",
                "developer_id": "dev_a",
                "is_pr_claim": True,
                "claim_branch": "feat/open",
            },
            {
                "file_path": "c.py",
                "developer_id": "dev_other",
                "is_pr_claim": True,
                "claim_branch": "feat/done",
            },
        ],
    )
    # Only feat/done is stale (merged/gone); feat/open is still active.
    monkeypatch.setattr(
        mod.overlap, "stale_claim_branches", lambda *_a, **_k: frozenset({"feat/done"})
    )
    released = []
    monkeypatch.setattr(
        mod.LockClient,
        "_release_developer_scope",
        lambda self, fp: released.append(fp) or True,
    )
    # Releases only my own claim on the stale branch (a.py); never b.py (still
    # open) nor c.py (another developer).
    assert client.reconcile_pr_claims() == 1
    assert released == ["a.py"]
