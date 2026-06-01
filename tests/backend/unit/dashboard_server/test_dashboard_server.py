"""Tests for collab.dashboard_server static asset serving."""

from __future__ import annotations

import atexit
import http.server
import json
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


def test_repo_name_from_remote_url_parses_https_and_scp():
    assert (
        mod._repo_name_from_remote_url("https://github.com/KirilMT/collab.git")
        == "collab"
    )
    assert (
        mod._repo_name_from_remote_url("git@github.com:KirilMT/collab.git") == "collab"
    )
    assert mod._repo_name_from_remote_url("") is None


def test_resolve_project_display_name_precedence(tmp_path, monkeypatch):
    """Env override wins; otherwise git remote repo name is used."""
    monkeypatch.delenv("COLLAB_PROJECT_NAME", raising=False)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "from-pyproject"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "_name_from_git_remote", lambda _root: "from-git")
    assert mod.resolve_project_display_name(str(tmp_path)) == "from-git"

    (tmp_path / ".env").write_text(
        "COLLAB_PROJECT_NAME=My Custom App\n",
        encoding="utf-8",
    )
    from dotenv import dotenv_values

    vals = dict(dotenv_values(tmp_path / ".env"))
    assert mod.resolve_project_display_name(str(tmp_path), vals) == "My Custom App"


def test_resolve_project_display_name_pyproject_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("COLLAB_PROJECT_NAME", raising=False)
    monkeypatch.setattr(mod, "_name_from_git_remote", lambda _root: None)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "collab-runtime"\n',
        encoding="utf-8",
    )
    assert mod.resolve_project_display_name(str(tmp_path)) == "collab-runtime"


def test_resolve_project_display_name_package_json_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("COLLAB_PROJECT_NAME", raising=False)
    monkeypatch.setattr(mod, "_name_from_git_remote", lambda _root: None)
    (tmp_path / "package.json").write_text('{"name": "pkg-name"}\n', encoding="utf-8")
    assert mod.resolve_project_display_name(str(tmp_path)) == "pkg-name"


def test_name_from_pyproject_handles_invalid_toml(tmp_path):
    (tmp_path / "pyproject.toml").write_text("not valid {{{\n", encoding="utf-8")
    assert mod._name_from_pyproject(str(tmp_path)) is None


def test_name_from_package_json_handles_invalid_json(tmp_path):
    (tmp_path / "package.json").write_text("{bad json", encoding="utf-8")
    assert mod._name_from_package_json(str(tmp_path)) is None


def test_name_from_git_remote_parses_config_output(tmp_path, monkeypatch):
    from collab import safe_subprocess

    class _Cap:
        ok = True
        timed_out = False
        stdout = b"https://github.com/org/demo.git\n"

    monkeypatch.setattr(safe_subprocess, "capture", lambda *a, **k: _Cap())
    monkeypatch.setattr(safe_subprocess, "decode_output", lambda b: b.decode("utf-8"))
    assert mod._name_from_git_remote(str(tmp_path)) == "demo"


def test_name_from_git_remote_returns_none_when_git_fails(tmp_path, monkeypatch):
    from collab import safe_subprocess

    class _Cap:
        ok = False
        timed_out = False
        stdout = b""

    monkeypatch.setattr(safe_subprocess, "capture", lambda *a, **k: _Cap())
    assert mod._name_from_git_remote(str(tmp_path)) is None


def test_runtime_config_endpoint_serves_fresh_env(monkeypatch, tmp_path):
    """Dashboard sync can reload Supabase credentials from project .env."""
    dash_dir = tmp_path / "dashboard"
    dash_dir.mkdir()
    (dash_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "SUPABASE_URL=https://fresh.supabase.co\n"
        "SUPABASE_ANON_KEY=anon-fresh\n"
        "COLLAB_DEVELOPER_ID=tester\n",
        encoding="utf-8",
    )

    html = mod.write_injected_dashboard_html(
        str(tmp_path),
        {
            "url": "https://stale.supabase.co",
            "anonKey": "old",
            "serviceKey": None,
            "user": "x",
        },
    )
    assert html is not None

    url = mod.start_dashboard_http_server(
        str(tmp_path), html, project_root=str(tmp_path)
    )
    assert url is not None

    base = url.rsplit("/", 1)[0]
    cfg_resp = urllib.request.urlopen(
        f"{base}{mod.RUNTIME_CONFIG_PATH}",
        timeout=2,
    )
    assert cfg_resp.status == 200
    payload = json.loads(cfg_resp.read().decode("utf-8"))
    assert payload["url"] == "https://fresh.supabase.co"
    assert payload["anonKey"] == "anon-fresh"
    assert payload["user"] == "tester"
    assert payload["projectName"] == tmp_path.name

    os.unlink(html)


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
