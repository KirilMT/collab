"""Validated Windows/Unix process introspection (Phase 5.2).

Centralizes tasklist, wmic, powershell, and ps invocations with fixed argv shapes and
numeric PID guards. Git and taskkill remain in ``safe_subprocess``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from typing import Optional, Sequence

from . import safe_subprocess
from .subprocess_bridge import get_subprocess

logger = logging.getLogger("collab.platform_probe")

_WIN_CREATION_FLAGS = 0x08000000
_PYTHON_IMAGE_NAMES = frozenset({"python.exe", "pythonw.exe", "python3.exe"})
# pip-generated console-script wrappers for the collab runtime. On Windows these
# ``.exe`` launchers hold their own image file open for the life of the process,
# which can block deletion of the virtualenv when a watcher was started through
# the wrapper (older IDE extensions) and the wrapper is left orphaned.
_COLLAB_LAUNCHER_IMAGE_NAMES = frozenset({"collab.exe", "collab-watcher.exe"})


def _require_pid(pid: int) -> int:
    if not isinstance(pid, int) or pid <= 0:
        raise ValueError(f"invalid pid: {pid!r}")
    return pid


def _resolve(name: str) -> Optional[str]:
    try:
        path = shutil.which(name)
    except Exception:
        path = None
    if path:
        return os.path.abspath(path)
    if safe_subprocess.is_test_mode():
        return name
    return None


def _run_platform(
    argv: Sequence[str], *, timeout: float = 30.0, text: bool = True
) -> str:
    """Run a pre-validated platform argv and return stdout (empty on failure)."""
    sp = get_subprocess()
    kwargs: dict = {
        "stderr": sp.DEVNULL,
        "timeout": timeout,
        "text": text,
    }
    if os.name == "nt":
        kwargs["creationflags"] = _WIN_CREATION_FLAGS
    try:
        completed = sp.run(list(argv), capture_output=True, **kwargs)
        if completed.returncode != 0:
            return ""
        return (completed.stdout or "").strip()
    except Exception as exc:
        logger.debug("platform probe failed: %s (%s)", " ".join(argv), exc)
        return ""


def taskkill_force(pid: int, *, tree: bool = False) -> None:
    """Force-terminate a process via validated taskkill."""
    _require_pid(pid)
    argv = ["taskkill", "/F", "/PID", str(pid)]
    if tree:
        argv = ["taskkill", "/F", "/T", "/PID", str(pid)]
    try:
        safe_subprocess.run(argv, policy="taskkill", capture_output=True)
    except Exception as exc:
        logger.debug("taskkill_force failed for pid=%s: %s", pid, exc)


def is_pid_alive_tasklist(pid: int) -> bool:
    """Return True if tasklist reports the PID (Windows fallback)."""
    if sys.platform != "win32":
        return False
    _require_pid(pid)
    exe = _resolve("tasklist")
    if not exe:
        return False
    out = _run_platform([exe, "/FI", f"PID eq {pid}", "/NH"], timeout=10.0)
    return str(pid) in out


def tasklist_csv_for_image(image_name: str) -> str:
    """Return tasklist CSV output for a fixed python image name."""
    if sys.platform != "win32":
        return ""
    if image_name.lower() not in _PYTHON_IMAGE_NAMES:
        raise ValueError(f"tasklist image not allowed: {image_name!r}")
    exe = _resolve("tasklist")
    if not exe:
        return ""
    return _run_platform(
        [exe, "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
        timeout=60.0,
    )


def tasklist_csv_for_pid(pid: int) -> str:
    """Return tasklist CSV row output for a single PID."""
    if sys.platform != "win32":
        return ""
    _require_pid(pid)
    exe = _resolve("tasklist")
    if not exe:
        return ""
    return _run_platform(
        [exe, "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
        timeout=10.0,
    )


def wmic_cmdline(pid: int) -> Optional[str]:
    """Return process command line via WMIC, or None."""
    if sys.platform != "win32":
        return None
    _require_pid(pid)
    if not _resolve("wmic"):
        return None
    where_clause = f"ProcessId={pid}"
    if not re.fullmatch(r"ProcessId=\d+", where_clause):
        return None
    out = _run_platform(
        [
            _resolve("wmic") or "wmic",
            "process",
            "where",
            where_clause,
            "get",
            "CommandLine",
        ],
        timeout=15.0,
    )
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    if len(lines) >= 2:
        return " ".join(lines[1:]).strip()
    return None


def wmic_cmdline_value(pid: int) -> str:
    """Return lowercased WMIC /value CommandLine output for orphan scans."""
    if sys.platform != "win32":
        return ""
    _require_pid(pid)
    exe = _resolve("wmic")
    if not exe:
        return ""
    return _run_platform(
        [
            exe,
            "process",
            "where",
            f"ProcessId={pid}",
            "get",
            "CommandLine",
            "/value",
        ],
        timeout=15.0,
    ).lower()


def wmic_process_name_and_ppid(pid: int) -> tuple[Optional[str], Optional[int]]:
    """Return (name, parent_pid) via WMIC table output."""
    if sys.platform != "win32":
        return None, None
    _require_pid(pid)
    exe = _resolve("wmic")
    if not exe:
        return None, None
    out = _run_platform(
        [
            exe,
            "process",
            "where",
            f"ProcessId={pid}",
            "get",
            "Name,ParentProcessId",
        ],
        timeout=15.0,
    )
    lines = out.splitlines()
    if len(lines) > 1:
        parts = lines[1].split()
        if len(parts) >= 2:
            name = parts[0]
            try:
                return name, int(parts[1])
            except ValueError:
                return name, None
    return None, None


def wmic_process_name_and_ppid_value(pid: int) -> tuple[Optional[str], Optional[int]]:
    """Return (name, parent_pid) via WMIC /value output."""
    if sys.platform != "win32":
        return None, None
    _require_pid(pid)
    exe = _resolve("wmic")
    if not exe:
        return None, None
    out = _run_platform(
        [
            exe,
            "process",
            "where",
            f"ProcessId={pid}",
            "get",
            "Name,ParentProcessId",
            "/value",
        ],
        timeout=15.0,
    )
    if not out:
        return None, None
    name_match = re.search(r"Name=(\S+)", out)
    parent_match = re.search(r"ParentProcessId=(\d+)", out)
    if name_match:
        name = name_match.group(1)
        if not name.lower().endswith(".exe"):
            name += ".exe"
        parent_id = int(parent_match.group(1)) if parent_match else None
        return name, parent_id
    return None, None


def powershell_cmdline(pid: int) -> Optional[str]:
    """Return process command line via PowerShell CIM (Windows)."""
    if sys.platform != "win32":
        return None
    _require_pid(pid)
    exe = _resolve("powershell")
    if not exe:
        return None
    cmd_str = f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'
    out = _run_platform([exe, "-NoProfile", "-Command", cmd_str], timeout=15.0)
    return out or None


def get_cmdline(pid: int) -> Optional[str]:
    """Best-effort command line: psutil (caller) then WMIC then PowerShell."""
    if sys.platform != "win32":
        return _unix_cmdline(pid)
    line = wmic_cmdline(pid)
    if line:
        return line
    return powershell_cmdline(pid)


def _unix_cmdline(pid: int) -> Optional[str]:
    _require_pid(pid)
    proc_path = f"/proc/{pid}/cmdline"
    try:
        if os.path.exists(proc_path):
            with open(proc_path, "rb") as fh:
                data = fh.read()
            if not data:
                return None
            parts = [p.decode(errors="replace") for p in data.split(b"\x00") if p]
            return " ".join(parts)
    except OSError:
        pass
    return None


def ps_pid_cmd_csv() -> str:
    """Return ``ps -eo pid,cmd`` output (Unix watcher discovery)."""
    if sys.platform == "win32":
        return ""
    exe = _resolve("ps") or "ps"
    return _run_platform([exe, "-eo", "pid,cmd"], timeout=60.0)


def ps_aux() -> str:
    """Return ``ps aux`` output (Unix orphan cleanup)."""
    if sys.platform == "win32":
        return ""
    exe = _resolve("ps") or "ps"
    return _run_platform([exe, "aux"], timeout=60.0)


def iter_collab_launcher_pids() -> list[int]:
    """Collect PIDs for collab console-script launcher images (Windows).

    Enumerates ``collab.exe`` and ``collab-watcher.exe`` processes via tasklist so
    callers can reap orphaned wrappers that keep the virtualenv ``.exe`` locked. Returns
    an empty list off Windows or when tasklist is unavailable.
    """
    if sys.platform != "win32":
        return []
    pids: list[int] = []
    seen: set[int] = set()
    exe = _resolve("tasklist")
    if not exe:
        return []
    for image in sorted(_COLLAB_LAUNCHER_IMAGE_NAMES):
        out = _run_platform(
            [exe, "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
            timeout=30.0,
        )
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.strip().strip('"').split('","')
            if len(parts) >= 2:
                try:
                    pid = int(parts[1])
                    if pid not in seen:
                        seen.add(pid)
                        pids.append(pid)
                except (ValueError, IndexError):
                    continue
    return pids


def iter_tasklist_python_pids() -> list[int]:
    """Collect PIDs from tasklist for known Python image names."""
    pids: list[int] = []
    seen: set[int] = set()
    for image in sorted(_PYTHON_IMAGE_NAMES):
        out = tasklist_csv_for_image(image)
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.strip().strip('"').split('","')
            if len(parts) >= 2:
                try:
                    pid = int(parts[1])
                    if pid not in seen:
                        seen.add(pid)
                        pids.append(pid)
                except (ValueError, IndexError):
                    continue
    return pids
