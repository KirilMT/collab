"""Shared path-ignore rules for the collaborative lock watchers.

Decides which working-tree paths must never acquire a lock: VCS internals,
runtime artifacts, and user/project-configured ephemeral (scratch/temp) files.
Locking transient scratch files is the root cause of the misleading 1-2 second
lock durations reported in #170 (a file is created, locked, then deleted within
seconds), so excluding them at the source is the robust fix.

Configuration is additive and every source is optional:

* Built-in structural ignores: ``.git/``, ``instance/`` folders, and the collab
  marker files (``.startup_summary.json``, ``.shutdown_complete``).
* Built-in temp-suffix ignores: ``*.tmp``, ``*.temp``, ``*.bak``, ``*.backup``,
  ``*.orig``, ``*.swp``, ``*.swo``, ``*~``, ``*.isorted``. These are never source
  files, so locking them only produces short-lived noise.
* ``COLLAB_LOCK_IGNORE`` env var: extra glob patterns separated by the OS path
  separator, commas, or newlines.
* ``.collabignore`` at the project root: gitignore-style glob patterns, one per
  line (``#`` comments and blank lines ignored). Cached by mtime.

Patterns are matched (via :mod:`fnmatch`) against both the project-relative
POSIX path and the basename, so ``build/*.log`` and ``*.tmp`` both work. A
trailing ``/`` matches a directory prefix; a trailing ``/**`` matches anything
beneath a directory.

Fail-open: any configuration error yields "do not ignore" so a malformed file
never silently disables locking.
"""

from __future__ import annotations

import fnmatch
import os
import threading
from typing import Iterable, Optional

# Marker files the watcher itself writes into the tree; never lock them.
_STRUCTURAL_MARKERS = (".startup_summary.json", ".shutdown_complete")

# Suffixes that are, by construction, transient and never source code.
_DEFAULT_TEMP_GLOBS: tuple[str, ...] = (
    "*.tmp",
    "*.temp",
    "*.bak",
    "*.backup",
    "*.orig",
    "*.swp",
    "*.swo",
    "*~",
    "*.isorted",
)

_COLLABIGNORE_NAME = ".collabignore"

# project_root -> (mtime_or_None, patterns). ``None`` mtime means "no file".
_ignore_cache: dict[str, tuple[Optional[float], tuple[str, ...]]] = {}
_cache_lock = threading.Lock()


def _normalize(path: str) -> str:
    """Return *path* as a POSIX-style relative path without a leading ``./``."""
    norm = path.replace("\\", "/")
    if norm.startswith("./"):
        norm = norm[2:]
    return norm


def _is_structural_ignore(norm: str) -> bool:
    """Return True for VCS internals, instance folders, and collab markers."""
    if norm == ".git" or norm.startswith(".git/") or "/.git/" in norm:
        return True
    if (
        norm == "instance"
        or norm.startswith("instance/")
        or norm.endswith("/instance")
        or "/instance/" in norm
    ):
        return True
    for marker in _STRUCTURAL_MARKERS:
        if marker in norm:
            return True
    return False


def _split_patterns(raw: str) -> tuple[str, ...]:
    """Split a raw string on OS path separator, commas, and newlines."""
    if not raw:
        return ()
    normalized = raw.replace(os.pathsep, "\n").replace(",", "\n")
    return tuple(chunk.strip() for chunk in normalized.splitlines() if chunk.strip())


def _env_patterns() -> tuple[str, ...]:
    """Return the extra ignore globs from ``COLLAB_LOCK_IGNORE``."""
    return _split_patterns(os.getenv("COLLAB_LOCK_IGNORE", ""))


def _collabignore_patterns(project_root: str) -> tuple[str, ...]:
    """Return glob patterns from ``<project_root>/.collabignore`` (mtime-cached)."""
    if not project_root:
        return ()
    fpath = os.path.join(project_root, _COLLABIGNORE_NAME)
    try:
        mtime: Optional[float] = os.path.getmtime(fpath)
    except OSError:
        mtime = None

    with _cache_lock:
        cached = _ignore_cache.get(project_root)
        if cached is not None and cached[0] == mtime:
            return cached[1]

    patterns: tuple[str, ...] = ()
    if mtime is not None:
        try:
            with open(fpath, "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
            patterns = tuple(
                line.strip()
                for line in lines
                if line.strip() and not line.strip().startswith("#")
            )
        except OSError:
            patterns = ()

    with _cache_lock:
        _ignore_cache[project_root] = (mtime, patterns)
    return patterns


def _matches(norm: str, patterns: Iterable[str]) -> bool:
    """Return True when *norm* (or its basename) matches any glob in *patterns*."""
    base = norm.rsplit("/", 1)[-1]
    for pattern in patterns:
        if not pattern:
            continue
        pat = pattern.replace("\\", "/")
        if pat.endswith("/**"):
            prefix = pat[:-3].rstrip("/")
            if prefix and (norm == prefix or norm.startswith(prefix + "/")):
                return True
            continue
        if pat.endswith("/"):
            prefix = pat.rstrip("/")
            if prefix and (norm == prefix or norm.startswith(prefix + "/")):
                return True
            continue
        if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(base, pat):
            return True
    return False


def should_ignore_lock_path(path: str, project_root: str = "") -> bool:
    """Return True when *path* must never acquire a collaborative lock.

    Combines structural ignores, built-in temp-suffix ignores, the
    ``COLLAB_LOCK_IGNORE`` env var, and a project ``.collabignore`` file. Fails open
    (returns False) on any unexpected error so locking is never silently disabled.
    """
    if not path:
        return False
    try:
        norm = _normalize(path)
        if _is_structural_ignore(norm):
            return True
        if _matches(norm, _DEFAULT_TEMP_GLOBS):
            return True
        if _matches(norm, _env_patterns()):
            return True
        if _matches(norm, _collabignore_patterns(project_root)):
            return True
    except Exception:
        return False
    return False
