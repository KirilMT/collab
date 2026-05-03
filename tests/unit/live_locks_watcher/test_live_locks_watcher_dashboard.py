"""Dashboard and server-start tests for live_locks_watcher."""

from __future__ import annotations

from ._helpers import load_watcher_module


def test_start_dashboard_server_returns_url(monkeypatch):
    mod = load_watcher_module()
    import http.server

    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "SUPABASE_SERVICE_ROLE_KEY", None)
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")

    class FakeServer:
        def __init__(self, addr, handler):
            self.server_address = (addr[0], 9999)

        def serve_forever(self):
            pass

        def shutdown(self):
            pass

    monkeypatch.setattr(http.server, "ThreadingHTTPServer", FakeServer)

    url = mod._start_dashboard_server()
    assert url is not None
    assert url.startswith("http://127.0.0.1:9999/")
    assert url.endswith(".html")


def test_start_dashboard_server_missing_html(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_COLLAB_ROOT", "/nonexistent/path")
    result = mod._start_dashboard_server()
    assert result is None


def test_start_dashboard_server_read_error(monkeypatch, tmp_path):
    mod = load_watcher_module()
    dashboard_dir = tmp_path / "dashboard"
    dashboard_dir.mkdir()
    html_file = dashboard_dir / "index.html"
    html_file.write_text("test")
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))

    original_open = open

    def failing_open(path, *args, **kwargs):
        if "index.html" in str(path):
            raise PermissionError("denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)
    result = mod._start_dashboard_server()
    assert result is None


def test_start_dashboard_server_http_server_error(monkeypatch, tmp_path):
    mod = load_watcher_module()
    import http.server

    dashboard_dir = tmp_path / "dashboard"
    dashboard_dir.mkdir()
    (dashboard_dir / "index.html").write_text("<html></html>")
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "key")
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")

    def raising_server(*args, **kwargs):
        raise OSError("Address already in use")

    monkeypatch.setattr(http.server, "ThreadingHTTPServer", raising_server)

    result = mod._start_dashboard_server()
    assert result is None


def test_start_dashboard_server_migrated(tmp_path, monkeypatch):
    mod = load_watcher_module()
    # Prepare dashboard directory
    d = tmp_path / "dashboard"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text("<html>ok</html>")
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))

    # Monkeypatch the ThreadingHTTPServer to avoid binding sockets
    import http.server as _http
    import threading as _threading

    class FakeServer:
        def __init__(self, addr, handler):
            self.server_address = ("127.0.0.1", 11111)

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

    url = mod._start_dashboard_server()
    assert url is not None


def test_start_dashboard_server_tmpfile_error(monkeypatch, tmp_path):
    mod = load_watcher_module()
    import tempfile

    dashboard_dir = tmp_path / "dashboard"
    dashboard_dir.mkdir()
    (dashboard_dir / "index.html").write_text("<html></html>")
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "key")
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")

    def failing_tmpfile(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", failing_tmpfile)

    result = mod._start_dashboard_server()
    assert result is None


def test_start_dashboard_server_unlink_error(monkeypatch, tmp_path):
    mod = load_watcher_module()
    import http.server

    dashboard_dir = tmp_path / "dashboard"
    dashboard_dir.mkdir()
    (dashboard_dir / "index.html").write_text("<html></html>")
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "key")
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")

    def raising_server(*args, **kwargs):
        raise OSError("port in use")

    monkeypatch.setattr(http.server, "ThreadingHTTPServer", raising_server)

    original_unlink = __import__("os").unlink

    def failing_unlink(path):
        if str(path).endswith(".html"):
            raise OSError("cannot unlink")
        return original_unlink(path)

    monkeypatch.setattr(__import__("os"), "unlink", failing_unlink)

    result = mod._start_dashboard_server()
    assert result is None


# ---- Auto-migrated from migrated_remaining ----


def test_start_dashboard_server_missing_and_success_moved(tmp_path, monkeypatch):
    mod = load_watcher_module()
    tmp_root = tmp_path / "collab_root"
    (tmp_root / "dashboard").mkdir(parents=True)
    # missing file
    monkeypatch.setattr(watcher, "_COLLAB_ROOT", str(tmp_root))
    url = mod._start_dashboard_server()
    assert url is None

    # create file and succeed
    (tmp_root / "dashboard" / "index.html").write_text("<html>ok</html>")
    url2 = mod._start_dashboard_server()
    assert url2 and url2.startswith("http://127.0.0.1:")


watcher = load_watcher_module()
