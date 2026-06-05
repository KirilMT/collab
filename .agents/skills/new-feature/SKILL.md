---
name: new-feature
description: Use when scaffolding a new feature or implementing a significant new capability.
---

# Feature Development Workflow

## Use this skill when

- Adding a new feature in `collab/`
- Implementing a significant capability across multiple files
- Executing a roadmap item from [GitHub Projects](https://github.com/KirilMT/collab/projects) or [Milestones](https://github.com/KirilMT/collab/milestones)

## Do not use this skill when

- Fixing a bug (use `bug-tracking`)
- Making small isolated tweaks in one file

---

## Step 1: Plan Before Coding

### Pre-Flight: Conflict Prevention (MANDATORY)

Before touching any code, verify you are the ONLY person working on this issue:

1. **Check the GitHub Issue** — is it already assigned to someone else? If yes, **STOP** and report.
2. **Check the [Collab Roadmap](https://github.com/users/KirilMT/projects/2)** — is the board Status already `In Progress`? If yes, **STOP** and verify with the assignee.
3. **Claim the issue** — assign it to yourself, set `status: in-progress` label, AND set the board Status to `In Progress`.
4. If using `collab` file-locking, also run `collab active`.

### Planning Steps

1. Analyze and restate the requirement.
2. Find or create the corresponding [GitHub Issue](https://github.com/KirilMT/collab/issues).
3. **Claim the issue** (see Pre-Flight above) — assign self, set `status: in-progress` label, update board Status to `In Progress`.
4. Outline the implementation plan with exact files.
5. Check AGENTS.md boundaries before changes.
6. Create a feature branch: `git checkout -b feat/issue-<N>-description`.

---

## Step 2: Write Tests First

Before implementing:

1. Search existing tests: `findstr /S "def test_" tests\\backend\\*.py`
2. Run baseline tests: `pytest tests/backend -q`
3. Add failing tests for the new behavior.
4. Follow red-green-refactor.

See `testing-workflow` for test placement and strategy.

---

## Step 3: Implement the Feature

### Core Runtime Locations

| Component                  | Location                                  |
| -------------------------- | ----------------------------------------- |
| CLI and lock orchestration | `collab/lock_client.py`, `collab/main.py` |
| Watcher process            | `collab/live_locks_watcher.py`            |
| Logging setup              | `collab/logging_config.py`                |
| Dashboard template         | `collab/dashboard/index.html`             |
| Validation and tooling     | `scripts/`                                |

### Separation of Concerns

- JavaScript in `.js` files only.
- CSS in `.css` files only.
- HTML in `.html` templates only.
- Avoid inline handlers when JavaScript assets are introduced.

---

## Step 4: Validate

```bash
python scripts/format_code.py
python scripts/validate_code.py
```

Ensure coverage thresholds remain satisfied.

### If You Add New Root-Level Python Files or Top-Level Packages

Every new Python file must be included in all validation tools. Update:

| Location                                              | What to update                 |
| ----------------------------------------------------- | ------------------------------ |
| `scripts/validate_code.py` -> `python_targets`        | Add file/dir                   |
| `scripts/validate_code.py` -> `_cov_sources`          | Add `--cov=<path>`             |
| `scripts/validate_code.py` -> `_FULL_TESTPATHS`       | Add test directory             |
| `scripts/validate_code.py` -> `_BACKEND_MAP`          | Add prefix to test dir mapping |
| `scripts/format_code.py` -> `format_python()` targets | Add file/dir                   |
| `.github/workflows/ci.yml`                            | Add lint/test/coverage target  |

Missing any of these can create false coverage failures.

---

## Step 5: Document

1. Update the corresponding [GitHub Issue](https://github.com/KirilMT/collab/issues) with progress notes.
2. If scope changes, update the issue description and re-evaluate the [Milestone](https://github.com/KirilMT/collab/milestones) target.
3. Add or update public API docstrings.
4. Update README or docs if user-facing behavior changes.

---

## Step 6: Commit

Follow `commit-workflow`. Include `Closes #<N>` or `Fixes #<N>` in the commit body to link the PR and auto-close the issue on merge.

After merge, the issue auto-closes AND the board Status auto-updates to `Done` via GitHub's built-in project workflow. No manual board update needed.

## Safety

- New code must include tests.
- Do not lower thresholds to pass CI.
- Do not hardcode secrets or environment-sensitive values.
