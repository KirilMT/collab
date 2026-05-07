# VS Code Extension - Collaborative File Locks

## Automatic Setup (Recommended)

Run the development setup script. It auto-detects VS Code and installs extension dependencies:

```powershell
./scripts/setup-dev.ps1
```

Then install the extension in VS Code:

1. Press F1 and run Developer: Install Extension from Location...
2. Select vscode-extension/collab-locks/
3. Reload VS Code

## Lifecycle Behavior

- On VS Code startup or activation, the extension starts collab daemon watcher.
- On VS Code deactivate, close, or window reload, the extension requests daemon stop.
- Locks remain preserved by watcher graceful shutdown behavior.
- On next activation the daemon starts and watcher reconciliation resumes.

## Features

- Lock-on-open warning for files owned by another developer.
- Status bar lock indicator for the active file.
- Commands:
  - collabLocks.showAll
  - collabLocks.releaseAll
  - collabLocks.openDashboard

## Configuration

The extension reads workspace .env:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_anon_key
DEVELOPER_ID=your_name
```

DEVELOPER_ID is optional. If missing, git user.name is used.
