"""Tests for collab.dashboard_server static asset serving."""

from __future__ import annotations

import atexit
import http.server
import os
import threading
import urllib.request
from unittest import mock

from collab import dashboard_server as mod


def test_prepare_dashboard_server_serves_sibling_static_assets(monkeypatch, tmp_path):
    """Injected HTML must be served from dashboard dir so format.js loads."""
    dash_dir = tmp_path / "dashboard"
    dash_dir.mkdir()
    (dash_dir / "index.html").write_text(
        "<html><head></head><body>"
        '<script src="dashboard-format.js"></script></body></html>',
        encoding="utf-8",
    )
    (dash_dir / "dashboard-format.js").write_text(
        "window.DashboardFormat = {};",
        encoding="utf-8",
    )

    injected = {
        "url": "https://example.supabase.co",
        "anonKey": "anon",
        "serviceKey": None,
        "user": "dev",
    }
    url, html_path = mod.prepare_dashboard_server(str(tmp_path), injected)
    assert url is not None
    assert html_path is not None

    base = url.rsplit("/", 1)[0]
    fmt_resp = urllib.request.urlopen(f"{base}/dashboard-format.js", timeout=2)
    assert fmt_resp.status == 200
    assert b"DashboardFormat" in fmt_resp.read()

    if html_path:
        try:
            os.unlink(html_path)
        except OSError:
            pass


def test_prepare_dashboard_server_missing_template(tmp_path):
    """Return None when index.html is absent."""
    (tmp_path / "dashboard").mkdir()
    url, html_path = mod.prepare_dashboard_server(
        str(tmp_path),
        {"url": "", "anonKey": "", "serviceKey": None, "user": ""},
    )
    assert url is None
    assert html_path is None


def test_write_injected_dashboard_html_uses_dashboard_dir(tmp_path):
    """Temp HTML is created inside collab/dashboard, not system temp."""
    dash_dir = tmp_path / "dashboard"
    dash_dir.mkdir()
    (dash_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    path = mod.write_injected_dashboard_html(
        str(tmp_path),
        {"url": "u", "anonKey": "k", "serviceKey": None, "user": "x"},
    )
    assert path is not None
    assert path.startswith(str(dash_dir))
    assert mod.DASHBOARD_TEMP_PREFIX in path
    content = open(path, encoding="utf-8").read()
    assert "__SUPABASE_CONFIG__" in content
    os.unlink(path)


def test_register_temp_html_cleanup_unlinks_file(tmp_path):
    """Registered atexit handler removes the generated dashboard HTML."""
    html = tmp_path / "cleanup-me.html"
    html.write_text("<html></html>", encoding="utf-8")
    callbacks: list = []
    original_register = atexit.register

    def capture(func):
        callbacks.append(func)
        return original_register(func)

    with mock.patch.object(atexit, "register", side_effect=capture):
        mod._register_temp_html_cleanup(str(html))

    assert html.exists()
    callbacks[-1]()
    assert not html.exists()


def test_register_temp_html_cleanup_swallows_missing_file(tmp_path):
    """Cleanup does not raise when the temp HTML was already removed."""
    missing = tmp_path / "already-gone.html"
    callbacks: list = []

    with mock.patch.object(atexit, "register", side_effect=callbacks.append):
        mod._register_temp_html_cleanup(str(missing))

    callbacks[-1]()


def test_start_dashboard_http_server_shutdown_is_resilient(monkeypatch, tmp_path):
    """Server shutdown hooks tolerate shutdown/close errors (atexit safety)."""
    dash_dir = tmp_path / "dashboard"
    dash_dir.mkdir()
    html = dash_dir / "live.html"
    html.write_text("<html></html>", encoding="utf-8")
    shutdown_callbacks: list = []

    class _BrokenServer:
        server_address = ("127.0.0.1", 8765)

        def __init__(self, *_args, **_kwargs):
            pass

        def serve_forever(self) -> None:
            return None

        def shutdown(self) -> None:
            raise RuntimeError("shutdown failed")

        def server_close(self) -> None:
            raise OSError("close failed")

    class _InstantThread:
        def __init__(self, target=None, **_kwargs):
            self._target = target

        def start(self) -> None:
            return None

    monkeypatch.setattr(
        http.server, "ThreadingHTTPServer", lambda *_a, **_k: _BrokenServer()
    )
    monkeypatch.setattr(threading, "Thread", _InstantThread)
    monkeypatch.setattr(atexit, "register", shutdown_callbacks.append)
    monkeypatch.setattr(
        mod,
        "_register_temp_html_cleanup",
        lambda _path: None,
    )

    url = mod.start_dashboard_http_server(str(tmp_path), str(html))
    assert url is not None

    shutdown_handlers = [
        cb for cb in shutdown_callbacks if cb.__name__ == "_safe_shutdown"
    ]
    assert shutdown_handlers
    shutdown_handlers[0]()


def test_read_dashboard_template_read_error(monkeypatch, tmp_path):
    """read_dashboard_template returns None when index.html cannot be read."""
    dash_dir = tmp_path / "dashboard"
    dash_dir.mkdir()
    (dash_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    def _fail_open(path, *args, **kwargs):
        if str(path).endswith("index.html"):
            raise OSError("permission denied")
        return open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _fail_open)
    assert mod.read_dashboard_template(str(tmp_path)) is None


def test_write_injected_dashboard_html_tmpfile_error(monkeypatch, tmp_path):
    """write_injected_dashboard_html returns None when temp file creation fails."""
    dash_dir = tmp_path / "dashboard"
    dash_dir.mkdir()
    (dash_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(
        mod.tempfile,
        "NamedTemporaryFile",
        mock.Mock(side_effect=OSError("disk full")),
    )
    assert (
        mod.write_injected_dashboard_html(
            str(tmp_path),
            {"url": "u", "anonKey": "k", "serviceKey": None, "user": "x"},
        )
        is None
    )


def test_start_dashboard_http_server_failure_unlinks_html(monkeypatch, tmp_path):
    """When the HTTP server cannot start, the injected HTML file is removed."""
    dash_dir = tmp_path / "dashboard"
    dash_dir.mkdir()
    html = dash_dir / "live.html"
    html.write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(
        http.server,
        "ThreadingHTTPServer",
        mock.Mock(side_effect=OSError("bind failed")),
    )

    url = mod.start_dashboard_http_server(
        str(tmp_path),
        str(html),
        log_error=lambda *_a, **_k: None,
        log_warning=lambda *_a, **_k: None,
    )
    assert url is None
    assert not html.exists()
