---
name: new-feature
description: Use when scaffolding a new feature or implementing a significant new capability.
---

# Feature Development Workflow

## Use this skill when

- Adding a new feature in `src/`
- Implementing a significant capability across multiple files
- Executing a roadmap item from `docs/collab_roadmap.md`

## Do not use this skill when

- Fixing a bug (use `bug-tracking`)
- Making small isolated tweaks in one file

---

## Step 1: Plan Before Coding

1. Analyze and restate the requirement.
2. Outline the implementation plan with exact files.
3. Check AGENTS.md boundaries before changes.
4. Create a feature branch: `git checkout -b feat/feature-name`.

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

| Component                  | Location                            |
| -------------------------- | ----------------------------------- |
| CLI and lock orchestration | `src/lock_client.py`, `src/main.py` |
| Watcher process            | `src/live_locks_watcher.py`         |
| Logging setup              | `src/logging_config.py`             |
| Dashboard template         | `src/dashboard/index.html`          |
| Validation and tooling     | `scripts/`                          |

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

1. Update `docs/collab_roadmap.md` when milestone scope changes.
2. Add or update public API docstrings.
3. Update README or docs if user-facing behavior changes.

---

## Step 6: Commit

Follow `commit-workflow`.

## Safety

- New code must include tests.
- Do not lower thresholds to pass CI.
- Do not hardcode secrets or environment-sensitive values.
