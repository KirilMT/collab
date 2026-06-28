<!-- BEGIN collab-agent-attribution (managed by `collab install-agent-hooks`) -->

## Collaborative lock attribution (AI agent)

This project uses the `collab` runtime for collaborative file locking. JetBrains IDEs do not expose a per-edit hook, so to keep the locks dashboard accurate (showing **AI Agent** vs **User**), after you create or modify any file(s) you MUST immediately claim them as an AI agent using the project virtual environment (`.venv`):

```
collab claim <path> [<path> ...] --label "<short task>" --reason "AI agent edit"
```

- Claim every file you edit. Do not rely on the background watcher — it attributes edits to the human developer by design.
- Claiming marks the lock `origin=agent` so the work is shown as AI-agent work, with your task as the label.

<!-- END collab-agent-attribution -->
