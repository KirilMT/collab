"""Agent identity resolution for multi-agent collaborative locking.

When one GitHub user runs multiple AI agents in the same repository, each agent needs a
stable, unique identity layered on top of the human ``developer_id``. This module
resolves ``agent_id`` / ``agent_label`` and provides helpers for lock ownership
comparisons.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from typing import Any, Optional

_AGENT_ID_FILE = ".agent_id"
_AGENT_LABEL_FILE = ".agent_label"
_AGENT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")

# Environment variables that imply an AI agent runtime (not exhaustive).
_AGENT_RUNTIME_MARKERS: tuple[tuple[str, str], ...] = (
    ("CURSOR_TRACE_ID", "cursor"),
    ("CURSOR_SESSION_ID", "cursor"),
    ("CURSOR_AGENT", "cursor"),
    ("COMPOSER_SESSION_ID", "composer"),
    ("CLAUDE_CODE", "claude-code"),
    ("CLAUDE_CODE_SESSION", "claude-code"),
    ("GITHUB_COPILOT_AGENT_ID", "copilot"),
)


def _read_clean_env(name: str) -> Optional[str]:
    raw = os.getenv(name)
    if raw is None:
        return None
    val = raw.strip()
    if not val or val.startswith("#"):
        return None
    if "#" in val:
        val = val.split("#", 1)[0].strip()
    return val or None


def _is_truthy_env(name: str) -> bool:
    raw = _read_clean_env(name)
    if raw is None:
        return False
    return raw.lower() in {"1", "true", "yes", "on"}


def detect_agent_runtime_label() -> Optional[str]:
    """Return a friendly runtime label when known agent env markers are present."""
    for env_name, label in _AGENT_RUNTIME_MARKERS:
        if _read_clean_env(env_name):
            return label
    return None


def is_agent_mode_requested() -> bool:
    """Return True when agent identity should be active for this process.

    STRICT ATTRIBUTION: the mere *presence* of an AI runtime (e.g. a process
    spawned from a Cursor/Claude terminal that exports ``CURSOR_TRACE_ID``) does
    NOT by itself attribute locks to an agent. Doing so caused every background
    auto-lock to be mislabelled as the runtime. Agent attribution now requires an
    *explicit* signal: ``COLLAB_AGENT_ID`` or ``COLLAB_AGENT_MODE``. The detected
    runtime is still used for friendly display only (see :func:`resolve_agent_kind`).
    """
    if _read_clean_env("COLLAB_AGENT_ID"):
        return True
    if _is_truthy_env("COLLAB_AGENT_MODE"):
        return True
    return False


def _sanitize_agent_id(value: str) -> Optional[str]:
    candidate = value.strip()
    if not candidate or not _AGENT_ID_PATTERN.match(candidate):
        return None
    return candidate


def _agent_id_file(state_dir: str) -> str:
    return os.path.join(state_dir, _AGENT_ID_FILE)


def load_persisted_agent_id(state_dir: str) -> Optional[str]:
    """Load a previously persisted agent id from the collab state directory."""
    path = _agent_id_file(state_dir)
    try:
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as fh:
            raw = fh.read().strip()
        return _sanitize_agent_id(raw) if raw else None
    except OSError:
        return None


def persist_agent_id(state_dir: str, agent_id: str) -> None:
    """Persist agent id so subsequent invocations in this state dir reuse it."""
    os.makedirs(state_dir, exist_ok=True)
    path = _agent_id_file(state_dir)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(agent_id)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def generate_agent_id() -> str:
    """Generate a stable-format unique agent id."""
    return f"agent-{uuid.uuid4().hex[:8]}"


def resolve_agent_id(
    state_dir: str,
    *,
    explicit_agent_id: Optional[str] = None,
    agent_mode: Optional[bool] = None,
) -> Optional[str]:
    """Resolve agent id using precedence: explicit env/arg → persisted → generated.

    Returns ``None`` when agent mode is off (human-only locking).
    """
    mode = is_agent_mode_requested() if agent_mode is None else agent_mode
    if explicit_agent_id or _read_clean_env("COLLAB_AGENT_ID"):
        mode = True
    if not mode:
        return None

    for candidate in (
        explicit_agent_id,
        _read_clean_env("COLLAB_AGENT_ID"),
    ):
        if candidate:
            sanitized = _sanitize_agent_id(candidate)
            if sanitized:
                persist_agent_id(state_dir, sanitized)
                return sanitized

    persisted = load_persisted_agent_id(state_dir)
    if persisted:
        return persisted

    generated = generate_agent_id()
    persist_agent_id(state_dir, generated)
    return generated


def resolve_agent_label(
    *,
    explicit_label: Optional[str] = None,
    runtime_label: Optional[str] = None,
) -> Optional[str]:
    """Resolve the human-readable *task* label (the "why / what for").

    This intentionally does NOT fall back to the runtime name (e.g. ``cursor``): the
    label describes the task an agent is working on (``fix-ci-dashboard``), while the
    runtime family is tracked separately as ``agent_kind`` for display. When no task
    label is supplied the dashboard shows a generic "AI Agent".
    """
    for candidate in (
        explicit_label,
        _read_clean_env("COLLAB_AGENT_LABEL"),
        runtime_label,
    ):
        if candidate:
            val = candidate.strip()
            if val:
                return val[:256]
    return None


def resolve_agent_kind(
    *,
    explicit_kind: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve the AI runtime family for friendly display (icon/name).

    Precedence: explicit value → ``COLLAB_AGENT_KIND`` → detected runtime marker.
    When an agent identity exists but the runtime is unknown, falls back to the
    generic ``"other"`` so the dashboard can still render an AI badge. Returns
    ``None`` for human (no agent) locks.
    """
    for candidate in (
        explicit_kind,
        _read_clean_env("COLLAB_AGENT_KIND"),
        detect_agent_runtime_label(),
    ):
        if candidate:
            val = candidate.strip().lower()
            if val:
                return val[:64]
    if agent_id:
        return "other"
    return None


def resolve_origin(agent_id: Optional[str]) -> str:
    """Return the authoritative attribution origin for a lock.

    ``'agent'`` when a unique agent identity is present, otherwise ``'human'``.
    """
    return "agent" if agent_id else "human"


def agent_ids_match(
    lock_agent_id: Optional[str],
    client_agent_id: Optional[str],
) -> bool:
    """Return True when two agent_id values represent the same lock owner."""
    return (lock_agent_id or None) == (client_agent_id or None)


def lock_owned_by_client(
    lock: dict[str, Any],
    developer_id: str,
    agent_id: Optional[str],
) -> bool:
    """Return True when *lock* belongs to the given human + agent pair."""
    if lock.get("developer_id") != developer_id:
        return False
    return agent_ids_match(lock.get("agent_id"), agent_id)


def format_lock_owner(
    developer_id: str,
    agent_id: Optional[str] = None,
    agent_label: Optional[str] = None,
) -> str:
    """Format lock owner for CLI/log messages."""
    base = f"@{developer_id}"
    if agent_id:
        label = agent_label or agent_id
        return f"{base} (agent: {label})"
    return base


def format_conflict_message(
    file_path: str,
    developer_id: str,
    agent_id: Optional[str] = None,
    agent_label: Optional[str] = None,
) -> str:
    """Build a user-facing conflict message."""
    owner = format_lock_owner(developer_id, agent_id, agent_label)
    return f"⚠ {file_path} is locked by {owner}. Editing is not recommended."


def session_token_seed(
    developer_id: str,
    agent_id: Optional[str],
    hostname: str,
    project_root: str,
) -> str:
    """Build the seed string for deterministic session tokens.

    Each component derivation is defensive: if a value cannot be stringified, a safe
    fallback is used rather than raising (a raised seed breaks re-adoption).
    """
    try:
        dev = str(developer_id).strip().lower() if developer_id else "unknown"
    except Exception:
        dev = "unknown"
    try:
        host = hostname.lower() if hostname else "localhost"
    except Exception:
        host = "localhost"
    try:
        root = project_root.lower().rstrip("\\/") if project_root else "project"
    except Exception:
        root = "project"
    if agent_id:
        try:
            agent = str(agent_id).strip().lower()
        except Exception:
            agent = "agent"
        return f"{dev}:{agent}:{host}:{root}"
    return f"{dev}:{host}:{root}"


def session_token_from_seed(seed: str) -> str:
    """Derive the 16-char hex session token from a seed."""
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


def daemon_pid_basename(agent_id: Optional[str]) -> str:
    """Return the daemon PID filename for the given agent (or default)."""
    if not agent_id:
        return ".daemon.pid"
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", agent_id)
    return f".daemon.{safe}.pid"


def resolve_daemon_pid_path(
    state_dir: str,
    agent_id: Optional[str],
    *,
    env_override: Optional[str] = None,
) -> str:
    """Resolve the PID file path for a watcher instance."""
    override = env_override or _read_clean_env("COLLAB_PID_FILE")
    if override:
        return override
    return os.path.join(state_dir, daemon_pid_basename(agent_id))


def apply_agent_filter(query: Any, agent_id: Optional[str]) -> Any:
    """Scope a PostgREST delete/update query to the current agent_id (or NULL)."""
    if agent_id is None:
        is_null = getattr(query, "is_", None)
        if callable(is_null):
            return is_null("agent_id", "null")
        # Test doubles may only implement ``eq`` — treat NULL as None there.
        return query.eq("agent_id", None)
    return query.eq("agent_id", agent_id)


def identity_summary(
    developer_id: str,
    agent_id: Optional[str],
    agent_label: Optional[str],
    agent_kind: Optional[str] = None,
) -> dict[str, Optional[str]]:
    """Return a dict suitable for ``collab whoami`` JSON output."""
    mode = "agent" if agent_id else "human"
    return {
        "developer_id": developer_id,
        "agent_id": agent_id,
        "agent_label": agent_label,
        "agent_kind": agent_kind,
        "mode": mode,
    }
