# Collab Runtime — Troubleshooting

Common problems and fixes when using the `collab` CLI, watcher, and IDE integration.

---

## `ModuleNotFoundError: No module named 'src'`

**Cause:** Stale editable install after upgrading to the flat `collab/` package layout.

**Fix:**

```powershell
.\.venv\Scripts\pip.exe install -e . --force-reinstall
collab --help
```

Console scripts must point to `collab.lock_client:main`, not `src.lock_client:main`.

---

## `collab` not found or wrong Python

**Cause:** Virtual environment not activated or IDE using a different interpreter.

**Fix:**

- Windows: `.\.venv\Scripts\Activate.ps1` then `collab --help`
- Or call explicitly: `.\.venv\Scripts\collab.exe active`
- In VS Code / Cursor: **Python: Select Interpreter** → this repo’s `.venv`

See `AGENTS.md` for agent terminal guidance.

---

## Lock service / Supabase errors

**Symptoms:** `Lock service unavailable`, HTTP errors, empty `active` when you expect locks.

**Checks:**

1. `.env` exists with `SUPABASE_URL` and `SUPABASE_ANON_KEY` (see `.env.example`).
2. Schema applied: run `supabase/schema.sql` in the Supabase SQL editor.
3. Network/VPN allows HTTPS to Supabase.
4. `collab status path/to/file.py` for a single-file probe.

**Admin force-release** (emergency only): set `SUPABASE_SERVICE_ROLE_KEY` and use `collab force-release`.

---

## Locks visible in Supabase but dashboard / `collab active` show zero

**Symptoms:** Row exists in Supabase **Table Editor** for `file_locks`, VS Code status bar may show the lock, but the **Collaborative Explorer** dashboard lists **0 active locks**, or `collab active` printed **No active locks** earlier.

**Common causes:**

1. **Stale dashboard tab after changing `.env`** — The watcher bakes an initial config into the temp dashboard HTML at startup. Older builds kept that snapshot until restart. Current dashboards reload `/collab-runtime-config.json` on each sync; still prefer **`collab dashboard`** or click **Sync** after editing `.env`, and confirm the header chip shows your new project id (subdomain before `.supabase.co`).
2. **`collab` not from this repo’s venv** — Use `.\.venv\Scripts\collab.exe active` (Windows) or activate `.venv` first. A global `collab` on `PATH` may miss `.env` or use another install.
3. **Timing** — `collab active` queries the database directly; if you ran it before the watcher acquired locks, run it again after saving the file.
4. **Schema / Realtime** — Apply `supabase/schema.sql` on the new project (RLS + `supabase_realtime` publication for `file_locks`).

**Verify:**

```powershell
.\.venv\Scripts\collab.exe active
.\.venv\Scripts\collab.exe status testFile
```

Check `logs/collab.log` for `🔒 [LOCKED] your-file` and extension lines like `found 1 lock(s)` (not `Error: none` — that old message only meant “no API error”).

**After migrating Supabase projects:** Update `.env`, restart the watcher (`collab daemon-stop` then save a file or reload the IDE), open a fresh dashboard URL from `logs/collab.log`, and click **Sync**.

---

## Supabase Free tier project paused (inactive 7+ days)

**Symptoms:** Dashboard shows **Paused**, API returns errors, `collab active` fails until you click **Restore** in the [Supabase dashboard](https://supabase.com/dashboard).

**Cause:** Free-tier projects pause after about **7 days** with no database/API activity. Opening the Supabase UI alone does not count; a real query or REST request does.

**Prevent pausing (recommended for this repo):**

1. Add GitHub Actions secrets: `SUPABASE_URL`, `SUPABASE_ANON_KEY` (optional `SUPABASE_SERVICE_ROLE_KEY`).
2. Ensure workflows are enabled on the repository.
3. The scheduled workflow **Supabase Keep-Alive** (`.github/workflows/supabase-keepalive.yml`) runs **twice per week** and reads one row from `file_locks` — enough to reset the timer.
4. You can also run it manually: **Actions → Supabase Keep-Alive → Run workflow**.

**Other options:** Any weekly cron that hits PostgREST (e.g. UptimeRobot), or local dev using `collab` at least once per week. Upgrading to a paid Supabase plan removes auto-pause.

**After restore:** Run `collab daemon-start` again if the watcher was down; verify with `collab active`.

---

## Watcher shows NOT running

**Symptoms:** `collab daemon-status` reports not running right after `daemon-start`.

**Checks:**

1. Wait a few seconds and run `daemon-status` again.
2. Inspect `logs/collab.log` (or `collab/logs/` depending on layout) for crash traces.
3. Run `collab cleanup` to clear stale PID files, then `collab daemon-start`.
4. Confirm `.env` is loaded from project root.

If the watcher works from your IDE but not CLI, compare which Python binary each uses.

---

## Locks on deleted or moved files

**Symptoms:** `collab active` lists paths you deleted locally (e.g. `src/__init__.py` during a refactor).

**Explanation:** Locks live in Supabase until released; deleting a file locally does not auto-release.

**Fix:** `collab release <file_path>` when you intentionally removed the file.

---

## Git hooks fail on commit/push

**Symptoms:** Pre-commit or pre-push runs `validate_code.py` and fails.

**Fix:**

1. Read the failing step in the hook output (lint, mypy, pytest, diff coverage).
2. Run locally: `python scripts/format_code.py` then `python scripts/validate_code.py --quick`.
3. Ensure hooks call the project venv Python (re-run `.\scripts\setup-dev.ps1` if needed).

---

## Extension cannot find runtime

**Symptoms:** VS Code collab-locks extension reports missing `collab`.

**Fix:**

1. Install runtime: `pip install collab-runtime` or `pip install -e .` from this repo.
2. Reload VS Code; check extension settings for custom binary path.
3. See `editors/vscode/collab-locks/` and `editors/pycharm/plugin_notes.md`.

---

## Windows encoding / Unicode in terminal

**Symptoms:** Garbled symbols in CLI output.

**Fix:** Use Windows Terminal or PowerShell 7+; the CLI reconfigures stdout to UTF-8 when supported.

---

## Still stuck?

1. `collab --help` and `collab daemon-status`
2. `docs/API.md` / `docs/CLI_REFERENCE.md` for command details
3. `docs/bug_tracking.md` for known issues
