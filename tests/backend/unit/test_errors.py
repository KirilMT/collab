"""Unit tests for collab structured error taxonomy (Phase 5C)."""

from __future__ import annotations

import pytest

from src.errors import (
    CollabError,
    ConfigurationError,
    DaemonLifecycleError,
    DaemonStartError,
    LockServiceUnavailableError,
    ParentMonitorError,
    PidParseError,
    SubprocessSecurityError,
    WatcherDiscoveryError,
)


def test_error_codes_are_stable_strings():
    assert ConfigurationError("missing config").code == "configuration_error"
    assert LockServiceUnavailableError("offline").code == "lock_service_unavailable"
    assert DaemonStartError("x").code == "daemon_start_error"
    assert PidParseError("bad pid").code == "pid_parse_error"
    assert WatcherDiscoveryError("missing").code == "watcher_discovery_error"
    assert ParentMonitorError("parent gone").code == "parent_monitor_error"
    assert SubprocessSecurityError("nope").code == "subprocess_security_error"


def test_daemon_errors_inherit_lifecycle_base():
    err = DaemonStartError("failed", detail="pid timeout")
    assert isinstance(err, DaemonLifecycleError)
    assert isinstance(err, CollabError)
    assert err.detail == "pid timeout"
    assert "failed" in str(err)


def test_configuration_error_message():
    with pytest.raises(ConfigurationError) as exc:
        raise ConfigurationError("Supabase client not initialized")
    assert exc.value.code == "configuration_error"
