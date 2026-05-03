"""Tests for the collab logging configuration module.

Covers edge cases such as directory creation failure, fallback paths, test mode vs
production mode, stale handler cleanup, and file handler creation errors.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import types
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest


def _find_logging_config_path() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        candidate = parent / ".collab" / "logging_config.py"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("logging_config.py not found in repo")


def _import_fresh_logging_config():
    """Import logging_config module with a clean module object."""
    path = _find_logging_config_path()
    spec = importlib.util.spec_from_file_location(
        "collab.logging_config_test", str(path)
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore[arg-type]
    return mod


def _reset_logging():
    """Reset the Python logging system to a pristine state."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.WARNING)
    collab_logger = logging.getLogger("collab")
    for h in list(collab_logger.handlers):
        collab_logger.removeHandler(h)
    collab_logger.propagate = True
    collab_logger.setLevel(logging.NOTSET)


# =========================================================================
# _ensure_dir
# =========================================================================


def test_ensure_dir_creates(tmp_path):
    """_ensure_dir creates the directory when possible."""
    lc = _import_fresh_logging_config()
    target = tmp_path / "logs"
    lc._ensure_dir(str(target))
    assert target.exists()


def test_ensure_dir_handles_exception(monkeypatch):
    """_ensure_dir does not raise when os.makedirs fails."""
    lc = _import_fresh_logging_config()

    def bad_makedirs(path, exist_ok=False):
        raise PermissionError("Access denied")

    monkeypatch.setattr(os, "makedirs", bad_makedirs)
    # Should not raise
    lc._ensure_dir("/nonexistent/path")


def test_is_test_mode_reflects_environment(monkeypatch):
    """_is_test_mode mirrors the COLLAB_TEST_MODE environment flag."""
    lc = _import_fresh_logging_config()

    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    assert lc._is_test_mode() is True

    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    assert lc._is_test_mode() is False


# =========================================================================
# setup_collab_logging — fallback base path
# =========================================================================


def test_setup_collab_logging_no_collab_dir(monkeypatch):
    """When collab_dir is None, uses dirname of __file__ as base."""
    _reset_logging()
    lc = _import_fresh_logging_config()
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setenv("COLLAB_STATE_DIR", os.getcwd())
    # Should not raise
    logger = lc.setup_collab_logging(collab_dir=None)
    assert logger is not None
    assert logger is logging.getLogger()


# =========================================================================
# setup_collab_logging — test mode vs production mode file names
# =========================================================================


def test_setup_collab_logging_test_mode_uses_test_filename(monkeypatch, tmp_path):
    """COLLAB_TEST_MODE=1 writes test_collab.log inside collab_dir/logs, not
    COLLAB_STATE_DIR.

    Logs must always live in <collab_dir>/logs/ so they are persistent and discoverable
    after a test session ends.  COLLAB_STATE_DIR isolates *process-state* artifacts (PID
    files, lock files) but must NOT redirect log files.
    """
    _reset_logging()
    lc = _import_fresh_logging_config()
    repo_collab_dir = tmp_path / "repo_collab"
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    # Set a COLLAB_STATE_DIR that differs from repo_collab_dir to prove logs
    # do NOT follow it.
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path / "state"))
    lc.setup_collab_logging(collab_dir=str(repo_collab_dir))
    assert (repo_collab_dir / "logs" / "test_collab.log").exists()
    assert not (tmp_path / "state" / "logs" / "test_collab.log").exists()


def test_setup_collab_logging_production_uses_collab_filename(monkeypatch, tmp_path):
    """Without COLLAB_TEST_MODE, uses 'collab.log'."""
    _reset_logging()
    lc = _import_fresh_logging_config()
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    monkeypatch.delenv("COLLAB_STATE_DIR", raising=False)
    lc.setup_collab_logging(collab_dir=str(tmp_path))
    log_dir = os.path.join(str(tmp_path), "logs")
    assert os.path.exists(os.path.join(log_dir, "collab.log"))


def test_setup_collab_logging_uses_rotating_file_handler(monkeypatch, tmp_path):
    """Collab logging uses bounded file rotation to avoid unbounded log growth."""
    _reset_logging()
    lc = _import_fresh_logging_config()
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)

    lc.setup_collab_logging(collab_dir=str(tmp_path))

    collab_logger = logging.getLogger("collab")
    file_handler = next(
        handler
        for handler in collab_logger.handlers
        if getattr(handler, "baseFilename", None)
    )

    assert isinstance(file_handler, RotatingFileHandler)
    assert file_handler.maxBytes == lc.DEFAULT_LOG_MAX_BYTES
    assert file_handler.backupCount == lc.DEFAULT_LOG_BACKUP_COUNT


def test_prune_old_log_files_removes_only_expired_rotated_logs(tmp_path):
    """Expired rotated logs are removed while fresh and active logs are preserved."""
    lc = _import_fresh_logging_config()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    active = log_dir / "collab.log"
    expired_rotated = log_dir / "collab.log.1"
    fresh_rotated = log_dir / "collab.log.2"
    other_log = log_dir / "test_collab.log.1"
    for path in (active, expired_rotated, fresh_rotated, other_log):
        path.write_text("x", encoding="utf-8")

    now = 1_700_000_000
    expired_mtime = now - (6 * 24 * 60 * 60)
    fresh_mtime = now - (2 * 24 * 60 * 60)
    os.utime(active, (expired_mtime, expired_mtime))
    os.utime(expired_rotated, (expired_mtime, expired_mtime))
    os.utime(fresh_rotated, (fresh_mtime, fresh_mtime))
    os.utime(other_log, (expired_mtime, expired_mtime))

    lc._prune_old_log_files(str(log_dir), "collab.log", now=now)

    assert active.exists()
    assert not expired_rotated.exists()
    assert fresh_rotated.exists()
    assert other_log.exists()


def test_prune_old_log_files_ignores_os_errors(monkeypatch, tmp_path):
    """Best-effort pruning should not raise if directory scanning fails."""
    lc = _import_fresh_logging_config()

    def failing_scandir(path):
        raise OSError("scan failed")

    monkeypatch.setattr(os, "scandir", failing_scandir)

    lc._prune_old_log_files(str(tmp_path), "collab.log")


def test_prune_old_log_files_skips_when_retention_disabled(monkeypatch, tmp_path):
    """Non-positive retention disables pruning and avoids directory scanning."""
    lc = _import_fresh_logging_config()
    called = []

    def tracking_scandir(path):
        called.append(path)
        raise AssertionError("scandir should not run when retention is disabled")

    monkeypatch.setattr(os, "scandir", tracking_scandir)

    lc._prune_old_log_files(str(tmp_path), "collab.log", retention_days=0)

    assert called == []


def test_prune_old_log_files_skips_directories(tmp_path):
    """Directory entries in the logs folder are ignored during pruning."""
    lc = _import_fresh_logging_config()
    log_dir = tmp_path / "logs"
    nested = log_dir / "collab.log.archive"
    log_dir.mkdir()
    nested.mkdir()

    lc._prune_old_log_files(str(log_dir), "collab.log", now=1_700_000_000)

    assert nested.exists()


def test_prune_old_log_files_ignores_remove_errors(monkeypatch, tmp_path):
    """Best-effort pruning continues if deleting an expired rotated log fails."""
    lc = _import_fresh_logging_config()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    expired_rotated = log_dir / "collab.log.1"
    expired_rotated.write_text("x", encoding="utf-8")

    now = 1_700_000_000
    expired_mtime = now - (6 * 24 * 60 * 60)
    os.utime(expired_rotated, (expired_mtime, expired_mtime))

    def failing_remove(path):
        raise OSError("delete failed")

    monkeypatch.setattr(os, "remove", failing_remove)

    lc._prune_old_log_files(str(log_dir), "collab.log", now=now)

    assert expired_rotated.exists()


# =========================================================================
# Stale handler removal (lines 95-108)
# =========================================================================


def test_stale_handler_removal_does_not_raise(monkeypatch, tmp_path):
    """Calling setup_collab_logging multiple times does not raise."""
    _reset_logging()
    lc = _import_fresh_logging_config()
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)

    # First call creates handlers
    lc.setup_collab_logging(collab_dir=str(tmp_path))

    # Second call - stale handler cleanup should not raise
    lc.setup_collab_logging(collab_dir=str(tmp_path))
    collab_logger = logging.getLogger("collab")
    assert len(collab_logger.handlers) > 0


def test_stale_handler_removed_when_collab_name_changes(monkeypatch, tmp_path):
    """When switching between test and production mode, old file handler is stale."""
    _reset_logging()
    lc = _import_fresh_logging_config()
    collab_dir = tmp_path / "collab"
    # First call with COLLAB_TEST_MODE=1 -> creates handler for test_collab.log
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    lc.setup_collab_logging(collab_dir=str(collab_dir))
    collab_logger = logging.getLogger("collab")
    file_handlers = [h for h in collab_logger.handlers if hasattr(h, "baseFilename")]
    assert len(file_handlers) >= 1
    assert any(
        "test_collab.log" in getattr(h, "baseFilename", "") for h in file_handlers
    )

    # Second call without COLLAB_TEST_MODE -> should create handler for collab.log
    # and old test_collab.log handler should be detected as stale
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    lc.setup_collab_logging(collab_dir=str(collab_dir))
    assert (collab_dir / "logs" / "test_collab.log").exists()
    assert (collab_dir / "logs" / "collab.log").exists()
    current_paths = {getattr(h, "baseFilename", None) for h in collab_logger.handlers}
    assert str(collab_dir / "logs" / "collab.log") in current_paths
    assert str(collab_dir / "logs" / "test_collab.log") not in current_paths


# =========================================================================
# File handler creation failure (line 117)
# =========================================================================


def test_setup_collab_logging_file_handler_failure(monkeypatch, tmp_path):
    """When the rotating log handler cannot be created, continue gracefully."""
    _reset_logging()
    lc = _import_fresh_logging_config()
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)

    def failing_rotating_handler(filename, *args, **kwargs):
        if "collab.log" in str(filename):
            raise OSError("Cannot open log file")
        raise OSError("Unexpected")

    monkeypatch.setattr(lc, "RotatingFileHandler", failing_rotating_handler)
    # Should not raise
    lc.setup_collab_logging(collab_dir=str(tmp_path))


# =========================================================================
# Console handler is added when missing
# =========================================================================


def test_setup_collab_logging_adds_console_handler(monkeypatch, tmp_path):
    """When root logger has no StreamHandler, one is added."""
    _reset_logging()
    lc = _import_fresh_logging_config()
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path / "state"))

    # Remove any existing StreamHandlers from root
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    lc.setup_collab_logging(collab_dir=str(tmp_path))
    has_stream = any(type(h).__name__ == "StreamHandler" for h in root.handlers)
    assert has_stream


def test_setup_collab_logging_console_already_present(monkeypatch, tmp_path):
    """When root logger already has a StreamHandler, no duplicate is added."""
    _reset_logging()
    lc = _import_fresh_logging_config()
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path / "state"))

    root = logging.getLogger()
    # Remove existing handlers then add exactly one StreamHandler
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(logging.StreamHandler())

    stream_count_before = sum(
        1 for h in root.handlers if type(h).__name__ == "StreamHandler"
    )

    lc.setup_collab_logging(collab_dir=str(tmp_path))

    stream_count_after = sum(
        1 for h in root.handlers if type(h).__name__ == "StreamHandler"
    )
    # Should not increase
    assert stream_count_after == stream_count_before


def test_close_collab_logging_releases_file_handlers(monkeypatch, tmp_path):
    """close_collab_logging removes collab file handlers from the current process."""
    _reset_logging()
    lc = _import_fresh_logging_config()
    state_dir = tmp_path / "state"
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setenv("COLLAB_STATE_DIR", str(state_dir))

    lc.setup_collab_logging(collab_dir=str(tmp_path / "repo_collab"))
    collab_logger = logging.getLogger("collab")
    assert any(getattr(h, "baseFilename", None) for h in collab_logger.handlers)

    lc.close_collab_logging()

    assert not any(getattr(h, "baseFilename", None) for h in collab_logger.handlers)


def test_setup_collab_logging_closes_stale_file_handler(monkeypatch, tmp_path):
    """When the effective log filename changes (test↔prod mode switch), the stale file
    handler is explicitly closed, releasing Windows file locks."""
    _reset_logging()
    lc = _import_fresh_logging_config()
    collab_dir = tmp_path / "collab"
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    lc.setup_collab_logging(collab_dir=str(collab_dir))

    collab_logger = logging.getLogger("collab")
    stale_handler = next(
        handler
        for handler in collab_logger.handlers
        if getattr(handler, "baseFilename", None)
    )

    closed = []
    original_close = stale_handler.close

    def tracked_close():
        closed.append(stale_handler.baseFilename)
        original_close()

    stale_handler.close = tracked_close  # type: ignore[assignment]

    # Switch to production mode: filename changes test_collab.log → collab.log
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    lc.setup_collab_logging(collab_dir=str(collab_dir))

    assert closed == [str(collab_dir / "logs" / "test_collab.log")]
    current_paths = {
        getattr(handler, "baseFilename", None) for handler in collab_logger.handlers
    }
    assert str(collab_dir / "logs" / "collab.log") in current_paths
    assert str(collab_dir / "logs" / "test_collab.log") not in current_paths


def test_setup_collab_logging_ignores_stale_handler_close_errors(monkeypatch, tmp_path):
    """Stale handler cleanup is best-effort if closing the old handler fails."""
    _reset_logging()
    lc = _import_fresh_logging_config()
    collab_dir = tmp_path / "collab"
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    lc.setup_collab_logging(collab_dir=str(collab_dir))

    collab_logger = logging.getLogger("collab")
    stale_handler = next(
        handler
        for handler in collab_logger.handlers
        if getattr(handler, "baseFilename", None)
    )
    monkeypatch.setattr(
        stale_handler,
        "close",
        lambda: (_ for _ in ()).throw(OSError("close failed")),
    )

    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    lc.setup_collab_logging(collab_dir=str(collab_dir))

    current_paths = {
        getattr(handler, "baseFilename", None) for handler in collab_logger.handlers
    }
    assert str(collab_dir / "logs" / "collab.log") in current_paths


def test_close_collab_logging_preserves_console_handlers(monkeypatch, tmp_path):
    """close_collab_logging only removes file handlers and keeps console handlers."""
    _reset_logging()
    lc = _import_fresh_logging_config()
    state_dir = tmp_path / "state"
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setenv("COLLAB_STATE_DIR", str(state_dir))

    root = logging.getLogger()
    root.handlers.clear()
    console_handler = logging.StreamHandler()
    root.addHandler(console_handler)

    lc.setup_collab_logging(collab_dir=str(tmp_path / "repo_collab"))
    lc.close_collab_logging()

    assert console_handler in root.handlers


def test_close_collab_logging_ignores_flush_and_close_errors(monkeypatch, tmp_path):
    """close_collab_logging is best-effort even if handler flush/close fail."""
    _reset_logging()
    lc = _import_fresh_logging_config()
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")

    lc.setup_collab_logging(collab_dir=str(tmp_path / "repo_collab"))
    collab_logger = logging.getLogger("collab")
    file_handler = next(
        handler
        for handler in collab_logger.handlers
        if getattr(handler, "baseFilename", None)
    )

    monkeypatch.setattr(
        file_handler,
        "flush",
        lambda: (_ for _ in ()).throw(OSError("flush failed")),
    )
    monkeypatch.setattr(
        file_handler,
        "close",
        lambda: (_ for _ in ()).throw(OSError("close failed")),
    )

    lc.close_collab_logging()

    assert not any(getattr(h, "baseFilename", None) for h in collab_logger.handlers)


def test_collab_test_conftest_cleanup_fixture_calls_close_logging(monkeypatch):
    """The collab test autouse cleanup fixture calls close_collab_logging after
    yield."""
    conftest_path = Path(__file__).resolve().parents[1] / "conftest.py"
    spec = importlib.util.spec_from_file_location(
        "collab.tests.conftest_for_test", str(conftest_path)
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)  # type: ignore[arg-type]

    close_calls = []
    fake_logging_module = types.SimpleNamespace(
        close_collab_logging=lambda: close_calls.append("closed")
    )
    monkeypatch.setattr(
        module, "_load_logging_config_module", lambda: fake_logging_module
    )

    fixture_impl = module._close_collab_logging_after_each_test.__wrapped__
    generator = fixture_impl()
    next(generator)
    with pytest.raises(StopIteration):
        next(generator)

    assert close_calls == ["closed"]
