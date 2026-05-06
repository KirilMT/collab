"""Helpers for loading script modules in tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from tests.backend.unit.lock_client._helpers import (
    FakeResponse,
    load_lock_client_module,
    make_create_client,
)


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "scripts").exists():
            return parent
    raise FileNotFoundError("Could not locate repository root")


ROOT = _find_repo_root()
SCRIPTS_DIR = ROOT / "scripts"


def load_script_module(script_name: str, module_name: str):
    """Load a script file from scripts/ as an importable module object."""
    module_path = SCRIPTS_DIR / script_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


__all__ = [
    "FakeResponse",
    "load_lock_client_module",
    "load_script_module",
    "make_create_client",
]
