# AGENTS.md - Collab Runtime

> Single source of truth for all AI coding assistants (Antigravity, Gemini CLI, Claude Code, GitHub Copilot, Jules).
> Tool-specific overrides live in `CLAUDE.md` and `.github/copilot-instructions.md`.
> Workflow-heavy procedures live in `.agents/skills/`.

---

## CRITICAL ENVIRONMENT RULE - SHELL COMPATIBILITY (Permanent)

Never assume the shell. Detect the active terminal shell first, then use only shell-native syntax for all commands.

Mandatory behavior:

1. At the beginning of every new session, run environment detection for the current shell.
2. After detection, use only commands compatible with that shell.
3. Do not mix shell syntaxes in a single command.
4. If complex logic is needed, write a shell-native script (`.ps1` for PowerShell, `.sh` for bash/zsh).
5. This rule has highest priority.

PowerShell patterns:

```powershell
Write-Host "=== ENVIRONMENT DETECTION ===" -ForegroundColor Green
$PSVersionTable
Get-Command git
Get-Content <file> -TotalCount 300
Get-Content <file> -Tail 50
(Get-Content <file> | Measure-Object -Line).Lines
Get-Content <file> | Select-String -Pattern "..."
```

Bash/zsh patterns:

```bash
echo "=== ENVIRONMENT DETECTION ==="
echo "$SHELL"
git --version
head -n 300 <file>
tail -n 50 <file>
wc -l <file>
grep -n "..." <file>
```

Before outputting any terminal command, internally verify it is compatible with the detected shell (or is plain `git`). If unsure, run detection again and use shell-native file-reading/search patterns.

---

## Project Overview

Collab Runtime is a standalone collaborative file-locking package that provides atomic lock acquisition, real-time conflict detection, and lock lifecycle tooling through Supabase Realtime and PostgREST.

**Environment:** `.env` at project root (copy from `.env.example`).

---

## Stack

| Layer                | Technology                                           | Version              |
| -------------------- | ---------------------------------------------------- | -------------------- |
| Language             | Python                                               | 3.12 (target >=3.10) |
| Runtime backend      | Supabase Realtime + PostgREST                        | -                    |
| Dashboard            | HTML/CSS/JS-compatible template surface              | -                    |
| Python linting       | Ruff, Flake8, Mypy, Pylint                           | -                    |
| Python formatting    | isort -> Black -> docformatter (strict order)        | -                    |
| Frontend lint/format | Prettier, ESLint-ready hooks, Playwright-ready hooks | -                    |
| Backend tests        | Pytest (coverage >=92%)                              | -                    |
| Frontend tests       | Jest/Playwright-ready structure                      | -                    |
| Package managers     | pip (`requirements.txt`), npm (`package.json`)       | -                    |

---

## Repository Structure

```text
collab/
├── collab/                     # Sole Python package (CLI, lock client, watcher, dashboard)
├── scripts/
│   ├── git-hooks/              # Collab hook templates (installed into .git/hooks)
│   ├── install_hooks.sh
│   ├── setup.ps1 / setup.sh
│   └── setup-dev.ps1 / setup-dev.sh
├── supabase/schema.sql         # Database schema for consumer Supabase projects
├── editors/
│   ├── vscode/collab-locks/    # VS Code / Cursor extension
│   └── pycharm/                # PyCharm run configuration template
├── docs/pypi/README.md         # PyPI readme (pyproject.toml readme)
├── tests/
├── .agents/
├── .github/
├── run.py
├── pyproject.toml
├── pytest.ini
└── README.md
```

---

## Setup

```powershell
# Production setup
.\scripts\setup.ps1

# Development setup
.\scripts\setup-dev.ps1

# Runtime command surface
collab --help
```

```bash
# Linux/macOS — development setup (hooks, prettier, extension tooling)
./scripts/setup-dev.sh
```

### AI agent terminals and the virtual environment

Automated and IDE-agent shells often run **without** `VIRTUAL_ENV` set, even after setup. Do not assume `python` / `pip` on `PATH` point at this repo’s `.venv`.

Prefer the project interpreter explicitly:

- Windows PowerShell: `.\.venv\Scripts\python.exe` and `.\.venv\Scripts\pip.exe`
- Linux/macOS: `./.venv/bin/python` and `./.venv/bin/pip`

Or activate first: `.\.venv\Scripts\Activate.ps1` (Windows) / `source .venv/bin/activate` (Unix), then run `python` / `pip`.

`scripts/format_code.py` and `scripts/validate_code.py` resolve `.venv` Python automatically and run dev tools as `python -m <tool>` so they work in agent shells without activation.

In **Cursor** or **VS Code**, use **Python: Select Interpreter** and point at this repo’s `.venv` so integrated terminals and tasks use the same environment as `.\.venv\Scripts\python.exe`.

---

## Key Scripts

| Script                             | Purpose                                            | Usage                                            |
| ---------------------------------- | -------------------------------------------------- | ------------------------------------------------ |
| `scripts/format_code.py`           | Auto-fix formatting for backend/frontend/docs/yaml | `python scripts/format_code.py`                  |
| `scripts/validate_code.py`         | Full CI simulation (lint + test + coverage)        | `python scripts/validate_code.py`                |
| `scripts/validate_code.py --quick` | Smart targeted validation for changed files        | `python scripts/validate_code.py --quick`        |
| `scripts/generate_tests.py`        | Generate test stubs for new Python modules         | `python scripts/generate_tests.py collab/foo.py` |

---

## Testing

### Commands

```bash
# Backend
pytest tests/backend
pytest --cov=collab --cov=scripts tests/backend

# Frontend placeholders (future-ready)
npm test
npm run test:frontend:e2e:fast   # quick validate / local mock + contract
npm run test:frontend:e2e:ci     # full validate_code + CI (chromium + live)
npm run test:frontend:e2e:firefox  # optional cross-browser snapshots

# Full validation
python scripts/validate_code.py
```

### Coverage Thresholds (IMMUTABLE)

- Backend total coverage: >=92 percent
- Backend diff coverage on changed lines: >=95 percent
- Frontend checks (ESLint + Playwright E2E/visual) remain enabled; real tests exist under `tests/frontend/playwright/`

### Test Organization

Tests must follow this structure (cleaned of empty placeholder directories for maintainability, accuracy, and user preference for optimized project layout; only directories containing real tests or test assets are listed):

- `tests/backend/unit/` (module-grouped subdirectories under it—lock_client/, live_locks_watcher/, scripts/—for scalable organization of the 900+ tests)
- `tests/backend/functional/`
- `tests/backend/integration/`
- `tests/backend/security/`
- `tests/frontend/unit/` (Jest — `dashboard-format.js` helpers)
- `tests/frontend/playwright/` (E2E + visual + live smoke + Supabase contract; dense seeded fixtures)
- `tests/packaging/` (packaging/install smoke tests)

---

## Code Conventions

### Architecture

- Separation of concerns: Python logic in `.py`, template in `.html`, future JS/CSS in external files.
- SOLID, DRY, KISS.
- Comments explain why, not what.
- All public functions/classes need docstrings.
- No hardcoded secrets. Use `.env`.

### Formatting (STRICT ORDER)

1. `isort`
2. `black`
3. `docformatter`

Or run `python scripts/format_code.py`.

### Error Handling

- No silent failures.
- Catch, log, and handle errors explicitly.
- Keep runtime and daemon shutdown paths safe on all platforms.

---

## Key Boundaries (Do Not Touch to Bypass Quality)

| Item                                      | Rule                                                    |
| ----------------------------------------- | ------------------------------------------------------- |
| `pyproject.toml`, `pytest.ini`, `.flake8` | Immutable for bypasses. Fix code, not config.           |
| `.github/workflows/`                      | Immutable for quality gates. Never skip failing checks. |
| GitHub Issues                             | Ask user before adding new bug reports.                 |
| Coverage thresholds                       | Immutable. Add tests, do not lower thresholds.          |

---

## Documentation Structure

- Core docs: `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/PERFORMANCE.md`, `docs/TROUBLESHOOTING.md`, `docs/CLI_REFERENCE.md`
- Docs must stay current and concise.
- Roadmap is tracked via the [Collab Roadmap](https://github.com/users/KirilMT/projects/2) GitHub Project and [Milestones](https://github.com/KirilMT/collab/milestones).
- Bugs are tracked via [GitHub Issues](https://github.com/KirilMT/collab/issues) with appropriate labels.

---

## GitHub Workflow: Issues, Projects, and Milestones

### The Triad

| Component                                                         | Purpose                                                     | URL                |
| ----------------------------------------------------------------- | ----------------------------------------------------------- | ------------------ |
| **[GitHub Issues](https://github.com/KirilMT/collab/issues)**     | Individual work items (bugs, features, chores)              | `../../issues`     |
| **[Collab Roadmap](https://github.com/users/KirilMT/projects/2)** | Kanban board using built-in **Status** field (auto-updated) | Project #2         |
| **[Milestones](https://github.com/KirilMT/collab/milestones)**    | Version-targeted groupings                                  | `../../milestones` |

### Label System

Every issue must have labels from ALL four categories:

| Category      | Labels                                                                                                             |
| ------------- | ------------------------------------------------------------------------------------------------------------------ |
| **Type**      | `type: bug`, `type: feature`, `type: enhancement`, `type: docs`, `type: chore`, `type: refactor`, `type: security` |
| **Priority**  | `priority: critical`, `priority: high`, `priority: medium`, `priority: low`                                        |
| **Lifecycle** | `status: triage`, `status: in-progress`, `status: blocked`, `status: needs-review`                                 |
| **Scope**     | `scope: cli`, `scope: daemon`, `scope: dashboard`, `scope: extension`, `scope: ci`, `scope: docs`                  |

### Project Board: Built-in Status Field (Auto-Updated)

The [Collab Roadmap](https://github.com/users/KirilMT/projects/2) uses GitHub's **built-in Status field** (NOT a custom field). This is critical because:

- **Built-in workflows auto-update Status** — when an issue is closed via `Fixes #N`, the project item automatically moves to `Done`. No manual step needed.
- **AI agents can update it** — the built-in Status field is accessible via the standard GitHub API (`status: "In Progress"`, `status: "Done"`). Custom fields require GraphQL node IDs that AI agents cannot discover.
- **GitHub's automation engine** keeps the board in sync with reality — there is zero drift between what the board shows and what's actually happening.

The Status values are GitHub's defaults:

```
Todo  →  In Progress  →  Done
```

### Built-in Workflows (Configured in Project Settings)

The following auto-transitions are enabled on the [Collab Roadmap](https://github.com/users/KirilMT/projects/2) project:

| #   | Workflow                         | Trigger                         | Action                       | Status      |
| --- | -------------------------------- | ------------------------------- | ---------------------------- | ----------- |
| 1   | **Item added to project**        | Issue/PR added to project       | Status → `Todo`              | ✅ ENABLED  |
| 2   | **Item closed**                  | Issue closed (via `Fixes #N`)   | Status → `Done`              | ✅ ENABLED  |
| 3   | **Item reopened**                | Closed issue reopened           | Status → `Todo`              | ✅ ENABLED  |
| 4   | **Pull request merged**          | PR merged                       | Status → `Done` (safety net) | ✅ ENABLED  |
| 5   | **Auto-add to project**          | New/updated item matches filter | Add item to project          | ✅ ENABLED  |
| 6   | **Auto-archive items**           | Item in `Done` for 7 days       | Archive item                 | ✅ ENABLED  |
| 7   | **Pull request linked to issue** | PR links to issue               | Status → `In Progress`       | ⚠️ DISABLED |
| —   | **Auto-close issue**             | —                               | —                            | ❌ DISABLED |
| —   | **Auto-add sub-issues**          | —                               | —                            | ❌ DISABLED |
| —   | **Code changes requested**       | —                               | —                            | ❌ DISABLED |
| —   | **Code review approved**         | —                               | —                            | ❌ DISABLED |

#### Workflow #5: Auto-Add Filter

The auto-add filter is:

```
is:open -label:priority:low
```

**What this means:** Any open issue or PR in the `collab` repo is automatically added to the project board **unless** it has the `priority: low` label.

**Why this filter:**

| Included                     | Excluded        | Rationale                                                  |
| ---------------------------- | --------------- | ---------------------------------------------------------- |
| `priority: critical`         | `priority: low` | Low = "someday/maybe" — stays as GitHub Issue only         |
| `priority: high`             |                 | The board shows **actionable** work, not the icebox        |
| `priority: medium`           |                 |                                                            |
| Items with NO priority label |                 | Forces triage: unlabeled items appear, demanding attention |
| Items with NO status label   |                 | Same forcing function — someone must categorize them       |

**Why DISABLED workflows stay disabled:**

| Workflow                     | Reason                                                                                                        |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Auto-close issue             | Issues must ONLY close via `Fixes #N` in merged PR. Auto-close is dangerous.                                  |
| Auto-add sub-issues          | No sub-issue pattern in use. Could bypass conflict-prevention protocol.                                       |
| Code changes requested       | Manual `status: blocked` label is safer than auto-moving board status.                                        |
| Code review approved         | Review approval ≠ ready to merge. Manual merge decision is safer.                                             |
| Pull request linked to issue | Could auto-set Status to `In Progress` on an issue someone else already claimed — breaks conflict prevention. |

> **Human admin action required (one-time):** These workflows must be enabled/disabled in the GitHub Project UI under **Settings → Workflows**. They are NOT configurable via code — they must be toggled on manually in the GitHub web interface.

### `status:*` Labels (Lifecycle Tracking)

The `status:*` labels track the **review/blocker lifecycle** independently of the board position:

| Label                  | Meaning                                 |
| ---------------------- | --------------------------------------- |
| `status: triage`       | Needs triage before work can start      |
| `status: in-progress`  | Actively being worked on                |
| `status: blocked`      | Waiting on dependency/external decision |
| `status: needs-review` | PR open, awaiting code review           |

> The lifecycle label and the board Status are **independent dimensions**. An issue can be `status: blocked` (label) while its board Status is `In Progress`, or `status: needs-review` while its board Status is `In Progress` (PR is open but not yet merged).

---

## Issue-First Workflow (MANDATORY — every task must trace to a GitHub Issue)

**No code shall be written without a corresponding GitHub Issue.** This is the
single source of truth for WHAT is being done, WHO is doing it, and WHERE it
stands.

### When you receive a task request (direct chat, not from an existing Issue)

1. **Understand the task** — restate it back to the user to confirm scope.
2. **Search existing issues** — is there already an issue for this? If yes, use it.
3. **If no issue exists → create one NOW**, before touching any code:
   - Title: concise, action-oriented (e.g., `feat: add --version and daemon-restart CLI commands`)
   - Body: brief description of what's being done and why
   - Labels: `type:*` + `priority:*` + `scope:*` + `status: in-progress`
   - Assignee: the developer responsible for the task (e.g., the repo owner, GitHub user that is working on this task or whoever requested the work)
   - Project: add to [Collab Roadmap](https://github.com/users/KirilMT/projects/2)
   - Board Status: set to `In Progress`
4. **Branch naming** — Recommended: `feat/issue-<N>-desc` (features) or `fix/issue-<N>-desc` (bugs), where `<N>` is the GitHub issue number. Other common patterns are also acceptable:
   - `feat/<N>-short-desc` or `fix/<N>-short-desc`
   - `feature/<N>-description` or `bugfix/<N>-description`
   - The key rule: the issue number must appear in the branch name so anyone can trace the branch back to its issue.
5. **Commit messages** — include `Closes #<N>` or `Fixes #<N>` in the body so the issue auto-closes on merge.

### Why this matters

- Every commit in `main` links to a tracked, documented work item.
- The [Collab Roadmap](https://github.com/users/KirilMT/projects/2) always reflects reality.
- Auto-close on merge means zero manual issue-closing cleanup.
- The dashboard's lock ownership ties back to a concrete task.

---

## Conflict Prevention Protocol (CRITICAL)

**No two developers or AI agents may work on the same issue at the same time.** This is enforced by a strict "claim before work" protocol:

### Before Starting ANY Work on an Issue:

1. **Check the GitHub Issue** — is it already assigned to someone else? If yes, **STOP**.
2. **Check the Project Board** — is the Status already `In Progress`? If yes, **STOP** and verify the assignee.
3. **Claim the issue** — assign it to yourself AND set `status: in-progress` label AND update the board Status to `In Progress` before writing any code.
4. **If using file-locking** (`collab`), also run `collab active` to verify no file-level conflicts.

### AI Agent Workflow Summary

1. **New bug discovered** → Ask user → Open issue with `type: bug` + `priority:*` + `scope:*` labels + `status: triage` → Add to [Collab Roadmap](https://github.com/users/KirilMT/projects/2) project
2. **Before starting work** → Verify issue is unassigned and board Status is `Todo` → Assign self → Set `status: in-progress` label → Set board Status to `In Progress`
3. **Fix/implement** → Branch (e.g. `feat/issue-<N>-desc` or `fix/<N>-short-desc`) → Code + tests → Format → Validate
4. **Commit** → Include `Fixes #<N>` or `Closes #<N>` in body → Open PR → Set `status: needs-review` label
5. **Merged** → Issue auto-closes → Board Status auto-updates to `Done` (via built-in workflow)
6. **New feature idea** → Open issue → Add to project → `priority: low`

> **⚠️ AI agents CANNOT update custom project fields** (like the old "Stage" field). They CAN update the built-in Status field via the GitHub API. This is why we use the built-in Status field exclusively.

---

## Git and Commit Standards

- Conventional Commits required: `type(scope): description`
- Supported types: `feat`, `fix`, `chore`, `refactor`, `perf`, `remove`, `revert`, `docs`, `test`, `style`, `build`, `ci`
- Never commit directly to `main`
- New branches should be pushed through PR creation flow
- See `.github/GIT_WORKFLOW.md` for full process

---

## Agent Behavior Rules

### Autonomy

- Operate autonomously until final `git commit` and `git push` approval points.
- Auto-run validation and correction loops up to three attempts.

### Decision Making

- Verify through tools before asking user.
- Ask only when genuinely blocked by missing requirements or lock conflicts.

### CI Parity (CRITICAL — Never "Push and Hope")

- **Never push code expecting CI to catch what local validation missed.**
  If CI runs on Linux, simulate Linux locally via `platform.system` mocking.
  If CI runs on Ubuntu, simulate Ubuntu. The local environment MUST match CI
  exactly — no exceptions.
- **diff-cover MUST use `--compare-branch=origin/main`**, not HEAD or HEAD~1.
  This is what CI uses. Comparing against HEAD will always report "no coverage
  information" and hide real gaps.
- **Fix ALL missing lines at once.** Do not target one line at a time.
  Run diff-cover first, read the full list of missing lines, then write
  comprehensive tests covering every line in a single iteration.
- **Never use `# pragma: no cover` or any bypass annotation.** Write proper
  tests that exercise the code path. If a branch is "Windows-only" and CI
  runs on Linux, mock `platform.system` → `"Windows"` and mock the
  platform-specific APIs (e.g. `ctypes.windll` via `MagicMock`).
- **Coverage data corruption:** The `_configure_coverage_data_file()` call
  in `scripts/validate_code.py` routes `.coverage` to a temp directory
  during test collection, corrupting aggregated coverage data. Always set
  `$env:CI = "1"` and `$env:COVERAGE_FILE = "$PWD\.coverage"` before
  running full-suite coverage. The `PYTEST_CURRENT_TEST` guard now
  prevents this in most cases, but explicit env vars are safest.

### Pre-Commit/Push Workflow (MUST follow this order)

1. Run targeted tests: `pytest tests/backend/unit/<changed>/ -q`
2. Generate full coverage: `$env:CI="1"; $env:COVERAGE_FILE="$PWD\.coverage"; pytest tests/backend/ --cov=collab --cov=scripts --cov-report=xml -q`
3. Check diff-cover: `diff-cover coverage.xml --compare-branch=origin/main --fail-under=95`
4. If diff-cover fails: **fix ALL missing lines**, re-run from step 1
5. Format: `python scripts/format_code.py`
6. Full validation: `python scripts/validate_code.py --quick`
7. If all pass → stage, commit, push

### File Locking Protocol

Before editing:

1. Identify all files to be changed.
2. Run `collab active`.
3. If a target file is locked by another developer, stop and report.
4. Never force-release another developer's lock.

See skill `file-locking` for full procedure.

### File Safety

- Never use destructive restore commands to undo unrelated work.
- Make targeted edits.
- Keep repository clean of temporary artifacts.

---

## Key References

| Document                           | Purpose                                     |
| ---------------------------------- | ------------------------------------------- |
| `.github/CONTRIBUTING.md`          | Contribution process and commit conventions |
| `.github/GIT_WORKFLOW.md`          | Branching strategy and push rules           |
| [GitHub Issues](../../issues)      | Bug reports and feature requests            |
| [Collab Roadmap](../../projects/2) | Kanban board (built-in Status + Priority)   |
| [Milestones](../../milestones)     | Version-targeted groupings                  |
| `MIGRATION_PLAN.md`                | Migration status and phases                 |

---

## Skills

Workflow procedures are in `.agents/skills/`:

| Skill                 | Trigger                                                           |
| --------------------- | ----------------------------------------------------------------- |
| `shell-compatibility` | Mandatory shell detection and shell-native terminal command usage |
| `file-locking`        | Mandatory lock verification before edits                          |
| `testing-workflow`    | Test planning, coverage, validation                               |
| `commit-workflow`     | Staging, reviewing, committing, pushing                           |
| `bug-tracking`        | Discovery, triage, fix, and closure via GitHub Issues             |
| `new-feature`         | Feature planning and implementation                               |

---

## Version

**Version:** 0.3.3
**Last Updated:** June 2, 2026
