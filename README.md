# Collab Runtime — Collaborative File Locking

Prevents merge conflicts by automatically locking files when a developer starts editing them, using Supabase Realtime as the backend.

---

## Prerequisites

- **Python** 3.10+
- **Supabase** account with a project ([supabase.com](https://supabase.com))
- **Node.js** (only for the VS Code extension)
- **Git** (for pre-commit hooks integration)

---

## 5-Minute Setup Guide

### 1. Create the Database Schema

Open your Supabase project's **SQL Editor** and run the contents of `schema.sql`:

**Steps:**

1. Open your Supabase project dashboard
2. Navigate to **SQL Editor** (left sidebar)
3. Click **New Query**
4. Copy-paste the full contents of [schema.sql](schema.sql) from this repository
5. Click **Run**

This creates:

- `file_locks` table (active locks)
- `file_locks_history` table (audit trail)
- `acquire_lock()` RPC function (atomic lock acquisition)
- Row Level Security policies
- Realtime publication
- Auto-history trigger on lock release
- Automatic history retention (30 days by default)

### 2. Run the Development Setup

One command handles everything — dependencies, `.env` configuration, and VS Code integration:

**Windows (PowerShell):**

```powershell
.\scripts\setup-dev.ps1
```

**Linux/macOS (Bash):**

```bash
./scripts/setup.sh
```

The script automatically:

- Creates a Python virtual environment (`.venv`)
- Installs Python dependencies (`supabase`, `psutil`, `plyer`, etc.)
- Prompts for Supabase credentials if `.env` is missing
- Copies git pre-commit hooks
- Installs repository tooling dependencies from `package.json` (Prettier and YAML plugin)

`node_modules` in the repository root is expected for formatting/validation scripts and is not part of the runtime lock engine.

### 3. Environment Variables

After setup, verify your `.env` at the project root has these values:

| Variable                    | Description                                                     |
| --------------------------- | --------------------------------------------------------------- |
| `SUPABASE_URL`              | Your Supabase project URL (from Project Settings → API)         |
| `SUPABASE_ANON_KEY`         | Anonymous/public key (from Project Settings → API)              |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key (**required** for dashboard force-release)     |
| `LOCK_STRICT`               | If `1`, git hooks block on lock errors. Default `0` (warn only) |

> **Important:** `SUPABASE_SERVICE_ROLE_KEY` is needed for the dashboard's Force Release button. Without it, only your own locks can be released.

### 4. Verify Setup

After setup, verify the connection:

```bash
collab active
```

If connected, this shows all active locks (initially empty).

---

## Quick Start — Testing the Locking System

Once setup is complete, test the locking system:

**Terminal 1 (Lock a file):**

```bash
collab acquire src/main.py --reason "Testing locking system"
collab status src/main.py
```

**Terminal 2 (View lock in another session):**

```bash
collab active
```

**Then release the lock:**

```bash
collab release src/main.py
```

**View real-time lock changes:**

```bash
collab dashboard
```

Opens in your default browser showing live lock status.

---

## CLI Reference

The `collab` command is installed globally after setup:

```bash
# List all active locks
collab active

# Lock a file
collab acquire path/to/file.py --reason "Feature implementation"

# Release a file you locked
collab release path/to/file.py

# Check file status
collab status path/to/file.py

# Release all your locks
collab release-all

# Force release (admin only)
collab force-release path/to/file.py
collab force-release-all

# Batch lock operations
collab acquire-batch path/to/a.py path/to/b.py --reason "Batch work"
collab release-batch path/to/a.py path/to/b.py

# Reconcile local and remote lock state
collab reconcile

# Lock history and retention
collab history
collab history path/to/file.py --limit 50
collab history-prune --days 30

# Cleanup orphaned watcher processes
collab cleanup

# Start background watcher
collab daemon-start
collab daemon-start --interval 10 --timeout 480

# Check daemon status
collab daemon-status

# Stop the daemon
collab daemon-stop

# Foreground watcher (internal/diagnostics)
collab watch --interval 5 --timeout 0

# Alternate foreground watcher invocation
python -m src.main watch --interval 5 --timeout 0

# View lock dashboard
collab dashboard
```

---

## VS Code Extension

The extension warns you when a locked file is opened and provides dashboard access.

### Installation

This repository does not currently ship extension source code or a guaranteed `.vsix` file.

Use one of these paths:

1. If you have a `collab-locks-*.vsix` artifact, install it from Extensions view via `...` -> `Install from VSIX...`.
2. If no `.vsix` is available, skip extension setup and validate locking with CLI + dashboard (`collab active`, `collab dashboard`).

### Features

- **Lock-on-Open Warning** — popup when opening a locked file
- **Status Bar** — shows current file lock status
- **Output Channel** — watcher logs in **View → Output → Collab Locks**
- **Commands** — quick access via Command Palette

### Notification Channels

Notifications can come from two independent channels:

- **VS Code extension notifications** (when extension is installed): lock warnings, status bar updates, output channel events.
- **Watcher desktop notifications** (runtime via `plyer`): emitted by the watcher for lock/conflict lifecycle events.

If the extension is not available, watcher notifications and CLI/dashboard behavior still work.

### Extension Configuration

Create or update `.env` in your workspace:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_anon_key
DEVELOPER_ID=your_name  # optional: defaults to git config user.name
```

---

## Session Identity & Stability

Collab uses a stable session identity derived from your developer id, host, and project root.
This keeps lock ownership consistent across IDE terminals and avoids accidental token churn
between restarts.

---

## Automatic Lifecycle

- `collab daemon-start` creates a watcher process namespace tied to this repository.
- `collab daemon-status` returns exit code `0` when watcher is running and `1` when not running.
- `collab daemon-stop` requests graceful shutdown and releases process-state artifacts.

If a stale PID marker exists, startup logic attempts cleanup and recovery before launching a
replacement watcher.

---

## Kill the Daemon Manually

If graceful shutdown fails, remove the watcher process manually:

Windows PowerShell:

```powershell
$pidPath = ".daemon.pid"
if (Test-Path $pidPath) {
	$raw = Get-Content $pidPath -Raw
	if ($raw.Trim().StartsWith("{")) {
		$obj = $raw | ConvertFrom-Json
		taskkill /F /PID $obj.pid
	} else {
		taskkill /F /PID [int]$raw
	}
	Remove-Item $pidPath -Force
}
```

---

## Database Schema

The full schema is in `schema.sql` and includes:

- `file_locks` for active locks.
- `file_locks_history` for audit/history.
- `acquire_lock(...)` RPC for atomic lock acquisition.
- release trigger for automatic history writes.
- optional retention pruning support.

---

## Security Model

- Lock correctness is enforced in the application workflow plus atomic RPC behavior.
- Force-release operations require service-role level credentials.
- Keep `SUPABASE_SERVICE_ROLE_KEY` private; never commit it.
- Use `.env` only for local secret storage.

---

## Directory Structure

```
collab/
├── src/
│   ├── lock_client.py          # CLI entry point
│   ├── live_locks_watcher.py   # Background watcher
│   ├── main.py                 # CLI orchestration + module entry point
│   ├── dashboard/              # Web UI
│   └── __init__.py             # Package marker
├── logs/                       # Runtime logs (created dynamically)
├── tests/
│   ├── backend/
│   │   ├── unit/               # Unit tests
│   │   ├── functional/         # Functional tests
│   │   ├── integration/        # Integration tests
│   │   ├── security/           # Security tests
│   │   ├── performance/        # Performance tests
│   │   └── reliability/        # Reliability tests
│   └── frontend/
│       ├── jest/               # Frontend unit placeholder
│       └── playwright/         # Frontend e2e placeholder
├── scripts/
│   ├── setup-dev.ps1           # Windows setup
│   ├── setup.sh                # Linux/macOS setup
│   ├── format_code.py          # Code formatter
│   ├── validate_code.py        # CI validator
│   └── cleanup.py              # Cache cleanup
├── schema.sql                  # Supabase database schema
├── pyproject.toml              # Package configuration
└── README.md                   # This file
```

---

## Runtime Logs

Logs are stored in `logs/` and the directory is created dynamically at runtime:

```
logs/
├── collab.log                  # Production logs
└── test_collab.log             # Test logs
```

View logs in real-time:

```bash
# Linux/macOS
tail -f logs/collab.log

# Windows PowerShell
Get-Content logs/collab.log -Tail 10 -Wait
```

---

## Git Integration

Pre-commit hooks prevent commits of locked files. To use:

```bash
collab acquire path/to/file.py --reason "Fixing bug #123"
git add path/to/file.py
git commit -m "fix: resolve issue"
collab release path/to/file.py
```

---

## Troubleshooting

### `collab active` shows "Connection refused"

**Fix:** Check credentials in `.env`:

```bash
cat .env  # Verify SUPABASE_URL and SUPABASE_ANON_KEY
```

### `collab` command not found

**Fix:** Reinstall the package:

```bash
python -m pip install .
```

### `python collab.py daemon-status` fails with file-not-found

`collab.py` is not a file in this repository.

Use:

```bash
collab daemon-status
```

or:

```bash
python -m src.main daemon-status
```

### VS Code extension not loading

**Fix:**

1. Reload: `Ctrl+Shift+P` → "Developer: Reload Window"
2. Check logs: **View → Output → Collab Locks**

---

## Architecture

### Lock Acquisition

1. `collab acquire file.py` sends lock request to Supabase
2. Supabase atomically adds lock (unique constraint prevents conflicts)
3. Realtime broadcasts lock to all connected clients
4. VS Code shows lock warning if file is open

### Conflict Prevention

- File locks use a unique key on `file_path`
- Only one developer can hold a lock per file
- Merge conflicts prevented by design

### Dashboard

```bash
collab dashboard  # Opens in browser with real-time lock view
```

---

## Development

### Install for Development

```bash
python -m pip install .
```

### Run Tests

```bash
pytest tests/backend tests/frontend
```

### Code Quality

```bash
python scripts/format_code.py
python scripts/validate_code.py
```

### Create Distribution

```bash
python -m build
```

---

## License

MIT License — see LICENSE file for details.

---

## Support

For issues, questions, or feature requests:

1. Check [issues](../../issues)
2. Open a [new issue](../../issues/new) with reproduction steps
3. See [docs/collab_roadmap.md](docs/collab_roadmap.md) for planned features
