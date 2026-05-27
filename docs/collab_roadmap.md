# Collab Runtime Project Roadmap

_Updated May 13, 2026_ (Phase 5 — Hardening kickoff)

---

> [!TIP]
> **Document Relationship:** This roadmap tracks new features and strategic improvements. For bugs in existing functionality, see `docs/bug_tracking.md`.

---

## ⚠️ INSTRUCTIONS FOR AI ASSISTANTS

**When working on this project:**

1. **Update "ACTIVE WORK" section** when sprint phases change or complete
2. **Update status** as work progresses (e.g., "Phase 0.5" → "Phase 1" → "Completed")
3. **Move completed sprints** to "Recently Completed" section (don't delete immediately)
4. **Add new active work** when starting new sprints/features
5. **Update "Last Updated" date** at the top when making changes

### Quick Update Template

```markdown
## 🔥 ACTIVE WORK

**Current Phase:** [Phase Name]
**Status:** [Phase Name] - [Brief status]
**Started:** [Date]
**Target Completion:** [Target Date]
```

---

## LIVING DOCUMENT GUIDELINES

This roadmap is a living document that evolves with the project.

### Maintenance Rules

1. **Mark Completed Items** — When complete, change `[ ]` to `[x]` and move to "Recently Completed"
2. **Add New Work** — New features follow existing structure with Goal → Features → Priority
3. **Update Progress** — Keep "ACTIVE WORK" current; add status updates to in-progress items
4. **Preserve History** — Do not delete completed items; move to "Recently Completed"

---

## 🔥 ACTIVE WORK

**Current Phase:** Phase 5 — Hardening
**Status:** 🔄 Commencing Workstream A (Central Subprocess Wrapper)
**Started:** May 13, 2026
**Target Completion:** May 20, 2026

> [!NOTE]
> All migration phases (1-4.7) are successfully completed. The repository is now a standalone package with auto-provisioning extension. Phase 5 focuses on security, reliability, and error taxonomy.

---

## 📋 PLANNED PHASES

### Phase 0 — Alignment and Freeze

- [ ] Define behavioral contract for lock acquisition/release/daemon lifecycle
- [ ] Record baseline metrics (CLI parity, daemon behavior, extension workflows)
- [ ] Freeze collab feature work during extraction

### Phase 1 — Extract Package Without Behavior Changes

- [ ] Ensure CLI command parity with current implementation
- [ ] Verify all tests pass against new package layout
- [x] Test module entrypoint (`python -m collab` / `collab --help`)
- [ ] Build and test wheel distribution

### Phase 2 — Update Extension to Call Installed Package

- [ ] Replace extension spawn targets to use `collab` CLI
- [ ] Add runtime detection and health checks
- [ ] Define extension-to-runtime compatibility policy
- [ ] Test lock-on-open, status bar, release commands in IDE

### Phase 3 — Update Setup Scripts to Provision Package

- [ ] Update setup scripts to install `collab` from artifact index
- [ ] Add idempotent behavior for reruns
- [ ] Add non-interactive mode for automation
- [ ] Test end-to-end setup in clean environment

### Phase 4 — Remove In-Repo `.collab` and Decouple Validation

- [ ] Remove legacy in-repo `.collab` directory from consumer application repositories
- [ ] Update consumer application validation scripts to exclude collab internals
- [ ] Benchmark app validation time improvements
- [ ] Migrate CI/CD to separate collab pipeline

### Phase 5 — Hardening and Security

- [ ] Build central subprocess wrapper utility
- [ ] Replace runtime asserts with explicit guards
- [ ] Define error taxonomy for lifecycle paths
- [ ] Add security regression tests
- [ ] Module-by-module Bandit debt burndown

---

## 📊 MILESTONES

| Milestone                                               | Target Date  | Status |
| ------------------------------------------------------- | ------------ | ------ |
| Phase 0.5: Infrastructure complete                      | May 5, 2026  | ✅     |
| Phase 0: Behavioral contract approved                   | May 5, 2026  | ✅     |
| Phase 1: Package CLI parity verified                    | May 7, 2026  | ✅     |
| Phase 2: Extension calls installed package              | May 7, 2026  | ✅     |
| Phase 3: Setup scripts working                          | May 7, 2026  | ✅     |
| Phase 4: Consumer apps decoupled from in-repo `.collab` | May 8, 2026  | ✅     |
| Phase 4.5: Package published to TestPyPI                | May 12, 2026 | ✅     |
| Phase 4.6: Legacy entrypoint removed                    | May 12, 2026 | ✅     |
| Phase 4.7: Extension distribution                       | May 12, 2026 | ✅     |
| Phase 5: Hardening complete                             | TBD          | 🔄     |
| First stable release (v1.0.0)                           | TBD          | 🔄     |

---

## 🎯 KEY UNIMPLEMENTED FEATURES

1. **Error Taxonomy** — Structured failure classification for daemon lifecycle
2. **Subprocess Wrapper** — Central utility for safe process spawning
3. **Security Regression Tests** — Automated validation of spawn invariants
4. **Extension Refactor** — Thin client calling installed package

---

## 🧩 OPTIONAL ENHANCEMENTS BACKLOG

> [!NOTE]
> These are explicitly optional and non-blocking for current parity closure.

### Documentation Pages

- [x] Expand `docs/API.md` with detailed endpoint/function documentation
- [x] Expand `docs/ARCHITECTURE.md` to ~100-150 lines and include diagrams
- [ ] Add `docs/SECURITY.md` with security best practices and testing guidelines
- [ ] Add `docs/PERFORMANCE.md` with performance tuning and testing guidelines
- [ ] Add `docs/TROUBLESHOOTING.md` with common issues and resolutions
- [ ] Add `docs/CLI_REFERENCE.md` with complete CLI usage documentation

### Shared-Tools Extraction (Optional)

- [ ] Extract `scripts/cleanup.py` logic into `shared_collab_tools/cleanup.py`
- [ ] Extract `scripts/format_code.py` logic into `shared_collab_tools/formatters.py`
- [ ] Extract `scripts/validate_code.py` logic into `shared_collab_tools/validators.py`
- [ ] Extract `scripts/generate_tests.py` logic into `shared_collab_tools/generators.py`
- [ ] Publish shared package to internal registry or PyPI
- [ ] Update `collab` and consumer applications to consume the shared package

---

## 📌 NOTES

- All CLI commands must remain backward compatible
- Daemon lifecycle must not change between phases
- Tests must pass at each phase boundary
- Documentation must stay synchronized with implementation
