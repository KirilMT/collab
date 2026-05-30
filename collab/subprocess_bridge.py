"""Indirection for subprocess used by ``safe_subprocess`` and ``platform_probe``.

Production code always resolves the stdlib ``subprocess`` module through
:func:`get_subprocess`. Tests replace behavior via :func:`set_test_override` (see
``tests.backend.subprocess_testing``).
"""

from __future__ import annotations

import subprocess as _stdlib_subprocess
from types import ModuleType

_test_override: ModuleType | None = None


def get_subprocess() -> ModuleType:
    """Return the active subprocess module (test override or stdlib)."""
    if _test_override is not None:
        return _test_override
    return _stdlib_subprocess


def set_test_override(module: ModuleType | None) -> None:
    """Install or clear a test double for :func:`get_subprocess`."""
    global _test_override
    _test_override = module
