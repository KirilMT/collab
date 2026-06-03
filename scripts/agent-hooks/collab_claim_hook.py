#!/usr/bin/env python3
"""Runtime-agnostic ``afterFileEdit`` hook that claims files as an AI agent.

This single script works for *any* IDE/agent that can run a command after an
edit and pass event JSON on stdin (Cursor ``afterFileEdit``, Claude Code
``PostToolUse`` for ``Edit``/``Write``, custom integrations, ...). It extracts
the edited file path(s) from the event payload and runs ``collab claim`` so the
lock is attributed to the AI agent (``origin=agent``) with a stable, unique
agent identity — never to the human developer.

Design goals:
    * **Fail open**: editing must never be blocked by lock bookkeeping. Any error
      results in exit code 0 and no JSON mutation.
    * **Generic**: no IDE-specific assumptions; the payload is scanned for any
      plausible "edited file" key.
    * **Safe by default**: only runs when ``COLLAB_AGENT_HOOKS`` is truthy, so it
      never claims files unexpectedly during normal human work or test runs.

Identity precedence (most specific first):
    agent id    : COLLAB_AGENT_ID -> event conversation/session id -> generated
    agent label : COLLAB_AGENT_LABEL -> event title/summary (if any)
    agent kind  : COLLAB_AGENT_KIND -> detected runtime -> "other"

Enable for a session, e.g.::

    # PowerShell
    $env:COLLAB_AGENT_HOOKS = "1"
    $env:COLLAB_AGENT_LABEL = "fix-ci-dashboard"   # optional task label

    # bash/zsh
    export COLLAB_AGENT_HOOKS=1
    export COLLAB_AGENT_LABEL=fix-ci-dashboard
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from typing import Any, Iterable, Optional

# Keys that commonly carry an edited file path across IDE hook payloads.
_PATH_KEYS = (
    "file_path",
    "filePath",
    "path",
    "absolute_path",
    "absolutePath",
    "target",
    "uri",
)
# Keys that commonly carry a session/conversation identifier.
_SESSION_KEYS = (
    "conversation_id",
    "conversationId",
    "session_id",
    "sessionId",
    "thread_id",
    "threadId",
    "chat_id",
    "chatId",
    "generation_id",
    "generationId",
)
# Keys that may carry a human-friendly task description.
_LABEL_KEYS = ("title", "summary", "task", "label", "name")


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _read_event() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _walk(obj: Any) -> Iterable[Any]:
    """Yield every nested dict/list node (depth-first)."""
    yield obj
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _first_str(event: dict[str, Any], keys: Iterable[str]) -> Optional[str]:
    for node in _walk(event):
        if isinstance(node, dict):
            for key in keys:
                val = node.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    return None


def _looks_like_path(value: str) -> bool:
    if not value or "\n" in value:
        return False
    return ("/" in value) or ("\\" in value) or bool(re.search(r"\.\w{1,8}$", value))


def _normalize_path(value: str) -> Optional[str]:
    val = value.strip()
    if val.startswith("file://"):
        val = val[len("file://") :]
        # Strip a leading slash on Windows-style /C:/... paths.
        if re.match(r"^/[A-Za-z]:", val):
            val = val[1:]
    if not _looks_like_path(val):
        return None
    return val


def _extract_paths(event: dict[str, Any]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for node in _walk(event):
        if not isinstance(node, dict):
            continue
        for key in _PATH_KEYS:
            val = node.get(key)
            if isinstance(val, str):
                norm = _normalize_path(val)
                if norm and norm not in seen:
                    seen.add(norm)
                    found.append(norm)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        norm = _normalize_path(item)
                        if norm and norm not in seen:
                            seen.add(norm)
                            found.append(norm)
    return found


def _stable_agent_id(event: dict[str, Any]) -> str:
    explicit = os.getenv("COLLAB_AGENT_ID", "").strip()
    if explicit:
        return explicit
    session = _first_str(event, _SESSION_KEYS)
    if session:
        digest = hashlib.sha1(
            session.encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:8]
        return f"agent-{digest}"
    # Last resort: a per-working-directory stable id so a single session reuses
    # one identity instead of generating a new one on every edit.
    seed = os.path.abspath(os.getcwd()).lower()
    digest = hashlib.sha1(seed.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"agent-{digest}"


def _detect_kind() -> str:
    explicit = os.getenv("COLLAB_AGENT_KIND", "").strip().lower()
    if explicit:
        return explicit
    markers = (
        ("CURSOR_TRACE_ID", "cursor"),
        ("CURSOR_SESSION_ID", "cursor"),
        ("CURSOR_AGENT", "cursor"),
        ("CLAUDE_CODE", "claude-code"),
        ("CLAUDE_CODE_SESSION", "claude-code"),
        ("GITHUB_COPILOT_AGENT_ID", "copilot"),
        ("COMPOSER_SESSION_ID", "composer"),
    )
    for env_name, kind in markers:
        if os.getenv(env_name, "").strip():
            return kind
    return "other"


def _resolve_label(event: dict[str, Any]) -> Optional[str]:
    explicit = os.getenv("COLLAB_AGENT_LABEL", "").strip()
    if explicit:
        return explicit
    return _first_str(event, _LABEL_KEYS)


def main() -> int:
    # Fail open and stay silent unless explicitly enabled.
    if not _truthy("COLLAB_AGENT_HOOKS"):
        return 0

    event = _read_event()
    paths = _extract_paths(event)
    if not paths:
        return 0

    env = dict(os.environ)
    env["COLLAB_AGENT_MODE"] = "1"
    env["COLLAB_AGENT_ID"] = _stable_agent_id(event)
    env["COLLAB_AGENT_KIND"] = _detect_kind()
    label = _resolve_label(event)
    if label:
        env["COLLAB_AGENT_LABEL"] = label

    cmd = [sys.executable, "-m", "collab", "claim", *paths]
    if label:
        cmd += ["--label", label]
    cmd += ["--reason", "AI agent edit"]

    try:
        subprocess.run(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    except Exception:
        # Never block edits on claim failures.
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
