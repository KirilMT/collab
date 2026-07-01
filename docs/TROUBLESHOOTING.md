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

## Cannot delete a finished worktree folder (Windows: "folder in use")

**Symptoms:** After finishing work in a Git **worktree** — often after switching to a new chat in a
Cursor **Agents** window without closing the window — you cannot delete the worktree folder, or
`git worktree remove` complains the directory is busy. On Windows, Explorer reports the folder (or
`.venv`) is in use.

**Cause:** A background watcher is still running for that worktree and holds file handles. Closing a
single chat inside an open Agents window does not stop the watcher — Cursor exposes no per-chat
lifecycle signal — so the watcher lives until the **window** closes.

**Fix (recommended — deterministic, from any directory):**

```bash
collab worktree-unregister /path/to/worktree-a
# or, equivalently:
collab daemon-stop --worktree /path/to/worktree-a
```

This stops **only** that worktree's watcher and heartbeat keeper (never other worktrees), releasing
its handles so the folder can be deleted.

**Automatic reap:** If you delete the worktree folder or run `git worktree remove` first, the
watcher detects the missing worktree and self-exits within one poll interval (Layer 3), typically a
few seconds — no manual command needed. Closing the Agents window remains the reliable "clean up
everything for this window" action.

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

## Commit works in terminal but fails in VS Code / Cursor Source Control

**Symptoms:** **Commit** or **Commit & Push** from the IDE shows `Git: [collab] Checking locks for staged files...` and then fails or appears hung. The same commit from an integrated terminal (often with `.venv` activated) succeeds.

**Cause:** IDE git runs hooks in a subprocess that usually does **not** inherit your activated virtual environment. Collab lock checks may succeed, but chained hooks (`pre-commit run`, `language: system` validators) can still resolve **system** `python` and miss venv-only tools (`yamllint`, project linters, etc.). Cursor’s git UI also tends to show only the **last collab-branded line**, which can look like a collab-only failure while validation failed later.

**Fix:**

1. **Reinstall the packaged hooks** from a current runtime so the IDE-safe templates are in place:

   ```bash
   pip install -U collab-runtime
   collab init-hooks --force
   ```

   These hooks prepend the project `.venv` to `PATH` **before** lock checks and validations, print `[collab] Locks OK - running project validations...` at the lock/validation handoff, and call `python -m collab.githooks` (no collab-repo files required in the consumer).

2. Open **View → Output → Git** (or **Show Command Output** in the error dialog) for the full hook log—not only the modal title.
3. Pin the extension CLI if needed: set `collab.cliPath` to `${workspaceFolder}/.venv/Scripts/collab.exe` (Windows) or `${workspaceFolder}/.venv/bin/collab` (macOS/Linux) in workspace settings. (This affects the extension's watcher/dashboard detection, **not** the git commit path.)
4. Confirm **Python: Select Interpreter** points at this repo’s `.venv` (affects terminals and tasks, not git hooks directly).
5. If you maintain a **custom** `scripts/hooks/pre-commit` overlay instead of `collab init-hooks`, mirror the same **venv-first `PATH`** export and `python -m collab.githooks acquire-staged` call.

**Still failing?** Run the same commit from Git Bash without activating venv:

```powershell
git commit -m "test: hook probe"
```

If that fails too, fix hook/`PATH` resolution before blaming the IDE.

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

## PR claims not holding after push (`COLLAB_PR_CLAIMS=1`)

**Symptoms:** With `COLLAB_PR_CLAIMS=1`, files changed on a pushed branch should stay locked as
persistent **claims** until the PR is merged/deleted, but the locks disappear right after `git push`
— especially when the background daemon is running.

**Cause & fix (#181):**

1. **Missing Supabase migration.** The claim columns (`is_pr_claim`, `claim_branch`, `claimed_at`)
   and RPCs (`release_all_except`, `release_stale_claims`) must exist in the target database. If they
   are absent, the daemon logs a clear warning at startup and the pre-push path warns and falls back
   to a full release. Apply `supabase/schema.sql` (or the claims migration) to your Supabase project,
   then restart the daemon. Verify the warning is gone in `logs/collab.log`.
2. **Stale daemon from before the fix.** Older watcher builds deleted claim rows unconditionally
   during release, racing the pre-push hook. Update collab (`pip install -e .` / `pip install -U
collab-runtime`), then `collab daemon-stop` and `collab daemon-start`. The current daemon is
   **claim-aware**: it never deletes `is_pr_claim=true` rows and promotes pushed-branch files to
   claims itself.
3. **Verify:** after pushing, run `collab active` — the pushed-branch files should still be listed
   (as claims). They are released automatically once the branch is **merged or deleted** on the
   remote (git-only reconcile), or after the DB safety-net expiry (default 30 days).

---

## Updated git hooks not taking effect on collaborators' clones

**Symptoms:** A hook template changed upstream (e.g. the claim-aware pre-push message), but a
teammate's clone still runs the old hook.

**Fix (#181):** Re-run `collab init-hooks` (or `./scripts/setup-dev.*`, which calls it). Installed
hooks carry a fingerprint marker (`# collab-hook v=<version> fp=<fingerprint>`); when the packaged
template's fingerprint differs, collab **auto-updates** the hook — no `--force` needed. Pre-commit-
framework-managed slots are detected and skipped (manage those via `.pre-commit-config.yaml`); a
custom hook is backed up to `<hook>.bak` before being overwritten when `--force` is passed.

---

## Still stuck?

1. `collab --help` and `collab daemon-status`
2. `docs/API.md` / `docs/CLI_REFERENCE.md` for command details
3. [GitHub Issues](https://github.com/KirilMT/collab/issues?q=is%3Aissue+label%3A%22type%3A+bug%22) for known issues
