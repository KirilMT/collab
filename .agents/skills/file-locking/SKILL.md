---
name: file-locking
description: Use BEFORE any file modification. Check locks, edit safely with automatic lock handling, and never force-release others' locks.
---

# File Locking Workflow

## Use this skill when

- You are about to edit one or more files for any reason (bug, feature, refactor, docs)
- You need to check whether a file is currently locked by another developer
- You are about to run commands that modify files (for example `python scripts/format_code.py`, codemods, or bulk refactors)

## Do not use this skill when

- You are only reading files (status checks, searches, reviews)
- You are running tests or validation that do not modify files

---

## Why This Matters

This repository uses a **collaborative file locking system** (`.collab/`) to prevent merge conflicts when multiple developers or AI agents work simultaneously. Editing a file that another developer has locked will cause conflicts.

**Rule: Never edit a file without verifying it is either unlocked or already locked by the current developer (the dev using this AI agent).**

---

## Step 1: Identify All Files to Change

Before touching anything, enumerate the **complete list of files** the task requires. Do this upfront — discovering mid-task that a file is locked wastes effort.

---

## Step 2: Check Lock Status

Check all active locks at once:

```bash
python -m src.main active
```

Optional targeted check:

```bash
python -m src.main status path/to/file.py
```

Cross-reference the active lock list against your planned file list.

---

## Step 3: Decision Gate

For each file in your list:

| Lock state | Owner                 | Action                                                                  |
| ---------- | --------------------- | ----------------------------------------------------------------------- |
| Unlocked   | —                     | Proceed with edits (lock is acquired automatically when editing starts) |
| Locked     | **Current developer** | Proceed — already owned by the active dev                               |
| Locked     | **Another developer** | **STOP** — see below                                                    |

### If a file is locked by another developer

**Do not edit the file.** Instead:

1. Report to the user exactly which files are locked and by whom.
2. Ask the user whether to wait, contact the owner, or proceed with a reduced scope.
3. **ABSOLUTE AI RULE:** force-releasing another developer's lock is forbidden.
