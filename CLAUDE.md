<!-- See AGENTS.md for all shared context (stack, conventions, testing, boundaries, agent behavior). -->
<!-- This file contains ONLY Claude-specific additions. -->

# Claude Code — Tool-Specific Instructions

> **Primary reference:** [AGENTS.md](./AGENTS.md) — read it first.

---

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
