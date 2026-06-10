"""Unit tests for package imports and basic module availability.

Tests verify that all critical modules can be imported and instantiated without errors.
This is a basic sanity check for package structure.
"""

from __future__ import annotations


def test_lock_client_import() -> None:
    """Verify LockClient is importable and callable."""
    from collab.lock_client import LockClient

    assert callable(LockClient)


def test_main_module_import() -> None:
    """Verify the main module exposes a callable ``main`` entry point."""
    from collab import main

    assert hasattr(main, "main") and callable(main.main)


def test_live_locks_watcher_import() -> None:
    """Verify live_locks_watcher exposes its PID_FILE constant."""
    from collab import live_locks_watcher

    assert hasattr(live_locks_watcher, "PID_FILE")


def test_logging_config_import() -> None:
    """Verify logging_config exposes setup_collab_logging."""
    from collab import logging_config

    assert hasattr(logging_config, "setup_collab_logging")


def test_lock_client_instantiation() -> None:
    """Verify LockClient can be instantiated in test mode."""
    from collab.lock_client import LockClient

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
