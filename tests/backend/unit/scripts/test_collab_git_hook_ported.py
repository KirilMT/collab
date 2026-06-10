"""Extra branch coverage tests for scripts/collab_git_hook.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "scripts").exists():
            return parent
    raise FileNotFoundError("Could not locate repository root")


def _load_fresh(module_name: str):
    root = _repo_root()
    module_path = root / "scripts" / "collab_git_hook.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_project_root_added_to_sys_path(monkeypatch):
    # Isolate sys.path: swap in a copy so removing/re-adding the project root
    # while exec'ing a fresh module cannot leak into later tests. monkeypatch
    # restores the original sys.path object on teardown.
    monkeypatch.setattr(sys, "path", list(sys.path))
    project_root = str(_repo_root().resolve())
    while project_root in sys.path:
        sys.path.remove(project_root)

    mod = _load_fresh("collab_git_hook_fresh_path")
    assert str(mod.PROJECT_ROOT) in sys.path
