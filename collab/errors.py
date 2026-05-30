"""Structured error taxonomy for collab runtime lifecycle paths.

Phase 5 (hardening) introduces stable exception types so daemon, watcher, PID, and
subprocess failures can be classified without string matching in tests or operators.
"""

from __future__ import annotations

from typing import Optional


class CollabError(Exception):
    """Base exception for collab runtime failures."""

    code: str = "collab_error"

    def __init__(self, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class ConfigurationError(CollabError):
    """Missing or invalid runtime configuration (credentials, client, env)."""

    code = "configuration_error"


class LockServiceUnavailableError(CollabError):
    """Lock service (Supabase) cannot be reached — DNS, network, or API outage."""

    code = "lock_service_unavailable"


class DaemonLifecycleError(CollabError):
    """Daemon / background watcher lifecycle failure."""

    code = "daemon_lifecycle_error"


class DaemonStartError(DaemonLifecycleError):
    """Watcher daemon failed to start or record a healthy PID."""

    code = "daemon_start_error"


class DaemonStopError(DaemonLifecycleError):
    """Watcher daemon failed to stop cleanly."""

    code = "daemon_stop_error"


class PidError(CollabError):
    """PID file or process identifier handling failure."""

    code = "pid_error"


class PidParseError(PidError):
    """PID file contents are missing or malformed."""

    code = "pid_parse_error"


class WatcherDiscoveryError(CollabError):
    """Could not discover or verify an existing watcher process."""

    code = "watcher_discovery_error"


class ParentMonitorError(CollabError):
    """Parent IDE / terminal process monitoring failure."""

    code = "parent_monitor_error"


class SubprocessSecurityError(CollabError):
    """Rejected subprocess invocation (unknown executable or disallowed args)."""

    code = "subprocess_security_error"


class SubprocessExecutionError(CollabError):
    """Subprocess exited with failure or timed out."""

    code = "subprocess_execution_error"
