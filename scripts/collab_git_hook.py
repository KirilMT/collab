"""Git hook helpers for collab lock lifecycle integration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")


def _hook_log(message: str) -> None:
    """Emit hook progress on stderr and flush for IDE git UIs (VS Code/Cursor)."""
    print(message, file=sys.stderr)
    sys.stderr.flush()


def _git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or "git failed"
        )
    return result.stdout.strip()


def _get_staged_files() -> list[str]:
    output = _git_output("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [line.strip() for line in output.splitlines() if line.strip()]


def _read_pid_file() -> Optional[int]:
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
    pid = _read_pid_file()
    if pid is None:
        return None
    return pid if _pid_is_running(pid) else None


def acquire_staged() -> int:
    from collab.lock_client import LockClient

    staged_files = _get_staged_files()
    if not staged_files:
        return 0

    watcher_pid = _watcher_pid()
    if watcher_pid is not None:
        _hook_log(
            "[collab] Watcher running "
            f"(PID: {watcher_pid}) — skipping pre-commit lock acquisition."
        )
        return 0

    count = len(staged_files)
    file_word = "file" if count == 1 else "files"
    _hook_log(f"[collab] Checking locks for {count} staged {file_word}...")

    try:
        client = LockClient()
        ok, failed, _message = client.acquire_multiple(
            staged_files, reason="pre-commit"
        )
    except Exception as exc:
        _hook_log(f"[collab] Warning: lock check failed: {exc}")
        return 1 if os.getenv("LOCK_STRICT", "0") == "1" else 0

    if ok:
        _hook_log(
            f"[collab] Locks acquired for {count} staged {file_word}.",
        )
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
    from collab.lock_client import LockClient

    try:
        client = LockClient()
        released = client.release_all()
    except Exception as exc:
        print(f"[collab] Warning: lock cleanup failed: {exc}", file=sys.stderr)
        return 0

    print(f"[collab] Released {released} lock(s).", file=sys.stderr)
    return 0


def validate_and_release() -> int:
    """Run quick validation, then release locks only on success."""
    validate_script = PROJECT_ROOT / "scripts" / "validate_code.py"
    result = subprocess.run(
        [sys.executable, str(validate_script), "--quick"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        print(
            "[collab] Pre-push validation failed — keeping locks active.",
            file=sys.stderr,
        )
        return result.returncode
    return release_all()


def main() -> int:
    if len(sys.argv) != 2:
        print(
            "Usage: python scripts/collab_git_hook.py "
            "<acquire-staged|release-all|validate-and-release>"
        )
        return 2

    command = sys.argv[1]
    if command == "acquire-staged":
        return acquire_staged()
    if command == "release-all":
        return release_all()
    if command == "validate-and-release":
        return validate_and_release()

    print(f"Unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
