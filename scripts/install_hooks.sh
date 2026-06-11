#!/bin/sh
# Install collab hook entrypoints from scripts/git-hooks/ into .git/hooks.

set -e

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
SOURCE_DIR="$PROJECT_ROOT/scripts/git-hooks"
TARGET_DIR="$PROJECT_ROOT/.git/hooks"

if [ ! -d "$SOURCE_DIR" ]; then
    echo "[collab] Hook source directory not found: $SOURCE_DIR" >&2
    exit 1
fi

mkdir -p "$TARGET_DIR"
for hook in pre-commit post-commit pre-push commit-msg post-merge post-checkout; do
    if [ ! -f "$SOURCE_DIR/$hook" ]; then
        echo "[collab] Missing hook template: $SOURCE_DIR/$hook" >&2
        exit 1
    fi
    cp "$SOURCE_DIR/$hook" "$TARGET_DIR/$hook"
    chmod +x "$TARGET_DIR/$hook" 2>/dev/null || true
done

echo "[collab] Installed git hooks from scripts/git-hooks/ (pre-commit, post-commit, pre-push, commit-msg, post-merge, post-checkout)"
