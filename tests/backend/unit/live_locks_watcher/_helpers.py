"""Helpers for live_locks_watcher tests.

Provide a stable loader that imports the watcher module from `collab` and ensures
optional imports are mocked so tests don't fail at import-time when running in CI
without optional deps.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

# Mock optional external modules that the watcher may import at module load
# time to avoid ImportError / SystemExit during tests.
sys.modules.setdefault("supabase", mock.MagicMock())
sys.modules.setdefault("plyer", mock.MagicMock())


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "collab").exists():
            return parent
    raise FileNotFoundError("Could not locate repository root")


def _ensure_collab_package(proj_root: Path) -> None:
    """Register ``collab`` so relative imports resolve in file-based loads."""
    if "collab" in sys.modules:
        return
    collab_init = proj_root / "collab" / "__init__.py"
    pkg_spec = importlib.util.spec_from_file_location(
        "collab",
        collab_init,
        submodule_search_locations=[str(proj_root / "collab")],
    )
    assert pkg_spec and pkg_spec.loader
    pkg = importlib.util.module_from_spec(pkg_spec)
    sys.modules["collab"] = pkg
    pkg_spec.loader.exec_module(pkg)  # type: ignore[attr-defined]


def _watcher_spec(proj_root: Path, mod_name: str = "collab.live_locks_watcher"):
    module_path = proj_root / "collab" / "live_locks_watcher.py"
    _ensure_collab_package(proj_root)
    spec = importlib.util.spec_from_file_location(
        mod_name,
        module_path,
        submodule_search_locations=[str(proj_root / "collab")],
    )
    assert spec and spec.loader
    return spec, module_path


def reload_watcher_module(mod_name: str = "collab.live_locks_watcher"):
    """Load a fresh watcher module instance (for import-time branch tests)."""
    proj_root = _find_repo_root()
    sys.modules.pop(mod_name, None)
    spec, _ = _watcher_spec(proj_root, mod_name)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def patch_git_capture(monkeypatch, mod, handler) -> None:
    """Replace watcher git helper for tests (runtime uses safe_subprocess, not
    check_output)."""
    monkeypatch.setattr(mod, "_git_capture_text", handler)


def load_watcher_module():
    proj_root = _find_repo_root()
    mod_name = "collab.live_locks_watcher"
    # If already loaded, return the cached module but reset volatile
    # module-level state so tests execute with predictable defaults.
    # Many tests expect the same module *instance* (a `watcher` variable
    # created at import-time), while also needing fresh global state
    # between test invocations. Reset known mutable globals here.
    if mod_name in sys.modules:
        mod = sys.modules[mod_name]
        for _attr, _val in (
            ("_shutdown_done", False),
            ("_local_owned_locks", set()),
            ("_active_conflicts", set()),
            ("_warned_remote_locks", set()),
            ("_known_remote_locks", set()),
            ("_lock_acquired_at", {}),
            ("SESSION_TOKEN", ""),
            ("_dashboard_url", None),
            ("_dashboard_server", None),
            ("_dashboard_thread", None),
        ):
            try:
                setattr(mod, _attr, _val)
            except Exception:
                pass
        # Use a test-local PID file to avoid colliding with any real watcher
        # running on the developer machine or CI environment.
        try:
            tmp_pid_name = f"pytest_collab_{os.getpid()}.daemon.pid"
            tmp_pid = os.path.join(tempfile.gettempdir(), tmp_pid_name)
            setattr(mod, "PID_FILE", tmp_pid)
        except Exception:
            pass
        # Use a test-local isolated .collab root to avoid touching repo files.
        try:
            tmp_collab_dir = os.path.join(
                tempfile.gettempdir(), f"pytest_collab_{os.getpid()}_collab"
            )
            os.makedirs(tmp_collab_dir, exist_ok=True)
            # Mirror the real dashboard template into the test collab root
            try:
                repo_dashboard = Path(proj_root) / "collab" / "dashboard"
                if repo_dashboard.exists():
                    shutil.copytree(
                        str(repo_dashboard),
                        os.path.join(tmp_collab_dir, "dashboard"),
                        dirs_exist_ok=True,
                    )
            except Exception:
                pass
            setattr(mod, "_COLLAB_ROOT", tmp_collab_dir)
        except Exception:
            pass
        return mod

    spec, _ = _watcher_spec(proj_root, mod_name)
    mod = importlib.util.module_from_spec(spec)
    # Insert into sys.modules early so recursive imports / monkeypatching
    # that reference the module name get the same instance.
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    # Also set a test-local PID file by default to avoid false positives when
    # detecting existing watcher processes on the developer machine.
    try:
        tmp_pid_name = f"pytest_collab_{os.getpid()}.daemon.pid"
        tmp_pid = os.path.join(tempfile.gettempdir(), tmp_pid_name)
        setattr(mod, "PID_FILE", tmp_pid)
    except Exception:
        pass
    # Use a test-local isolated .collab root so tests don't modify repo files.
    try:
        tmp_collab_dir = os.path.join(
            tempfile.gettempdir(), f"pytest_collab_{os.getpid()}_collab"
        )
        os.makedirs(tmp_collab_dir, exist_ok=True)
        # Mirror the real dashboard template into the test collab root
        try:
            repo_dashboard = Path(proj_root) / "collab" / "dashboard"
            if repo_dashboard.exists():
                shutil.copytree(
                    str(repo_dashboard),
                    os.path.join(tmp_collab_dir, "dashboard"),
                    dirs_exist_ok=True,
                )
        except Exception:
            pass
        setattr(mod, "_COLLAB_ROOT", tmp_collab_dir)
    except Exception:
        pass
    return mod


def patch_subprocess(monkeypatch, **kwargs):
    """Stub subprocess for code paths using ``subprocess_bridge``."""
    from tests.backend.subprocess_testing import patch_subprocess as _patch

    return _patch(monkeypatch, **kwargs)
