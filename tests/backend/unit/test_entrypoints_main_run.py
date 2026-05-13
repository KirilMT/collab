"""Tests for entrypoint modules run.py and src/main.py (implementation module)."""

from __future__ import annotations

import runpy

import pytest


def test_run_py_imports_main_function(monkeypatch):
    called = {"n": 0}

    def _fake_main():
        called["n"] += 1

    # run.py delegates to collab.__main__.main
    monkeypatch.setattr("collab.__main__.main", _fake_main)

    module_globals = runpy.run_path("run.py")
    assert "main" in module_globals

    module_globals["main"]()
    assert called["n"] == 1


def test_run_py_dunder_main_executes(monkeypatch):
    called = {"n": 0}

    def _fake_main():
        called["n"] += 1

    # run.py delegates to collab.__main__.main
    monkeypatch.setattr("collab.__main__.main", _fake_main)
    runpy.run_path("run.py", run_name="__main__")
    assert called["n"] == 1


def test_src_main_module_executes_as_script(monkeypatch):
    monkeypatch.setattr("sys.argv", ["python", "daemon-status"])
    # Ensure package 'src' is not present in sys.modules to avoid
    # runpy runtime-warning about pre-imported package state.
    import sys as _sys

    saved = _sys.modules.pop("src", None)
    try:
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("src.main", run_name="__main__")
    finally:
        if saved is not None:
            _sys.modules["src"] = saved

    assert exc.value.code in (0, 1)


def test_src_main_success_path(monkeypatch):
    import src.main as main_mod

    monkeypatch.setattr(main_mod, "_run_cli", lambda: None)
    main_mod.main()


def test_src_main_exception_path(monkeypatch, capsys):
    import src.main as main_mod

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
