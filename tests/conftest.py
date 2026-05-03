import atexit
import importlib.util
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ============================================================================
# EARLY ISOLATION (MUST HAPPEN BEFORE TEST COLLECTION)
# ============================================================================
# Pytest imports test modules during the test collection phase, which happens
# *before* session-scoped fixtures execute. Since lock_client and watcher
# modules eagerly evaluate os.getenv("COLLAB_PID_FILE") at module load time,
# we MUST set test isolation variables at the top level of this file.

_session_temp_dir = tempfile.mkdtemp(prefix="collab_test_")

os.environ["COLLAB_PID_FILE"] = os.path.join(_session_temp_dir, "daemon.pid")
os.environ["COLLAB_TEST_MODE"] = "1"

# We intentionally mock these at the top level for tests to avoid hitting
# real endpoints if _get_create_client fallback triggers early.
# NOTE: Port must be ≤ 65535; using 5432 (PostgreSQL default) as placeholder.
os.environ.setdefault("SUPABASE_URL", "http://localhost:5432")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")


def _cleanup_session_temp():
    try:
        shutil.rmtree(_session_temp_dir)
    except Exception:
        pass


def _is_test_watcher_cmdline(cmdline: str) -> bool:
    """Return True for watcher processes started by test runs only."""
    text = (cmdline or "").lower().replace('"', "")
    if "lock_client.py watch" not in text:
        return False
    if "--daemon" not in text:
        return False
    if "--pid-file" not in text:
        return False
    # Strictly match temp test namespaces; never touch production .collab/.daemon.pid.
    return (
        "pytest-of-" in text
        or "collab_test_" in text
        or "mockcmms_pytest_collab_" in text
    )


def _terminate_orphan_test_watchers() -> None:
    """Best-effort kill for orphaned test watcher daemons.

    Keeps scope narrow to test-mode watcher cmdlines so production daemons are never
    affected.
    """
    try:
        if os.name == "nt":
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "Get-CimInstance Win32_Process | "
                        "Where-Object { $_.Name -eq 'pythonw.exe' "
                        "-or $_.Name -eq 'python.exe' } | "
                        "Select-Object ProcessId,CommandLine | "
                        "ConvertTo-Json -Compress"
                    ),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            raw = (proc.stdout or "").strip()
            if not raw:
                return
            import json

            rows = json.loads(raw)
            if isinstance(rows, dict):
                rows = [rows]
            for row in rows:
                pid = int(row.get("ProcessId", 0) or 0)
                cmdline = row.get("CommandLine") or ""
                if pid > 0 and _is_test_watcher_cmdline(cmdline):
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except Exception:
                        pass
        else:
            proc = subprocess.run(
                ["ps", "-eo", "pid,args"],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in (proc.stdout or "").splitlines():
                m = re.match(r"^\s*(\d+)\s+(.*)$", line)
                if not m:
                    continue
                pid = int(m.group(1))
                cmdline = m.group(2)
                if _is_test_watcher_cmdline(cmdline):
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except Exception:
                        pass
    except Exception:
        pass


# Ensure test mode is explicitly kept, do not clear it, so late-firing
# atexit hooks attached to test processes still skip network calls.
atexit.register(_cleanup_session_temp)
atexit.register(_terminate_orphan_test_watchers)


def pytest_sessionfinish(session, exitstatus):
    """Cleanup any orphaned test watcher daemons at end of a pytest session."""
    _terminate_orphan_test_watchers()


def _load_logging_config_module():
    collab_root = Path(__file__).resolve().parents[2]
    logging_config_path = collab_root / "logging_config.py"
    spec = importlib.util.spec_from_file_location(
        "collab.logging_config_test_cleanup", str(logging_config_path)
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[arg-type]
    return module


@pytest.fixture(autouse=True)
def _close_collab_logging_after_each_test():
    yield
    try:
        _load_logging_config_module().close_collab_logging()
    except Exception:
        pass


# ============================================================================
# MOCKS & FIXTURES
# ============================================================================


class FakeNotification:
    def notify(self, **kwargs):
        pass


class FakePlyer:
    notification = FakeNotification()


sys.modules["plyer"] = FakePlyer()  # type: ignore[assignment]
