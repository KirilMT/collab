"""Tests for scripts/generate_tests.py."""

from __future__ import annotations

import runpy
import sys

import pytest

from tests.backend.unit.scripts._helpers import load_script_module

gen = load_script_module("generate_tests.py", "generate_tests_under_test")


class TestCodeAnalyzer:
    def test_analyze_finds_public_functions_and_classes(self, tmp_path):
        src = tmp_path / "sample.py"
        src.write_text(
            "class Foo:\n    pass\n\n"
            "def bar():\n    pass\n\n"
            "def _private():\n    pass\n",
            encoding="utf-8",
        )
        analyzer = gen.CodeAnalyzer(str(src))
        entities = analyzer.analyze()
        names = [e[0] for e in entities]
        assert "Foo" in names
        assert "bar" in names
        assert "_private" not in names

    def test_analyze_async_and_bom_and_syntax_error(self, tmp_path, capsys):
        src = tmp_path / "async_mod.py"
        src.write_text("async def fetch():\n    return 1\n", encoding="utf-8")
        assert gen.CodeAnalyzer(str(src)).analyze() == [("fetch", "function")]

        bom = tmp_path / "bom_mod.py"
        bom.write_text("\ufeffdef hello():\n    return 'hi'\n", encoding="utf-8")
        assert gen.CodeAnalyzer(str(bom)).analyze() == [("hello", "function")]

        bad = tmp_path / "bad.py"
        bad.write_text("def broken(:\n    pass\n", encoding="utf-8")
        assert gen.CodeAnalyzer(str(bad)).analyze() == []
        assert "Syntax error" in capsys.readouterr().out


class TestTestGenerator:
    def test_detect_category(self):
        assert gen.TestGenerator("collab/routes/api.py").category == "functional"
        assert gen.TestGenerator("collab/lock_client.py").category == "unit"
        assert gen.TestGenerator("collab/unknown.py").category == "unit"

    def test_generate_empty_entities(self):
        tg = gen.TestGenerator("collab/foo.py")
        assert tg.generate([]) == ""

    def test_generate_import_block_for_src(self):
        tg = gen.TestGenerator("collab/my_module.py")
        code = tg.generate([("MyClass", "class"), ("do_it", "function")])
        assert "import pytest" in code
        assert "from collab.my_module import (" in code
        assert "class TestMyClass:" in code
        assert "test_do_it_is_callable" in code

    def test_generate_path_loader_block_for_scripts(self):
        tg = gen.TestGenerator("scripts/cleanup.py")
        code = tg.generate([("clean_default", "function")])
        assert "import importlib.util" in code
        assert "def _find_repo_root() -> Path:" in code
        assert "module_under_test = _load_module()" in code
        assert "clean_default = module_under_test.clean_default" in code

    def test_get_import_path(self, tmp_path):
        tg = gen.TestGenerator("collab/foo.py")
        assert tg._get_import_path() == "foo"

        tg2 = gen.TestGenerator("other/bar.py")
        assert tg2._get_import_path().endswith("bar")

        external_src = tmp_path / "collab" / "pkg" / "mod.py"
        external_src.parent.mkdir(parents=True, exist_ok=True)
        external_src.write_text("x = 1\n", encoding="utf-8")
        tg3 = gen.TestGenerator(
            str(external_src), repo_root=tmp_path / "different-root"
        )
        tg3.relative_source_path = None
        assert tg3._get_import_path() == "pkg.mod"

        external_no_src = tmp_path / "outside" / "simple.py"
        external_no_src.parent.mkdir(parents=True, exist_ok=True)
        external_no_src.write_text("x = 1\n", encoding="utf-8")
        tg4 = gen.TestGenerator(
            str(external_no_src), repo_root=tmp_path / "another-root"
        )
        tg4.relative_source_path = None
        assert tg4._get_import_path() == "simple"

    def test_direct_import_module_extra_branches(self, tmp_path):
        tg = gen.TestGenerator("run.py")
        assert tg._get_direct_import_module() == "run"

        # Force external-path branch with collab/ in the absolute path.
        external = tmp_path / "collab" / "nested" / "mod.py"
        external.parent.mkdir(parents=True, exist_ok=True)
        external.write_text("def x():\n    return 1\n", encoding="utf-8")
        tg2 = gen.TestGenerator(str(external), repo_root=tmp_path / "other-root")
        assert tg2._get_direct_import_module() == "collab.nested.mod"

    def test_generate_adds_blank_line_when_import_block_has_no_trailing_empty(self):
        tg = gen.TestGenerator("collab/my_mod.py")
        tg._build_import_block = (  # type: ignore[method-assign]
            lambda _names: ["import pytest"]
        )
        code = tg.generate([("Foo", "class")])
        assert "\n\nclass TestFoo:" in code

    def test_build_module_path_expression_for_external_source(self, tmp_path):
        ext = tmp_path / "external.py"
        ext.write_text("x=1\n", encoding="utf-8")
        tg = gen.TestGenerator(str(ext), repo_root=tmp_path / "repo2")
        expr = tg._build_module_path_expression()
        assert "Path(" in expr

    def test_get_test_dir_and_file(self, tmp_path):
        src = tmp_path / "collab" / "collab"
        src.mkdir(parents=True)
        source_file = src / "foo.py"
        source_file.write_text("def x():\n    return 1\n", encoding="utf-8")

        tg = gen.TestGenerator(str(source_file), repo_root=tmp_path)
        assert tg.get_test_dir() == tmp_path / "tests" / "backend" / "unit" / "collab"
        assert tg.get_test_file() == (
            tmp_path / "tests" / "backend" / "unit" / "collab" / "test_foo.py"
        )

        custom_root = tmp_path / "out"
        assert tg.get_test_dir(output_root=custom_root) == custom_root / "unit"


class TestDiscovery:
    def test_find_untested_repo_scan(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\n", encoding="utf-8"
        )
        (tmp_path / "AGENTS.md").write_text("# repo\n", encoding="utf-8")

        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "cleanup.py").write_text(
            "def clean_default():\n    return None\n", encoding="utf-8"
        )

        collab_dir = tmp_path / "collab"
        collab_dir.mkdir(parents=True)
        (collab_dir / "alpha.py").write_text(
            "def x():\n    return 1\n", encoding="utf-8"
        )

        tests_dir = tmp_path / "tests" / "backend" / "unit"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_cleanup.py").write_text("# existing\n", encoding="utf-8")

        disc = gen.TestDiscovery(repo_root=tmp_path)
        untested = disc.find_untested()

        assert "scripts/cleanup.py" not in untested
        assert "collab/alpha.py" in untested

    def test_find_untested_external(self, tmp_path):
        src_dir = tmp_path / "external"
        src_dir.mkdir()
        (src_dir / "beta.py").write_text("x=1\n", encoding="utf-8")

        disc = gen.TestDiscovery(repo_root=gen.ROOT)
        untested = disc.find_untested(str(src_dir))
        assert any("beta.py" in p for p in untested)

    def test_find_untested_returns_empty_for_missing_path(self, tmp_path):
        disc = gen.TestDiscovery(repo_root=tmp_path)
        assert disc.find_untested(str(tmp_path / "does-not-exist")) == []

    def test_iter_repo_source_files_for_non_repo_path(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        f = src / "a.py"
        f.write_text("x=1\n", encoding="utf-8")
        disc = gen.TestDiscovery(repo_root=tmp_path / "other")
        files = list(disc._iter_repo_source_files(src))
        assert f.resolve() in files

    def test_iter_python_files_file_mode_and_hidden_skip(self, tmp_path):
        file_path = tmp_path / "alpha.py"
        file_path.write_text("x=1\n", encoding="utf-8")
        disc = gen.TestDiscovery(repo_root=tmp_path)
        files = list(disc._iter_python_files(file_path))
        assert files == [file_path.resolve()]
        assert disc._should_skip_dir(tmp_path / ".hidden") is True
        assert disc._is_candidate_source(tmp_path / "tests" / "x.py") is False


class TestMain:
    def test_scan_mode(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["generate_tests.py", "--scan"])
        gen.main()
        out = capsys.readouterr().out
        assert "Untested modules" in out or "All modules have tests" in out

    def test_no_source_file_prints_help(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["generate_tests.py"])
        with pytest.raises(SystemExit) as exc_info:
            gen.main()
        assert exc_info.value.code == 1

    def test_missing_file_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["generate_tests.py", "missing.py"])
        with pytest.raises(SystemExit) as exc_info:
            gen.main()
        assert exc_info.value.code == 1
        assert "File not found" in capsys.readouterr().out

    def test_directory_source_exits(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(sys, "argv", ["generate_tests.py", str(tmp_path)])
        with pytest.raises(SystemExit) as exc_info:
            gen.main()
        assert exc_info.value.code == 1
        assert "Source path is a directory" in capsys.readouterr().out

    def test_dry_run_and_write(self, monkeypatch, tmp_path, capsys):
        src = tmp_path / "mod.py"
        src.write_text("def hello():\n    return 1\n", encoding="utf-8")

        monkeypatch.setattr(sys, "argv", ["generate_tests.py", str(src), "--dry-run"])
        gen.main()
        assert "Generated test template" in capsys.readouterr().out

        out_root = tmp_path / "generated"
        monkeypatch.setattr(
            sys,
            "argv",
            ["generate_tests.py", str(src), "--output-root", str(out_root)],
        )
        gen.main()
        # output-root puts files under <output-root>/<category>
        assert (out_root / "unit" / "test_mod.py").exists()

    def test_scan_mode_all_modules_have_tests_branch(self, monkeypatch, capsys):
        class _Discovery:
            def find_untested(self, _scan_target=None):
                return []

        monkeypatch.setattr(gen, "TestDiscovery", lambda: _Discovery())
        monkeypatch.setattr(sys, "argv", ["generate_tests.py", "--scan"])
        gen.main()
        assert "All modules have tests" in capsys.readouterr().out

    def test_no_entities_and_existing_file_branches(
        self,
        monkeypatch,
        tmp_path,
        capsys,
    ):
        src = tmp_path / "m.py"
        src.write_text("x=1\n", encoding="utf-8")

        class _Analyzer:
            def __init__(self, *_a, **_k):
                pass

            def analyze(self):
                return []

        monkeypatch.setattr(gen, "CodeAnalyzer", _Analyzer)
        monkeypatch.setattr(sys, "argv", ["generate_tests.py", str(src)])
        gen.main()
        assert "No testable entities found" in capsys.readouterr().out

        class _Analyzer2:
            def __init__(self, *_a, **_k):
                pass

            def analyze(self):
                return [("hello", "function")]

        monkeypatch.setattr(gen, "CodeAnalyzer", _Analyzer2)
        out_root = tmp_path / "out"
        out_root.mkdir()
        existing = out_root / "unit" / "test_m.py"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("# preexisting\n", encoding="utf-8")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "generate_tests.py",
                str(src),
                "--output-root",
                str(out_root),
            ],
        )
        gen.main()
        assert "File exists" in capsys.readouterr().out


def test_generate_tests_dunder_main(monkeypatch, tmp_path):
    src = tmp_path / "sample_mod.py"
    src.write_text("def hello():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["generate_tests.py", str(src), "--dry-run"])
    runpy.run_path("scripts/generate_tests.py", run_name="__main__")
