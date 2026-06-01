# Collab Runtime API Reference

This document provides a comprehensive reference for the `collab` command-line interface (CLI) and the underlying runtime behaviors.

---

## CLI Overview

The `collab` command is the primary entry point for all lock operations, daemon management, and diagnostics.

### General Usage

```bash
collab [command] [options]
```

### Core Commands

#### `active`

List all currently active locks across the entire Supabase project.

```bash
collab active
```

#### `acquire`

Acquire a lock for a specific file.

```bash
collab acquire path/to/file.py --reason "Working on feature X"
```

- **Options:**
  - `--reason`, `-r`: A short description of why the lock is being acquired (highly recommended).

#### `release`

Release a lock you currently hold.

```bash
collab release path/to/file.py
```

#### `status`

Check the lock status of a specific file.

```bash
collab status path/to/file.py
```

#### `release-all`

Release all locks held by the current developer session.

```bash
collab release-all
```

---

### Batch Operations

#### `acquire-batch`

Acquire locks for multiple files in a single command.

```bash
collab acquire-batch file1.py file2.py --reason "Batch update"
```

#### `release-batch`

Release multiple locks in a single command.

```bash
collab release-batch file1.py file2.py
```

---

### Administrative Commands

#### `force-release`

Release a lock held by another developer (requires `SUPABASE_SERVICE_ROLE_KEY`).

```bash
collab force-release path/to/file.py
```

#### `force-release-all`

Release **all** locks in the database (requires `SUPABASE_SERVICE_ROLE_KEY`). Use with extreme caution.

```bash
collab force-release-all
```

---

### Daemon Management

The daemon is a background process that monitors file system events and synchronizes local state with Supabase.

#### `daemon-start`

Start the background watcher daemon.

```bash
collab daemon-start [--interval SECONDS] [--timeout MINUTES]
```

- **Options:**
  - `--interval`, `-i`: Polling interval in seconds (default: 30).
  - `--timeout`, `-t`: Idle timeout in minutes after which the daemon stops (default: 480).

#### `daemon-stop`

Gracefully stop the background daemon.

```bash
collab daemon-stop
```

#### `daemon-status`

Check if the daemon is currently running. Exit code `0` if running, `1` if not.

```bash
collab daemon-status
```

---

### Diagnostics & Utility

#### `dashboard`

Open the real-time web dashboard in your default browser.

```bash
collab dashboard
```

#### `history`

View the lock history (audit trail).

```bash
collab history [path/to/file.py] [--limit N]
```

#### `reconcile`

Force-synchronize local lock state with the remote Supabase state.

```bash
collab reconcile
```

#### `cleanup`

Clean up stale PID files and orphaned watcher processes.

```bash
collab cleanup
```

---

## Environment Variables

| Variable                    | Description                                                         |
| :-------------------------- | :------------------------------------------------------------------ |
| `SUPABASE_URL`              | Your Supabase project URL.                                          |
| `SUPABASE_ANON_KEY`         | Supabase anonymous/public key.                                      |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key (for force-release).                      |
| `DEVELOPER_ID`              | Custom developer identifier (defaults to git user.name).            |
| `COLLAB_DEVELOPER_ID`       | Same as `DEVELOPER_ID` (preferred name).                            |
| `COLLAB_AGENT_ID`           | Stable agent identity for multi-agent workflows.                    |
| `COLLAB_AGENT_LABEL`        | Human-readable agent/task label.                                    |
| `COLLAB_AGENT_MODE`         | If `1`, auto-generate/persist `agent_id` when unset.                |
| `COLLAB_STATE_DIR`          | Directory for storing PID and state files (defaults to `.collab/`). |
| `COLLAB_LOG_LEVEL`          | Logging level (DEBUG, INFO, WARNING, ERROR).                        |
| `LOCK_STRICT`               | If `1`, block on lock errors during git hooks.                      |
