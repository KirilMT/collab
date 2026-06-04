# Professional Git Workflow Guide

This document outlines the standard process for contributing to this project.
Following these steps ensures the `main` branch remains stable and all changes
are properly managed.

---

> **⚠️ CRITICAL RULE: NEW BRANCHES REQUIRE A PULL REQUEST**
>
> When you create a **new local branch** that does not exist on GitHub yet:
>
> - **NEVER** use `git push -u origin <branch>` to push it directly
> - **ALWAYS** use `gh pr create` to push AND create a PR in one step
>
> When you are on a branch that **already tracks a remote branch** (you see `[origin/...]` in `git branch -vv`):
>
> - You can use `git push` normally to push additional commits
>
> **Why?** Pushing a new branch without a PR leaves orphan branches on GitHub with no review process.

## Supported Commit Types

The following commit types are supported for release automation and changelog generation:

- feat, fix, chore, refactor, perf, remove, revert, docs, test, style, build, ci

Use only these types in your commit messages for releases.

### Linking Commits to Issues

Always reference the relevant GitHub Issue in your commit body:

- `Fixes #<N>` — Auto-closes the issue when merged (use for bug fixes)
- `Closes #<N>` — Auto-closes the issue when merged (use for feature completion)
- `Refs #<N>` — References without closing (use for related work)

Example:

```
fix(lock-client): preserve owned locks during watcher shutdown

Previously, the watcher would release locks owned by the same developer
when it encountered a stale PID file. Now it skips owned locks.

Fixes #42
```

### 1. Initial Setup (First Time on a New Machine)

If you are starting on a new computer or don't have the project locally, you
need to clone it from GitHub.

```sh
# Clone the repository from GitHub to your local machine
git clone https://github.com/KirilMT/collab collab

# Navigate into the newly created project directory
cd collab
```

---

### 2. Starting New Work (Creating a Feature Branch)

Before starting any new feature, bugfix, or improvement, always create a new
branch from an up-to-date `main`.

**Step 2.1: Sync Your Local `main` Branch**

```sh
# Switch to the main branch
git checkout main

# Pull the latest changes from the remote `main`
git pull origin main
```

**Step 2.2: Create Your New Branch**

Create a new branch with a descriptive name (e.g., `fix-lock-timeout`, `add-force-release`).

```sh
# Replace `new-feature-name` with your actual branch name
git checkout -b new-feature-name
```

---

### 3. During Development (Committing and Pushing)

Now you are on your new branch and can work safely without affecting `main`.

**Step 3.1: The 5-Step Quality Loop (Iterative Process)**
Before you commit, apply this loop to every file you touch:

1.  **Check:** Run linters `ruff check collab/`.
2.  **Format:** Run formatters `black collab/`.
3.  **Test:** Run `pytest` to ensure no regressions.
4.  **Audit:** Self-review logic and complexity.
5.  **Commit:** Only when 1-4 pass.

_Tip: `python scripts/validate_code.py --quick` does 1-3 for you!_

**Step 3.2: Verify Tracking Status Before Pushing**

It is **critical** to check whether your branch is already linked to a remote branch.

1.  **Check tracking status:**

    ```sh
    git branch -vv
    ```

    | Branch Status                     | Action Required                            |
    | --------------------------------- | ------------------------------------------ |
    | **Untracked** (no `[origin/...]`) | Use `gh pr create` - creates branch AND PR |
    | **Tracked** (has `[origin/...]`)  | Use `git push` - pushes to existing remote |

**Step 3.3: Push to GitHub**

**Option A: New Branch (Untracked) → Create PR**

⚠️ **NEVER use `git push -u origin <branch>` for new branches!**

```sh
# This pushes the branch AND creates the PR in one step
# ⚠️ Ensure the PR Title strictly follows Conventional Commits! Do NOT use --fill.
gh pr create --base main --head <your-branch-name> --title "feat(scope): your descriptive title" --body "Your detailed PR body"
```

**Option B: Existing Branch (Tracked) → Push Updates**

If your branch is already tracked (you see `[origin/...]`):

```sh
# Simply push your new commits
git push
```

---

### 4. Versioning and Documentation

This project follows Semantic Versioning (SemVer).

#### Automated Releases (Google Release Please)

Releases and changelog generation are fully automated by **Google Release Please**:

1. Merge your changes (with Conventional Commits) to `main` via a pull request.
2. The Release Please action runs automatically and opens a **Release PR**.
3. Review the Release PR. When you are ready to cut the actual release, simply **merge the Release PR**.
4. Upon merging, the action automatically creates the git tag and the GitHub Release on the repository.

---

### 5. Finishing Your Work (Pull Request)

If you haven't created a Pull Request yet (e.g., you were working on an existing branch), create one now via CLI (`gh pr create`) or GitHub Web UI.

---

### 6. Code Review & Merging

**Step 6.1: Automated Checks (CI/CD)**
All GitHub Actions (tests, linting) **must pass** before merging.

**Step 6.2: Merge**
Once approved and checks pass, click **"Merge pull request"**.
