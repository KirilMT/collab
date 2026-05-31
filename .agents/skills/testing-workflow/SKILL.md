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
- Backend diff coverage floor: 92 percent for changed lines.
- Frontend checks hard-fail on ESLint, Jest, and Playwright (no soft-skip on failure).
