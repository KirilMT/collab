"""Tests for scripts/cleanup.py."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

from tests.backend.unit.scripts._helpers import load_script_module

cleanup = load_script_module("cleanup.py", "cleanup_under_test")


class TestIsProtected:
    def test_protected_paths(self):
        assert cleanup._is_protected(Path(".venv/something")) is True
        assert cleanup._is_protected(Path("node_modules/pkg")) is True
        assert cleanup._is_protected(Path(".git/objects")) is True
        assert cleanup._is_protected(Path("instance/db")) is True
        assert cleanup._is_protected(Path("test_data/dummy.json")) is True

    def test_unprotected_paths(self):
        assert cleanup._is_protected(Path("htmlcov/index.html")) is False
        assert cleanup._is_protected(Path(".coverage")) is False
        assert cleanup._is_protected(Path("__pycache__")) is False


class TestRemove:
    def test_remove_nonexistent(self, tmp_path):
        ok, msg = cleanup._remove(tmp_path / "ghost", False)
        assert ok is False
        assert msg == ""

    def test_remove_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        f = tmp_path / ".coverage"
        f.write_text("data", encoding="utf-8")
        ok, _ = cleanup._remove(f, dry_run=False)
        assert ok is True
        assert not f.exists()

    def test_remove_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        d = tmp_path / "htmlcov"
        d.mkdir()
        (d / "index.html").write_text("<html/>", encoding="utf-8")
        ok, _ = cleanup._remove(d, dry_run=False)
        assert ok is True
        assert not d.exists()

    def test_dry_run_does_not_delete(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        f = tmp_path / "coverage.xml"
        f.write_text("xml", encoding="utf-8")
        ok, msg = cleanup._remove(f, dry_run=True)
        assert ok is True
        assert "[DRY-RUN]" in msg
        assert f.exists()

    def test_remove_permission_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        f = tmp_path / "locked"
        f.write_text("x", encoding="utf-8")
        monkeypatch.setattr(Path, "is_dir", lambda self: False)
        monkeypatch.setattr(
            Path,
            "unlink",
            lambda self: (_ for _ in ()).throw(PermissionError("nope")),
        )
        ok, msg = cleanup._remove(f, dry_run=False)
        assert ok is False
        assert "Could not remove" in msg


class TestCleanItemsAndGlob:
    def test_cleans_existing_items(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        (tmp_path / "htmlcov").mkdir()
        (tmp_path / ".coverage").write_text("data", encoding="utf-8")
        count = cleanup._clean_items(["htmlcov", ".coverage", "nonexistent"], False)
        assert count == 2

    def test_cleans_matching_patterns(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "mod.pyc").write_text("", encoding="utf-8")
        count = cleanup._clean_glob(["**/__pycache__"], False)
        assert count >= 1

    def test_skips_protected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        protected = tmp_path / "node_modules"
        protected.mkdir()
        count = cleanup._clean_glob(["node_modules"], False)
        assert count == 0
        assert protected.exists()


class TestCleanFunctions:
    def test_clean_coverage(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        (tmp_path / "coverage").mkdir()
        (tmp_path / "coverage" / "lcov.info").write_text("lcov", encoding="utf-8")
        (tmp_path / ".coverage").write_text("data", encoding="utf-8")
        (tmp_path / "coverage.xml").write_text("<xml/>", encoding="utf-8")
        count = cleanup.clean_coverage(dry_run=False)
        assert count >= 3
        assert "Cleaning coverage" in capsys.readouterr().out

    def test_clean_test_output(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        (tmp_path / "test-results").mkdir()
        (tmp_path / "playwright-report").mkdir()
        (tmp_path / "blob-report").mkdir()
        count = cleanup.clean_test_output(dry_run=False)
        assert count >= 3
        assert "Cleaning test output" in capsys.readouterr().out

    def test_clean_caches(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        (tmp_path / ".pytest_cache").mkdir()
        (tmp_path / ".ruff_cache").mkdir()
        count = cleanup.clean_caches(dry_run=False)
        assert count >= 2
        assert "Cleaning tool caches" in capsys.readouterr().out

    def test_clean_default_and_all(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        (tmp_path / ".coverage").write_text("data", encoding="utf-8")
        (tmp_path / "test-results").mkdir()
        (tmp_path / ".ruff_cache").mkdir()
        assert cleanup.clean_default(dry_run=False) >= 2
        assert cleanup.clean_all(dry_run=False) >= 1


class TestMainCLI:
    def _run_main(self, monkeypatch, tmp_path, args):
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv", ["cleanup.py"] + args)
        return cleanup.main()

    def test_default_mode(self, monkeypatch, tmp_path, capsys):
        (tmp_path / ".coverage").write_text("x", encoding="utf-8")
        rc = self._run_main(monkeypatch, tmp_path, [])
        assert rc == 0
        assert "test artifacts + coverage" in capsys.readouterr().out

    def test_switches(self, monkeypatch, tmp_path, capsys):
        (tmp_path / ".ruff_cache").mkdir()
        assert self._run_main(monkeypatch, tmp_path, ["--all"]) == 0

        (tmp_path / "coverage.xml").write_text("x", encoding="utf-8")
        assert self._run_main(monkeypatch, tmp_path, ["--coverage"]) == 0

        (tmp_path / "test-results").mkdir()
        assert self._run_main(monkeypatch, tmp_path, ["--tests"]) == 0

        (tmp_path / ".mypy_cache").mkdir()
        assert self._run_main(monkeypatch, tmp_path, ["--caches"]) == 0

        out = capsys.readouterr().out
        assert "ARTIFACT CLEANUP" in out

    def test_main_nothing_to_clean_and_dry_run_banner(
        self,
        monkeypatch,
        tmp_path,
        capsys,
    ):
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        rc = self._run_main(monkeypatch, tmp_path, ["--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[DRY-RUN]" in out
        assert "Nothing to clean" in out

    def test_packaging_dry_run(self, monkeypatch, tmp_path, capsys):
        # Dry-run should list packaging artifacts without deleting
        (tmp_path / "dist").mkdir()
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv", ["cleanup.py", "--packaging", "--dry-run"])
        rc = cleanup.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "[DRY-RUN]" in out
        assert "Cleaning packaging artifacts" in out

    def test_packaging_abort_on_no(self, monkeypatch, tmp_path, capsys):
        # If user answers 'no' to confirmation, main should abort with code 2
        (tmp_path / "dist").mkdir()
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv", ["cleanup.py", "--packaging"])
        monkeypatch.setattr("builtins.input", lambda prompt: "n")
        rc = cleanup.main()
        assert rc == 2
        out = capsys.readouterr().out
        assert "Aborted by user." in out

    def test_packaging_confirm_yes_removes(self, monkeypatch, tmp_path, capsys):
        # If user confirms, packaging artifacts should be removed
        d = tmp_path / "dist"
        d.mkdir()
        (tmp_path / "build").mkdir()
        monkeypatch.setattr(cleanup, "ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv", ["cleanup.py", "--packaging"])
        monkeypatch.setattr("builtins.input", lambda prompt: "y")
        rc = cleanup.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Removed" in out or "Would remove" in out
        assert not d.exists()


def test_clean_glob_duplicate_and_value_error_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(cleanup, "ROOT", tmp_path)
    d = tmp_path / "__pycache__"
    d.mkdir()

    # Duplicate pattern should hit seen-guard path.
    assert cleanup._clean_glob(["__pycache__", "__pycache__"], False) == 1

    class _BadPath:
        def __hash__(self):
            return 1

        def __eq__(self, _other):
            return False

        def relative_to(self, _root):
            raise ValueError("outside")

    class _FakeRoot:
        def glob(self, _pattern):
            return [_BadPath()]

    monkeypatch.setattr(cleanup, "ROOT", _FakeRoot())
    assert cleanup._clean_glob(["anything"], False) == 0


def test_remove_handles_oserror(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "ROOT", tmp_path)
    target = tmp_path / "problem.txt"
    target.write_text("x", encoding="utf-8")

    monkeypatch.setattr(
        Path,
        "unlink",
        lambda self: (_ for _ in ()).throw(OSError("busy")),
    )
    ok, msg = cleanup._remove(target, dry_run=False)
    assert ok is False
    assert "Could not remove" in msg


def test_clean_glob_skips_duplicate_entries(monkeypatch, tmp_path):
    duplicate = tmp_path / "dup.pyc"
    duplicate.write_text("data", encoding="utf-8")

    monkeypatch.setattr(cleanup, "ROOT", tmp_path)
    monkeypatch.setattr(
        cleanup.Path,
        "glob",
        lambda self, _pattern: [duplicate, duplicate] if self == tmp_path else [],
    )

    removed = []
    monkeypatch.setattr(
        cleanup,
        "_remove",
        lambda path, dry_run: removed.append(path) or (True, ""),
    )

    count = cleanup._clean_glob(["**/*.pyc"], dry_run=False)
    assert count == 1
    assert removed == [duplicate]


def test_cleanup_module_dunder_main(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cleanup.py", "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path("scripts/cleanup.py", run_name="__main__")
    assert exc.value.code == 0
