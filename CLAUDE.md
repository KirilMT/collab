<!-- See AGENTS.md for all shared context (stack, conventions, testing, boundaries, agent behavior). -->
<!-- This file contains ONLY Claude-specific additions. -->

# Claude Code — Tool-Specific Instructions

> **Primary reference:** [AGENTS.md](./AGENTS.md) — read it first.

---

## Task Tracking — Issue-First Workflow

Every task must trace to a GitHub Issue. When the user requests work that does
not yet have an issue:

1. Restate the task to confirm scope.
2. Search [existing issues](https://github.com/KirilMT/collab/issues) for duplicates.
3. If none found, **create the issue now** — before touching any code — with:
   - Title: `type: description` (e.g., `feat: add restart and --version CLI commands`)
   - Labels: `type:*`, `priority:*`, `scope:*`, `status: in-progress`
   - Assignee: yourself
   - Project: [Collab Roadmap](https://github.com/users/KirilMT/projects/2), Status → `In Progress`
4. Use `Closes #<N>` in the commit body so the issue auto-closes on merge.

See `AGENTS.md` → "Issue-First Workflow" for full details.

---

## Conflict Prevention (MANDATORY — before ANY work)

No two developers or AI agents may work on the same task at the same time. Before touching code:

1. Check the [GitHub Issue](https://github.com/KirilMT/collab/issues) — already assigned? **STOP**.
2. Check the [Collab Roadmap](https://github.com/users/KirilMT/projects/2) — Status already `In Progress`? **STOP**.
3. Claim: assign to self, set `status: in-progress` label, set board Status to `In Progress`.
4. Run `collab active` for file-level conflicts.

See `AGENTS.md` → "Conflict Prevention Protocol" for the full policy.

## File Locking

Before editing any file, check active locks:

```bash
collab active
```

If any target file is locked by another developer — **stop and report**. Do not edit.

---

## Autonomous Execution

- Auto-run all standard operations: python, pytest, ruff, black, isort, mypy
- Only `git commit` and `git push` require user approval
- Never pause for "Should I proceed?" during lint/format/test loops

---

## Browser Verification

When testing the dashboard or IDE extensions:

1. Check that `collab daemon-start` is running
2. Use browser tools to demonstrate features
3. Default login (if needed): use test credentials from `.env.example`
4. Describe only what's visible in screenshots

---

## Task Completion

- Operate autonomously until work is done — pause only at `git commit`
- Always run `python scripts/format_code.py` then `python scripts/validate_code.py` before finishing
- If validation fails, self-correct up to 3 attempts before reporting
