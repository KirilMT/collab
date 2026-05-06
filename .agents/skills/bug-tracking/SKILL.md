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

1. DO NOT add it to any bug tracking document immediately.
2. ASK the user first and describe:
   - What action you were performing
   - What you expected to happen
   - What actually happened
   - Evidence if available
3. WAIT for user confirmation before adding to bug tracking.
4. Assign priority with user input.

---

## Issue Severity Categorization

When reporting issues, categorize by severity:

| Severity | Criteria                                                                          | Action                 |
| -------- | --------------------------------------------------------------------------------- | ---------------------- |
| Critical | Security vulnerabilities, data loss, app crashes, type errors                     | Fix immediately        |
| High     | Duplicates over 10 lines, coverage under 70% for critical paths, broken workflows | Fix this sprint        |
| Medium   | Style violations, medium complexity, missing docstrings                           | Fix when touching file |
| Low      | Minor style issues, low complexity improvements, cosmetic concerns                | Technical debt backlog |

---

## Bug Fix Workflow

1. Verify the bug exists and reproduce it.
2. Apply the fix.
3. Verify the fix via tests and validation.
4. Update documentation and mark as Fixed.
5. Notify the user and wait for confirmation before Resolved.

---

## Status Transitions

| From        | To          | Trigger                                     |
| ----------- | ----------- | ------------------------------------------- |
| Open        | In Progress | You start working on the bug                |
| In Progress | Fixed       | Code applied, automated verification passed |
| Fixed       | Resolved    | User confirms fix works                     |

NEVER mark a bug as Resolved without explicit user confirmation.
"Fixed" means code is applied. "Resolved" means user verified.

---

## Documentation Rules

### File Locations

| Scope               | File                   |
| ------------------- | ---------------------- |
| Collab runtime bugs | `docs/bug_tracking.md` |

### Update Rules

- ALWAYS update summary counts when changing bug statuses.
- NEVER create duplicate bug IDs. Search first.
- NEVER add bugs without user confirmation.

---

## Resolving Bugs

When you fix a tracked bug, move it to the Resolved section of `docs/bug_tracking.md` and include a commit reference.

---

## Safety

- Always reproduce before fixing.
- Do not modify test configurations to hide bugs.
