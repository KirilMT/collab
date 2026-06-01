"""Local HTTP server for the collaborative dashboard static assets.

The dashboard HTML references sibling static files (e.g. ``dashboard-format.js``). The
injected config HTML must therefore be written *inside* ``collab/dashboard/`` and served
from that directory — not from a lone temp file in ``/tmp``.
"""

from __future__ import annotations

import atexit
import http.server
import json
import logging
import os
import tempfile
import threading
import time
import urllib.parse
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)

DASHBOARD_TEMP_PREFIX = ".collab-dashboard-"
RUNTIME_CONFIG_PATH = "/collab-runtime-config.json"


def load_runtime_supabase_config(project_root: str) -> dict[str, Any]:
    """Read ``.env`` from *project_root* and return dashboard Supabase settings.

    The local dashboard fetches this on each sync so credential changes take effect
    without restarting the watcher or reopening a stale browser tab. The project
    ``.env`` is read directly (not via ``load_dotenv``) so polling never mutates the
    running watcher's process environment.
    """
    from dotenv import dotenv_values

    env_path = os.path.join(project_root, ".env")
    file_vals: dict[str, Any] = {}
    if os.path.isfile(env_path):
        try:
            file_vals = dict(dotenv_values(env_path))
        except OSError as exc:
            logger.debug("Could not read %s for runtime config: %s", env_path, exc)

    def pick(name: str) -> Optional[str]:
        val = file_vals.get(name)
        if not val:
            val = os.getenv(name)
        return val

    url = pick("SUPABASE_URL") or ""
    anon = pick("SUPABASE_ANON_KEY") or ""
    service = pick("SUPABASE_SERVICE_ROLE_KEY") or None
    user = (
        pick("COLLAB_DEVELOPER_ID")
        or pick("DEVELOPER_ID")
        or os.getenv("USERNAME")
        or os.getenv("USER")
        or ""
    )
    from . import agent_identity

    state_override = os.getenv("COLLAB_STATE_DIR", "").strip()
    state_dir = state_override or os.path.join(project_root, ".collab")
    agent_id = agent_identity.resolve_agent_id(state_dir)
    agent_label = agent_identity.resolve_agent_label()
    return {
        "url": url,
        "anonKey": anon,
        "serviceKey": service,
        "user": user,
        "agentId": agent_id,
        "agentLabel": agent_label,
    }


def create_dashboard_handler(project_root: str, directory: str) -> type:
    """Build a request handler that serves static assets and live runtime config."""

    class DashboardHandler(http.server.SimpleHTTPRequestHandler):
        """Serve dashboard static files without per-request stderr logging."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=directory, **kwargs)

        def log_message(self, format: str, *args: Any) -> None:
            """Suppress default SimpleHTTPRequestHandler request log lines."""

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == RUNTIME_CONFIG_PATH:
                self._serve_runtime_config()
                return
            super().do_GET()

        def _serve_runtime_config(self) -> None:
            payload = load_runtime_supabase_config(project_root)
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return DashboardHandler


def dashboard_directory(resource_root: str) -> str:
    """Return the path to packaged dashboard static assets."""
    return os.path.join(resource_root, "dashboard")


def read_dashboard_template(resource_root: str) -> Optional[str]:
    """Read ``index.html`` from the dashboard package directory."""
    html_path = os.path.join(dashboard_directory(resource_root), "index.html")
    if not os.path.exists(html_path):
        logger.error("Dashboard file not found at %s", html_path)
        return None
    try:
        with open(html_path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        logger.error("Error reading dashboard template: %s", exc)
        return None


def write_injected_dashboard_html(
    resource_root: str, injected: dict[str, Any]
) -> Optional[str]:
    """Write config-injected HTML next to static assets; return path or None."""
    content = read_dashboard_template(resource_root)
    if content is None:
        return None

    dash_dir = dashboard_directory(resource_root)
    inject_script = (
        f"<script>window.__SUPABASE_CONFIG__ = {json.dumps(injected)};</script>\n"
    )
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            suffix=".html",
            prefix=DASHBOARD_TEMP_PREFIX,
            dir=dash_dir,
            encoding="utf-8",
        )
        tmp.write(inject_script)
        tmp.write(content)
        tmp.flush()
        tmp.close()
        return tmp.name
    except OSError as exc:
        logger.error("Error creating temp dashboard file: %s", exc)
        return None


def _register_temp_html_cleanup(html_path: str) -> None:
    """Remove generated dashboard HTML on process exit."""

    def _unlink() -> None:
        try:
            os.unlink(html_path)
        except OSError:
            pass

    atexit.register(_unlink)


def start_dashboard_http_server(
    resource_root: str,
    injected_html_path: str,
    *,
    project_root: Optional[str] = None,
    log_error: Callable[[str, Any], None] = logger.error,
    log_warning: Callable[[str, Any], None] = logger.warning,
) -> Optional[str]:
    """Serve ``collab/dashboard`` and return the URL to the injected HTML file."""
    dash_dir = dashboard_directory(resource_root)
    filename = os.path.basename(injected_html_path)
    env_root = project_root or resource_root

    try:
        handler_cls = create_dashboard_handler(env_root, dash_dir)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = server.server_address[1]

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def _safe_shutdown() -> None:
            try:
                server.shutdown()
            except BaseException:
                pass
            close = getattr(server, "server_close", None)
            if callable(close):
                try:
                    close()
                except OSError:
                    pass

        atexit.register(_safe_shutdown)
        _register_temp_html_cleanup(injected_html_path)

        url = f"http://127.0.0.1:{port}/{filename}"

        import socket

        for _ in range(20):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.05)

        return url
    except OSError as exc:
        log_error("Failed to start local dashboard server: %s", exc)
        try:
            os.unlink(injected_html_path)
        except OSError as cleanup_exc:
            log_warning("Dashboard temp-file cleanup failed: %s", cleanup_exc)
        return None


def prepare_dashboard_server(
    resource_root: str,
    injected: dict[str, Any],
    *,
    project_root: Optional[str] = None,
    log_error: Callable[[str, Any], None] = logger.error,
    log_warning: Callable[[str, Any], None] = logger.warning,
) -> Tuple[Optional[str], Optional[str]]:
    """Write injected HTML, start server from dashboard dir; return (url, path)."""
    html_path = write_injected_dashboard_html(resource_root, injected)
    if not html_path:
        return None, None

    url = start_dashboard_http_server(
        resource_root,
        html_path,
        project_root=project_root,
        log_error=log_error,
        log_warning=log_warning,
    )
    if not url:
        return None, None
    return url, html_path
