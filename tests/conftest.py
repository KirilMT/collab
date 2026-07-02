import atexit
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
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

os.environ["COLLAB_STATE_DIR"] = _session_temp_dir
os.environ["COLLAB_PID_FILE"] = os.path.join(_session_temp_dir, "daemon.pid")
os.environ["COLLAB_TEST_MODE"] = "1"

# Neutralize ambient agent-mode markers so the suite is deterministic regardless of
# the host environment (e.g. running inside Cursor/Claude Code/Copilot, which export
# runtime markers). Tests that exercise agent identity opt in explicitly via the
# ``agent_id=`` constructor argument or by setting ``COLLAB_AGENT_ID`` with monkeypatch.
for _agent_env in (
    "COLLAB_AGENT_ID",
    "COLLAB_AGENT_MODE",
    "CURSOR_TRACE_ID",
    "CURSOR_SESSION_ID",
    "CURSOR_AGENT",
    "COMPOSER_SESSION_ID",
    "CLAUDE_CODE",
    "CLAUDE_CODE_SESSION",
    "GITHUB_COPILOT_AGENT_ID",
):
    os.environ.pop(_agent_env, None)

# We forcibly mock these for ALL tests to prevent accidental production leakage.
# Individual tests can still use monkeypatch if they need specific dummy values.
os.environ["SUPABASE_URL"] = "http://localhost:54321"
os.environ["SUPABASE_ANON_KEY"] = "test-anon-key-session"


def _cleanup_session_temp():
    try:
        shutil.rmtree(_session_temp_dir)
    except Exception:
        pass


def _is_test_namespace_path(text: str) -> bool:
    """Return True when *text* references an isolated test state namespace."""
    lowered = (text or "").lower().replace('"', "")
    return (
        "pytest-of-" in lowered
        or "collab_test_" in lowered
        or "collab_pytest_" in lowered
        or "\\pytest\\" in lowered
        or "/pytest/" in lowered
    )


def _is_test_watcher_cmdline(cmdline: str) -> bool:
    """Return True for watcher/daemon processes started by test runs only (#183).

    Matches lock_client watch, live_locks_watcher, ``python -m collab watch``, and
    collab console-script wrappers **only** when the cmdline also references a test
    isolation namespace (temp dirs / pytest paths). Production daemons that use
    ``.collab/.daemon.pid`` are never targeted.
    """
    text = (cmdline or "").lower().replace('"', "")
    if not text.strip():
        return False
    if not _is_test_namespace_path(text):
        return False

    # Classic daemon form: lock_client.py watch --daemon --pid-file <test ns>
    if "lock_client" in text and "watch" in text:
        return True
    # Module entry: python -m collab watch / python -m collab.live_locks_watcher
    if " -m collab" in text or " -m collab." in text:
        if "watch" in text or "live_locks_watcher" in text:
            return True
    # Direct live_locks_watcher module / script
    if "live_locks_watcher" in text:
        return True
    # Heartbeat keeper spawned under the test state dir (namespace already gated).
    if "daemon_heartbeat" in text:
        return True
    if "heartbeat" in text and ("keeper" in text or "collab" in text):
        return True
    # Console script wrappers launched with a test pid/state path
    if ("collab.exe" in text or "collab-watcher" in text or "\\collab " in text) and (
        "watch" in text or "--pid-file" in text or "daemon" in text
    ):
        return True
    return False


def _iter_pythonish_processes_psutil() -> "list[tuple[int, str]] | None":
    """Fast in-process scan via psutil (a declared runtime dependency).

    Returns ``(pid, cmdline)`` pairs, or None if psutil is unavailable so the caller can
    fall back to the subprocess-based enumeration. This avoids spawning a PowerShell/ps
    process after every test (~0.7s each on Windows), which would add many minutes of
    overhead across the full suite (#183).
    """
    try:
        import psutil
    except Exception:
        return None
    rows: list[tuple[int, str]] = []
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = proc.info
                pid = int(info.get("pid") or 0)
                if pid <= 0:
                    continue
                cmd = info.get("cmdline") or []
                cmdline = " ".join(cmd) if cmd else (info.get("name") or "")
                rows.append((pid, cmdline))
            except Exception:
                continue
    except Exception:
        return None
    return rows


def _iter_pythonish_processes() -> list[tuple[int, str]]:
    """Return ``(pid, cmdline)`` pairs for python/collab-like processes."""
    fast = _iter_pythonish_processes_psutil()
    if fast is not None:
        return fast
    rows: list[tuple[int, str]] = []
    try:
        if os.name == "nt":
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "Get-CimInstance Win32_Process | "
                        "Where-Object { "
                        "$_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe' "
                        "-or $_.Name -eq 'collab.exe' "
                        "-or $_.Name -eq 'collab-watcher.exe' "
                        "} | "
                        "Select-Object ProcessId,CommandLine | "
                        "ConvertTo-Json -Compress"
                    ),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            raw = (proc.stdout or "").strip()
            if not raw:
                return rows
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                parsed = [parsed]
            for row in parsed or []:
                pid = int(row.get("ProcessId", 0) or 0)
                cmdline = row.get("CommandLine") or ""
                if pid > 0:
                    rows.append((pid, cmdline))
        else:
            proc = subprocess.run(
                ["ps", "-eo", "pid,args"],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            for line in (proc.stdout or "").splitlines():
                m = re.match(r"^\s*(\d+)\s+(.*)$", line)
                if not m:
                    continue
                rows.append((int(m.group(1)), m.group(2)))
    except Exception:
        return rows
    return rows


def _force_kill_pid(pid: int) -> None:
    """Best-effort terminate *pid* (and tree on Windows)."""
    if pid <= 0 or pid == os.getpid():
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=15,
            )
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            time.sleep(0.05)
            # SIGKILL is POSIX-only; getattr keeps this cross-platform and avoids a
            # static "module has no attribute SIGKILL" error on win32 (this branch
            # never runs on Windows, where taskkill is used above).
            sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
            try:
                os.kill(pid, sigkill)
            except ProcessLookupError:
                pass
    except Exception:
        pass


def list_orphan_test_watcher_pids() -> list[tuple[int, str]]:
    """Return ``(pid, cmdline)`` for live test-scoped watcher/daemon processes."""
    found: list[tuple[int, str]] = []
    for pid, cmdline in _iter_pythonish_processes():
        if pid == os.getpid():
            continue
        if _is_test_watcher_cmdline(cmdline):
            found.append((pid, cmdline))
    return found


def _terminate_orphan_test_watchers() -> list[tuple[int, str]]:
    """Best-effort kill for orphaned test watcher daemons (#183).

    Keeps scope narrow to test-mode watcher cmdlines so production daemons are never
    affected. Returns the list of processes that were targeted.
    """
    targets = list_orphan_test_watcher_pids()
    for pid, _cmdline in targets:
        _force_kill_pid(pid)
    return targets


# Ensure test mode is explicitly kept, do not clear it, so late-firing
# atexit hooks attached to test processes still skip network calls.
atexit.register(_cleanup_session_temp)
atexit.register(_terminate_orphan_test_watchers)


def pytest_sessionfinish(session, exitstatus):
    """Cleanup any orphaned test watcher daemons at end of a pytest session (#183).

    After a force-kill pass, re-scan once. If any test-scoped daemons remain, mark the
    session failed so multi-daemon proliferation cannot land silently.
    """
    _terminate_orphan_test_watchers()
    # Brief settle so taskkill/SIGKILL can reap before the final scan.
    time.sleep(0.15)
    remaining = list_orphan_test_watcher_pids()
    if not remaining:
        return
    # One more kill attempt for stubborn Windows process trees.
    for pid, _cmd in remaining:
        _force_kill_pid(pid)
    time.sleep(0.2)
    still = list_orphan_test_watcher_pids()
    if not still:
        return
    detail = "; ".join(f"pid={pid} cmd={cmd[:120]}" for pid, cmd in still[:8])
    reporter = getattr(session.config, "pluginmanager", None)
    # Prefer a session-level failure without aborting mid-report hard.
    try:
        session.exitstatus = 1
    except Exception:
        pass
    # Surface a clear terminal message (and optional strict abort).
    sys.stderr.write(
        "\n[collab test guard] leftover test daemon/watcher process(es) after suite "
        f"({len(still)}): {detail}\n"
    )
    sys.stderr.flush()
    if os.getenv("COLLAB_STRICT_TEST_DAEMON", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        # Fail the session exit code; do not raise (can corrupt pytest reporting).
        if exitstatus == 0:
            try:
                session.exitstatus = pytest.ExitCode.TESTS_FAILED
            except Exception:
                session.exitstatus = 1
    _ = reporter  # reserved for future terminal-reporter integration


def _load_logging_config_module():
    collab_root = Path(__file__).resolve().parents[1]
    # Prefer package logging_config under collab/
    candidates = [
        collab_root / "collab" / "logging_config.py",
        collab_root / "logging_config.py",
    ]
    logging_config_path = next((p for p in candidates if p.is_file()), candidates[0])
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


@pytest.fixture(autouse=True)
def _reset_subprocess_bridge_override():
    """Ensure subprocess test doubles do not leak across tests."""
    from collab import subprocess_bridge

    subprocess_bridge.set_test_override(None)
    yield
    subprocess_bridge.set_test_override(None)


@pytest.fixture(autouse=True)
def _reap_test_daemons_after_each_test():
    """Per-test safety net so a single leak cannot accumulate across the suite."""
    yield
    _terminate_orphan_test_watchers()


# ============================================================================
# MOCKS & FIXTURES
# ============================================================================


class FakeNotification:
    def notify(self, **kwargs):
        pass


class FakePlyer:
    notification = FakeNotification()


sys.modules["plyer"] = FakePlyer()  # type: ignore[assignment]
