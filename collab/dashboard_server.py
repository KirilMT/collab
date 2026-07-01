"""Local HTTP server for the collaborative dashboard static assets.

The dashboard HTML references sibling static files (e.g. ``dashboard-format.js``). The
injected config HTML must therefore be written *inside* ``collab/dashboard/`` and served
from that directory — not from a lone temp file in ``/tmp``.
"""

from __future__ import annotations

import atexit
import fnmatch
import http.server
import json
import logging
import os
import re
import tempfile
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence, Tuple

import tomli as tomllib

logger = logging.getLogger(__name__)

DASHBOARD_TEMP_PREFIX = ".collab-dashboard-"
RUNTIME_CONFIG_PATH = "/collab-runtime-config.json"


def _repo_name_from_remote_url(url: str) -> Optional[str]:
    """Extract repo folder name from HTTPS or SCP-style git remote URLs."""
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        return None
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    if ":" in cleaned and "@" in cleaned.split(":", 1)[0]:
        cleaned = cleaned.rsplit(":", 1)[-1]
    tail = cleaned.rsplit("/", 1)[-1].strip()
    return tail or None


def _name_from_pyproject(project_root: str) -> Optional[str]:
    """Return ``[project].name`` from ``pyproject.toml`` when present."""
    path = os.path.join(project_root, "pyproject.toml")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        name = data.get("project", {}).get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
        logger.debug("Could not read pyproject.toml project name: %s", exc)
    return None


def _name_from_package_json(project_root: str) -> Optional[str]:
    """Return ``name`` from ``package.json`` when present."""
    path = os.path.join(project_root, "package.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        name = data.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.debug("Could not read package.json name: %s", exc)
    return None


def _name_from_git_remote(project_root: str) -> Optional[str]:
    """Return the repository folder name from ``remote.origin.url`` (git config)."""
    from collab import safe_subprocess

    captured = safe_subprocess.capture(
        ["git", "config", "--get", "remote.origin.url"],
        policy="git",
        cwd=project_root,
        timeout=5,
    )
    if not captured.ok or captured.timed_out:
        return None
    remote = safe_subprocess.decode_output(captured.stdout).strip()
    return _repo_name_from_remote_url(remote)


def resolve_project_display_name(
    project_root: str,
    file_vals: Optional[dict[str, Any]] = None,
) -> str:
    """Return a human-friendly label for the repository Collab is serving.

    Precedence (repository-first, as shown in the dashboard header):

    1. ``COLLAB_PROJECT_NAME`` (``.env`` or environment)
    2. Git ``origin`` remote repository name (e.g. ``collab`` from ``.../collab.git``)
    3. ``pyproject.toml`` ``[project].name``
    4. ``package.json`` ``name``
    5. Basename of *project_root*
    """
    vals = file_vals if file_vals is not None else {}
    override = vals.get("COLLAB_PROJECT_NAME") or os.getenv("COLLAB_PROJECT_NAME") or ""
    if isinstance(override, str) and override.strip():
        return override.strip()

    for resolver in (
        lambda: _name_from_git_remote(project_root),
        lambda: _name_from_pyproject(project_root),
        lambda: _name_from_package_json(project_root),
    ):
        name = resolver()
        if name:
            return name

    return os.path.basename(os.path.abspath(project_root)) or "project"


def _resolve_developer_name(project_root: str, file_vals: dict[str, Any]) -> str:
    """Return a human-friendly developer identifier for the dashboard header.

    Precedence (developer-first, as shown in the user-info chip):

    1. ``COLLAB_DEVELOPER_ID`` (``.env`` or environment)
    2. ``DEVELOPER_ID`` (``.env`` or environment)
    3. ``git config github.user`` (local → global)
    4. GitHub username extracted from ``remote.origin.url``
    5. ``git config user.name`` (local → global)
    6. ``USERNAME`` / ``USER`` environment variables
    7. Empty string (dashboard hides the chip)
    """

    def _pick(name: str) -> Optional[str]:
        val = file_vals.get(name)
        if not val:
            val = os.getenv(name)
        return val

    # 1-2. Explicit env vars
    for key in ("COLLAB_DEVELOPER_ID", "DEVELOPER_ID"):
        val = _pick(key)
        if val and val.strip():
            return val.strip()

    # 3. git config github.user
    from collab import safe_subprocess

    for scope in ("--local", "--global"):
        captured = safe_subprocess.capture(
            ["git", "config", scope, "github.user"],
            policy="git",
            cwd=project_root,
            timeout=5,
        )
        if captured.ok and not captured.timed_out:
            gh_user = safe_subprocess.decode_output(captured.stdout).strip()
            if gh_user:
                return gh_user

    # 4. Extract from remote.origin.url (e.g. github.com/KirilMT/collab.git)
    remote_name = _name_from_git_remote(project_root)
    if remote_name:
        captured = safe_subprocess.capture(
            ["git", "config", "--get", "remote.origin.url"],
            policy="git",
            cwd=project_root,
            timeout=5,
        )
        if captured.ok and not captured.timed_out:
            remote_url = safe_subprocess.decode_output(captured.stdout).strip()
            if remote_url:
                # Try to extract GitHub username from SSH or HTTPS URL
                import re

                # SSH: git@github.com:USER/REPO.git
                # HTTPS: https://github.com/USER/REPO.git
                match = re.search(r"github\.com[:/]([^/]+)/", remote_url)
                if match:
                    return match.group(1)

    # 5. git config user.name
    captured = safe_subprocess.capture(
        ["git", "config", "--get", "user.name"],
        policy="git",
        cwd=project_root,
        timeout=5,
    )
    if captured.ok and not captured.timed_out:
        git_name = safe_subprocess.decode_output(captured.stdout).strip()
        if git_name:
            return git_name

    # 6. OS username
    for env_name in ("USERNAME", "USER"):
        val = os.getenv(env_name)
        if val and val.strip():
            return val.strip()

    # 7. Nothing found
    return ""


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

    from .env_secrets import effective_anon_key, effective_service_role_key

    url = pick("SUPABASE_URL") or ""
    anon = effective_anon_key(pick("SUPABASE_ANON_KEY")) or ""
    service = effective_service_role_key(pick("SUPABASE_SERVICE_ROLE_KEY"))
    user = _resolve_developer_name(project_root, file_vals)
    from . import agent_identity

    state_override = os.getenv("COLLAB_STATE_DIR", "").strip()
    state_dir = state_override or os.path.join(project_root, ".collab")
    agent_id = agent_identity.resolve_agent_id(state_dir)
    agent_label = agent_identity.resolve_agent_label()
    agent_kind = agent_identity.resolve_agent_kind(agent_id=agent_id)
    project_name = resolve_project_display_name(project_root, file_vals)
    watcher_status = _check_watcher_health(state_dir, project_root)
    return {
        "url": url,
        "anonKey": anon,
        "serviceKey": service,
        "user": user,
        "agentId": agent_id,
        "agentLabel": agent_label,
        "agentKind": agent_kind,
        "projectName": project_name,
        "watcher": watcher_status,
    }


def _check_watcher_health(state_dir: str, project_root: str = "") -> dict[str, Any]:
    """Check if the collab watcher daemon is running and return health info.

    Returns a dict with ``running`` (bool) and optionally ``heartbeatLatencyMs`` (int)
    when available.
    """
    if not state_dir or not os.path.isdir(state_dir):
        if not project_root:
            return {"running": False}
        # Skip to fallback below
        state_dir = ""

    # Match the watcher's own PID file naming: .daemon.pid (or .daemon.<agent>.pid)
    from .agent_identity import daemon_pid_basename

    pid_file = os.path.join(state_dir, daemon_pid_basename(None))
    if not os.path.isfile(pid_file):
        # Try agent-specific PID files
        import glob as _glob

        candidates = _glob.glob(os.path.join(state_dir, ".daemon.*.pid"))
        if candidates:
            pid_file = candidates[0]
        else:
            # Fallback: lock_client stores PID in tempdir/collab_runtime_<hash>/
            if not project_root:
                return {"running": False}
            try:
                import hashlib as _hashlib
                import tempfile as _tempfile

                norm = (
                    os.path.abspath(project_root)
                    .replace("/", "\\")
                    .lower()
                    .rstrip("\\")
                )
                h = _hashlib.sha1(
                    norm.encode("utf-8"), usedforsecurity=False
                ).hexdigest()[:8]
                ws_dir = os.path.join(_tempfile.gettempdir(), f"collab_runtime_{h}")
                alt_pid = os.path.join(ws_dir, daemon_pid_basename(None))
                if os.path.isfile(alt_pid):
                    pid_file = alt_pid
                else:
                    alt_glob = _glob.glob(os.path.join(ws_dir, ".daemon.*.pid"))
                    if alt_glob:
                        pid_file = alt_glob[0]
                    else:
                        return {"running": False}
            except Exception:
                return {"running": False}

    try:
        with open(pid_file, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
        # PID file may be plain int or a JSON object with "pid" key
        if raw.startswith("{"):
            data = json.loads(raw)
            pid = int(data["pid"])
        else:
            pid = int(raw)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return {"running": False}

    # Check if PID is alive
    import platform

    if platform.system() == "Windows":
        try:
            import ctypes

            # mypy on Linux does not know ctypes.windll — the type: ignore
            # below lets the type-check pass. At runtime on Windows windll
            # is always present; the except AttributeError is defensive.
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(
                0x0400, False, pid
            )  # PROCESS_QUERY_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                # Process exists
            else:
                return {"running": False}
        except (AttributeError, OSError):
            return {"running": False}
    else:
        try:
            os.kill(pid, 0)
        except OSError:
            return {"running": False}

    # Compute heartbeat latency from watcher PID file mtime
    try:
        mtime = os.path.getmtime(pid_file)
        latency_ms = int((time.time() - mtime) * 1000)
    except OSError:
        latency_ms = None

    result: dict[str, Any] = {"running": True}
    if latency_ms is not None:
        result["heartbeatLatencyMs"] = latency_ms
    return result


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


# --- Dashboard static-asset packaging guards -------------------------------
#
# index.html loads sibling assets (e.g. ``dashboard-format.js``). If those files
# are not shipped in the wheel ``[tool.setuptools.package-data]`` the browser gets
# a 404 and the dashboard renders blank. The helpers below let the runtime warn on
# a broken install and let tests prove every referenced/shipped asset is packaged.

_PACKAGE_DASHBOARD_PREFIX = "dashboard/"
_WHEEL_DASHBOARD_PREFIX = "collab/dashboard/"

_LOCAL_SCRIPT_SRC = re.compile(
    r'<script[^>]+src=["\'](?!https?://)([^"\']+)["\']',
    re.IGNORECASE,
)
_LOCAL_LINK_HREF = re.compile(
    r'<link[^>]+href=["\'](?!https?://)([^"\']+)["\']',
    re.IGNORECASE,
)


def _normalize_static_ref(ref: str) -> Optional[str]:
    """Strip query/fragment and reject absolute or protocol-relative refs."""
    path = ref.split("?", 1)[0].split("#", 1)[0].strip()
    if not path or path.startswith("/"):
        return None
    return path.replace("\\", "/")


def local_static_refs_from_html(html: str) -> Tuple[str, ...]:
    """Return sorted relative script/link paths referenced by dashboard HTML.

    CDN (``https://``) and absolute (``/foo``) references are ignored; only assets
    that must ship inside the package are returned.
    """
    refs: list[str] = []
    for pattern in (_LOCAL_SCRIPT_SRC, _LOCAL_LINK_HREF):
        for raw in pattern.findall(html):
            normalized = _normalize_static_ref(raw)
            if normalized:
                refs.append(normalized)
    return tuple(sorted(set(refs)))


def shipped_dashboard_relative_paths(resource_root: str) -> Tuple[str, ...]:
    """Return dashboard-relative paths of files that must ship in the wheel.

    Hidden files (e.g. injected ``.collab-dashboard-*`` temp HTML) are excluded.
    """
    dash_dir = Path(dashboard_directory(resource_root))
    if not dash_dir.is_dir():
        return ()
    return tuple(
        path.relative_to(dash_dir).as_posix()
        for path in sorted(dash_dir.rglob("*"))
        if path.is_file() and not path.name.startswith(".")
    )


def missing_local_static_files(resource_root: str, html: str) -> Tuple[str, ...]:
    """Return local refs in *html* that are absent on disk under the dashboard dir."""
    dash_dir = Path(dashboard_directory(resource_root))
    return tuple(
        rel
        for rel in local_static_refs_from_html(html)
        if not (dash_dir / rel).is_file()
    )


def verify_dashboard_static_assets(resource_root: str) -> Tuple[str, ...]:
    """Log and return any local assets referenced by index.html but missing.

    Called on each template read so a broken wheel (or partial dev tree) surfaces a
    clear error instead of a silent blank dashboard.
    """
    dash_dir = Path(dashboard_directory(resource_root))
    index_path = dash_dir / "index.html"
    if not index_path.is_file():
        logger.error("Dashboard template missing at %s", index_path)
        return ("index.html",)
    missing = missing_local_static_files(
        resource_root, index_path.read_text(encoding="utf-8")
    )
    for rel in missing:
        logger.error(
            "Dashboard static asset missing at %s (broken wheel or dev tree)",
            dash_dir / rel,
        )
    return missing


def read_package_data_patterns(pyproject_path: str) -> Tuple[str, ...]:
    """Return ``[tool.setuptools.package-data].collab`` glob patterns."""
    with open(pyproject_path, "rb") as fh:
        data = tomllib.load(fh)
    patterns = (
        data.get("tool", {})
        .get("setuptools", {})
        .get("package-data", {})
        .get("collab", [])
    )
    if isinstance(patterns, str):
        return (patterns,)
    if isinstance(patterns, list):
        return tuple(str(p) for p in patterns)
    return ()


def package_data_covers(relative_path: str, patterns: Sequence[str]) -> bool:
    """Return True when a dashboard-relative path matches a package-data glob."""
    for pattern in patterns:
        if not pattern.startswith(_PACKAGE_DASHBOARD_PREFIX):
            continue
        suffix = pattern[len(_PACKAGE_DASHBOARD_PREFIX) :]
        if suffix == "**":
            return True
        if fnmatch.fnmatch(relative_path, suffix):
            return True
    return False


def missing_package_data_coverage(
    resource_root: str, patterns: Sequence[str]
) -> Tuple[str, ...]:
    """Return shipped dashboard files not matched by any package-data glob."""
    return tuple(
        rel
        for rel in shipped_dashboard_relative_paths(resource_root)
        if not package_data_covers(rel, patterns)
    )


def wheel_dashboard_member_paths(wheel_path: str) -> Tuple[str, ...]:
    """Return dashboard-relative paths contained in a built wheel archive."""
    members: set[str] = set()
    with zipfile.ZipFile(wheel_path) as archive:
        for name in archive.namelist():
            normalized = name.replace("\\", "/")
            if normalized.startswith(_WHEEL_DASHBOARD_PREFIX) and not (
                normalized.endswith("/")
            ):
                members.add(normalized[len(_WHEEL_DASHBOARD_PREFIX) :])
    return tuple(sorted(members))


def missing_wheel_dashboard_files(
    wheel_path: str, required: Iterable[str]
) -> Tuple[str, ...]:
    """Return required dashboard paths absent from a built wheel."""
    present = set(wheel_dashboard_member_paths(wheel_path))
    return tuple(sorted(set(required) - present))


def read_dashboard_template(resource_root: str) -> Optional[str]:
    """Read ``index.html`` from the dashboard package directory."""
    verify_dashboard_static_assets(resource_root)
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
    env_root = project_root or resource_root
    enriched = {
        **injected,
        "projectName": resolve_project_display_name(env_root),
    }
    html_path = write_injected_dashboard_html(resource_root, enriched)
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
