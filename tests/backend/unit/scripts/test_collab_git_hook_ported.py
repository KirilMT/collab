"""Extra branch coverage tests for scripts/collab_git_hook.py."""

from __future__ import annotations

import importlib.util
import json
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
    root = _repo_root()
    project_root = str(root.resolve())
    if project_root in sys.path:
        sys.path.remove(project_root)

    mod = _load_fresh("collab_git_hook_fresh_path")
    assert str(mod.PROJECT_ROOT) in sys.path


def test_read_pid_file_json_decode_and_non_int_pid(monkeypatch, tmp_path):
    from tests.backend.unit.scripts._helpers import load_script_module

    hook = load_script_module("collab_git_hook.py", "collab_git_hook_extra_pid")
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr("collab.lock_client.PID_FILE", str(pid_file))

    pid_file.write_text("{bad-json", encoding="utf-8")
    assert hook._read_pid_file() is None

    pid_file.write_text(json.dumps({"pid": "abc"}), encoding="utf-8")
    assert hook._read_pid_file() is None
