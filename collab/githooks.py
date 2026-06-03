"""Git hook runtime shipped with the ``collab`` package.

This module is the consumer-facing counterpart to the repo-internal
``scripts/collab_git_hook.py`` helper. It is importable from an installed wheel
so downstream projects get working hooks via ``pip install collab-runtime`` plus
``collab init-hooks`` — without copying any collab-repo files.

Responsibilities:

* ``acquire-staged`` / ``release-all`` — lock lifecycle orchestration invoked by
  the installed git hooks (``python -m collab.githooks <command>``).
* ``install_hooks`` — copy the bundled hook templates into a consumer repo's git
  hooks directory (honoring ``core.hooksPath`` and worktrees).

The project root is resolved from git at call time (git runs hooks from the
repository top level), so the same module works for any consumer repository.
"""

from __future__ import annotations

import json
import os
import sys
from importlib import resources
from pathlib import Path
from typing import Optional

from collab import safe_subprocess

HOOK_NAMES = ("pre-commit", "post-commit", "pre-push", "commit-msg")
_TEMPLATE_PACKAGE = "collab"
_TEMPLATE_DIR = "hook_templates"


def _hook_log(message: str) -> None:
    """Emit hook progress on stderr and flush for IDE git UIs (VS Code/Cursor)."""
    print(message, file=sys.stderr)
    sys.stderr.flush()


def _run_git(root: Optional[Path], *args: str) -> tuple[int, str]:
    """Run a git subcommand via the safe wrapper; return (returncode, stdout).

    All subprocess execution goes through :mod:`collab.safe_subprocess` so the package
    honors the project's subprocess security invariants (absolute exe resolution + argv
    allowlists). Returns a non-zero code on any failure.
    """
    cwd = str(root) if root is not None else None
    try:
        result = safe_subprocess.capture(["git", *args], policy="git", cwd=cwd)
    except Exception:
        return 1, ""
    return result.returncode, safe_subprocess.decode_output(result.stdout).strip()


def _git_toplevel(start: Optional[Path] = None) -> Path:
    """Return the git repository root, falling back to the working directory."""
    returncode, top = _run_git(start, "rev-parse", "--show-toplevel")
    if returncode == 0 and top:
        return Path(top)
    return start if start is not None else Path.cwd()


def _git_output(root: Path, *args: str) -> str:
    """Run a git command in ``root`` and return stripped stdout."""
    returncode, output = _run_git(root, *args)
    if returncode != 0:
        raise RuntimeError(output or "git failed")
    return output


def _get_staged_files(root: Path) -> list[str]:
    """Return staged files (added/copied/modified/renamed) for the commit."""
    output = _git_output(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [line.strip() for line in output.splitlines() if line.strip()]


def _read_pid_file() -> Optional[int]:
    """Read the watcher PID from the lock client's PID file, if present."""
    from collab.lock_client import PID_FILE

    pid_path = Path(PID_FILE)
    if not pid_path.exists():
        return None

    try:
        raw = pid_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not raw:
        return None

    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        pid = payload.get("pid")
        return pid if isinstance(pid, int) else None

    try:
        return int(raw)
    except ValueError:
        return None


def _pid_is_running(pid: int) -> bool:
    """Return True when a process with ``pid`` is currently alive."""
    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid))
    except Exception:
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True


def _watcher_pid() -> Optional[int]:
    """Return the live watcher PID, or None when no watcher is running."""
    pid = _read_pid_file()
    if pid is None:
        return None
    return pid if _pid_is_running(pid) else None


def _load_env(root: Path) -> None:
    """Load ``.env`` from the consumer repo root so lock checks have credentials."""
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def acquire_staged() -> int:
    """Acquire locks for staged files; used by the installed pre-commit hook."""
    root = _git_toplevel()
    _load_env(root)

    staged_files = _get_staged_files(root)
    if not staged_files:
        return 0

    watcher_pid = _watcher_pid()
    if watcher_pid is not None:
        _hook_log(
            "[collab] Watcher running "
            f"(PID: {watcher_pid}) - skipping pre-commit lock acquisition."
        )
        return 0

    count = len(staged_files)
    file_word = "file" if count == 1 else "files"
    _hook_log(f"[collab] Checking locks for {count} staged {file_word}...")

    from collab.lock_client import LockClient

    try:
        client = LockClient()
        ok, failed, _message = client.acquire_multiple(
            staged_files, reason="pre-commit"
        )
    except Exception as exc:
        _hook_log(f"[collab] Warning: lock check failed: {exc}")
        return 1 if os.getenv("LOCK_STRICT", "0") == "1" else 0

    if ok:
        _hook_log(f"[collab] Locks acquired for {count} staged {file_word}.")
        return 0

    _hook_log("[collab] Commit blocked due to lock conflicts:")
    for file_path in failed:
        try:
            status = client.get_lock_status(file_path)
        except Exception:
            status = {}
        owner = status.get("locked_by") or status.get("developer_id") or "unknown"
        _hook_log(f"  - {file_path} (locked by @{owner})")
    return 1


def release_all() -> int:
    """Release all locks held by this developer; used by the pre-push hook."""
    root = _git_toplevel()
    _load_env(root)

    from collab.lock_client import LockClient

    try:
        client = LockClient()
        released = client.release_all()
    except Exception as exc:
        _hook_log(f"[collab] Warning: lock cleanup failed: {exc}")
        return 0

    _hook_log(f"[collab] Released {released} lock(s).")
    return 0


def _read_template(name: str) -> str:
    """Return the bundled hook template text for ``name``."""
    resource = resources.files(_TEMPLATE_PACKAGE).joinpath(_TEMPLATE_DIR).joinpath(name)
    return resource.read_text(encoding="utf-8")


def _hooks_dir(root: Path) -> Path:
    """Resolve the git hooks directory, honoring core.hooksPath and worktrees."""
    returncode, value = _run_git(root, "config", "--get", "core.hooksPath")
    if returncode == 0 and value:
        hooks_path = Path(value)
        return hooks_path if hooks_path.is_absolute() else (root / hooks_path)

    returncode, value = _run_git(root, "rev-parse", "--git-path", "hooks")
    if returncode == 0 and value:
        hooks_path = Path(value)
        return hooks_path if hooks_path.is_absolute() else (root / hooks_path)

    return root / ".git" / "hooks"


def _is_collab_hook(text: str) -> bool:
    """Return True when existing hook content was produced by collab."""
    return "collab" in text.lower()


def install_hooks(
    project_root: Optional[Path] = None,
    force: bool = False,
) -> dict[str, list[str] | str]:
    """Install bundled collab git hooks into the target repository.

    Existing non-collab hooks are preserved unless ``force`` is set, so a developer's
    custom hooks are never silently overwritten.

    Returns a summary dict with ``installed``, ``skipped`` and ``hooks_dir``.
    """
    root = Path(project_root) if project_root is not None else _git_toplevel()
    hooks_dir = _hooks_dir(root)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    skipped: list[str] = []

    for name in HOOK_NAMES:
        target = hooks_dir / name
        if target.exists() and not force:
            try:
                existing = target.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                existing = ""
            if not _is_collab_hook(existing):
                skipped.append(name)
                continue

        # Normalize to LF so POSIX `sh` (incl. Git Bash on Windows) can parse
        # the shebang and body regardless of how the template was checked out.
        content = _read_template(name).replace("\r\n", "\n")
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        try:
            mode = target.stat().st_mode
            target.chmod(mode | 0o755)
        except OSError:
            pass
        installed.append(name)

    return {
        "installed": installed,
        "skipped": skipped,
        "hooks_dir": str(hooks_dir),
    }


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for ``python -m collab.githooks <command>``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            "Usage: python -m collab.githooks "
            "<acquire-staged|release-all|init> [--force]",
            file=sys.stderr,
        )
        return 2

    command = args[0]
    if command == "acquire-staged":
        return acquire_staged()
    if command == "release-all":
        return release_all()
    if command == "init":
        force = "--force" in args[1:]
        summary = install_hooks(force=force)
        installed = summary["installed"]
        skipped = summary["skipped"]
        print(f"[collab] Installed git hooks into {summary['hooks_dir']}")
        if installed:
            print(f"[collab] Installed: {', '.join(installed)}")
        if skipped:
            print(
                "[collab] Skipped (existing non-collab hooks, use --force): "
                f"{', '.join(skipped)}"
            )
        return 0

    print(f"[collab] Unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
