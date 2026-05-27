# PyCharm — Collaborative Lock Watcher Setup

## Overview

The PyCharm watcher is a standalone Python script that monitors your local git
changes and automatically acquires/releases file locks via Supabase. It uses
`plyer` for cross-platform desktop notifications.

## Automatic Setup (Recommended)

Run the development setup script — it auto-detects PyCharm and installs the
Run Configuration for you:

```powershell
.\scripts\setup-dev.ps1
```

After setup, open **Run > Collab Lock Watcher** in PyCharm and click Run.
The watcher runs in the **Run** tool window (background tab) and will not
interfere with your coding workflow.

## Manual Start

```bash
python src/live_locks_watcher.py --interval 5 --timeout 480
```

## How Conflicts Work

When the watcher detects a conflict (file locked by another developer):

1. A **desktop notification** pops up with the file name and lock owner
2. The terminal shows a detailed warning with a dashboard link:
   ```
   [10:30] WARNING: ⚠ CONFLICT: src/lock_client.py is locked by @bob
                     — your changes may cause a merge conflict.
                     Run: collab dashboard
   ```
3. **Commits are blocked** — the `pre-commit` hook prevents committing
   files locked by another developer
4. When you revert the file or the conflict resolves, the watcher logs:
   ```
   [10:35] INFO: ✅ Conflict cleared: src/lock_client.py (file reverted or resolved)
   ```

## Stopping the Watcher

- **Manual**: Press the **Stop** button (⬛) in PyCharm's Run tool window, or `Ctrl+C`.
- **Automatic (IDE Close)**: The watcher is tied to the IDE window's terminal session.
  Closing PyCharm or the terminal tab will automatically terminate the background
  process within 5 seconds.
- **Automatic (Signal)**: When PyCharm sends SIGINT/SIGTERM on IDE close, the watcher
  performs a clean shutdown. **All active locks are strictly preserved** in Supabase
  so they are safe until your next session or till you push your code.
- **CLI**: `collab daemon-stop`

## Installing the Run Configuration Manually

1. Copy `editors/pycharm/Collab_Lock_Watcher.xml` to your project's
   `.idea/runConfigurations/` directory.
2. Restart PyCharm or use **Run > Edit Configurations** to reload.
3. The **Collab Lock Watcher** run configuration will appear in the Run menu.
