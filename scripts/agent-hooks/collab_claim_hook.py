#!/usr/bin/env python3
"""Runtime-agnostic ``afterFileEdit`` / ``PostToolUse`` hook (thin shim).

This is a copy-pastable entry point for any IDE/agent that can run a command
after an edit and pass event JSON on stdin (Cursor ``afterFileEdit``, Claude
Code ``PostToolUse`` for ``Edit``/``Write``, custom integrations, ...). All the
real logic lives in :mod:`collab.agent_hooks` so there is a single source of
truth for path extraction and claim invocation.

It claims the edited file(s) as the AI agent (``origin=agent``) so the dashboard
shows **AI Agent** instead of attributing the work to the human developer.

Enable in one of two ways:
    * Pass ``--from-ide-hook`` (what the auto-installer bakes in). Genuine edit
      hooks fire only for agent edits, so this self-enables with no env setup.
    * Or set ``COLLAB_AGENT_HOOKS=1`` for ad-hoc / manual pipelines.

Prefer the automatic installer instead of wiring this by hand::

    collab install-agent-hooks
"""

from __future__ import annotations

import sys


def main() -> int:
    """Delegate to the packaged runner; fail open if the package is missing."""
    try:
        from collab.agent_hooks import run_ide_hook
    except Exception:
        # Never block an edit because the runtime is unavailable.
        return 0
    return run_ide_hook(sys.argv[1:])


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
