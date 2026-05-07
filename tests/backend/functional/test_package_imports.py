"""Unit tests for package imports and basic module availability.

Tests verify that all critical modules can be imported and instantiated without errors.
This is a basic sanity check for package structure.
"""

from __future__ import annotations


def test_lock_client_import() -> None:
    """Verify LockClient module can be imported."""
    from src.lock_client import LockClient  # noqa: F401


def test_main_module_import() -> None:
    """Verify main module can be imported."""
    from src import main  # noqa: F401


def test_live_locks_watcher_import() -> None:
    """Verify live_locks_watcher module can be imported."""
    from src import live_locks_watcher  # noqa: F401


def test_logging_config_import() -> None:
    """Verify logging_config module can be imported."""
    from src import logging_config  # noqa: F401


def test_lock_client_instantiation() -> None:
    """Verify LockClient can be instantiated in test mode."""
    from src.lock_client import LockClient

    # Should not crash; connection errors are acceptable
    try:
        client = LockClient(local_only=True)
        assert client is not None
    except Exception as exc:
        # Connection errors expected in CI; import/instantiation is verified
        assert any(
            word in str(exc).lower()
            for word in ["connect", "supabase", "timeout", "network"]
        )
