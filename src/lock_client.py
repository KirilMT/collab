"""Supabase-backed collaborative file lock client.

Provides atomic lock acquisition, release, and daemon management for preventing merge
conflicts in multi-developer workflows.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

# CLI entrypoint (collab = "src.lock_client:main" in pyproject.toml).
# Main orchestration is in src/main.py; import here for backward compatibility.
from .main import _run_cli, main  # noqa: F401


def _safe_now() -> datetime:
    """Return the current datetime using the (possibly monkeypatched) ``datetime``
    symbol imported into this module.

    Tests patch ``datetime`` with a fake
    class/instance and some replacement objects may present a ``now`` attribute
    that behaves oddly when bound. This helper attempts to call the patched
    ``now`` safely and falls back to the real datetime on failure.
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

    Treat empty values and comment-only values as unset. Inline comments are
    stripped so values like ``X=path  # comment`` remain usable.
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


def _resolve_runtime_root(project_root: str) -> str:
    """Resolve persistent runtime state directory for the current project."""
    home_override = _read_clean_env_path("COLLAB_HOME")
    if home_override:
        return os.path.abspath(home_override)
    return project_root


_PROJECT_ROOT = _resolve_project_root()
_COLLAB_ROOT = _resolve_runtime_root(_PROJECT_ROOT)
_RESOURCE_ROOT = _THIS_DIR
os.makedirs(_COLLAB_ROOT, exist_ok=True)


def _is_test_mode() -> bool:
    """Return True when running under pytest/test harness context."""
    return (
        os.getenv("COLLAB_TEST_MODE") == "1"
        or os.getenv("TESTING") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
    )


def _get_state_dir() -> str:
    """Return a per-workspace state directory outside the repo for non- essential
    runtime markers (heartbeat, shutdown marker, startup summary). This avoids creating
    transient files inside the workspace tree.

    The location can be overridden with the `COLLAB_STATE_DIR` env var for
    testing or custom setups.
    """
    state_dir = _read_clean_env_path("COLLAB_STATE_DIR")
    if state_dir:
        try:
            os.makedirs(state_dir, exist_ok=True)
        except Exception:
            pass
        return state_dir

    try:
        import hashlib as _hashlib
        import tempfile as _tempfile

        h = _hashlib.sha1(
            _PROJECT_ROOT.encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:8]
        base_tmp = _tempfile.gettempdir()
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
        return sd
    except Exception:
        # Fallback to the repository runtime root (best-effort)
        return _COLLAB_ROOT


def _state_path(name: str) -> str:
    return os.path.join(_get_state_dir(), name)


def _resolve_executable_path(name: str) -> Optional[str]:
    """Return an absolute executable path from PATH.

    In explicit test mode only, fall back to the command name so unit tests can
    monkeypatch subprocess calls without depending on host PATH contents.

    Note: On Windows/Linux platform mismatches (e.g., running tests on Linux
    that test Windows executables), shutil.which() may fail trying to check
    Windows-specific APIs. We catch and gracefully degrade in that case.
    """
    try:
        resolved = shutil.which(name)
    except (AttributeError, OSError, ValueError):
        # Platform mismatch (e.g., testing Windows code on Linux).
        # shutil.which() tried to call _winapi functions that don't exist.
        # Fall back as if the executable wasn't found.
        resolved = None

    if not resolved:
        if _is_test_mode():
            return name
        return None
    return os.path.abspath(resolved)


# Load .env from the project root (never modify .env)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
LOCK_STRICT = os.getenv("LOCK_STRICT", "0") == "1"

# Expiry semantics: this project enforces NO automatic expiry. Locks persist
# until released explicitly. The DB RPC ignores time-based expiry; the
# expires_at column is kept for audit but is not used for automatic
# replacement. Clients do not send an expires_at value.

# Developer id prefixes treated as ephemeral (do not persist locks to the DB).
# Enforced in code (not configurable via .env) to avoid accidental skips.
EPHEMERAL_PREFIXES = ["test_dev", "ci"]

# (Intentionally no repo-level toggle) Do not expose a runtime flag to
# enable/disable runtime-path locking.

# PID file lives at project root unless overridden.
# Tests can override this via COLLAB_PID_FILE env var to avoid interfering with
# the live production watcher.
PID_FILE = os.getenv("COLLAB_PID_FILE") or os.path.join(_COLLAB_ROOT, ".daemon.pid")

# Maximum retry attempts for network errors
MAX_RETRIES = 3

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
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        logger.error(
            "Missing Supabase credentials.\n"
            "  SUPABASE_URL=%s\n"
            "  SUPABASE_ANON_KEY=%s\n\n"
            "Please copy .env.example to .env at the project root\n"
            "and fill in your Supabase project credentials.\n"
            "See README.md for setup instructions.",
            SUPABASE_URL or "(not set)",
            "(set)" if SUPABASE_ANON_KEY else "(not set)",
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
                for kw in ("timeout", "connection", "network", "unreachable")
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


# ---------------------------------------------------------------------------
# Supabase Lock Client
# ---------------------------------------------------------------------------
class LockClient:
    """Supabase-backed file lock client.

    All lock operations use the Supabase REST API with the official Python
    client. Lock acquisition uses the atomic ``acquire_lock`` RPC function
    defined in ``schema.sql`` to prevent race conditions.
    """

    def __init__(
        self, developer_id: Optional[str] = None, local_only: bool = False
    ) -> None:
        from typing import cast

        self.local_only = local_only
        self.developer_id = (
            developer_id or os.getenv("COLLAB_DEVELOPER_ID") or self._get_git_username()
        )
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
        self._is_admin: bool = bool(SUPABASE_SERVICE_ROLE_KEY)
        # Treat certain developer ids as ephemeral (e.g. CI/test accounts) so
        # they do not persist locks to the DB. This list is enforced in-code to
        # avoid relying on environment configuration being correct.
        self._ephemeral_developer_ids: set[str] = set(
            # ephemeral (CI/test prefixes)
        )
        self._is_ephemeral: bool = False
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
            key = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
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

        seed = f"{dev_id}:{hostname}:{p_root}"
        return hashlib.sha256(seed.encode()).hexdigest()[:16]

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
            git_name = (
                subprocess.check_output(
                    ["git", "config", "user.name"],
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
                .lower()
            )
            if git_name:
                candidates.append(git_name)
        except Exception:
            pass

        # Also try the system username as fallback
        for env_var in ("USERNAME", "USER", "LOGNAME"):
            val = os.getenv(env_var)
            if val:
                candidates.append(val.lower())

        # Also try path variants (with/without trailing slash)
        path_variants = [p_root, p_root.rstrip("/\\"), p_root + "/", p_root + "\\"]

        seen_seeds: set[str] = set()
        for dev_id in set(candidates):
            for p in path_variants:
                seed = f"{dev_id}:{hostname}:{p}"
                if seed in seen_seeds:
                    continue
                seen_seeds.add(seed)
                token = hashlib.sha256(seed.encode()).hexdigest()[:16]
                if token == stored_token:
                    logger.debug(
                        "Token matched same-machine variant: dev_id=%r path=%r",
                        dev_id,
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
            name = (
                subprocess.check_output(
                    ["git", "config", "user.name"], stderr=subprocess.DEVNULL
                )
                .decode()
                .strip()
            )
            if name:
                return name
        except Exception:
            pass
        return os.getenv("USERNAME") or os.getenv("USER") or "unknown_user"

    @staticmethod
    def _get_current_branch() -> Optional[str]:
        """Return the current git branch name, or None."""
        try:
            if sys.platform == "win32":
                return (
                    subprocess.check_output(
                        ["git", "branch", "--show-current"],
                        stderr=subprocess.DEVNULL,
                        cwd=_PROJECT_ROOT,
                        creationflags=0x08000000,
                    )
                    .decode()
                    .strip()
                )
            else:
                return (
                    subprocess.check_output(
                        ["git", "branch", "--show-current"],
                        stderr=subprocess.DEVNULL,
                        cwd=_PROJECT_ROOT,
                    )
                    .decode()
                    .strip()
                )
        except Exception:
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
            client = self._client
            assert client is not None
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

                # Only log locks owned by this developer
                if owner == self.developer_id:
                    br = lock.get("branch_name") or "main"
                    reason = lock.get("reason") or "Auto-Watch Sync"
                    logger.info(
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
                in_progress = norm in set(self._get_modified_and_unpushed_files())
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
        }

        client = self._client
        assert client is not None, "Supabase client not initialized"
        try:
            res = _retry_on_network_error(
                lambda: client.rpc("acquire_lock", rpc_params).execute()
            )
        except Exception as e:
            return False, f"API Error: {e}"

        status, data, error = self._parse_response(res)

        if error:
            msg = (
                error.get("message", str(error))
                if isinstance(error, dict)
                else str(error)
            )
            return False, f"API Error: {msg}"

        # Parse RPC result
        if isinstance(data, list) and len(data) > 0:
            row = data[0]
            if row.get("status") == "ok":
                logger.info(
                    "🔒 [LOCKED] %s — @%s (branch: %s, reason: %s)",
                    self._normalize_file_path(file_path),
                    self.developer_id,
                    branch or "main",
                    reason or "No reason",
                )
                return True, token
            if row.get("status") == "conflict":
                owner = row.get("owner", "another developer")
                logger.warning(
                    (
                        "⚠️ CONFLICT: %s is locked by @%s — your changes may "
                        "cause a merge conflict."
                    ),
                    self._normalize_file_path(file_path),
                    owner,
                )
                return False, (
                    f"⚠ {file_path} is locked by @{owner}. Editing is not recommended."
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

        Returns (success: bool, message: str).
        """
        # If ephemeral, nothing was persisted so there's nothing to delete.
        if getattr(self, "_is_ephemeral", False):
            logger.info(
                "🔓 [EPHEMERAL-RELEASE] %s (no-op for %s)", file_path, self.developer_id
            )
            return True, "ephemeral-released"

        client = self._client
        assert client is not None, "Supabase client not initialized"
        try:
            norm = self._normalize_file_path(file_path)
            res = _retry_on_network_error(
                lambda: (
                    client.table("file_locks")
                    .delete()
                    .eq("file_path", norm)
                    .eq("developer_id", self.developer_id)
                    .execute()
                )
            )
        except Exception as e:
            return False, f"API Error: {e}"

        status, data, error = self._parse_response(res)
        if error:
            return False, f"API Error: {error}"
        if status in (200, 204) or data is not None:
            logger.info(
                "🔓 [RELEASED] %s — lock released", self._normalize_file_path(file_path)
            )
            return True, "released"
        return False, "No lock released (not owner or lock does not exist)"

    def active(self) -> List[Dict]:
        """Return all currently active locks."""
        client = self._client
        assert client is not None, "Supabase client not initialized"
        try:
            res = _retry_on_network_error(
                lambda: client.table("file_locks").select("*").execute()
            )
        except Exception:
            return []
        _, data, error = self._parse_response(res)
        if error:
            return []
        return data or []

    def get_lock_status(self, file_path: str) -> Dict:
        """Return the lock status for a specific file."""
        client = self._client
        assert client is not None, "Supabase client not initialized"
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
            "acquired_at": lock.get("acquired_at"),
            "reason": lock.get("reason"),
            "can_edit": lock.get("developer_id") == self.developer_id,
        }

    def release_all(self) -> int:
        """Release all locks held by this developer.

        Returns count released.
        """
        locks = self.active()
        my_locks = [lk for lk in locks if lk.get("developer_id") == self.developer_id]
        count = 0
        for lk in my_locks:
            ok, _ = self.release(lk.get("file_path", ""))
            if ok:
                count += 1
        return count

    def force_release(self, file_path: str) -> Tuple[bool, str]:
        """Force-release a lock on file_path.

        Non-admin users can only force-release their own locks. Admin users (with
        SUPABASE_SERVICE_ROLE_KEY) can force-release any lock.

        Returns (success: bool, message: str).
        """
        if not self._is_admin:
            # Non-admin: verify the lock belongs to this developer
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

        client = self._client
        assert client is not None, "Supabase client not initialized"
        try:
            query = client.table("file_locks").delete().eq("file_path", file_path)
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

            client = self._client
            assert client is not None, "Supabase client not initialized"

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
                logger.warning("Lock conflict: %s — %s", fp, msg)
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

    def history(self, file_path: Optional[str] = None, limit: int = 20) -> List[Dict]:
        """Fetch lock history records.

        When *file_path* is provided, an exact match is tried first.  If that
        returns nothing, a ``LIKE %<basename>%`` fallback query runs so the user
        does not have to remember the full stored path.
        """
        client = self._client
        assert client is not None, "Supabase client not initialized"
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
            except Exception:
                pass  # Fallback is best-effort

        return rows

    def prune_history(self, retention_days: int = 30) -> Tuple[bool, int, str]:
        """Delete lock history rows older than *retention_days* days.

        Returns (ok, deleted_count, message).
        """
        if retention_days < 1:
            return False, 0, "retention_days must be >= 1"

        client = self._client
        assert client is not None, "Supabase client not initialized"

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
                logger.info(
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
            "src.lock_client",
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
            if os.path.exists(pythonw):
                proc = subprocess.Popen(
                    [pythonw] + cmd[1:],
                    creationflags=creation_flags,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    cwd=_PROJECT_ROOT,
                )
            else:
                proc = subprocess.Popen(
                    cmd,
                    creationflags=creation_flags,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    cwd=_PROJECT_ROOT,
                )
        else:
            # Unix/Linux/Mac: only use start_new_session if NOT tracking a parent
            # start_new_session creates a new process group, detaching from parent
            popen_kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "cwd": _PROJECT_ROOT,
            }
            if not parent_pid:
                # No parent to track - can safely create new session
                popen_kwargs["start_new_session"] = True
                logger.debug("Starting detached watcher (new session)")
            else:
                # Tied to parent - stay in same process group
                logger.debug(
                    "Starting watcher tied to parent %d (same session)", parent_pid
                )
            proc = subprocess.Popen(cmd, **popen_kwargs)
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
            if i == 20:
                print("... waiting for watcher to initialize ...")
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
            print(
                "❌ Watcher process exited or failed to record PID. "
                f"(Launcher PID: {proc.pid})"
            )
            print("   Check logs/collab.log for details.")
            pid = self._read_pid()
            if pid == proc.pid:
                self._remove_pid()

    def daemon_stop(self) -> None:
        """Stop the running watcher daemon."""
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

            if pid and self._is_process_alive(pid):
                pids_to_stop = [pid]
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
                    return

                # Attempt to discover live watcher processes related to this repo
                try:
                    found = self._discover_running_watchers()
                    if found:
                        pids_to_stop = found
                    else:
                        print("No running watcher found.")
                        logger.info("No running watcher found for this workspace")
                        self._remove_pid()
                        return
                except Exception as e:
                    logger.debug("Watcher discovery failed: %s", e)
                    print("No running watcher found.")
                    self._remove_pid()
                    return

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

                    try:
                        if os.path.exists(stop_file):
                            os.remove(stop_file)
                            logger.info("Removed stop request file: %s", stop_file)
                    except Exception:
                        logger.debug(
                            "Failed to remove stop request file: %s", stop_file
                        )

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
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(target_pid)],
                        capture_output=True,
                        creationflags=0x08000000,
                    )
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

            # Final cleanup: ensure canonical PID file removed
            try:
                self._remove_pid()
            except Exception:
                pass
        finally:
            try:
                collab_logger.propagate = _old_prop
            except Exception:
                pass

    def daemon_status(self) -> bool:
        """Check if the watcher daemon is running.

        Checks both the primary PID file and the legacy PyCharm watcher PID file for
        backward compatibility.
        """
        pid = self._read_pid()
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
        print("❌ Lock watcher is NOT running.")
        return False

    def cleanup_orphaned_processes(self) -> None:
        """Find and kill all orphaned lock_client.py processes.

        This is useful when log files are locked by zombie processes.
        Locks are PRESERVED - only the watcher processes are terminated.
        """
        print("Scanning for orphaned lock_client processes...")
        killed = 0
        pids_to_check = set()

        if sys.platform == "win32":
            # Check multiple Python executable names
            python_images = ["python.exe", "pythonw.exe", "python3.exe"]
            for image in python_images:
                try:
                    result = subprocess.run(
                        [
                            "tasklist",
                            "/FI",
                            f"IMAGENAME eq {image}",
                            "/FO",
                            "CSV",
                            "/NH",
                        ],
                        capture_output=True,
                        text=True,
                        creationflags=0x08000000,
                    )
                    for line in result.stdout.strip().split("\n"):
                        if not line.strip():
                            continue
                        parts = line.strip().strip('"').split('","')
                        if len(parts) >= 2:
                            try:
                                pid = int(parts[1])
                                # Don't kill ourselves
                                if pid != os.getpid():
                                    pids_to_check.add(pid)
                            except (ValueError, IndexError):
                                pass
                except Exception as e:
                    logger.debug("Error scanning %s processes: %s", image, e)

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

                    if inspected and cmd and "lock_client" in cmd.lower():
                        print(f"Killing orphaned lock_client (PID: {pid})")
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            capture_output=True,
                            creationflags=0x08000000,
                        )
                        killed += 1
                        continue

                    # psutil not available or didn't identify commandline;
                    # try WMIC if present
                    if shutil.which("wmic"):
                        try:
                            result = subprocess.run(
                                [
                                    "wmic",
                                    "process",
                                    "where",
                                    f"ProcessId={pid}",
                                    "get",
                                    "CommandLine",
                                    "/value",
                                ],
                                capture_output=True,
                                text=True,
                                creationflags=0x08000000,
                                errors="ignore",
                            )
                            out = (result.stdout or "").lower()
                            if "lock_client" in out:
                                print(f"Killing orphaned lock_client (PID: {pid})")
                                subprocess.run(
                                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                                    capture_output=True,
                                    creationflags=0x08000000,
                                )
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
                result = subprocess.run(
                    ["ps", "aux"],
                    capture_output=True,
                    text=True,
                )
                for line in result.stdout.split("\n"):
                    if "lock_client" in line.lower() and "python" in line.lower():
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

        Returns (url, tmp_path) or (None, None) on error.
        """
        html_path = os.path.join(_RESOURCE_ROOT, "dashboard", "index.html")
        if not os.path.exists(html_path):
            logger.error("Dashboard file not found at %s", html_path)
            return None, None

        try:
            with open(html_path, "r", encoding="utf-8") as fh:
                content = fh.read()
        except Exception as e:
            logger.error("Error reading dashboard template: %s", e)
            return None, None

        injected = {
            "url": SUPABASE_URL or "",
            "anonKey": SUPABASE_ANON_KEY or "",
            "serviceKey": SUPABASE_SERVICE_ROLE_KEY or None,
            "user": self.developer_id or "",
        }
        inject_script = (
            f"<script>window.__SUPABASE_CONFIG__ = {json.dumps(injected)};</script>\n"
        )

        try:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", delete=False, suffix=".html", encoding="utf-8"
            )
            tmp.write(inject_script)
            tmp.write(content)
            tmp.flush()
            tmp.close()
        except Exception as e:
            logger.error("Error creating temp dashboard file: %s", e)
            return None, None

        try:
            import http.server
            from functools import partial

            tmp_dir = os.path.dirname(tmp.name)
            filename = os.path.basename(tmp.name)

            Handler = partial(http.server.SimpleHTTPRequestHandler, directory=tmp_dir)

            # Silence request logging
            RequestHandler = http.server.SimpleHTTPRequestHandler
            RequestHandler.log_message = lambda *a, **k: None  # type: ignore  # noqa

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            port = server.server_address[1]

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def _safe_shutdown() -> None:
                """Best-effort dashboard server shutdown for process exit."""
                try:
                    server.shutdown()
                except BaseException:
                    pass
                try:
                    server.server_close()
                except Exception:
                    pass

            atexit.register(_safe_shutdown)

            url = f"http://127.0.0.1:{port}/{filename}"

            # Probe until ready
            import socket as _socket

            for _ in range(20):
                try:
                    with _socket.create_connection(("127.0.0.1", port), timeout=0.3):
                        break
                except Exception:
                    time.sleep(0.05)

            return url, tmp.name
        except Exception as e:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
            logger.error("Failed to start local dashboard server: %s", e)
            return None, None

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
                                remove_file = False

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
                                        remove_file = True
                                elif txt.startswith("PID:"):
                                    try:
                                        requested_pid = int(txt.split(":", 1)[1])
                                        if requested_pid in (actual_pid, os.getpid()):
                                            matched = True
                                            remove_file = True
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
                                                remove_file = True
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
                                    if remove_file:
                                        try:
                                            os.remove(stop_file)
                                        except Exception as exc:
                                            logger.debug(
                                                "Failed to remove stop marker %s: %s",
                                                stop_file,
                                                exc,
                                            )
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
                                # DEBUG: Log heartbeat check
                                now_ts = time.time()
                                logger.debug(
                                    "Heartbeat check: file=%s exists=%s",
                                    self._heartbeat_file,
                                    os.path.exists(self._heartbeat_file),
                                )

                                # If the heartbeat file is missing, allow a short
                                # startup grace window to avoid races with the
                                # extension creating the heartbeat immediately
                                # after spawning the watcher.
                                if not os.path.exists(self._heartbeat_file):
                                    if now_ts - startup_time < 3.0:
                                        logger.debug(
                                            (
                                                "Heartbeat missing but within startup "
                                                "grace (%.2fs) — ignoring"
                                            ),
                                            now_ts - startup_time,
                                        )
                                    else:
                                        logger.info(
                                            (
                                                "Heartbeat file missing (%s). "
                                                "Shutting down..."
                                            ),
                                            self._heartbeat_file,
                                        )
                                        self._graceful_shutdown(
                                            reason="heartbeat_missing"
                                        )
                                        return

                                # If the heartbeat file exists, ensure it has been
                                # updated recently according to the configured
                                # grace window.
                                age = now_ts - os.path.getmtime(self._heartbeat_file)
                                logger.debug(
                                    "Heartbeat age: %.1fs (threshold: %ss)",
                                    age,
                                    self._heartbeat_grace_seconds,
                                )
                                # Allow a small one-time soft skip when the parent
                                # IDE process is still alive. This helps tolerate
                                # brief extension-host hiccups (file system delays,
                                # quick reloads) while preserving safety.
                                soft_extra = 5.0
                                if age > float(self._heartbeat_grace_seconds):
                                    parent_alive = bool(
                                        self._parent_pid
                                        and self._is_process_alive(self._parent_pid)
                                    )
                                    if parent_alive and not getattr(
                                        self, "_heartbeat_soft_skipped", False
                                    ):
                                        logger.warning(
                                            (
                                                "Heartbeat stale (%.1fs > %ss). "
                                                "Parent alive; allowing "
                                                "one-time extra %.1fs grace."
                                            ),
                                            age,
                                            self._heartbeat_grace_seconds,
                                            soft_extra,
                                        )
                                        self._heartbeat_soft_skipped = True
                                    elif (
                                        age
                                        > float(self._heartbeat_grace_seconds)
                                        + soft_extra
                                    ):
                                        # Final failure: log file contents for debugging
                                        try:
                                            with open(
                                                self._heartbeat_file,
                                                "r",
                                                encoding="utf-8",
                                            ) as hf:
                                                content = hf.read().strip()
                                            logger.debug(
                                                "Heartbeat file content: %s", content
                                            )
                                        except Exception:
                                            pass
                                        logger.info(
                                            (
                                                "Heartbeat stale (%.1fs > %ss) at %s. "
                                                "Shutting down..."
                                            ),
                                            age,
                                            self._heartbeat_grace_seconds,
                                            self._heartbeat_file,
                                        )
                                        self._graceful_shutdown(
                                            reason="heartbeat_stale"
                                        )
                                        return
                            except Exception as e:
                                logger.debug("Heartbeat check exception: %s", e)
                                pass

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

                    out = self._get_modified_and_unpushed_files()
                    current_modified = set(out)

                    if current_modified != last_modified:
                        last_change_time = _safe_now()
                        new_files = current_modified - last_modified
                        if new_files:
                            ts = _safe_now().strftime("%H:%M:%S")
                            logger.info("[%s] Detected: %s", ts, list(new_files))
                            branch = self._get_current_branch()
                            ok, failed, msg = self.acquire_multiple(
                                list(new_files),
                                branch_name=branch,
                                reason="Auto-Watch Sync",
                            )
                            if ok:
                                for fp in sorted(new_files):
                                    logger.info(
                                        (
                                            "🔒 [LOCKED] %s — @%s (branch: %s, "
                                            "reason: Auto-Watch Sync)"
                                        ),
                                        fp,
                                        self.developer_id,
                                        branch or "main",
                                    )
                            else:
                                logger.warning("⚠️ CONFLICT ALERT: %s", msg)

                        released = last_modified - current_modified
                        if released:
                            ts = _safe_now().strftime("%H:%M:%S")
                            for fp in sorted(released):
                                logger.info(
                                    "🔓 [RELEASED] %s — lock released (file finalized)",
                                    fp,
                                )
                            ok, count, _ = self.release_multiple(list(released))
                            if ok and count > 0:
                                logger.info("🔓 [RELEASED] %d file(s) released", count)

                        last_modified = current_modified
                    else:
                        # Idle timeout
                        idle = _safe_now() - last_change_time
                        if timeout_mins > 0 and idle > timedelta(minutes=timeout_mins):
                            logger.info(
                                "Watcher timed out after %dm inactivity.", timeout_mins
                            )
                            break

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
                logger.debug(
                    "OpenProcess failed for parent PID %s: err=%s", parent, err
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
        terminal sessions. They are only released automatically during 'git push'
        (via pre-push hook) or manual release-all.
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
        try:
            active_locks = self.active()
            my_locks = [
                lk for lk in active_locks if lk.get("developer_id") == self.developer_id
            ]
            for lock in sorted(my_locks, key=lambda x: x.get("file_path", "")):
                fp = lock.get("file_path", "")
                if fp:
                    n_kept += 1
                    logger.info(
                        "🔒 [PRESERVED] %s — still has local edits, lock preserved", fp
                    )
        except Exception as e:
            logger.error(
                "Exception while enumerating active locks during shutdown: %s", e
            )

        logger.info(
            "Shutdown complete. Preserved %d lock(s); released 0 lock(s).", n_kept
        )
        # Emit a concise stdout marker for the extension to detect.
        try:
            print(
                f"Shutdown complete. Preserved {n_kept} lock(s); released 0 lock(s).",
                flush=True,
            )
        except Exception:
            pass

        # Write shutdown marker early into the per-workspace state dir so
        # external tools can detect shutdown without placing transient files
        # inside the repository working tree.
        try:
            shutdown_file = _state_path(".shutdown_complete")
            with open(shutdown_file, "w") as f:
                f.write(f"{n_kept}\n")
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
            modified_files = self._get_modified_and_unpushed_files()
            git_modified = set(modified_files)
        except Exception as e:
            logger.error("Error identifying modified files (skipping reconcile): %s", e)
            # DANGEROUS: Returning set() here would cause it to think we should
            # release EVERYTHING we currently have. Instead, return our currently
            # known locks so reconciliation essentially becomes a no-op for this cycle.
            try:
                active = self.active()
                return {
                    lk["file_path"]
                    for lk in active
                    if lk.get("developer_id") == self.developer_id
                }
            except Exception:
                return set()

        try:
            active = self.active()
            my_locks = {
                lk["file_path"]
                for lk in active
                if lk.get("developer_id") == self.developer_id
            }
            # Build lock_map for token checking
            lock_map: dict[str, dict] = {}
            for lk in active:
                if lk.get("developer_id") == self.developer_id:
                    fp = lk.get("file_path", "")
                    if fp:
                        lock_map[fp] = lk
        except Exception as e:
            logger.error("Error getting Supabase locks: %s", e)
            return git_modified

        # Calculate lock categories
        stale = my_locks - git_modified
        missing = git_modified - my_locks
        still_valid = my_locks & git_modified

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
            logger.info("Starting lock reconciliation...")

        # Process stale locks
        if stale:
            for fp in sorted(stale):
                logger.info(
                    "🔓 [STALE-RELEASED] %s — locked but file is now clean, releasing",
                    fp,
                )
            self.release_multiple(list(stale))

        # Process RESUMED locks: use direct table update (preserves acquired_at)
        # This prevents the timer from resetting when switching IDEs
        if resumed_locks:
            for fp in sorted(resumed_locks):
                logger.info("🔒 [RESUMED] %s — lock re-adopted from this machine", fp)
                try:
                    # Use direct update to ONLY change lock_token, NOT acquired_at
                    client = self._client
                    assert client is not None
                    client.table("file_locks").update({"lock_token": current_token}).eq(
                        "file_path", fp
                    ).eq("developer_id", self.developer_id).execute()
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
            for fp in sorted(refreshed_locks):
                logger.info("🔒 [REFRESHED] %s — token refreshed", fp)
            branch = self._get_current_branch()
            self.acquire_multiple(
                list(refreshed_locks), branch_name=branch, reason="Auto-Watch Sync"
            )

        # Process NEW locks (missing) - use acquire RPC
        if missing:
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

        return git_modified

    @staticmethod
    def _run_git_status() -> str:
        """Run git status --porcelain and return output."""
        args = ["git", "status", "--porcelain"]
        if sys.platform == "win32":
            return (
                subprocess.check_output(
                    args, stderr=subprocess.DEVNULL, creationflags=0x08000000
                )
                .decode()
                .strip()
            )
        else:
            return (
                subprocess.check_output(args, stderr=subprocess.DEVNULL)
                .decode()
                .strip()
            )

    def _get_modified_and_unpushed_files(self) -> List[str]:
        """Return files that are either dirty (status) or have unpushed commits
        (diff)."""
        modified = set()

        # 1. Get Dirty/Staged files
        try:
            out = self._run_git_status()
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

        # 2. Get Unpushed Files (diff against upstream)
        try:
            # Check if upstream exists
            args_rev = [
                "git",
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{u}",
            ]
            if sys.platform == "win32":
                subprocess.check_output(
                    args_rev, stderr=subprocess.DEVNULL, creationflags=0x08000000
                )
            else:
                subprocess.check_output(args_rev, stderr=subprocess.DEVNULL)

            # If upstream exists, get names/statuses of files that differ from it.
            # Keep deleted paths as "in progress" so lock ownership remains
            # visible in the dashboard until explicit release.
            args_diff = ["git", "diff", "--name-status", "@{u}..HEAD"]
            if sys.platform == "win32":
                diff_out = (
                    subprocess.check_output(
                        args_diff, stderr=subprocess.DEVNULL, creationflags=0x08000000
                    )
                    .decode()
                    .strip()
                )
            else:
                diff_out = (
                    subprocess.check_output(args_diff, stderr=subprocess.DEVNULL)
                    .decode()
                    .strip()
                )

            if diff_out:
                for line in diff_out.splitlines():
                    raw = line.strip()
                    if not raw:
                        continue
                    parts = raw.split(None, 1)
                    if len(parts) != 2:
                        continue
                    status, payload = parts
                    payload = payload.strip()
                    if "\t" in payload:
                        payload = payload.split("\t")[-1].strip()
                    if " -> " in payload:
                        payload = payload.split(" -> ")[-1].strip()
                    path = self._normalize_file_path(payload)
                    if path.endswith("/"):
                        continue
                    if path and not self._should_ignore_path(path):
                        modified.add(path)
        except Exception:
            # No upstream or command failed - fallback to status-only
            pass

        return list(modified)

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
        # Ignore collab metadata files that the watcher itself creates
        if ".startup_summary.json" in norm or ".shutdown_complete" in norm:
            return True
        # Do not ignore other runtime-relative project paths here.
        return False

    @staticmethod
    def _read_pid() -> Optional[int]:
        """Read daemon PID from the PID file.

        Supports two formats for backward compatibility:
        - Plain integer stored in `.daemon.pid` (legacy)
        - JSON object stored in `.daemon.pid` containing a numeric "pid" field

        Returns the pid as an int, or None if the file is missing or malformed.
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
                    return None
            # Fallback: plain integer
            return int(raw)
        except ValueError:
            logger.debug("PID file does not contain an integer: %s", PID_FILE)
            return None
        except OSError as e:
            logger.debug("Could not read PID file %s: %s", PID_FILE, e)
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
                # If psutil fails for this PID, continue to fallbacks
                pass
        except Exception:
            # psutil not installed — continue to platform fallbacks
            pass

        # Platform-specific fallbacks
        if sys.platform == "win32":
            # Prefer modern PowerShell CIM query when WMIC is not present.
            # Only call WMIC if it is actually available on PATH to avoid
            # repeated FileNotFoundError/WinError logs on newer Windows.
            try:
                if shutil.which("wmic"):
                    try:
                        out = subprocess.check_output(
                            [
                                "wmic",
                                "process",
                                "where",
                                f"ProcessId={pid}",
                                "get",
                                "CommandLine",
                            ],
                            stderr=subprocess.DEVNULL,
                            text=True,
                        )
                        lines = [
                            line.strip() for line in out.splitlines() if line.strip()
                        ]
                        if len(lines) >= 2:
                            return " ".join(lines[1:]).strip()
                    except Exception:
                        # If WMIC fails, continue to PowerShell fallback
                        logger.debug("WMIC command-line query failed for PID %d", pid)
                # PowerShell CIM fallback (works on recent Windows)
                try:
                    cmd_str = (
                        "(Get-CimInstance Win32_Process -Filter "
                        '"ProcessId=%d").CommandLine'
                    ) % pid
                    ps_cmd = ("-NoProfile", "-Command", cmd_str)
                    out = subprocess.check_output(
                        ["powershell", *ps_cmd], stderr=subprocess.DEVNULL, text=True
                    )
                    out = out.strip()
                    if out:
                        return out
                except Exception:
                    logger.debug("PowerShell command-line query failed for PID %d", pid)
            except Exception:
                # Defensive: if shutil or other checks fail, give up gracefully
                logger.debug("Windows cmdline fallback failed for PID %d", pid)
            # As a last resort on Windows we cannot reliably get a cmdline
            return None
        else:
            # Unix-like systems: read /proc/<pid>/cmdline if available
            proc_path = f"/proc/{pid}/cmdline"
            try:
                if os.path.exists(proc_path):
                    with open(proc_path, "rb") as fh:
                        data = fh.read()
                        if not data:
                            return None
                        # cmdline entries are null-separated
                        raw_parts = data.split(b"\x00")
                        parts = [
                            part.decode(errors="replace") for part in raw_parts if part
                        ]
                        return " ".join(parts)
            except Exception:
                pass
            return None

    @staticmethod
    def _cmdline_matches_watcher(cmdline: str) -> bool:
        """Heuristic: return True if the command-line looks like our watcher.

        Matches supported watcher entrypoints, including legacy path-based
        invocations and the current module/CLI forms.
        """
        if not cmdline:
            return False
        s = cmdline.lower()
        return (
            "live_locks_watcher" in s
            or ("lock_client.py" in s and "watch" in s)
            or ("collab.core.lock_client" in s and "watch" in s)
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

            # Final Fallback: tasklist (slow but usually present)
            try:
                tasklist_exe = _resolve_executable_path("tasklist")
                if not tasklist_exe:
                    return False
                out = subprocess.check_output(
                    [tasklist_exe, "/FI", f"PID eq {pid}", "/NH"],
                    text=True,
                    creationflags=0x08000000,
                )
                return str(pid) in out
            except Exception as exc:
                logger.debug("tasklist process check failed for PID %s: %s", pid, exc)
                return False
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
            tasklist_exe = _resolve_executable_path("tasklist")
            if not tasklist_exe:
                logger.debug("tasklist executable not found; skipping Windows fallback")
                tasklist_exe = None
            if not tasklist_exe:
                return sorted(candidates)
            python_images = ["python.exe", "pythonw.exe", "python3.exe"]
            for image in python_images:
                try:
                    result = subprocess.run(
                        [
                            tasklist_exe,
                            "/FI",
                            f"IMAGENAME eq {image}",
                            "/FO",
                            "CSV",
                            "/NH",
                        ],
                        capture_output=True,
                        text=True,
                        creationflags=0x08000000,
                    )
                    for line in (result.stdout or "").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        parts = line.strip().strip('"').split('","')
                        if len(parts) >= 2:
                            try:
                                pid = int(parts[1])
                                if pid != os.getpid():
                                    candidates.add(pid)
                            except Exception as exc:
                                logger.debug(
                                    "Failed parsing tasklist row for image %s: %s",
                                    image,
                                    exc,
                                )
                except Exception as exc:
                    logger.debug(
                        "tasklist fallback failed for image %s: %s", image, exc
                    )
                    continue
        else:
            try:
                ps_exe = _resolve_executable_path("ps") or "ps"
                result = subprocess.run(
                    [ps_exe, "-eo", "pid,cmd"], capture_output=True, text=True
                )
                for line in (result.stdout or "").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(None, 1)
                    if len(parts) >= 2:
                        try:
                            pid = int(parts[0])
                            if pid != os.getpid():
                                candidates.add(pid)
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
            taskkill_exe = _resolve_executable_path("taskkill")
            if not taskkill_exe:
                logger.debug("taskkill not found while terminating PID %s", pid)
                return
            subprocess.run(
                [taskkill_exe, "/F", "/PID", str(pid)],
                capture_output=True,
                creationflags=0x08000000,
            )
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
            wmic_exe = _resolve_executable_path("wmic")
            if wmic_exe:
                result = subprocess.run(
                    [
                        wmic_exe,
                        "process",
                        "where",
                        f"ProcessId={pid}",
                        "get",
                        "Name,ParentProcessId",
                        "/value",
                    ],
                    capture_output=True,
                    text=True,
                    creationflags=0x08000000,
                    timeout=5,
                    errors="ignore",
                )
                logger.debug(
                    "WMIC result for PID %d: rc=%d stdout=%r stderr=%r",
                    pid,
                    result.returncode,
                    result.stdout[:200] if result.stdout else None,
                    result.stderr[:200] if result.stderr else None,
                )
                if result.returncode == 0 and result.stdout:
                    name_match = re.search(r"Name=(\S+)", result.stdout)
                    parent_match = re.search(r"ParentProcessId=(\d+)", result.stdout)
                    logger.debug(
                        "WMIC parse for PID %d: name_match=%s parent_match=%s",
                        pid,
                        name_match.group(0) if name_match else None,
                        parent_match.group(0) if parent_match else None,
                    )
                    if name_match:
                        name = name_match.group(1)
                        parent_id = int(parent_match.group(1)) if parent_match else None
                        if not name.lower().endswith(".exe"):
                            name += ".exe"
                        logger.info(
                            "WMIC success: PID %d = %s, parent = %s",
                            pid,
                            name,
                            parent_id,
                        )
                        return name, parent_id
        except Exception as e:
            logger.debug("WMIC query failed for PID %d: %s", pid, e)

        # Fallback: tasklist for name only
        try:
            tasklist_exe = _resolve_executable_path("tasklist")
            if not tasklist_exe:
                return None, None
            args = [tasklist_exe, "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"]
            out = (
                subprocess.check_output(
                    args, stderr=subprocess.DEVNULL, creationflags=0x08000000, timeout=5
                )
                .decode("utf-8", errors="ignore")
                .strip()
            )
            # Format: "Image Name","PID","Session Name","Session#","Mem Usage"
            if out.startswith('"'):
                parts = [p.strip('"') for p in out.split(",")]
                if len(parts) >= 2:
                    name = parts[0]
                    return name, None
        except Exception as e:
            logger.debug("tasklist query failed for PID %d: %s", pid, e)

        return None, None

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
                if name_lower == "code.exe" and code_exe_pid is None:
                    code_exe_pid = current_pid
                    logger.debug(
                        "Found outermost Code.exe in process tree (PID: %d)",
                        current_pid,
                    )
                    # Don't break - continue walking to find if there's a closer one

                # Found node.exe extension host - walk up to find Code.exe
                if name_lower == "node.exe" and ppid:
                    next_name, next_ppid = self._get_process_info_local(ppid)
                    if next_name and "code" in next_name.lower():
                        logger.debug(
                            "Detected VSCode via node.exe parent (PID: %d)", ppid
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
                        if name_lower == "code.exe":
                            logger.info(
                                "Found VSCode Code.exe via simple walk (PID: %d)",
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

        # Ultimate fallback: just use immediate parent
        ppid = os.getppid()
        if ppid > 0 and self._is_process_alive(ppid):
            logger.info("Falling back to immediate parent PID: %d", ppid)
            return ppid, "immediate_parent"

        logger.warning("Could not determine parent IDE/terminal PID")
        return None, "unknown"

    def _get_process_name_via_tasklist(self, pid: int) -> Optional[str]:
        """Get process name using tasklist - simpler and more reliable than WMIC."""
        try:
            tasklist_exe = _resolve_executable_path("tasklist")
            if not tasklist_exe:
                return None
            result = subprocess.run(
                [tasklist_exe, "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True,
                text=True,
                creationflags=0x08000000,
                timeout=3,
                errors="ignore",
            )
            if result.returncode == 0 and result.stdout:
                # Format: "Image Name","PID","Session Name","Session#","Mem Usage"
                lines = result.stdout.strip().split("\n")
                for line in lines:
                    if line.startswith('"'):
                        parts = [p.strip('"') for p in line.split(",")]
                        if len(parts) >= 2:
                            return parts[0]
        except Exception as exc:
            logger.debug("tasklist name lookup failed for PID %s: %s", pid, exc)
        return None


if __name__ == "__main__":
    main()
