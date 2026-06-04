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

1. Verify the bug exists and reproduce it.
2. Assign the GitHub Issue to yourself and set label `status: in-progress`.
3. If the issue is on the [Collab Roadmap](https://github.com/users/KirilMT/projects/2), set its Stage to `🚧 In Progress`.
4. Create a branch: `git checkout -b fix/issue-<N>-description`.
5. Apply the fix with tests.
6. Run `python scripts/format_code.py` then `python scripts/validate_code.py`.
7. Commit with `Fixes #<N>` in the commit message body to auto-close on merge.
8. Open a PR; set issue label `status: needs-review`.
9. Once merged and user confirms, update the Project item Stage to `✅ Shipped`.

---

## Status Label Transitions

| From                   | To                     | Trigger                                    |
| ---------------------- | ---------------------- | ------------------------------------------ |
| `status: triage`       | `status: in-progress`  | You start working on the bug               |
| `status: in-progress`  | `status: needs-review` | PR opened, automated verification passed   |
| `status: needs-review` | (issue closed)         | PR merged with `Fixes #<N>`, auto-closed   |
| `status: in-progress`  | `status: blocked`      | Waiting on dependency or external decision |
| `status: blocked`      | `status: in-progress`  | Blocker resolved                           |

### Project Stage Transitions (parallel track)

> **Note:** The built-in GitHub Project **Status** field (Todo/In Progress/Done) is disabled on the Collab Roadmap project to avoid confusion. Use only the **Stage** field for Kanban positioning and the **`status:*` labels** for issue lifecycle tracking.

| Stage            | When                              |
| ---------------- | --------------------------------- |
| `📋 Backlog`     | Issue created, not yet scheduled  |
| `🔜 Next`        | Scheduled for current/next sprint |
| `🚧 In Progress` | Active development                |
| `✅ Shipped`     | Merged and deployed               |

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
- Apply all three label categories (`type:`, `priority:`, `scope:`) to each new bug issue.
- If the issue belongs on the roadmap, add it to the [Collab Roadmap](https://github.com/users/KirilMT/projects/2) project.

---

## Resolving Bugs

When you fix a tracked bug, close the GitHub Issue with a comment referencing the fixing commit. Use `Fixes #<issue>` in the commit message to auto-close.

---

## Safety

- Always reproduce before fixing.
- Do not modify test configurations to hide bugs.
