---
name: commit-workflow
description: Use when staging, reviewing, and committing changes. Covers the full git commit and push workflow.
---

# Commit Workflow

## Use this skill when

- You have completed a task and are ready to commit
- You need to stage, review, and commit changes
- You need to push a branch (new or existing)

## Do not use this skill when

- You're still writing code or running tests
- You need general git knowledge (see `.github/GIT_WORKFLOW.md`)

---

## Step 1: Pre-Commit Formatting & Validation

**MANDATORY** — never commit without formatting and passing validation.

**Format first, validate second:**

```bash
python scripts/format_code.py      # Format ALL code (must be the LAST edit before commit)
python scripts/validate_code.py    # Full repo scan (lint + test + coverage)
```

> ⚠️ **CRITICAL RULE:** `format_code.py` must be the **very last thing** you run before `git add` and `git commit`. If you edit ANY file after formatting, you MUST re-run `format_code.py` before committing.

What the pre-commit hook does automatically:

- Runs `validate_code.py --quick` on only your staged files.
- Ensures your specific changes pass lint and tests even if unrelated files are noisy.

## Step 2: Review All Changed Files

```bash
git status
git diff --stat
```

For each modified file, review actual changes:

```bash
git diff path/to/file.ext
```

Identify whether changes are related to the current task or unrelated.

## Step 3: Stage Relevant Files

```bash
git add path/to/file1.ext path/to/file2.ext
```

Rules:

- DO NOT stage unrelated changes.
- Use `git add -p` for partial staging when needed.
- DO NOT stage debug code or temporary changes.

## Step 4: Capture Staged Diff (MANDATORY for AI)

```bash
git diff --cached > temp_diff_output.txt
```

Read `temp_diff_output.txt` to understand ALL staged changes. Use this to write an accurate commit message.

## Step 5: Verify Staged Changes

```bash
git diff --cached --stat
git diff --cached path/to/file.ext
```

Confirm that only intended changes are staged.

## Step 6: Write Commit Message

Check recent commits for style:

```bash
git log -n 5 --oneline
```

Format: Conventional Commits

```
type(scope): short description (50-72 chars)

Detailed explanation of what changed and why.

Files changed:
- path/to/file1.ext (description of change)
- path/to/file2.ext (description of change)

Testing:
- How the changes were verified
```

Supported types: `feat`, `fix`, `chore`, `refactor`, `perf`, `remove`, `revert`, `docs`, `test`, `style`, `build`, `ci`

### Linking to GitHub Issues

Include issue references in the commit body to auto-close on merge:

- `Fixes #<N>` — for bug fixes
- `Closes #<N>` — for feature completion
- `Refs #<N>` — for related but non-closing changes

```bash
git commit -m "type(scope): description

Body text here...

Fixes #42"
```

**NOTE:** `git commit` requires user approval.

## Step 7: Commit

The `commit-msg` hook enforces Conventional Commits format.

## Step 8: Clean Up

```bash
del temp_diff_output.txt
```

## Step 9: Push

Check tracking status:

```bash
git branch -vv
```

| Branch Status                     | Action           | Command                                                                                |
| --------------------------------- | ---------------- | -------------------------------------------------------------------------------------- |
| **Untracked** (no `[origin/...]`) | Create PR & Push | `gh pr create --title "type(scope): message" --body "..." --base main --head <branch>` |
| **Tracked** (has `[origin/...]`)  | Push Updates     | `git push`                                                                             |

Do not use `git push -u origin <branch>` for new branches.

## Pre-Commit Checklist

- [ ] All code changes complete
- [ ] `format_code.py` passed and was run last
- [ ] `validate_code.py` passed
- [ ] Related files staged
- [ ] Unrelated changes not staged
- [ ] Commit message follows `type(scope): description`
- [ ] Documentation updated if applicable

## Safety

- NEVER use `git checkout` or `git restore` to fix files.
- Only `git commit` and `git push` require user approval.
