<!-- See AGENTS.md for all shared context (stack, conventions, testing, boundaries, agent behavior). -->
<!-- This file contains ONLY GitHub Copilot-specific additions. -->

# GitHub Copilot — Tool-Specific Instructions

> **Primary reference:** [AGENTS.md](./AGENTS.md) — read it first.

---

## CRITICAL ENVIRONMENT RULE - SHELL COMPATIBILITY (Permanent)

Never assume the shell. Detect the active terminal shell first, then use only shell-native syntax for all commands.

Mandatory behavior:

1. At the beginning of every new session, run environment detection for the current shell.
2. After detection, use only commands compatible with that shell.
3. Do not mix shell syntaxes in a single command.
4. If complex logic is needed, write a shell-native script (`.ps1` for PowerShell, `.sh` for bash/zsh).
5. This rule has highest priority.

PowerShell patterns:

```powershell
Write-Host "=== ENVIRONMENT DETECTION ===" -ForegroundColor Green
$PSVersionTable
Get-Command git
Get-Content <file> -TotalCount 300
Get-Content <file> -Tail 50
(Get-Content <file> | Measure-Object -Line).Lines
Get-Content <file> | Select-String -Pattern "..."
```

Bash/zsh patterns:

```bash
echo "=== ENVIRONMENT DETECTION ==="
echo "$SHELL"
git --version
head -n 300 <file>
tail -n 50 <file>
wc -l <file>
grep -n "..." <file>
```

Before outputting any terminal command, internally verify it is compatible with the detected shell (or is plain `git`). If unsure, run detection again and use shell-native file-reading/search patterns.

---

## Task Tracking — Issue-First Workflow (MANDATORY)

Every task must trace to a GitHub Issue. When the user requests work outside of
existing Issues:

1. **Restate the task** to the user to confirm scope.
2. **Search** [existing issues](https://github.com/KirilMT/collab/issues) for duplicates.
3. **If no issue exists → create one NOW**, before touching any code:
   - Title: concise, action-oriented (e.g., `feat: add --version and daemon-restart CLI commands`)
   - Body: brief description of what's being done
   - Labels: `type:*` + `priority:*` + `scope:*` + `status: in-progress`
   - Assignee: yourself
   - Project: add to [Collab Roadmap](https://github.com/users/KirilMT/projects/2)
   - Board Status: set to `In Progress`
4. **Branch** — Recommended: `feat/issue-<N>-desc` for features, `fix/issue-<N>-desc` for bugs. Other common patterns (e.g. `feat/<N>-desc`, `feature/<N>-description`) are also acceptable — the issue number must be present.
5. **Commit body** — include `Closes #<N>` or `Fixes #<N>`

### When NOT to create an issue

- The task is explicitly linked to an existing issue the user referenced
- The user says "no issue needed" (rare — default is to create one)

See `AGENTS.md` → "Issue-First Workflow" for the canonical policy.

---

## Conflict Prevention (MANDATORY — before ANY work)

No two developers or AI agents may work on the same task at the same time. Before touching code:

1. Check the [GitHub Issue](https://github.com/KirilMT/collab/issues) — already assigned? **STOP**.
2. Check the [Collab Roadmap](https://github.com/users/KirilMT/projects/2) — Status already `In Progress`? **STOP**.
3. Claim: assign to self, set `status: in-progress` label, set board Status to `In Progress`.
4. Run `collab active` for file-level conflicts.

See `AGENTS.md` → "Conflict Prevention Protocol" for the full policy.

## File Locking

Before editing any file:

1. List all files the task will touch.
2. Run `collab active`.
3. If a target file is locked by another developer, stop and report.
4. Never force-release another developer's lock.

### Troubleshooting

If you suspect a lock is stale or the watcher is unresponsive:

1. Run `collab daemon-status`.
2. If stopped, run `collab daemon-start`.
3. Check `collab active`.
4. If still blocked, check `collab status path/to/file.py` to see the exact owner.

### Worktree cleanup when finishing a chat

Cursor has no per-chat teardown event, so a per-worktree watcher keeps running
(and locks the folder/venv on Windows) until you release it. When done with a
worktree, run `collab worktree-unregister <path>` (or `collab daemon-stop
--worktree <path>`), or delete the folder / `git worktree remove <path>` to let
it auto-reap. See `AGENTS.md` → "Worktree Lifecycle & Chat-Switch Cleanup".

---

## Tool Limitations

GitHub Copilot does **not** have access to:

- Browser automation tools
- Full terminal command execution in all IDEs

**Implications:**

- Provide clear manual testing instructions instead of automated browser verification
- Include step-by-step verification steps in PR descriptions

---

## Verification Workflow

Since Copilot cannot run browser tests automatically:

1. **Identify test plan** — check `docs/` for test procedures
2. **Provide instructions** — give clear steps to verify changes manually
3. **Create checklist** — if no test plan exists, add one to the PR description
4. **Wait for confirmation** — do not mark task complete until user confirms

---

## Agent File Claiming (MANDATORY — after EVERY file edit)

GitHub Copilot does **not** have native `afterFileEdit` hooks like Cursor or Claude Code.
To maintain correct agent attribution in the dashboard, you **MUST** claim every file you
edit **immediately** after the edit completes.

### When to claim

Run `collab claim` after **every** use of these tools:

| Tool                           | Claim trigger                       |
| ------------------------------ | ----------------------------------- |
| `replace_string_in_file`       | After each replacement              |
| `multi_replace_string_in_file` | After all replacements in the batch |
| `create_file`                  | After creating the file             |
| `edit_notebook_file`           | After each notebook cell edit       |

### How to claim

Always use the project's venv interpreter. On this Windows workspace:

```powershell
$env:COLLAB_AGENT_MODE = "1"
.\.venv\Scripts\python.exe -m collab claim <file_paths...> --reason "<what was done>"
```

**Rules:**

1. Claim ALL files touched in the edit operation — never skip any.
2. The `--reason` must be a short, human-readable summary of the edit (e.g. `"Add ping CLI command"`).
3. If the claim command fails (locked by another developer), **report it** — do not silently continue.
4. Batch multiple files from a single `multi_replace_string_in_file` into one `collab claim` call.
5. After claiming a batch of files across multiple independent edits, you may combine them into a single `collab claim` call for efficiency.

### Why this matters

Without claiming, all file edits show as the **human developer** (`origin=human`) in the
dashboard and lock history. Claiming ensures the dashboard correctly shows **AI Agent**
badges with task labels, giving the team full visibility into who (or what) is working on
each file.

This is the same pattern that Junie uses (guidelines-based claiming instead of native
IDE hooks). The Cursor and Claude Code agents don't need this because their native
`afterFileEdit` / `PostToolUse` hooks fire automatically.

---

## Task Completion

- Always run `python scripts/format_code.py` then `python scripts/validate_code.py` before finishing
- Follow conventional commits: `type(scope): description`
- See [.github/GIT_WORKFLOW.md](.github/GIT_WORKFLOW.md) for the full commit procedure
