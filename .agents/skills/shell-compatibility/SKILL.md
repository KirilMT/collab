---
name: shell-compatibility
description: Mandatory environment rule for detecting the active shell and using only shell-native commands.
---

# Shell Compatibility (Critical)

## Non-Negotiable Rule

Never assume the shell. Detect the active terminal shell first, then use only shell-native syntax for all commands.

Do not mix shell syntaxes in one command.

## Mandatory Session Behavior

At the beginning of every new session, run shell detection for the active terminal:

PowerShell:

```powershell
Write-Host "=== ENVIRONMENT DETECTION ===" -ForegroundColor Green
$PSVersionTable
Get-Command git
```

Bash/zsh:

```bash
echo "=== ENVIRONMENT DETECTION ==="
echo "$SHELL"
git --version
```

Then follow these rules:

1. Use only commands compatible with the detected shell.
2. If complex logic is required, write a shell-native script (`.ps1` for PowerShell, `.sh` for bash/zsh).
3. Never fall back to a different shell syntax after a failure.
4. This rule has highest priority.

## Approved Patterns by Shell

PowerShell:

```powershell
Get-Content <file> -TotalCount 300
Get-Content <file> -Tail 50
(Get-Content <file> | Measure-Object -Line).Lines
Get-Content <file> | Select-String -Pattern "..."
```

Bash/zsh:

```bash
head -n 300 <file>
tail -n 50 <file>
wc -l <file>
grep -n "..." <file>
```

Before outputting any terminal command, internally verify it is compatible with the detected shell (or is plain `git`). If unsure, run shell detection again and use shell-native file-read/search patterns.
