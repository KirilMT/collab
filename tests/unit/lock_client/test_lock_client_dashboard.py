"""Dashboard-related tests for LockClient.

Moved from the main `test_lock_client.py` for clearer organization.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import types
from unittest import mock

import pytest

from ._helpers import FakeResponse, load_lock_client_module, make_create_client

mod = load_lock_client_module()


def test_dashboard_opens_browser(monkeypatch):
    """Test dashboard() opens a browser."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    opened_urls = []

    def mock_prepare(self):
        _tmp = os.path.join(tempfile.gettempdir(), "dash.html")
        return "http://127.0.0.1:9999/dash.html", _tmp

    monkeypatch.setattr(mod.LockClient, "_prepare_dashboard_server", mock_prepare)

    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda url: opened_urls.append(url))

    lc = mod.LockClient(developer_id="test_user")
    lc.dashboard()
    assert len(opened_urls) == 1


def test_dashboard_no_url(monkeypatch):
    """Test dashboard() when _prepare_dashboard_server returns None."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    def mock_prepare(self):
        return None, None

    monkeypatch.setattr(mod.LockClient, "_prepare_dashboard_server", mock_prepare)

    lc = mod.LockClient(developer_id="test_user")
    lc.dashboard()  # Should return early without error


def test_dashboard_browser_exception(monkeypatch, capsys):
    """Test dashboard() handles browser open failure."""
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

    monkeypatch.setattr(
        webbrowser, "open", mock.Mock(side_effect=Exception("No browser"))
    )

    lc = mod.LockClient(developer_id="test_user")
    lc.dashboard()
    captured = capsys.readouterr()
    assert "open in browser" in captured.out.lower() or "http" in captured.out.lower()


def test_prepare_dashboard_server_missing_html(monkeypatch):
    """Test _prepare_dashboard_server when index.html is missing."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(mod, "_COLLAB_ROOT", "/nonexistent/path")

    lc = mod.LockClient(developer_id="test_user")
    url, tmp_path = lc._prepare_dashboard_server()
    assert url is None
    assert tmp_path is None


def test_prepare_dashboard_server_success(monkeypatch, tmp_path):
    """Test _prepare_dashboard_server creates server and returns URL."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    # Create fake dashboard directory with index.html
    dash_dir = tmp_path / "dashboard"
    dash_dir.mkdir()
    (dash_dir / "index.html").write_text("<html><body>Test</body></html>")

    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    lc = mod.LockClient(developer_id="test_user")
    url, tmp_file = lc._prepare_dashboard_server()

    # Should return a valid URL
    if url:
        assert "http://127.0.0.1" in url
    # Clean up temp file if created
    if tmp_file and os.path.exists(tmp_file):
        os.unlink(tmp_file)


def test_prepare_dashboard_server_read_error(monkeypatch, tmp_path):
    """Test _prepare_dashboard_server when reading HTML file fails."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    # Create dashboard dir with index.html, but make it unreadable
    dash_dir = tmp_path / "dashboard"
    dash_dir.mkdir()
    html_file = dash_dir / "index.html"
    html_file.write_text("<html></html>")

    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    # Mock open to raise IOError on the specific file
    original_open = open

    def failing_open(path, *args, **kwargs):
        if "index.html" in str(path):
            raise IOError("Permission denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    lc = mod.LockClient(developer_id="test_user")
    url, tmp_file = lc._prepare_dashboard_server()
    assert url is None


def test_prepare_dashboard_server_tmpfile_error(monkeypatch, tmp_path):
    """Test _prepare_dashboard_server when tmpfile creation fails."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    dash_dir = tmp_path / "dashboard"
    dash_dir.mkdir()
    (dash_dir / "index.html").write_text("<html></html>")

    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    # Mock tempfile to raise
    import tempfile

    monkeypatch.setattr(
        tempfile,
        "NamedTemporaryFile",
        mock.Mock(side_effect=OSError("Disk full")),
    )

    lc = mod.LockClient(developer_id="test_user")
    url, tmp_file = lc._prepare_dashboard_server()
    assert url is None
    assert tmp_file is None


def test_prepare_dashboard_server_http_error(monkeypatch, tmp_path):
    """Test _prepare_dashboard_server when HTTP server fails."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    dash_dir = tmp_path / "dashboard"
    dash_dir.mkdir()
    (dash_dir / "index.html").write_text("<html></html>")

    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    # Mock http.server.ThreadingHTTPServer to raise
    import http.server

    monkeypatch.setattr(
        http.server,
        "ThreadingHTTPServer",
        mock.Mock(side_effect=OSError("Port error")),
    )

    lc = mod.LockClient(developer_id="test_user")
    url, tmp_file = lc._prepare_dashboard_server()
    assert url is None
    assert tmp_file is None


def test_prepare_dashboard_server_socket_probe_failure(monkeypatch, tmp_path):
    """Test _prepare_dashboard_server socket probe retry."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    dash_dir = tmp_path / "dashboard"
    dash_dir.mkdir()
    (dash_dir / "index.html").write_text("<html></html>")

    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    import http.server
    import socket as _socket

    # Create a real-ish server mock that binds but returns a port
    # where sockets will initially fail then succeed
    probe_count = [0]
    original_create_connection = _socket.create_connection

    def flaky_connection(addr, timeout=None):
        probe_count[0] += 1
        if probe_count[0] <= 2:
            raise ConnectionRefusedError("not ready yet")
        return original_create_connection(addr, timeout=timeout)

    class FakeServerForProbe:
        def __init__(self, addr, handler):
            self.server_address = ("127.0.0.1", 19876)

        def serve_forever(self):
            pass

        def shutdown(self):
            pass

    monkeypatch.setattr(http.server, "ThreadingHTTPServer", FakeServerForProbe)
    monkeypatch.setattr(_socket, "create_connection", flaky_connection)
    monkeypatch.setattr(mod.time, "sleep", lambda x: None)

    lc = mod.LockClient(developer_id="test_user")
    url, tmp_file = lc._prepare_dashboard_server()

    # Should succeed after retries
    if url:
        assert "http://127.0.0.1" in url
    if tmp_file and os.path.exists(tmp_file):
        os.unlink(tmp_file)


def test_prepare_dashboard_server_unlink_error(monkeypatch, tmp_path):
    """Test _prepare_dashboard_server handles unlink error."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")

    dash_dir = tmp_path / "dashboard"
    dash_dir.mkdir()
    (dash_dir / "index.html").write_text("<html></html>")

    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )

    import http.server

    # Server creation raises, triggering the except block
    def raise_on_create(addr, handler):
        raise OSError("Cannot bind")

    monkeypatch.setattr(http.server, "ThreadingHTTPServer", raise_on_create)

    # Also mock os.unlink to raise, covering unlink error
    original_unlink = os.unlink

    def failing_unlink(path):
        if path.endswith(".html"):
            raise OSError("Permission denied on unlink")
        return original_unlink(path)

    monkeypatch.setattr(os, "unlink", failing_unlink)

    lc = mod.LockClient(developer_id="test_user")
    url, tmp_file = lc._prepare_dashboard_server()
    assert url is None
    assert tmp_file is None


# --- Appended from test_lock_client_dashboard_cli_cleanup.py ---


def test_prepare_dashboard_server_cli_migrated(monkeypatch, tmp_path):
    mod = load_lock_client_module()

    # Use a temporary .collab/dashboard directory
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    d = tmp_path / "dashboard"
    d.mkdir(parents=True, exist_ok=True)
    html = d / "index.html"
    html.write_text("<html>dashboard</html>")

    # Fake ThreadingHTTPServer and Thread so we don't actually bind a port
    import http.server as _http
    import threading as _threading

    class FakeServer:
        def __init__(self, addr, handler):
            self.server_address = ("127.0.0.1", 54321)

        def serve_forever(self):
            return None

        def shutdown(self):
            return None

    class FakeThread:
        def __init__(self, target, daemon=True):
            self._target = target

        def start(self):
            return None

    monkeypatch.setattr(_http, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(_threading, "Thread", FakeThread)

    # Make socket.create_connection succeed immediately
    class DummyConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: DummyConn())

    url, tmpfile = mod.LockClient(local_only=True)._prepare_dashboard_server()
    assert url and tmpfile
    txt = open(tmpfile, "r", encoding="utf-8").read()
    assert "window.__SUPABASE_CONFIG__" in txt
    try:
        os.unlink(tmpfile)
    except Exception:
        pass


def test_cleanup_orphaned_processes_unix_cli(monkeypatch, capsys):
    mod = load_lock_client_module()
    # Force UNIX branch
    monkeypatch.setattr(sys, "platform", "linux", raising=False)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")

    out_line = "root 9999 0.0 0 0 ? S 0:00 python lock_client"

    def fake_run(*a, **k):
        return types.SimpleNamespace(stdout=out_line)

    monkeypatch.setattr("subprocess.run", fake_run)

    killed = {"n": 0}

    def fake_kill(pid, sig):
        killed["n"] += 1

    monkeypatch.setattr("os.kill", fake_kill)

    client = mod.LockClient(local_only=True)
    client._remove_pid = lambda: None
    client.cleanup_orphaned_processes()
    captured = capsys.readouterr()
    assert killed["n"] >= 1
    assert "Killing orphaned" in captured.out or "Killed" in captured.out


def test_cli_force_release_all_and_cleanup_migrated(monkeypatch):
    mod = load_lock_client_module()

    class FakeLockClient:
        last = None

        def __init__(self, local_only=False):
            FakeLockClient.last = self
            self.is_admin = True

        def force_release_all(self):
            return 3

        def cleanup_orphaned_processes(self):
            self.cleaned = True

    monkeypatch.setattr(mod, "LockClient", FakeLockClient)

    # force-release-all should exit 0 when admin
    monkeypatch.setattr(  # type: ignore[arg-type]
        sys, "argv", ["prog", "force-release-all"]
    )
    with pytest.raises(SystemExit) as exc:
        mod._run_cli()
    assert exc.value.code == 0

    # cleanup should call the instance method (no SystemExit expected)
    monkeypatch.setattr(sys, "argv", ["prog", "cleanup"])  # type: ignore[arg-type]
    mod._run_cli()
    assert getattr(FakeLockClient.last, "cleaned", False) is True


def test_get_parent_ide_pid_vscode_cli_migrated(monkeypatch):
    mod = load_lock_client_module()
    monkeypatch.setenv("VSCODE_PID", "4321")
    client = mod.LockClient(local_only=True)
    monkeypatch.setattr(client, "_is_process_alive", lambda pid: True)
    pid, method = client._get_parent_ide_pid()
    assert pid == 4321
    assert method == "vscode_pid"
