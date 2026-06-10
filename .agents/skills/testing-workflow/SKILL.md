---
name: testing-workflow
description: Use when writing tests, debugging coverage gaps, running validation, or investigating test failures.
---

# Testing Workflow

## Use this skill when

- Writing new tests for existing or new code
- Debugging why coverage is below threshold
- Running the full validation pipeline
- Investigating test failures after code changes
- Adding coverage for uncovered code paths
- Performing code quality audits

## Do not use this skill when

- Simply running `pytest` for a quick check (just run it)
- The question is about test configuration files (those are IMMUTABLE)

---

## 1. Testing Philosophy

### What Tests Verify

- Regression prevention
- Behavior documentation
- API and CLI contracts
- Safer refactoring

### What Tests Do Not Verify Alone

- Business correctness in all contexts
- Code quality or maintainability
- Security posture
- Performance characteristics

---

## 2. Pre-Flight Checks

Before writing any tests, find existing tests to avoid duplicates:

```bash
findstr /S "def test_" tests\\backend\\*.py
```

Check what's uncovered:

```bash
pytest --cov=collab --cov=scripts --cov-report=term-missing tests/backend
```

### Key Fixtures Available (tests/conftest.py)

| Fixture          | Purpose                                |
| ---------------- | -------------------------------------- |
| `mock_env`       | Mocks environment variables            |
| `temp_workspace` | Temporary directory for test isolation |

---

## 2. Writing Tests

1. Place new backend tests in `tests/backend/unit/`.
2. Place integration tests in `tests/backend/integration/`.
3. Place frontend JS unit tests in `tests/frontend/unit/` (see `jest.config.cjs` for `testMatch` and coverage paths).
4. Frontend testing stack (all required before merge):
   - **Jest unit:** `collab/dashboard/dashboard-format.js` + `tests/frontend/unit/` — run `npm test`.
   - **Playwright mock E2E/visual:** dense fixtures in `dashboard-seed-data.js`, `dashboard.spec.js`.
   - **Playwright live smoke:** `@live` tag, `chromium-live` project — same as CI.
   - **Schema/RLS contract:** `supabase-contract.spec.js` — PostgREST column checks.
   - **CI:** repository secrets `SUPABASE_URL`, `SUPABASE_ANON_KEY` (see CONTRIBUTING).
5. Follow the `test_<module>_<function>.py` naming convention.
6. Use Arrange-Act-Assert pattern.

---

## 3. Validation

### Full validation (pre-push, matches CI strictness)

```bash
python scripts/validate_code.py
```

| Step       | When it runs                   | What it proves                                                        |
| ---------- | ------------------------------ | --------------------------------------------------------------------- |
| ESLint     | Always (frontend block)        | Playwright helpers + specs                                            |
| Jest       | Always                         | `dashboard-format.js`                                                 |
| Playwright | `npm run test:frontend:e2e:ci` | chromium mock + contract + **@live** (requires `.env` Supabase creds) |

Firefox is **not** in CI or validate — optional: `npm run test:frontend:e2e:firefox`.

### Quick validation (local iteration)

```bash
python scripts/validate_code.py --quick
```

| Behavior                          | Detail                                            |
| --------------------------------- | ------------------------------------------------- |
| No frontend diff                  | Entire frontend block **skipped**                 |
| Dashboard/E2E diff only           | Playwright **fast** (mock + contract, no `@live`) |
| `dashboard-format.js` / unit diff | Jest runs                                         |
| JS in diff                        | Targeted ESLint                                   |
| `package.json` / global config    | Full suite for that category                      |

### Targeted

```bash
python scripts/validate_code.py --backend
python scripts/validate_code.py --frontend
```

### Playwright npm scripts

| Script                              | Use                                   |
| ----------------------------------- | ------------------------------------- |
| `npm run test:frontend:e2e:fast`    | Mock + contract (~12s, parallel)      |
| `npm run test:frontend:e2e:ci`      | Same as CI (chromium + chromium-live) |
| `npm run test:frontend:e2e:live`    | Live smoke only                       |
| `npm run test:frontend:e2e:firefox` | Optional firefox snapshots            |

Threshold policy:

- Backend coverage floor: 85 percent.
- Backend diff coverage floor: 95 percent for changed lines.
- Frontend checks hard-fail on ESLint, Jest, and Playwright (no soft-skip on failure).

---

## 4. Diff-Coverage Workflow (MANDATORY before commit)

### Rule: Never push hoping CI will catch coverage gaps

CI runs on Linux. You MUST simulate Linux locally and check diff-cover
against `origin/main` — the exact comparison CI uses.

### Procedure

```powershell
# 1. Fetch origin/main so diff-cover has the right baseline
git fetch origin main

# 2. Run full backend test suite with coverage in project root
$env:CI = "1"
$env:COVERAGE_FILE = "$PWD\.coverage"
python -m pytest tests/backend/ --cov=collab --cov=scripts --cov-report=xml -q

# 3. Check diff-cover against origin/main (NOT HEAD, NOT HEAD~1)
python -m diff_cover.diff_cover_tool coverage.xml `
    --compare-branch=origin/main --fail-under=95

# 4. If FAILS: read ALL missing lines, write comprehensive tests
#    covering every line in a single iteration. Never one-by-one.
```

### Common Mistakes

| Mistake                       | Why it's wrong                                  | Correct approach                                            |
| ----------------------------- | ----------------------------------------------- | ----------------------------------------------------------- |
| `--compare-branch=HEAD`       | Shows "no coverage information" — useless       | `--compare-branch=origin/main`                              |
| `--compare-branch=HEAD~1`     | Only checks last commit, not full branch diff   | `--compare-branch=origin/main`                              |
| Targeting one line at a time  | Wastes iterations                               | Read ALL missing lines, fix at once                         |
| `# pragma: no cover`          | Bypasses coverage — NEVER use                   | Write proper test or mock platform                          |
| Letting CI catch gaps         | Violates local-first principle                  | Simulate CI platform locally                                |
| Not mocking `platform.system` | Windows-only code uncovered on Linux CI         | Mock `platform.system` → `"Linux"` or `"Windows"` as needed |
| Coverage data in temp dir     | `_configure_coverage_data_file()` corrupts data | Set `$env:CI="1"` and `$env:COVERAGE_FILE` before pytest    |

### Simulating Linux CI on Windows

When code has platform-specific branches (`if platform.system() == "Windows":`),
you MUST cover both branches regardless of your local OS:

```python
import platform

# Simulate Linux
monkeypatch.setattr(platform, "system", lambda: "Linux")

# Simulate Windows with ctypes (works on Linux too via MagicMock)
monkeypatch.setattr(platform, "system", lambda: "Windows")
mock_windll = mock.MagicMock()
mock_windll.kernel32.OpenProcess.return_value = 0
monkeypatch.setattr(ctypes, "windll", mock_windll, raising=False)
```

### Coverage Tool Corruption Fix

The `_configure_coverage_data_file()` function in `scripts/validate_code.py`
runs at module-import time and routes `.coverage` to a temp directory. This
silently corrupts aggregated coverage data from the full test suite. The fix
is to set environment variables BEFORE running pytest:

```powershell
$env:CI = "1"                         # Skip temp routing
$env:COVERAGE_FILE = "$PWD\.coverage"  # Force project-root coverage
```
