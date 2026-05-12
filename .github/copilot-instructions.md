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

## Task Completion

- Always run `python scripts/format_code.py` then `python scripts/validate_code.py` before finishing
- Follow conventional commits: `type(scope): description`
- See [.github/GIT_WORKFLOW.md](.github/GIT_WORKFLOW.md) for the full commit procedure
