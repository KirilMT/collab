"""Tests for entrypoint modules run.py and collab/main.py (implementation module)."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

# tests/backend/unit/test_entrypoints_main_run.py -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_PY = REPO_ROOT / "run.py"


def test_run_py_imports_main_function(monkeypatch):
    called = {"n": 0}

    def _fake_main():
        called["n"] += 1

    # run.py delegates to collab.__main__.main
    monkeypatch.setattr("collab.__main__.main", _fake_main)

    module_globals = runpy.run_path(str(RUN_PY))
    assert "main" in module_globals

    module_globals["main"]()
    assert called["n"] == 1


def test_run_py_dunder_main_executes(monkeypatch):
    called = {"n": 0}

    def _fake_main():
        called["n"] += 1

    # run.py delegates to collab.__main__.main
    monkeypatch.setattr("collab.__main__.main", _fake_main)
    runpy.run_path(str(RUN_PY), run_name="__main__")
    assert called["n"] == 1


def test_collab_main_module_executes_as_script(monkeypatch):
    # Mock the lazily-imported LockClient so daemon-status resolves to a fixed
    # value, making the script's exit code deterministic (1 = not running).
    import collab.lock_client as lc

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def daemon_status(self):
            return False

    monkeypatch.setattr(lc, "LockClient", _Client)
    monkeypatch.setattr("sys.argv", ["python", "daemon-status"])

    # Ensure package 'collab' is not present in sys.modules to avoid
    # runpy runtime-warning about pre-imported package state.
    import sys as _sys

    saved = _sys.modules.pop("collab", None)
    try:
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("collab.main", run_name="__main__")
    finally:
        if saved is not None:
            _sys.modules["collab"] = saved

    # daemon_status() returned False -> daemon-status command exits with 1.
    assert exc.value.code == 1


def test_collab_main_success_path(monkeypatch):
    import collab.main as main_mod

    # Spy on _run_cli to prove main() invokes it exactly once on the happy path.
    called = {"n": 0}

    def _spy():
        called["n"] += 1

    monkeypatch.setattr(main_mod, "_run_cli", _spy)
    main_mod.main()
    assert called["n"] == 1


def test_collab_main_exception_path(monkeypatch, capsys):
    import collab.main as main_mod

    monkeypatch.setattr(
        main_mod,
        "_run_cli",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    exits = []
    monkeypatch.setattr(
        main_mod.sys,
        "exit",
        lambda code: exits.append(code) or (_ for _ in ()).throw(SystemExit(code)),
    )

    with pytest.raises(SystemExit) as exc:
        main_mod.main()

    assert exc.value.code == 1
    assert exits == [1]
    assert "FATAL: lock_client crashed" in capsys.readouterr().err
