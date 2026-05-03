"""Centralized logging helpers for the collab utilities in `.collab/`.

This module configures a repository-scoped logs directory at `.collab/logs` and provides
an idempotent helper to wire FileHandlers and a console handler without producing
duplicate handlers when called multiple times (useful for import-time setup in CLI tools
and long-running daemons).
"""

from __future__ import annotations

import atexit
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from typing import Optional

DEFAULT_FORMAT = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 5
DEFAULT_LOG_RETENTION_DAYS = 5


def _ensure_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        # Best-effort: if we cannot create a logs directory, don't raise here
        # to avoid breaking CLI entrypoints. Upstream callers will still see
        # console output.
        pass


def _is_test_mode() -> bool:
    return os.getenv("COLLAB_TEST_MODE") == "1"


def _prune_old_log_files(
    log_dir: str,
    active_log_name: str,
    *,
    retention_days: int = DEFAULT_LOG_RETENTION_DAYS,
    now: Optional[float] = None,
) -> None:
    """Delete expired rotated log files for the active log family.

    The current active log file is never deleted here, even if its mtime is old.
    Rotation limits file size; this pruning step bounds total on-disk lifetime.
    """
    if retention_days <= 0:
        return

    cutoff = (time.time() if now is None else now) - (retention_days * 24 * 60 * 60)
    active_path = os.path.abspath(os.path.join(log_dir, active_log_name))
    try:
        entries = list(os.scandir(log_dir))
    except OSError:
        return

    for entry in entries:
        try:
            if not entry.is_file():
                continue
            if not entry.name.startswith(active_log_name):
                continue

            entry_path = os.path.abspath(entry.path)
            if entry_path == active_path:
                continue

            if entry.stat().st_mtime < cutoff:
                os.remove(entry.path)
        except OSError:
            continue


def _resolve_log_dir(collab_dir: Optional[str]) -> str:
    """Resolve the log directory for the current collab runtime.

    Logs always live under ``<collab_dir>/logs/`` so they are persistent and
    discoverable regardless of the test isolation environment.  COLLAB_STATE_DIR
    isolates *process-state* artifacts (PID files, lock files) but must not
    redirect log files, because the temp dir it points to is cleaned up after each
    test session, making the logs ephemeral and invisible to the developer.

    File-handle safety for test mode is handled by:
    - the ``atexit.register(close_collab_logging)`` call in this module, and
    - the autouse ``_close_collab_logging_after_each_test`` fixture in
      ``.collab/tests/conftest.py``.
    """
    if collab_dir:
        base = collab_dir
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "logs")


def _close_collab_file_handlers() -> None:
    """Close and detach all file handlers from the collab logger.

    Windows keeps an exclusive handle on open log files, so tests and short-lived CLI
    commands need an explicit close path when they finish.
    """
    collab_logger = logging.getLogger("collab")
    for handler in list(collab_logger.handlers):
        if getattr(handler, "baseFilename", None):
            collab_logger.removeHandler(handler)
            try:
                handler.flush()
            except Exception:
                pass
            try:
                handler.close()
            except Exception:
                pass


def close_collab_logging() -> None:
    """Release file-based collab logging handlers for the current process."""
    _close_collab_file_handlers()


def setup_collab_logging(
    collab_dir: Optional[str] = None, *, level: int = logging.INFO
) -> logging.Logger:
    """Configure logging for the collab tooling.

    - collab_dir: path to the `.collab` directory. If omitted, current working
      directory is used and a `logs/` subdir is created there.
    - level: root logger level (defaults to INFO).

    The function is idempotent: calling it multiple times will not attach
    duplicate handlers.
    Returns the root logger instance.
    """
    # Production logs remain in .collab/logs/. Test-mode logs are written to the
    # isolated state dir so test daemons never lock files in the repo itself.
    log_dir = _resolve_log_dir(collab_dir)
    _ensure_dir(log_dir)

    root = logging.getLogger()
    root.setLevel(level)

    # Simple console handler: keep when not present on the root logger
    console_present = any(type(h).__name__ == "StreamHandler" for h in root.handlers)
    if not console_present:
        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(logging.Formatter(DEFAULT_FORMAT, DEFAULT_DATEFMT))
        root.addHandler(ch)

    # Configure a dedicated 'collab' logger for file-based logging. This
    # prevents unrelated libraries or the application root logger from
    # polluting the .collab/logs files. Handlers are attached to the
    # 'collab' logger (and not to the root logger) so only logs emitted by
    # 'collab' namespace (e.g. "collab.lock_client", "collab.pycharm_watcher")
    # are written to collab.log / test_collab.log.
    collab_logger = logging.getLogger("collab")
    collab_logger.setLevel(level)
    # Allow records to propagate so test harnesses (caplog) and the root
    # console handler can observe collab log records. FileHandlers are
    # attached directly to the collab logger so only collab.* records are
    # written to .collab/logs; other libraries will not be recorded there.
    collab_logger.propagate = True

    # Single file handler: `collab.log` will contain both INFO and ERROR records.
    # In test mode we use a distinct filename to avoid Windows file locks
    # when the live daemon and tests run concurrently.
    if os.getenv("COLLAB_TEST_MODE") == "1":
        collab_name = "test_collab.log"
    else:
        collab_name = "collab.log"

    collab_path = os.path.join(log_dir, collab_name)
    _prune_old_log_files(log_dir, collab_name)

    def _has_filehandler_for(logger_obj: logging.Logger, path: str) -> bool:
        for h in getattr(logger_obj, "handlers", []):
            if getattr(h, "baseFilename", None) == os.path.abspath(path):
                return True
        return False

    # Remove stale file handlers so switching between test and production mode
    # does not leave old file handles attached.
    _stale = []
    for h in list(collab_logger.handlers):
        bf = getattr(h, "baseFilename", None)
        if bf and bf != os.path.abspath(collab_path):
            _stale.append(h)
    for h in _stale:
        collab_logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    if not _has_filehandler_for(collab_logger, collab_path):
        try:
            fh = RotatingFileHandler(
                collab_path,
                maxBytes=DEFAULT_LOG_MAX_BYTES,
                backupCount=DEFAULT_LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            # Use the provided root level so the single file captures INFO+ (and ERROR)
            fh.setLevel(level)
            fh.setFormatter(logging.Formatter(DEFAULT_FORMAT, DEFAULT_DATEFMT))
            collab_logger.addHandler(fh)
        except Exception:
            # Best-effort: continue with console-only logging if file can't be opened
            pass

    # Return the root logger for convenience (callers typically ignore return)
    return root


atexit.register(close_collab_logging)
