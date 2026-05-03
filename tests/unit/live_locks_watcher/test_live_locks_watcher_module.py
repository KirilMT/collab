"""Module-level tests for live_locks_watcher."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from ._helpers import load_watcher_module


def test_main_function_exists():
    mod = load_watcher_module()
    assert hasattr(mod, "main") and callable(mod.main)


def test_module_imports():
    mod = load_watcher_module()
    assert hasattr(mod, "main")
    assert hasattr(mod, "_parse_git_status_path")
    assert hasattr(mod, "_should_ignore_path")


def test_main_block_present():
    module_file = (
        Path(__file__)
        .resolve()
        .parents[4]
        .joinpath(".collab/pycharm/live_locks_watcher.py")
    )
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
        spec = importlib.util.spec_from_file_location(
            "tmp_watcher",
            Path(__file__)
            .resolve()
            .parents[4]
            .joinpath(".collab/pycharm/live_locks_watcher.py"),
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[arg-defined]

        # Basic smoke checks on functions that depend on the optional imports
        assert callable(mod._color)
        # _notify should use our fake notification without raising
        mod._notify("T", "M")
    finally:
        for name in ("colorama", "plyer", "supabase"):
            try:
                del sys.modules[name]
            except KeyError:
                pass
        import importlib as _importlib

        monkeypatch.setattr(_importlib.util, "find_spec", orig_find_spec)


def test_color_without_colorama():
    mod = load_watcher_module()
    mod._HAS_COLORAMA = False
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
    module_path = (
        Path(__file__)
        .resolve()
        .parents[4]
        .joinpath(".collab/pycharm/live_locks_watcher.py")
    )

    def _raising_find_spec(_name):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(importlib.util, "find_spec", _raising_find_spec)

    spec = importlib.util.spec_from_file_location("tmp_watcher_probe_fail", module_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[arg-defined]

    # Module should still import with optional dependencies disabled.
    assert mod._HAS_COLORAMA is False
    assert mod.create_client is None
    assert mod.desktop_notify is None


def test_reload_watcher_exits_on_local_supabase_shadow(monkeypatch):
    """Watcher should abort when a local .collab/supabase module shadows package."""
    module_path = (
        Path(__file__)
        .resolve()
        .parents[4]
        .joinpath(".collab/pycharm/live_locks_watcher.py")
    )
    collab_root = module_path.parents[1]

    fake_supa_spec = types.SimpleNamespace(origin=str(collab_root / "supabase.py"))

    def _find_spec(name):
        if name == "supabase":
            return fake_supa_spec
        return None

    monkeypatch.setattr(importlib.util, "find_spec", _find_spec)

    spec = importlib.util.spec_from_file_location(
        "tmp_watcher_shadowed_supa", module_path
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)

    with pytest.raises(SystemExit):
        spec.loader.exec_module(mod)  # type: ignore[arg-defined]
