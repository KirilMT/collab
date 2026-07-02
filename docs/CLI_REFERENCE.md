# Collab Runtime — CLI Reference

Complete reference for the `collab` and `collab-watcher` console scripts.
For conceptual overview and environment variables, see [API.md](./API.md).

**Entry points:** `collab` → `collab.lock_client:main` → `collab.main._run_cli`
**Module form:** `python -m collab [command] ...`

---

## Global usage

```bash
collab [--agent-id ID] [--agent-label LABEL] [--version] [-h] <command> ...
```

| Global option   | Env fallback         | Description                                              |
| --------------- | -------------------- | -------------------------------------------------------- |
| `--agent-id`    | `COLLAB_AGENT_ID`    | Stable agent identity (multi-agent workflows)            |
| `--agent-label` | `COLLAB_AGENT_LABEL` | Human task label for display ("why / what for")          |
| `--agent-kind`  | `COLLAB_AGENT_KIND`  | AI runtime family for the dashboard icon (auto-detected) |
| `--version`     | —                    | Print the installed collab-runtime version and exit      |

Set `COLLAB_AGENT_MODE=1` to auto-generate and persist an agent id when none is provided
(useful for AI agent sessions). Run `collab whoami` to see the resolved identity.

For automatic, **runtime-agnostic** agent attribution, run `collab install-agent-hooks` (done
automatically by `setup-dev`) to wire Cursor / Claude Code / Junie to `collab claim` with zero manual
steps — see [`scripts/agent-hooks/`](../scripts/agent-hooks/README.md). The background watcher always
attributes bulk git changes to the human; set `COLLAB_WATCHER_AGENT_ID` only if you intentionally
want a dedicated agent watcher.

Exit codes: most commands use `0` on success, `1` on failure. `daemon-status` uses `0` if the watcher is running, `1` if not.

---

## Lock commands

| Command             | Arguments       | Options               | Description                                                                                                   |
| ------------------- | --------------- | --------------------- | ------------------------------------------------------------------------------------------------------------- |
| `acquire`           | `file_path`     | `--reason`            | Acquire lock; prints lock id on success                                                                       |
| `claim`             | `file_paths...` | `--reason`, `--label` | Claim file(s) as an AI agent edit (`origin=agent`); auto-generates a unique agent id. Used by IDE edit hooks. |
| `release`           | `file_path`     | —                     | Release your lock                                                                                             |
| `status`            | `file_path`     | —                     | Show lock holder or unlocked                                                                                  |
| `active`            | —               | `--mine`              | List all active locks (may auto-start watcher)                                                                |
| `whoami`            | —               | —                     | Show resolved developer and agent identity                                                                    |
| `release-all`       | —               | `--identity-only`     | Release every lock held by you (incl. your AI-agent locks); `--identity-only` keeps the current identity only |
| `acquire-batch`     | `file_paths...` | `--reason`            | Acquire multiple files                                                                                        |
| `release-batch`     | `file_paths...` | —                     | Release multiple files                                                                                        |
| `force-release`     | `file_path`     | —                     | Admin/service-role force release                                                                              |
| `force-release-all` | —               | —                     | Admin: release every lock                                                                                     |

**Examples:**

```bash
collab acquire src/routes.py --reason "Refactor routes"
collab status src/routes.py
collab active
collab release-all
```

---

## Project setup

| Command               | Options   | Description                                                                                                                                                          |
| --------------------- | --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `init-hooks`          | `--force` | Install collab git hooks (`pre-commit`, `post-commit`, `pre-push`, `commit-msg`) into the current repository                                                         |
| `install-agent-hooks` | `--force` | Wire IDE AI-agent attribution hooks (Cursor `.cursor/hooks.json`, Claude Code `.claude/settings.json`, JetBrains `.junie/guidelines.md`) into the current repository |

`init-hooks` is an **offline** filesystem operation — it does not contact Supabase. It copies the
hooks bundled with the installed wheel into the repo's git hooks directory (honoring
`core.hooksPath` and worktrees). Existing **non-collab** hooks are preserved unless `--force` is
passed. The installed hooks resolve the project `.venv` first, so commits from VS Code / Cursor
Source Control behave the same as a venv-activated terminal.

Each installed hook is **content-stamped** with a fingerprint marker
(`# collab-hook v=<version> fp=<fingerprint>`). On re-run (#181), `init-hooks`:

- **auto-updates** collab hooks whose fingerprint has drifted from the packaged template — **no
  `--force` needed**, so template changes reach existing clones on the next run;
- leaves **up-to-date** collab hooks untouched;
- **never clobbers** a pre-commit-framework-managed slot (even with `--force`); the collab
  lock lifecycle runs through `.pre-commit-config.yaml` for those slots instead;
- **backs up** a custom hook to `<hook>.bak` before overwriting it when `--force` is passed.

The command prints what happened per hook: `Installed`, `Updated (template changed)`,
`Backed up before overwrite`, `Managed by pre-commit (...), left as-is` (framework-owned
slots — expected, not actionable), and `Skipped existing custom hooks (rerun with --force
to back up & replace)`.

```bash
pip install collab-runtime
collab init-hooks            # install / auto-update collab hooks in the current repo
collab init-hooks --force    # also overwrite custom hooks (backed up to <hook>.bak first)
```

The hooks invoke `python -m collab.githooks <acquire-staged|release-all>` from the project venv; no
collab-repo files need to be present in the consumer repository.

The bundled `pre-commit` / `pre-push` templates also:

- prepend the project `.venv` to `PATH` **before** lock checks and validations;
- verify `collab-runtime` is importable and print canonical install guidance otherwise (an unrelated
  PyPI `collab` package will not expose `collab.githooks`);
- honor `LOCK_STRICT=1` to block on lock/runtime problems;
- print `[collab] Locks OK - running project validations...` at the lock/validation handoff so an IDE
  git UI does not mistake a later validation failure for a collab lock failure.

### Consumer hook strategies (two-layer model)

`collab` is a **library**; consumers own their git workflow. Pick the layer that matches the repo:

| Repo type                             | Recommended approach                                                                                                                                                                                                                                             |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Greenfield / library, no custom hooks | `collab init-hooks` — use the bundled templates as-is.                                                                                                                                                                                                           |
| App with its own validation/hooks     | Keep a repo-owned overlay (e.g. `scripts/hooks/`) installed by your setup script; mirror the template's venv-first `PATH`, runtime guard, and handoff message. Optionally call `python -m collab.githooks acquire-staged` for fast, watcher-aware batch locking. |
| Repo with unrelated existing hooks    | `collab init-hooks` (without `--force`) installs only where no non-collab hook exists; merge manually otherwise.                                                                                                                                                 |

`collab init-hooks` is **not** meant to overwrite a maintained consumer overlay. For overlay repos,
treat `collab/hook_templates/` as the **reference implementation** and re-apply your app-specific
additions (runtime fingerprinting, extra install guidance, etc.) on top.

---

## Daemon and watcher

| Command               | Options                                                                    | Description                                          |
| --------------------- | -------------------------------------------------------------------------- | ---------------------------------------------------- |
| `daemon-start`        | `--interval` (default 5), `--timeout` (minutes, 0=off), `--open-dashboard` | Start background watcher                             |
| `daemon-stop`         | `--worktree <path>`                                                        | Stop watcher (current worktree, or a specific one)   |
| `daemon-status`       | —                                                                          | Print running/stopped; exit code reflects state      |
| `daemon-restart`      | `--interval` (default 5), `--timeout` (minutes, 0=off), `--open-dashboard` | Stop and restart the watcher daemon                  |
| `worktree-unregister` | `[worktree_path]` (defaults to cwd)                                        | Stop the watcher + heartbeat keeper for one worktree |
| `cleanup`             | —                                                                          | Kill orphaned watcher processes; preserves locks     |
| `watch`               | See [watch options](#watch-internal)                                       | Foreground watcher (used by daemon-start)            |

**Examples:**

```bash
collab daemon-start --interval 10
collab daemon-status
collab daemon-restart --interval 10
collab daemon-stop

# Finish a worktree/chat and release its file handles so the folder can be
# deleted — runs from ANY directory and only affects that worktree:
collab worktree-unregister /path/to/worktree-a
collab daemon-stop --worktree /path/to/worktree-a   # equivalent
```

### Per-worktree teardown (`worktree-unregister`)

`worktree-unregister` (and the equivalent `daemon-stop --worktree <path>`) stops **only** the
watcher and heartbeat keeper bound to the given worktree's isolated state namespace — and reaps any
orphaned `collab.exe` launcher wrapper bound to that same namespace (Windows), matching the cleanup
`daemon-stop` performs for the current worktree. Use it when you finish work in a Git worktree — for
example switching chats in a Cursor **Agents** window — so the worktree's `.venv`/file handles are
released and the folder can be deleted (important on Windows, where a live watcher or wrapper pins
the directory). Launcher reaping is strictly `--pid-file`-scoped to the target, so sibling worktrees
are never touched. It is safe to run from any directory, idempotent (a no-op when nothing is
running), and returns a non-zero exit code only from `worktree-unregister` when no watcher was
found. Deleting a worktree folder or running `git worktree remove` is also reaped automatically
within one poll interval by the watcher's Layer-3 self-check.

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

## Sync, dashboard, history, diagnostics

| Command         | Arguments     | Options                                                                                   | Description                                                             |
| --------------- | ------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `dashboard`     | —             | —                                                                                         | Open collaborative dashboard in browser                                 |
| `reconcile`     | —             | `--prune-orphans`, `--max-age-hours`, `--foreign-auto-watch`, `--aggressive`, `--dry-run` | Sync git status with Supabase locks; optional orphan prune (#182)       |
| `prune-orphans` | —             | `--max-age-hours`, `--foreign-auto-watch`, `--aggressive`, `--dry-run`                    | Release orphan lock rows left by dead worktrees / killed daemons (#182) |
| `history`       | `[file_path]` | `--limit` (20), `--json`                                                                  | Lock audit trail                                                        |
| `history-prune` | —             | `--days` (30)                                                                             | Delete old history rows                                                 |
| `ping`          | —             | —                                                                                         | Check Supabase connectivity + latency                                   |
| `info`          | —             | —                                                                                         | Comprehensive status overview                                           |
| `logs`          | —             | `--lines` (50), `--follow` (`-f`)                                                         | Show recent collab log entries                                          |

**Examples:**

```bash
collab ping
collab info
collab logs --lines 20
collab logs --follow

# Heal Auto-Watch lock rows left by a dead worktree / killed daemon (#182)
collab prune-orphans
collab prune-orphans --dry-run
collab reconcile --prune-orphans --aggressive
# admin + service role only:
collab prune-orphans --foreign-auto-watch --max-age-hours 24
```

`prune-orphans` releases **this developer's** non–PR-claim locks that are no longer
in local in-progress work. It refuses when git status is unreliable. A fresh
watcher also runs an own-lock orphan prune on startup. See
[TROUBLESHOOTING.md](./TROUBLESHOOTING.md#phantom-locks-after-a-deleted-worktree-or-killed-daemon-182).

---

## `collab-watcher` (PyCharm / IDE)

Standalone entry: `collab-watcher` → `collab.live_locks_watcher:main`

```bash
collab-watcher [--interval SECONDS] [--timeout MINUTES] [--debug] [--parent-pid PID]
```

Use when the IDE runs the watcher directly instead of `collab daemon-start`.

---

## Environment variables (CLI-relevant)

| Variable                                 | Effect                                                  |
| ---------------------------------------- | ------------------------------------------------------- |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`      | Required for remote locks                               |
| `SUPABASE_SERVICE_ROLE_KEY`              | `force-release*`; foreign `--foreign-auto-watch` prune  |
| `DEVELOPER_ID`                           | Lock owner id (default: git user)                       |
| `COLLAB_STATE_DIR` / `COLLAB_HOME`       | State and PID files                                     |
| `COLLAB_PROJECT_ROOT`                    | Override project root for watcher                       |
| `COLLAB_AUTO_START_WATCHER`              | `0` disables auto-start on `active`/`status`            |
| `COLLAB_TEST_MODE`                       | Test harness; skips destructive shutdown                |
| `COLLAB_ORPHAN_LOCK_MAX_AGE_HOURS`       | Foreign Auto-Watch age for `prune-orphans` (default 24) |
| `COLLAB_ORPHAN_AUTO_WATCH_GRACE_SECONDS` | Aggressive own Auto-Watch grace (default 30)            |
| `LOCK_STRICT`                            | `1` = strict git hook behavior                          |
| `PYTEST_CURRENT_TEST`                    | Disables auto watcher start under pytest                |

Full list: [API.md](./API.md#environment-variables).

---

## Related tools

| Tool                                | Purpose                                    |
| ----------------------------------- | ------------------------------------------ |
| `python run.py`                     | Legacy wrapper → `collab.__main__`         |
| `python -m collab.githooks`         | Packaged git hook runtime (consumer repos) |
| `python scripts/collab_git_hook.py` | Git hook helper (collab repo internal)     |
| `python scripts/validate_code.py`   | Local CI simulation                        |
