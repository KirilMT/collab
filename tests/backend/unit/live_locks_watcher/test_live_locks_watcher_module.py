"""Module-level tests for live_locks_watcher."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from ._helpers import load_watcher_module, reload_watcher_module


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "collab").exists():
            return parent
    raise FileNotFoundError("Could not locate repository root")


def test_module_imports():
    # Merged former test_main_function_exists here (strict subset): keep its
    # callable(main) assertion so no coverage/assertion strength is lost.
    mod = load_watcher_module()
    assert hasattr(mod, "main") and callable(mod.main)
    assert hasattr(mod, "_parse_git_status_path")
    assert hasattr(mod, "_should_ignore_path")


def test_main_block_present():
    module_file = _repo_root().joinpath("collab/live_locks_watcher.py")
    src = module_file.read_text(encoding="utf-8")
    assert '__name__ == "__main__"' in src or "__name__ == '__main__'" in src


def test_reload_watcher_with_colorama_and_plyer(monkeypatch):
    """Reload the watcher module with fake colorama and plyer to exercise optional-
    import branches executed at module import time."""
    fake_colorama = types.SimpleNamespace(
        Fore=types.SimpleNamespace(GREEN="G", YELLOW="Y", CYAN="C", MAGENTA="M"),
        Style=types.SimpleNamespace(RESET_ALL="R"),
        init=lambda: None,
    )
    fake_plyer = types.SimpleNamespace(
        notification=types.SimpleNamespace(
            notify=lambda **k: None,
        ),
    )
    fake_supa = types.SimpleNamespace(create_client=lambda url, key: object())

    # Inject into sys.modules and monkeypatch find_spec so importlib sees them
    sys.modules["colorama"] = fake_colorama
    sys.modules["plyer"] = types.SimpleNamespace(notification=fake_plyer.notification)
    sys.modules["supabase"] = fake_supa
    orig_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())

    try:
        mod = reload_watcher_module("collab.live_locks_watcher_colorama")

        # Basic smoke checks on functions that depend on the optional imports
        assert callable(mod._color)

        # _notify should forward to the desktop notifier. Spy on it and disable
        # COLLAB_TEST_MODE (which otherwise short-circuits _notify).
        notify_calls = []
        spy = types.SimpleNamespace(notify=lambda **kwargs: notify_calls.append(kwargs))
        monkeypatch.setattr(mod, "desktop_notify", spy)
        monkeypatch.setenv("COLLAB_TEST_MODE", "0")

        mod._notify("Title", "Body")

        assert len(notify_calls) == 1
        assert notify_calls[0]["title"] == "Title"
        assert notify_calls[0]["message"] == "Body"
        assert notify_calls[0]["app_name"] == "Collab Locks"
    finally:
        for name in ("colorama", "plyer", "supabase"):
            try:
                del sys.modules[name]
            except KeyError:
                pass
        import importlib as _importlib

        monkeypatch.setattr(_importlib.util, "find_spec", orig_find_spec)


def test_color_without_colorama(monkeypatch):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_HAS_COLORAMA", False)
    out = mod._color("hello", "X")
    assert out == "hello"


def test_setup_collab_logging_fallback_to_basic_config(monkeypatch):
    """setup_collab_logging falls back to basicConfig when _setup_collab_logging_obj is
    None."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_setup_collab_logging_obj", None)
    called = []
    monkeypatch.setattr(
        mod.logging, "basicConfig", lambda **kwargs: called.append(kwargs)
    )
    mod.setup_collab_logging(collab_dir="/fake/collab")
    assert called, "basicConfig should be called when _setup_collab_logging_obj is None"


def test_reload_watcher_handles_find_spec_exceptions(monkeypatch):
    """Import-time optional dependency probes should tolerate find_spec errors."""

    def _raising_find_spec(_name):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(importlib.util, "find_spec", _raising_find_spec)

    mod = reload_watcher_module("collab.live_locks_watcher_probe_fail")

    # Module should still import with optional dependencies disabled.
    assert mod._HAS_COLORAMA is False
    assert mod.create_client is None
    assert mod.desktop_notify is None


def test_reload_watcher_exits_on_local_supabase_shadow(monkeypatch):
    """Watcher should abort when a local .collab/supabase module shadows package."""
    module_path = _repo_root().joinpath("collab/live_locks_watcher.py")
    # Force a deterministic runtime collab root so shadow detection doesn't
    # depend on the process cwd/environment.
    monkeypatch.setenv("COLLAB_HOME", str(module_path.parent))
    fake_supa_spec = types.SimpleNamespace(
        origin=str(module_path.parent / "supabase.py")
    )

    def _find_spec(name):
        if name == "supabase":
            return fake_supa_spec
        return None

    monkeypatch.setattr(importlib.util, "find_spec", _find_spec)

    with pytest.raises(SystemExit):
        reload_watcher_module("collab.live_locks_watcher_shadowed_supa")


def test_watcher_allows_project_venv_site_packages_origin(monkeypatch):
    """Watcher import should allow installed packages from a repo-local virtualenv."""
    module_path = _repo_root().joinpath("collab/live_locks_watcher.py")
    project_root = module_path.parents[1]
    monkeypatch.delenv("COLLAB_HOME", raising=False)
    monkeypatch.setenv("COLLAB_PROJECT_ROOT", str(project_root))
    fake_supa_spec = types.SimpleNamespace(
        origin=str(
            project_root
            / ".venv"
            / "Lib"
            / "site-packages"
            / "supabase"
            / "__init__.py"
        )
    )

    def _find_spec(name):
        if name == "supabase":
            return fake_supa_spec
        return None

    fake_supa = types.SimpleNamespace(create_client=lambda *_a, **_k: object())
    monkeypatch.setitem(sys.modules, "supabase", fake_supa)
    monkeypatch.setattr(importlib.util, "find_spec", _find_spec)

    mod = reload_watcher_module("collab.live_locks_watcher_site_packages_supa")
    assert callable(mod.create_client)
