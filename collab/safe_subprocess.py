"""Centralized safe subprocess utilities for collab runtime (Phase 5).

Resolves executables to absolute paths, validates argv against allowlists, applies
default timeouts, and returns typed results for logging and tests.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from .errors import SubprocessSecurityError
from .subprocess_bridge import get_subprocess

logger = logging.getLogger("collab.safe_subprocess")

DEFAULT_CAPTURE_TIMEOUT_S = 30.0
DEFAULT_RUN_TIMEOUT_S = 60.0

# CREATE_NO_WINDOW — hide console for background git probes on Windows.
_WIN_CREATE_NO_WINDOW = 0x08000000


def _host_supports_creationflags() -> bool:
    """True only on a real Windows host (not ``sys.platform`` test doubles)."""
    return os.name == "nt"


# Git subcommands used by lock_client / live_locks_watcher.
_ALLOWED_GIT_SUBCOMMANDS = frozenset(
    {
        "branch",
        "config",
        "diff",
        "fetch",
        "for-each-ref",
        "merge-base",
        "merge-tree",
        "remote",
        "rev-list",
        "rev-parse",
        "show",
        "status",
    }
)

# Watcher daemon argv shape: python -m collab.lock_client watch ...
_ALLOWED_WATCHER_FLAGS = frozenset(
    {
        "--daemon",
        "--interval",
        "--open-dashboard",
        "--parent-method",
        "--parent-name",
        "--parent-pid",
        "--pid-file",
        "--timeout",
    }
)

_TASKKILL_FLAGS = frozenset({"/F", "/PID", "/T"})

# Agent claim argv shape: python -m collab claim <path...> [--label V] [--reason V]
_ALLOWED_CLAIM_FLAGS = frozenset({"--label", "--reason"})


@dataclass(frozen=True)
class CaptureResult:
    """Result of a captured subprocess invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


@dataclass(frozen=True)
class RunResult:
    """Result of a subprocess run without mandatory capture."""

    argv: tuple[str, ...]
    returncode: int
    timed_out: bool = False


def is_test_mode() -> bool:
    return os.getenv("COLLAB_TEST_MODE") == "1"


def resolve_executable(name: str) -> Optional[str]:
    """Return an absolute executable path from PATH (or name in test mode)."""
    try:
        resolved = shutil.which(name)
    except (AttributeError, OSError, ValueError):
        resolved = None
    if not resolved:
        if is_test_mode():
            return name
        return None
    return os.path.abspath(resolved)


def _basename(argv0: str) -> str:
    return os.path.basename(argv0).lower().replace(".exe", "")


def _is_python_executable(argv0: str) -> bool:
    base = _basename(argv0)
    return base in {"python", "pythonw", "python3"}


def _validate_git_argv(argv: Sequence[str]) -> None:
    if len(argv) < 2:
        raise SubprocessSecurityError("git invocation requires a subcommand")
    sub = argv[1]
    if sub not in _ALLOWED_GIT_SUBCOMMANDS:
        raise SubprocessSecurityError(f"git subcommand not allowed: {sub!r}")


def _validate_taskkill_argv(argv: Sequence[str]) -> None:
    if len(argv) < 4:
        raise SubprocessSecurityError("taskkill invocation too short")
    flags = {a.upper() for a in argv[1:-2] if a.startswith("/")}
    if not flags.issubset(_TASKKILL_FLAGS):
        raise SubprocessSecurityError(f"taskkill flags not allowed: {flags!r}")
    if argv[-2].upper() != "/PID":
        raise SubprocessSecurityError("taskkill requires /PID before process id")
    if not str(argv[-1]).isdigit():
        raise SubprocessSecurityError("taskkill PID must be numeric")


def _validate_watcher_argv(argv: Sequence[str]) -> None:
    if len(argv) < 4:
        raise SubprocessSecurityError("watcher argv too short")
    if not _is_python_executable(argv[0]):
        raise SubprocessSecurityError("watcher must be launched with python/pythonw")
    if tuple(argv[1:4]) != ("-m", "collab.lock_client", "watch"):
        raise SubprocessSecurityError(
            "watcher module entry must be collab.lock_client watch"
        )
    idx = 4
    while idx < len(argv):
        token = argv[idx]
        if token not in _ALLOWED_WATCHER_FLAGS:
            raise SubprocessSecurityError(f"watcher flag not allowed: {token!r}")
        if token in {"--interval", "--timeout", "--parent-pid", "--pid-file"}:
            idx += 2
            continue
        if token in {"--parent-name", "--parent-method"}:
            idx += 2
            continue
        idx += 1


def _validate_agent_claim_argv(argv: Sequence[str]) -> None:
    if len(argv) < 5:
        raise SubprocessSecurityError("agent claim argv too short")
    if not _is_python_executable(argv[0]):
        raise SubprocessSecurityError("claim must be launched with python")
    if tuple(argv[1:4]) != ("-m", "collab", "claim"):
        raise SubprocessSecurityError("claim module entry must be collab claim")
    rest = list(argv[4:])
    has_path = False
    idx = 0
    while idx < len(rest):
        token = rest[idx]
        if token in _ALLOWED_CLAIM_FLAGS:
            if idx + 1 >= len(rest):
                raise SubprocessSecurityError(f"{token} requires a value")
            idx += 2
            continue
        if token.startswith("--"):
            raise SubprocessSecurityError(f"claim flag not allowed: {token!r}")
        has_path = True
        idx += 1
    if not has_path:
        raise SubprocessSecurityError("claim requires at least one file path")


def validate_argv(argv: Sequence[str], *, policy: str = "auto") -> tuple[str, ...]:
    """Validate and normalize argv; resolve argv[0] to an absolute path."""
    if not argv:
        raise SubprocessSecurityError("empty argv")
    normalized = list(argv)
    exe_name = _basename(normalized[0])

    if policy == "auto":
        if exe_name == "git":
            policy = "git"
        elif exe_name == "taskkill":
            policy = "taskkill"
        elif _is_python_executable(normalized[0]):
            policy = "watcher"
        elif exe_name in {"powershell", "wmic"}:
            policy = "platform"
        else:
            policy = "generic"

    if policy == "git":
        git_exe = resolve_executable("git") or normalized[0]
        normalized[0] = git_exe
        _validate_git_argv(normalized)
    elif policy == "taskkill":
        tk = resolve_executable("taskkill") or normalized[0]
        normalized[0] = tk
        _validate_taskkill_argv(normalized)
    elif policy == "watcher":
        if not _is_python_executable(normalized[0]) and _basename(
            normalized[0]
        ) not in {
            "python",
            "pythonw",
            "python3",
        }:
            raise SubprocessSecurityError(
                "watcher must be launched with python/pythonw"
            )
        if not os.path.isabs(normalized[0]):
            if _basename(normalized[0]) == "pythonw":
                candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                normalized[0] = (
                    candidate if os.path.exists(candidate) else sys.executable
                )
            else:
                normalized[0] = sys.executable
        normalized[0] = os.path.abspath(normalized[0])
        _validate_watcher_argv(normalized)
    elif policy == "agent_claim":
        if not _is_python_executable(normalized[0]):
            raise SubprocessSecurityError("claim must be launched with python")
        if not os.path.isabs(normalized[0]):
            normalized[0] = sys.executable
        normalized[0] = os.path.abspath(normalized[0])
        _validate_agent_claim_argv(normalized)
    elif policy == "platform":
        resolved = resolve_executable(normalized[0])
        if resolved:
            normalized[0] = resolved
    else:
        resolved = resolve_executable(normalized[0])
        if resolved:
            normalized[0] = resolved
        elif not is_test_mode():
            raise SubprocessSecurityError(
                f"executable not found on PATH: {normalized[0]!r}"
            )

    return tuple(normalized)


def capture(
    argv: Sequence[str],
    *,
    policy: str = "auto",
    timeout: float = DEFAULT_CAPTURE_TIMEOUT_S,
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    text: bool = False,
) -> CaptureResult:
    """Run a subprocess and capture stdout/stderr with timeout."""
    sp = get_subprocess()
    safe_argv = validate_argv(argv, policy=policy)
    kwargs: dict[str, Any] = {
        "stderr": sp.DEVNULL,
        "timeout": timeout,
        "cwd": cwd,
    }
    if env is not None:
        kwargs["env"] = dict(env)
    if text:
        kwargs["text"] = True
    if _host_supports_creationflags():
        kwargs["creationflags"] = _WIN_CREATE_NO_WINDOW
    try:
        out = sp.check_output(list(safe_argv), **kwargs)
        return CaptureResult(
            argv=safe_argv,
            returncode=0,
            stdout=out if isinstance(out, bytes) else out.encode(),
            stderr=b"",
        )
    except sp.TimeoutExpired as exc:
        logger.warning("subprocess timed out: %s", " ".join(safe_argv))
        return CaptureResult(
            argv=safe_argv,
            returncode=-1,
            stdout=exc.output or b"",
            stderr=exc.stderr or b"",
            timed_out=True,
        )
    except sp.CalledProcessError as exc:
        return CaptureResult(
            argv=safe_argv,
            returncode=int(exc.returncode),
            stdout=exc.output or b"",
            stderr=exc.stderr or b"",
        )


def run(
    argv: Sequence[str],
    *,
    policy: str = "auto",
    timeout: float = DEFAULT_RUN_TIMEOUT_S,
    cwd: Optional[str] = None,
    capture_output: bool = True,
) -> RunResult:
    """Run a subprocess with validation and optional capture."""
    sp = get_subprocess()
    safe_argv = validate_argv(argv, policy=policy)
    kwargs: dict[str, Any] = {"cwd": cwd, "timeout": timeout}
    if capture_output:
        kwargs["capture_output"] = True
    if _host_supports_creationflags():
        kwargs["creationflags"] = _WIN_CREATE_NO_WINDOW
    try:
        completed = sp.run(list(safe_argv), **kwargs)
        return RunResult(
            argv=safe_argv,
            returncode=int(completed.returncode),
        )
    except sp.TimeoutExpired:
        logger.warning("subprocess timed out: %s", " ".join(safe_argv))
        return RunResult(argv=safe_argv, returncode=-1, timed_out=True)


def spawn_background(
    argv: Sequence[str],
    *,
    policy: str = "watcher",
    cwd: Optional[str] = None,
    creationflags: int = 0,
    start_new_session: bool = False,
    env: Optional[Mapping[str, str]] = None,
) -> Any:
    """Spawn a background process (daemon watcher) after argv validation."""
    sp = get_subprocess()
    safe_argv = validate_argv(argv, policy=policy)
    popen_kwargs: dict[str, Any] = {
        "stdout": sp.DEVNULL,
        "stderr": sp.DEVNULL,
        "cwd": cwd,
        "close_fds": True,
    }
    if env is not None:
        popen_kwargs["env"] = dict(env)
    if _host_supports_creationflags():
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = start_new_session
    logger.debug("spawn_background: %s", " ".join(shlex.quote(a) for a in safe_argv))
    return sp.Popen(list(safe_argv), **popen_kwargs)


def decode_output(data: bytes) -> str:
    return data.decode(errors="replace")
