<!-- markdownlint-disable MD024 -->

<!-- prettier-ignore -->
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] (2026-05-04)

### ✨ Features

- **infrastructure:** Phase 0.5 scaffolding — complete development environment with CI/CD,
  testing, linting, and AI governance infrastructure
- **validation:** Enhanced validate_code.py with 10 robust validation steps including
  docformatter, flake8, yamllint, and diff-cover
- **testing:** All 635 unit tests passing with ≥85% coverage, zero skipped tests
- **lock-client:** Atomic file locking with Supabase Realtime conflict detection
- **live-locks-watcher:** Real-time collaborative workflow synchronization
- **dashboard:** Interactive file lock status visualization

### 🔧 Infrastructure

- Full Python package structure with setuptools entry points
- Comprehensive tool configuration (pytest, ruff, black, mypy, coverage, bandit)
- Git workflows for CI/CD automation and release management
- Pre-commit hooks for code quality enforcement
- Development scripts: setup, format, validate, cleanup, test generation

### 📝 Documentation

- Complete repository structure matching industry patterns
- AI agent governance for collaborative development (AGENTS.md, CLAUDE.md)
- Skill-based workflow documentation for file-locking, testing, commits, bugs
- Architecture and API reference documentation

---

## [0.1.1] (2026-05-06)

### ✨ Features

- **frontend:** Add `eslint.config.js` with mockCMMS-aligned flat-config rules (recommended
  - no-unused-vars warn, no-console warn, no-undef error; targets
    `src/dashboard/**/*.js` and `tests/frontend/playwright/**/*.js`)
- **frontend:** Add `playwright.config.js` with full mockCMMS-style E2E test
  configuration — globalSetup/globalTeardown hooks, webServer auto-start,
  `src/dashboard` static serving, Chromium + Firefox projects, visual regression
  settings, .env feature-flag support
- **frontend:** Add four Playwright E2E helper modules under
  `tests/frontend/playwright/` (e2e-test-setup.js, e2e-test-teardown.js,
  pre-test-cleanup.js, test-utils.js) — full mockCMMS pattern port adapted for
  collab dashboard and Supabase environment

### 🔧 Infrastructure

- **npm:** Add `@eslint/js`, `eslint`, `globals`, `@playwright/test` to
  `package.json` devDependencies (versions aligned with mockCMMS)
- **npm:** Add `lint:frontend`, `lint:frontend:fix`, `test:frontend:e2e`,
  `test:frontend:e2e:chromium` scripts
- **ci:** Branch triggers updated from `master` to `main` across all GitHub
  workflow files (`ci.yml`, `release.yml`, `lock-service-smoke-test.yml`)
- **hooks:** Pre-push hook hardened — lock release now atomic with validation
  (`validate_and_release` in `scripts/collab_git_hook.py`)
- **validate:** Default diff-cover compare-branch updated to `origin/main`/`main`
  (removed `master` and `origin/master` candidates)
- **src:** Source tree reorganised from `src/collab/` flat layout to `src/`
  (renames tracked by git)
- **tests:** Test directories reorganised from `tests/unit/` to
  `tests/backend/unit/`; new `tests/frontend/` scaffold with Jest and Playwright
  subdirectories

### 🐛 Bug Fixes

- **migration:** Remove duplicate Phase 0.5 status line and fix broken repository
  tree markdown block in `MIGRATION_PLAN.md`
- **validate:** Fix ESLint fallback target from `src tests/frontend` (fails on
  directories with no `.js` files) to `tests/frontend/playwright`

### 📝 Documentation

- **license:** Add MIT `LICENSE` file (Copyright 2026 KirilMT)
- **readme:** Align README with current command surface, daemon lifecycle,
  VS Code notification channels, and `watch` command
- **gitignore:** Extend ignore rules to cover `.env.*` variants while preserving
  `.env.example`
- **roadmap:** Add Optional Enhancements Backlog section to
  `docs/collab_roadmap.md`
- **migration:** Update `MIGRATION_PLAN.md` Phase 0.5 status, fix tree block
  formatting, promote `eslint.config.js` and `playwright.config.js` from
  "could add" to completed, remove deferred lower-priority block

---

## [Unreleased]

### Planned for Future Releases

**Phase 1:** Extract into installable Python wheel with golden command suite

**Phase 2:** Extension integration with installed runtime package

**Phase 3:** Setup automation for integrated environment provisioning

**Phase 4:** Decouple application repos from collab source code

**Phase 5:** Security hardening and error taxonomy refinement
