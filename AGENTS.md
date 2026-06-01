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
| Backend tests        | Pytest (coverage >=85%)                              | -                    |
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

- Backend total coverage: >=85 percent
- Backend diff coverage on changed lines: >=92 percent
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
| `docs/bug_tracking.md`                    | Ask user before adding new bugs.                        |
| Coverage thresholds                       | Immutable. Add tests, do not lower thresholds.          |

---

## Documentation Structure

- Core docs: `docs/collab_roadmap.md`, `docs/bug_tracking.md`, `docs/API.md`, `docs/ARCHITECTURE.md`
- Docs must stay current and concise.
- Keep roadmap and bug-tracker statuses synchronized.

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

| Document                  | Purpose                                     |
| ------------------------- | ------------------------------------------- |
| `.github/CONTRIBUTING.md` | Contribution process and commit conventions |
| `.github/GIT_WORKFLOW.md` | Branching strategy and push rules           |
| `docs/collab_roadmap.md`  | Active roadmap                              |
| `docs/bug_tracking.md`    | Bug lifecycle                               |
| `MIGRATION_PLAN.md`       | Migration status and phases                 |

---

## Skills

Workflow procedures are in `.agents/skills/`:

| Skill                 | Trigger                                                           |
| --------------------- | ----------------------------------------------------------------- |
| `shell-compatibility` | Mandatory shell detection and shell-native terminal command usage |
| `file-locking`        | Mandatory lock verification before edits                          |
| `testing-workflow`    | Test planning, coverage, validation                               |
| `commit-workflow`     | Staging, reviewing, committing, pushing                           |
| `bug-tracking`        | Discovery, triage, fix, and closure                               |
| `new-feature`         | Feature planning and implementation                               |

---

## Version

**Version:** 0.3.2
**Last Updated:** May 31, 2026
