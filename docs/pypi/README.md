# collab-runtime — Collaborative File Locking

> Prevent merge conflicts by automatically locking files when a developer starts editing them, using **Supabase Realtime** as the backend.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/KirilMT/collab/blob/main/LICENSE)
[![PyPI](https://img.shields.io/pypi/v/collab-runtime)](https://pypi.org/project/collab-runtime/)

---

## What It Does

`collab-runtime` provides a CLI and background daemon that coordinate file-level locking across a development team in real time.
When a developer opens a file, a lock is acquired in Supabase. Other developers are immediately notified — via desktop notifications, VS Code warnings, or the web dashboard — that the file is in use.

**Key features:**

- ⚡ **Atomic lock acquisition** — Supabase RPC prevents two developers locking the same file simultaneously
- 📡 **Real-time broadcast** — Supabase Realtime pushes lock events to all connected clients instantly
- 🖥️ **Web dashboard** — Live lock status view, force-release controls
- 🔔 **VS Code extension** — Lock warnings, status bar, output channel
- 🪝 **Git hooks** — Pre-commit and pre-push hooks block commits of locked files
- 📋 **Audit trail** — Full lock history with configurable retention

---

## Prerequisites

| Requirement      | Version / Notes                                        |
| ---------------- | ------------------------------------------------------ |
| Python           | 3.10 or higher                                         |
| Supabase account | [supabase.com](https://supabase.com) — free tier works |
| Node.js          | Only required for the VS Code extension                |

---

## Installation

```bash
pip install collab-runtime
```

For a minimum-version install (ensures you get the latest compatible release):

```bash
pip install "collab-runtime>=0.3.2"
```

---

## Quick Start

### 1 — Create the Database Schema

In your Supabase project, open **SQL Editor** and run the contents of [`supabase/schema.sql`](https://github.com/KirilMT/collab/blob/main/supabase/schema.sql).

This creates the `file_locks` table, `file_locks_history` audit table, the atomic `acquire_lock()` RPC, Row Level Security policies, and Realtime publication.

### 2 — Configure Environment Variables

Create a `.env` file in your project root:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_anon_key_here
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key   # Required for force-release
DEVELOPER_ID=your_name                             # Optional, defaults to git user.name
LOCK_STRICT=0                                      # 1 = block on lock errors, 0 = warn only
COLLAB_AGENT_ID=agent-my-task                      # Optional: unique id per AI agent session
COLLAB_AGENT_LABEL=refactor-auth                   # Optional display label
COLLAB_AGENT_MODE=1                                # Auto-generate/persist agent id when unset
COLLAB_OVERLAP_STRICT=0                            # 1 = block git push on cross-branch overlaps (fail-closed)
COLLAB_OVERLAP_FETCH=auto                          # auto = fetch only in strict; 1/0 to force/skip
COLLAB_OVERLAP_LINE_LEVEL=1                        # 0 = file-level instead of git merge-tree (line-level)
COLLAB_OVERLAP_REMOTE=                             # override the auto-detected remote (e.g. upstream)
COLLAB_PR_CLAIMS=0                                 # 1 = keep a pushed branch's files claimed until merged
```

> **Keep `SUPABASE_SERVICE_ROLE_KEY` private — never commit it to version control.**

### Multi-agent usage (same GitHub user, multiple AI agents)

When one developer runs several AI agents in the same repo, give each agent its own identity so
locks do not collide (effective owner is `developer_id` + `agent_id`):

```bash
# Agent A
export COLLAB_AGENT_ID=agent-refactor-auth
collab whoami
collab acquire src/auth.py --reason "Refactor auth"

# Agent B (different id) — conflicts with A on the same file
export COLLAB_AGENT_ID=agent-fix-tests
collab acquire src/auth.py
```

CLI flags `--agent-id` and `--agent-label` override env vars. Use `collab active --mine` to list
only locks held by the current human + agent pair.

**Existing Supabase projects:** apply the `agent_id` / `agent_label` columns and updated
`acquire_lock` function from
[`supabase/schema.sql`](https://github.com/KirilMT/collab/blob/main/supabase/schema.sql) in the SQL
Editor (fresh installs already include them).

### 3 — Verify Connection

```bash
collab active
```

If connected, this lists all currently active locks (empty on a fresh setup).

### 4 — Install Git Hooks (optional)

```bash
collab init-hooks
```

This installs `pre-commit`, `post-commit`, `pre-push`, and `commit-msg` hooks into the current
repository. The hooks acquire locks for staged files, block commits that conflict with another
developer's lock, and release locks after a successful push. By default, Collab warns about
overlaps across unmerged branches during push; set `COLLAB_OVERLAP_STRICT=1` to block the push
when overlaps are detected (fail-closed, and the hook first runs `git fetch` so branches pushed
from other clones are seen). Overlap is confirmed at line level via `git merge-tree`, so edits to
different regions of the same file are not flagged, and the comparison remote is auto-detected.
For enforcement that cannot be bypassed with `git push --no-verify`,
add the `PR Overlap Guard` GitHub Action to your branch-protection required checks.
For **edit-time** protection across open PRs, set `COLLAB_PR_CLAIMS=1`: a pushed branch's
files stay claimed (so other developers are warned/blocked as they edit) until the branch is
merged or deleted — this requires re-running `supabase/schema.sql` (idempotent migration). The hooks
resolve the project `.venv` first,
so **commits from VS Code / Cursor Source Control behave the same as a venv-activated terminal**.

Existing non-collab hooks are preserved; pass `--force` to overwrite them.

---

## CLI Reference

```bash
# Show resolved developer and agent identity
collab whoami

# Show all active locks across the team
collab active
collab active --mine

# Lock a file before editing
collab acquire path/to/file.py --reason "Implementing feature X"

# Release a lock when done
collab release path/to/file.py

# Check lock status for a specific file
collab status path/to/file.py

# Release all locks held by you (including your own AI-agent locks)
collab release-all

# Force release (requires SUPABASE_SERVICE_ROLE_KEY)
collab force-release path/to/file.py
collab force-release-all

# Batch operations
collab acquire-batch path/to/a.py path/to/b.py --reason "Refactoring"
collab release-batch path/to/a.py path/to/b.py

# Install git hooks into the current repo (offline)
collab init-hooks
collab init-hooks --force

# Reconcile local and remote lock state
collab reconcile

# Lock history and retention
collab history
collab history path/to/file.py --limit 50
collab history-prune --days 30

# Background watcher daemon
collab daemon-start
collab daemon-start --interval 10 --timeout 480
collab daemon-status
collab daemon-stop

# Foreground watcher (diagnostics)
collab watch --interval 5 --timeout 0

# Web dashboard (opens in browser)
collab dashboard

# Cleanup orphaned watcher processes
collab cleanup
```

---

## VS Code Extension

The optional VS Code extension provides lock-on-open warnings, a status bar indicator, and one-click dashboard access.

**Install from source** (extension is bundled in the [GitHub repository](https://github.com/KirilMT/collab)):

1. Press `F1` → **Developer: Install Extension from Location...**
2. Select the `editors/vscode/collab-locks/` directory
3. Reload VS Code

Once installed, the extension automatically starts and stops the background daemon with your VS Code window.

---

## How It Works

```
Developer opens file
      │
      ▼
collab acquire (CLI or extension)
      │
      ▼
Supabase RPC: acquire_lock()   ◄─── atomic, unique constraint
      │                                prevents double-locking
      ▼
Supabase Realtime broadcast
      │
      ├─► VS Code extension  →  lock warning popup
      ├─► Web dashboard      →  live status update
      └─► Desktop notification (via plyer)
```

Lock release follows the same path in reverse and writes an entry to `file_locks_history` for audit.

---

## Security

- Lock correctness is enforced at the database level via atomic RPC — no client-side race conditions.
- Force-release requires `SUPABASE_SERVICE_ROLE_KEY`; regular releases are scoped to the owning developer.
- Row Level Security (RLS) is configured on all tables via `supabase/schema.sql`.
- Never commit secrets; use `.env` for local configuration only.

---

## Links

- 📦 **PyPI:** [pypi.org/project/collab-runtime](https://pypi.org/project/collab-runtime/)
- 🐙 **GitHub:** [github.com/KirilMT/collab](https://github.com/KirilMT/collab)
- 🐛 **Issues:** [github.com/KirilMT/collab/issues](https://github.com/KirilMT/collab/issues)
- 📖 **Full docs:** [github.com/KirilMT/collab/tree/main/docs](https://github.com/KirilMT/collab/tree/main/docs)
- 🗺️ **Roadmap:** [GitHub Projects](https://github.com/KirilMT/collab/projects)

---

## License

MIT — see [LICENSE](https://github.com/KirilMT/collab/blob/main/LICENSE) for details.
