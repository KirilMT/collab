# Collab Runtime — CLI Reference

Complete reference for the `collab` and `collab-watcher` console scripts.
For conceptual overview and environment variables, see [API.md](./API.md).

**Entry points:** `collab` → `collab.lock_client:main` → `collab.main._run_cli`
**Module form:** `python -m collab [command] ...`

---

## Global usage

```bash
collab [-h] <command> ...
```

Exit codes: most commands use `0` on success, `1` on failure. `daemon-status` uses `0` if the watcher is running, `1` if not.

---

## Lock commands

| Command             | Arguments       | Options    | Description                                    |
| ------------------- | --------------- | ---------- | ---------------------------------------------- |
| `acquire`           | `file_path`     | `--reason` | Acquire lock; prints lock id on success        |
| `release`           | `file_path`     | —          | Release your lock                              |
| `status`            | `file_path`     | —          | Show lock holder or unlocked                   |
| `active`            | —               | —          | List all active locks (may auto-start watcher) |
| `release-all`       | —               | —          | Release all locks held by you                  |
| `acquire-batch`     | `file_paths...` | `--reason` | Acquire multiple files                         |
| `release-batch`     | `file_paths...` | —          | Release multiple files                         |
| `force-release`     | `file_path`     | —          | Admin/service-role force release               |
| `force-release-all` | —               | —          | Admin: release every lock                      |

**Examples:**

```bash
collab acquire src/routes.py --reason "Refactor routes"
collab status src/routes.py
collab active
collab release-all
```

---

## Daemon and watcher

| Command         | Options                                                                    | Description                                      |
| --------------- | -------------------------------------------------------------------------- | ------------------------------------------------ |
| `daemon-start`  | `--interval` (default 5), `--timeout` (minutes, 0=off), `--open-dashboard` | Start background watcher                         |
| `daemon-stop`   | —                                                                          | Stop watcher                                     |
| `daemon-status` | —                                                                          | Print running/stopped; exit code reflects state  |
| `cleanup`       | —                                                                          | Kill orphaned watcher processes; preserves locks |
| `watch`         | See [watch options](#watch-internal)                                       | Foreground watcher (used by daemon-start)        |

**Examples:**

```bash
collab daemon-start --interval 10
collab daemon-status
collab daemon-stop
```

### `watch` (internal)

Used by the daemon launcher; advanced debugging only.

| Option                      | Default | Purpose                        |
| --------------------------- | ------- | ------------------------------ |
| `--interval`                | 5       | Git poll interval (seconds)    |
| `--timeout`                 | 0       | Idle shutdown (minutes)        |
| `--open-dashboard`          | off     | Open dashboard on start        |
| `--daemon`                  | off     | Skip parent-PID liveness check |
| `--parent-pid`              | —       | Exit when parent exits         |
| `--parent-name`             | —       | Logging label                  |
| `--parent-method`           | —       | Parent detection method        |
| `--heartbeat-file`          | —       | Stale file triggers shutdown   |
| `--heartbeat-grace-seconds` | 10      | Heartbeat staleness            |
| `--pid-file`                | —       | PID file path                  |

Spawn contract (validated): `python -m collab.lock_client watch ...`

---

## Sync, dashboard, history

| Command         | Arguments     | Options                  | Description                             |
| --------------- | ------------- | ------------------------ | --------------------------------------- |
| `dashboard`     | —             | —                        | Open collaborative dashboard in browser |
| `reconcile`     | —             | —                        | Sync git status with Supabase locks     |
| `history`       | `[file_path]` | `--limit` (20), `--json` | Lock audit trail                        |
| `history-prune` | —             | `--days` (30)            | Delete old history rows                 |

---

## `collab-watcher` (PyCharm / IDE)

Standalone entry: `collab-watcher` → `collab.live_locks_watcher:main`

```bash
collab-watcher [--interval SECONDS] [--timeout MINUTES] [--debug] [--parent-pid PID]
```

Use when the IDE runs the watcher directly instead of `collab daemon-start`.

---

## Environment variables (CLI-relevant)

| Variable                            | Effect                                       |
| ----------------------------------- | -------------------------------------------- |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | Required for remote locks                    |
| `SUPABASE_SERVICE_ROLE_KEY`         | `force-release*` commands                    |
| `DEVELOPER_ID`                      | Lock owner id (default: git user)            |
| `COLLAB_STATE_DIR` / `COLLAB_HOME`  | State and PID files                          |
| `COLLAB_PROJECT_ROOT`               | Override project root for watcher            |
| `COLLAB_AUTO_START_WATCHER`         | `0` disables auto-start on `active`/`status` |
| `COLLAB_TEST_MODE`                  | Test harness; skips destructive shutdown     |
| `LOCK_STRICT`                       | `1` = strict git hook behavior               |
| `PYTEST_CURRENT_TEST`               | Disables auto watcher start under pytest     |

Full list: [API.md](./API.md#environment-variables).

---

## Related tools

| Tool                                | Purpose                            |
| ----------------------------------- | ---------------------------------- |
| `python run.py`                     | Legacy wrapper → `collab.__main__` |
| `python scripts/collab_git_hook.py` | Git hook helper                    |
| `python scripts/validate_code.py`   | Local CI simulation                |
