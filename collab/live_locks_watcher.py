"""Standalone lock watcher for PyCharm and other IDEs.

Monitors local git status and subscribes to Supabase Realtime for
collaborative file lock notifications. Uses plyer for cross-platform
desktop notifications.

Usage:
    python -m collab.live_locks_watcher [--interval 5] [--timeout 0]
"""

from __future__ import annotations

import atexit
import importlib.util
import json
import logging
import os
import signal
import socket
import sys
import tempfile
import time
import traceback
import webbrowser
from datetime import datetime, timedelta, timezone
from importlib import import_module
from typing import Any, Callable, Optional, Protocol, cast

from dotenv import load_dotenv

from . import agent_identity, platform_probe, safe_subprocess

# NOTE: do NOT import collab-local modules before the runtime root and sys.path
# setup is complete. The import for `logging_config` is moved
# lower in this file (after sys.path.insert and load_dotenv) so that
# the local helper module can be resolved reliably when running from
# the project root or via IDE run configurations.

# Optional colored output (avoid try/except to reduce unreachable-branch lines)
_HAS_COLORAMA = False
try:
    _colorama_spec = importlib.util.find_spec("colorama")
except Exception:
    _colorama_spec = None
if _colorama_spec is not None:
    colorama_mod = import_module("colorama")
    Fore = getattr(colorama_mod, "Fore")
    Style = getattr(colorama_mod, "Style")
    _colorama_init = getattr(colorama_mod, "init", None)
    if callable(_colorama_init):
        try:
            _colorama_init()
        except Exception:
            pass
    _HAS_COLORAMA = True

# Runtime roots
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def _read_clean_env_path(name: str) -> Optional[str]:
    """Return a sanitized path-like environment override."""
    raw = os.getenv(name)
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    if "#" in cleaned:
        cleaned = cleaned.split("#", 1)[0].strip()
    if not cleaned or cleaned.startswith("#"):
        return None
    return cleaned


_project_root_override = _read_clean_env_path("COLLAB_PROJECT_ROOT")
_PROJECT_ROOT = os.path.abspath(_project_root_override or os.getcwd())
_runtime_base = _read_clean_env_path("COLLAB_HOME") or _read_clean_env_path(
    "COLLAB_STATE_DIR"
)
if _runtime_base:
    _COLLAB_ROOT = os.path.abspath(_runtime_base)
else:
    _COLLAB_ROOT = _PROJECT_ROOT
_RESOURCE_ROOT = _THIS_DIR
os.makedirs(_COLLAB_ROOT, exist_ok=True)

# Load environment before reading config variables
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

_setup_collab_logging_obj: Any = None
try:
    from . import logging_config as _logging_config

    _setup_collab_logging_obj = getattr(_logging_config, "setup_collab_logging", None)
except Exception:
    _setup_collab_logging_obj = None


def setup_collab_logging(collab_dir: str) -> None:
    """Dynamically routes logging setup or falls back to basicConfig.

    This proxy func resolves static analyzer type-hinting natively without relying on
    TYPE_CHECKING imports or triggering F811 redefinition lints.
    """
    if _setup_collab_logging_obj is not None:
        _setup_collab_logging_obj(collab_dir)
    else:
        logging.basicConfig(level=logging.INFO)


class _ReconfigurableStream(Protocol):
    """Protocol for streams that support runtime encoding reconfiguration."""

    def reconfigure(self, **kwargs: Any) -> Any: ...


# ---------------------------------------------------------------------------
# UTF-8 encoding (Windows fix — same pattern as validate_code.py / run.py)
# ---------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            cast(_ReconfigurableStream, _stream).reconfigure(
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

if callable(setup_collab_logging):
    setup_collab_logging(collab_dir=_COLLAB_ROOT)
else:
    # Best-effort fallback: configure a simple console logger so runtime
    # still produces useful output even when the collab helper cannot be loaded.
    logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("collab.pycharm_watcher")

# Reduce noisy HTTP client logs (Supabase client uses httpx under the hood)
for _noisy in ("httpx", "httpx._client", "urllib3", "asyncio"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# Type annotation: allow create_client to be None until we bind the real factory.
create_client: Optional[Callable[..., Any]] = None


def _is_installed_package_origin(origin_abs: str) -> bool:
    """Return True when an import origin points to an installed package location."""
    origin_norm = os.path.normcase(origin_abs)
    return (
        f"{os.sep}site-packages{os.sep}" in origin_norm
        or f"{os.sep}dist-packages{os.sep}" in origin_norm
    )


try:
    _supa_spec = importlib.util.find_spec("supabase")
except Exception:
    _supa_spec = None
if _supa_spec is None:
    logger.warning("supabase not installed. Run: pip install supabase")
    # create_client remains None; main() will exit when it detects this.
else:
    # Defensive diagnostic: ensure we are importing the expected package and
    # not a local file under the project which would
    # shadow the installed package. This has caused failures where a test stub
    # or stray file raised a RuntimeError during watcher startup.
    origin = getattr(_supa_spec, "origin", None)
    try:
        if origin:
            origin_abs = os.path.abspath(origin)
            origin_norm = os.path.normcase(origin_abs)
            collab_norm = os.path.normcase(os.path.abspath(_COLLAB_ROOT))
            project_norm = os.path.normcase(os.path.abspath(_PROJECT_ROOT))

            # Block local shadow modules in the project before import.
            in_runtime_collab = origin_norm.startswith(collab_norm)
            in_project_tree = origin_norm.startswith(project_norm)
            is_single_file_shadow = origin_norm.endswith(
                os.path.normcase(f"{os.sep}supabase.py")
            )
            is_package_shadow = f"{os.sep}supabase{os.sep}" in origin_norm
            is_installed_package = _is_installed_package_origin(origin_abs)

            if (in_runtime_collab and not is_installed_package) or (
                in_project_tree
                and not is_installed_package
                and (is_single_file_shadow or is_package_shadow)
            ):
                logger.error(
                    "Detected local module 'supabase' at %s "
                    "which shadows the installed package.",
                    origin_abs,
                )
                logger.error(
                    "Remove or rename this file/folder and restart the watcher."
                )
                sys.exit(1)
    except Exception:
        # Best-effort diagnostics only; continue to import below if something
        # went wrong inspecting the origin.
        pass

    _supa = import_module("supabase")
    create_client = getattr(_supa, "create_client", None)
    if create_client is None:
        logger.error(
            "The installed 'supabase' package does not expose 'create_client'."
        )
        logger.error("Ensure supabase-py is correctly installed and up to date.")

try:
    _ply_spec = importlib.util.find_spec("plyer")
except Exception:
    _ply_spec = None
if _ply_spec is None:
    desktop_notify = None
    logger.warning(
        "plyer not installed — desktop notifications disabled. Run: pip install plyer"
    )
else:
    _ply = import_module("plyer")
    desktop_notify = getattr(_ply, "notification", None)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
# PID file lives at project root unless overridden.
# Tests can override this via COLLAB_PID_FILE env var to avoid interfering with
# the live production watcher.
PID_FILE = agent_identity.resolve_daemon_pid_path(_COLLAB_ROOT, None)
DEVELOPER_ID = None
AGENT_ID: Optional[str] = None
AGENT_LABEL: Optional[str] = None
AGENT_KIND: Optional[str] = None

# Ephemeral developer prefixes enforced in code (not via env) to avoid
# accidental disabling of lock persistence. These accounts (e.g. CI/test)
# will not write locks to the remote DB and are used by automated runners.
EPHEMERAL_PREFIXES = ["test_dev", "ci"]

# Expiry semantics: disabled. The DB RPC ignores time-based expiry and locks
# persist until explicitly released. The watcher does not send an expires_at
# value when acquiring locks.

# Track files currently in conflict (locked by another dev)
_active_conflicts: set[str] = set()
# Track remote locks we already warned about (avoid duplicate notifications)
_warned_remote_locks: set[str] = set()
# Track all remote locks last seen (used to surface any add/remove activity)
_known_remote_locks: set[str] = set()
# Track locks this watcher process has acquired locally (avoid duplicate notices)
_local_owned_locks: set[str] = set()
# Guard to prevent _graceful_shutdown from running more than once
_shutdown_done: bool = False

# URL of the running dashboard server (set in main after _start_dashboard_server).
# Used by interactive conflict menus so users can review all active locks.
_dashboard_url: str | None = None

# Stable session token for this watcher process lifetime.
# Used as lock_token for all locks acquired by this session,
# enabling multi-machine/multi-session detection.
# Initialized at runtime in main() once DEVELOPER_ID is known.
SESSION_TOKEN: str = ""


def _git_capture_text(argv: list[str], *, cwd: str | None = None) -> str:
    """Run an allowlisted git argv and return decoded stdout (empty on failure)."""
    try:
        captured = safe_subprocess.capture(argv, policy="git", cwd=cwd or _PROJECT_ROOT)
        if captured.ok:
            return safe_subprocess.decode_output(captured.stdout).strip()
    except Exception as exc:
        logger.debug("git command failed %s: %s", argv, exc)
    return ""


def _git_capture_status_porcelain() -> str:
    """Return ``git status --porcelain`` stdout, preserving leading whitespace.

    Unlike :func:`_git_capture_text`, this trims only surrounding newlines and never
    performs a full ``.strip()``. Porcelain lines begin with a 2-column status field
    (XY) whose first column is a space for worktree-only changes (e.g. ``" M path"``).
    Stripping the whole blob would remove the leading space of the FIRST line, shifting
    the fixed-width parse in :func:`_parse_git_status_path` and silently dropping the
    first character of that path.
    """
    try:
        captured = safe_subprocess.capture(
            ["git", "status", "--porcelain"], policy="git", cwd=_PROJECT_ROOT
        )
        if captured.ok:
            return safe_subprocess.decode_output(captured.stdout).strip("\r\n")
    except Exception as exc:
        logger.debug("git status --porcelain failed: %s", exc)
    return ""


def _get_developer_id() -> str:
    """Derive developer identity from git config or environment."""
    try:
        name = _git_capture_text(["git", "config", "user.name"])
        if name:
            return name
    except Exception as exc:
        logger.debug("git config user.name unavailable, using env fallback: %s", exc)
    return os.getenv("USERNAME") or os.getenv("USER") or "unknown_user"


def _get_session_token(dev_id: str, agent_id: Optional[str] = None) -> str:
    """Return a stable session token for the current machine, project, user, and agent.

    Must NEVER fall back to a random value — a random token breaks cross-IDE re-adoption
    because it cannot be reconstructed. If derivation fails for any component, use a
    safe fallback value for that component rather than giving up entirely.
    """
    try:
        hostname = socket.gethostname().lower()
    except Exception:
        hostname = "localhost"
    try:
        p_root = os.path.abspath(_PROJECT_ROOT).lower().rstrip("\\/")
    except Exception:
        p_root = _PROJECT_ROOT.lower().rstrip("\\/") if _PROJECT_ROOT else "project"

    seed = agent_identity.session_token_seed(dev_id, agent_id, hostname, p_root)
    return agent_identity.session_token_from_seed(seed)


def _lock_owned_by_us(lock: dict) -> bool:
    """Return True when *lock* belongs to this watcher (developer + agent)."""
    if not DEVELOPER_ID:
        return False
    return agent_identity.lock_owned_by_client(lock, DEVELOPER_ID, AGENT_ID)


def _scope_agent(query: Any) -> Any:
    """Restrict a PostgREST query to the current agent_id."""
    return agent_identity.apply_agent_filter(query, AGENT_ID)


def _acquire_rpc_payload(
    file_path: str,
    branch: Optional[str],
    reason: str,
    lock_token: str,
) -> dict[str, Any]:
    """Build RPC parameters for acquire_lock including agent identity."""
    return {
        "p_file_path": file_path,
        "p_developer_id": DEVELOPER_ID,
        "p_branch_name": branch,
        "p_reason": reason,
        "p_lock_token": lock_token,
        "p_is_ephemeral": _is_ephemeral_dev(DEVELOPER_ID or ""),
        "p_agent_id": AGENT_ID,
        "p_agent_label": AGENT_LABEL,
        "p_origin": agent_identity.resolve_origin(AGENT_ID),
        "p_agent_kind": AGENT_KIND,
    }


def _is_same_machine_token(stored_token: str) -> bool:
    """Return True if stored_token looks like it was generated on this machine.

    Tries multiple plausible developer ID and path variants to account for environment
    differences between IDEs (e.g. VSCode vs PyCharm terminals may yield slightly
    different git config outputs or working directories).
    """
    hostname = socket.gethostname().lower()
    p_root = os.path.abspath(_PROJECT_ROOT).lower().rstrip("\\/")

    # Gather candidate developer IDs to try
    candidates: list[str] = []
    if DEVELOPER_ID:
        candidates.append(str(DEVELOPER_ID).lower())
        # Also try stripped variants in case of whitespace differences
        candidates.append(str(DEVELOPER_ID).strip().lower())

    # Also try git config user.name directly from the current environment
    try:
        git_name = _git_capture_text(["git", "config", "user.name"]).lower()
        if git_name:
            candidates.append(git_name)
    except Exception as exc:
        logger.debug("git config user.name lookup failed in token check: %s", exc)

    # Also try the system username as fallback
    for env_var in ("USERNAME", "USER", "LOGNAME"):
        val = os.getenv(env_var)
        if val:
            candidates.append(val.lower())

    # Also try path variants (with/without trailing slash)
    path_variants = [p_root, p_root.rstrip("/\\"), p_root + "/", p_root + "\\"]

    agent_candidates: list[Optional[str]] = [AGENT_ID]
    if AGENT_ID is not None:
        agent_candidates.append(None)

    seen_seeds: set[str] = set()
    for dev_id in set(candidates):
        for agent in agent_candidates:
            for p in path_variants:
                seed = agent_identity.session_token_seed(dev_id, agent, hostname, p)
                if seed in seen_seeds:
                    continue
                seen_seeds.add(seed)
                token = agent_identity.session_token_from_seed(seed)
                if token == stored_token:
                    logger.debug(
                        "Token matched same-machine variant: dev_id=%r "
                        "agent=%r path=%r",
                        dev_id,
                        agent,
                        p,
                    )
                    return True
    return False


def _scoped_owned_query(query: Any) -> Any:
    """Scope a PostgREST query to this watcher's developer_id and agent_id."""
    return _scope_agent(query.eq("developer_id", DEVELOPER_ID))


def _get_current_branch() -> str:
    """Return the current git branch name."""
    try:
        branch = _git_capture_text(["git", "branch", "--show-current"])
        if branch:
            return branch
    except Exception:
        pass
    return "unknown"


def _parse_git_status_path(line: str) -> str:
    """Extract file path from git status --porcelain line."""
    p = line[3:].strip()
    if " -> " in p:
        p = p.split(" -> ")[-1].strip()
    if p.startswith('"') and p.endswith('"'):
        p = p[1:-1]
    return p


def _normalize_path(path: str, project_root: str) -> str:
    """Normalise a file path to a canonical project-relative Unix-style key.

    - Converts backslashes to forward slashes.
    - Strips a leading ``./`` if present.
        - Canonicalises runtime-relative paths consistently.
    - Uses ``os.path.relpath`` when the path is absolute.
    """
    try:
        if os.path.isabs(path):
            path = os.path.relpath(path, project_root)
        path = path.replace("\\", "/")
        if path.startswith("./"):
            path = path[2:]

        return path
    except Exception:
        return path.replace("\\", "/")


def _should_ignore_path(path: str) -> bool:
    """Return True for paths the watcher should skip."""
    norm = path.replace("\\", "/")
    if "/.git/" in norm or norm.startswith(".git/"):
        return True
    # Ignore runtime instance folders: they are environment artifacts and
    # should not produce collaborative file locks.
    if (
        norm == "instance"
        or norm.startswith("instance/")
        or norm.endswith("/instance")
        or "/instance/" in norm
    ):
        return True
    # Do not ignore runtime-relative project paths here.
    return False


def _color(text: str, color: str) -> str:
    if not _HAS_COLORAMA:
        return text
    return f"{color}{text}{Style.RESET_ALL}"


def _is_ephemeral_dev(dev_id: Optional[str]) -> bool:
    if not dev_id:
        return False
    for p in EPHEMERAL_PREFIXES:
        if dev_id.startswith(p):
            return True
    return False


# (No _compute_expires_at) - watcher intentionally does not compute or send
# expires_at. The DB handles locks as persistent until release.


def _notify(title: str, message: str) -> None:
    """Send a desktop notification if plyer is available."""
    if os.getenv("COLLAB_TEST_MODE") == "1":
        return
    if desktop_notify:
        try:
            desktop_notify.notify(
                title=title,
                message=message,
                app_name="Collab Locks",
                timeout=5,
            )
        except Exception:
            logger.info("[Notification] %s: %s", title, message)
    else:
        logger.info("[Notification] %s: %s", title, message)


def _is_process_alive(pid: int) -> bool:
    """Check if a process is alive."""
    if sys.platform == "win32":
        try:
            import psutil

            return bool(psutil.pid_exists(pid))
        except ImportError:
            return platform_probe.is_pid_alive_tasklist(pid)
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


def _scan_remote_locks(client) -> None:
    """Fetch all active locks and warn about files locked by other developers.

    This runs independently of ``git status`` so the user receives conflict warnings
    *before* saving a file.  Only new remote locks trigger a desktop notification
    (tracked via ``_warned_remote_locks``).
    """
    # Only rebind `_known_remote_locks` in this function; the other sets are
    # mutated in-place (add/discard) so they do not need a `global` declaration.
    global _known_remote_locks

    try:
        res = client.table("file_locks").select("*").execute()
        data = getattr(res, "data", None) or []
    except Exception as exc:
        logger.warning(
            "Remote lock scan failed — conflict warnings may be stale: %s", exc
        )
        return

    # Build set of all remote file paths (normalize separators) and map full lock rows
    current_remote_all: set[str] = set()
    owner_map: dict[str, dict] = {}
    for lock in data:
        owner = lock.get("developer_id", "")
        fp = lock.get("file_path", "")
        if not fp:
            continue
        fp = fp.replace("\\", "/")
        current_remote_all.add(fp)
        owner_map[fp] = lock

        # If this watcher already acquired this lock locally, skip notifications
        if owner == DEVELOPER_ID and fp in _local_owned_locks:
            continue

        # If the lock belongs to the same developer but a different session,
        # surface it as a normal locked message (not a conflict) and include
        # metadata (owner, branch, reason) so terminal output mirrors
        # `python run.py active` or `collab active`.
        if owner == DEVELOPER_ID:
            if fp not in _known_remote_locks:
                br = lock.get("branch_name") or "main"
                reason = lock.get("reason") or "No reason"
                msg = f"🔒 [LOCKED] {fp} — @{owner} (branch: {br}, reason: {reason})"
                logger.debug(_color(msg, Fore.GREEN) if _HAS_COLORAMA else msg)
            continue

        # For locks owned by others: warn once per lock and include branch/reason
        if owner != DEVELOPER_ID and fp not in _warned_remote_locks:
            _warned_remote_locks.add(fp)
            br = lock.get("branch_name") or "main"
            reason = lock.get("reason") or "No reason"
            warn_msg = (
                f"⚠️ REMOTE LOCK: {fp} — @{owner} (branch: {br}, reason: {reason})"
            )
            logger.warning(warn_msg)
            notify_msg = (
                f"{fp} is locked by @{owner}.\n"
                f"branch: {br}\n"
                f"reason: {reason}\n"
                "Coordinate before editing."
            )
            _notify("File Locked", notify_msg)

    # Clear warnings for locks that were released remotely
    released_warned = _warned_remote_locks - current_remote_all
    if released_warned:
        _warned_remote_locks.difference_update(released_warned)
        for fp in released_warned:
            logger.info("✅ Remote lock cleared: %s", fp)

    # Surface add/remove activity for remote locks (excluding those owned
    # by this watcher which we suppressed above). Do not re-report locks
    # that we already logged above (same-developer) — filter them out.
    added = current_remote_all - _known_remote_locks
    removed = _known_remote_locks - current_remote_all
    # Filter out additions that correspond to locks we just acquired locally
    # or locks owned by this developer (they were already logged as LOCKED).
    # Filter out additions that correspond to locks we just acquired locally
    # or locks owned by this developer (they were already logged as LOCKED).
    # NOTE: owner_map stores the full lock dict, so compare the stored
    # developer_id field rather than the dict object itself (bugfix).
    filtered_added = {
        p
        for p in added
        if p not in _local_owned_locks and not _lock_owned_by_us(owner_map.get(p, {}))
    }
    if filtered_added:
        for fp in sorted(filtered_added):
            lk = owner_map.get(fp, {})
            owner = lk.get("developer_id") if lk else "unknown"
            br = lk.get("branch_name") if lk else None
            reason = lk.get("reason") if lk else None
            br = br or "main"
            reason = reason or "No reason"
            # Remote additions should be highlighted so they stand out in the
            # terminal. Use yellow when colorama is available (matches
            # WARNING/CONFLICT color), otherwise plain info text.
            msg = (
                f"🔔 Remote lock added: {fp} — @{owner} "
                f"(branch: {br}, reason: {reason})"
            )
            log_msg = _color(msg, Fore.YELLOW) if _HAS_COLORAMA else msg
            logger.info(log_msg)
    if removed:
        for fp in sorted(removed):
            # If we had recorded it locally, ensure it's removed from that set
            if fp in _local_owned_locks:
                _local_owned_locks.discard(fp)
            # Use the same RELEASED log style as the watcher uses for local releases
            release_msg = f"🔓 [RELEASED] {fp}"
            # Use a distinct color for remote releases so they are visually
            # different from local releases in the terminal output.
            log_msg = _color(release_msg, Fore.CYAN) if _HAS_COLORAMA else release_msg
            logger.info(log_msg)
    _known_remote_locks = current_remote_all


def _process_new_files(client, branch: str, new_files: set[str]) -> None:
    """Process newly modified files: attempt to acquire locks and handle conflicts.

    Extracted from the main loop so unit tests can target error/fallback branches (e.g.
    when modifying the local-owned set raises).
    """
    for fp in new_files:
        try:
            if _is_ephemeral_dev(DEVELOPER_ID):
                msg = f"🔒 [EPHEMERAL] {fp} (not written to DB)"
                logger.info(_color(msg, Fore.CYAN) if _HAS_COLORAMA else msg)
                # skip remote RPC for ephemeral/dev prefixes
                continue

            res = client.rpc(
                "acquire_lock",
                _acquire_rpc_payload(fp, branch, "Auto-Watch", SESSION_TOKEN),
            ).execute()
            data = getattr(res, "data", None) or []
            if isinstance(data, list) and data and data[0].get("status") == "conflict":
                owner = data[0].get("owner", "someone")
                conflict_agent = data[0].get("agent_id")
                owner_display = agent_identity.format_lock_owner(
                    str(owner), conflict_agent, None
                )
                _active_conflicts.add(fp)
                msg = (
                    f"⚠️ CONFLICT: {fp} is locked by {owner_display} -- "
                    "your changes may cause a merge conflict."
                )
                log_msg = _color(msg, Fore.YELLOW) if _HAS_COLORAMA else msg
                logger.warning(log_msg)
                notify_msg = (
                    f"{fp} is locked by {owner_display}.\nCoordinate before committing."
                )
                _notify("Lock Conflict", notify_msg)
            else:
                br_local = branch or "main"
                reason_local = "Auto-Watch"
                msg = (
                    f"🔒 [LOCKED] {fp} — @{DEVELOPER_ID} "
                    f"(branch: {br_local}, reason: {reason_local})"
                )
                log_msg = _color(msg, Fore.GREEN) if _HAS_COLORAMA else msg
                logger.info(log_msg)
                # Track locks this watcher created so remote scans do not
                # report them as 'remote added' later.
                _local_owned_locks.add(fp)
        except Exception:
            # Log full traceback so errors during acquire are visible in errors.log
            logger.exception("Failed to acquire lock for %s", fp)


def _process_releases(client, released: set[str]) -> None:
    """Process local releases for files no longer modified.

    Extracted so tests can simulate exceptions when removing locks from the local-owned
    set without running the entire main loop.
    """
    for fp in released:
        # Was this file in conflict?
        if fp in _active_conflicts:
            _active_conflicts.discard(fp)
            msg = f"✅ Conflict cleared: {fp} (file reverted or resolved)"
            logger.info(_color(msg, Fore.BLUE) if _HAS_COLORAMA else msg)
        else:
            try:
                if _is_ephemeral_dev(DEVELOPER_ID):
                    logger.info("🔓 [EPHEMERAL-RELEASE] %s", fp)
                else:
                    client.table("file_locks").delete().eq("file_path", fp).eq(
                        "developer_id", DEVELOPER_ID
                    ).execute()
                    logger.info(
                        _color(f"🔓 [RELEASED] {fp}", Fore.MAGENTA)
                        if _HAS_COLORAMA
                        else f"[RELEASED] {fp}"
                    )
                    # If we released a lock we held locally, remove it
                    # from the local-owned set so remote scans don't keep it there.
                    _local_owned_locks.discard(fp)
            except Exception:
                # Ensure full traceback is captured in errors.log for diagnostics
                logger.exception("Failed to release lock for %s", fp)


def _resolve_watcher_identity(
    agent_id: Optional[str],
    agent_label: Optional[str],
    agent_kind: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Apply strict attribution to the watcher's resolved identity.

    The background watcher attributes bulk auto-locks to the human developer
    unless a dedicated agent watcher explicitly opts in via
    ``COLLAB_WATCHER_AGENT_ID``. When opting out (the default) the agent identity
    is dropped so every auto-lock is stamped ``origin=human``.
    """
    if os.getenv("COLLAB_WATCHER_AGENT_ID", "").strip():
        return agent_id, agent_label, agent_kind
    return None, None, None


def _release_developer_scope(client, file_path: str) -> bool:
    """Release a lock for *file_path* owned by this developer, ignoring agent id.

    Used by the human watcher to clean up the developer's own AI-agent locks for files
    that are no longer in progress. It never touches other developers' locks. Returns
    True when a delete was issued.
    """
    if _is_ephemeral_dev(DEVELOPER_ID):
        return False
    try:
        client.table("file_locks").delete().eq("file_path", file_path).eq(
            "developer_id", DEVELOPER_ID
        ).execute()
    except Exception:
        logger.exception("Developer-scoped release failed for %s", file_path)
        return False
    _local_owned_locks.discard(file_path)
    return True


def _fetch_dev_other_identity_locks(client) -> dict[str, dict]:
    """Return ``{file_path: lock}`` for same-developer locks NOT owned by us.

    When the watcher runs as the human (the default), these are the developer's
    AI-agent locks. Strict attribution requires the watcher to never fight or
    downgrade them: it skips acquiring such files and only cleans them up once the
    file is no longer in progress (mirrors the ``collab watch`` reconcile).
    """
    try:
        res = (
            client.table("file_locks")
            .select("*")
            .eq("developer_id", DEVELOPER_ID)
            .execute()
        )
        rows = getattr(res, "data", None) or []
    except Exception as exc:
        logger.debug("Failed to fetch developer locks for attribution: %s", exc)
        return {}
    out: dict[str, dict] = {}
    for lock in rows:
        fp = lock.get("file_path", "")
        if fp and not _lock_owned_by_us(lock):
            out[fp] = lock
    return out


def _filter_agent_held_new_files(client, new_files: set[str]) -> set[str]:
    """Drop files already held by this developer's AI agent from the auto-lock set.

    Prevents the human watcher from fighting (and logging false CONFLICTs for) the
    developer's own agent locks. The ``acquire_lock`` RPC lets an agent take over a
    human auto-lock, never the reverse, so the human watcher must simply step aside.
    """
    if not new_files:
        return new_files
    dev_agent_held = set(_fetch_dev_other_identity_locks(client))
    skipped = new_files & dev_agent_held
    if not skipped:
        return new_files
    for fp in sorted(skipped):
        logger.debug("Skipping auto-lock for %s — held by this developer's agent", fp)
    return new_files - dev_agent_held


def _start_dashboard_server() -> str | None:
    """Start a local HTTP server serving the dashboard and return the URL.

    Returns the ``http://127.0.0.1:<port>/...`` URL that terminals render
    as a clickable link, or *None* on failure.
    """
    from collab.dashboard_server import prepare_dashboard_server

    injected = {
        "url": SUPABASE_URL or "",
        "anonKey": SUPABASE_ANON_KEY or "",
        "serviceKey": SUPABASE_SERVICE_ROLE_KEY or None,
        "user": DEVELOPER_ID or "",
    }
    url, _html_path = prepare_dashboard_server(
        _RESOURCE_ROOT,
        injected,
        project_root=_PROJECT_ROOT,
        log_error=logger.warning,
        log_warning=logger.debug,
    )
    return url


def _resolve_lock_diff_base_ref() -> str | None:
    """Return the git ref to diff against for committed-but-not-on-remote work.

    Precedence mirrors ``LockClient._resolve_lock_diff_base_ref``: upstream ``@{u}``,
    then ``COLLAB_LOCK_BASE_REF``, then ``origin/<branch>``, then origin/main|master.
    Returns ``None`` when no suitable base ref exists.
    """
    if _git_capture_text(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
    ):
        return "@{u}"

    override = os.getenv("COLLAB_LOCK_BASE_REF", "").strip()
    if override and _git_capture_text(["git", "rev-parse", "--verify", override]):
        return override

    branch = _git_capture_text(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if branch and branch != "HEAD":
        remote_branch = f"origin/{branch}"
        if _git_capture_text(["git", "rev-parse", "--verify", remote_branch]):
            return remote_branch

    for candidate in ("origin/main", "origin/master"):
        if _git_capture_text(["git", "rev-parse", "--verify", candidate]):
            return candidate
    return None


def _get_modified_and_unpushed_files() -> set[str]:
    """Return the set of files that are 'in progress' for this developer.

    Includes both:
    - Dirty/staged files (git status --porcelain)
    - Committed but not yet on the remote base (git diff <base>..HEAD), where the base
      is the upstream when configured, otherwise origin/<branch> or origin/main.

    This matches the definition used by lock_client.py to ensure both watchers agree on
    which files should be locked.
    """
    result: set[str] = set()

    # Part 1: dirty/staged files
    try:
        out = _git_capture_status_porcelain()
        if out:
            for line in out.splitlines():
                if len(line) > 3:
                    p = _normalize_path(_parse_git_status_path(line), _PROJECT_ROOT)
                    if not _should_ignore_path(p):
                        result.add(p)
    except Exception as exc:
        logger.warning("git status failed in file-change detection: %s", exc)

    # Part 2: committed but not on remote (upstream, origin/<branch>, or origin/main)
    try:
        base_ref = _resolve_lock_diff_base_ref()
        if base_ref:
            range_spec = "@{u}..HEAD" if base_ref == "@{u}" else f"{base_ref}...HEAD"
            diff_out = _git_capture_text(["git", "diff", "--name-only", range_spec])
            if diff_out:
                for line in diff_out.splitlines():
                    p = _normalize_path(line.strip(), _PROJECT_ROOT)
                    if p and not _should_ignore_path(p):
                        result.add(p)
    except Exception:
        # No base ref or diff failed — fall back to status-only. This is safe: we
        # just won't lock committed-but-unpushed files, which beats crashing.
        pass

    return result


def _run_git_status_porcelain() -> set[str]:
    """Compatibility shim used by tests.

    Older tests monkeypatch `_run_git_status_porcelain`. Delegate to the current
    implementation to keep tests backward-compatible.
    """
    return _get_modified_and_unpushed_files()


def _reconcile_on_startup(client) -> None:
    """Reconcile Supabase lock state with local git state at watcher startup.

    Handles the case where the watcher was shut down while files were still dirty. Re-
    adopts valid locks, releases stale ones, acquires new ones, and surfaces post-
    restart conflicts.
    """
    if _is_ephemeral_dev(DEVELOPER_ID):
        logger.info("Ephemeral developer — skipping startup reconciliation.")
        return

    logger.debug("Starting lock reconciliation...")

    # Step A: Fetch existing owned locks from Supabase
    try:
        res = _scoped_owned_query(client.table("file_locks").select("*")).execute()
        existing_locks = getattr(res, "data", None) or []
    except Exception as exc:
        logger.warning("Failed to fetch existing locks during reconciliation: %s", exc)
        return

    # Step B: Get files that are in progress (dirty OR committed-but-unpushed)
    try:
        dirty_files = _run_git_status_porcelain()
    except Exception as exc:
        logger.warning("git status failed during reconciliation: %s", exc)
        return

    # Build lookup maps
    lock_map: dict[str, dict] = {}
    for lock in existing_locks:
        fp = lock.get("file_path", "")
        if fp:
            lock_map[fp] = lock

    locked_paths = set(lock_map.keys())
    branch = _get_current_branch()

    n_readopted = 0
    n_stale_released = 0
    n_newly_locked = 0
    n_conflicts = 0

    # Step C: Process each existing lock owned by this developer
    n_multi_session = 0
    for fp, lock in lock_map.items():
        stored_token = lock.get("lock_token", "")

        if fp in dirty_files:
            # File is still dirty — potential re-adopt
            if stored_token and stored_token != SESSION_TOKEN:
                # Before treating as multi-session, check if the lock was acquired on
                # THIS machine by verifying the stored token matches what this machine
                # would have generated with any reasonable developer ID variation.
                # If so, silently re-adopt (token mismatch is just an IDE
                # environment difference).
                if _is_same_machine_token(stored_token):
                    # Re-adopt silently, but update the token so future checks use the
                    # current session token.
                    try:
                        _scoped_owned_query(
                            client.table("file_locks")
                            .update({"lock_token": SESSION_TOKEN})
                            .eq("file_path", fp)
                        ).execute()
                    except Exception as exc:
                        logger.warning(
                            "Failed to update lock_token for %s — "
                            "future restarts may re-trigger MULTI-SESSION: %s",
                            fp,
                            exc,
                        )
                    _local_owned_locks.add(fp)
                    n_readopted += 1
                    msg = f"🔒 [RESUMED] {fp} — lock re-adopted from this machine"
                    logger.info(_color(msg, Fore.GREEN) if _HAS_COLORAMA else msg)
                else:
                    # Different session token — possible multi-machine scenario
                    n_multi_session += 1
                    _handle_multi_session_lock(client, fp, stored_token)
            else:
                # Same session or no token mismatch — safe to re-adopt
                # Update the lock_token to the current session so future restarts can
                # re-adopt this lock without hitting MULTI-SESSION.
                try:
                    _scoped_owned_query(
                        client.table("file_locks")
                        .update({"lock_token": SESSION_TOKEN})
                        .eq("file_path", fp)
                    ).execute()
                except Exception as exc:
                    logger.warning(
                        "Failed to refresh lock_token for %s — "
                        "future restarts may re-trigger MULTI-SESSION: %s",
                        fp,
                        exc,
                    )
                _local_owned_locks.add(fp)
                n_readopted += 1
                msg = f"🔒 [RESUMED] {fp} — lock re-adopted from this machine"
                logger.info(_color(msg, Fore.GREEN) if _HAS_COLORAMA else msg)
        else:
            # File is clean — stale lock, release it
            try:
                _scoped_owned_query(
                    client.table("file_locks").delete().eq("file_path", fp)
                ).execute()
                n_stale_released += 1
                msg = (
                    f"🔓 [STALE-RELEASED] {fp} — locked but file is "
                    "now clean, releasing"
                )
                logger.info(_color(msg, Fore.MAGENTA) if _HAS_COLORAMA else msg)
            except Exception:
                logger.exception("Failed to release stale lock for %s", fp)

    # Step D: Acquire locks for dirty files that have no existing lock.
    unlocked_dirty = dirty_files - locked_paths

    # Strict attribution: never fight this developer's AI-agent locks. Skip
    # acquiring files already held by the same developer under another identity,
    # and clean up such locks for files that are no longer in progress.
    dev_other = _fetch_dev_other_identity_locks(client)
    if dev_other:
        unlocked_dirty -= set(dev_other)
        for fp in sorted(set(dev_other) - dirty_files):
            if _release_developer_scope(client, fp):
                n_stale_released += 1
                msg = (
                    f"🔓 [STALE-RELEASED] {fp} — agent lock for clean file, "
                    "releasing"
                )
                logger.info(_color(msg, Fore.MAGENTA) if _HAS_COLORAMA else msg)

    for fp in sorted(unlocked_dirty):
        if _should_ignore_path(fp):
            continue
        try:
            res = client.rpc(
                "acquire_lock",
                _acquire_rpc_payload(fp, branch, "Auto-Watch (resumed)", SESSION_TOKEN),
            ).execute()
            data = getattr(res, "data", None) or []
            if isinstance(data, list) and data and data[0].get("status") == "conflict":
                n_conflicts += 1
                _handle_post_restart_conflict(client, fp, data[0])
            else:
                _local_owned_locks.add(fp)
                n_newly_locked += 1
                msg = f"🔒 [LOCKED] {fp} — acquired lock for dirty file at startup"
                logger.debug(_color(msg, Fore.GREEN) if _HAS_COLORAMA else msg)
        except Exception:
            logger.exception("Failed to acquire lock for %s during reconciliation", fp)

    # Step E: Log reconciliation summary
    summary = (
        f"Startup reconciliation complete.\n"
        f"  Re-adopted: {n_readopted} lock(s)\n"
        f"  Stale released: {n_stale_released} lock(s)\n"
        f"  Newly locked: {n_newly_locked} file(s)\n"
        f"  Conflicts: {n_conflicts} file(s)"
    )
    if n_conflicts > 0:
        summary += " — review required"
    if n_multi_session > 0:
        summary += (
            f"\n  Multi-session: {n_multi_session} lock(s) "
            "left under different session tokens"
        )
        logger.info(summary)
        info_msg = (
            f"ℹ️  {n_multi_session} lock(s) left under different session tokens. "
            "Run 'collab active' to review."
        )
        logger.info(_color(info_msg, Fore.CYAN) if _HAS_COLORAMA else info_msg)
    else:
        logger.info(summary)

    # Single batched notification for all startup reconciliation activity
    notification_title = "Collab Locks — Startup Summary"
    notification_msg = (
        f"Re-adopted: {n_readopted} lock(s)\n"
        f"Stale released: {n_stale_released} lock(s)\n"
        f"Newly locked: {n_newly_locked} file(s)\n"
        f"Conflicts: {n_conflicts} file(s)"
    )
    if n_multi_session > 0:
        notification_msg += f"\nMulti-session: {n_multi_session} lock(s)"
    if n_conflicts > 0:
        notification_msg += " — review required"
    _notify(notification_title, notification_msg)


def _handle_multi_session_lock(client, fp: str, stored_token: str) -> None:
    """Handle a lock held by the same developer but from a different session.

    Interactive mode prompts the developer to decide; non-interactive defaults to
    leaving the lock untouched (safe default — the other session may still be active).
    """
    if sys.stdin.isatty():
        print(f"\n⚠️  [MULTI-SESSION] {fp}")
        print(
            f"    Lock held by @{DEVELOPER_ID} from a different session "
            f"(token: {stored_token[:8]}...)"
        )
        print("    Are you running the watcher on multiple machines?\n")
        print("    [1] Re-adopt this lock for the current session")
        print("    [2] Leave it — another machine may still be active")
        print("    [3] Release it — the other session is no longer active")
        try:
            choice = input("    Enter choice [1/2/3]: ").strip()
        except (EOFError, KeyboardInterrupt):
            choice = "2"

        if choice == "1":
            try:
                _scoped_owned_query(
                    client.table("file_locks")
                    .update({"lock_token": SESSION_TOKEN})
                    .eq("file_path", fp)
                ).execute()
            except Exception:
                logger.exception("Failed to update lock_token for %s", fp)
            _local_owned_locks.add(fp)
            msg = f"🔒 [RESUMED] {fp} — lock re-adopted from different session"
            logger.info(_color(msg, Fore.GREEN) if _HAS_COLORAMA else msg)
            _notify(
                "Lock Resumed",
                f"{fp} — lock re-adopted from different session",
            )
        elif choice == "3":
            try:
                _scoped_owned_query(
                    client.table("file_locks").delete().eq("file_path", fp)
                ).execute()
            except Exception:
                logger.exception("Failed to release lock for %s", fp)
            msg = f"🔓 [RELEASED] {fp} — released per user request"
            logger.info(_color(msg, Fore.MAGENTA) if _HAS_COLORAMA else msg)
        else:
            msg = (
                f"⚠️ [MULTI-SESSION] {fp} — left to other session "
                f"(token: {stored_token[:8]}...)"
            )
            logger.warning(msg)
    else:
        # Non-interactive: default to leave (option 2 — safe default)
        msg = (
            f"⚠️ [MULTI-SESSION] {fp} — token mismatch "
            f"(stored: {stored_token[:8]}..., current: {SESSION_TOKEN[:8]}...). "
            f"Could not confirm same-machine origin. Lock left untouched — "
            f"use 'collab release-all' if this is stale."
        )
        logger.warning(msg)


def _handle_post_restart_conflict(client, fp: str, lock_data: dict) -> None:
    """Handle a post-restart conflict: dirty locally but locked by another dev.

    Interactive mode presents options; non-interactive defaults to continuing with the
    file added to the conflict tracking set.
    """
    owner = lock_data.get("owner", "someone")
    lock_branch = lock_data.get("branch", "unknown")
    lock_reason = lock_data.get("reason", "N/A")

    conflict_msg = (
        f"⚠️ [POST-RESTART CONFLICT] {fp} — dirty locally, "
        f"locked by @{owner}.\n"
        "   Your local edits may conflict. Manual review required."
    )
    logger.warning(conflict_msg)
    _notify("Post-restart conflict", f"{fp} locked by @{owner}")

    if sys.stdin.isatty():
        fp_display = fp[:50]
        owner_display = f"@{owner}"[:48]
        branch_display = str(lock_branch)[:50]
        reason_display = str(lock_reason)[:50]
        print(f"\n╔{'═' * 62}╗")
        print(f"║  ⚠️  POST-RESTART CONFLICT DETECTED{' ' * 26}║")
        print(f"║{' ' * 63}║")
        print(f"║  File    : {fp_display:<51}║")
        print(f"║  Locked by: {owner_display:<50}║")
        print(f"║  Branch  : {branch_display:<51}║")
        print(f"║  Reason  : {reason_display:<51}║")
        print(f"║{' ' * 63}║")
        print(f"║  This file has local uncommitted edits AND is now{' ' * 12}║")
        print(f"║  locked by another developer.{' ' * 33}║")
        print(f"║{' ' * 63}║")
        print(f"║  Options:{' ' * 53}║")
        print(f"║  [1] Continue — keep my edits, add to conflicts{' ' * 14}║")
        print(f"║  [2] Show diff — run `git diff`{' ' * 31}║")
        print(f"║  [3] Open dashboard — view all active locks{' ' * 18}║")
        print(f"║  [4] Abort watcher startup{' ' * 36}║")
        print(f"╚{'═' * 62}╝")

        while True:
            try:
                choice = input("  Enter choice [1/2/3/4]: ").strip()
            except (EOFError, KeyboardInterrupt):
                choice = "1"

            if choice == "2":
                try:
                    diff_args = ["git", "diff", fp]
                    captured = safe_subprocess.capture(
                        diff_args, policy="git", cwd=_PROJECT_ROOT
                    )
                    diff_out = safe_subprocess.decode_output(captured.stdout)
                    print(f"\n--- git diff {fp} ---")
                    print(diff_out or "(no diff output)")
                    print("---\n")
                except Exception as exc:
                    print(f"  (git diff failed: {exc})")
                continue
            elif choice == "3":
                url = _dashboard_url or _start_dashboard_server()
                if url:
                    print(f"  Opening dashboard: {url}")
                    try:
                        webbrowser.open(url)
                    except Exception as exc:
                        print(f"  (Could not open browser: {exc})")
                else:
                    print("  Dashboard unavailable. Run: collab dashboard")
                continue
            elif choice == "4":
                logger.info("User chose to abort watcher startup.")
                _graceful_shutdown()
                sys.exit(1)
            else:
                break

    _active_conflicts.add(fp)


def _graceful_shutdown() -> None:
    """Release only clean-file locks; keep dirty-file locks in Supabase.

    A lock is released if and only if its file is no longer dirty in
    ``git status --porcelain``. If git status fails, falls back to
    releasing all locks (legacy behavior) with a WARNING.

    Guarded so it runs at most once, even when invoked from multiple shutdown
    paths (signal handler, finally block, atexit).
    """
    global _shutdown_done
    if _shutdown_done or os.getenv("COLLAB_TEST_MODE") == "1":
        return
    _shutdown_done = True

    dev_id = DEVELOPER_ID
    if dev_id and SUPABASE_URL and SUPABASE_ANON_KEY and create_client is not None:
        try:
            client = cast(Callable[..., Any], create_client)(
                SUPABASE_URL, SUPABASE_ANON_KEY
            )

            # Determine which files are still in progress
            # (dirty OR committed-but-unpushed)
            still_dirty: set[str] = set()
            git_failed = False
            try:
                still_dirty = _run_git_status_porcelain()
            except Exception as exc:
                git_failed = True
                logger.warning(
                    "WARNING: git status failed during shutdown (%s). "
                    "Falling back to release-all.",
                    exc,
                )

            if git_failed:
                # Fallback: release all locks for this developer + agent only
                _scope_agent(
                    client.table("file_locks").delete().eq("developer_id", dev_id)
                ).execute()
                logger.info("✅ Released all locks during shutdown (fallback).")
            else:
                # Smart release: only release locks for clean files
                n_kept = 0
                n_released = 0

                # Release clean files from _local_owned_locks
                for fp in list(_local_owned_locks):
                    if fp in still_dirty:
                        n_kept += 1
                        msg = f"🔒 [KEPT] {fp} — still has local edits, lock preserved"
                        logger.debug(_color(msg, Fore.GREEN) if _HAS_COLORAMA else msg)
                    else:
                        try:
                            _scope_agent(
                                client.table("file_locks")
                                .delete()
                                .eq("file_path", fp)
                                .eq("developer_id", dev_id)
                            ).execute()
                            n_released += 1
                            msg = f"🔓 [RELEASED] {fp}"
                            logger.info(
                                _color(msg, Fore.MAGENTA) if _HAS_COLORAMA else msg
                            )
                        except Exception:
                            logger.exception(
                                "Failed to release lock for %s during shutdown",
                                fp,
                            )

                # If _local_owned_locks was empty (e.g. fresh startup),
                # query Supabase for any locks we might hold
                if not _local_owned_locks:
                    try:
                        res = _scope_agent(
                            client.table("file_locks")
                            .select("file_path")
                            .eq("developer_id", dev_id)
                        ).execute()
                        db_locks = [
                            r.get("file_path", "")
                            for r in (getattr(res, "data", None) or [])
                        ]
                        for fp in db_locks:
                            if fp and fp not in still_dirty:
                                _scope_agent(
                                    client.table("file_locks")
                                    .delete()
                                    .eq("file_path", fp)
                                    .eq("developer_id", dev_id)
                                ).execute()
                                n_released += 1
                                msg = f"🔓 [RELEASED] {fp}"
                                logger.info(
                                    _color(msg, Fore.MAGENTA) if _HAS_COLORAMA else msg
                                )
                            elif fp:
                                n_kept += 1
                                msg = (
                                    f"🔒 [KEPT] {fp} — still has "
                                    "local edits, lock preserved"
                                )
                                logger.debug(
                                    _color(msg, Fore.GREEN) if _HAS_COLORAMA else msg
                                )
                    except Exception:
                        logger.exception(
                            "Failed to query existing locks during shutdown"
                        )

                logger.info(
                    "Shutdown complete. Preserved %d lock(s), released %d lock(s).",
                    n_kept,
                    n_released,
                )
        except Exception:
            logger.exception("Error releasing locks during shutdown")
    for _attempt in range(3):
        try:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
                logger.info("Removed PID file: %s", PID_FILE)
            break
        except OSError as _e:
            if _attempt < 2:
                time.sleep(0.1)
            else:
                logger.warning("Could not remove PID file after 3 attempts: %s", _e)


def _write_pid_file(pid: int, parent_pid: int | None = None) -> None:
    """Atomically write JSON metadata to the PID file for daemon-status checks.

    Keeps process metadata to aid verification and diagnostics. Writes a JSON object
    containing pid, started_at (UTC ISO), cmdline and cwd.
    """
    meta = {
        "pid": pid,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "entrypoint": "pycharm-watcher",
        "cmdline": " ".join([sys.executable] + sys.argv),
        "cwd": os.getcwd(),
    }
    if parent_pid:
        meta["parent_pid"] = parent_pid
    pid_dir = os.path.dirname(PID_FILE) or "."
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir=pid_dir, suffix=".pid.tmp", encoding="utf-8"
        )
        tmp.write(json.dumps(meta))
        tmp.flush()
        tmp.close()
        os.replace(tmp.name, PID_FILE)
        logger.info("Wrote PID metadata to %s (PID: %d)", PID_FILE, pid)
    except Exception as exc:
        logger.warning("Failed to write PID metadata to %s: %s", PID_FILE, exc)
        if tmp is not None:
            try:
                os.unlink(tmp.name)
            except Exception as cleanup_exc:
                logger.debug("PID temp-file cleanup failed: %s", cleanup_exc)


def _get_process_info_local(pid: int) -> tuple[str | None, int | None]:
    """Fetch process name and parent PID via platform probe on Windows."""
    if sys.platform != "win32":
        return None, None
    try:
        return platform_probe.wmic_process_name_and_ppid(pid)
    except Exception as exc:
        logger.debug("wmic process-info lookup for pid=%d failed: %s", pid, exc)
    return None, None


def _get_parent_ide_pid_local() -> int | None:
    """Identify the process that owns this session.

    Prioritizes walking up the process tree to find a known IDE window. Falls back to
    the direct parent shell (terminal) to ensure closure on tab/window exit.
    """
    ide_names = {
        "antigravity.exe",
        "pycharm64.exe",
        "pycharm.exe",
        "code.exe",
        "idea64.exe",
        "idea.exe",
        "language_server_windows_x64.exe",
        "node.exe",  # VSCode extension host
    }

    try:
        current_pid: Optional[int] = os.getpid()
        visited: set[int] = set()
        while current_pid and current_pid not in visited:
            visited.add(current_pid)
            # Type guard: current_pid is int here
            name, ppid = _get_process_info_local(current_pid)
            if name and name.lower() in ide_names:
                # Special case: if we found node.exe (VSCode extension host),
                # try to go up
                # to find the actual Code.exe window process.
                if name.lower() == "node.exe" and ppid:
                    next_name, next_ppid = _get_process_info_local(ppid)
                    if next_name and "code" in next_name.lower():
                        logger.debug("Tying to VSCode IDE (PID: %d)", ppid)
                        return ppid
                # Special case: if we found the terminal host, try to go up one
                # more to find the actual IDE window process.
                if name.lower() == "language_server_windows_x64.exe" and ppid:
                    next_name, next_ppid = _get_process_info_local(ppid)
                    if next_name and "antigravity" in next_name.lower():
                        logger.debug("Tying to Antigravity IDE (PID: %d)", ppid)
                        return ppid

                logger.debug(
                    "Tying to IDE via process name: %s (PID: %d)", name, current_pid
                )
                return current_pid

            if not ppid or ppid == current_pid:
                break
            current_pid = ppid
    except Exception as e:
        logger.debug("Ancestor search failed: %s", e)

    # Fallback 1: Environment Variables
    vspid = os.getenv("VSCODE_PID")
    if vspid and vspid.isdigit():
        vspid_int = int(vspid)
        if _is_process_alive(vspid_int):
            return vspid_int

    if os.getenv("PYCHARM_HOSTED") == "1":
        return os.getppid()

    # Fallback 2: Direct Parent Shell
    ppid = os.getppid()
    if ppid > 0:
        return ppid

    return None


def _get_cmdline_for_pid_local(pid: int) -> Optional[str]:
    """Local helper to fetch a process command-line (psutil preferred, then platform-
    specific fallbacks)."""
    try:
        import psutil

        try:
            p = psutil.Process(pid)
            cmd = p.cmdline()
            if isinstance(cmd, (list, tuple)):
                return " ".join(cmd)
            return str(cmd)
        except Exception as exc:
            logger.debug("psutil.Process(%d).cmdline() failed: %s", pid, exc)
    except Exception:
        logger.debug("psutil not available for cmdline lookup (pid=%d)", pid)

    return platform_probe.get_cmdline(pid)


def _cmdline_matches_watcher_local(cmdline: Optional[str]) -> bool:
    if not cmdline:
        return False
    s = cmdline.lower()
    return (
        "live_locks_watcher" in s
        or "live_locks" in s
        or ("lock_client.py" in s and "watch" in s)
        or ("collab.core.lock_client" in s and "watch" in s)
    )


def _shorten_process_label(
    label: Optional[str], max_tokens: int = 4, max_len: int = 80
) -> Optional[str]:
    """Return a short, human-friendly label for a process/entrypoint string.

    - Collapse long filesystem paths to their basenames
    - Keep only the first `max_tokens` tokens and append ' ...' if truncated
    - Ensure the returned string is not longer than `max_len` (truncates with ellipsis)
    """
    if not label:
        return None
    try:
        parts = label.split()
        short_parts: list[str] = []
        for p in parts[:max_tokens]:
            # If it's a path-like token, show only the basename for readability
            if ("/" in p) or ("\\" in p):
                try:
                    b = os.path.basename(p)
                    if b:
                        short_parts.append(b)
                        continue
                except Exception:
                    pass
            # Normalize common python executable mention
            low = p.lower()
            if low.endswith("python") or low.endswith("python.exe") or "pythonw" in low:
                short_parts.append("python")
            else:
                short_parts.append(p)

        short = " ".join(short_parts)
        if len(parts) > max_tokens:
            short = short + " ..."
        if len(short) > max_len:
            short = short[: max_len - 3].rstrip() + "..."
        return short
    except Exception:
        # Best-effort: return the original label if shortening fails
        return label if label else None


def _existing_watcher_running() -> tuple[bool, int | None, str | None, str | None]:
    """Check for an existing watcher process via PID file and return (is_running, pid,
    cmdline, entrypoint).

    If no existing PID file or cannot verify, returns (False, None, None, None).
    """
    try:
        if not os.path.exists(PID_FILE):
            return (False, None, None, None)
        with open(PID_FILE, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
        if not raw:
            return (False, None, None, None)
        pid = None
        cmdline = None
        entrypoint = None
        obj = None
        if raw.startswith("{"):
            try:
                obj = json.loads(raw)
                pid = obj.get("pid")
                cmdline = obj.get("cmdline")
                entrypoint = obj.get("entrypoint")
            except Exception:
                return (False, None, None, None)
        else:
            try:
                pid = int(raw)
            except Exception:
                return (False, None, None, None)

        if not pid:
            return (False, None, None, None)

        # If the PID file contains JSON metadata with a recorded cmdline or
        # entrypoint, prefer to verify via cmdline matching first. This allows
        # test suites to populate the metadata and stub `_get_cmdline_for_pid_local`
        # without requiring the test process to actually own the PID.
        if isinstance(obj, dict):
            try:
                real_cmd = _get_cmdline_for_pid_local(pid)
                if real_cmd:
                    cmdline = real_cmd
                # If stored metadata or the resolved commandline looks like a
                # watcher, accept it as running (tests rely on this behavior).
                if _cmdline_matches_watcher_local(cmdline) or (
                    entrypoint and _cmdline_matches_watcher_local(entrypoint)
                ):
                    return (True, pid, cmdline, entrypoint)
            except Exception as exc:
                logger.debug("Cmdline check for pid=%d failed: %s", pid, exc)

        # Always verify the process is actually alive before trusting any cmdline data.
        if not _is_process_alive(pid):
            # Stale PID file — clean it up proactively so the next startup is fast.
            try:
                if os.path.exists(PID_FILE):
                    if isinstance(obj, dict):
                        stored_parent = obj.get("parent_pid")
                        stored_entry = obj.get("entrypoint")
                        started_at = obj.get("started_at")
                    else:
                        stored_parent = stored_entry = started_at = None

                    os.remove(PID_FILE)
                    logger.warning(
                        "Stale PID file detected: PID %d is no longer running. "
                        "Removing stale file and starting fresh.",
                        pid,
                    )
                    if stored_parent:
                        parent_alive = _is_process_alive(stored_parent)
                        logger.info(
                            "Previous watcher details: parent_pid=%d (alive=%s), "
                            "entrypoint=%s, started=%s",
                            stored_parent,
                            parent_alive,
                            stored_entry or "unknown",
                            started_at or "unknown",
                        )
                        if not parent_alive:
                            logger.info(
                                "Root cause: Parent IDE (PID %d) terminated. "
                                "It did not clean up the watcher.",
                                stored_parent,
                            )
            except OSError:
                pass
            return (False, pid, None, None)

        # Belt-and-suspenders: if the metadata records a parent_pid and that parent
        # is dead, the watcher is orphaned. Treat it as not running.
        if isinstance(obj, dict):
            stored_parent_pid = obj.get("parent_pid")
            if stored_parent_pid and not _is_process_alive(stored_parent_pid):
                logger.debug(
                    "Watcher PID %d is alive but its parent PID %d is dead — "
                    "treating as orphaned",
                    pid,
                    stored_parent_pid,
                )
                return (False, pid, cmdline, entrypoint)

        real_cmd = _get_cmdline_for_pid_local(pid)
        if real_cmd:
            cmdline = real_cmd
        if _cmdline_matches_watcher_local(cmdline):
            return (True, pid, cmdline, entrypoint)
        return (False, pid, cmdline, entrypoint)
    except Exception:
        return (False, None, None, None)


# ---------------------------------------------------------------------------
# Main Watcher Loop
# ---------------------------------------------------------------------------
def main() -> None:
    """Run the PyCharm live lock watcher."""
    global DEVELOPER_ID

    import argparse

    parser = argparse.ArgumentParser(description="PyCharm Live Lock Watcher")
    parser.add_argument(
        "--interval", type=int, default=5, help="Poll interval (seconds)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="Idle timeout in minutes (0 = disabled)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging (prints heartbeat and debug details)",
    )
    parser.add_argument(
        "--parent-pid", type=int, help="Tie watcher lifecycle to this parent PID"
    )
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        logger.error(
            "Missing SUPABASE_URL or SUPABASE_ANON_KEY in .env.\n"
            "See .env.example for setup."
        )
        sys.exit(1)

    # Normalize developer ID aggressively to avoid token divergence between IDEs
    global DEVELOPER_ID, AGENT_ID, AGENT_LABEL, AGENT_KIND, SESSION_TOKEN, PID_FILE
    DEVELOPER_ID = _get_developer_id().strip()
    AGENT_ID = agent_identity.resolve_agent_id(_COLLAB_ROOT)
    AGENT_LABEL = agent_identity.resolve_agent_label()
    AGENT_KIND = agent_identity.resolve_agent_kind(agent_id=AGENT_ID)
    # Strict attribution (parity with ``collab watch``): the background watcher —
    # including this PyCharm entrypoint — attributes bulk git-status auto-locks to
    # the HUMAN developer, never to an AI agent, even when launched from a terminal
    # that exported COLLAB_AGENT_ID/COLLAB_AGENT_MODE. A dedicated agent watcher
    # can opt in explicitly via COLLAB_WATCHER_AGENT_ID.
    AGENT_ID, AGENT_LABEL, AGENT_KIND = _resolve_watcher_identity(
        AGENT_ID, AGENT_LABEL, AGENT_KIND
    )
    if not os.getenv("COLLAB_PID_FILE"):
        PID_FILE = agent_identity.resolve_daemon_pid_path(_COLLAB_ROOT, AGENT_ID)

    SESSION_TOKEN = _get_session_token(DEVELOPER_ID, AGENT_ID)

    # Log session token (truncated) for debugging cross-IDE token divergence
    logger.debug(
        "Session token: %s... (dev=%s, agent=%s, host=%s)",
        SESSION_TOKEN[:8],
        DEVELOPER_ID,
        AGENT_ID or "(human)",
        socket.gethostname(),
    )

    # Optional debug mode: enable verbose logging for diagnostics
    debug_mode = args.debug or os.getenv("COLLAB_DEBUG", "0").lower() in (
        "1",
        "true",
        "yes",
    )
    if debug_mode:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)
        logger.info("Debug logging enabled")

    # Write PID file (unified with lock_client daemon)
    # Startup guard: avoid starting a second watcher if one is already active
    running, existing_pid, existing_cmd, existing_entry = _existing_watcher_running()
    if running:
        # When running under tests, the helper sets a test-local PID file
        # (named with prefix 'pytest_collab_'). In that case, avoid treating
        # the presence of the PID file as a real external watcher and allow
        # the test to drive main() behavior. This keeps test runs isolated
        # from developer machines that may have a real watcher running.
        if isinstance(PID_FILE, str) and "pytest_collab_" in PID_FILE:
            logger.debug(
                "Detected test-local PID file; ignoring existing-watcher guard"
            )
        else:
            # Prefer a stable human-facing label when the PID metadata contains an
            # entrypoint. Map well-known entrypoint tokens to a short, descriptive
            # process name so output is consistent for operators.
            label = None
            if existing_entry:
                e = str(existing_entry).lower()
                if e in ("lock-daemon", "lock-client"):
                    # Prefer an explicit, familiar invocation instead of a short token
                    label = "python lock_client.py"
                elif e == "pycharm-watcher":
                    label = "python -m collab.live_locks_watcher"
                else:
                    label = _shorten_process_label(existing_entry)
            elif existing_cmd:
                label = _shorten_process_label(existing_cmd)

            if label:
                first_line = f"Watcher already running (PID: {existing_pid}) — {label}."
            else:
                first_line = f"Watcher already running (PID: {existing_pid})."

            # Use multi-line info so the IDE/terminal shows each action on its own line
            msg = (
                first_line
                + "\nTo check status: collab daemon-status\n"
                + "To stop: collab daemon-stop"
            )
            logger.info(msg)
            # Avoid printing a duplicate concise line to the console — the logger
            # output is sufficient and prevents double messages in IDE Run windows.
            sys.exit(0)

    try:
        # Initialise parent PID from CLI, environment, or process tree
        parent_pid = args.parent_pid or _get_parent_ide_pid_local()

        if args.parent_pid:
            logger.debug("Tied to parent PID via CLI argument: %d", parent_pid)
        elif parent_pid:
            logger.debug("Tied to parent PID via IDE detection: %d", parent_pid)
        else:
            logger.debug("No IDE owner identified. Running in persistent mode.")

        # Record our PID and metadata so status checks work
        _write_pid_file(os.getpid(), parent_pid=parent_pid)
    except Exception:
        # Best-effort: if writing metadata fails, fall back to plain PID integer
        try:
            with open(PID_FILE, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass

    # Register cleanup
    if os.getenv("COLLAB_TEST_MODE") != "1":
        atexit.register(_graceful_shutdown)

    def _signal_handler(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        _graceful_shutdown()
        sys.exit(0)

    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Create Supabase client
    if create_client is None:
        logger.error(
            "Supabase client factory is not available. Ensure supabase is installed."
        )
        sys.exit(1)
    # static analyzers may still treat create_client as Optional; cast for
    # their sake so they recognize the value is callable beyond this point.
    client = cast(Callable[..., Any], create_client)(SUPABASE_URL, SUPABASE_ANON_KEY)

    # Start local dashboard server for a clickable URL
    dashboard_url = _start_dashboard_server()
    global _dashboard_url
    _dashboard_url = dashboard_url

    logger.info("=" * 60)
    logger.info("Collab Locks -- PyCharm Watcher")
    logger.info("Developer: %s", DEVELOPER_ID)
    timeout_label = f"{args.timeout}m" if args.timeout > 0 else "disabled"
    logger.info("Interval: %ds | Timeout: %s", args.interval, timeout_label)
    if args.timeout > 0:
        logger.warning(
            "⚠️  --timeout is deprecated. With lock-persistence semantics,\n"
            "    idle timeout means locks are kept alive with no active watcher.\n"
            "    Consider removing --timeout to run the watcher indefinitely,\n"
            "    or use `collab release-all` to manually clean up."
        )
    if dashboard_url:
        logger.info("Dashboard: %s", dashboard_url)
    else:
        logger.info("Dashboard: collab dashboard")
    logger.info("=" * 60)

    last_modified: set = set()
    last_change_time = datetime.now()
    last_remote_scan = datetime.now()
    last_heartbeat = datetime.now()
    last_parent_check = datetime.now()

    # Initial remote lock scan
    _scan_remote_locks(client)

    # Startup reconciliation: sync Supabase lock state with local git
    _reconcile_on_startup(client)

    # Initialize last_modified from current git state (post-reconciliation)
    # so the first polling iteration does not re-process already-locked files.
    try:
        last_modified = _run_git_status_porcelain()
    except Exception as exc:
        logger.warning(
            "Initial git-status snapshot failed — "
            "first poll may lock unexpected files: %s",
            exc,
        )

    try:
        while True:
            # Remote lock scan every 30 seconds (independent of git status)
            now = datetime.now()
            # Periodic heartbeat (helps diagnose silent exits)
            if (now - last_heartbeat).total_seconds() > 60:
                last_heartbeat = now
                logger.debug("heartbeat pid=%d", os.getpid())

            # Parent process liveness check every 5 seconds (snappy termination)
            if parent_pid and (now - last_parent_check).total_seconds() > 5:
                last_parent_check = now
                if not _is_process_alive(parent_pid):
                    logger.info(
                        "Parent process (PID: %d) is dead. Shutting down...", parent_pid
                    )
                    break

            if (now - last_remote_scan).total_seconds() > 30:
                last_remote_scan = now
                _scan_remote_locks(client)

            # Get files that are in progress (dirty OR committed-but-unpushed)
            try:
                current_modified = _run_git_status_porcelain()
            except Exception as e:
                logger.error("Failed to get modified files: %s", e)
                time.sleep(args.interval)
                continue

            if current_modified != last_modified:
                last_change_time = datetime.now()
                branch = _get_current_branch()

                # New files to lock
                new_files = current_modified - last_modified
                # Strict attribution: never auto-lock (as the human) a file that
                # this developer's AI agent already holds — that would create
                # false CONFLICT noise and fight the agent.
                new_files = _filter_agent_held_new_files(client, new_files)
                # Delegate acquire/release logic to helper functions to allow
                # targeted unit tests to exercise error/fallback branches.
                _process_new_files(client, branch, new_files)

                # Files no longer modified locally
                released = last_modified - current_modified
                _process_releases(client, released)

                last_modified = current_modified
            else:
                # Idle timeout check
                idle = datetime.now() - last_change_time
                if args.timeout > 0 and idle > timedelta(minutes=args.timeout):
                    # Check which locks will be preserved
                    # (dirty OR committed-but-unpushed)
                    try:
                        still_dirty = _run_git_status_porcelain()
                        kept_locks = _local_owned_locks & still_dirty
                    except Exception:
                        kept_locks = set(_local_owned_locks)
                    if kept_locks:
                        logger.warning(
                            "⚠️  IDLE TIMEOUT REACHED (%dm of inactivity)\n"
                            "    The watcher is stopping, but %d lock(s) are "
                            "being PRESERVED in Supabase\n"
                            "    because the following files still have local "
                            "edits:\n%s\n"
                            "    These files will remain locked until the "
                            "watcher is restarted.\n"
                            "    Restart with: python -m collab.live_locks_watcher",
                            args.timeout,
                            len(kept_locks),
                            "\n".join(f"      - {f}" for f in sorted(kept_locks)),
                        )
                        for kf in kept_locks:
                            _notify(
                                "Watcher idle timeout",
                                f"{kf} lock preserved",
                            )
                    else:
                        logger.info(
                            "Timed out after %dm inactivity.",
                            args.timeout,
                        )
                    break

            time.sleep(args.interval)

    except KeyboardInterrupt:
        logger.info("Stopped by user.")
    except Exception as e:
        logger.error("Watcher loop error: %s", e, exc_info=True)
        _notify("Watcher Error", f"Loop error: {e}")
    finally:
        _graceful_shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # top-level catch to ensure operator sees failures
        tb = traceback.format_exc()
        # Log to the standard logs/ directory via the structured logger.
        logger.error("Unhandled exception in live_locks_watcher: %s\n%s", exc, tb)

        # Print short, operator-friendly message to stderr so it appears in
        # the IDE/terminal immediately, pointing to the full log for details.
        print(
            "Unhandled error in watcher. See logs/collab.log",
            file=sys.stderr,
        )

        # Attempt graceful cleanup, then exit non-zero
        try:
            _graceful_shutdown()
        except Exception as cleanup_exc:
            logger.warning("Graceful-shutdown fallback failed: %s", cleanup_exc)
        sys.exit(1)
