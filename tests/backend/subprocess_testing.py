"""Helpers for tests that need to stub OS subprocess calls.

Runtime code invokes subprocess only through ``safe_subprocess`` and ``platform_probe``,
which call :func:`src.subprocess_bridge.get_subprocess`.
"""

from __future__ import annotations

import os
import subprocess as _stdlib_subprocess
import types
from collections.abc import Callable, Sequence
from types import ModuleType
from typing import Any, Optional

from src import subprocess_bridge


def argv_executable_is(args: Sequence[str], name: str) -> bool:
    """True when argv[0] is ``name`` or ``name.exe`` (PATH-resolved paths included)."""
    if not args:
        return False
    base = os.path.basename(str(args[0])).lower()
    want = name.lower()
    return base == want or base == f"{want}.exe"


def argv_subcommand(args: Sequence[str], executable: str, *parts: str) -> bool:
    """Match argv when the executable may be an absolute PATH resolution."""
    if not argv_executable_is(args, executable):
        return False
    return tuple(args[1 : 1 + len(parts)]) == parts


def _shim_from_stdlib(
    *,
    run: Optional[Callable[..., Any]] = None,
    check_output: Optional[Callable[..., Any]] = None,
    popen: Optional[Callable[..., Any]] = None,
) -> ModuleType:
    """Build a subprocess-like module delegating to stdlib except overridden methods."""
    shim = types.ModuleType("subprocess")
    for name in (
        "run",
        "check_output",
        "Popen",
        "DEVNULL",
        "TimeoutExpired",
        "CalledProcessError",
    ):
        setattr(shim, name, getattr(_stdlib_subprocess, name))
    if run is not None:
        setattr(shim, "run", run)
    if check_output is not None:
        setattr(shim, "check_output", check_output)
    if popen is not None:
        setattr(shim, "Popen", popen)
    return shim


def patch_subprocess(
    monkeypatch: Any,
    *,
    run: Optional[Callable[..., Any]] = None,
    check_output: Optional[Callable[..., Any]] = None,
    popen: Optional[Callable[..., Any]] = None,
) -> ModuleType:
    """Route wrapper subprocess calls through a test double for this test."""
    shim = _shim_from_stdlib(run=run, check_output=check_output, popen=popen)
    subprocess_bridge.set_test_override(shim)
    return shim
