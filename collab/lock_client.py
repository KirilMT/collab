"""Supabase-backed collaborative file lock client.

Provides atomic lock acquisition, release, and daemon management for preventing merge
conflicts in multi-developer workflows.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import re
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from dotenv import load_dotenv

from . import agent_identity, overlap, path_filter, platform_probe, safe_subprocess
from .env_secrets import effective_env_secret
from .errors import (
    ConfigurationError,
    DaemonStartError,
    LockServiceUnavailableError,
    ParentMonitorError,
    PidParseError,
    SubprocessSecurityError,
    WatcherDiscoveryError,
)

# CLI entrypoint (collab = "collab.lock_client:main" in pyproject.toml).
# Main orchestration lives in collab/main.py; re-exported here for console scripts.
from .main import _run_cli, main

__all__ = ["LockClient", "main", "_run_cli"]


def _safe_now() -> datetime:
    """Return the current datetime using the (possibly monkeypatched) ``datetime``
    symbol imported into this module.

    Tests patch ``datetime`` with a fake class/instance and some replacement objects may
    present a ``now`` attribute that behaves oddly when bound. This helper attempts to
    call the patched ``now`` safely and falls back to the real datetime on failure.
    """
    try:
        return datetime.now()
    except TypeError:
        # If the patched datetime is an instance, try to fetch the class-level
        # attribute and call it as an unbound function (avoids implicit binding)
        try:
            cls = datetime if isinstance(datetime, type) else datetime.__class__
            now_attr = getattr(cls, "now", None)
            if callable(now_attr):
                # Call the class-level now and ensure we return a real datetime
                try:
                    res = now_attr()
                except TypeError:
                    # If calling now as an unbound function failed, continue to fallback
                    res = None
                # Use the real stdlib datetime type for isinstance checks to avoid
                # confusion when the module-level `datetime` has been monkeypatched
                from datetime import datetime as _real_dt

                if isinstance(res, _real_dt):
                    return res
        except Exception:
            pass
        # Last-resort: use the real datetime type from the stdlib
        from datetime import datetime as _real_dt

        return _real_dt.now()


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
logger = logging.getLogger("collab.lock_client")


def _emit_log_resilient(log: logging.Logger, level: int, msg: str, *args: Any) -> None:
    """Emit a log record while tolerating interpreter-shutdown handler states.

    Daemon threads can outlive normal application flow, and by the time they log, some
    handlers may already have closed streams. Python's logging module reports those as
    noisy "Logging error" tracebacks. This helper keeps normal logging behavior for
    healthy handlers, skips closed streams, and suppresses handler-level failures.
    """
    try:
        if log.disabled or level < log.getEffectiveLevel():
            return

        record = log.makeRecord(
            log.name,
            level,
            __file__,
            0,
            msg,
            args,
            None,
            None,
            None,
        )

        current: Optional[logging.Logger] = log
        emitted = False
        while current is not None:
            for handler in current.handlers:
                try:
                    if record.levelno < handler.level:
                        continue
                    if not handler.filter(record):
                        continue
                    stream = getattr(handler, "stream", None)
                    if stream is not None and getattr(stream, "closed", False):
                        continue
                    handler.handle(record)
                    emitted = True
                except Exception:
                    # Best-effort: never let late-shutdown logging fail noisily.
                    continue

            if not current.propagate:
                break
            current = current.parent

        if not emitted:
            # Last fallback for debugging sessions with no available handlers.
            try:
                if sys.stderr is not None and not sys.stderr.closed:
                    sys.stderr.write(f"{record.levelname}: {record.getMessage()}\n")
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def _read_clean_env_path(name: str) -> Optional[str]:
    """Return a sanitized path-like environment override.

    Treat empty values and comment-only values as unset. Inline comments are stripped so
    values like ``X=path  # comment`` remain usable.
    """
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


def _resolve_project_root() -> str:
    """Resolve project root for runtime operations.

    Priority:
    1) COLLAB_PROJECT_ROOT env var
    2) Current working directory
    """
    override = _read_clean_env_path("COLLAB_PROJECT_ROOT")
    if override:
        return os.path.abspath(override)
    return os.path.abspath(os.getcwd())


# Resolve project root first — used by state-dir helpers below
_PROJECT_ROOT = _resolve_project_root()


def _is_test_mode() -> bool:
    """Return True when running under pytest/test harness context."""
    return (
        os.getenv("COLLAB_TEST_MODE") == "1"
        or os.getenv("TESTING") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
    )


def _get_state_dir() -> str:
    """Return a per-workspace state directory outside the repo for non-essential runtime
    markers (heartbeat, shutdown marker, startup summary). This avoids creating
    transient files inside the workspace tree.

    The location can be overridden with the `COLLAB_STATE_DIR` env var for testing or
    custom setups.
    """
    state_dir = _read_clean_env_path("COLLAB_STATE_DIR")
    if state_dir:
        try:
            os.makedirs(state_dir, exist_ok=True)
        except Exception:
            pass
        return os.path.abspath(str(state_dir))

    try:
        import hashlib as _hashlib

        # Normalize slashes and case for cross-runtime consistency (CLI vs Extension)
        norm_root = _PROJECT_ROOT.replace("/", "\\").lower().rstrip("\\")
        h = _hashlib.sha1(norm_root.encode("utf-8"), usedforsecurity=False).hexdigest()[
            :8
        ]
        base_tmp = tempfile.gettempdir()
        # Use a collab-specific namespace for runtime state dirs.
        current_prefix = "collab_runtime"
        if _is_test_mode():
            sd = os.path.join(base_tmp, f"{current_prefix}_{h}_test_{os.getpid()}")
        else:
            sd = os.path.join(base_tmp, f"{current_prefix}_{h}")

        try:
            os.makedirs(sd, exist_ok=True)
        except Exception:
            pass
        return os.path.abspath(str(sd))
    except Exception:
        # Fallback: prefer the configured runtime root if available (keeps
        # backwards-compatible test and import-time semantics), otherwise
        # fall back to the project root or current working directory.
        try:
            fallback = globals().get("_COLLAB_ROOT")
            if fallback:
                return os.path.abspath(str(fallback))
        except Exception:
            pass
        try:
            return os.path.abspath(_PROJECT_ROOT)
        except Exception:
            return os.getcwd()


def _state_dir_for_root(root: str) -> str:
    """Return the runtime state directory for an ARBITRARY project root.

    Mirrors the non-test hashing used by :func:`_get_state_dir` so a specific worktree's
    isolated namespace (heartbeat, PID, stop-request, keeper) can be resolved from any
    process. Used by ``worktree-unregister`` (#168) to target a single worktree's
    watcher without disturbing watchers in other worktrees.

    ``COLLAB_STATE_DIR`` precedence is intentional and required for correctness: when it
    is set, the running watcher's :func:`_get_state_dir` uses that exact directory, so
    resolution here MUST use the same one or teardown would target the wrong namespace.
    Setting a single ``COLLAB_STATE_DIR`` therefore collapses all roots onto one shared
    namespace (only one daemon at a time) — it is a deliberate test/custom-deployment
    knob and is mutually exclusive with per-worktree isolation. In normal use it is
    unset and every worktree hashes its own root into a distinct, isolated directory, so
    this is never a multi-worktree hazard in production.
    """
    state_dir = _read_clean_env_path("COLLAB_STATE_DIR")
    if state_dir:
        return os.path.abspath(str(state_dir))
    import hashlib as _hashlib

    # Match _get_state_dir(): normalize slashes/case for cross-runtime parity.
    norm_root = os.path.abspath(root).replace("/", "\\").lower().rstrip("\\")
    h = _hashlib.sha1(norm_root.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return os.path.abspath(os.path.join(tempfile.gettempdir(), f"collab_runtime_{h}"))


def _resolve_runtime_root(project_root: str) -> str:
    """Resolve persistent runtime root for the current project.

    Preference order:
      1. `COLLAB_HOME` env override
      2. Fallback to project root
    """
    home_override = _read_clean_env_path("COLLAB_HOME")
    if home_override:
        return os.path.abspath(home_override)

    # Fallback to state dir for backwards compatibility in tests/custom setups
    state_override = _read_clean_env_path("COLLAB_STATE_DIR")
    if state_override:
        return os.path.abspath(state_override)

    return project_root


_COLLAB_ROOT = _resolve_runtime_root(_PROJECT_ROOT)
_RESOURCE_ROOT = _THIS_DIR
os.makedirs(_COLLAB_ROOT, exist_ok=True)


def _state_path(name: str) -> str:
    # Ensure we use the normalized state directory
    return os.path.join(_get_state_dir(), name)


def _resolve_executable_path(name: str) -> Optional[str]:
    """Return an absolute executable path from PATH (delegates to safe_subprocess)."""
    return safe_subprocess.resolve_executable(name)


# Load .env from the project root (never modify .env)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
LOCK_STRICT = os.getenv("LOCK_STRICT", "0") == "1"


def _effective_service_role_key() -> Optional[str]:
    """Service role key for API calls, or None when placeholder/unset."""
    return effective_env_secret(SUPABASE_SERVICE_ROLE_KEY)


def _effective_anon_key() -> Optional[str]:
    """Anon key for API calls, or None when placeholder/unset."""
    return effective_env_secret(SUPABASE_ANON_KEY)


# Expiry semantics: this project enforces NO automatic expiry. Locks persist
# until released explicitly. The DB RPC ignores time-based expiry; the
# expires_at column is kept for audit but is not used for automatic
# replacement. Clients do not send an expires_at value.

# Developer id prefixes treated as ephemeral (do not persist locks to the DB).
# Enforced in code (not configurable via .env) to avoid accidental skips.
EPHEMERAL_PREFIXES = ["test_dev", "ci"]

# (Intentionally no repo-level toggle) Do not expose a runtime flag to
# enable/disable runtime-path locking.

# PID file lives in the state dir unless overridden. When an agent id is active,
# each agent gets its own PID file so multiple watchers can coexist.
# Tests can override via COLLAB_PID_FILE env var.
PID_FILE = agent_identity.resolve_daemon_pid_path(_get_state_dir(), None)


def _refresh_pid_file(agent_id: Optional[str]) -> None:
    """Update module-level PID_FILE for the resolved agent (unless env override set)."""
    global PID_FILE
    if os.getenv("COLLAB_PID_FILE"):
        return
    PID_FILE = agent_identity.resolve_daemon_pid_path(_get_state_dir(), agent_id)


# Maximum retry attempts for network errors
MAX_RETRIES = 3

# ---------------------------------------------------------------------------
# Heartbeat grace constants (single coherent policy block — see
# _heartbeat_should_shutdown() and watch()).
# ---------------------------------------------------------------------------
# Startup grace: allow the IDE extension time to create the heartbeat file
# after spawning the watcher. Configurable via env for slow machines or
# unusually slow extension-host startup.
_HEARTBEAT_STARTUP_GRACE_SECONDS = float(
    os.getenv("COLLAB_HEARTBEAT_STARTUP_GRACE_SECONDS", "3.0")
)
# Soft extra: one-time additional grace window when the parent IDE process
# is still alive but the heartbeat mtime is stale. Tolerates brief extension-
# host hiccups (file-system delays, quick reloads) without shutting down.
_HEARTBEAT_SOFT_EXTRA_SECONDS = 5.0
# Dashboard watcher-health uses PID file mtime as a liveness signal; refresh it
# periodically because the JSON metadata is written only once at startup.
_PID_FILE_HEARTBEAT_INTERVAL_SECONDS = float(
    os.getenv("COLLAB_PID_FILE_HEARTBEAT_INTERVAL_SECONDS", "10.0")
)

# Grace period used when daemon-start provides a heartbeat file (no extension
# owner).  Generous enough to tolerate transient stalls while still reaping
# within a reasonable window after the session ends.
_DAEMON_HEARTBEAT_GRACE_SECONDS = int(
    os.getenv("COLLAB_DAEMON_HEARTBEAT_GRACE_SECONDS", "30")
)

# Period between worktree-validity checks in the watcher loop (Layer 2
# defense-in-depth).  Configurable via env; set to 0 to disable.
_WORKTREE_VALIDITY_CHECK_INTERVAL_SECONDS = float(
    os.getenv("COLLAB_WORKTREE_VALIDITY_CHECK_INTERVAL_SECONDS", "60.0")
)

# Inline script for the heartbeat-keeper subprocess spawned by daemon-start.
# Touches a heartbeat file every 2 s while COLLAB_HEARTBEAT_SESSION_PID is
# alive.  The session PID is resolved at daemon-start time (per-window Cursor
# utility / extension-host process, or COLLAB_SESSION_PID override) — NOT the
# shared VSCODE_PID and NOT the ephemeral setup-script console.
_HEARTBEAT_KEEPER_SCRIPT = r"""
import os, sys, time

hb = os.getenv("COLLAB_HEARTBEAT_KEEPER_FILE")
session_raw = os.getenv("COLLAB_HEARTBEAT_SESSION_PID")
if not hb or not session_raw:
    sys.exit(1)
try:
    session_pid = int(session_raw)
except Exception:
    sys.exit(1)
if session_pid <= 0:
    sys.exit(1)


def _pid_alive(pid):
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        code = wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return int(code.value) == STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


try:
    os.makedirs(os.path.dirname(hb), exist_ok=True)
except Exception:
    pass

while _pid_alive(session_pid):
    try:
        with open(hb, "w", encoding="utf-8") as f:
            f.write(str(time.time()) + "\n")
    except Exception:
        pass
    time.sleep(2)
"""

# Process names skipped when resolving the per-window session owner PID.
_SESSION_CHAIN_SKIP_EXE_NAMES = frozenset(
    {
        "windowsterminal.exe",
        "conhost.exe",
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "bash.exe",
        "sh.exe",
        "zsh.exe",
        "fish.exe",
        "python.exe",
        "pythonw.exe",
        "collab.exe",
        "collab-watcher.exe",
    }
)

# How long daemon-start waits for the keeper to create the heartbeat file.
_HEARTBEAT_KEEPER_CONFIRM_SECONDS = float(
    os.getenv("COLLAB_HEARTBEAT_KEEPER_CONFIRM_SECONDS", "2.0")
)


def _min_auto_lock_hold_seconds() -> int:
    """Minimum seconds an auto-watch lock must be held before auto-release.

    Configurable via ``COLLAB_MIN_AUTO_LOCK_HOLD_SECONDS`` (default 300 = 5 min).
    Wrapped in a callable so tests can monkeypatch without import-time coupling.
    """
    return int(os.getenv("COLLAB_MIN_AUTO_LOCK_HOLD_SECONDS", "300"))


# ---------------------------------------------------------------------------
# Supabase client (lazy import)
# ---------------------------------------------------------------------------
_supabase_create_client = None


def _is_installed_package_origin(origin_abs: str) -> bool:
    """Return True when an import origin points to an installed package location."""
    origin_norm = os.path.normcase(origin_abs)
    return (
        f"{os.sep}site-packages{os.sep}" in origin_norm
        or f"{os.sep}dist-packages{os.sep}" in origin_norm
    )


def _get_create_client():
    """Lazy-load the supabase create_client function."""
    global _supabase_create_client
    if _supabase_create_client is None:
        # First: if tests or other harnesses have injected a fake module into
        # ``sys.modules['supabase']``, prefer that module. Tests commonly
        # monkeypatch sys.modules rather than relying on import machinery, and
        # failing here causes fragile tests. If the injected module exposes a
        # ``create_client`` symbol it will be used. If the injected module has
        # a __file__ located inside the repository, treat that as accidental
        # local shadowing and fail fast with a clear message.
        supa_mod = sys.modules.get("supabase")
        if supa_mod is not None:
            # Honour any test-level import-time failures: if the import
            # machinery (builtins.__import__) has been monkeypatched to raise
            # ImportError for 'supabase' we should respect that and exit so
            # tests that simulate missing packages behave deterministically.
            try:
                __import__("supabase")
            except ImportError:
                logger.error(
                    "supabase-py is not installed (import failed). "
                    "Install it with: pip install supabase"
                )
                sys.exit(1)

            origin = None
            try:
                spec = getattr(supa_mod, "__spec__", None)
                spec_origin = getattr(spec, "origin", None) if spec else None
                origin = spec_origin or getattr(supa_mod, "__file__", None)
            except Exception:
                origin = None

            try:
                if origin:
                    origin_abs = os.path.abspath(origin)
                    is_repo_shadow = origin_abs.startswith(
                        _COLLAB_ROOT
                    ) and not _is_installed_package_origin(origin_abs)
                    if is_repo_shadow:
                        logger.error(
                            "Detected local module 'supabase' at %s "
                            "which shadows the installed package.",
                            origin_abs,
                        )
                        logger.error(
                            "Remove or rename this file/folder and re-run "
                            "tests / watcher."
                        )
                        sys.exit(1)
            except Exception:
                # Defensive: any unexpected error inspecting the fake module
                # should not break tests; fall through and attempt to use it.
                pass

            create_fn = getattr(supa_mod, "create_client", None)
            if create_fn is None:
                logger.error(
                    "The 'supabase' module present in sys.modules "
                    "does not expose 'create_client'."
                )
                logger.error(
                    "If this is a test, ensure your fake module "
                    "provides 'create_client'."
                )
                sys.exit(1)

            _supabase_create_client = create_fn
            return _supabase_create_client

        # No preloaded module in sys.modules — fall back to importing the
        # real package. If it is missing, fail loudly with a helpful message.
        try:
            # This will call the import machinery and raise ImportError if
            # the package is not available or tests have patched __import__.
            from supabase import create_client as create_fn
        except ImportError:
            logger.error(
                "supabase-py is not installed. Install it with: pip install supabase\n"
                "See .env.example for required environment variables."
            )
            sys.exit(1)

        # After a successful import, detect if the resolved module originates
        # from the repository (e.g. supabase.py) which would indicate
        # an accidental shadowing of the real package.
        supa_mod = sys.modules.get("supabase")
        spec_origin = None
        if supa_mod is not None:
            spec = getattr(supa_mod, "__spec__", None)
            spec_origin = getattr(spec, "origin", None) if spec else None

        if supa_mod is not None:
            origin = spec_origin or getattr(supa_mod, "__file__", None)
        else:
            origin = None

        try:
            if origin:
                origin_abs = os.path.abspath(origin)
                is_repo_shadow = origin_abs.startswith(
                    _COLLAB_ROOT
                ) and not _is_installed_package_origin(origin_abs)
                if is_repo_shadow:
                    logger.error(
                        "Detected local module 'supabase' at %s "
                        "which shadows the installed package.",
                        origin_abs,
                    )
                    logger.error(
                        "Remove or rename this file/folder and re-run tests / watcher."
                    )
                    sys.exit(1)
        except Exception:
            pass

        _supabase_create_client = create_fn
    return _supabase_create_client


@contextmanager
def _quiet_console_loggers(names: Optional[List[str]] = None):
    """Context manager to temporarily silence noisy console loggers while preserving
    `collab` file-based logging. Useful for clean CLI output.

    - Sets specified logger names to WARNING level.
        - Temporarily disables propagation from the `collab` logger to the root
            console handler so `collab.*` records are still written to `logs/`.
    """
    if names is None:
        names = ["httpx", "httpcore", "urllib3", "postgrest", "supabase"]
    old_levels: Dict[str, int] = {}
    for n in names:
        lg = logging.getLogger(n)
        old_levels[n] = lg.level
        try:
            lg.setLevel(logging.WARNING)
        except Exception:
            pass

    collab_logger = logging.getLogger("collab")
    old_propagate = getattr(collab_logger, "propagate", True)
    try:
        # Prevent collab.* logs from propagating to the root console handler
        # while still allowing file handlers attached to the collab logger to
        # record messages.
        collab_logger.propagate = False
        yield
    finally:
        for n, lvl in old_levels.items():
            try:
                logging.getLogger(n).setLevel(lvl)
            except Exception:
                pass
        try:
            collab_logger.propagate = old_propagate
        except Exception:
            pass


def _validate_credentials() -> None:
    """Validate that Supabase credentials are present, exit with clear error if not."""
    if not SUPABASE_URL or not _effective_anon_key():
        logger.error(
            "Missing Supabase credentials.\n"
            "  SUPABASE_URL=%s\n"
            "  SUPABASE_ANON_KEY=%s\n\n"
            "Please copy .env.example to .env at the project root\n"
            "and fill in your Supabase project credentials.\n"
            "See README.md for setup instructions.",
            SUPABASE_URL or "(not set)",
            "(set)" if _effective_anon_key() else "(not set)",
        )
        sys.exit(1)


def _retry_on_network_error(func, *args, **kwargs) -> Any:
    """Execute func with exponential backoff retry on network errors."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            # Only retry on network-related errors
            if any(
                kw in err_str
                for kw in (
                    "timeout",
                    "connection",
                    "connect",
                    "network",
                    "unreachable",
                    "refused",
                    "getaddrinfo",
                )
            ):
                wait = 2**attempt
                logger.debug(
                    "Network error (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    wait,
                    e,
                )
                time.sleep(wait)
            else:
                raise
    # Log the permanent failure with full traceback so operators can diagnose
    # why retries exhausted (e.g. DNS resolution errors).
    logger.exception("Permanent network failure after %d attempts", MAX_RETRIES)
    raise last_error  # type: ignore[misc]


# Lock service: short TCP probe so CLI/watcher fail fast when Supabase is down.
_LOCK_SERVICE_PROBE_TIMEOUT_S = 5.0

# Git discovery (per watcher tick): default 30s balances responsiveness with large
# Windows repos (logs showed 30s was tight). Override for slow disks:
#   COLLAB_GIT_CAPTURE_TIMEOUT_S=45
_GIT_STATUS_TIMEOUT_S = float(os.getenv("COLLAB_GIT_CAPTURE_TIMEOUT_S", "30"))
_GIT_REF_TIMEOUT_S = min(_GIT_STATUS_TIMEOUT_S, 15.0)


def _current_supabase_url() -> str:
    """Return the active Supabase URL (env at call time, not import time)."""
    return os.getenv("SUPABASE_URL") or SUPABASE_URL or ""


def _lock_service_hostname() -> str:
    """Return the hostname from SUPABASE_URL, or empty when unset/invalid."""
    url = _current_supabase_url()
    if not url:
        return ""
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _ensure_lock_service_reachable() -> None:
    """Raise when the configured Supabase host cannot be resolved or is missing."""
    # Unit/integration tests use fake hosts and mock RPC; connectivity is not real.
    if os.getenv("COLLAB_TEST_MODE") == "1":
        return

    url = _current_supabase_url()
    anon = _effective_anon_key()
    if not url or not anon:
        raise ConfigurationError(
            "Supabase credentials are not configured",
            detail="Set SUPABASE_URL and SUPABASE_ANON_KEY in .env",
        )
    host = _lock_service_hostname()
    if not host:
        raise ConfigurationError(
            "SUPABASE_URL is invalid",
            detail=f"Could not parse hostname from {url!r}",
        )
    try:
        with socket.create_connection(
            (host, 443),
            timeout=_LOCK_SERVICE_PROBE_TIMEOUT_S,
        ):
            pass
    except OSError as exc:
        raise LockServiceUnavailableError(
            f"Cannot reach lock service host {host!r}",
            detail=(
                f"{exc}. Verify SUPABASE_URL, network/VPN, and that the Supabase "
                "project is active."
            ),
        ) from exc


def _is_sandbox_lock_service() -> bool:
    """True when CLI runs against the integration-test localhost Supabase stub."""
    if os.getenv("COLLAB_TEST_MODE") != "1":
        return False
    return _lock_service_hostname() in {"localhost", "127.0.0.1"}


def _is_lock_service_error(exc: BaseException) -> bool:
    """Return True when an exception indicates the remote lock service is
    unreachable."""
    if isinstance(exc, LockServiceUnavailableError):
        return True
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "getaddrinfo",
            "gaierror",
            "name or service not known",
            "network is unreachable",
            "network error",
            "connection refused",
            "connection error",
            "connecterror",
            "failed to establish",
            "actively refused",
            "temporary failure in name resolution",
            "11001",
            "10061",
        )
    )


# ---------------------------------------------------------------------------
# Supabase Lock Client
# ---------------------------------------------------------------------------
class LockClient:
    """Supabase-backed file lock client.

    All lock operations use the Supabase REST API with the official Python client. Lock
    acquisition uses the atomic ``acquire_lock`` RPC function defined in ``schema.sql``
    to prevent race conditions.
    """

    def __init__(
        self,
        developer_id: Optional[str] = None,
        local_only: bool = False,
        agent_id: Optional[str] = None,
        agent_label: Optional[str] = None,
        agent_kind: Optional[str] = None,
        agent_mode: Optional[bool] = None,
    ) -> None:
        from typing import cast

        self.local_only = local_only
        self.developer_id = (
            developer_id or os.getenv("COLLAB_DEVELOPER_ID") or self._get_git_username()
        )
        state_dir = _get_state_dir()
        self.agent_id = agent_identity.resolve_agent_id(
            state_dir,
            explicit_agent_id=agent_id,
            agent_mode=agent_mode,
        )
        self.agent_label = agent_identity.resolve_agent_label(
            explicit_label=agent_label,
        )
        # Runtime family (cursor/claude-code/...) for friendly display only, and
        # the authoritative attribution origin (human vs agent).
        self.agent_kind = agent_identity.resolve_agent_kind(
            explicit_kind=agent_kind,
            agent_id=self.agent_id,
        )
        self.origin = agent_identity.resolve_origin(self.agent_id)
        _refresh_pid_file(self.agent_id)
        self._client: Optional[Any] = None
        self._branch_name: Optional[str] = None
        self._session_token: Optional[str] = None
        self._parent_pid: Optional[int] = None
        self._heartbeat_file: Optional[str] = None
        self._heartbeat_grace_seconds: int = 10
        # One-time soft-skip flag to tolerate a short heartbeat hiccup
        self._heartbeat_soft_skipped: bool = False
        # OS-level parent monitor status (Windows)
        self._parent_monitor_started: bool = False
        self._parent_monitor_handle: Optional[int] = None
        self._parent_monitor_thread: Optional[threading.Thread] = None
        self._is_admin: bool = bool(_effective_service_role_key())
        # Treat certain developer ids as ephemeral (e.g. CI/test accounts) so
        # they do not persist locks to the DB. This list is enforced in-code to
        # avoid relying on environment configuration being correct.
        self._ephemeral_developer_ids: set[str] = set(
            # ephemeral (CI/test prefixes)
        )
        self._is_ephemeral: bool = False
        # Track when the auto-watcher acquired each lock so we can enforce a
        # minimum hold time and avoid rapid acquire/release cycles.
        self._lock_acquired_at: Dict[str, datetime] = {}
        if self.developer_id:
            try:
                for p in EPHEMERAL_PREFIXES:
                    if self.developer_id.startswith(p):
                        self._is_ephemeral = True
                        break
            except Exception:
                # Defensive: if developer_id is not a str for any reason
                self._is_ephemeral = False

        if not self.local_only and not getattr(self, "_is_ephemeral", False):
            _validate_credentials()
            key = _effective_service_role_key() or _effective_anon_key()
            create_client = cast(Any, _get_create_client())
            self._client = cast(Any, create_client(SUPABASE_URL, key))

    def _normalize_file_path(self, file_path: str) -> str:
        """Normalize a file path to a project-root relative Unix-style path.

        This ensures that paths stored in Supabase match the paths produced by "git
        status --porcelain" (which are relative paths with forward slashes).
        """
        try:
            # If an absolute path was provided, make it relative to project root
            if os.path.isabs(file_path):
                rel = os.path.relpath(file_path, _PROJECT_ROOT)
            else:
                rel = file_path
            # Normalise separators to forward-slash for consistency in the DB
            rel = rel.replace("\\", "/")
            if rel.startswith("./"):
                rel = rel[2:]

            return rel
        except Exception:
            return file_path.replace("\\", "/")

    @property
    def is_admin(self) -> bool:
        """Return True if this client has admin privileges (service role key)."""
        return self._is_admin

    def _require_client(self) -> Any:
        """Return the Supabase client or raise ConfigurationError."""
        client = self._client
        if client is None:
            raise ConfigurationError("Supabase client not initialized")
        return client

    def _get_session_token(self) -> str:
        """Return a stable session token for this machine, project and user.

        Must NEVER fall back to a random value — a random token breaks cross-IDE re-
        adoption because it cannot be reconstructed. If derivation fails for any
        component, use a safe fallback value for that component rather than giving up
        entirely.
        """
        try:
            dev_id = (
                str(self.developer_id).strip().lower()
                if self.developer_id
                else "unknown"
            )
        except Exception:
            dev_id = "unknown"
        try:
            hostname = socket.gethostname().lower()
        except Exception:
            hostname = "localhost"
        try:
            p_root = os.path.abspath(_PROJECT_ROOT).lower().rstrip("\\/")
        except Exception:
            p_root = _PROJECT_ROOT.lower().rstrip("\\/") if _PROJECT_ROOT else "project"

        seed = agent_identity.session_token_seed(
            dev_id, self.agent_id, hostname, p_root
        )
        return agent_identity.session_token_from_seed(seed)

    def _lock_owned_by_me(self, lock: Dict) -> bool:
        """Return True when *lock* is owned by this client (human + agent)."""
        return agent_identity.lock_owned_by_client(
            lock, self.developer_id, self.agent_id
        )

    def _apply_agent_scope(self, query: Any) -> Any:
        """Restrict a PostgREST query to this client's agent_id."""
        return agent_identity.apply_agent_filter(query, self.agent_id)

    def _format_owner(
        self,
        developer_id: str,
        lock_agent_id: Optional[str] = None,
        lock_agent_label: Optional[str] = None,
        lock_agent_kind: Optional[str] = None,
    ) -> str:
        return agent_identity.format_lock_owner(
            developer_id, lock_agent_id, lock_agent_label, lock_agent_kind
        )

    def _is_same_machine_token(self, stored_token: str) -> bool:
        """Return True if stored_token looks like it was generated on this machine.

        Tries multiple plausible developer ID and path variants to account for
        environment differences between IDEs (e.g. VSCode vs PyCharm terminals may yield
        slightly different git config outputs or working directories).
        """
        hostname = socket.gethostname().lower()
        p_root = os.path.abspath(_PROJECT_ROOT).lower().rstrip("\\/")

        # Gather candidate developer IDs to try
        candidates: list[str] = []
        if self.developer_id:
            candidates.append(str(self.developer_id).lower())
            # Also try stripped variants in case of whitespace differences
            candidates.append(str(self.developer_id).strip().lower())

        # Also try git config user.name directly from the current environment
        try:
            git_capture = safe_subprocess.capture(
                ["git", "config", "user.name"], policy="git"
            )
            if git_capture.ok:
                git_name = (
                    safe_subprocess.decode_output(git_capture.stdout).strip().lower()
                )
                if git_name:
                    candidates.append(git_name)
        except (SubprocessSecurityError, Exception):
            pass

        # Also try the system username as fallback
        for env_var in ("USERNAME", "USER", "LOGNAME"):
            val = os.getenv(env_var)
            if val:
                candidates.append(val.lower())

        # Also try path variants (with/without trailing slash)
        path_variants = [p_root, p_root.rstrip("/\\"), p_root + "/", p_root + "\\"]

        agent_candidates: list[Optional[str]] = [self.agent_id]
        if self.agent_id is not None:
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

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _get_git_username() -> str:
        """Derive developer identity from git config or environment."""
        try:
            captured = safe_subprocess.capture(
                ["git", "config", "user.name"], policy="git"
            )
            if captured.ok:
                name = safe_subprocess.decode_output(captured.stdout).strip()
                if name:
                    return name
        except (SubprocessSecurityError, Exception):
            pass
        return os.getenv("USERNAME") or os.getenv("USER") or "unknown_user"

    @staticmethod
    def _get_current_branch() -> Optional[str]:
        """Return the current git branch name, or None."""
        try:
            captured = safe_subprocess.capture(
                ["git", "branch", "--show-current"],
                policy="git",
                cwd=_PROJECT_ROOT,
            )
            if captured.ok:
                branch = safe_subprocess.decode_output(captured.stdout).strip()
                return branch or None
        except (SubprocessSecurityError, Exception):
            pass
        return None

    # ------------------------------------------------------------------
    # Response parsing (handles varying supabase-py response shapes)
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_response(res) -> Tuple[Optional[int], Any, Any]:
        """Normalize supabase-py response into (status, data, error)."""
        status = getattr(res, "status_code", None) or getattr(res, "status", None)
        data = getattr(res, "data", None)
        error = getattr(res, "error", None)
        if isinstance(res, dict):
            status = status or res.get("status") or res.get("status_code")
            data = data if data is not None else res.get("data")
            error = error or res.get("error")
        return (status, data, error)

    # ------------------------------------------------------------------
    # Remote lock scanning (like pycharm_watcher)
    # ------------------------------------------------------------------
    def _scan_remote_locks(self) -> None:
        """Fetch all active locks and log those held by this developer.

        This runs before reconciliation so the user sees [LOCKED] messages for existing
        locks, matching pycharm_watcher behavior.
        """
        try:
            client = self._require_client()
            res = _retry_on_network_error(
                lambda: client.table("file_locks").select("*").execute()
            )
            _, data, _ = self._parse_response(res)
            if not data:
                return

            for lock in data:
                owner = lock.get("developer_id", "")
                fp = lock.get("file_path", "")
                if not fp:
                    continue

                # Only log locks owned by this developer + agent
                if owner == self.developer_id and agent_identity.agent_ids_match(
                    lock.get("agent_id"), self.agent_id
                ):
                    br = lock.get("branch_name") or "main"
                    reason = lock.get("reason") or "Auto-Watch Sync"
                    logger.debug(
                        "🔒 [LOCKED] %s — @%s (branch: %s, reason: %s)",
                        fp,
                        owner,
                        br,
                        reason,
                    )
        except Exception as exc:
            logger.debug("Remote lock scan failed: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def acquire(
        self,
        file_path: str,
        reason: Optional[str] = None,
        branch_name: Optional[str] = None,
        expires_minutes: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Acquire a lock on file_path using the atomic RPC function.

        Returns (success: bool, message: str).
        """
        # Local validation — accept either project-relative or absolute paths.
        full_path = (
            file_path
            if os.path.isabs(file_path)
            else os.path.join(_PROJECT_ROOT, file_path)
        )
        if not os.path.exists(full_path):
            # Deleted files can still be "in progress" (staged/unstaged delete
            # or committed-but-unpushed delete). Keep them lockable so the
            # dashboard still shows ownership until the lock is explicitly
            # released (for example on push).
            norm = self._normalize_file_path(file_path)
            try:
                modified_list, _ = self._get_modified_and_unpushed_files()
                in_progress = norm in set(modified_list)
            except Exception:
                in_progress = False

            if not in_progress:
                return False, f"File or directory does not exist locally: {file_path}"

            logger.info(
                (
                    "🔒 [DELETED-PATH] %s — path missing locally but "
                    "tracked as in-progress"
                ),
                norm,
            )

        # Locking directories creates noisy, transient dashboard rows
        # (for example runtime instance/ folders). Locks are file-oriented.
        if os.path.isdir(full_path):
            return False, f"Path is a directory and cannot be locked: {file_path}"

        # Ephemeral developer IDs do not persist locks to the backend
        # (useful for CI/test users). Short-circuit and return a local token.
        if getattr(self, "_is_ephemeral", False):
            token = f"ephemeral-{uuid.uuid4()}"
            logger.info(
                "🔒 [EPHEMERAL] %s (not persisted) — owner=%s",
                file_path,
                self.developer_id,
            )
            return True, token

        try:
            _ensure_lock_service_reachable()
        except (ConfigurationError, LockServiceUnavailableError) as exc:
            detail = f" ({exc.detail})" if exc.detail else ""
            return False, f"{exc.message}{detail}"

        branch = branch_name or self._get_current_branch()
        token = self._get_session_token()

        # Do not send expires_at: the RPC and DB intentionally ignore
        # time-based expiry. This keeps acquisition atomic while ensuring
        # locks persist until explicitly released.
        # Normalize the stored file_path so the watcher and dashboard see the
        # same canonical (project-relative, forward-slash) path.
        rpc_params = {
            "p_file_path": self._normalize_file_path(file_path),
            "p_developer_id": self.developer_id,
            "p_branch_name": branch,
            "p_reason": reason,
            "p_lock_token": token,
            "p_is_ephemeral": bool(getattr(self, "_is_ephemeral", False)),
            "p_agent_id": self.agent_id,
            "p_agent_label": self.agent_label,
            "p_origin": getattr(self, "origin", None)
            or agent_identity.resolve_origin(self.agent_id),
            "p_agent_kind": getattr(self, "agent_kind", None),
        }

        client = self._require_client()
        try:
            res = _retry_on_network_error(
                lambda: client.rpc("acquire_lock", rpc_params).execute()
            )
        except Exception as e:
            return False, self._format_acquire_failure(f"API Error: {e}")

        status, data, error = self._parse_response(res)

        if error:
            msg = (
                error.get("message", str(error))
                if isinstance(error, dict)
                else str(error)
            )
            return False, self._format_acquire_failure(f"API Error: {msg}")

        # Parse RPC result
        if isinstance(data, list) and len(data) > 0:
            row = data[0]
            if row.get("status") == "ok":
                # Sticky attribution (#169): the RPC returns the *stored* owner
                # after the upsert. When a human auto-lock renewed an existing
                # agent lock, the row stays ``origin=agent`` — reflect that in the
                # log instead of claiming a human "Auto-Watch Sync" reason.
                stored_agent_id = row.get("agent_id")
                if stored_agent_id and not self.agent_id:
                    effective_reason = f"preserved AI agent lock ({reason or 'sync'})"
                else:
                    effective_reason = reason or "No reason"
                logger.info(
                    "🔒 [LOCKED] %s — @%s (branch: %s, reason: %s)",
                    self._normalize_file_path(file_path),
                    self.developer_id,
                    branch or "main",
                    effective_reason,
                )
                # Cross-branch advisory: warn when renewing a lock that was
                # previously held by the same developer on a *different* branch.
                # This catches same-developer concurrent edits across worktrees
                # before they become merge conflicts (#150).
                existing_branch = row.get("existing_branch")
                if existing_branch and existing_branch != (branch or "main"):
                    logger.warning(
                        (
                            "⚠️ CROSS-BRANCH: %s is also locked by you on "
                            "branch '%s' (current: '%s'). "
                            "Concurrent edits may cause merge conflicts."
                        ),
                        self._normalize_file_path(file_path),
                        existing_branch,
                        branch or "main",
                    )
                return True, token
            if row.get("status") == "conflict":
                owner = row.get("owner", "another developer")
                conflict_agent = row.get("agent_id")
                conflict_kind = row.get("agent_kind")
                owner_display = self._format_owner(
                    str(owner),
                    conflict_agent,
                    row.get("agent_label"),
                    conflict_kind,
                )
                # #172: a conflict against your OWN developer id is not a
                # cross-developer merge risk. Under sticky attribution (#169) this
                # is only same-developer CROSS-AGENT; log at DEBUG so the polling
                # daemon does not spam "locked by yourself" warnings.
                if str(owner) == self.developer_id:
                    logger.debug(
                        "Self-lock on %s held by %s (same developer) — "
                        "no cross-developer conflict.",
                        self._normalize_file_path(file_path),
                        owner_display,
                    )
                else:
                    logger.warning(
                        (
                            "⚠️ CONFLICT: %s is locked by %s — your changes may "
                            "cause a merge conflict."
                        ),
                        self._normalize_file_path(file_path),
                        owner_display,
                    )
                return False, agent_identity.format_conflict_message(
                    file_path,
                    str(owner),
                    conflict_agent,
                    row.get("agent_label"),
                    conflict_kind,
                )

        if status in (200, 201):
            logger.info(
                "🔒 [LOCKED] %s — @%s (branch: %s, reason: %s)",
                self._normalize_file_path(file_path),
                self.developer_id,
                branch or "main",
                reason or "No reason",
            )
            return True, token

        return False, f"Unexpected response: status={status}, data={data}"

    def release(self, file_path: str) -> Tuple[bool, str]:
        """Release a lock on file_path owned by this developer.

        When called by a **human** (``agent_id`` is ``None``), any lock owned by this
        ``developer_id`` is released regardless of which agent claimed it — the human is
        in charge of all their agents.  When called by an **agent** (``agent_id`` is
        set), only locks claimed by that specific agent identity are released.

        Returns (success: bool, message: str).
        """
        # If ephemeral, nothing was persisted so there's nothing to delete.
        if getattr(self, "_is_ephemeral", False):
            logger.info(
                "🔓 [EPHEMERAL-RELEASE] %s (no-op for %s)", file_path, self.developer_id
            )
            return True, "ephemeral-released"

        norm = self._normalize_file_path(file_path)
        client = self._require_client()

        # Pre-check: verify a lock row exists for this file *and* belongs to
        # this developer before attempting the DELETE.  PostgREST returns
        # 204 No Content even when zero rows are deleted, so without this
        # guard the CLI would falsely report "✓ released" for locks that
        # belong to another developer or do not exist at all.
        try:
            check_res = _retry_on_network_error(
                lambda: client.table("file_locks")
                .select("developer_id")
                .eq("file_path", norm)
                .execute()
            )
        except Exception as e:
            return False, f"API Error: {e}"
        _st, rows, _err = self._parse_response(check_res)
        if not rows or not isinstance(rows, list) or len(rows) == 0:
            return False, f"No lock found for: {file_path}"
        lock_owner = rows[0].get("developer_id")
        if lock_owner != self.developer_id:
            return False, (
                f"Permission denied: {file_path} is locked by @{lock_owner or '?'}. "
                "Use `collab force-release` if you have admin credentials."
            )

        try:
            delete_query = (
                client.table("file_locks")
                .delete()
                .eq("file_path", norm)
                .eq("developer_id", self.developer_id)
            )
            # Human (agent_id is None): developer-scoped — release any agent's
            # lock.  Agent (agent_id is set): identity-scoped — only release
            # this specific agent's lock.
            if self.agent_id is not None:
                delete_query = delete_query.eq("agent_id", self.agent_id)
            res = _retry_on_network_error(lambda: delete_query.execute())
        except Exception as e:
            return False, f"API Error: {e}"

        status, data, error = self._parse_response(res)
        if error:
            return False, f"API Error: {error}"
        if status in (200, 204) or data is not None:
            logger.info("🔓 [RELEASED] %s — lock released", norm)
            return True, "released"
        return False, "No lock released (not owner or lock does not exist)"

    def active(self) -> List[Dict]:
        """Return all currently active locks.

        Raises :class:`LockServiceUnavailableError` when the lock service cannot be
        reached. Callers that must degrade gracefully (for example reconcile during a
        transient outage) should catch that exception explicitly.
        """
        if not _is_sandbox_lock_service():
            _ensure_lock_service_reachable()
        client = self._require_client()
        try:
            res = _retry_on_network_error(
                lambda: client.table("file_locks").select("*").execute()
            )
        except Exception as e:
            logger.error("Exception in active() Supabase query: %s", e)
            if _is_lock_service_error(e):
                if _is_sandbox_lock_service():
                    logger.debug("active() sandbox: treating unreachable stub as empty")
                    return []
                raise LockServiceUnavailableError(
                    "Lock service query failed",
                    detail=str(e),
                ) from e
            raise
        _, data, error = self._parse_response(res)
        if error:
            logger.error("Supabase error in active(): %s", error)
            if _is_sandbox_lock_service():
                return []
            raise LockServiceUnavailableError(
                "Lock service returned an error",
                detail=str(error),
            )
        return data or []

    def get_lock_status(self, file_path: str) -> Dict:
        """Return the lock status for a specific file."""
        client = self._require_client()
        try:
            norm = self._normalize_file_path(file_path)
            res = _retry_on_network_error(
                lambda: (
                    client.table("file_locks")
                    .select("*")
                    .eq("file_path", norm)
                    .execute()
                )
            )
        except Exception as e:
            return {"is_locked": False, "error": str(e)}

        _, data, error = self._parse_response(res)
        if error:
            return {"is_locked": False, "error": str(error)}

        rows = data or []
        if not rows:
            return {"is_locked": False, "can_edit": True}

        lock = rows[0]

        # With server-side expiry disabled, a present row implies an active
        # lock until it is explicitly released. Do not expose expires_at — it
        # was removed from the schema and is treated as audit-only historically.
        return {
            "is_locked": True,
            "locked_by": lock.get("developer_id"),
            "locked_by_agent_id": lock.get("agent_id"),
            "locked_by_agent_label": lock.get("agent_label"),
            "locked_by_agent_kind": lock.get("agent_kind"),
            "acquired_at": lock.get("acquired_at"),
            "reason": lock.get("reason"),
            "can_edit": self._lock_owned_by_me(lock),
        }

    def release_all(self, include_agent: bool = True) -> int:
        """Release locks held by this developer.

        By default (``include_agent=True``) every lock owned by this ``developer_id`` is
        released regardless of ``agent_id`` — both the human auto-locks and this
        developer's own AI-agent locks. This lets a human session fully clear locks left
        behind by its own agents (for example stale agent locks after a session ended
        without pushing). Genuine cross-developer locks are never touched.

        Set ``include_agent=False`` to restrict cleanup to the current ``(developer_id,
        agent_id)`` identity only.

        Returns the number of locks released.
        """
        try:
            locks = self.active()
        except LockServiceUnavailableError as exc:
            logger.error("release_all skipped — lock service unavailable: %s", exc)
            return 0

        count = 0
        for lk in locks:
            file_path = lk.get("file_path", "")
            if not file_path:
                continue
            if include_agent:
                # Developer-scoped: clear any lock under our developer_id,
                # including this developer's own agent identities.
                if lk.get("developer_id") != self.developer_id:
                    continue
                if self._release_developer_scope(file_path):
                    count += 1
            else:
                # Identity-scoped: only the current (developer_id, agent_id).
                if not self._lock_owned_by_me(lk):
                    continue
                ok, _ = self.release(file_path)
                if ok:
                    count += 1
        return count

    def release_all_except(self, keep_paths: List[str], branch: Optional[str]) -> int:
        """Release this developer's locks, retaining ``keep_paths`` as PR claims.

        Used by the pre-push hook when ``COLLAB_PR_CLAIMS=1``: the files still part of
        the pushed branch are promoted to persistent claims (tied to ``branch``)
        instead of being released, so cross-developer edit-time protection extends to
        the open PR. All other locks are released as usual. Returns the number of
        locks released (claims retained are not counted).

        Degrades gracefully: if ``keep_paths`` is empty, or the claim RPC is
        unavailable (migration not applied) or errors, this falls back to the ordinary
        :meth:`release_all` so behavior is never worse than today.
        """
        if getattr(self, "_is_ephemeral", False):
            return 0

        norm_keep = sorted({self._normalize_file_path(p) for p in keep_paths if p})
        if not norm_keep:
            return self.release_all()

        try:
            client = self._require_client()
            res = _retry_on_network_error(
                lambda: client.rpc(
                    "release_all_except",
                    {
                        "p_developer_id": self.developer_id,
                        "p_keep_paths": norm_keep,
                        "p_branch": branch or "",
                    },
                ).execute()
            )
            _, data, error = self._parse_response(res)
            if error:
                raise RuntimeError(str(error))
        except Exception as exc:
            logger.warning(
                "PR-claim retention unavailable (%s); releasing all locks instead",
                exc,
            )
            return self.release_all()

        count = data[0] if isinstance(data, list) and data else data
        if isinstance(count, bool):
            return 0
        if isinstance(count, int):
            return count
        if isinstance(count, str) and count.strip().lstrip("-").isdigit():
            return int(count)
        return 0

    def reconcile_pr_claims(self) -> int:
        """Release this developer's PR claims whose branch is merged or gone.

        Inert unless ``COLLAB_PR_CLAIMS=1``. Forces a pruning fetch and checks each
        claim's branch via git (see :func:`overlap.stale_claim_branches`); a never-
        running owner is still covered by the DB-side ``release_stale_claims`` expiry.
        Returns the number of claims released.
        """
        if not overlap.is_pr_claims_enabled():
            return 0
        try:
            locks = self.active()
        except LockServiceUnavailableError as exc:
            logger.debug("reconcile_pr_claims skipped — lock service down: %s", exc)
            return 0

        claims = [
            lk
            for lk in locks
            if lk.get("developer_id") == self.developer_id and lk.get("is_pr_claim")
        ]
        if not claims:
            return 0

        branches = {
            str(lk.get("claim_branch")) for lk in claims if lk.get("claim_branch")
        }
        stale = overlap.stale_claim_branches(_PROJECT_ROOT, list(branches))
        if not stale:
            return 0

        released = 0
        for lk in claims:
            if lk.get("claim_branch") in stale:
                file_path = lk.get("file_path", "")
                if file_path and self._release_developer_scope(file_path):
                    released += 1
        return released

    def force_release(self, file_path: str) -> Tuple[bool, str]:
        """Force-release a lock on file_path.

        Non-admin users can only force-release their own locks. Admin users (with
        SUPABASE_SERVICE_ROLE_KEY) can force-release any lock.

        Returns (success: bool, message: str).
        """
        if not self._is_admin:
            # Non-admin: may force-release own developer_id (any agent) but not others'.
            status_info = self.get_lock_status(file_path)
            if (
                status_info.get("is_locked")
                and status_info.get("locked_by") != self.developer_id
            ):
                owner = status_info.get("locked_by", "another developer")
                return False, (
                    f"Permission denied: {file_path} is locked by @{owner}. "
                    "Only admins can force-release other developers' locks."
                )

        client = self._require_client()
        try:
            norm = self._normalize_file_path(file_path)
            query = client.table("file_locks").delete().eq("file_path", norm)
            if not self._is_admin:
                query = query.eq("developer_id", self.developer_id)
            res = _retry_on_network_error(lambda: query.execute())
        except Exception as e:
            return False, f"API Error: {e}"
        _, data, error = self._parse_response(res)
        if error:
            return False, f"API Error: {error}"
        if data is not None:
            return True, "force-released"
        return False, "No lock removed"

    def force_release_all(self) -> int:
        """Force-release all locks (admin only).

        Returns the number of locks released.
        """
        if not self._is_admin:
            logger.warning(
                "Attempted force_release_all without admin privileges (dev=%s)",
                self.developer_id,
            )
            return 0

        try:

            # Count existing locks and collect file paths
            locks = self.active()
            paths: List[str] = []
            for lk in locks or []:
                p = lk.get("file_path")
                if isinstance(p, str) and p:
                    paths.append(p)
            count = len(paths)
            if count == 0:
                return 0

            client = self._require_client()

            # PostgREST forbids DELETE without a WHERE clause. Delete by
            # file_path IN (<paths>) in reasonably-sized chunks to avoid URL
            # length limits for very large sets.
            def chunks(lst: List[str], n: int):
                for i in range(0, len(lst), n):
                    yield lst[i : i + n]

            deleted_total = 0
            for ch in chunks(paths, 200):
                try:
                    res = _retry_on_network_error(
                        lambda: client.table("file_locks")
                        .delete()
                        .in_("file_path", ch)
                        .execute()
                    )
                except Exception as e:
                    logger.error("force_release_all chunk delete failed: %s", e)
                    return deleted_total
                status, data, error = self._parse_response(res)
                if error:
                    logger.error("force_release_all API error: %s", error)
                    return deleted_total
                # If PostgREST returns the deleted rows, prefer that; otherwise
                # conservatively count the attempted paths in the chunk.
                if data is not None and isinstance(data, list):
                    deleted_total += len(data)
                else:
                    deleted_total += len(ch)

            logger.info(
                "🔓 [FORCE-RELEASE-ALL] %d lock(s) released by admin", deleted_total
            )
            return deleted_total
        except Exception as e:
            logger.error("Failed to force_release_all: %s", e)
            return 0

    def _warn_if_non_editable(self) -> None:
        """Emit a warning if the package is installed non-editably in a source tree."""
        # Only warn if we appear to be in a source checkout of collab itself
        if not os.path.exists(os.path.join(_PROJECT_ROOT, "collab", "lock_client.py")):
            return

        is_editable = False
        try:
            import importlib.metadata

            dist = importlib.metadata.distribution("collab-runtime")
            data = dist.read_text("direct_url.json")
            if data:
                is_editable = (
                    json.loads(data).get("dir_info", {}).get("editable", False)
                )
        except Exception:
            pass

        if not is_editable:
            warning_msg = (
                "WARNING: collab is installed as a non-editable package. "
                "New dashboard assets and Python changes may not be visible. "
                "Run: pip install -e .   (or: scripts/setup.ps1 -Force)"
            )
            logger.warning(warning_msg)
            print(warning_msg)

    @staticmethod
    def _format_acquire_failure(message: str) -> str:
        """Add actionable context for common Supabase RPC / schema failures."""
        text = message or ""
        if "PGRST202" in text and "acquire_lock" in text:
            return (
                f"{text} — PostgREST does not see the agent-aware acquire_lock RPC. "
                "Re-run the full supabase/schema.sql in the Supabase SQL Editor "
                "(including CREATE OR REPLACE FUNCTION acquire_lock), then reload "
                "the API schema cache (Project Settings → API → Reload schema)."
            )
        return text

    def acquire_multiple(
        self,
        file_paths: List[str],
        reason: Optional[str] = None,
        branch_name: Optional[str] = None,
    ) -> Tuple[bool, List[str], str]:
        """Acquire locks for multiple files.

        Returns (all_ok, failed_paths, message).
        """
        failed = []
        for fp in file_paths:
            ok, msg = self.acquire(fp, reason=reason, branch_name=branch_name)
            if not ok:
                failed.append(fp)
                level = (
                    "Lock acquire failed"
                    if str(msg).startswith("API Error")
                    else "Lock conflict"
                )
                logger.warning("%s: %s — %s", level, fp, msg)
        if failed:
            return False, failed, "Conflicts or errors"
        return True, [], "Success"

    def release_multiple(self, file_paths: List[str]) -> Tuple[bool, int, str]:
        """Release locks for multiple files.

        Returns (ok, count, message).
        """
        count = 0
        for fp in file_paths:
            ok, _ = self.release(fp)
            if ok:
                count += 1
        return True, count, "Success"

    def _release_developer_scope(self, file_path: str) -> bool:
        """Release a lock owned by this *developer*, ignoring agent identity.

        Used by the background watcher to clean up this developer's locks for files that
        are no longer in progress (e.g. after a push), regardless of whether the lock
        was created by the human auto-watcher or by an AI agent of the same developer.
        It never touches other developers' locks.
        """
        if getattr(self, "_is_ephemeral", False):
            return True
        try:
            client = self._require_client()
            norm = self._normalize_file_path(file_path)
            delete_query = (
                client.table("file_locks")
                .delete()
                .eq("file_path", norm)
                .eq("developer_id", self.developer_id)
            )
            res = _retry_on_network_error(lambda: delete_query.execute())
        except Exception as exc:
            logger.debug("Developer-scoped release failed for %s: %s", file_path, exc)
            return False
        status, data, error = self._parse_response(res)
        if error:
            logger.debug("Developer-scoped release error for %s: %s", file_path, error)
            return False
        return status in (200, 204) or data is not None

    def history(self, file_path: Optional[str] = None, limit: int = 20) -> List[Dict]:
        """Fetch lock history records.

        When *file_path* is provided, an exact match is tried first.  If that returns
        nothing, a ``LIKE %<basename>%`` fallback query runs so the user does not have
        to remember the full stored path.
        """
        client = self._require_client()
        try:
            q = client.table("file_locks_history").select("*")
            if file_path:
                q = q.eq("file_path", file_path)
            q = q.order("id", desc=True).limit(limit)
            res = q.execute()
        except Exception as exc:
            logger.error("Failed to fetch lock history: %s", exc)
            return []

        _, data, error = self._parse_response(res)
        if error:
            logger.error("History query error: %s", error)
            return []
        rows = data or []

        # Fallback: if exact match returned nothing, try a partial match
        if not rows and file_path:
            try:
                basename = file_path.replace("\\", "/").rsplit("/", 1)[-1]
                q2 = (
                    client.table("file_locks_history")
                    .select("*")
                    .ilike("file_path", f"%{basename}%")
                    .order("id", desc=True)
                    .limit(limit)
                )
                res2 = q2.execute()
                _, data2, error2 = self._parse_response(res2)
                if not error2 and data2:
                    rows = data2
            except Exception as exc:
                logger.debug(
                    "History basename fallback failed for %s: %s", file_path, exc
                )

        return rows

    def prune_history(self, retention_days: int = 30) -> Tuple[bool, int, str]:
        """Delete lock history rows older than *retention_days* days.

        Returns (ok, deleted_count, message).
        """
        if retention_days < 1:
            return False, 0, "retention_days must be >= 1"

        client = self._require_client()

        # Preferred path: RPC in schema.sql (stable, server-side retention logic).
        try:
            res = _retry_on_network_error(
                lambda: client.rpc(
                    "prune_lock_history", {"p_retention_days": retention_days}
                ).execute()
            )
            _, data, error = self._parse_response(res)
            if error:
                raise RuntimeError(str(error))

            deleted = 0
            if isinstance(data, list) and data:
                row = data[0]
                if isinstance(row, dict):
                    for k in ("prune_lock_history", "deleted_count", "count"):
                        if k in row:
                            try:
                                deleted = int(row[k])
                                break
                            except Exception:
                                pass
                elif isinstance(row, (int, float)):
                    deleted = int(row)
            elif isinstance(data, (int, float)):
                deleted = int(data)

            return True, deleted, "history-pruned"
        except Exception as exc:
            # Backward-compatible fallback when RPC isn't deployed yet.
            logger.warning(
                "History prune RPC unavailable, falling back to REST delete: %s", exc
            )

        cutoff_iso = (
            _safe_now().astimezone(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()
        try:
            res = _retry_on_network_error(
                lambda: (
                    client.table("file_locks_history")
                    .delete()
                    .lt("released_at", cutoff_iso)
                    .execute()
                )
            )
            _, data, error = self._parse_response(res)
            if error:
                return False, 0, f"API Error: {error}"
            deleted = len(data) if isinstance(data, list) else 0
            return True, deleted, "history-pruned-fallback"
        except Exception as exc:
            return False, 0, f"API Error: {exc}"

    # ------------------------------------------------------------------
    # Daemon management
    # ------------------------------------------------------------------
    def daemon_start(
        self, interval: int = 5, timeout_mins: int = 0, open_dashboard: bool = False
    ) -> None:
        """Start the watcher as a background daemon process."""
        self._warn_if_non_editable()
        pid = self._read_pid()
        if pid and self._is_process_alive(pid):
            # Check if the watcher is orphaned (parent process dead)
            metadata = self._read_pid_file()
            if metadata:
                parent_pid = metadata.get("parent_pid")
                if parent_pid and not self._is_process_alive(parent_pid):
                    # Orphaned watcher - kill it and start fresh
                    print(
                        f"Detected orphaned watcher (PID: {pid}, parent "
                        f"{parent_pid} dead). Replacing..."
                    )
                    self._terminate_process(pid)
                    time.sleep(0.5)  # Give it time to terminate
                    self._remove_pid()
                    # Continue to start a new watcher
                else:
                    # Parent is alive, watcher is valid
                    entrypoint = metadata.get("entrypoint", "")
                    if entrypoint:
                        print(f"Watcher already running (PID: {pid}) — {entrypoint}")
                    else:
                        print(f"Watcher already running (PID: {pid})")
                    return
            else:
                # Legacy PID file without metadata - verify cmdline
                cmdline = self._get_cmdline_for_pid(pid)
                if cmdline and self._cmdline_matches_watcher(cmdline):
                    print(f"Watcher already running (PID: {pid})")
                    return
                if cmdline is None:
                    # If process metadata cannot be read (permission/race),
                    # prefer assuming the watcher is running instead of
                    # spawning a duplicate daemon.
                    print(f"Watcher already running (PID: {pid})")
                    return
                # cmdline doesn't match or unavailable - consider stale.
                # Continue to start new

        print("Starting lock watcher in background...")

        # Defensive: remove any stale stop-request file left behind by a previous
        # `daemon-stop` (otherwise the newly-started watcher will immediately
        # detect it and perform a graceful shutdown). This can happen if a
        # stop file was left in the state dir when no watcher was running.
        try:
            stop_file = _state_path(".stop_request")
            if os.path.exists(stop_file):
                logger.debug(
                    (
                        "Found stale stop request %s — removing before "
                        "starting new watcher"
                    ),
                    stop_file,
                )
                try:
                    os.remove(stop_file)
                except Exception:
                    logger.debug("Failed to remove stale stop request: %s", stop_file)
        except Exception:
            # Best-effort — don't fail startup if we can't inspect/remove the file
            pass
        cmd = [
            sys.executable,
            "-m",
            "collab.lock_client",
            "watch",
            "--interval",
            str(interval),
            "--timeout",
            str(timeout_mins),
            "--daemon",
            "--pid-file",
            PID_FILE,
        ]

        # Tie to parent PID for clean termination
        parent_pid, parent_method = self._get_parent_ide_pid()
        if parent_pid:
            cmd.extend(["--parent-pid", str(parent_pid)])
            # Get process name for better logging
            parent_name, _ = self._get_process_info_local(parent_pid)
            parent_name_str = parent_name or "unknown"
            # Pass parent name + detection method to child for better logging
            cmd.extend(["--parent-name", parent_name_str])
            cmd.extend(["--parent-method", parent_method or "unknown"])
            # Demote verbose parent-tying messages to DEBUG so they don't
            # clutter interactive console output when the user runs
            # `collab daemon-start`.
            logger.debug(
                "Tying watcher to parent process: %s (PID: %d) via %s",
                parent_name_str,
                parent_pid,
                parent_method or "unknown",
            )
        else:
            logger.debug("No parent IDE detected - watcher will run independently")

        # -----------------------------------------------------------------
        # Layer 1 — Session heartbeat for daemon-started watchers
        #
        # When an IDE session is present (parent_pid is set), resolve a
        # per-window session owner PID (NOT the shared VSCODE_PID) and spawn
        # a heartbeat-keeper that touches .daemon_heartbeat while that process
        # is alive.  The watcher is only armed with --heartbeat-file after the
        # keeper is confirmed running — spawn failure falls back to parent-PID
        # monitoring only (no 3 s self-destruct).
        #
        # The extension-owned heartbeat path (startWatcher / deactivate) is
        # unchanged.
        # -----------------------------------------------------------------
        _daemon_heartbeat: Optional[str] = None
        _heartbeat_keeper_proc = None
        _session_owner_pid: Optional[int] = None
        _session_owner_method: Optional[str] = None
        if parent_pid:
            _session_owner_pid, _session_owner_method = (
                self._get_session_heartbeat_owner_pid(parent_pid)
            )
            if _session_owner_pid:
                _daemon_heartbeat = _state_path(".daemon_heartbeat")
                self._terminate_heartbeat_keeper()
                _heartbeat_keeper_proc = self._spawn_heartbeat_keeper(
                    _daemon_heartbeat, _session_owner_pid
                )
                if _heartbeat_keeper_proc and self._confirm_heartbeat_keeper(
                    _heartbeat_keeper_proc, _daemon_heartbeat
                ):
                    cmd.extend(["--heartbeat-file", _daemon_heartbeat])
                    cmd.extend(
                        [
                            "--heartbeat-grace-seconds",
                            str(_DAEMON_HEARTBEAT_GRACE_SECONDS),
                        ]
                    )
                    self._write_keeper_pid(
                        _heartbeat_keeper_proc.pid,
                        session_owner_pid=_session_owner_pid,
                        heartbeat_file=_daemon_heartbeat,
                        session_method=_session_owner_method,
                    )
                    logger.debug(
                        (
                            "Daemon heartbeat armed: file=%s grace=%ds "
                            "session_owner=%d (%s) keeper=%d"
                        ),
                        _daemon_heartbeat,
                        _DAEMON_HEARTBEAT_GRACE_SECONDS,
                        _session_owner_pid,
                        _session_owner_method,
                        _heartbeat_keeper_proc.pid,
                    )
                else:
                    if _heartbeat_keeper_proc is not None:
                        self._terminate_process(_heartbeat_keeper_proc.pid)
                    _heartbeat_keeper_proc = None
                    logger.debug(
                        (
                            "Heartbeat keeper not confirmed for session owner "
                            "%d (%s); watcher will use parent-PID only"
                        ),
                        _session_owner_pid,
                        _session_owner_method,
                    )
            else:
                logger.debug(
                    "No per-window session owner resolved; "
                    "watcher will use parent-PID only"
                )

        if open_dashboard:
            cmd.append("--open-dashboard")

        if sys.platform == "win32":
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            # CREATE_NO_WINDOW (0x08000000) - hide console window
            # Only use DETACHED_PROCESS if we DON'T have a parent to track
            # DETACHED_PROCESS would orphan the process,
            # preventing IDE shutdown detection
            if parent_pid:
                # Tied to parent - use only CREATE_NO_WINDOW, not DETACHED_PROCESS
                # This ensures the process terminates when the parent IDE closes
                creation_flags = 0x08000000
                logger.debug(
                    "Starting watcher tied to parent PID %d (no DETACHED)", parent_pid
                )
            else:
                # No parent to track - can safely detach
                creation_flags = (
                    0x00000008 | 0x08000000
                )  # DETACHED_PROCESS + CREATE_NO_WINDOW
                logger.debug("Starting detached watcher (no parent to track)")

            # CRITICAL: Don't pass file handles from parent to child!
            # The child process will open its own log files via logging_config.py.
            # Passing parent file handles causes NUL corruption and file locking issues.
            spawn_argv = [pythonw] + cmd[1:] if os.path.exists(pythonw) else cmd
            try:
                proc = safe_subprocess.spawn_background(
                    spawn_argv,
                    policy="watcher",
                    cwd=_PROJECT_ROOT,
                    creationflags=creation_flags,
                )
            except SubprocessSecurityError as exc:
                logger.error("Refusing to start watcher: %s", exc)
                print(f"❌ Refusing to start watcher: {exc}")
                self._rollback_daemon_start_keeper(_heartbeat_keeper_proc)
                return
        else:
            # Unix/Linux/Mac: only use start_new_session if NOT tracking a parent
            # start_new_session creates a new process group, detaching from parent
            try:
                if not parent_pid:
                    logger.debug("Starting detached watcher (new session)")
                    proc = safe_subprocess.spawn_background(
                        cmd,
                        policy="watcher",
                        cwd=_PROJECT_ROOT,
                        start_new_session=True,
                    )
                else:
                    logger.debug(
                        "Starting watcher tied to parent %d (same session)",
                        parent_pid,
                    )
                    proc = safe_subprocess.spawn_background(
                        cmd,
                        policy="watcher",
                        cwd=_PROJECT_ROOT,
                    )
            except SubprocessSecurityError as exc:
                logger.error("Refusing to start watcher: %s", exc)
                print(f"❌ Refusing to start watcher: {exc}")
                self._rollback_daemon_start_keeper(_heartbeat_keeper_proc)
                return
        if sys.platform != "win32":
            # On Linux/Mac, the spawned proc.pid is the real child.
            # We record it immediately for tracking, though the child
            # will soon overwrite it with its own metadata.
            self._write_pid(proc.pid)

        # Wait up to 10 seconds for the child loop to start and write its true PID.
        # On Windows venv, pythonw.exe is a wrapper that exits quickly.
        # On Linux/Mac or non-venv Windows, it stays identical to proc.pid.
        actual_pid = None
        for i in range(100):  # 10 seconds max
            pid = self._read_pid()
            if pid and self._is_process_alive(pid):
                if sys.platform != "win32" or pid != proc.pid:
                    # Successfully found the real child (different PID from launcher)
                    actual_pid = pid
                    break
                # On Windows, if pid == proc.pid, it might be the launcher or a
                # non-wrapped pythonw.exe process.
                # If it stays stable for 1.5s, assume it's the real process.
                if i > 15:
                    actual_pid = pid
                    break
            time.sleep(0.1)

        if actual_pid:
            print(f"✅ Started (PID: {actual_pid})")
        else:
            start_error = DaemonStartError(
                "Watcher process exited or failed to record PID. "
                f"(Launcher PID: {proc.pid})"
            )
            logger.error("%s: %s", start_error.code, start_error.message)
            print(f"❌ {start_error.message}")
            print("   Check logs/collab.log for details.")

            # Clean up orphan processes on failed startup.
            # The launcher (proc.pid) may still be running even though
            # verification timed out, and a child watcher may have
            # recorded a different PID before dying.
            pid_from_file = self._read_pid()
            killed_any = False

            # 1) Terminate the launcher process if it's still alive.
            if self._is_process_alive(proc.pid):
                logger.info("Startup failed — terminating launcher PID %d", proc.pid)
                self._terminate_process(proc.pid)
                killed_any = True

            # 2) If a child watcher wrote a different PID before dying,
            #    terminate it too so we don't leave an orphan.
            if (
                pid_from_file
                and pid_from_file != proc.pid
                and self._is_process_alive(pid_from_file)
            ):
                logger.info(
                    "Startup failed — terminating orphan watcher PID %d "
                    "(different from launcher PID %d)",
                    pid_from_file,
                    proc.pid,
                )
                self._terminate_process(pid_from_file)
                killed_any = True

            # 3) Remove the PID file so stale state is cleared.
            self._remove_pid()
            self._terminate_heartbeat_keeper()

            if killed_any:
                logger.info(
                    "Cleaned up orphan process(es) after failed daemon start "
                    "(launcher PID: %d)",
                    proc.pid,
                )

    def daemon_stop(self) -> bool:
        """Stop the running watcher daemon.

        Returns ``True`` when a live watcher (or an orphaned ``collab.exe`` launcher
        wrapper) was found and reaped, ``False`` when nothing was running. The return
        value lets callers such as :meth:`worktree_unregister` report an accurate result
        and CLI exit code.
        """
        # Ensure file-based collab logging is configured for CLI actions,
        # then temporarily prevent collab.* logs from propagating to the root
        # console handler so INFO-level records produced by this command are
        # still written to the collab log file but do not echo to the
        # user's terminal. Restore the original propagation setting at the end.
        try:
            from .logging_config import setup_collab_logging

            setup_collab_logging(collab_dir=_COLLAB_ROOT)
        except Exception:
            # Best-effort: continue even if logging setup fails
            pass

        collab_logger = logging.getLogger("collab")
        _old_prop = getattr(collab_logger, "propagate", True)
        collab_logger.propagate = False
        try:

            # Try PID file first, but fall back to discovering running watcher
            # processes for this workspace if the PID file is missing or stale.
            pid = self._read_pid()
            pids_to_stop: List[int] = []
            watcher_found = False

            if pid and self._is_process_alive(pid):
                pids_to_stop = [pid]
                watcher_found = True
            else:
                # Safety rail: during tests, never discover/stop external watcher
                # processes when the module is still using the production PID file.
                default_pid = os.path.join(_COLLAB_ROOT, ".daemon.pid")
                if _is_test_mode() and os.path.abspath(PID_FILE) == os.path.abspath(
                    default_pid
                ):
                    print("No running watcher found.")
                    logger.info(
                        (
                            "Test mode with default PID file detected; "
                            "skipping watcher discovery fallback"
                        )
                    )
                    self._remove_pid()
                    return False

                # Attempt to discover live watcher processes related to this repo.
                # Note: even when no Python watcher is found we still fall through
                # to the launcher-reaping step below, because an orphaned
                # ``collab.exe`` wrapper can outlive the watcher it spawned.
                try:
                    found = self._discover_running_watchers()
                    if found:
                        pids_to_stop = found
                        watcher_found = True
                except Exception as e:
                    logger.debug("Watcher discovery failed: %s", e)

            # Stop each discovered watcher PID (soft stop first, then force)
            for target_pid in pids_to_stop:
                try:
                    print(f"Stopping lock watcher (PID: {target_pid})...")
                except Exception:
                    pass

                stop_file = _state_path(".stop_request")
                # Prefer token-based stop requests when available to avoid
                # accidentally stopping unrelated watcher processes that happen
                # to share PIDs (PID reuse) or when multiple watchers exist.
                try:
                    pid_meta = self._read_pid_file()
                    token = None
                    if pid_meta and isinstance(pid_meta, dict):
                        token = pid_meta.get("token")
                    if token:
                        payload = f"TOKEN:{token}"
                    else:
                        payload = f"PID:{target_pid}"

                    with open(stop_file, "w", encoding="utf-8") as sf:
                        sf.write(payload)
                        sf.flush()
                        try:
                            os.fsync(sf.fileno())
                        except Exception:
                            pass
                    logger.info(
                        "Wrote stop request file: %s (payload: %s)", stop_file, payload
                    )
                except Exception as _e:
                    logger.exception("Failed to write stop request file: %s", _e)

                # Wait up to ~8 seconds for watcher to exit gracefully
                for _ in range(16):
                    if not self._is_process_alive(target_pid):
                        break
                    time.sleep(0.5)

                if not self._is_process_alive(target_pid):
                    # Wait briefly for the shutdown marker
                    shutdown_file = _state_path(".shutdown_complete")
                    for _ in range(20):
                        if os.path.exists(shutdown_file):
                            break
                        time.sleep(0.1)

                    # Do NOT remove the stop request here; the IDE extension
                    # needs to see it to avoid triggering an auto-restart.
                    # The next watcher startup will clean it up.

                    # If the stopped PID matched the canonical PID file, remove it
                    try:
                        canonical_pid = self._read_pid()
                        if canonical_pid == target_pid:
                            self._remove_pid()
                    except Exception:
                        logger.debug(
                            "Failed to remove canonical PID after stop: %s", target_pid
                        )

                    logger.info("Stopped watcher (PID: %d)", target_pid)
                    print("✅ Stopped.")
                    continue

                # Soft stop did not work — fallback to forced termination
                if sys.platform == "win32":
                    platform_probe.taskkill_force(target_pid, tree=True)
                else:
                    try:
                        os.kill(-target_pid, signal.SIGTERM)
                    except (ProcessLookupError, OSError):
                        try:
                            os.kill(target_pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass

                # Wait up to 5 seconds for clean exit
                for _ in range(10):
                    if not self._is_process_alive(target_pid):
                        break
                    time.sleep(0.5)
                else:
                    # Force kill if still running (Unix only)
                    if sys.platform != "win32":
                        try:
                            os.kill(-target_pid, signal.SIGKILL)
                        except (ProcessLookupError, OSError):
                            try:
                                os.kill(target_pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass

                # Clean up PID file if it referenced the killed process
                try:
                    canonical_pid = self._read_pid()
                    if canonical_pid == target_pid:
                        self._remove_pid()
                except Exception:
                    logger.debug(
                        "Failed to remove canonical PID after forced kill: %s",
                        target_pid,
                    )

                logger.info("Stopped watcher (PID: %d) (forced)", target_pid)
                print("✅ Stopped.")

            # Defense-in-depth (Windows): reap orphaned ``collab.exe`` /
            # ``collab-watcher.exe`` console-script wrappers in this namespace.
            # These keep the venv ``.exe`` image locked (EBUSY on delete) and can
            # outlive the Python watcher when started by an older IDE extension.
            # Give a well-behaved wrapper a brief moment to exit on its own first.
            if pids_to_stop:
                time.sleep(0.5)
            reaped = self._reap_collab_launchers()
            if reaped:
                logger.info("Reaped %d orphaned collab launcher wrapper(s)", reaped)
                print(
                    f"✅ Cleaned up {reaped} leftover collab launcher "
                    f"process(es) locking the virtualenv."
                )

            if not watcher_found and not reaped:
                print("No running watcher found.")
                logger.info("No running watcher found for this workspace")

            # Reap the daemon-start heartbeat keeper if present.
            self._terminate_heartbeat_keeper()

            # Final cleanup: ensure canonical PID file removed
            try:
                self._remove_pid()
            except Exception:
                pass

            return watcher_found or bool(reaped)
        finally:
            try:
                collab_logger.propagate = _old_prop
            except Exception:
                pass

    def worktree_unregister(self, worktree_path: str) -> bool:
        """Stop the watcher + heartbeat keeper for ONE specific worktree.

        Deterministic teardown primitive (#168). Targets only the given
        worktree's isolated state namespace, so finishing work in a worktree
        (for example switching chats in a Cursor Agents window) can release that
        worktree's file handles — letting the folder be deleted on Windows —
        without affecting watchers running in other worktrees. Safe to run from
        any directory and idempotent: clears stale markers and returns ``False``
        when no live watcher is found.
        """
        raw = str(worktree_path or "").strip()
        if not raw:
            print("✗ worktree-unregister requires a worktree path.")
            return False
        target = os.path.abspath(os.path.expanduser(raw))

        # Current worktree → reuse the fully battle-tested in-process stop path
        # (handles watcher discovery, launcher reaping, and PID cleanup). Its
        # boolean result flows straight through so the return value / exit code
        # is accurate whether or not a watcher was actually running.
        if os.path.normcase(target) == os.path.normcase(os.path.abspath(_PROJECT_ROOT)):
            return self.daemon_stop()

        state_dir = _state_dir_for_root(target)
        pid_file = os.path.join(state_dir, agent_identity.daemon_pid_basename(None))
        stopped = self._stop_worktree_namespace(state_dir, pid_file, target)
        if stopped:
            print(f"✅ Stopped watcher for worktree: {target}")
        else:
            print(f"No running watcher found for worktree: {target}")
        return stopped

    def _stop_worktree_namespace(
        self, state_dir: str, pid_file: str, target_root: str
    ) -> bool:
        """Signal + reap the watcher/keeper recorded in a specific state namespace.

        Writes a scoped ``.stop_request`` (token-matched when possible) so the target
        watcher shuts down gracefully, then force-terminates it and its heartbeat keeper
        if still alive. Only touches files/PIDs recorded in *state_dir* — it never
        performs global process reaping, so sibling worktrees are unaffected.
        """
        watcher_pid: Optional[int] = None
        token: Optional[str] = None
        try:
            if os.path.isfile(pid_file):
                with open(pid_file, "r", encoding="utf-8") as fh:
                    meta = json.loads(fh.read().strip() or "{}")
                if isinstance(meta, dict):
                    raw_pid = meta.get("pid")
                    watcher_pid = int(raw_pid) if raw_pid else None
                    tok = meta.get("token")
                    token = str(tok) if tok else None
        except Exception as exc:
            logger.debug(
                "worktree-unregister: unreadable PID file %s: %s", pid_file, exc
            )

        # Write a scoped stop request the target watcher polls for in its own
        # state dir (token-matched to avoid PID-reuse false positives).
        try:
            os.makedirs(state_dir, exist_ok=True)
            if token:
                payload = f"TOKEN:{token}"
            elif watcher_pid:
                payload = f"PID:{watcher_pid}"
            else:
                payload = "PID:0"
            stop_file = os.path.join(state_dir, ".stop_request")
            with open(stop_file, "w", encoding="utf-8") as sf:
                sf.write(payload)
                sf.flush()
                try:
                    os.fsync(sf.fileno())
                except Exception:
                    pass
            logger.info(
                "worktree-unregister: wrote stop request %s (%s) for %s",
                stop_file,
                payload,
                target_root,
            )
        except Exception as exc:
            logger.debug("worktree-unregister: failed to write stop request: %s", exc)

        found = bool(watcher_pid and self._is_process_alive(watcher_pid))
        if found and watcher_pid is not None:
            # Wait ~8s for graceful self-exit, then force-terminate.
            for _ in range(16):
                if not self._is_process_alive(watcher_pid):
                    break
                time.sleep(0.5)
            if self._is_process_alive(watcher_pid):
                logger.info(
                    "worktree-unregister: force-terminating watcher PID %d",
                    watcher_pid,
                )
                self._terminate_process(int(watcher_pid))

        # Reap the heartbeat keeper recorded in this namespace, if any.
        self._terminate_keeper_in_dir(state_dir)

        # Defense-in-depth (Windows): reap orphaned ``collab.exe`` /
        # ``collab-watcher.exe`` console-script wrappers bound to THIS worktree's
        # namespace. The Python watcher + keeper are the primary file-handle
        # blockers, but a legacy wrapper spawned by an older IDE extension can
        # outlive them and keep the venv ``.exe`` image locked. This closes the
        # cross-worktree parity gap with :meth:`daemon_stop`. A reaped wrapper
        # counts as a stop so the caller reports success accurately.
        if self._reap_launchers_in_namespace(state_dir, pid_file, target_root):
            found = True

        # Remove the PID marker so status/start no longer sees a stale watcher.
        try:
            if os.path.isfile(pid_file):
                os.remove(pid_file)
        except OSError as exc:
            logger.debug("worktree-unregister: could not remove PID file: %s", exc)

        return found

    def _reap_launchers_in_namespace(
        self, state_dir: str, pid_file: str, target_root: str
    ) -> int:
        """Reap orphaned ``collab.exe`` launcher wrappers for ONE worktree namespace.

        Cross-worktree teardown (#168) cannot call :meth:`_reap_collab_launchers`
        directly: that method matches wrappers against the *module-global*
        namespace (``PID_FILE`` / ``_PROJECT_ROOT`` / ``_COLLAB_ROOT``) of the
        current process, which points at the caller's own worktree. This wrapper
        temporarily retargets those globals at *target_root* so the existing,
        strictly ``--pid-file``-scoped matcher only ever reaps wrappers that
        belong to the target worktree — sibling worktrees are never touched. The
        globals are restored unconditionally, even on error, so the caller's own
        namespace is left exactly as it was. Returns the number of wrappers
        reaped (always ``0`` off Windows or in test mode).
        """
        global PID_FILE, _PROJECT_ROOT, _COLLAB_ROOT
        saved = (PID_FILE, _PROJECT_ROOT, _COLLAB_ROOT)
        try:
            PID_FILE = pid_file
            _PROJECT_ROOT = target_root
            _COLLAB_ROOT = state_dir
            return self._reap_collab_launchers()
        except Exception as exc:
            logger.debug("worktree-unregister: launcher reap failed: %s", exc)
            return 0
        finally:
            PID_FILE, _PROJECT_ROOT, _COLLAB_ROOT = saved

    def _terminate_keeper_in_dir(self, state_dir: str) -> None:
        """Terminate + clear the heartbeat keeper recorded in *state_dir*."""
        keeper_path = os.path.join(state_dir, ".daemon_keeper.pid")
        try:
            if not os.path.isfile(keeper_path):
                return
            with open(keeper_path, "r", encoding="utf-8") as fh:
                meta = json.loads(fh.read().strip() or "{}")
            keeper_pid = meta.get("pid") if isinstance(meta, dict) else None
            if isinstance(keeper_pid, int) and keeper_pid > 0:
                if self._is_process_alive(keeper_pid):
                    logger.debug(
                        "worktree-unregister: terminating heartbeat keeper PID %d",
                        keeper_pid,
                    )
                    self._terminate_process(keeper_pid)
            try:
                os.remove(keeper_path)
            except OSError:
                pass
        except Exception as exc:
            logger.debug("worktree-unregister: keeper reap failed: %s", exc)

    def daemon_status(self) -> bool:
        """Check if the watcher daemon is running.

        Checks both the primary PID file and the legacy PyCharm watcher PID file for
        backward compatibility.
        """
        try:
            pid = self._read_pid(strict=True)
        except PidParseError as exc:
            print(f"ℹ️  Lock watcher status unavailable: {exc.message}")
            return False
        local_only_mode = bool(getattr(self, "local_only", False))
        if pid and self._is_process_alive(pid):
            # Attempt to read PID metadata (entrypoint) and prefer it for
            # human-facing output when available. When the PID file is the
            # legacy plain-integer format we avoid strict cmdline verification
            # to reduce false negatives in environments where reconstructing
            # a cmdline is unreliable (tests, limited containers, etc.).
            entrypoint: Optional[str] = None
            had_metadata = False
            try:
                if os.path.exists(PID_FILE):
                    with open(PID_FILE, "r", encoding="utf-8") as fh:
                        raw = fh.read().strip()
                    if raw.startswith("{"):
                        had_metadata = True
                        obj = json.loads(raw)
                        entrypoint = obj.get("entrypoint")
            except Exception:
                entrypoint = None

            # If an entrypoint is present in the PID metadata, prefer it.
            if entrypoint:
                print(f"✅ Lock watcher is RUNNING (PID: {pid}) — {entrypoint}")
                return True

            # If we have no richer metadata (legacy plain-PID) preserve the
            # historical, lenient behaviour: older clients only wrote an integer PID
            # and callers expect a live PID to indicate the watcher is running.
            # Do NOT mark such PIDs stale solely because the reconstructed
            # command-line doesn't match — this avoids false negatives in tests
            # and constrained environments where cmdline inspection is unreliable.
            if not had_metadata:
                # If this is the legacy plain-PID file, preserve the historical
                # behavior: if the PID matches the current process, confidently
                # report running. Otherwise fall through and attempt a
                # best-effort cmdline verification below to avoid treating an
                # unrelated process as the watcher.
                if pid == os.getpid():
                    print(f"✅ Lock watcher is RUNNING (PID: {pid}) (cmdline unknown)")
                    return True

            # Fallback: try to verify the process command-line to avoid false positives
            cmdline = self._get_cmdline_for_pid(pid)
            if cmdline:
                if not self._cmdline_matches_watcher(cmdline):
                    logger.debug("PID %d cmdline: %s", pid, cmdline)
                else:
                    print(f"✅ Lock watcher is RUNNING (PID: {pid}) — {cmdline}")
                    return True
            else:
                # Can't verify cmdline — assume running
                print(f"✅ Lock watcher is RUNNING (PID: {pid}) (cmdline unknown)")
                return True

            # Stale or repurposed PID in canonical file; in local-only CLI mode,
            # try process discovery before reporting NOT running.
            if local_only_mode:
                try:
                    found = self._discover_running_watchers()
                    if len(found) > 1:
                        logger.warning(
                            "%s",
                            WatcherDiscoveryError(
                                f"multiple watcher processes: {found}",
                                detail="canonical PID file may be stale",
                            ),
                        )
                    for found_pid in found:
                        if self._is_process_alive(found_pid):
                            found_cmd = self._get_cmdline_for_pid(found_pid)
                            if found_cmd and self._cmdline_matches_watcher(found_cmd):
                                print(
                                    "✅ Lock watcher is RUNNING "
                                    f"(PID: {found_pid}) — {found_cmd}"
                                )
                            else:
                                print(
                                    "✅ Lock watcher is RUNNING "
                                    f"(PID: {found_pid}) (discovered)"
                                )
                            return True
                except Exception as e:
                    logger.debug("Watcher discovery fallback failed: %s", e)

            return False

        # In local-only CLI mode, if no canonical PID was available/alive,
        # fall back to watcher process discovery.
        if local_only_mode:
            try:
                found = self._discover_running_watchers()
                if len(found) > 1:
                    logger.warning(
                        "%s",
                        WatcherDiscoveryError(
                            f"multiple watcher processes: {found}",
                            detail="canonical PID file may be stale",
                        ),
                    )
                for found_pid in found:
                    if self._is_process_alive(found_pid):
                        found_cmd = self._get_cmdline_for_pid(found_pid)
                        if found_cmd and self._cmdline_matches_watcher(found_cmd):
                            print(
                                "✅ Lock watcher is RUNNING "
                                f"(PID: {found_pid}) — {found_cmd}"
                            )
                        else:
                            print(
                                "✅ Lock watcher is RUNNING "
                                f"(PID: {found_pid}) (discovered)"
                            )
                        return True
            except Exception as e:
                logger.debug("Watcher discovery fallback failed: %s", e)

        # Fallback: check legacy PyCharm watcher PID file
        _legacy_pid_file = os.path.join(_COLLAB_ROOT, ".pycharm_watcher.pid")
        if os.path.exists(_legacy_pid_file):
            try:
                with open(_legacy_pid_file, "r") as f:
                    legacy_pid = int(f.read().strip())
                if self._is_process_alive(legacy_pid):
                    print(f"✅ Lock watcher is RUNNING (PID: {legacy_pid})")
                    return True
            except (ValueError, OSError):
                pass
        print("ℹ️  Lock watcher is not running.")
        return False

    def cleanup_orphaned_processes(self) -> None:
        """Find and kill all orphaned lock_client.py processes.

        This is useful when log files are locked by zombie processes.
        Locks are PRESERVED - only the watcher processes are terminated.
        """
        print("Scanning for orphaned lock_client processes...")
        killed = 0
        pids_to_check: set[int] = set()

        is_test = _is_test_mode()

        def _should_kill(cmdline: str) -> bool:
            cmd = cmdline.lower()
            if "lock_client" not in cmd:
                return False

            # Safeguard: prevent test runs from killing production daemons.
            is_test_watcher = (
                "pytest-of-" in cmd or "collab_test_" in cmd or "collab_pytest_" in cmd
            )
            return is_test_watcher if is_test else not is_test_watcher

        if sys.platform == "win32":
            try:
                for pid in platform_probe.iter_tasklist_python_pids():
                    if pid != os.getpid():
                        pids_to_check.add(pid)
            except Exception as e:
                logger.debug("Error scanning python processes via tasklist: %s", e)

            # Inspect command-lines (prefer psutil); fall back to WMIC if available.
            for pid in list(pids_to_check):
                try:
                    inspected = False
                    try:
                        import psutil

                        try:
                            p = psutil.Process(pid)
                            cmd = (
                                " ".join(p.cmdline())
                                if isinstance(p.cmdline(), (list, tuple))
                                else str(p.cmdline())
                            )
                            inspected = True
                        except psutil.NoSuchProcess:
                            continue
                        except Exception:
                            inspected = False
                    except Exception:
                        inspected = False

                    if inspected and cmd and _should_kill(cmd):
                        print(f"Killing orphaned lock_client (PID: {pid})")
                        platform_probe.taskkill_force(pid, tree=True)
                        killed += 1
                        continue

                    # psutil not available or didn't identify commandline;
                    # try WMIC if present
                    if shutil.which("wmic"):
                        try:
                            out = platform_probe.wmic_cmdline_value(pid)
                            if out and _should_kill(out):
                                print(f"Killing orphaned lock_client (PID: {pid})")
                                platform_probe.taskkill_force(pid, tree=True)
                                killed += 1
                        except Exception as e:
                            logger.debug("Error checking PID %d via WMIC: %s", pid, e)
                    else:
                        # Cannot reliably inspect command-line on this host
                        logger.debug(
                            (
                                "Skipping command-line inspection for PID %d "
                                "(no psutil or wmic)"
                            ),
                            pid,
                        )
                except Exception as e:
                    logger.debug("Error checking PID %d: %s", pid, e)
        else:
            # Unix: use ps and grep
            try:
                ps_out = platform_probe.ps_aux()
                for line in ps_out.split("\n"):
                    if "python" in line.lower() and _should_kill(line):
                        parts = line.split()
                        if len(parts) >= 2:
                            try:
                                pid = int(parts[1])
                                # Don't kill ourselves
                                if pid != os.getpid():
                                    print(f"  Killing orphaned process (PID: {pid})")
                                    try:
                                        os.kill(pid, signal.SIGTERM)
                                        killed += 1
                                    except ProcessLookupError:
                                        pass
                            except (ValueError, IndexError):
                                pass
            except Exception as e:
                logger.warning("Error scanning for orphaned processes: %s", e)

        if killed > 0:
            print(f"✅ Killed {killed} orphaned process(es).")
            print("Log files should now be unlocked.")
            # Also clean up PID file if present
            self._remove_pid()
        else:
            print("No orphaned lock_client processes found.")
            # Try to identify what's holding the log files
            if sys.platform == "win32":
                print("\nChecking what's holding log files...")
                for log_file in ["application.log", "errors.log"]:
                    log_path = os.path.join(_COLLAB_ROOT, "logs", log_file)
                    if os.path.exists(log_path):
                        try:
                            # Try to open the file to see if it's locked
                            with open(log_path, "a"):
                                pass  # File is accessible
                        except PermissionError:
                            print(f"  {log_file} is LOCKED by another process")
                            print(f"  Run: handle.exe {log_path} (from Sysinternals)")
                        except Exception as e:
                            print(f"  {log_file}: {e}")

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------
    def dashboard(self) -> None:
        """Open the collaborative dashboard in the default browser."""
        url, _ = self._prepare_dashboard_server()
        if not url:
            return
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            print(f"Open in browser manually: {url}")

    def _prepare_dashboard_server(self) -> Tuple[Optional[str], Optional[str]]:
        """Create temp HTML with injected config, start local HTTP server.

        Serves the full ``collab/dashboard`` directory so sibling assets (``dashboard-
        format.js``, etc.) resolve correctly.

        Returns (url, tmp_path) or (None, None) on error.
        """
        from collab.dashboard_server import prepare_dashboard_server

        injected = {
            "url": SUPABASE_URL or "",
            "anonKey": _effective_anon_key() or "",
            "serviceKey": _effective_service_role_key(),
            "user": self.developer_id or "",
        }
        return prepare_dashboard_server(
            _RESOURCE_ROOT, injected, project_root=_PROJECT_ROOT
        )

    # ------------------------------------------------------------------
    # Heartbeat policy (extracted from watch() for clarity and testability)
    # ------------------------------------------------------------------
    def _heartbeat_should_shutdown(self, startup_time: float) -> Optional[str]:
        """Return a reason string if the watcher should shut down due to heartbeat.

        Single coherent policy:

        1. **Missing file** — silent during
           ``COLLAB_HEARTBEAT_STARTUP_GRACE_SECONDS`` (default 3 s), then
           returns ``"heartbeat_missing"``.
        2. **Stale mtime** — one-time soft skip when the parent IDE process
           is still alive, then returns ``"heartbeat_stale"`` once the age
           exceeds ``heartbeat_grace_seconds + _HEARTBEAT_SOFT_EXTRA_SECONDS``.

        Returns ``None`` when the heartbeat is healthy or inside an allowed
        grace window.
        """
        if not self._heartbeat_file:
            return None

        now_ts = time.time()

        # --- Missing heartbeat file ---
        if not os.path.exists(self._heartbeat_file):
            age_since_startup = now_ts - startup_time
            if age_since_startup < _HEARTBEAT_STARTUP_GRACE_SECONDS:
                logger.debug(
                    "Heartbeat missing but within startup grace (%.2fs) — ignoring",
                    age_since_startup,
                )
                return None
            logger.info(
                "Heartbeat file missing (%s). Shutting down...",
                self._heartbeat_file,
            )
            return "heartbeat_missing"

        # --- Stale heartbeat ---
        age = now_ts - os.path.getmtime(self._heartbeat_file)
        logger.debug(
            "Heartbeat age: %.1fs (threshold: %ss)",
            age,
            self._heartbeat_grace_seconds,
        )

        if age <= float(self._heartbeat_grace_seconds):
            return None

        parent_alive = bool(
            self._parent_pid and self._is_process_alive(self._parent_pid)
        )

        # One-time soft skip: tolerate a brief extension-host hiccup
        # when the parent IDE process is still running.
        if parent_alive and not self._heartbeat_soft_skipped:
            logger.warning(
                (
                    "Heartbeat stale (%.1fs > %ss). "
                    "Parent alive; allowing one-time extra %.1fs grace."
                ),
                age,
                self._heartbeat_grace_seconds,
                _HEARTBEAT_SOFT_EXTRA_SECONDS,
            )
            self._heartbeat_soft_skipped = True
            return None

        if age > float(self._heartbeat_grace_seconds) + _HEARTBEAT_SOFT_EXTRA_SECONDS:
            # Final failure: log file contents for debugging
            try:
                with open(self._heartbeat_file, "r", encoding="utf-8") as hf:
                    content = hf.read().strip()
                logger.debug("Heartbeat file content: %s", content)
            except Exception:
                pass
            logger.info(
                "Heartbeat stale (%.1fs > %ss) at %s. Shutting down...",
                age,
                self._heartbeat_grace_seconds,
                self._heartbeat_file,
            )
            return "heartbeat_stale"

        return None

    # ------------------------------------------------------------------
    # Watcher (foreground process)
    # ------------------------------------------------------------------
    def watch(
        self,
        interval: int = 5,
        timeout_mins: int = 0,
        open_dashboard: bool = False,
        daemon_mode: bool = False,
        parent_pid: Optional[int] = None,
        parent_name: Optional[str] = None,
        parent_method: Optional[str] = None,
        heartbeat_file: Optional[str] = None,
        heartbeat_grace_seconds: int = 10,
    ) -> None:
        """Run the file-watching loop (foreground).

        Called by daemon_start.  When *daemon_mode* is True the parent- PID liveness
        check is skipped (detached daemons have no meaningful parent).
        """
        # Ensure file-based logging is wired so watch output goes to logs/
        from .logging_config import setup_collab_logging

        setup_collab_logging(collab_dir=_COLLAB_ROOT)

        if not daemon_mode:
            self._parent_pid = parent_pid or os.getppid()
        else:
            self._parent_pid = parent_pid

        self._heartbeat_file = heartbeat_file
        self._heartbeat_grace_seconds = heartbeat_grace_seconds
        # Reset soft-skip on (re)start of the watch loop
        self._heartbeat_soft_skipped = False

        # Include a short session token in PID metadata so stop requests can
        # target the intended watcher instance instead of relying solely on PIDs.
        try:
            token = self._get_session_token()
        except Exception:
            token = None
        self._write_pid(os.getpid(), parent_pid=self._parent_pid, token=token)
        logger.info("Wrote PID metadata to %s (PID: %d)", PID_FILE, os.getpid())

        # Defensive: remove any stale stop-request file on startup so we don't
        # immediately shut down. The IDE extension or CLI may have left this
        # behind from a previous session.
        try:
            stop_file = _state_path(".stop_request")
            if os.path.exists(stop_file):
                os.remove(stop_file)
                logger.info("Removed stale stop request on watch loop entry.")
        except Exception:
            pass

        self._register_signal_handlers()
        # Start a low-latency OS-level parent monitor (Windows) to detect
        # parent termination without relying on WMIC/tasklist polling.
        try:
            self._start_parent_monitor_thread()
        except Exception:
            # Best-effort: continue if monitor can't be started
            logger.debug("Parent monitor thread not started or failed to initialize")

        # NOTE: Job Object is disabled to allow graceful shutdown
        # The Job Object kills the process immediately when parent dies,
        # preventing signal handlers and atexit from running.
        # We rely on parent death detection and signal handlers instead.

        # Startup banner matching pycharm_watcher format exactly
        timeout_label = f"{timeout_mins}m" if timeout_mins > 0 else "disabled"
        logger.info("=" * 60)
        logger.info("Collab Locks -- Lock Client Watcher")
        logger.info("Developer: %s", self.developer_id)
        logger.info("Interval: %ds | Timeout: %s", interval, timeout_label)
        # Dashboard URL or command (like pycharm_watcher)
        dashboard_url, _ = self._prepare_dashboard_server()
        if dashboard_url:
            logger.info("Dashboard: %s", dashboard_url)
        else:
            logger.info("Dashboard: collab dashboard")
        # Optionally open the dashboard in the default browser when requested.
        if open_dashboard:
            try:
                self.dashboard()
            except Exception:
                logger.exception("Failed to open dashboard")
        logger.info("=" * 60)

        # Log session token (truncated) for debugging cross-IDE token divergence
        session_token = self._get_session_token()
        logger.debug(
            "Session token: %s... (dev=%s, host=%s)",
            session_token[:8],
            self.developer_id,
            socket.gethostname(),
        )

        # Initialize parent PID tracking for adoption detection (debug only)
        self._initial_ppid = os.getppid()
        logger.debug(
            "Initial parent PID recorded for adoption detection: %d", self._initial_ppid
        )

        last_modified: set = set()
        last_change_time = _safe_now()
        last_parent_check = _safe_now()
        last_pid_heartbeat = time.time()
        _last_worktree_check = 0.0  # timestamp of last worktree-validity check

        # Initialize WMIC resolution failure streak counter for zombie process detection
        _parent_name_unknown_streak = 0
        _last_known_parent_name = parent_name

        # Initial remote lock scan (logs [LOCKED] for existing locks)
        self._scan_remote_locks()

        # Startup reconciliation: sync Supabase lock state with local git
        last_modified = self._reconcile()

        # Short grace window after startup where a missing heartbeat should
        # not immediately trigger shutdown. This avoids a race where the
        # extension spawns the watcher and the heartbeat file is created
        # a few milliseconds later.
        startup_time = time.time()

        # Normalize parent detection method if not provided by caller. This
        # ensures logs can state how the parent PID was inferred.
        if parent_method is None:
            try:
                # If VSCODE_PID matches the provided parent_pid, mark accordingly
                vspid = os.getenv("VSCODE_PID")
                if (
                    vspid
                    and vspid.isdigit()
                    and parent_pid
                    and int(vspid) == int(parent_pid)
                ):
                    parent_method = "vscode_pid"
                elif os.getenv("PYCHARM_HOSTED") == "1":
                    parent_method = "pycharm_hosted"
                else:
                    detected_pid, detected_method = self._get_parent_ide_pid()
                    if detected_method:
                        parent_method = detected_method
                    else:
                        parent_method = "unknown"
            except Exception:
                parent_method = "unknown"

        try:
            while True:
                try:
                    now_ts = time.time()
                    if (
                        now_ts - last_pid_heartbeat
                        >= _PID_FILE_HEARTBEAT_INTERVAL_SECONDS
                    ):
                        last_pid_heartbeat = now_ts
                        self._touch_pid_heartbeat()

                    # Parent process liveness check every 2 seconds
                    # (faster zombie detection)
                    if (_safe_now() - last_parent_check).total_seconds() > 2:
                        last_parent_check = _safe_now()

                        # Soft-stop request support: if a .stop_request file is
                        # present, the watcher should perform a graceful
                        # shutdown instead of being forcibly killed.
                        try:
                            stop_file = _state_path(".stop_request")
                            if os.path.exists(stop_file):
                                try:
                                    with open(stop_file, "r", encoding="utf-8") as sf:
                                        txt = sf.read().strip()
                                except Exception:
                                    txt = ""

                                # Determine this watcher's PID (actual running pid)
                                try:
                                    actual_pid = self._read_pid() or os.getpid()
                                except Exception:
                                    actual_pid = os.getpid()

                                matched = False

                                # TOKEN:<token> takes precedence
                                if txt.startswith("TOKEN:"):
                                    requested_token = txt.split(":", 1)[1]
                                    try:
                                        my_token = self._get_session_token()
                                    except Exception:
                                        my_token = None
                                    if (
                                        requested_token
                                        and my_token
                                        and requested_token == my_token
                                    ):
                                        matched = True
                                elif txt.startswith("PID:"):
                                    try:
                                        requested_pid = int(txt.split(":", 1)[1])
                                        if requested_pid in (actual_pid, os.getpid()):
                                            matched = True
                                    except Exception:
                                        matched = False
                                else:
                                    # Backwards-compatible numeric-only payload
                                    try:
                                        if txt:
                                            requested_pid_opt = int(txt)
                                            if requested_pid_opt in (
                                                actual_pid,
                                                os.getpid(),
                                            ):
                                                matched = True
                                    except Exception:
                                        matched = False

                                if matched:
                                    logger.info(
                                        (
                                            "Stop request detected (%s). "
                                            "Initiating graceful shutdown."
                                        ),
                                        stop_file,
                                    )
                                    # Do NOT remove the stop_file here. The IDE
                                    # extension needs to see it after the process
                                    # exits to avoid an automatic restart.
                                    # The next watcher startup (via daemon_start)
                                    # will clean it up.
                                    self._graceful_shutdown(reason="stop_requested")
                                    return
                        except Exception as exc:
                            # Best-effort - don't crash the watcher over the stop file
                            logger.debug("Stop-request polling failed: %s", exc)

                        # VSCode heartbeat support: if the heartbeat stops updating,
                        # treat it as IDE/window termination and shut down.
                        # NOTE: Check heartbeat even when an OS-level parent monitor
                        # exists. Some IDE reloads may not terminate the parent PID
                        # but will stop the extension/heartbeat; checking the
                        # heartbeat makes the watcher more robust to fast reloads.
                        if self._heartbeat_file:
                            try:
                                logger.debug(
                                    "Heartbeat check: file=%s exists=%s",
                                    self._heartbeat_file,
                                    os.path.exists(self._heartbeat_file),
                                )
                                reason = self._heartbeat_should_shutdown(startup_time)
                                if reason:
                                    self._graceful_shutdown(reason=reason)
                                    return
                            except Exception as e:
                                logger.debug("Heartbeat check exception: %s", e)

                        # Parent diagnostics are useful during debugging but too noisy
                        # for normal collab.log operation, so keep them at DEBUG.
                        parent_alive = (
                            self._is_process_alive(self._parent_pid)
                            if self._parent_pid
                            else False
                        )
                        parent_name = "unknown"
                        if self._parent_pid:
                            try:
                                name, _ = self._get_process_info_local(self._parent_pid)
                                if name:
                                    parent_name = name
                            except Exception:
                                pass

                        # Track WMIC resolution failures for zombie process detection
                        if parent_name == "unknown":
                            _parent_name_unknown_streak += 1
                            # First transient failure: log at DEBUG
                            # to avoid noisy warnings
                            if (
                                _last_known_parent_name
                                and _parent_name_unknown_streak == 1
                            ):
                                logger.debug(
                                    (
                                        "Parent PID %d name no longer resolvable "
                                        "(was '%s'). Streak: %d"
                                    ),
                                    self._parent_pid,
                                    _last_known_parent_name,
                                    _parent_name_unknown_streak,
                                )
                            # Escalate to WARNING on the second consecutive failure
                            elif (
                                _last_known_parent_name
                                and _parent_name_unknown_streak == 2
                            ):
                                logger.warning(
                                    (
                                        "Parent PID %d name unresolvable for %d "
                                        "consecutive checks (was '%s'). May indicate "
                                        "IDE is shutting down."
                                    ),
                                    self._parent_pid,
                                    _parent_name_unknown_streak,
                                    _last_known_parent_name,
                                )
                        else:
                            if _parent_name_unknown_streak > 0:
                                logger.info(
                                    (
                                        "Parent PID %d name resolved again as '%s'. "
                                        "Resetting streak."
                                    ),
                                    self._parent_pid,
                                    parent_name,
                                )
                            _parent_name_unknown_streak = 0
                            _last_known_parent_name = parent_name

                        # If parent is reported alive but name has been
                        # unresolvable for 2+ checks,
                        # treat it as a zombie process and shut down
                        # (2 checks @ 2s interval = 4s max wait)
                        if parent_alive and _parent_name_unknown_streak >= 2:
                            parent_name_str = _last_known_parent_name or "unknown"
                            logger.info(
                                (
                                    "Parent process %s (PID: %d) confirmed "
                                    "terminated after %d unresolvable checks. "
                                    "Initiating shutdown."
                                ),
                                parent_name_str,
                                self._parent_pid,
                                _parent_name_unknown_streak,
                            )
                            logger.info(
                                (
                                    "Parent PID %d name unresolvable for %d "
                                    "consecutive checks — treating as terminated. "
                                    "Shutting down..."
                                ),
                                self._parent_pid,
                                _parent_name_unknown_streak,
                            )
                            # Console printing is redundant with logging; keep it in
                            # the logs only to avoid duplicate terminal lines.
                            self._graceful_shutdown()
                            return

                        current_ppid = os.getppid()

                        # DEBUG: Always log the comparison
                        logger.debug(
                            "adoption check: initial=%d current=%d match=%s",
                            self._initial_ppid,
                            current_ppid,
                            current_ppid == self._initial_ppid,
                        )

                        # Check if adopted by a new parent (original parent died)
                        if current_ppid != self._initial_ppid:
                            logger.info(
                                (
                                    "Detected adoption by new parent (was %d, now %d). "
                                    "Original parent died. Shutting down..."
                                ),
                                self._initial_ppid,
                                current_ppid,
                            )
                            # avoid printing duplicate messages to console
                            self._graceful_shutdown()
                            return

                        # Resolve immediate parent process name for clearer logs
                        immediate_parent_name = None
                        try:
                            if current_ppid:
                                immediate_parent_name, _ = self._get_process_info_local(
                                    current_ppid
                                )
                        except Exception:
                            immediate_parent_name = None

                        # Include detection method for clarity
                        if self._parent_pid:
                            logger.debug(
                                (
                                    "Parent check — detected IDE: %s (PID: %s) via=%s "
                                    "alive=%s; immediate parent: %s (PID: %d)"
                                ),
                                parent_name or "unknown",
                                self._parent_pid,
                                parent_method or "unknown",
                                parent_alive,
                                immediate_parent_name or "unknown",
                                current_ppid,
                            )
                        else:
                            logger.debug(
                                (
                                    "Parent check — immediate parent: %s (PID: %d) "
                                    "via=%s alive=%s"
                                ),
                                immediate_parent_name or "unknown",
                                current_ppid,
                                parent_method or "unknown",
                                parent_alive,
                            )

                        # Check if we have a parent PID and it's dead
                        if self._parent_pid:
                            if not self._is_process_alive(self._parent_pid):
                                logger.info(
                                    "Parent process (PID: %d) terminated. "
                                    "Shutting down...",
                                    self._parent_pid,
                                )
                                # Avoid duplicate console prints;
                                # logging is authoritative
                                self._graceful_shutdown()
                                return
                        else:
                            # No explicit parent PID - check for orphan status
                            current_ppid = os.getppid()
                            # On Windows, orphaned processes may get
                            # adopted by system processes
                            # On Unix, they get adopted by init (PID 1)
                            if sys.platform == "win32":
                                # Windows: check if adopted by a low-PID system process
                                if (
                                    current_ppid <= 4
                                ):  # System, smss.exe, csrss.exe, etc.
                                    logger.info(
                                        (
                                            "Detected orphaned watcher (adopted "
                                            "by system PID: %d). "
                                            "Shutting down..."
                                        ),
                                        current_ppid,
                                    )
                                    # Avoid printing to console redundantly
                                    self._graceful_shutdown()
                                    return
                            else:
                                # Unix: check if adopted by init
                                if current_ppid == 1:
                                    logger.info(
                                        (
                                            "Detected orphaned watcher (adopted "
                                            "by init). Shutting down..."
                                        ),
                                    )
                                    # Avoid printing to console redundantly
                                    self._graceful_shutdown()
                                    return

                    out, git_ok = self._get_modified_and_unpushed_files()
                    current_modified = set(out)

                    if current_modified != last_modified:
                        last_change_time = _safe_now()
                        new_files = current_modified - last_modified
                        if new_files:
                            logger.info("Detected local changes: %s", list(new_files))
                            branch = self._get_current_branch()
                            ok, failed, msg = self.acquire_multiple(
                                list(new_files),
                                branch_name=branch,
                                reason="Auto-Watch Sync",
                            )
                            if not ok:
                                logger.warning("⚠️ CONFLICT ALERT: %s", msg)
                            # Record acquisition times for minimum-hold enforcement
                            now = _safe_now()
                            for fp in new_files:
                                self._lock_acquired_at[fp] = now

                        released = last_modified - current_modified
                        if released and git_ok:
                            # Enforce minimum lock hold time: keep locks that were
                            # acquired too recently to avoid rapid acquire/release
                            # cycles when git status is transient.
                            keep = set()
                            for fp in released:
                                acquired = self._lock_acquired_at.get(fp)
                                if acquired is not None:
                                    age = (_safe_now() - acquired).total_seconds()
                                    if age < _min_auto_lock_hold_seconds():
                                        logger.debug(
                                            "⏳ [KEPT] %s — lock is only %ds old "
                                            "(< %ds minimum); deferring auto-release",
                                            fp,
                                            int(age),
                                            _min_auto_lock_hold_seconds(),
                                        )
                                        keep.add(fp)
                            actual_releases = released - keep
                            if actual_releases:
                                ok, count, _ = self.release_multiple(
                                    list(actual_releases)
                                )
                                if ok and count > 0:
                                    logger.info(
                                        "🔓 [RELEASED] %d file(s) released", count
                                    )
                            # Keep young locks in last_modified so they are not
                            # re-acquired on the next iteration.
                            last_modified = current_modified | keep
                        elif released:
                            logger.warning(
                                "Skipping release of %d lock(s) — "
                                "git status snapshot failed; "
                                "locks preserved until next successful sync",
                                len(released),
                            )
                            last_modified = current_modified
                        else:
                            last_modified = current_modified
                    else:
                        # Idle timeout
                        idle = _safe_now() - last_change_time
                        if timeout_mins > 0 and idle > timedelta(minutes=timeout_mins):
                            logger.info(
                                "Watcher timed out after %dm inactivity.", timeout_mins
                            )
                            break

                    # -----------------------------------------------------------------
                    # Layer 2 — Worktree-validity self-check (IDE-agnostic)
                    #
                    # Periodically verify the project root is still a valid,
                    # registered Git worktree.  If the worktree has been
                    # removed / pruned / invalidated, self-exit so the
                    # directory can be cleaned up.  This is defense-in-depth
                    # that guarantees ``git worktree remove`` always reaps
                    # the watcher, independent of IDE extension or PID
                    # binding.
                    # -----------------------------------------------------------------
                    if _WORKTREE_VALIDITY_CHECK_INTERVAL_SECONDS > 0:
                        # Layer 3 (#168) — prompt worktree-gone reap. A cheap
                        # per-iteration existence check: when the folder or its
                        # ``.git`` marker has vanished (deleted via OS file
                        # explorer or ``git worktree remove``), confirm with the
                        # authoritative validity check and self-exit within one
                        # poll interval instead of waiting for the ~60s Layer-2
                        # cycle — releasing the directory handle promptly so the
                        # folder can be deleted (critical on Windows, where a
                        # live watcher pins the ``.venv``).
                        _root_present = os.path.isdir(_PROJECT_ROOT) and os.path.exists(
                            os.path.join(_PROJECT_ROOT, ".git")
                        )
                        if not _root_present and not self._verify_worktree_valid():
                            logger.info(
                                "Worktree %s no longer present. Shutting down...",
                                _PROJECT_ROOT,
                            )
                            self._graceful_shutdown(reason="worktree_gone")
                            return

                        _now_ts = time.time()
                        if (
                            _now_ts - _last_worktree_check
                            >= _WORKTREE_VALIDITY_CHECK_INTERVAL_SECONDS
                        ):
                            _last_worktree_check = _now_ts
                            try:
                                _valid = self._verify_worktree_valid()
                                if not _valid:
                                    logger.info(
                                        "Worktree %s is no longer valid. "
                                        "Shutting down...",
                                        _PROJECT_ROOT,
                                    )
                                    self._graceful_shutdown(reason="worktree_invalid")
                                    return
                            except Exception as _exc:
                                logger.debug("Worktree validity check failed: %s", _exc)

                    time.sleep(interval)
                except Exception as e:
                    logger.error("Error in watcher loop: %s", e, exc_info=True)
                    time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Watcher stopped by user.")
        finally:
            self._graceful_shutdown()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _verify_worktree_valid(self) -> bool:
        """Return True when the project root is a valid, registered Git worktree.

        Layer 2 defense-in-depth: if the worktree has been removed, pruned, or its
        ``.git`` gitdir no longer resolves, this method returns False so the watcher can
        self-terminate and free the directory handle.
        """
        try:
            # Fast path: does .git exist and point to a real gitdir?
            _git_file = os.path.join(_PROJECT_ROOT, ".git")
            if not os.path.exists(_git_file):
                logger.debug("Worktree .git file missing: %s", _git_file)
                return False
            # ``git rev-parse --is-inside-work-tree`` confirms both that git
            # is available and that the directory is a worktree.
            result = safe_subprocess.capture(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=_PROJECT_ROOT,
            )
            if not result.ok:
                logger.debug(
                    "rev-parse --is-inside-work-tree failed (rc=%d): %s",
                    result.returncode,
                    result.stderr.decode(errors="replace").strip(),
                )
                return False
            out = result.stdout.decode(errors="replace").strip()
            if out.lower() != "true":
                logger.debug("Not inside a worktree: %s", _PROJECT_ROOT)
                return False
            # Also verify the gitdir resolves (worktree .git is a file
            # pointing at the real gitdir).
            try:
                with open(_git_file, "r", encoding="utf-8") as _gf:
                    _content = _gf.read().strip()
                if _content.startswith("gitdir:"):
                    _linked = _content.split(":", 1)[1].strip()
                    if not os.path.exists(_linked):
                        logger.debug(
                            "Worktree gitdir link broken: %s -> %s",
                            _git_file,
                            _linked,
                        )
                        return False
            except Exception:
                # If we can't read the gitdir link, fall through to the
                # rev-parse result (which already passed).
                pass
            return True
        except Exception as exc:
            logger.debug("Worktree validity check raised: %s", exc)
            # Fail open — don't shut down on transient errors.
            return True

    def _register_signal_handlers(self) -> None:
        """Register cleanup handlers for clean shutdown."""
        logger.debug("_register_signal_handlers called")

        if os.getenv("COLLAB_TEST_MODE") != "1":
            logger.debug("Registering atexit handler")
            atexit.register(self._graceful_shutdown)

        def _handle_signal(signum, frame):
            logger.debug("Signal handler called: signum=%d", signum)
            logger.info("Received signal %d, shutting down...", signum)
            try:
                self._graceful_shutdown(reason=f"signal_{signum}")
            except Exception:
                logger.exception("Error during graceful shutdown for signal %s", signum)
            sys.exit(0)

        if sys.platform != "win32":
            logger.debug("Registering SIGTERM handler")
            signal.signal(signal.SIGTERM, _handle_signal)
        logger.debug("Registering SIGINT handler")
        signal.signal(signal.SIGINT, _handle_signal)

        # Windows-specific handlers: SIGBREAK and a console control handler.
        # These improve the chance that we run graceful shutdown when the
        # extension host or window closes (CTRL_CLOSE_EVENT, SHUTDOWN, etc.).
        if sys.platform == "win32":
            if hasattr(signal, "SIGBREAK"):
                try:
                    logger.debug("Registering SIGBREAK handler")
                    signal.signal(signal.SIGBREAK, _handle_signal)
                except Exception as _e:
                    logger.debug("Failed to register SIGBREAK handler: %s", _e)

            try:
                import ctypes
                from ctypes import wintypes

                HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

                def _console_handler(dwCtrlType):
                    try:
                        logger.debug("Console control event: %s", dwCtrlType)
                        # Attempt graceful shutdown
                        try:
                            self._graceful_shutdown(reason=f"console_ctrl_{dwCtrlType}")
                        except Exception:
                            logger.exception(
                                "Exception during graceful shutdown in console handler"
                            )
                    except Exception:
                        logger.exception("Exception in console handler")
                    return True

                ctypes.windll.kernel32.SetConsoleCtrlHandler(
                    HandlerRoutine(_console_handler), True
                )
                logger.debug("Registered Windows console ctrl handler")
            except Exception as _e:
                logger.debug("Failed to register console ctrl handler: %s", _e)

            logger.debug("Signal handlers registered")

    def _start_parent_monitor_thread(self) -> None:
        """Start a background thread that waits on the parent process handle (Windows).

        This uses OpenProcess + WaitForSingleObject so we can be notified the instant
        the parent process exits, avoiding fragile polling or WMIC queries. The thread
        is daemonized so it won't block shutdown.
        """
        if sys.platform != "win32":
            return
        parent = getattr(self, "_parent_pid", None)
        if not parent:
            return
        try:
            import ctypes

            # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
            SYNCHRONIZE = 0x00100000
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            desired_access = SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION

            handle = ctypes.windll.kernel32.OpenProcess(
                desired_access, False, int(parent)
            )
            if not handle:
                try:
                    err = ctypes.windll.kernel32.GetLastError()
                except Exception:
                    err = None
                logger.warning(
                    "%s",
                    ParentMonitorError(
                        f"cannot monitor parent PID {parent}",
                        detail=f"OpenProcess err={err}",
                    ),
                )
                return

            def _waiter(hndl, ppid):
                try:
                    INFINITE = 0xFFFFFFFF
                    res = ctypes.windll.kernel32.WaitForSingleObject(hndl, INFINITE)
                    logger.info(
                        (
                            "Parent PID %s handle signaled "
                            "(WaitForSingleObject returned %s). "
                            "Initiating shutdown."
                        ),
                        ppid,
                        res,
                    )
                    try:
                        ctypes.windll.kernel32.CloseHandle(hndl)
                    except Exception as exc:
                        logger.debug("CloseHandle failed for parent monitor: %s", exc)
                    # mark monitor as stopped to avoid races
                    self._parent_monitor_started = False
                    self._parent_monitor_handle = None
                    self._parent_monitor_thread = None
                    # Trigger graceful shutdown with a reason
                    try:
                        self._graceful_shutdown(reason=f"parent_exit_{ppid}")
                    except Exception:
                        logger.exception("Error while shutting down after parent exit")
                except Exception as e:
                    logger.debug("Parent monitor waiter failed: %s", e)

            th = threading.Thread(
                target=_waiter, args=(handle, int(parent)), daemon=True
            )
            # Record diagnostics before starting
            self._parent_monitor_handle = handle
            self._parent_monitor_started = True
            self._parent_monitor_thread = th
            logger.info("Parent monitor listening for parent PID %s", parent)
            th.start()
        except Exception as e:
            logger.debug("Failed to start parent monitor thread: %s", e)
            self._parent_monitor_started = False
            self._parent_monitor_handle = None
            self._parent_monitor_thread = None

    def _graceful_shutdown(self, reason: Optional[str] = None) -> None:
        """Cleanup the local daemon state on shutdown.

        IMPORTANT: This handler strictly DOES NOT release any Supabase locks.
        Locks are preserved to ensure they persist across IDE restarts and
        terminal sessions.  They are only released by:

        * The next watcher reconciliation pass (stale-file cleanup, with a
          minimum hold time to prevent thrashing).
        * The VS Code extension's smart shutdown on deactivation (which uses
          the same ``git status`` + ``git diff`` criteria as the watcher).
        * The pre-push hook (when code is pushed).
        * Explicit user action (``collab release`` / ``release-all``).
        """
        logger.debug("_graceful_shutdown called (reason=%s)", reason)

        # Flush immediately so we see this even if process dies
        for handler in logging.getLogger().handlers:
            try:
                handler.flush()
            except Exception:
                pass

        if getattr(self, "_shutdown_done", False):
            logger.debug("shutdown already done, returning (reason=%s)", reason)
            return
        self._shutdown_done = True

        # Never touch real Supabase OR local PID file in test mode
        if os.getenv("COLLAB_TEST_MODE") == "1":
            logger.debug("COLLAB_TEST_MODE=1 - skipping real shutdown actions")
            return

        # Log shutdown start (clear, stepwise messages)
        if reason:
            logger.info(
                (
                    "Shutdown initiated — received shutdown signal (%s). "
                    "Beginning graceful shutdown."
                ),
                reason,
            )
        else:
            logger.info(
                (
                    "Shutdown initiated — received shutdown signal. "
                    "Beginning graceful shutdown."
                )
            )

        # Flush again
        for handler in logging.getLogger().handlers:
            try:
                handler.flush()
            except Exception:
                pass

        # Log kept locks (matching pycharm_watcher format)
        n_kept = 0
        enumeration_ok = True
        try:
            active_locks = self.active()
            logger.debug(
                "Graceful shutdown: fetched %d active locks from Supabase. "
                "My dev ID: %s",
                len(active_locks),
                self.developer_id,
            )
            my_locks = [lk for lk in active_locks if self._lock_owned_by_me(lk)]
            for lock in sorted(my_locks, key=lambda x: x.get("file_path", "")):
                fp = lock.get("file_path", "")
                if fp:
                    n_kept += 1
                    logger.info(
                        "🔒 [PRESERVED] %s — still has local edits, lock preserved", fp
                    )
        except Exception as e:
            enumeration_ok = False
            logger.error(
                "Exception while enumerating active locks during shutdown: %s", e
            )

        if enumeration_ok:
            logger.info(
                "Shutdown complete. Preserved %d lock(s); released 0 lock(s).", n_kept
            )
            shutdown_msg = (
                f"Shutdown complete. Preserved {n_kept} lock(s); " "released 0 lock(s)."
            )
        else:
            logger.warning(
                "Shutdown complete. Could not verify lock count; "
                "locks unchanged in database."
            )
            shutdown_msg = (
                "Shutdown complete. Could not verify lock count; "
                "locks unchanged in database."
            )

        # Emit a concise stdout marker for the extension to detect.
        try:
            print(shutdown_msg, flush=True)
        except Exception:
            pass

        # Write shutdown marker early into the per-workspace state dir so
        # external tools can detect shutdown without placing transient files
        # inside the repository working tree.
        try:
            shutdown_file = _state_path(".shutdown_complete")
            with open(shutdown_file, "w") as f:
                # Write -1 sentinel when enumeration failed so that IDE
                # extensions can distinguish "0 locks held" (normal) from
                # "could not verify" (transient service error).
                f.write(f"{-1 if not enumeration_ok else n_kept}\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            logger.info("Wrote shutdown marker to %s", shutdown_file)
            # Remove any stray shutdown/startup markers that may exist in the
            # repository runtime root from older runs.
            try:
                repo_shutdown = os.path.join(_COLLAB_ROOT, ".shutdown_complete")
                repo_summary = os.path.join(_COLLAB_ROOT, ".startup_summary.json")
                for p in (repo_shutdown, repo_summary):
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                            logger.info("Removed stray runtime marker in repo: %s", p)
                    except Exception as _e:
                        logger.debug("Failed to remove stray repo marker %s: %s", p, _e)
            except Exception:
                pass
        except Exception as _e:
            logger.debug("Failed to write shutdown marker early: %s", _e)

        # Remove PID file with logging (matching pycharm_watcher)
        for _attempt in range(3):
            try:
                if os.path.exists(PID_FILE):
                    os.remove(PID_FILE)
                    logger.info("Removed PID file: %s", PID_FILE)
                break
            except OSError:
                if _attempt < 2:
                    time.sleep(0.1)
                pass

        # Flush all logging handlers to ensure shutdown logs are written
        # Flush handlers attached to the 'collab' logger (file handlers)
        try:
            collab_logger = logging.getLogger("collab")
            for handler in getattr(collab_logger, "handlers", []):
                try:
                    handler.flush()
                except Exception:
                    pass
        except Exception:
            pass

        # Also flush and fsync file-backed handlers as a best-effort so that
        # logs are persisted to disk even if the parent IDE reloads quickly.
        try:
            # First, handle collab-specific handlers
            collab_logger = logging.getLogger("collab")
            for handler in getattr(collab_logger, "handlers", []):
                try:
                    handler.flush()
                except Exception:
                    pass
                try:
                    stream = getattr(handler, "stream", None)
                    if stream and hasattr(stream, "fileno"):
                        try:
                            os.fsync(stream.fileno())
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        # Then root handlers
        try:
            for handler in logging.getLogger().handlers:
                try:
                    handler.flush()
                except Exception:
                    pass
                try:
                    stream = getattr(handler, "stream", None)
                    if stream and hasattr(stream, "fileno"):
                        try:
                            os.fsync(stream.fileno())
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        # Ensure all logging resources are flushed and closed before exit.
        try:
            logging.shutdown()
        except Exception:
            pass

        # Ensure stdout is flushed for console output
        try:
            sys.stdout.flush()
        except Exception:
            pass

        # Small delay to ensure file writes complete before process exit
        time.sleep(0.5)

    def _reconcile(self) -> set:
        """Sync Supabase locks with local git status and upstream state."""
        try:
            modified_files, _git_ok = self._get_modified_and_unpushed_files()
            git_modified = set(modified_files)
        except Exception as e:
            logger.error("Error identifying modified files (skipping reconcile): %s", e)
            # DANGEROUS: Returning set() here would cause it to think we should
            # release EVERYTHING we currently have. Instead, return our currently
            # known locks so reconciliation essentially becomes a no-op for this cycle.
            try:
                active = self.active()
                return {lk["file_path"] for lk in active if self._lock_owned_by_me(lk)}
            except LockServiceUnavailableError:
                return set()
            except Exception:
                return set()

        try:
            active = self.active()
            my_locks = {lk["file_path"] for lk in active if self._lock_owned_by_me(lk)}
            # Build lock_map for token checking
            lock_map: dict[str, dict] = {}
            # Also build a map for dev_other locks so the minimum-hold check
            # can look up acquired_at for agent-owned locks.
            dev_other_map: dict[str, dict] = {}
            # Locks held by THIS developer under a *different* identity. For the
            # background watcher (which runs as the human) these are the same
            # developer's AI-agent locks. We must never downgrade or fight them.
            dev_other_locked: set = set()
            for lk in active:
                fp = lk.get("file_path", "")
                if not fp:
                    continue
                if self._lock_owned_by_me(lk):
                    lock_map[fp] = lk
                elif lk.get("developer_id") == self.developer_id:
                    dev_other_locked.add(fp)
                    dev_other_map[fp] = lk
        except LockServiceUnavailableError as e:
            logger.error("Error getting Supabase locks (service unavailable): %s", e)
            return git_modified
        except Exception as e:
            logger.error("Error getting Supabase locks: %s", e)
            return git_modified

        # Populate _lock_acquired_at from existing active locks so the
        # minimum-hold check works correctly across watcher restarts.
        for lk in active:
            fp = lk.get("file_path", "")
            acquired_str = lk.get("acquired_at")
            if fp and acquired_str:
                try:
                    self._lock_acquired_at[fp] = (
                        datetime.fromisoformat(acquired_str)
                        .astimezone()
                        .replace(tzinfo=None)
                    )
                except (ValueError, TypeError):
                    pass

        # Calculate lock categories. ``missing`` excludes files already held by
        # this developer under another (agent) identity so the human watcher does
        # not downgrade agent attribution during bulk reconcile. Explicit acquire
        # (e.g. pre-commit) may still re-own same-developer agent locks via RPC.
        stale = my_locks - git_modified
        missing = git_modified - my_locks - dev_other_locked
        still_valid = my_locks & git_modified

        # Track locks that were kept because they're too young to release.
        # These are included in the return value so the main watch loop can
        # continue to evaluate them on subsequent iterations.
        kept_young: set = set()

        # Clean up this developer's agent locks for work that is no longer in
        # progress (e.g. after a push). Keeps the dashboard tidy without an agent
        # having to explicitly release every file it touched.
        # Enforce minimum hold time to avoid releasing agent locks that were just
        # acquired (e.g. an AI agent created a lock seconds before a watcher restart).
        dev_other_stale = dev_other_locked - git_modified
        if dev_other_stale:
            for fp in sorted(dev_other_stale):
                # Check minimum hold time against stored acquired_at.
                # Use dev_other_map (not lock_map) because agent locks
                # are tracked separately.
                lock = dev_other_map.get(fp, {})
                acquired_str = lock.get("acquired_at")
                if acquired_str:
                    try:
                        acq_dt = (
                            datetime.fromisoformat(acquired_str)
                            .astimezone()
                            .replace(tzinfo=None)
                        )
                        age = (_safe_now() - acq_dt).total_seconds()
                        if age < _min_auto_lock_hold_seconds():
                            kept_young.add(fp)
                            logger.debug(
                                "⏳ [KEPT] %s — agent lock is only %ds old "
                                "(< %ds minimum); skipping auto-release",
                                fp,
                                int(age),
                                _min_auto_lock_hold_seconds(),
                            )
                            continue
                    except (ValueError, TypeError):
                        pass
                logger.info(
                    "🔓 [STALE-RELEASED] %s — agent lock for clean file, releasing",
                    fp,
                )
                self._release_developer_scope(fp)

        # Count categories for summary
        current_token = self._get_session_token()
        resumed_locks = []
        refreshed_locks = []
        multi_session_locks = []

        for fp in sorted(still_valid):
            lock = lock_map.get(fp, {})
            stored_token = lock.get("lock_token", "")

            if stored_token and stored_token != current_token:
                if self._is_same_machine_token(stored_token):
                    resumed_locks.append(fp)
                else:
                    multi_session_locks.append(fp)
            elif stored_token == current_token:
                resumed_locks.append(fp)
            else:
                refreshed_locks.append(fp)

        # Calculate counts for summary
        n_released = len(stale)
        n_newly_locked = len(missing)
        n_readopted = len(resumed_locks)
        n_refreshed = len(refreshed_locks)
        n_multi = len(multi_session_locks)

        # Only log start message if there's work to do
        if any([n_released, n_newly_locked, n_readopted, n_refreshed, n_multi]):
            logger.debug("Starting lock reconciliation...")

        # Process stale locks — release immediately because git status
        # confirms the file is clean.  Unlike agent locks (dev_other_stale),
        # the developer's own watcher has authoritative knowledge of the
        # working tree.  Delaying here (e.g. via a minimum hold time) would
        # leave post-merge / post-pull files locked for minutes (#150, #151).
        if stale:
            for fp in sorted(stale):
                logger.info(
                    "🔓 [STALE-RELEASED] %s — locked but file is now clean,"
                    " releasing",
                    fp,
                )
            self.release_multiple(list(stale))
            n_released = len(stale)

        # Process RESUMED locks: use direct table update (preserves acquired_at)
        # This prevents the timer from resetting when switching IDEs
        if resumed_locks:
            for fp in sorted(resumed_locks):
                logger.info("🔒 [RESUMED] %s — lock re-adopted from this machine", fp)
                try:
                    # Use direct update to ONLY change lock_token, NOT acquired_at
                    client = self._require_client()
                    update_q = (
                        client.table("file_locks")
                        .update({"lock_token": current_token})
                        .eq("file_path", fp)
                        .eq("developer_id", self.developer_id)
                    )
                    update_q = self._apply_agent_scope(update_q)
                    update_q.execute()
                except Exception:
                    logger.debug("Failed to update lock_token for %s (non-fatal)", fp)

        # Process multi-session locks (different machine) - just log, don't touch
        if multi_session_locks:
            for fp in sorted(multi_session_locks):
                lock = lock_map.get(fp, {})
                stored_token = lock.get("lock_token", "")
                logger.warning(
                    (
                        "⚠️ [MULTI-SESSION] %s — token mismatch (stored: %s..., "
                        "current: %s...). "
                        "Lock left untouched — use 'collab release-all' "
                        "if stale."
                    ),
                    fp,
                    stored_token[:8] if stored_token else "none",
                    current_token[:8],
                )

        # Process REFRESHED locks (no stored token) - use acquire RPC
        if refreshed_locks:
            now = _safe_now()
            for fp in refreshed_locks:
                self._lock_acquired_at[fp] = now
                logger.info("🔒 [REFRESHED] %s — token refreshed", fp)
            branch = self._get_current_branch()
            self.acquire_multiple(
                list(refreshed_locks), branch_name=branch, reason="Auto-Watch Sync"
            )

        # Process NEW locks (missing) - use acquire RPC
        if missing:
            now = _safe_now()
            for fp in missing:
                self._lock_acquired_at[fp] = now
            branch = self._get_current_branch()
            self.acquire_multiple(
                list(missing), branch_name=branch, reason="Auto-Watch Sync"
            )

        # Always log startup reconciliation summary for notification detection
        # Ensure a clear stdout marker so the VS Code extension (which
        # monitors the watcher's stdout) reliably detects startup completion.
        print("Startup reconciliation complete.")
        logger.info("Startup reconciliation complete.")
        if n_readopted:
            logger.info("  Re-adopted: %d lock(s)", n_readopted)
        if n_released:
            logger.info("  Stale released: %d lock(s)", n_released)
        if n_newly_locked:
            logger.info("  Newly locked: %d file(s)", n_newly_locked)
        if n_multi:
            logger.info("  Conflicts: %d file(s)", n_multi)
        if n_refreshed:
            logger.info("  Token refresh: %d lock(s)", n_refreshed)

        # Write startup summary to file for VSCode extension notification
        # Skip if silencing is requested (e.g., during tests)
        if os.environ.get("COLLAB_SILENT_DAEMON"):
            logger.debug("Skipping startup summary (COLLAB_SILENT_DAEMON set)")
            return git_modified | kept_young

        try:
            import json

            summary_file = _state_path(".startup_summary.json")
            summary_data = {
                "readopted": n_readopted,
                "stale_released": n_released,
                "newly_locked": n_newly_locked,
                "conflicts": n_multi,
                "refreshed": n_refreshed,
                "timestamp": time.time(),
            }
            with open(summary_file, "w") as f:
                json.dump(summary_data, f)

            # For backward compatibility with older extension instances that
            # expect `.startup_summary.json` inside the repository root,
            # also write a short-lived copy there. Schedule its removal after
            # a short grace period so the git tree is not polluted long-term.
            try:
                repo_summary = os.path.join(_COLLAB_ROOT, ".startup_summary.json")
                try:
                    with open(repo_summary, "w") as rf:
                        json.dump(summary_data, rf)
                except Exception as _e:
                    logger.debug("Failed to write repo startup summary: %s", _e)

                def _cleanup_repo_markers(paths, delay=30):
                    def _worker():
                        try:
                            time.sleep(delay)
                            for p in paths:
                                try:
                                    if os.path.exists(p):
                                        os.remove(p)
                                        _emit_log_resilient(
                                            logger,
                                            logging.INFO,
                                            "Removed stray repo marker: %s",
                                            p,
                                        )
                                except Exception:
                                    _emit_log_resilient(
                                        logger,
                                        logging.DEBUG,
                                        "Failed to remove stray repo marker: %s",
                                        p,
                                    )
                        except Exception:
                            pass

                    th = threading.Thread(target=_worker, daemon=True)
                    th.start()

                # Schedule removal of both startup and shutdown markers (if present)
                repo_shutdown = os.path.join(_COLLAB_ROOT, ".shutdown_complete")
                _cleanup_repo_markers([repo_summary, repo_shutdown], delay=30)
            except Exception:
                pass
        except Exception:
            pass

        return git_modified | kept_young

    @staticmethod
    def _run_git_status() -> Tuple[str, bool]:
        """Run git status --porcelain and return (output, ok).

        Returns:
            Tuple of (output, ok) where ``ok`` is False when git failed
            (timeout, non-zero exit, or subprocess error), signalling
            callers that the snapshot is unreliable and should not be
            used to release locks.
        """
        captured = safe_subprocess.capture(
            ["git", "status", "--porcelain"],
            policy="git",
            cwd=_PROJECT_ROOT,
            timeout=_GIT_STATUS_TIMEOUT_S,
        )
        if captured.timed_out:
            logger.warning(
                "git status --porcelain timed out after %ss; skipping status snapshot",
                _GIT_STATUS_TIMEOUT_S,
            )
            return "", False
        if not captured.ok:
            logger.warning(
                "git status --porcelain failed (exit code or subprocess error)"
            )
            return "", False
        # NOTE: Only trim surrounding newlines, never a full ``.strip()``.
        # ``git status --porcelain`` lines begin with a 2-column status field
        # (XY) whose first column is a space for worktree-only changes (e.g.
        # " M path"). A full strip would remove the leading space of the FIRST
        # line, shifting the fixed-width parse in ``_parse_git_status_path`` and
        # silently dropping the first character of that path.
        return safe_subprocess.decode_output(captured.stdout).strip("\r\n"), True

    @staticmethod
    def _git_ref_exists(ref: str) -> bool:
        """Return True when *ref* resolves in the project repository."""
        captured = safe_subprocess.capture(
            ["git", "rev-parse", "--verify", ref],
            policy="git",
            cwd=_PROJECT_ROOT,
            timeout=_GIT_REF_TIMEOUT_S,
        )
        return captured.ok and not captured.timed_out

    @classmethod
    def _resolve_lock_diff_base_ref(cls) -> Optional[str]:
        """Return git ref for unpushed work: @{u}, env override, or origin/main."""
        upstream = safe_subprocess.capture(
            [
                "git",
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{u}",
            ],
            policy="git",
            cwd=_PROJECT_ROOT,
            timeout=_GIT_REF_TIMEOUT_S,
        )
        if upstream.ok and not upstream.timed_out:
            if safe_subprocess.decode_output(upstream.stdout).strip():
                return "@{u}"

        override = os.getenv("COLLAB_LOCK_BASE_REF", "").strip()
        if override and cls._git_ref_exists(override):
            return override

        branch_capture = safe_subprocess.capture(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            policy="git",
            cwd=_PROJECT_ROOT,
            timeout=_GIT_REF_TIMEOUT_S,
        )
        if branch_capture.ok and not branch_capture.timed_out:
            branch = safe_subprocess.decode_output(branch_capture.stdout).strip()
            if branch and branch != "HEAD":
                remote_branch = f"origin/{branch}"
                if cls._git_ref_exists(remote_branch):
                    return remote_branch

        for candidate in ("origin/main", "origin/master"):
            if cls._git_ref_exists(candidate):
                return candidate
        return None

    @staticmethod
    def _paths_from_git_diff_name_status(range_spec: str) -> List[str]:
        """Parse ``git diff --name-status`` output into normalized path strings."""
        diff_capture = safe_subprocess.capture(
            ["git", "diff", "--name-status", range_spec],
            policy="git",
            cwd=_PROJECT_ROOT,
            timeout=_GIT_REF_TIMEOUT_S,
        )
        if not diff_capture.ok or diff_capture.timed_out:
            return []

        paths: List[str] = []
        diff_out = safe_subprocess.decode_output(diff_capture.stdout).strip()
        for line in diff_out.splitlines():
            raw = line.strip()
            if not raw:
                continue
            parts = raw.split(None, 1)
            if len(parts) != 2:
                continue
            payload = parts[1].strip()
            if "\t" in payload:
                payload = payload.split("\t")[-1].strip()
            if " -> " in payload:
                payload = payload.split(" -> ")[-1].strip()
            if payload and not payload.endswith("/"):
                paths.append(payload)
        return paths

    def _get_modified_and_unpushed_files(self) -> Tuple[List[str], bool]:
        """Return (files, git_ok) for dirty/unpushed-commit files.

        ``git_ok`` is False when the git status snapshot failed, signalling callers that
        the result is **not** a reliable picture of local state and should not be used
        to release locks.

        Includes dirty files from sibling git worktrees (``git worktree list``) so that
        concurrent edits across worktrees are not released as stale (#150).
        """
        modified = set()
        git_status_ok = True

        # 1. Get Dirty/Staged files
        try:
            out, git_status_ok = self._run_git_status()
            if out:
                for line in out.splitlines():
                    if len(line) > 3:
                        p = self._normalize_file_path(self._parse_git_status_path(line))
                        if p.endswith("/"):
                            continue
                        if not self._should_ignore_path(p):
                            modified.add(p)
        except Exception as e:
            logger.debug("Git status failed: %s", e)
            git_status_ok = False

        # 2. Committed-but-not-on-remote (upstream, origin/main, or base-ref override)
        try:
            base_ref = self._resolve_lock_diff_base_ref()
            if base_ref:
                if base_ref != "@{u}":
                    logger.debug(
                        "No upstream (@{u}); locking in-progress files vs %s",
                        base_ref,
                    )
                # Three-dot range: only commits reachable from HEAD but not base_ref
                # (#178). Two-dot @{u}..HEAD is an endpoint diff and phantom-locks
                # remote files when local main is behind upstream.
                range_spec = f"{base_ref}...HEAD"
                for path in self._paths_from_git_diff_name_status(range_spec):
                    norm = self._normalize_file_path(path)
                    if norm and not self._should_ignore_path(norm):
                        modified.add(norm)
        except Exception as exc:
            logger.debug("Git diff for in-progress files failed: %s", exc)

        # 3. Sibling worktree dirty files — prevent stale-lock release when
        #    another worktree is actively editing the same files (#150).
        try:
            sibling_dirty = self._get_sibling_worktree_dirty_files()
            if sibling_dirty:
                modified.update(sibling_dirty)
        except Exception as exc:
            logger.debug("Sibling worktree scan failed: %s", exc)

        return list(modified), git_status_ok

    def _get_sibling_worktree_dirty_files(self) -> set[str]:
        """Return dirty files from sibling git worktrees.

        Uses ``git worktree list --porcelain`` to discover sibling worktrees, then runs
        ``git status --porcelain`` in each.  Paths are normalised to project- relative
        form so they match the watcher's own file set.
        """
        sibling_files: set[str] = set()

        # Discover worktrees
        captured = safe_subprocess.capture(
            ["git", "worktree", "list", "--porcelain"],
            policy="git",
            cwd=_PROJECT_ROOT,
            timeout=10.0,
        )
        if not captured.ok or captured.timed_out:
            return sibling_files

        out = safe_subprocess.decode_output(captured.stdout)
        worktrees: list[tuple[str, str]] = []  # (path, branch)
        current_path = ""
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("worktree "):
                current_path = line[len("worktree ") :]
            elif line.startswith("branch ") and current_path:
                branch_ref = line[len("branch ") :]
                branch = branch_ref.replace("refs/heads/", "", 1)
                worktrees.append((current_path, branch))
                current_path = ""

        # Normalise our own root for comparison
        our_root = os.path.abspath(_PROJECT_ROOT).rstrip("\\/").lower()

        for wt_path, wt_branch in worktrees:
            wt_abs = os.path.abspath(wt_path).rstrip("\\/").lower()
            # Skip our own worktree
            if wt_abs == our_root:
                continue
            if not os.path.isdir(wt_path):
                continue

            try:
                wt_captured = safe_subprocess.capture(
                    ["git", "status", "--porcelain"],
                    policy="git",
                    cwd=wt_path,
                    timeout=_GIT_STATUS_TIMEOUT_S,
                )
                if not wt_captured.ok or wt_captured.timed_out:
                    continue
                wt_out = safe_subprocess.decode_output(wt_captured.stdout).strip("\r\n")
                for line in wt_out.splitlines():
                    if len(line) > 3:
                        p = self._normalize_file_path(self._parse_git_status_path(line))
                        if (
                            p
                            and not p.endswith("/")
                            and not self._should_ignore_path(p)
                        ):
                            sibling_files.add(p)
                            logger.debug(
                                "👥 [WORKTREE] %s — dirty in sibling worktree"
                                " '%s' (%s)",
                                p,
                                wt_branch,
                                wt_path,
                            )
            except Exception as exc:
                logger.debug("Failed to scan sibling worktree %s: %s", wt_path, exc)

        return sibling_files

    @staticmethod
    def _parse_git_status_path(line: str) -> str:
        """Extract file path from git status --porcelain, handling renames."""
        p = line[3:].strip()
        if " -> " in p:
            p = p.split(" -> ")[-1].strip()
        if p.startswith('"') and p.endswith('"'):
            p = p[1:-1]
            try:
                p = p.encode("utf-8").decode("unicode_escape")
            except Exception:
                pass
        return p

    @staticmethod
    def _should_ignore_path(path: str) -> bool:
        """Return True for paths the watcher should skip.

        Delegates to :func:`collab.path_filter.should_ignore_lock_path`, which
        also honors ``COLLAB_LOCK_IGNORE`` and a project ``.collabignore`` so
        transient scratch files never produce short-lived locks (#170).
        """
        return path_filter.should_ignore_lock_path(path, _PROJECT_ROOT)

    @staticmethod
    def _read_pid(*, strict: bool = False) -> Optional[int]:
        """Read daemon PID from the PID file.

        Supports two formats for backward compatibility:
        - Plain integer stored in `.daemon.pid` (legacy)
        - JSON object stored in `.daemon.pid` containing a numeric "pid" field

        Returns the pid as an int, or None if the file is missing or empty.
        When ``strict`` is True and the file exists but cannot be parsed, raises
        :class:`PidParseError`.
        """
        if not os.path.exists(PID_FILE):
            return None
        try:
            with open(PID_FILE, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            if not raw:
                return None
            # Try JSON first (richer metadata), fall back to int
            if raw.startswith("{"):
                try:
                    obj = json.loads(raw)
                    pid = obj.get("pid")
                    if isinstance(pid, int):
                        return pid
                except Exception:
                    logger.debug("PID file contains invalid JSON: %s", raw)
                    if strict:
                        raise PidParseError(
                            f"PID file at {PID_FILE} contains invalid JSON",
                            detail=raw[:200],
                        ) from None
                    return None
            # Fallback: plain integer
            return int(raw)
        except ValueError:
            logger.debug("PID file does not contain an integer: %s", PID_FILE)
            if strict:
                raise PidParseError(
                    f"PID file at {PID_FILE} does not contain a valid integer",
                ) from None
            return None
        except OSError as e:
            logger.debug("Could not read PID file %s: %s", PID_FILE, e)
            if strict:
                raise PidParseError(
                    f"Could not read PID file at {PID_FILE}",
                    detail=str(e),
                ) from e
            return None

    @staticmethod
    def _get_cmdline_for_pid(pid: int) -> Optional[str]:
        """Return the command-line string for a process, or None if unavailable.

        Uses psutil when available. If psutil is not installed or access fails, returns
        None which indicates we couldn't verify the cmdline.
        """
        # Prefer psutil when available (robust cross-platform). If unavailable,
        # fall back to lightweight platform-specific methods (procfs on Unix,
        # WMIC/tasklist on Windows) so we can verify PID command-lines even
        # in minimal environments.
        try:
            import psutil

            try:
                p = psutil.Process(pid)
                cmd = p.cmdline()
                if isinstance(cmd, (list, tuple)):
                    return " ".join(cmd)
                return str(cmd)
            except Exception:
                pass
        except Exception:
            # psutil not installed — continue to platform fallbacks
            pass

        return platform_probe.get_cmdline(pid)

    @staticmethod
    def _cmdline_matches_watcher(cmdline: str) -> bool:
        """Heuristic: return True if the command-line looks like our watcher.

        Matches supported watcher entrypoints, including legacy path-based invocations
        and the current module/CLI forms.
        """
        if not cmdline:
            return False
        s = cmdline.lower()
        return (
            "live_locks_watcher" in s
            or ("lock_client.py" in s and "watch" in s)
            or ("collab.core.lock_client" in s and "watch" in s)
            or ("collab" in s and "watch" in s)
        )

    @staticmethod
    def _extract_pid_file_from_cmdline(cmdline: str) -> Optional[str]:
        """Extract a --pid-file argument from cmdline when present.

        Returns the parsed value as-is (possibly quoted), or None when missing.
        """
        if not cmdline:
            return None
        # Match either:
        #   --pid-file VALUE
        #   --pid-file="VALUE"
        #   --pid-file='VALUE'
        m = re.search(r"--pid-file(?:=|\s+)(\"[^\"]+\"|'[^']+'|\S+)", cmdline)
        if not m:
            return None
        raw = m.group(1).strip()
        if (raw.startswith('"') and raw.endswith('"')) or (
            raw.startswith("'") and raw.endswith("'")
        ):
            raw = raw[1:-1]
        return raw

    def _cmdline_matches_current_pid_namespace(self, cmdline: str) -> bool:
        """Return True when a watcher cmdline belongs to this client's PID file scope.

        Rules:
        - If cmdline contains --pid-file, it must match current PID_FILE exactly.
        - If cmdline has no --pid-file (legacy watcher), only accept it for the
          default production PID file while *not* in test mode.
        """
        parsed = self._extract_pid_file_from_cmdline(cmdline)
        current = os.path.abspath(PID_FILE)
        default_pid = os.path.abspath(os.path.join(_COLLAB_ROOT, ".daemon.pid"))
        if parsed:
            try:
                return os.path.abspath(parsed) == current
            except Exception:
                return False
        # Legacy watcher without explicit namespace tag.
        if _is_test_mode():
            return False
        return current == default_pid

    @staticmethod
    def _write_pid(
        pid: int, parent_pid: Optional[int] = None, token: Optional[str] = None
    ) -> None:
        """Write daemon PID metadata to the PID file as JSON.

        Historically this file contained a plain integer.  Newer clients write a small
        JSON object with fields useful for diagnostics. The reader already supports both
        formats for backward compatibility.
        """
        meta = {
            "pid": int(pid),
            # Use _safe_now to accommodate tests that monkeypatch the module
            # level `datetime` symbol. Ensure the stored time is in UTC.
            "started_at": _safe_now().astimezone(timezone.utc).isoformat(),
            # Use a human-friendly entrypoint string so other tools can display
            # a concise description without reconstructing the full cmdline.
            "entrypoint": "python lock_client.py",
            "cmdline": " ".join([sys.executable] + sys.argv),
            "cwd": os.getcwd(),
        }
        if parent_pid:
            meta["parent_pid"] = parent_pid
        if token:
            # Small session token to uniquely identify this watcher instance
            meta["token"] = str(token)

        try:
            # Write atomically where possible
            tmp = PID_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(json.dumps(meta))
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            try:
                os.replace(tmp, PID_FILE)
            except Exception:
                # Fallback to non-atomic write
                with open(PID_FILE, "w", encoding="utf-8") as f2:
                    f2.write(json.dumps(meta))
        except OSError as e:
            logger.warning("Could not write PID file: %s", e)

    @staticmethod
    def _touch_pid_heartbeat() -> None:
        """Refresh PID file mtime so dashboard watcher-health can measure liveness."""
        try:
            if os.path.exists(PID_FILE):
                os.utime(PID_FILE, None)
        except OSError as exc:
            logger.debug("Could not touch PID heartbeat: %s", exc)

    @staticmethod
    def _remove_pid() -> None:
        """Remove the PID file if it exists.

        Suppressed in COLLAB_TEST_MODE to prevent test processes from accidentally
        deleting the production watcher's PID file.
        """
        if os.getenv("COLLAB_TEST_MODE") == "1":
            return

        try:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        except OSError:
            pass

    @staticmethod
    def _assign_to_job_object() -> None:
        """Assign current process to a Job Object that terminates children when parent
        dies.

        This is a Windows-specific mechanism to ensure the watcher dies with its parent
        IDE. If the parent process terminates, all processes in the job are
        automatically killed.
        """
        if sys.platform != "win32":
            return

        try:
            import ctypes
            from ctypes import wintypes

            # Windows constants
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

            # Create a job object
            job_handle = ctypes.windll.kernel32.CreateJobObjectW(None, None)
            if not job_handle:
                logger.debug("Failed to create Job Object")
                return

            # Configure the job to kill processes when the job handle is closed
            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_void_p),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", wintypes.ULARGE_INTEGER),
                    ("WriteOperationCount", wintypes.ULARGE_INTEGER),
                    ("OtherOperationCount", wintypes.ULARGE_INTEGER),
                    ("ReadTransferCount", wintypes.ULARGE_INTEGER),
                    ("WriteTransferCount", wintypes.ULARGE_INTEGER),
                    ("OtherTransferCount", wintypes.ULARGE_INTEGER),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

            # Set the job information
            result = ctypes.windll.kernel32.SetInformationJobObject(
                job_handle,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )

            if not result:
                logger.debug("Failed to set Job Object information")
                ctypes.windll.kernel32.CloseHandle(job_handle)
                return

            # Assign current process to the job
            current_process = ctypes.windll.kernel32.GetCurrentProcess()
            result = ctypes.windll.kernel32.AssignProcessToJobObject(
                job_handle, current_process
            )

            if result:
                logger.info(
                    "Assigned watcher to Job Object for automatic cleanup "
                    "on parent exit"
                )
            else:
                logger.debug(
                    "Failed to assign process to Job Object (may already be in a job)"
                )

            # Keep the job handle open - it will be closed when the process exits,
            # triggering termination of all processes in the job
        except Exception as e:
            logger.debug("Job Object setup failed (non-critical): %s", e)

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        """Check if a process with the given PID is currently running."""
        if sys.platform == "win32":
            # Try psutil first for most accurate status check
            try:
                import psutil
            except ImportError:
                pass
            else:
                try:
                    p = psutil.Process(pid)
                    status = p.status()
                    if status in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                        return False
                    return True
                except psutil.NoSuchProcess:
                    return False
                except psutil.AccessDenied:
                    return True  # exists but we can't query it
                except Exception as exc:
                    logger.debug("psutil status check failed for PID %s: %s", pid, exc)

            # Win32 API with GetExitCodeProcess to detect zombies
            try:
                import ctypes

                # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                process_handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                if process_handle:
                    try:
                        exit_code = ctypes.c_ulong(0)
                        result = ctypes.windll.kernel32.GetExitCodeProcess(
                            process_handle, ctypes.byref(exit_code)
                        )
                        # STILL_ACTIVE = 259
                        if result and exit_code.value != 259:
                            return False  # Process has exited
                        return True
                    finally:
                        ctypes.windll.kernel32.CloseHandle(process_handle)
                else:
                    # Access denied (5) often means the process exists but
                    # is a high-privileged system process.
                    error = ctypes.windll.kernel32.GetLastError()
                    if error == 5:
                        return True
                    return False
            except Exception as exc:
                logger.debug("Win32 API process check failed for PID %s: %s", pid, exc)

            # Fallback: psutil pid_exists only (no status check)
            try:
                import psutil

                return bool(psutil.pid_exists(pid))
            except ImportError:
                pass
            except Exception as exc:
                logger.debug("psutil pid_exists failed for PID %s: %s", pid, exc)

            return platform_probe.is_pid_alive_tasklist(pid)
        else:
            try:
                os.kill(pid, 0)
                return True
            except (ProcessLookupError, OSError):
                return False

    def _discover_running_watchers(self) -> List[int]:
        """Discover running watcher PIDs that appear to belong to this workspace.

        Tries psutil first for speed, then falls back to platform- specific process
        enumeration. Returns a list of candidate PIDs (may be empty).
        """
        candidates: set[int] = set()

        # Fast path: psutil if available
        try:
            import psutil

            for p in psutil.process_iter(attrs=("pid", "cmdline")):
                try:
                    pid = int(p.info.get("pid") or 0)
                    if pid == os.getpid():
                        continue
                    cmdline = p.info.get("cmdline")
                    if not cmdline:
                        continue
                    cmd_str = (
                        " ".join(cmdline)
                        if isinstance(cmdline, (list, tuple))
                        else str(cmdline)
                    )
                    if self._cmdline_matches_watcher(cmd_str):
                        if not self._cmdline_matches_current_pid_namespace(cmd_str):
                            continue
                        # Ensure the process references this repo (cwd or path)
                        s = cmd_str.lower()
                        if (
                            _PROJECT_ROOT.lower() in s
                            or _COLLAB_ROOT.lower() in s
                            or ".collab" in s
                        ):
                            candidates.add(pid)
                except Exception:
                    continue
            return sorted(candidates)
        except Exception as exc:
            # No psutil — fallback to platform enumeration
            logger.debug("psutil process_iter unavailable/failed: %s", exc)

        if sys.platform == "win32":
            try:
                for pid in platform_probe.iter_tasklist_python_pids():
                    if pid != os.getpid():
                        candidates.add(pid)
            except Exception as exc:
                logger.debug("tasklist watcher discovery failed: %s", exc)
        else:
            try:
                for line in platform_probe.ps_pid_cmd_csv().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(None, 1)
                    if len(parts) >= 2:
                        try:
                            cand = int(parts[0])
                            if cand != os.getpid():
                                candidates.add(cand)
                        except Exception as exc:
                            logger.debug("Failed parsing ps output row: %s", exc)
            except Exception as exc:
                logger.debug("ps fallback failed: %s", exc)

        found: List[int] = []
        for pid in sorted(candidates):
            try:
                cmd = self._get_cmdline_for_pid(pid)
                if not cmd:
                    continue
                if not self._cmdline_matches_watcher(cmd):
                    continue
                if not self._cmdline_matches_current_pid_namespace(cmd):
                    continue
                s = cmd.lower()
                if (
                    _PROJECT_ROOT.lower() in s
                    or _COLLAB_ROOT.lower() in s
                    or ".collab" in s
                ):
                    found.append(pid)
            except Exception:
                continue
        return found

    def _launcher_cmdline_in_namespace(self, cmdline: str) -> bool:
        """Return True for a watcher-launcher cmdline in this PID-file namespace.

        Used to identify pip console-script wrappers (``collab.exe`` / ``collab-
        watcher.exe``) that launched *this* workspace's watcher. Requires a ``watch``
        invocation and a ``--pid-file`` matching the current namespace so launchers from
        unrelated workspaces are never targeted.
        """
        if not cmdline:
            return False
        if "watch" not in cmdline.lower():
            return False
        return self._cmdline_matches_current_pid_namespace(cmdline)

    def _discover_collab_launcher_pids(self) -> List[int]:
        """Find running collab console-script launcher wrappers for this namespace.

        Windows-only. The pip-generated ``collab.exe`` / ``collab-watcher.exe`` wrappers
        keep their own image file open for their entire lifetime, so an orphaned wrapper
        (e.g. spawned by an older IDE extension) blocks deletion of the virtualenv long
        after the underlying Python watcher has exited. Returns candidate launcher PIDs
        to reap (may be empty).
        """
        if sys.platform != "win32":
            return []

        launcher_names = {"collab.exe", "collab-watcher.exe"}
        candidates: set[int] = set()
        self_pid = os.getpid()

        # Fast path: psutil enumeration.
        try:
            import psutil

            for p in psutil.process_iter(attrs=("pid", "name", "cmdline")):
                try:
                    pid = int(p.info.get("pid") or 0)
                    if pid <= 0 or pid == self_pid:
                        continue
                    name = (p.info.get("name") or "").lower()
                    if name not in launcher_names:
                        continue
                    cmdline = p.info.get("cmdline")
                    cmd_str = (
                        " ".join(cmdline)
                        if isinstance(cmdline, (list, tuple))
                        else str(cmdline or "")
                    )
                    if self._launcher_cmdline_in_namespace(cmd_str):
                        candidates.add(pid)
                except Exception:
                    continue
            return sorted(candidates)
        except Exception as exc:
            logger.debug("psutil launcher discovery unavailable/failed: %s", exc)

        # Fallback: tasklist enumeration + per-PID cmdline lookup.
        try:
            for pid in platform_probe.iter_collab_launcher_pids():
                if pid == self_pid:
                    continue
                cmd = self._get_cmdline_for_pid(pid)
                if cmd and self._launcher_cmdline_in_namespace(cmd):
                    candidates.add(pid)
        except Exception as exc:
            logger.debug("tasklist launcher discovery failed: %s", exc)
        return sorted(candidates)

    def _reap_collab_launchers(self) -> int:
        """Force-terminate orphaned collab launcher wrappers in this namespace.

        Windows-only defense-in-depth for ``daemon_stop``. The Python watcher is now
        launched via the interpreter, but older/already-deployed IDE extensions launched
        it through the ``collab.exe`` console-script wrapper. That wrapper can be left
        running (holding the venv ``.exe`` locked) even after the watcher PID is
        stopped. Reaping it makes the virtualenv deletable. Returns the number of
        wrappers terminated.
        """
        if sys.platform != "win32" or _is_test_mode():
            return 0

        try:
            launchers = self._discover_collab_launcher_pids()
        except Exception as exc:
            logger.debug("collab launcher discovery failed: %s", exc)
            return 0

        skip = {os.getpid()}
        try:
            skip.add(os.getppid())
        except Exception:
            pass

        reaped = 0
        for lpid in launchers:
            if lpid in skip:
                continue
            if not self._is_process_alive(lpid):
                continue
            logger.info(
                "Reaping orphaned collab launcher wrapper (PID: %d) holding venv .exe",
                lpid,
            )
            platform_probe.taskkill_force(lpid, tree=True)
            # Confirm termination; log if it stubbornly survives.
            for _ in range(10):
                if not self._is_process_alive(lpid):
                    break
                time.sleep(0.2)
            if self._is_process_alive(lpid):
                logger.warning(
                    "Collab launcher wrapper (PID: %d) survived reap attempt", lpid
                )
            else:
                reaped += 1
        return reaped

    def _read_pid_file(self) -> Optional[Dict[str, Any]]:
        """Read the PID file and return the metadata dictionary if available."""
        if not os.path.exists(PID_FILE):
            return None
        try:
            with open(PID_FILE, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
            if raw.startswith("{"):
                metadata = json.loads(raw)
                if isinstance(metadata, dict):
                    return metadata
        except Exception as exc:
            logger.debug("Failed reading PID metadata file %s: %s", PID_FILE, exc)
        return None

    def _terminate_process(self, pid: int) -> None:
        """Forcefully terminate a process by PID."""
        if sys.platform == "win32":
            try:
                safe_subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    policy="taskkill",
                )
            except SubprocessSecurityError as exc:
                logger.debug("taskkill rejected while terminating PID %s: %s", pid, exc)
            except Exception as exc:
                logger.debug("taskkill failed for PID %s: %s", pid, exc)
        else:
            try:
                # Use getattr or numeric 9 for SIGKILL fallback on Windows
                sig = getattr(signal, "SIGKILL", 9)
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

    def _get_process_info_local(self, pid: int) -> Tuple[Optional[str], Optional[int]]:
        """Fetch process name and parent PID via various Windows tools."""
        if sys.platform != "win32":
            return None, None
        # Prefer psutil when available - it's the most reliable cross-platform
        try:
            import psutil

            try:
                p = psutil.Process(pid)
                name = p.name()
                ppid = p.ppid()
                if name and not name.lower().endswith(".exe"):
                    name = name + ".exe"
                return name, ppid
            except psutil.NoSuchProcess:
                return None, None
            except Exception:
                # psutil present but failed for this PID; fall through to fallbacks
                pass
        except Exception:
            # psutil not available - continue to platform fallbacks
            pass

        # If WMIC is available, prefer it for name+PPID. Otherwise fall back
        # to tasklist for a name-only result.
        try:
            name, parent_id = platform_probe.wmic_process_name_and_ppid_value(pid)
            if name:
                logger.info(
                    "WMIC success: PID %d = %s, parent = %s",
                    pid,
                    name,
                    parent_id,
                )
                return name, parent_id
        except Exception as e:
            logger.debug("WMIC query failed for PID %d: %s", pid, e)

        try:
            out = platform_probe.tasklist_csv_for_pid(pid)
            if out.startswith('"'):
                parts = [p.strip('"') for p in out.split(",")]
                if len(parts) >= 2:
                    return parts[0], None
        except Exception as e:
            logger.debug("tasklist query failed for PID %d: %s", pid, e)

        return None, None

    @staticmethod
    def _keeper_pid_path() -> str:
        """Return the path to the heartbeat-keeper PID metadata file."""
        return _state_path(".daemon_keeper.pid")

    @staticmethod
    def _read_keeper_pid_file() -> Optional[Dict[str, Any]]:
        """Read heartbeat-keeper metadata written by daemon-start."""
        keeper_path = LockClient._keeper_pid_path()
        try:
            if not os.path.exists(keeper_path):
                return None
            with open(keeper_path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
            if not raw:
                return None
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.debug("Could not read keeper PID file: %s", exc)
            return None

    @staticmethod
    def _write_keeper_pid(
        keeper_pid: int,
        *,
        session_owner_pid: Optional[int] = None,
        heartbeat_file: Optional[str] = None,
        session_method: Optional[str] = None,
    ) -> None:
        """Persist heartbeat-keeper metadata for daemon-stop / extension cleanup."""
        meta: Dict[str, Any] = {
            "pid": int(keeper_pid),
            "started_at": _safe_now().astimezone(timezone.utc).isoformat(),
        }
        if session_owner_pid:
            meta["session_owner_pid"] = int(session_owner_pid)
        if heartbeat_file:
            meta["heartbeat_file"] = heartbeat_file
        if session_method:
            meta["session_method"] = session_method
        keeper_path = LockClient._keeper_pid_path()
        try:
            tmp = keeper_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(meta))
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except Exception:
                    pass
            try:
                os.replace(tmp, keeper_path)
            except Exception:
                with open(keeper_path, "w", encoding="utf-8") as fh2:
                    fh2.write(json.dumps(meta))
        except OSError as exc:
            logger.debug("Could not write keeper PID file: %s", exc)

    @staticmethod
    def _remove_keeper_pid() -> None:
        """Remove the heartbeat-keeper PID metadata file."""
        if os.getenv("COLLAB_TEST_MODE") == "1":
            return
        try:
            keeper_path = LockClient._keeper_pid_path()
            if os.path.exists(keeper_path):
                os.remove(keeper_path)
        except OSError:
            pass

    def _rollback_daemon_start_keeper(self, keeper_proc: Any) -> None:
        """Reap heartbeat keeper when watcher startup aborts before finishing."""
        if keeper_proc is not None:
            try:
                keeper_alive = keeper_proc.poll() is None
            except Exception:
                keeper_alive = False
            if keeper_alive:
                logger.debug(
                    "Rolling back heartbeat keeper PID %d after aborted daemon-start",
                    keeper_proc.pid,
                )
                self._terminate_process(keeper_proc.pid)
        self._terminate_heartbeat_keeper()

    def _terminate_heartbeat_keeper(self) -> None:
        """Stop the daemon-start heartbeat keeper if one is recorded."""
        meta = self._read_keeper_pid_file()
        if not meta:
            return
        keeper_pid = meta.get("pid")
        if isinstance(keeper_pid, int) and keeper_pid > 0:
            if self._is_process_alive(keeper_pid):
                logger.debug("Terminating heartbeat keeper PID %d", keeper_pid)
                self._terminate_process(keeper_pid)
        self._remove_keeper_pid()

    @staticmethod
    def _resolve_keeper_python() -> str:
        """Return a windowless Python interpreter for the heartbeat keeper."""
        keeper_python = sys.executable
        if sys.platform == "win32":
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if os.path.exists(pythonw):
                keeper_python = pythonw
            else:
                base = os.path.basename(sys.executable)
                base_lower = base.lower()
                if base_lower.startswith("python") and not base_lower.startswith(
                    "pythonw"
                ):
                    pythonw_base = "pythonw" + base[6:]
                    candidate = os.path.join(
                        os.path.dirname(sys.executable), pythonw_base
                    )
                    if os.path.exists(candidate):
                        keeper_python = candidate
        return keeper_python

    def _spawn_heartbeat_keeper(
        self, heartbeat_file: str, session_owner_pid: int
    ) -> Optional[Any]:
        """Spawn a detached keeper that touches *heartbeat_file* while session lives."""
        try:
            import subprocess as _sp

            keeper_env: dict = {
                **os.environ,
                "COLLAB_HEARTBEAT_KEEPER_FILE": heartbeat_file,
                "COLLAB_HEARTBEAT_SESSION_PID": str(session_owner_pid),
            }
            keeper_argv = [
                self._resolve_keeper_python(),
                "-c",
                _HEARTBEAT_KEEPER_SCRIPT,
            ]
            creation_flags = 0
            if sys.platform == "win32":
                # pythonw.exe + CREATE_NO_WINDOW — no visible console flash.
                # Lifetime is governed by session-PID polling inside the keeper.
                creation_flags = 0x08000000  # CREATE_NO_WINDOW
            proc = _sp.Popen(
                keeper_argv,
                env=keeper_env,
                stdin=_sp.DEVNULL,
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
                creationflags=creation_flags,
            )
            logger.debug(
                "Heartbeat keeper spawned (PID: %d) session_owner=%d file=%s",
                proc.pid,
                session_owner_pid,
                heartbeat_file,
            )
            return proc
        except Exception as exc:
            logger.debug("Failed to spawn heartbeat keeper: %s", exc)
            return None

    @staticmethod
    def _confirm_heartbeat_keeper(keeper_proc: Any, heartbeat_file: str) -> bool:
        """Return True when the keeper is alive and has created the heartbeat file."""
        deadline = time.time() + _HEARTBEAT_KEEPER_CONFIRM_SECONDS
        while time.time() < deadline:
            try:
                if keeper_proc.poll() is not None:
                    return False
            except Exception:
                return False
            if os.path.exists(heartbeat_file):
                return True
            time.sleep(0.05)
        return False

    @staticmethod
    def _is_session_owner_candidate(name_lower: str) -> bool:
        """Return True when *name_lower* looks like a per-window IDE process."""
        if name_lower in (
            "cursor.exe",
            "code.exe",
            "antigravity.exe",
            "vscodium.exe",
            "node.exe",
        ):
            return True
        if "helper" in name_lower and any(
            token in name_lower for token in ("cursor", "code", "electron")
        ):
            return True
        return False

    def _get_session_heartbeat_owner_pid(
        self, ide_parent_pid: Optional[int]
    ) -> Tuple[Optional[int], str]:
        """Resolve a per-window process for daemon-start heartbeat ownership.

        Unlike ``_get_parent_ide_pid()`` (shared ``VSCODE_PID``), this targets processes
        whose lifetime tracks a single IDE window — e.g. a Cursor utility/renderer child
        — so closing one Agents worktree window reaps the watcher without quitting the
        entire IDE.

        Override explicitly via ``COLLAB_SESSION_PID`` when auto-detection is
        insufficient (documented for integrators).
        """
        explicit = os.getenv("COLLAB_SESSION_PID")
        if explicit and explicit.isdigit():
            pid = int(explicit)
            if self._is_process_alive(pid):
                logger.debug("Session heartbeat owner from COLLAB_SESSION_PID: %d", pid)
                return pid, "collab_session_pid_env"

        try:
            current_pid: Optional[int] = os.getppid()
            visited: set[int] = set()
            while current_pid and current_pid not in visited and len(visited) < 25:
                visited.add(current_pid)
                if current_pid == ide_parent_pid:
                    name, ppid = self._get_process_info_local(current_pid)
                    if not ppid or ppid == current_pid:
                        break
                    current_pid = ppid
                    continue

                name, ppid = self._get_process_info_local(current_pid)
                if name:
                    name_lower = name.lower()
                    if (
                        name_lower not in _SESSION_CHAIN_SKIP_EXE_NAMES
                        and self._is_session_owner_candidate(name_lower)
                        and self._is_process_alive(current_pid)
                    ):
                        logger.debug(
                            "Session heartbeat owner via process tree: %s (PID %d)",
                            name,
                            current_pid,
                        )
                        return current_pid, "process_tree_session"

                if not ppid or ppid == current_pid:
                    break
                current_pid = ppid
        except Exception as exc:
            logger.debug("Session owner process-tree walk failed: %s", exc)

        workspace_pid, workspace_method = self._session_owner_from_workspace_cmdline(
            ide_parent_pid
        )
        if workspace_pid:
            return workspace_pid, workspace_method

        return None, "unknown"

    def _session_owner_from_workspace_cmdline(
        self, ide_parent_pid: Optional[int]
    ) -> Tuple[Optional[int], str]:
        """Fallback: match a per-window IDE process by workspace path in its cmdline.

        When the ancestor chain is only ``terminal → shared VSCODE_PID`` (no
        intermediate utility/renderer), walk still fails but the window-scoped process
        often has ``VSCODE_CWD`` or the project root in its command line.
        """
        markers: List[str] = []
        for env_key in ("VSCODE_CWD", "CURSOR_WORKSPACE_FOLDER"):
            raw = os.getenv(env_key)
            if raw:
                markers.append(os.path.normcase(os.path.abspath(raw)))
        project_root = os.path.normcase(os.path.abspath(_PROJECT_ROOT))
        if project_root not in markers:
            markers.append(project_root)
        if not markers:
            return None, "unknown"

        try:
            import psutil
        except ImportError:
            logger.debug("psutil unavailable for workspace cmdline session scan")
            return None, "unknown"

        best_pid: Optional[int] = None
        best_depth = 10_000
        current_pid = os.getpid()

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                pid = proc.info.get("pid")
                if not isinstance(pid, int) or pid <= 0:
                    continue
                if ide_parent_pid and pid == ide_parent_pid:
                    continue
                name = (proc.info.get("name") or "").lower()
                if not self._is_session_owner_candidate(name):
                    continue
                if not self._is_process_alive(pid):
                    continue
                cmd_parts = proc.info.get("cmdline") or []
                cmdline = os.path.normcase(" ".join(str(part) for part in cmd_parts))
                if not any(marker in cmdline for marker in markers):
                    continue
                depth = self._ancestor_depth_between(pid, current_pid)
                if depth is None:
                    continue
                if depth < best_depth:
                    best_depth = depth
                    best_pid = pid
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        if best_pid:
            logger.debug(
                "Session heartbeat owner via workspace cmdline match (PID %d)",
                best_pid,
            )
            return best_pid, "workspace_cmdline_match"
        return None, "unknown"

    @staticmethod
    def _ancestor_depth_between(
        ancestor_pid: int, descendant_pid: int
    ) -> Optional[int]:
        """Return hop count from *descendant_pid* up to *ancestor_pid*, or None."""
        try:
            import psutil
        except ImportError:
            return None

        depth = 0
        current: Optional[int] = descendant_pid
        visited: set[int] = set()
        while current and current not in visited and depth < 25:
            if current == ancestor_pid:
                return depth
            visited.add(current)
            try:
                current = psutil.Process(current).ppid()
            except Exception:
                return None
            depth += 1
        return None

    def _get_parent_ide_pid(self) -> Tuple[Optional[int], Optional[str]]:
        """Identify the IDE or terminal process that owns this session.

        Returns a tuple: (pid, detection_method).

        Detection order (priority):
        - VSCODE_PID env var -> method = "vscode_pid"
        - PYCHARM_HOSTED env -> method = "pycharm_hosted"
        - Process-tree detection (Code.exe / PyCharm) -> method = "process_tree"
        - Simple parent-name walk -> method = "simple_walk"
        - Fallback to immediate parent -> method = "immediate_parent"
        - Unknown -> (None, "unknown")
        """
        # Priority 1: VSCODE_PID environment variable (most reliable)
        vspid = os.getenv("VSCODE_PID")
        logger.debug("VSCODE_PID env var: %s", vspid)
        if vspid and vspid.isdigit():
            vspid_int = int(vspid)
            if self._is_process_alive(vspid_int):
                logger.info("Detected VSCode via VSCODE_PID: %d", vspid_int)
                return vspid_int, "vscode_pid"
            else:
                logger.debug("VSCODE_PID %d is not alive", vspid_int)

        if os.getenv("PYCHARM_HOSTED") == "1":
            hosted_ppid = os.getppid()
            if self._is_process_alive(hosted_ppid):
                logger.debug("Tying to PyCharm hosted session (PID: %d)", hosted_ppid)
                return hosted_ppid, "pycharm_hosted"

        # Priority 2: Walk up process tree looking for IDE window process
        # For VSCode: walk past conhost/node to find the actual Code.exe
        try:
            current_pid: Optional[int] = os.getpid()
            visited: set[int] = set()
            code_exe_pid: Optional[int] = None
            process_chain = []  # For debugging

            logger.debug("Walking process tree starting from PID: %d", current_pid)
            while current_pid and current_pid not in visited:
                visited.add(current_pid)
                active_pid = current_pid
                if active_pid is None:
                    break
                name, ppid = self._get_process_info_local(active_pid)

                if not name:
                    logger.debug("PID %d: no name found, stopping walk", current_pid)
                    break

                name_lower = name.lower()
                process_chain.append(f"{name}({current_pid})")
                logger.debug("PID %d: %s (parent: %s)", current_pid, name, ppid)

                # Track the outermost terminal we find
                if name_lower in (
                    "windowsterminal.exe",
                    "conhost.exe",
                    "cmd.exe",
                    "powershell.exe",
                ):
                    pass

                # Found Code.exe - this is the actual IDE window
                # Use the FIRST one found (closest to terminal), not the deepest one
                if (
                    name_lower
                    in ("code.exe", "antigravity.exe", "cursor.exe", "vscodium.exe")
                    and code_exe_pid is None
                ):
                    code_exe_pid = current_pid
                    logger.debug(
                        "Found outermost Code.exe in process tree (PID: %d)",
                        current_pid,
                    )
                    # Don't break - continue walking to find if there's a closer one

                # Found node.exe extension host - walk up to find Code.exe
                if name_lower == "node.exe" and ppid:
                    next_name, next_ppid = self._get_process_info_local(ppid)
                    if next_name and any(
                        x in next_name.lower()
                        for x in ("code", "antigravity", "cursor", "vscodium")
                    ):
                        logger.debug(
                            "Detected VSCode-like IDE via node.exe parent (PID: %d)",
                            ppid,
                        )
                        return ppid, "node_parent"

                # Found PyCharm
                if name_lower in (
                    "pycharm64.exe",
                    "pycharm.exe",
                    "idea64.exe",
                    "idea.exe",
                ):
                    logger.debug("Detected %s (PID: %d)", name, current_pid)
                    return current_pid, "pycharm_process"

                if not ppid or ppid == current_pid:
                    break
                current_pid = ppid

            logger.debug("Process chain: %s", " -> ".join(process_chain))

            # Return Code.exe if we found it (it's the outermost IDE window)
            if code_exe_pid:
                logger.debug("Tying to VSCode Code.exe (PID: %d)", code_exe_pid)
                return code_exe_pid, "process_tree"

        except Exception as e:
            logger.debug("Process tree walk failed: %s", e)

        # Fallback: Simple parent chain walking using os.getppid()
        # This works when WMIC fails in subprocess contexts
        try:
            logger.debug("Using simple parent chain fallback")
            current = os.getpid()
            visited = set()
            while current and current not in visited and len(visited) < 20:
                visited.add(current)
                try:
                    parent = os.getppid()
                    if parent <= 0 or parent == current:
                        break
                    # Get process name using tasklist (simpler than WMIC)
                    name = self._get_process_name_via_tasklist(parent)
                    logger.info(
                        "Simple walk: PID %d -> parent %d (%s)",
                        current,
                        parent,
                        name or "unknown",
                    )
                    if name:
                        name_lower = name.lower()
                        if name_lower in (
                            "code.exe",
                            "antigravity.exe",
                            "cursor.exe",
                            "vscodium.exe",
                        ):
                            logger.info(
                                "Found VSCode-like IDE %s via simple walk (PID: %d)",
                                name,
                                parent,
                            )
                            return parent, "simple_walk"
                        if name_lower in ("pycharm64.exe", "pycharm.exe"):
                            logger.info(
                                "Found PyCharm via simple walk (PID: %d)", parent
                            )
                            return parent, "simple_walk"
                    current = parent
                except Exception as e:
                    logger.debug("Simple walk error at PID %d: %s", current, e)
                    break
        except Exception as e:
            logger.debug("Simple parent walk failed: %s", e)

        # Fallback 2: Return immediate parent if alive (last resort)
        try:
            ppid = os.getppid()
            if ppid > 0 and self._is_process_alive(ppid):
                logger.info("Falling back to immediate parent (PID: %d)", ppid)
                return ppid, "immediate_parent"
        except Exception as e:
            logger.debug("Immediate parent fallback failed: %s", e)

        logger.warning("Could not determine parent IDE/terminal PID")
        return None, "unknown"

    def _get_process_name_via_tasklist(self, pid: int) -> Optional[str]:
        """Get process name using tasklist - simpler and more reliable than WMIC."""
        try:
            out = platform_probe.tasklist_csv_for_pid(pid)
            if out.startswith('"'):
                parts = [p.strip('"') for p in out.split(",")]
                if len(parts) >= 2:
                    return parts[0]
        except Exception as exc:
            logger.debug("tasklist name lookup failed for PID %s: %s", pid, exc)
        return None


if __name__ == "__main__":
    main()
