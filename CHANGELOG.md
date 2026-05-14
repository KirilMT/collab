<!-- markdownlint-disable MD024 -->

<!-- prettier-ignore -->
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.7](https://github.com/KirilMT/collab/compare/v0.2.6...v0.2.7) (2026-05-14)


### Bug Fixes

* **ci:** resolve GitHub Actions failures ([#39](https://github.com/KirilMT/collab/issues/39)) ([cc29cca](https://github.com/KirilMT/collab/commit/cc29cca23d0fa2b5f30e60f5120305d7a8e7054d))

## [0.2.6](https://github.com/KirilMT/collab/compare/v0.2.5...v0.2.6) (2026-05-14)

### Bug Fixes

- **release:** json parsing error ([#37](https://github.com/KirilMT/collab/issues/37)) ([2a2de27](https://github.com/KirilMT/collab/commit/2a2de277d6adb2b515c6582c0a8da2dda2a21e96))

## [0.2.5](https://github.com/KirilMT/collab/compare/v0.2.4...v0.2.5) (2026-05-14)

### Bug Fixes

- **ci:** improve workflow robustness and add repository_dispatch trigger ([#30](https://github.com/KirilMT/collab/issues/30)) ([9a5b47b](https://github.com/KirilMT/collab/commit/9a5b47b44c216d2386068345324b59392bf04d4b))

## [0.2.4](https://github.com/KirilMT/collab/compare/v0.2.3...v0.2.4) (2026-05-13)

### Bug Fixes

- **ci:** remove skip condition blocking release-please tag creation on merge ([#26](https://github.com/KirilMT/collab/issues/26)) ([3e11b0a](https://github.com/KirilMT/collab/commit/3e11b0abc5263cfd6af940ac965c33530319c68f))

## [0.2.3](https://github.com/KirilMT/collab/compare/v0.2.2...v0.2.3) (2026-05-13)

### Bug Fixes

- **ci:** create GitHub Release before uploading artifacts; fix GPG signing ([#20](https://github.com/KirilMT/collab/issues/20)) ([659e64a](https://github.com/KirilMT/collab/commit/659e64afb78ff7e2bd6653a1cae7718559588c91))

## [0.2.2](https://github.com/KirilMT/collab/compare/v0.2.1...v0.2.2) (2026-05-08)

### Bug Fixes

- **ci:** restore dependabot auto-format stability with Ruff E9 gate ([#17](https://github.com/KirilMT/collab/issues/17)) ([3cb43e0](https://github.com/KirilMT/collab/commit/3cb43e080049ef43a1fa7ece3a723df03e097425))

## [0.2.1](https://github.com/KirilMT/collab/compare/v0.2.0...v0.2.1) (2026-05-08)

### Bug Fixes

- **ci:** enforce LF line endings for all repository text files ([#8](https://github.com/KirilMT/collab/issues/8)) ([46200d4](https://github.com/KirilMT/collab/commit/46200d42762d33a42da5fc1249fd6a5cc304874d))

## [0.2.0](https://github.com/KirilMT/collab/compare/v0.1.0...v0.2.0) (2026-05-07)

### Features

- **hooks:** align collab hook lifecycle and CI parity with mockCMMS ([27c5ce3](https://github.com/KirilMT/collab/commit/27c5ce39d1f6849fadbe5458d3437dc65d8c3f68))
- **infra:** Phase 0.5 infrastructure scaffolding and frontend parity ([fca3d51](https://github.com/KirilMT/collab/commit/fca3d51f8f99dc5bcc26cee0162d691c4e43ddfd))
- **phase1:** complete migration Phase 1 and add shell-compatibility skill ([27ee505](https://github.com/KirilMT/collab/commit/27ee50569ee69b83b96d77d636d4dbb79cf14cd1))
- **phase2:** update extension to call installed collab package ([ea7dbe6](https://github.com/KirilMT/collab/commit/ea7dbe671773503121834bf62dfe3ad2cbfaf929))
- **phase3:** update setup scripts to provision collab package and extension ([97f07e9](https://github.com/KirilMT/collab/commit/97f07e96919dc12ef0f75385a6f35c10a508b6a6))
- **vscode-extension:** add collaborative locks VS Code extension with watcher lifecycle management ([4de01d1](https://github.com/KirilMT/collab/commit/4de01d1437814d28b56af4c5fcae81867ea26c9e))

### Bug Fixes

- **build:** remove redundant License classifier conflicting with PEP 639 ([974bbc0](https://github.com/KirilMT/collab/commit/974bbc07ec2e106405ac75a6769066a0b0180ac8))
- **ci:** harden workflow triggers and manual dispatch support ([44b0886](https://github.com/KirilMT/collab/commit/44b0886ff1f6c67a473f1b80cc3d141a421eb1c5))
- **ci:** preserve coverage artifacts for report step ([2b371b9](https://github.com/KirilMT/collab/commit/2b371b9c3058c1f02fd2098a6bd521cea324afd6))
- **hooks:** enable verbose output for collab lock hooks ([146c217](https://github.com/KirilMT/collab/commit/146c217169800f34fbf75320e20b42da5fabc043))
- **hooks:** show only collab messages in hooks ([90c0da4](https://github.com/KirilMT/collab/commit/90c0da4a4fe5054ab580772094447f8ddbace15f))
- **hooks:** stop forwarding git args to pre-push pre-commit run ([3fa8456](https://github.com/KirilMT/collab/commit/3fa84569512af10e7c76f3764f8f76fb01bac88e))
- **test:** close subprocess stdin in hook template tests to prevent CI hang ([e14356a](https://github.com/KirilMT/collab/commit/e14356ab1d97ffe8060a6e3733bc126ecc7a42b5))
- **test:** resolve infinite loop in hook template pre-push test ([1518010](https://github.com/KirilMT/collab/commit/15180100302e5a7367e56accde4b4dd6c1e34e70))
- **validation:** clarify skipped checks and harden CI/frontend detection ([2dee206](https://github.com/KirilMT/collab/commit/2dee206cec13fdc245ef26595529d4794bd14122))
- **validation:** make checks deterministic and tighten cleanup coverage ([dea2028](https://github.com/KirilMT/collab/commit/dea202810aaeb6a09dc06617f3acd5a3dafaf40a))

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
