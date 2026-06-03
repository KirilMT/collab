# Agent edit hooks — automatic AI-agent attribution

These hooks make **strict user-vs-agent attribution** automatic: whenever an AI
agent edits a file, the lock is claimed as that agent (`origin=agent`) with a
stable, unique identity. Files the human edits are locked by the background
watcher as the human (`origin=human`). The dashboard then shows a clear
**"AI Agent"** badge (with runtime + task) instead of a generic `cursor`/`user`.

The mechanism is **runtime-agnostic**. Everything funnels through one command:

```bash
collab claim <file...> --label "<task>"
```

`collab claim` always attributes to an AI agent and auto-generates/persists a
unique agent id when one is not supplied. Any IDE that can run a command after an
edit can wire it up; `collab_claim_hook.py` adapts arbitrary hook JSON to that
command.

## Enable

The hook is **safe by default** and does nothing unless explicitly enabled, so it
never claims files during normal human work or test runs:

```powershell
# PowerShell
$env:COLLAB_AGENT_HOOKS = "1"
$env:COLLAB_AGENT_LABEL = "fix-ci-dashboard"   # optional task ("what for")
```

```bash
# bash/zsh
export COLLAB_AGENT_HOOKS=1
export COLLAB_AGENT_LABEL=fix-ci-dashboard
```

Identity is resolved automatically (overridable via env):

| Env var              | Purpose                                              |
| -------------------- | ---------------------------------------------------- |
| `COLLAB_AGENT_HOOKS` | Master on/off switch for the hook (default off)      |
| `COLLAB_AGENT_ID`    | Stable unique id; else derived from the session      |
| `COLLAB_AGENT_LABEL` | Human task label shown on the dashboard ("what for") |
| `COLLAB_AGENT_KIND`  | Runtime family for the icon; else auto-detected      |

## Wire it to your IDE

### Cursor

Copy `cursor-hooks.json` to `.cursor/hooks.json` (project) or `~/.cursor/hooks.json`
(user). Cursor reloads `hooks.json` on save.

### Claude Code

Add a `PostToolUse` hook for `Edit`/`Write` in your Claude settings that pipes the
event to the script:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python scripts/agent-hooks/collab_claim_hook.py"
          }
        ]
      }
    ]
  }
}
```

### Any other agent / IDE

Either:

- Pipe the post-edit event JSON to `python scripts/agent-hooks/collab_claim_hook.py`, or
- Call `collab claim <file> --label "<task>"` directly from your agent workflow
  (set `COLLAB_AGENT_ID`/`COLLAB_AGENT_LABEL` once per task).

## Notes

- The hook **fails open**: any error exits 0 and never blocks an edit.
- Ensure `python` on `PATH` resolves to the environment where `collab-runtime` is
  installed (or use the project's `.venv` python in the command).
- The background watcher must be running (`collab daemon-start`) for locks to sync.
