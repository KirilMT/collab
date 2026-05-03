"""CLI-focused tests for LockClient moved from the canonical file.

These tests use the shared helpers in `_helpers.py` to load the module and
re-use the FakeResponse/FakeClient factories.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest

from ._helpers import FakeResponse, load_lock_client_module, make_create_client

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
            "file_path": "src/other/app.py",
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
        ["lock_client.py", "history", "src/requested.py"],
    )

    mod._run_cli()
    out = capsys.readouterr().out
    assert "no exact match" in out.lower()
    assert "partial matches" in out.lower()


def test_main_unhandled_exception_exits_with_fatal(monkeypatch, capsys):
    """Cover main() unhandled-exception logging and fatal stderr message."""
    monkeypatch.setattr(
        mod, "_run_cli", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(SystemExit):
        mod.main()

    err = capsys.readouterr().err
    assert "fatal: lock_client crashed" in err.lower()


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

    test_file = tmp_path / "src" / "app.py"
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

    response = FakeResponse(status=200, data=[{"file_path": "src/app.py"}])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "release", "src/app.py"])

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


def test_cli_active_with_locks(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(
        status=200,
        data=[
            {
                "file_path": "src/app.py",
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
    assert "src/app.py" in captured.out


def test_cli_status_locked(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
    response = FakeResponse(
        status=200,
        data=[
            {
                "file_path": "src/app.py",
                "developer_id": "user1",
                "acquired_at": "2025-01-01T10:00:00+00:00",
                "expires_at": future,
            }
        ],
    )
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "status", "src/app.py"])

    mod._run_cli()
    captured = capsys.readouterr()
    assert "locked" in captured.out.lower() or "🔒" in captured.out


def test_cli_status_unlocked(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(status=200, data=[])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "status", "src/app.py"])

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


def test_cli_force_release(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    response = FakeResponse(status=200, data=[{"file_path": "src/app.py"}])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "force-release", "src/app.py"])

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

    file1 = tmp_path / "src" / "a.py"
    file2 = tmp_path / "src" / "b.py"
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

    file1 = tmp_path / "src" / "a.py"
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

    response = FakeResponse(status=200, data=[{"file_path": "src/app.py"}])
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(
        sys, "argv", ["lock_client.py", "release-batch", "src/a.py", "src/b.py"]
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
    monkeypatch.setattr(subprocess, "Popen", mock_popen_wrap)
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
    monkeypatch.setattr(mod.LockClient, "_run_git_status", staticmethod(lambda: ""))
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

    records = [{"id": 1, "file_path": "src/app.py", "developer_id": "alice"}]
    response = FakeResponse(status=200, data=records)
    monkeypatch.setattr(mod, "_get_create_client", lambda: make_create_client(response))
    monkeypatch.setattr(sys, "argv", ["lock_client.py", "history", "--json"])

    mod._run_cli()
    captured = capsys.readouterr()
    assert '"file_path"' in captured.out
    assert '"src/app.py"' in captured.out


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
            "file_path": "src/app.py",
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
    assert "src/app.py" in captured.out
    assert "@alice" in captured.out
    assert "released" in captured.out
    assert "branch:main" in captured.out


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
    monkeypatch.setattr(mod.LockClient, "_run_git_status", staticmethod(lambda: ""))
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

    mod._run_cli()
    capsys.readouterr()


def test_main_entry_point(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(sys, "argv", ["lock_client.py"])

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
    monkeypatch.setattr(subprocess, "Popen", mock_popen)
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
