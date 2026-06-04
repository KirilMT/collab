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

Open your Supabase project's **SQL Editor** and run the contents of `supabase/schema.sql`:

**Steps:**

1. Open your Supabase project dashboard
2. Navigate to **SQL Editor** (left sidebar)
3. Click **New Query**
4. Copy-paste the full contents of [supabase/schema.sql](supabase/schema.sql) from this repository
5. Click **Run**

This creates:

- `file_locks` table (active locks)
- `file_locks_history` table (audit trail)
- `acquire_lock()` RPC function (atomic lock acquisition)
- Row Level Security policies
- Realtime publication
- Auto-history trigger on lock release
- Automatic history retention (30 days by default)

### 2. Install the Package

The `collab` command-line tool is distributed as `collab-runtime` on PyPI.

**Development Setup (from source):**
One command handles everything — dependencies, `.env` configuration, and IDE integration.
The included `.env.example` is pre-configured with the shared team Supabase project, so
collaborative file locking works immediately after setup — no manual key entry required.

**Windows (PowerShell):**

```powershell
.\scripts\setup-dev.ps1
```

**Linux/macOS (Bash):**

```bash
./scripts/setup-dev.sh
```

**End-User Install (via pip):**

```bash
pip install collab-runtime
```

The setup script automatically:

- Creates a Python virtual environment (`.venv`)
- Installs `collab-runtime` and its dependencies
- Copies `.env.example` → `.env` with pre-configured Supabase team credentials
- Copies git pre-commit hooks
- Installs IDE extensions (VS Code, etc.)

### 3. Environment Variables

After setup, your `.env` at the project root is ready to use. The team Supabase URL and
anon key come pre-configured from `.env.example`:

| Variable                    | Description                                                         |
| --------------------------- | ------------------------------------------------------------------- |
| `SUPABASE_URL`              | Your Supabase project URL (pre-configured from `.env.example`)      |
| `SUPABASE_ANON_KEY`         | Anonymous/public key (pre-configured; safe to commit — see below)   |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key (**required** for dashboard force-release)         |
| `LOCK_STRICT`               | If `1`, git hooks block on lock errors. Default `0` (warn only)     |
| `COLLAB_AGENT_ID`           | Optional stable id for an AI agent session (multi-agent locking)    |
| `COLLAB_AGENT_LABEL`        | Optional task label shown on the dashboard (e.g. `refactor-auth`)   |
| `COLLAB_AGENT_KIND`         | Optional AI runtime for the dashboard icon (auto-detected)          |
| `COLLAB_AGENT_MODE`         | Set to `1` to auto-generate/persist an agent id when unset          |
| `COLLAB_AGENT_HOOKS`        | Set to `1` to enable the IDE edit hook that auto-claims agent edits |
| `COLLAB_WATCHER_AGENT_ID`   | Opt in to a dedicated agent watcher (default: watcher = human)      |

> **Important:** `SUPABASE_SERVICE_ROLE_KEY` is needed for the dashboard's Force Release button.
> Without it, only your own locks can be released. Obtain it from a maintainer — **never commit it**.
>
> The `SUPABASE_ANON_KEY` is a public client key and safe to commit. Security relies on
> Row Level Security (RLS) policies, not key secrecy. If the anon key rotates, `.env.example`
> will be updated and collaborators notified.

### Multi-agent usage (same GitHub user, multiple AI agents)

When one developer runs several AI agents in the same repo, give each agent its own identity so
locks do not collide:

```bash
# Terminal / agent A
set COLLAB_AGENT_ID=agent-refactor-auth
collab whoami
collab acquire src/auth.py --reason "Refactor auth"

# Terminal / agent B (different id)
set COLLAB_AGENT_ID=agent-fix-tests
collab acquire src/auth.py   # conflict — locked by agent-refactor-auth
```

For existing Supabase projects, re-run [supabase/schema.sql](supabase/schema.sql) to add the
`agent_id` / `agent_label` / `origin` / `agent_kind` columns and the updated `acquire_lock` function
(the script is idempotent; fresh installs already include them).

#### Strict user-vs-agent attribution

The dashboard distinguishes **human** edits from **AI agent** edits and shows _what the agent is
working on_ — not a cryptic id. Attribution is decided by an explicit signal:

- The background watcher locks bulk git changes as the **human** (`User` chip), even inside an AI
  IDE. So normal work is never mislabelled as an agent.
- An AI agent claims the files it edits, producing an **"AI Agent"** badge (runtime icon + task).
  Make this automatic by wiring your IDE's edit hook to `collab claim` — see
  [scripts/agent-hooks/](scripts/agent-hooks/README.md). It is **runtime-agnostic** (Cursor, Claude
  Code, Copilot, Gemini, ...). Enable with `COLLAB_AGENT_HOOKS=1` and optionally
  `COLLAB_AGENT_LABEL="<task>"`.

Agents can also claim explicitly:

```bash
collab claim src/auth.py --label "refactor-auth" --reason "Refactor auth"
```

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
collab acquire collab/main.py --reason "Testing locking system"
collab status collab/main.py
```

**Terminal 2 (View lock in another session):**

```bash
collab active
```

**Then release the lock:**

```bash
collab release collab/main.py
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

# View lock dashboard
collab dashboard
```

---

## VS Code Extension

The extension warns you when a locked file is opened and provides dashboard access.

### Installation

The extension is primarily distributed via the **Collab Runtime** Python package. When you open a repository in VS Code, the extension will automatically check for the runtime and prompt you to install it if missing.

**Automatic Install:**

1. Open VS Code in a collab-enabled repository.
2. If prompted, click **[Install via pip]**.

**Manual Install (from source):**

1. Press `F1` -> `Developer: Install Extension from Location...`
2. Select `editors/vscode/collab-locks/`
3. Reload VS Code

**CLI Install:**
Production `scripts/setup.ps1` / `scripts/setup.sh` download the latest release `.vsix` and install it when a supported editor CLI is on `PATH`. **Development setup** (`scripts/setup-dev.ps1` / `scripts/setup-dev.sh`) repeats this with stronger IDE detection and resolves `code` / `cursor` (and siblings) from common install locations on Windows and macOS when they are not on `PATH`, which fixes installs from integrated terminals (for example **Cursor**).

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

The extension reads `.env` from your workspace root. The team Supabase URL and anon key are
pre-configured via `.env.example`, so no manual setup is needed for basic locking. Add the
service-role key (obtained from a maintainer) if you need force-release:

```env
SUPABASE_URL=https://<team-project>.supabase.co
SUPABASE_ANON_KEY=<team-anon-jwt>
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key_here  # optional: ask maintainer
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

The full schema is in `supabase/schema.sql` and includes:

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
├── collab/
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
│   │   └── (performance/ and reliability/ removed — empty placeholders deleted for clean/optimized structure)
│   └── frontend/
│       └── playwright/         # E2E + visual regression (config, CI job, helpers, snapshots, deterministic fixtures)
├── scripts/
│   ├── setup-dev.ps1           # Windows dev setup
│   ├── setup.sh                # Linux/macOS setup
│   ├── git-hooks/              # Collab git hook templates
│   ├── install_hooks.sh        # Installs templates into .git/hooks
│   ├── format_code.py          # Code formatter
│   ├── validate_code.py        # CI validator
│   └── cleanup.py              # Cache cleanup
├── supabase/
│   └── schema.sql              # Supabase database schema
├── editors/
│   ├── vscode/collab-locks/    # VS Code / Cursor extension
│   └── pycharm/                # PyCharm run configuration template
├── docs/
│   └── pypi/README.md          # PyPI package readme
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

**Fix:** Ensure your virtual environment is active or install the package:

```bash
pip install collab-runtime
```

### `python collab.py daemon-status` fails with file-not-found

`collab.py` is not a file in this repository.

Use:

```bash
collab daemon-status
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

## Documentation

| Document                                      | Description                              |
| --------------------------------------------- | ---------------------------------------- |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md)       | System design and data flow              |
| [API.md](docs/API.md)                         | CLI overview and environment variables   |
| [CLI_REFERENCE.md](docs/CLI_REFERENCE.md)     | Full command reference                   |
| [SECURITY.md](docs/SECURITY.md)               | Subprocess hardening and secret handling |
| [PERFORMANCE.md](docs/PERFORMANCE.md)         | Validation and watcher tuning            |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues and fixes                  |
| [collab_roadmap.md](docs/collab_roadmap.md)   | Future enhancements                      |

---

## Support

For issues, questions, or feature requests:

1. Check [issues](../../issues)
2. Open a [new issue](../../issues/new) with reproduction steps
3. See [docs/collab_roadmap.md](docs/collab_roadmap.md) for planned features
