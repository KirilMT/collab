# Collab Runtime Project Roadmap

_Updated May 31, 2026_

---

> [!TIP]
> **Document relationship:** This roadmap tracks **current and future product work**. Related docs:
>
> - Bugs: [`bug_tracking.md`](./bug_tracking.md)
> - Design: [`ARCHITECTURE.md`](./ARCHITECTURE.md)
> - CLI: [`API.md`](./API.md), [`CLI_REFERENCE.md`](./CLI_REFERENCE.md)

---

## ⚠️ Instructions for AI assistants

**When working on this project:**

1. Update **Active work** when starting or finishing a sprint.
2. Move finished items to **Recently shipped** (keep a short summary, do not delete history immediately).
3. Track new ideas under **Backlog** or **Product ideas**.
4. Update the **Updated** date at the top whenever you change this file.

### Quick update template

```markdown
## 🔥 Active work

**Current focus:** [Feature or theme]
**Status:** [In progress | Blocked | Complete]
**Started:** [Date]
**Target:** [Date or milestone]
```

---

## Living document guidelines

1. **Mark completed items** — Change `[ ]` to `[x]` and summarize under **Recently shipped**.
2. **Add new work** — Use Goal → scope → priority in the backlog sections.
3. **Keep active work honest** — One primary focus at a time; use `None` when between sprints.
4. **Preserve history** — Keep shipped capabilities listed; do not silently remove them.

---

## 🔥 Active work

**Current focus:** None.

**Next:** Pick from the [Backlog](#backlog) or [Product ideas](#product-ideas-unscheduled).

---

## Capabilities (shipped)

The runtime is feature-complete and published. Current capabilities:

| Area             | Summary                                                                  |
| ---------------- | ------------------------------------------------------------------------ |
| Runtime package  | `collab-runtime` on PyPI; `collab` and `collab-watcher` console scripts  |
| Locking          | Atomic acquire/release, batch operations, status, history, reconcile     |
| Daemon & watcher | Background watcher with lifecycle management and health checks           |
| Editor support   | VS Code / Cursor extension and PyCharm watcher integration               |
| Reliability      | Centralized safe subprocess layer, platform process probes, typed errors |
| Security         | Bandit-clean runtime, subprocess invariants enforced in CI               |
| Setup            | One-command dev/prod setup with idempotent, non-interactive modes        |
| Documentation    | Architecture, API, CLI reference, security, performance, troubleshooting |

---

## Backlog

### Documentation

- [x] `docs/API.md`
- [x] `docs/ARCHITECTURE.md`
- [x] `docs/SECURITY.md`
- [x] `docs/PERFORMANCE.md`
- [x] `docs/TROUBLESHOOTING.md`
- [x] `docs/CLI_REFERENCE.md`

### Shared-tools extraction (optional — deferred)

- [ ] Extract `scripts/cleanup.py` logic into `shared_collab_tools/cleanup.py`
- [ ] Extract `scripts/format_code.py` logic into `shared_collab_tools/formatters.py`
- [ ] Extract `scripts/validate_code.py` logic into `shared_collab_tools/validators.py`
- [ ] Extract `scripts/generate_tests.py` logic into `shared_collab_tools/generators.py`
- [ ] Publish shared package to internal registry or PyPI
- [ ] Adopt the shared package in `collab` and consumer applications

---

## Product ideas (unscheduled)

- Extension/runtime compatibility matrix published per release
- Richer dashboard metrics and filtering UX
- Additional IDE hosts beyond VS Code and PyCharm

---

## Maintenance expectations

- **Versioning:** Conventional Commits; semver for `collab-runtime` releases
- **Quality gates:** `scripts/validate_code.py` before merge; hooks on commit/push
- **Security:** Bandit `-ll` on `collab/` and `scripts/`; subprocess invariants in CI
- **Consumer apps:** Pin `collab-runtime` in setup scripts

---

## Notes for contributors

- CLI behavior changes require updates to `docs/API.md`, `docs/CLI_REFERENCE.md`, and integration tests
- New public Python modules belong under `collab/` and must be wired into `validate_code.py` targets
- Do not lower coverage thresholds in `pyproject.toml` / `pytest.ini`
- CLI commands should remain backward compatible unless a major version bump is intentional
