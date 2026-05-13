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
pip install "collab-runtime>=0.2.2"
```

---

## Quick Start

### 1 — Create the Database Schema

In your Supabase project, open **SQL Editor** and run the contents of [`schema.sql`](https://github.com/KirilMT/collab/blob/main/schema.sql).

This creates the `file_locks` table, `file_locks_history` audit table, the atomic `acquire_lock()` RPC, Row Level Security policies, and Realtime publication.

### 2 — Configure Environment Variables

Create a `.env` file in your project root:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_anon_key_here
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key   # Required for force-release
DEVELOPER_ID=your_name                             # Optional, defaults to git user.name
LOCK_STRICT=0                                      # 1 = block on lock errors, 0 = warn only
```

> **Keep `SUPABASE_SERVICE_ROLE_KEY` private — never commit it to version control.**

### 3 — Verify Connection

```bash
collab active
```

If connected, this lists all currently active locks (empty on a fresh setup).

---

## CLI Reference

```bash
# Show all active locks across the team
collab active

# Lock a file before editing
collab acquire path/to/file.py --reason "Implementing feature X"

# Release a lock when done
collab release path/to/file.py

# Check lock status for a specific file
collab status path/to/file.py

# Release all locks held by you
collab release-all

# Force release (requires SUPABASE_SERVICE_ROLE_KEY)
collab force-release path/to/file.py
collab force-release-all

# Batch operations
collab acquire-batch path/to/a.py path/to/b.py --reason "Refactoring"
collab release-batch path/to/a.py path/to/b.py

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
2. Select the `vscode-extension/collab-locks/` directory
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
- Row Level Security (RLS) is configured on all tables via `schema.sql`.
- Never commit secrets; use `.env` for local configuration only.

---

## Links

- 📦 **PyPI:** [pypi.org/project/collab-runtime](https://pypi.org/project/collab-runtime/)
- 🐙 **GitHub:** [github.com/KirilMT/collab](https://github.com/KirilMT/collab)
- 🐛 **Issues:** [github.com/KirilMT/collab/issues](https://github.com/KirilMT/collab/issues)
- 📖 **Full docs:** [github.com/KirilMT/collab/tree/main/docs](https://github.com/KirilMT/collab/tree/main/docs)
- 🗺️ **Roadmap:** [docs/collab_roadmap.md](https://github.com/KirilMT/collab/blob/main/docs/collab_roadmap.md)

---

## License

MIT — see [LICENSE](https://github.com/KirilMT/collab/blob/main/LICENSE) for details.
