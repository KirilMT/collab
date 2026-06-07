"""CLI-focused tests for LockClient moved from the canonical file.

These tests use the shared helpers in `_helpers.py` to load the module and re-use the
FakeResponse/FakeClient factories.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

from ._helpers import (
    FakeResponse,
    load_lock_client_module,
    make_create_client,
    patch_subprocess,
)

mod = load_lock_client_module()


def test_cli_history_partial_match_hint(monkeypatch, capsys):
    """Cover history fallback hint when first row path differs from query path."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    rows = [
        {
            "file_path": "collab/other/app.py",
            "acquired_at": "2026-01-01T10:00:00+00:00",
            "released_at": "2026-01-01T11:00:00+00:00",
            "developer_id": "alice",
            "branch_name": "feat/x",
            "outcome": "released",
        }
    ]

    monkeypatch.setattr(mod.LockClient, "history", lambda self, fp, limit=20: rows)
    monkeypatch.setattr(
        sys,
        "argv",
        ["lock_client.py", "history", "collab/requested.py"],
    )

    mod._run_cli()
    out = capsys.readouterr().out
    assert "no exact match" in out.lower()
    assert "partial matches" in out.lower()


def test_main_unhandled_exception_exits_with_fatal(monkeypatch, capsys):
    """Cover main() unhandled-exception logging and fatal stderr message."""
    import collab.main as main_module

    monkeypatch.setattr(
        main_module, "_run_cli", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(SystemExit):
        mod.main()

    err = capsys.readouterr().err
    assert "fatal: lock_client crashed" in err.lower()


def test_cli_active_lock_service_unavailable(monkeypatch, capsys):
    """``collab active`` prints a clear message and exits when Supabase is down."""
    from collab.errors import LockServiceUnavailableError

    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    class _UnavailableClient:
        local_only = False

        def daemon_status(self) -> bool:
            return True

        def daemon_start(self) -> None:
            return None

        def _reconcile(self) -> set:
            return set()

        def active(self):
            raise LockServiceUnavailableError(
                "Lock service query failed",
                detail="getaddrinfo failed",
            )

    monkeypatch.setattr(mod, "LockClient", lambda **_kw: _UnavailableClient())
    monkeypatch.setattr(sys, "argv", ["collab", "active"])

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Lock service unavailable" in out
    assert "getaddrinfo failed" in out


def test_cli_history_prune_success(monkeypatch, capsys):
    """History-prune should print success message when prune succeeds."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(
        mod.LockClient,
        "prune_history",
        lambda self, retention_days=30: (True, 5, "history-pruned"),
    )
    monkeypatch.setattr(
        sys, "argv", ["lock_client.py", "history-prune", "--days", "30"]
    )

    mod._run_cli()
    out = capsys.readouterr().out
    assert "pruned 5 lock history row(s)" in out.lower()


def test_cli_history_prune_failure(monkeypatch, capsys):
    """History-prune should exit non-zero and print failure details on error."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(
        mod.LockClient,
        "prune_history",
        lambda self, retention_days=30: (False, 0, "bad-request"),
    )
    monkeypatch.setattr(
        sys, "argv", ["lock_client.py", "history-prune", "--days", "30"]
    )

    with pytest.raises(SystemExit):
        mod._run_cli()

    out = capsys.readouterr().out
    assert "failed to prune lock history" in out.lower()


def test_cli_acquire(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    test_file = tmp_path / "collab" / "app.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("# code")

    response = FakeResponse(status=200, data=[{"status": "ok"}])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "acquire", str(test_file)])

    try:
        mod._run_cli()
    except SystemExit:
        pass
    captured = capsys.readouterr()
    assert "locked" in captured.out.lower() or "✓" in captured.out


def test_cli_release(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_DEVELOPER_ID", "testdev")

    # release() now does a pre-check SELECT before DELETE.  The response must
    # include "developer_id" matching the client so the ownership guard passes.
    response = FakeResponse(
        status=200, data=[{"file_path": "collab/app.py", "developer_id": "testdev"}]
    )
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "release", "collab/app.py"])

    mod._run_cli()
    captured = capsys.readouterr()
    assert "released" in captured.out.lower() or "✓" in captured.out


def test_cli_active_no_locks(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(status=200, data=[])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "active"])

    mod._run_cli()
    captured = capsys.readouterr()
    assert "no active" in captured.out.lower()


def test_cli_whoami_human(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.delenv("COLLAB_AGENT_ID", raising=False)
    monkeypatch.delenv("COLLAB_AGENT_MODE", raising=False)
    for env_name, _ in mod.agent_identity._AGENT_RUNTIME_MARKERS:
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path / "state"))

    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(
        mod.LockClient, "_get_git_username", staticmethod(lambda: "alice")
    )
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "whoami"])

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Developer: alice" in out
    assert "Mode:      human" in out


def test_cli_whoami_with_agent(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(
        mod.LockClient, "_get_git_username", staticmethod(lambda: "alice")
    )
    monkeypatch.setattr(
        sys, "argv", ["lock_client.py", "--agent-id", "agent-1", "whoami"]
    )

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Agent ID:    agent-1" in out


def test_cli_active_mine_filter(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(
        status=200,
        data=[
            {
                "file_path": "mine.py",
                "developer_id": "alice",
                "agent_id": "agent-a",
                "branch_name": "main",
                "reason": "x",
            },
            {
                "file_path": "other.py",
                "developer_id": "alice",
                "agent_id": "agent-b",
                "branch_name": "main",
                "reason": "y",
            },
        ],
    )
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(
        mod.LockClient, "_get_git_username", staticmethod(lambda: "alice")
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["lock_client.py", "--agent-id", "agent-a", "active", "--mine"],
    )

    mod._run_cli()
    out = capsys.readouterr().out
    assert "mine.py" in out
    assert "other.py" not in out


def test_cli_claim_runs_as_agent(monkeypatch, capsys):
    """`collab claim` attributes to an AI agent even without explicit env."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.delenv("COLLAB_AGENT_ID", raising=False)
    monkeypatch.delenv("COLLAB_AGENT_MODE", raising=False)
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(
        mod.LockClient, "_get_git_username", staticmethod(lambda: "alice")
    )

    captured: dict = {}

    def fake_acquire_multiple(self, paths, **kwargs):
        captured["origin"] = self.origin
        captured["agent_id"] = self.agent_id
        captured["agent_kind"] = self.agent_kind
        captured["agent_label"] = self.agent_label
        captured["paths"] = list(paths)
        return True, [], "ok"

    monkeypatch.setattr(mod.LockClient, "acquire_multiple", fake_acquire_multiple)
    monkeypatch.setattr(
        sys,
        "argv",
        ["lock_client.py", "claim", "collab/app.py", "--label", "fix-ci"],
    )

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 0
    assert captured["origin"] == "agent"
    assert captured["agent_id"]  # auto-generated, non-empty
    assert captured["agent_label"] == "fix-ci"
    assert captured["paths"] == ["collab/app.py"]


def test_cli_claim_reports_failures(monkeypatch, capsys):
    """The claim command surfaces files it could not claim and exits non-zero."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(
        mod.LockClient, "_get_git_username", staticmethod(lambda: "alice")
    )
    monkeypatch.setattr(
        mod.LockClient,
        "acquire_multiple",
        lambda self, paths, **k: (False, ["b.py"], "conflict"),
    )
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "claim", "a.py", "b.py"])

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Could not claim" in out
    assert "b.py" in out


def test_cli_watch_forces_human_identity(monkeypatch):
    """The watcher must run as the human even when COLLAB_AGENT_ID is set."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_AGENT_ID", "agent-explicit")
    monkeypatch.delenv("COLLAB_WATCHER_AGENT_ID", raising=False)
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(
        mod.LockClient, "_get_git_username", staticmethod(lambda: "alice")
    )

    captured: dict = {}

    def fake_watch(self, **kwargs):
        captured["agent_id"] = self.agent_id
        captured["origin"] = self.origin
        captured["agent_kind"] = self.agent_kind

    monkeypatch.setattr(mod.LockClient, "watch", fake_watch)
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "watch"])

    mod._run_cli()

    assert captured["agent_id"] is None
    assert captured["origin"] == "human"
    assert captured["agent_kind"] is None


def test_cli_watch_respects_dedicated_agent_watcher(monkeypatch):
    """COLLAB_WATCHER_AGENT_ID opts into a dedicated agent watcher."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_AGENT_ID", "agent-explicit")
    monkeypatch.setenv("COLLAB_WATCHER_AGENT_ID", "agent-explicit")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(
        mod.LockClient, "_get_git_username", staticmethod(lambda: "alice")
    )

    captured: dict = {}

    def fake_watch(self, **kwargs):
        captured["agent_id"] = self.agent_id
        captured["origin"] = self.origin

    monkeypatch.setattr(mod.LockClient, "watch", fake_watch)
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "watch"])

    mod._run_cli()

    assert captured["agent_id"] == "agent-explicit"
    assert captured["origin"] == "agent"


def test_cli_active_with_locks(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(
        status=200,
        data=[
            {
                "file_path": "collab/app.py",
                "developer_id": "user1",
                "branch_name": "main",
                "reason": "testing",
            }
        ],
    )
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "active"])

    mod._run_cli()
    captured = capsys.readouterr()
    assert "collab/app.py" in captured.out


def test_cli_status_locked(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
    response = FakeResponse(
        status=200,
        data=[
            {
                "file_path": "collab/app.py",
                "developer_id": "user1",
                "acquired_at": "2025-01-01T10:00:00+00:00",
                "expires_at": future,
            }
        ],
    )
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "status", "collab/app.py"])

    mod._run_cli()
    captured = capsys.readouterr()
    assert "locked" in captured.out.lower() or "🔒" in captured.out


def test_cli_status_unlocked(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(status=200, data=[])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "status", "collab/app.py"])

    mod._run_cli()
    captured = capsys.readouterr()
    assert "unlocked" in captured.out.lower() or "🔓" in captured.out


def test_cli_release_all(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(status=200, data=[])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "release-all"])

    mod._run_cli()
    captured = capsys.readouterr()
    assert "released" in captured.out.lower()


def test_cli_release_all_developer_scope_default(monkeypatch, capsys):
    """`collab release-all` defaults to developer scope (include_agent=True)."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    captured_kwargs: dict = {}

    def _release_all(self, include_agent: bool = True) -> int:
        captured_kwargs["include_agent"] = include_agent
        return 0

    monkeypatch.setattr(mod.LockClient, "release_all", _release_all)
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "release-all"])

    mod._run_cli()
    assert captured_kwargs["include_agent"] is True


def test_cli_release_all_identity_only(monkeypatch, capsys):
    """`--identity-only` narrows release-all to the current identity."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    captured_kwargs: dict = {}

    def _release_all(self, include_agent: bool = True) -> int:
        captured_kwargs["include_agent"] = include_agent
        return 0

    monkeypatch.setattr(mod.LockClient, "release_all", _release_all)
    monkeypatch.setattr(
        sys, "argv", ["lock_client.py", "release-all", "--identity-only"]
    )

    mod._run_cli()
    assert captured_kwargs["include_agent"] is False


def test_cli_force_release(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(status=200, data=[{"file_path": "collab/app.py"}])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(
        sys, "argv", ["lock_client.py", "force-release", "collab/app.py"]
    )

    mod._run_cli()
    captured = capsys.readouterr()
    assert "✓" in captured.out or "✗" in captured.out


def test_cli_force_release_all_requires_admin(monkeypatch, capsys):
    """Force-release-all exits with permission message for non-admin client."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(status=200, data=[])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setattr(
        mod.LockClient, "is_admin", property(lambda self: False), raising=False
    )
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "force-release-all"])

    with pytest.raises(SystemExit):
        mod._run_cli()
    captured = capsys.readouterr()
    assert "permission denied" in captured.out.lower()


def test_cli_acquire_batch(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    file1 = tmp_path / "collab" / "a.py"
    file2 = tmp_path / "collab" / "b.py"
    file1.parent.mkdir(parents=True)
    file1.write_text("# a")
    file2.write_text("# b")

    response = FakeResponse(status=200, data=[{"status": "ok"}])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        sys, "argv", ["lock_client.py", "acquire-batch", str(file1), str(file2)]
    )

    try:
        mod._run_cli()
    except SystemExit:
        pass
    captured = capsys.readouterr()
    assert "locked" in captured.out.lower() or "✓" in captured.out


def test_cli_acquire_batch_conflict(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    file1 = tmp_path / "collab" / "a.py"
    file1.parent.mkdir(parents=True)
    file1.write_text("# a")

    response = FakeResponse(status=200, data=[{"status": "conflict", "owner": "other"}])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "acquire-batch", str(file1)])

    with pytest.raises(SystemExit):
        mod._run_cli()


def test_cli_release_batch(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(status=200, data=[{"file_path": "collab/app.py"}])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(
        sys, "argv", ["lock_client.py", "release-batch", "collab/a.py", "collab/b.py"]
    )

    mod._run_cli()
    captured = capsys.readouterr()
    assert "released" in captured.out.lower()


def test_cli_daemon_start(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    class FakeProc:
        pid = 12345

    called_popen = []
    read_pid_calls = [0]

    def mock_read_pid():
        read_pid_calls[0] += 1
        if read_pid_calls[0] <= 1:
            return None
        return 67891

    def mock_popen_wrap(*a, **k):
        called_popen.append(True)
        return FakeProc()

    class LocalLockClient(mod.LockClient):
        @staticmethod
        def _read_pid():
            return mock_read_pid()

    monkeypatch.setattr(mod, "LockClient", LocalLockClient)
    # Ensure we don't rely on a real process check in tests
    is_alive = staticmethod(lambda pid: True)
    monkeypatch.setattr(mod.LockClient, "_is_process_alive", is_alive)
    patch_subprocess(monkeypatch, popen=mock_popen_wrap)
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "daemon-start"])

    try:
        mod._run_cli()
    except SystemExit:
        pass
    captured = capsys.readouterr()
    assert "started" in captured.out.lower()


def test_cli_daemon_stop(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "daemon-stop"])

    mod._run_cli()
    captured = capsys.readouterr()
    assert "no running" in captured.out.lower() or "stop" in captured.out.lower()


def test_cli_daemon_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "daemon-status"])

    try:
        mod._run_cli()
    except SystemExit:
        pass
    captured = capsys.readouterr()
    assert "not running" in captured.out.lower()


def test_cli_reconcile(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(
        mod.LockClient, "_run_git_status", staticmethod(lambda: ("", True))
    )
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "reconcile"])

    mod._run_cli()


def test_cli_history(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(status=200, data=[])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "history"])

    mod._run_cli()
    captured = capsys.readouterr()
    assert "no lock history" in captured.out.lower()


def test_cli_history_json_flag(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    records = [{"id": 1, "file_path": "collab/app.py", "developer_id": "alice"}]
    response = FakeResponse(status=200, data=records)
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "history", "--json"])

    mod._run_cli()
    captured = capsys.readouterr()
    assert '"file_path"' in captured.out
    assert '"collab/app.py"' in captured.out


def test_cli_history_no_match_with_file(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(status=200, data=[])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "history", "nonexistent.py"])

    mod._run_cli()
    captured = capsys.readouterr()
    assert "no history found" in captured.out.lower()
    assert "tip" in captured.out.lower()


def test_cli_history_formatted_output(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    records = [
        {
            "id": 1,
            "file_path": "collab/app.py",
            "developer_id": "alice",
            "acquired_at": "2026-04-03T22:00:00+00:00",
            "released_at": "2026-04-03T22:30:00+00:00",
            "branch_name": "main",
            "outcome": "released",
        }
    ]
    response = FakeResponse(status=200, data=records)
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "history"])

    mod._run_cli()
    captured = capsys.readouterr()
    assert "collab/app.py" in captured.out
    assert "@alice" in captured.out


# ── restart / ping / info / logs CLI command tests ──


def test_cli_restart(monkeypatch, tmp_path, capsys):
    """``collab restart`` calls daemon_stop then daemon_start."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    calls = []

    class FakeProc:
        pid = 12345

    class LocalLockClient(mod.LockClient):
        def daemon_stop(self) -> None:
            calls.append("stop")

        def daemon_start(self, interval=5, timeout_mins=0, open_dashboard=False):
            calls.append("start")

        _is_process_alive = staticmethod(lambda pid: True)

    monkeypatch.setattr(mod, "LockClient", LocalLockClient)
    patch_subprocess(monkeypatch, popen=lambda *a, **k: FakeProc())
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "restart"])

    mod._run_cli()
    assert calls == ["stop", "start"]


def test_cli_ping_no_url(monkeypatch, capsys):
    """``collab ping`` exits 1 when SUPABASE_URL is missing."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "ping"])

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "SUPABASE_URL is not configured" in out


def test_cli_ping_invalid_url(monkeypatch, capsys):
    """``collab ping`` exits 1 when SUPABASE_URL cannot be parsed."""
    # urlparse is lenient; a URL without a scheme yields empty hostname
    # which hits the "Could not parse hostname" branch
    monkeypatch.setenv("SUPABASE_URL", "not-a-valid-url-!!!")
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "ping"])

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Could not parse hostname" in out


def test_cli_ping_success(monkeypatch, capsys):
    """``collab ping`` prints reachable message on success."""
    import socket as _socket

    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "ping"])

    # Mock socket.create_connection to succeed
    class _FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def _fake_connect(addr, timeout=5.0):
        return _FakeSock()

    monkeypatch.setattr(_socket, "create_connection", _fake_connect)

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "reachable" in out


def test_cli_ping_connection_failure(monkeypatch, capsys):
    """``collab ping`` prints unreachable message on connection failure."""
    import socket as _socket

    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "ping"])

    def _fake_connect(addr, timeout=5.0):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(_socket, "create_connection", _fake_connect)

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Cannot reach" in out


def test_cli_restart_with_auto_open(monkeypatch, tmp_path, capsys):
    """``collab restart`` passes open_dashboard when AUTO_OPEN_DASHBOARD=1."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("AUTO_OPEN_DASHBOARD", "1")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    class FakeProc:
        pid = 12345

    start_kwargs = {}

    class LocalLockClient(mod.LockClient):
        def daemon_stop(self) -> None:
            pass

        def daemon_start(self, **kwargs):
            start_kwargs.update(kwargs)

        _is_process_alive = staticmethod(lambda pid: True)

    monkeypatch.setattr(mod, "LockClient", LocalLockClient)
    patch_subprocess(monkeypatch, popen=lambda *a, **k: FakeProc())
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "restart"])

    mod._run_cli()
    assert start_kwargs.get("open_dashboard") is True


def test_cli_ping_no_hostname(monkeypatch, capsys):
    """``collab ping`` exits 1 when SUPABASE_URL has no parseable hostname."""
    monkeypatch.setenv("SUPABASE_URL", "://")
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "ping"])

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Could not parse hostname" in out


def test_cli_info(monkeypatch, capsys):
    """``collab info`` prints comprehensive status overview."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    class InfoClient(mod.LockClient):
        is_admin = True

        def daemon_status(self) -> bool:
            return True

        def active(self):
            return [
                {
                    "file_path": "a.py",
                    "developer_id": "testdev",
                    "agent_id": None,
                    "agent_label": None,
                    "agent_kind": None,
                },
                {
                    "file_path": "b.py",
                    "developer_id": "other",
                    "agent_id": None,
                    "agent_label": None,
                    "agent_kind": None,
                },
            ]

        def _lock_owned_by_me(self, lock):
            return lock.get("developer_id") == "testdev"

    monkeypatch.setattr(mod, "LockClient", InfoClient)
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "info"])

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "collab-runtime" in out
    assert "RUNNING" in out
    assert "Active:" in out
    assert "Mine:" in out
    assert "Admin:" in out
    assert "Runtime:" in out
    assert "Project:" in out


def test_cli_info_daemon_exception(monkeypatch, capsys):
    """``collab info`` handles daemon_status raising an exception gracefully."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    class InfoClient(mod.LockClient):
        def daemon_status(self) -> bool:
            raise RuntimeError("boom")

        def active(self):
            return []

    monkeypatch.setattr(mod, "LockClient", InfoClient)
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "info"])

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "stopped" in out


def test_cli_info_active_exception(monkeypatch, capsys):
    """``collab info`` shows '?' when active() raises."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    class InfoClient(mod.LockClient):
        is_admin = False

        def daemon_status(self) -> bool:
            return False

        def active(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(mod, "LockClient", InfoClient)
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "info"])

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Active:      ?" in out
    assert "Mine:        ?" in out


def test_cli_logs_no_file(monkeypatch, capsys):
    """``collab logs`` exits 1 when log file does not exist."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    # Point _COLLAB_ROOT to a temp dir with no logs/
    import tempfile as _tf

    tmp = _tf.mkdtemp()
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "logs"])

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 1


def test_cli_logs_with_file(monkeypatch, capsys, tmp_path):
    """``collab logs`` prints last N lines of the log file."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    # Create a fake logs directory with a collab.log
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    log_file = logs_dir / "collab.log"
    log_file.write_text("line1\nline2\nline3\n", encoding="utf-8")

    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "logs", "--lines", "2"])

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "line2" in out
    assert "line3" in out


def test_cli_history_partial_match_output(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    fallback_records = [
        {
            "id": 1,
            "file_path": "collab/README.md",
            "developer_id": "alice",
            "acquired_at": "2026-04-03T22:00:00+00:00",
            "released_at": "2026-04-03T22:30:00+00:00",
            "branch_name": "main",
            "outcome": "released",
        }
    ]
    call_count = [0]

    class FallbackClient:
        def table(self, *a, **k):
            return self

        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def ilike(self, *a, **k):
            return self

        def order(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            call_count[0] += 1
            if call_count[0] == 1:
                return FakeResponse(data=[])
            return FakeResponse(data=fallback_records)

    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: (lambda url, key: FallbackClient())
    )
    lc = mod.LockClient(developer_id="test_user")
    result = lc.history(file_path="README.md")
    assert result == fallback_records
    assert call_count[0] == 2

    # RESTORED: test_validate_credentials_missing_url
    def test_validate_credentials_missing_url(monkeypatch):
        monkeypatch.setattr(mod, "SUPABASE_URL", "")
        monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")

        with pytest.raises(SystemExit):
            mod._validate_credentials()

    # RESTORED: test_validate_credentials_missing_key
    def test_validate_credentials_missing_key(monkeypatch):
        monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "")

        with pytest.raises(SystemExit):
            mod._validate_credentials()


def test_cli_dashboard(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    def mock_prepare(self):
        _tmp = os.path.join(tempfile.gettempdir(), "dash.html")
        return "http://127.0.0.1:9999/dash.html", _tmp

    monkeypatch.setattr(mod.LockClient, "_prepare_dashboard_server", mock_prepare)

    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda url: None)
    monkeypatch.setattr(
        mod.time, "sleep", lambda x: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "dashboard"])

    try:
        mod._run_cli()
    except KeyboardInterrupt:
        pass


def test_cli_watch(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(
        mod.LockClient, "_run_git_status", staticmethod(lambda: ("", True))
    )
    monkeypatch.setattr(mod.LockClient, "_reconcile", lambda self: set())
    monkeypatch.setattr(
        mod.time, "sleep", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "watch"])

    mod._run_cli()


def test_cli_no_command(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(sys, "argv", ["lock_client.py"])

    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "usage:" in out.lower()


def test_main_entry_point(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(sys, "argv", ["lock_client.py"])

    with pytest.raises(SystemExit):
        mod.main()


def test_cli_daemon_start_with_auto_open_env(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("AUTO_OPEN_DASHBOARD", "1")

    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    class FakeProc:
        pid = 12345

    popen_cmds = []
    read_pid_calls = [0]

    def mock_popen(cmd, **kwargs):
        popen_cmds.append(cmd)
        return FakeProc()

    def mock_read_pid():
        read_pid_calls[0] += 1
        if read_pid_calls[0] <= 1:
            return None
        return 67892

    class LocalLockClient(mod.LockClient):
        @staticmethod
        def _read_pid():
            return mock_read_pid()

    monkeypatch.setattr(mod, "LockClient", LocalLockClient)
    # Mock Popen so we capture the child command, and stub process liveness
    patch_subprocess(monkeypatch, popen=mock_popen)
    is_alive = staticmethod(lambda pid: True)
    monkeypatch.setattr(mod.LockClient, "_is_process_alive", is_alive)
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "daemon-start"])

    mod._run_cli()
    assert any("--open-dashboard" in str(cmd) for cmd in popen_cmds)


def test_cli_acquire_failure(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(
        sys, "argv", ["lock_client.py", "acquire", "nonexistent/file.py"]
    )

    with pytest.raises(SystemExit):
        mod._run_cli()
