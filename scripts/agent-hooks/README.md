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
unique agent id when one is not supplied. The packaged runner
`python -m collab.agent_hooks run-hook` adapts arbitrary IDE hook JSON (read from
stdin) into that command.

## Zero-config: it is installed automatically

You normally do **not** need to do anything. The dev setup scripts
(`scripts/setup-dev.ps1` / `scripts/setup-dev.sh`) run:

```bash
collab install-agent-hooks
```

which idempotently writes/merges, using the project `.venv` interpreter by
absolute path (no `PATH`/activation assumptions):

| File                    | IDE / agent       | Mechanism                                          |
| ----------------------- | ----------------- | -------------------------------------------------- |
| `.cursor/hooks.json`    | Cursor (+ forks)  | `afterFileEdit` (fires on AI edits)                |
| `.claude/settings.json` | Claude Code       | `PostToolUse` for Edit/Write/MultiEdit             |
| `.junie/guidelines.md`  | JetBrains / Junie | Instruction to run `collab claim` (no native hook) |

Re-run any time (safe, idempotent):

```bash
collab install-agent-hooks          # add --force to overwrite an unparsable config
```

The installed command carries `--from-ide-hook`, so the runner **self-enables**:
because `afterFileEdit` / `PostToolUse` fire only for genuine agent edits, no
`COLLAB_AGENT_HOOKS` environment variable is required.

### Why `.cursor/hooks.json` and `.claude/settings.json` are git-ignored

Those two files bake in the **absolute, machine-specific** `.venv` interpreter
path (so attribution works without relying on `PATH`/activation). A single
committed file cannot hold the right interpreter for every OS, so they are
git-ignored and **regenerated per machine** by `collab install-agent-hooks`
(run automatically by `setup-dev`). The portable Junie guidance
(`.junie/guidelines.md`) has no machine path and **is** committed, like
`AGENTS.md` / `CLAUDE.md`.

## Identity (all optional — auto-resolved)

| Env var              | Purpose                                              |
| -------------------- | ---------------------------------------------------- |
| `COLLAB_AGENT_ID`    | Stable unique id; else derived from the session      |
| `COLLAB_AGENT_LABEL` | Human task label shown on the dashboard ("what for") |
| `COLLAB_AGENT_KIND`  | Runtime family for the icon; else auto-detected      |
| `COLLAB_AGENT_HOOKS` | Legacy opt-in for ad-hoc pipelines without the flag  |

## IDEs without a native per-edit hook

Plain **VS Code + Copilot**, **Windsurf**, and **JetBrains/Junie** do not expose
an `afterFileEdit`-style hook to third parties. For those, attribution relies on
the agent running `collab claim` itself — which is exactly what `AGENTS.md` and
the `file-locking` skill instruct agents to do, and what `.junie/guidelines.md`
tells Junie to do.

### GitHub Copilot

Copilot attribution is **guidelines-based** (same pattern as Junie). The file
[`.github/copilot-instructions.md`](../../.github/copilot-instructions.md)
contains a dedicated **"Agent File Claiming"** section that instructs the Copilot
agent to run `collab claim` after every `replace_string_in_file`,
`create_file`, `multi_replace_string_in_file`, and `edit_notebook_file` operation.

The agent runs:

```powershell
$env:COLLAB_AGENT_MODE = "1"
.\.venv\Scripts\python.exe -m collab claim <files> --reason "<description>"
```

This auto-generates a stable agent id, persists it to `.collab/.agent_id`, and
claims every edited file so the dashboard shows **AI Agent** badges instead of
`KirilMT (human)`.

## Manual wiring (only if you opted out of setup)

### Cursor

Copy `cursor-hooks.json` to `.cursor/hooks.json` (project) or `~/.cursor/hooks.json`
(user). Cursor reloads `hooks.json` on save. Or just run `collab install-agent-hooks`.

### Claude Code

Add a `PostToolUse` hook for `Edit`/`Write`/`MultiEdit` that pipes the event to the
runner:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python -m collab.agent_hooks run-hook --from-ide-hook"
          }
        ]
      }
    ]
  }
}
```

### Any other agent / IDE

Either:

- Pipe the post-edit event JSON to `python -m collab.agent_hooks run-hook --from-ide-hook`
  (the standalone shim `python scripts/agent-hooks/collab_claim_hook.py` does the
  same), or
- Call `collab claim <file> --label "<task>"` directly from your agent workflow.

## Notes

- The hook **fails open**: any error exits 0 and never blocks an edit.
- The installed command uses the project `.venv` interpreter, so it works even
  when `PATH` is wrong or the venv is not activated.
- The background watcher must be running (`collab daemon-start`) for locks to sync.
