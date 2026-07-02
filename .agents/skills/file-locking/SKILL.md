---
name: file-locking
description: Use BEFORE any file modification. Check locks, edit safely with automatic lock handling, and never force-release others' locks.
---

# File Locking Workflow

## Use this skill when

- You are about to edit one or more files for any reason (bug, feature, refactor, docs)
- You need to check whether a file is currently locked by another developer
- You are about to run commands that modify files (for example `python scripts/format_code.py`, codemods, or bulk refactors)

## Do not use this skill when

- You are only reading files (status checks, searches, reviews)
- You are running tests or validation that do not modify files

---

## Why This Matters

This repository uses a **collaborative file locking system** (Supabase Realtime) to prevent merge conflicts when multiple developers or AI agents work simultaneously. Editing a file that another developer has locked will cause conflicts.

**Rule: Never edit a file without verifying it is either unlocked or already locked by the current developer.**

---

## Step 1: Identify All Files to Change

Before touching anything, enumerate the **complete list of files** the task requires. Do this upfront — discovering mid-task that a file is locked wastes effort.

---

## Step 2: Check Lock Status

Check all active locks at once:

```bash
collab active
```

Optional targeted check:

```bash
collab status path/to/file.py
```

Cross-reference the active lock list against your planned file list.

---

## Step 3: Decision Gate

For each file in your list:

| Lock state | Owner                 | Action                                                                  |
| ---------- | --------------------- | ----------------------------------------------------------------------- |
| Unlocked   | —                     | Proceed with edits (lock is acquired automatically when editing starts) |
| Locked     | **Current developer** | Proceed — already owned by the active dev                               |
| Locked     | **Another developer** | **STOP** — see below                                                    |

### If a file is locked by another developer

**Do not edit the file.** Instead:

1. Report to the user exactly which files are locked and by whom.
2. Ask the user whether to wait, contact the owner, or proceed with a reduced scope.
3. **ABSOLUTE AI RULE:** force-releasing another developer's lock is forbidden.

---

## Step 4: Claim Your Edits as an AI Agent (strict attribution)

So the dashboard correctly shows that an **AI agent** edited a file (and what for) — instead of
attributing it to the human — claim the files you edit:

```bash
collab claim path/to/file.py --label "<short task>" --reason "AI agent edit"
```

- `collab claim` marks the lock as `origin=agent` with a unique agent identity (auto-generated and
  persisted; override with `COLLAB_AGENT_ID`). Set `COLLAB_AGENT_LABEL` once per task for a friendly
  "what for" label.
- This is **automated by setup**: `setup-dev` runs `collab install-agent-hooks`, which wires
  Cursor (`.cursor/hooks.json`), Claude Code (`.claude/settings.json`) and JetBrains/Junie
  (`.junie/guidelines.md`) to claim agent edits automatically (see `scripts/agent-hooks/`). In
  Cursor and Claude Code this happens with no manual steps. In IDEs without a native edit hook
  (plain VS Code + Copilot, JetBrains), run `collab claim` for the files you edit.
- The background watcher attributes everything else to the human, so only files an agent actually
  edits are shown as AI-agent work.

---

## Step 5: Finishing a Worktree Chat (Release the Watcher)

Each Git worktree — including a per-chat worktree in a Cursor Agents window —
runs its **own** lock watcher in an isolated state namespace. Cursor exposes
**no per-chat teardown event**, so that watcher does **not** stop automatically
when you switch chats or finish the task. A lingering watcher keeps the
worktree's files and virtualenv open and, on Windows, blocks deleting the folder
("folder in use"). This is the hard constraint tracked in issue #168.

**When you finish work in a worktree, release its watcher — do one of:**

| Action                             | Command / Step                                                                      | Effect                                                                                                                            |
| ---------------------------------- | ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Deterministic teardown (preferred) | `collab worktree-unregister <path>` (alias: `collab daemon-stop --worktree <path>`) | Stops only that worktree's watcher + keeper and reaps its orphaned `collab.exe` wrapper. Sibling worktrees untouched. Idempotent. |
| Remove the worktree                | delete the folder or `git worktree remove <path>`                                   | Layer 3 auto-reaps: the watcher self-exits within one poll interval.                                                              |
| Clean up everything                | close the Agents window                                                             | Window-scoped keeper reaps all watchers that window launched.                                                                     |

`worktree-unregister` is safe from **any** directory and safe to run when nothing
is running (returns "no running watcher"). Treat this cleanup as part of "task
done": never leave an orphaned watcher behind.

> Note: `COLLAB_STATE_DIR` is a test/custom-deployment knob that forces a single
> shared state namespace for all roots (mutually exclusive with per-worktree
> isolation). Leave it unset in normal use so each worktree stays isolated.

---

## Step 6: Orphan Lock Rows After Ungraceful Exit (#182)

Process teardown (Step 5) stops the watcher. If the daemon was killed hard or the
worktree folder disappeared before graceful release, **Supabase lock rows can
remain** and still show on `collab active` / the dashboard as the human developer's
Auto-Watch locks — even though no live watcher holds them.

| Situation                                                                     | Action                                                                                          |
| ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Your own stale Auto-Watch locks, no local dirty/unpushed work for those paths | `collab prune-orphans` or `collab reconcile --prune-orphans`                                    |
| Preview only                                                                  | `collab prune-orphans --dry-run`                                                                |
| Faster release of very recent own Auto-Watch orphans                          | `collab prune-orphans --aggressive`                                                             |
| Other developers' old Auto-Watch rows (admin only)                            | `collab prune-orphans --foreign-auto-watch --max-age-hours 24` with `SUPABASE_SERVICE_ROLE_KEY` |

**Rules (same as force-release policy):**

- Prefer `worktree-unregister` first so graceful release runs when possible.
- `prune-orphans` only releases **this developer's** non-claim locks that are
  **not** in the local in-progress set (dirty/staged/unpushed + sibling worktrees).
- Never releases PR claims (`is_pr_claim`).
- Never force-releases another developer's locks without admin + `--foreign-auto-watch`.
- If git status is unreliable, prune refuses and exits non-zero (no mass release).
- A fresh watcher (`daemon-start` / `watch`) also runs an own-lock orphan prune on
  startup.

Env: `COLLAB_ORPHAN_LOCK_MAX_AGE_HOURS` (default 24 for foreign Auto-Watch),
`COLLAB_ORPHAN_AUTO_WATCH_GRACE_SECONDS` (default 30 with `--aggressive`).

DB safety net (optional, re-run `supabase/schema.sql`): `release_stale_auto_locks(72)`

- pg_cron expires Auto-Watch rows older than 72h if no client ever prunes.
