<!-- See AGENTS.md for all shared context (stack, conventions, testing, boundaries, agent behavior). -->
<!-- This file contains ONLY GitHub Copilot-specific additions. -->

# GitHub Copilot — Tool-Specific Instructions

> **Primary reference:** [AGENTS.md](./AGENTS.md) — read it first.

---

## File Locking

Before editing any file:

1. List all files the task will touch
2. Run `collab active` (or `python -m src.main active` from the repo root) to check locks
3. If locked by another developer — **stop and report**. Do not edit.
4. If unlocked, proceed — lock acquisition/release is automatic

**ABSOLUTELY FORBIDDEN:** Never force-release another developer's lock.

---

## Tool Limitations

GitHub Copilot does **not** have access to:

- Browser automation tools
- Full terminal command execution in all IDEs

**Implications:**

- Provide clear manual testing instructions instead of automated browser verification
- Include step-by-step verification steps in PR descriptions

---

## Verification Workflow

Since Copilot cannot run browser tests automatically:

1. **Identify test plan** — check `docs/` for test procedures
2. **Provide instructions** — give clear steps to verify changes manually
3. **Create checklist** — if no test plan exists, add one to the PR description
4. **Wait for confirmation** — do not mark task complete until user confirms

---

## Task Completion

- Always run `python scripts/format_code.py` then `python scripts/validate_code.py` before finishing
- Follow conventional commits: `type(scope): description`
- See [.github/GIT_WORKFLOW.md](.github/GIT_WORKFLOW.md) for the full commit procedure
