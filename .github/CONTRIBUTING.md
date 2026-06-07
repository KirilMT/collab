# Contributing to Collab

Thank you for contributing to Collab. This project is a quality-gated, release-automated runtime package, so contribution discipline matters as much as code correctness.

This document explains standards, safety requirements, validation flow, and release expectations for contributors.

## Scope and Authority

Use this file for contribution policy, coding standards, quality rules, and release requirements.

Use `.github/GIT_WORKFLOW.md` for branch strategy, push behavior, and detailed PR flow.

If there is any conflict between generic contributor habits and repository workflows, repository workflows win.

## Who Should Read This

Read this if you are:

- Opening your first PR in this repository.
- Modifying runtime code in `collab/`.
- Changing scripts in `scripts/`.
- Updating tests under `tests/`.
- Changing CI or release behavior.
- Running as an AI assistant operating autonomously.

## Core Principles

Every contribution must satisfy these principles:

- Reliability first. Never bypass quality checks to make progress look faster.
- Small, intentional changes. Reduce blast radius by keeping changes focused.
- Strong validation. Prove correctness with tests and static checks.
- Documentation parity. Behavior changes must update docs in the same PR.
- Reproducibility. Any reviewer should be able to run your checks locally.
- Respect for concurrent work. Follow file-locking protocol before editing.

## Code of Conduct

We are committed to respectful, inclusive collaboration.

Expected behavior:

- Be constructive and precise.
- Critique code and decisions, not people.
- Accept feedback and iterate quickly.
- Ask clarifying questions when requirements are ambiguous.

Unacceptable behavior:

- Harassment, discrimination, or personal attacks.
- Dismissive or hostile review language.
- Publishing private information without consent.
- Intentional policy bypass.

## Repository Overview

Collab provides collaborative file-locking runtime behavior with CLI and watcher flows.

Key areas:

- `collab/main.py`: CLI entry orchestration.
- `collab/lock_client.py`: lock operations, daemon lifecycle, status, release logic.
- `collab/live_locks_watcher.py`: watcher runtime behavior and conflict handling.
- `collab/logging_config.py`: logging setup and lifecycle handling.
- `scripts/`: format, validation, cleanup, utility workflows.
- `tests/`: backend and frontend test hierarchy.

## Development Environment

Use Python 3.12 where possible (project target is >=3.10).

Suggested setup:

1. Read `README.md`.
2. Install dependencies from `requirements.txt` and `requirements-dev.txt`.
3. Verify baseline health:

```bash
python scripts/format_code.py
python scripts/validate_code.py
```

4. Review policy docs:

- `.github/GIT_WORKFLOW.md`
- `AGENTS.md`
- `.github/copilot-instructions.md`

## Conflict Prevention (Mandatory — Read Before ANY Work)

No two developers or AI agents may work on the same task at the same time. Before touching code:

1. **Check the GitHub Issue** — is it already assigned? If yes, **STOP**.
2. **Check the [Collab Roadmap](https://github.com/users/KirilMT/projects/2)** — is the Status already `In Progress`? If yes, **STOP**.
3. **If no issue exists for this task, create one now** — every task must trace to a GitHub Issue (see `AGENTS.md` → "Issue-First Workflow").
4. **Claim the issue** — assign to yourself, set `status: in-progress` label, set board Status to `In Progress`.
5. **File locking** — run `collab active` before editing files.

See `AGENTS.md` → "Conflict Prevention Protocol" for full details.

## File Locking Protocol (Mandatory)

Before editing files:

1. List files you expect to modify.
2. Run `collab active`.
3. If a target file is locked by another developer, stop and report.
4. Never force-release another developer's lock.

### Troubleshooting

If you suspect a lock is stale or the watcher is unresponsive:

1. Run `collab daemon-status`.
2. If stopped, run `collab daemon-start`.
3. Check `collab active`.
4. If still blocked, check `collab status path/to/file.py` to see the exact owner.

## Branching and PR Flow

Branching and push policy is defined in `.github/GIT_WORKFLOW.md`.

Critical reminders:

- Never commit directly to `main`.
- New branch publication must follow the PR-first flow.
- Keep PRs scoped and reviewable.

## Conventional Commits (Mandatory)

All commits must follow Conventional Commits.

Supported types:

- `feat`
- `fix`
- `chore`
- `refactor`
- `perf`
- `remove`
- `revert`
- `docs`
- `test`
- `style`
- `build`
- `ci`

Use format:

```text
type(scope): short description
```

Example:

```text
fix(lock-client): preserve owned locks during watcher shutdown
```

Long commit body is encouraged for non-trivial changes:

- What changed.
- Why it changed.
- Any migration or compatibility notes.
- Validation evidence.

## Scopes Guidance

Use meaningful scopes. Examples:

- `lock-client`
- `watcher`
- `logging`
- `scripts`
- `tests`
- `docs`
- `ci`

Avoid generic scopes like `misc` unless truly unavoidable.

## Release Automation (Release Please)

This repository uses Release Please.

Flow summary:

1. You merge compliant commits into `main` via PR.
2. Release Please opens or updates a release PR.
3. Release PR updates version/changelog artifacts.
4. Merge release PR to publish.

Contributor responsibilities:

- Use accurate commit types and scopes.
- Keep changelog-worthy behavior in clear commits.
- Avoid squashing unrelated change categories into one commit.

## Versioning Policy

Semantic versioning is expected:

- Patch: bug fixes and behavior-preserving internal changes.
- Minor: backward-compatible feature additions.
- Major: breaking changes.

If your change is potentially breaking:

- Call it out in PR description.
- Add migration notes in docs.
- Coordinate before merge.

## Coding Standards

### General

- Follow PEP 8 and project formatter output.
- Keep functions focused and side effects explicit.
- Prefer explicit error handling over silent fallback.
- Avoid hidden global mutations unless intentional and documented.
- Keep public APIs stable unless a breaking change is approved.

### Architecture and Separation

- Keep Python logic in Python files.
- Keep HTML template structure in template files.
- Avoid embedding style/script logic in HTML where avoidable.
- Keep business logic out of test helpers.

### Comments and Docstrings

- Public functions and classes require docstrings.
- Comments should explain intent, constraints, or rationale.
- Avoid comments that only restate code.
- Remove obsolete comments during refactors.

### Error Handling

- No silent `except` blocks without logging/justification.
- Catch specific exceptions where possible.
- Preserve actionable context in raised errors.
- Ensure shutdown and cleanup paths are defensive.

### Logging

- Use structured, meaningful messages.
- Avoid leaking secrets or environment credentials.
- Keep logs useful for troubleshooting race conditions and daemon state.

## Frontend Testing (Required)

The dashboard has a full automated frontend stack. Run locally before PRs:

```bash
npm test                                    # Jest — dashboard-format.js
npm run test:frontend:e2e:fast              # mock + contract (~12s; validate --quick)
npm run test:frontend:e2e:ci                # CI + validate full (chromium + live)
npm run test:frontend:e2e:live              # live Supabase smoke only
npm run test:frontend:e2e:firefox           # optional firefox snapshots
```

| Layer           | Location                                              | Purpose                                                              |
| --------------- | ----------------------------------------------------- | -------------------------------------------------------------------- |
| Jest unit       | `tests/frontend/unit/`                                | Formatting/routing helpers in `collab/dashboard/dashboard-format.js` |
| Playwright mock | `tests/frontend/playwright/dashboard.spec.js`         | Dense fixtures (16 locks / 30 history), flows, visual baselines      |
| Playwright live | same + `playwright-live.html`                         | Real Supabase (same injection as `collab dashboard`)                 |
| Schema contract | `tests/frontend/playwright/supabase-contract.spec.js` | PostgREST columns + RLS read access                                  |

### CI repository secrets (Playwright live + contract)

Add these in GitHub **Settings → Secrets and variables → Actions** (same values as local `.env`):

- `SUPABASE_URL` (required)
- `SUPABASE_ANON_KEY` (required)
- `SUPABASE_SERVICE_ROLE_KEY` (optional; matches local dashboard when force-release is enabled)

The Playwright CI job writes `.env` from secrets and fails fast if required secrets are missing.

The **Supabase Keep-Alive** workflow (`.github/workflows/supabase-keepalive.yml`) uses the same secrets on a **Mon/Thu schedule** so Free-tier projects are not paused for inactivity (~7-day window). Enable it when you add secrets.

Requirements:

- Keep frontend validation hooks intact in `scripts/validate_code.py`.
- Do not remove frontend checks to make pipelines pass faster.

## Test Organization Requirements

Use this structure (updated for cleanliness—no empty placeholder directories):

- `tests/backend/unit/` (with module-grouped subdirectories for the large test surface)
- `tests/backend/functional/`
- `tests/backend/integration/`
- `tests/backend/security/`
- `tests/frontend/unit/` (Jest)
- `tests/frontend/playwright/` (E2E, visual, live smoke, Supabase contract)
- `tests/packaging/`

When adding tests:

- Place each test by intent, not by convenience.
- Avoid dumping unrelated tests into one file.
- Keep helper modules scoped and reusable.

## Coverage Rules (Immutable)

Quality gates rely on these thresholds:

- Backend total coverage >= 85 percent.
- Backend changed-lines coverage >= 95 percent.

Do not lower thresholds to pass CI.
Do not weaken assertions to inflate pass rate.

If coverage fails:

- Add missing tests.
- Refactor for testability if needed.
- Re-run full validation.

## Required Local Validation Before PR

Run these commands before opening or updating PR:

```bash
python scripts/format_code.py
python scripts/validate_code.py
```

Useful targeted options:

```bash
python scripts/validate_code.py --quick
python scripts/validate_code.py --backend
python scripts/validate_code.py --frontend
python scripts/format_code.py --check
```

If any command fails, fix issues before requesting review.

## Script Responsibilities

### `scripts/format_code.py`

Purpose:

- Enforce consistent formatting for backend, docs, templates, and yaml assets.

Behavior:

- Runs in deterministic order.
- Supports check-only and scoped modes.

### `scripts/validate_code.py`

Purpose:

- Simulate CI locally.

Behavior:

- Runs linting, type checks, tests, coverage checks, docs checks.
- Frontend validation runs ESLint, Jest, and Playwright (chromium); any failure fails the run. Jest/E2E are skipped only in `--quick` mode or when scripts/specs are absent.

### `scripts/generate_tests.py`

Purpose:

- Generate test scaffolding for Python modules.

Expectation:

- Generated tests are a starting point, not final quality evidence.

### `scripts/cleanup.py`

Purpose:

- Remove generated artifacts and stale local outputs.

Expectation:

- Use safely; do not remove source files.

## Documentation Requirements

If behavior changes, documentation must change in the same PR.

Update candidates include:

- `README.md`
- `docs/API.md`
- `docs/ARCHITECTURE.md`
- Update the corresponding [GitHub Issue](https://github.com/KirilMT/collab/issues) with progress or resolution
- Update the [Collab Roadmap](https://github.com/users/KirilMT/projects/2) project item board Status if applicable (the built-in workflow auto-updates on issue close)

For bug tracking:

- Confirm reproducibility.
- Keep issue statuses current with labels (`status: triage`, `status: in-progress`, `status: blocked`, `status: needs-review`).
- Search for duplicates before opening a new issue.
- Apply all four required label categories: `type:*`, `priority:*`, `scope:*`, and `status:*` lifecycle labels.

## GitHub Workflow for Contributors

### Issue-Driven Development

1. **Pick or create an issue** — All work must have a corresponding [GitHub Issue](https://github.com/KirilMT/collab/issues).
2. **Label it correctly** — Every issue needs `type:*`, `priority:*`, `scope:*`, and `status:*` lifecycle labels.
3. **Claim the issue** — Assign yourself, set `status: in-progress` label, AND update the board Status to `In Progress` on the [Collab Roadmap](https://github.com/users/KirilMT/projects/2).
4. **Branch naming** — Recommended: `feat/issue-<N>-desc` for features, `fix/issue-<N>-desc` for bugs, where `<N>` is the GitHub issue number. The key rule: the issue number must appear in the branch name. Other common patterns (e.g. `feat/<N>-desc`, `feature/<N>-description`, `bugfix/<N>-description`) are also acceptable.
5. **Commit with references** — Include `Fixes #<N>` or `Closes #<N>` in the commit body.
6. **Open a PR** — Set issue label to `status: needs-review`.
7. **After merge** — The issue auto-closes AND the board Status auto-updates to `Done` (via GitHub's built-in project workflow). No manual board update needed.

## Security and Secrets Policy

Never commit:

- API keys.
- tokens.
- credentials.
- private URLs containing secrets.

Use `.env` and `.env.example` conventions.

When touching auth/network logic:

- Validate failure paths.
- Ensure logs do not expose sensitive data.
- Add security-focused tests where applicable.

## CI and Config Safety Rules

Do not modify quality gate files to bypass failures.

Protected by policy:

- `pyproject.toml` (do not relax linters/coverage to force pass)
- `pytest.ini`
- `.flake8`
- `.github/workflows/*`

When CI fails, fix code/tests, not guardrails.

## Pull Request Expectations

Each PR should include:

- Clear summary of changes.
- Motivation and problem statement.
- Validation commands run and outcomes.
- Risk assessment (breaking, migration, performance).
- Screenshots or logs when relevant.

Keep PRs reviewable:

- Avoid mixing refactor + behavior change + docs overhaul without reason.
- Split large unrelated work into separate PRs.

## Reviewer Checklist

Reviewers should verify:

- Correctness and behavior under edge cases.
- Tests cover changed logic and regressions.
- Docs match implementation.
- No policy bypass introduced.
- Commit messages and PR title follow Conventional Commits.
- File locking protocol appears respected for contentious files.

## Author Checklist Before Requesting Review

- [ ] File locks were checked before edits.
- [ ] Changes are scoped and intentional.
- [ ] `python scripts/format_code.py` passed.
- [ ] `python scripts/validate_code.py` passed.
- [ ] Tests were added or updated for changed behavior.
- [ ] Docs were updated for behavior or interface changes.
- [ ] Commit messages follow Conventional Commits.
- [ ] PR title follows Conventional Commits.
- [ ] No secrets added.

## Troubleshooting Common Contribution Failures

### Validation fails after test path changes

Actions:

- Verify imports use `tests.backend...` hierarchy.
- Recheck helper root-resolution logic.
- Run quick validation, then full validation.

### Coverage threshold fails

Actions:

- Add tests for changed branches and exception paths.
- Avoid broad monkeypatching that hides behavior.
- Confirm diff-cover include set is correct.

### Release Please did not classify expected change

Actions:

- Verify commit type/scope syntax.
- Ensure commit message is not malformed by squash edits.
- Use explicit, semantic commit subjects.

### Lock conflicts during active development

Actions:

- Run `collab active`.
- Coordinate with lock owner.
- Do not force release another developer's lock.

### Windows daemon/process edge cases

Actions:

- Validate parent PID and namespace behavior in tests.
- Check logging and shutdown marker cleanup behavior.
- Re-run backend tests before merge.

## AI Assistant Requirements

For AI-assisted contributions:

- Follow the same validation standards as human contributors.
- Do not claim checks passed without running them.
- Do not revert unrelated user changes.
- Preserve repository conventions and test structure.
- Respect lock protocol and stop on lock conflicts.

## What Not To Do

Do not:

- Push direct, unreviewed commits to `main`.
- Disable checks to force green CI.
- Rewrite unrelated files for style-only changes in feature PRs.
- Introduce broad refactors without explicit requirement.
- Force release locks owned by others.
- Leave PRs without validation evidence.

## FAQ

### Why is `GIT_WORKFLOW.md` separate from this file?

To keep responsibilities clear:

- `CONTRIBUTING.md` explains standards and quality policy.
- `GIT_WORKFLOW.md` explains operational git/branch/PR mechanics.

Both are important. Neither replaces the other.

### Should I duplicate all git steps in this file?

No. Keep detailed branch mechanics in `GIT_WORKFLOW.md`. This file references it and captures contribution standards.

### Can I use `--quick` validation only?

Use quick mode while iterating, but run full validation before requesting review.

### Can I skip frontend checks because there is little frontend code?

No. Frontend readiness checks must remain intact.

## Final Notes

High quality in this repository depends on consistent workflow, strict validation, and complete documentation.

If you are unsure whether a change should be split, documented, or tested differently, open a draft PR early and ask for direction.

Thank you for helping keep Collab reliable and maintainable.
