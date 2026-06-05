---
name: bug-tracking
description: Use when discovering, reporting, or fixing bugs. Covers the full bug lifecycle from discovery to user-confirmed resolution.
---

# Bug Tracking Workflow

## Use this skill when

- You discover a potential bug during browsing or testing
- You are fixing a reported bug
- You need to update bug tracking documentation

## Do not use this skill when

- Writing new features (see Skill: `new-feature`)
- Working on test failures that are not bugs (see Skill: `testing-workflow`)

---

## Bug Discovery Protocol

When you observe unexpected behavior:

1. DO NOT open a GitHub Issue immediately.
2. ASK the user first and describe:
   - What action you were performing
   - What you expected to happen
   - What actually happened
   - Evidence if available
3. WAIT for user confirmation before opening an issue.
4. Apply labels (`type: bug`, `priority:*`, `scope:*`) with user input.

---

## Issue Severity Categorization

When reporting issues, categorize using GitHub labels:

| Label                | Criteria                                                      | Action                 |
| -------------------- | ------------------------------------------------------------- | ---------------------- |
| `priority: critical` | Security vulnerabilities, data loss, app crashes, type errors | Fix immediately        |
| `priority: high`     | Broken workflows, coverage under 70% for critical paths       | Fix this sprint        |
| `priority: medium`   | Moderate impact, missing docstrings, non-critical regressions | Fix when touching file |
| `priority: low`      | Minor improvements, cosmetic concerns, technical debt         | Backlog                |

Always pair `type: bug` with a `priority:*` and `scope:*` label.

---

## Bug Fix Workflow

### Pre-Flight: Conflict Prevention (MANDATORY)

Before touching any code, verify you are the ONLY person working on this issue:

1. **Check the GitHub Issue** — is it already assigned to someone else? If yes, **STOP** and report.
2. **Check the [Collab Roadmap](https://github.com/users/KirilMT/projects/2)** — is the board Status already `In Progress`? If yes, **STOP** and verify with the assignee.
3. **Claim the issue** — assign it to yourself, set `status: in-progress` label, AND set the board Status to `In Progress`.
4. If using `collab` file-locking, also run `collab active`.

### Fix Steps

1. Verify the bug exists and reproduce it.
2. **Claim the issue** (see Pre-Flight above).
3. Create a branch: `git checkout -b fix/issue-<N>-description`.
4. Apply the fix with tests.
5. Run `python scripts/format_code.py` then `python scripts/validate_code.py`.
6. Commit with `Fixes #<N>` in the commit message body to auto-close on merge.
7. Open a PR; set issue label `status: needs-review`.
8. **After merge**: The issue auto-closes AND the board Status auto-updates to `Done` (via GitHub's built-in project workflow). No manual board update needed.

---

## Lifecycle Label Transitions

| From                   | To                     | Trigger                                    |
| ---------------------- | ---------------------- | ------------------------------------------ |
| `status: triage`       | `status: in-progress`  | You start working on the bug               |
| `status: in-progress`  | `status: needs-review` | PR opened, automated verification passed   |
| `status: needs-review` | (issue closed)         | PR merged with `Fixes #<N>`, auto-closed   |
| `status: in-progress`  | `status: blocked`      | Waiting on dependency or external decision |
| `status: blocked`      | `status: in-progress`  | Blocker resolved                           |

### Board Status Transitions (Auto-Managed)

The [Collab Roadmap](https://github.com/users/KirilMT/projects/2) uses GitHub's **built-in Status field** with auto-workflows:

| Board Status  | When                                          | Trigger                        |
| ------------- | --------------------------------------------- | ------------------------------ |
| `Todo`        | Issue added to project                        | Automatic (built-in workflow)  |
| `In Progress` | You start work (manual update via GitHub API) | Manual — AI agent or developer |
| `Done`        | Issue closed via `Fixes #<N>` in merged PR    | Automatic (built-in workflow)  |

> **⚠️ CRITICAL:** AI agents MUST manually set the board Status to `In Progress` when starting work. The `Done` transition is fully automatic. Never move a card to `Done` manually — let the built-in workflow handle it.

NEVER close an issue without explicit user confirmation that the fix works.

---

## Documentation Rules

### File Locations

| Scope               | Location                                                                         |
| ------------------- | -------------------------------------------------------------------------------- |
| Collab runtime bugs | [GitHub Issues](https://github.com/KirilMT/collab/issues) with `type: bug` label |

### Update Rules

- Search [existing issues](https://github.com/KirilMT/collab/issues) before opening a new one.
- NEVER create duplicate issues.
- NEVER add bugs without user confirmation.
- Apply all four label categories (`type:`, `priority:`, `scope:`, and `status:*` lifecycle) to each new bug issue.
- If the issue belongs on the roadmap, add it to the [Collab Roadmap](https://github.com/users/KirilMT/projects/2) project.

---

## Resolving Bugs

When you fix a tracked bug, close the GitHub Issue with a comment referencing the fixing commit. Use `Fixes #<issue>` in the commit message to auto-close.

---

## Safety

- Always reproduce before fixing.
- Do not modify test configurations to hide bugs.
