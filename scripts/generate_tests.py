#!/usr/bin/env python3
"""Generate test stubs for new Python code.

Analyzes source files using AST and generates pytest test templates that follow
repository standards. Generated tests include proper fixtures, docstrings, and
the Arrange-Act-Assert pattern.

Usage:
    python scripts/generate_tests.py src/services/new_module.py
    python scripts/generate_tests.py src/lock_client.py
    python scripts/generate_tests.py scripts/cleanup.py --dry-run
    python scripts/generate_tests.py --scan
    python scripts/generate_tests.py src/ --scan
"""

import argparse
import ast
import os
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


ROOT = Path(__file__).resolve().parent.parent


def _is_relative_to(path: Path, other: Path) -> bool:
    """Return True when *path* is located under *other*."""
    try:
        path.relative_to(other)
    except ValueError:
        return False
    return True


class CodeAnalyzer:
    """Analyzes Python source files to extract testable entities."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.module_name = Path(filepath).stem
        self.entities: List[Tuple[str, str]] = []  # (name, type)

    def analyze(self) -> List[Tuple[str, str]]:
        """Extract module-level public (non-private) classes and functions."""
        with open(self.filepath, "r", encoding="utf-8-sig") as f:
            content = f.read()

        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            print(f"⚠️  Syntax error in {self.filepath}: {e}")
            return []

        # Only inspect top-level statements to avoid nested/helper functions.
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                self.entities.append((node.name, "class"))
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and not node.name.startswith("_"):
                self.entities.append((node.name, "function"))

        return self.entities


class TestGenerator:
    """Generates pytest test templates following repo standards."""

    CATEGORY_PATTERNS = {
        "api.py": "functional",
        "routes.py": "functional",
        "lock_client.py": "unit",
        "utils.py": "unit",
        "models.py": "unit",
    }

    def __init__(
        self,
        source_file: str,
        category: Optional[str] = None,
        repo_root: Optional[Path] = None,
    ):
        self.repo_root = (repo_root or ROOT).resolve()
        self.source_path = Path(source_file).resolve()
        self.source_file = str(self.source_path)
        self.module_name = self.source_path.stem
        self.relative_source_path = self._get_relative_source_path()
        self.category = category or self._detect_category()

    def _get_relative_source_path(self) -> Optional[Path]:
        """Return the repository-relative source path when available."""
        if _is_relative_to(self.source_path, self.repo_root):
            return self.source_path.relative_to(self.repo_root)
        return None

    def _detect_category(self) -> str:
        """Auto-detect test category based on file path."""
        filepath_lower = self._display_source_path().lower()
        if filepath_lower.startswith("scripts/") or filepath_lower.startswith(
            ".collab/"
        ):
            return "unit"
        for pattern, cat in self.CATEGORY_PATTERNS.items():
            if pattern in filepath_lower:
                return cat
        return "unit"

    def _display_source_path(self) -> str:
        """Return a readable source path for messages and generated docstrings."""
        if self.relative_source_path is not None:
            return self.relative_source_path.as_posix()
        return self.source_path.as_posix()

    def _get_direct_import_module(self) -> Optional[str]:
        """Return the direct import module path when the source is importable."""
        rel = self.relative_source_path
        if rel is None:
            path = self.source_file.replace("\\", "/")
            if "src/" in path:
                return "src." + path.split("src/")[1].replace("/", ".").replace(
                    ".py", ""
                )
            return self.module_name

        parts = rel.with_suffix("").parts
        if not parts:
            return self.module_name
        if parts[0] == "src":
            return ".".join(parts)
        if len(parts) == 1:
            return parts[0]
        return None

    def get_test_dir(self, output_root: Optional[Path] = None) -> Path:
        """Return the target directory for the generated test file."""
        if output_root is not None:
            return Path(output_root) / self.category

        rel = self.relative_source_path
        if rel is not None and rel.parts:
            # Preserve module structure, e.g., src/foo.py -> tests/backend/unit/foo
            if len(rel.parts) > 1 and rel.parts[0] == "src":
                sub_path = Path(*rel.parts[1:-1])
                return self.repo_root / "tests" / "backend" / self.category / sub_path

        return self.repo_root / "tests" / "backend" / self.category

    def get_test_file(self, output_root: Optional[Path] = None) -> Path:
        """Return the full destination path for the generated test file."""
        return self.get_test_dir(output_root=output_root) / (
            f"test_{self.module_name}.py"
        )

    def generate(self, entities: List[Tuple[str, str]]) -> str:
        """Generate test file content."""
        if not entities:
            return ""

        # Add imports
        classes = [e[0] for e in entities if e[1] == "class"]
        functions = [e[0] for e in entities if e[1] == "function"]
        import_names = sorted(set(classes + functions))

        lines = [f'"""Tests for `{self._display_source_path()}`."""', ""]
        lines.extend(self._build_import_block(import_names))

        if lines[-1] != "":
            lines.append("")

        # Generate test classes
        for class_name, _ in entities:
            if class_name in classes:
                lines.extend(self._generate_class_tests(class_name))
                lines.append("")

        # Generate function tests
        function_entities = [e[0] for e in entities if e[1] == "function"]
        if function_entities:
            lines.extend(self._generate_function_tests(function_entities))

        return "\n".join(lines)

    def _build_import_block(self, import_names: List[str]) -> List[str]:
        """Build the import or module-loading section for generated tests."""
        direct_import = self._get_direct_import_module()

        if direct_import:
            lines = ["import pytest", ""]
            lines.append(f"from {direct_import} import (")
            for name in import_names:
                lines.append(f"    {name},")
            lines[-1] = lines[-1].rstrip(",")
            lines.append(")")
            lines.append("")
            return lines

        return self._build_path_loader_block(import_names)

    def _get_import_path(self) -> str:
        """Convert file path to import path."""
        rel = self.relative_source_path
        if rel is not None:
            rel_without_suffix = rel.with_suffix("")
            if rel.parts and rel.parts[0] == "src":
                return ".".join(rel_without_suffix.parts[1:])
            return ".".join(rel_without_suffix.parts)

        path = self.source_file.replace("\\", "/")
        if "src/" in path:
            return path.split("src/")[1].replace("/", ".").replace(".py", "")
        return self.module_name

    def _build_path_loader_block(self, import_names: List[str]) -> List[str]:
        """Build a loader block for non-importable repository files."""
        separator = "# " + "-" * 75
        root_docstring = (
            '    """Locate the repository root from the generated test file."""'
        )
        root_marker_check = (
            '        if (candidate / "pyproject.toml").exists() '
            'and (candidate / "AGENTS.md").exists():'
        )
        spec_line = (
            "    spec = importlib.util.spec_from_file_location("
            f'"{self.module_name}_ut", module_path)'
        )
        lines = [
            "import importlib.util",
            "from pathlib import Path",
            "",
            "import pytest",
            "",
            separator,
            "# Module loading",
            separator,
            "",
        ]

        if self.relative_source_path is not None:
            lines.extend(
                [
                    "def _find_repo_root() -> Path:",
                    root_docstring,
                    "    current = Path(__file__).resolve().parent",
                    "    for candidate in (current, *current.parents):",
                    root_marker_check,
                    "            return candidate",
                    '    raise RuntimeError("Could not locate the repository root.")',
                    "",
                    "",
                ]
            )

        lines.extend(
            [
                "def _load_module():",
                f'    """Load `{self._display_source_path()}` as a testable module."""',
                f"    module_path = {self._build_module_path_expression()}",
                spec_line,
                "    assert spec and spec.loader",
                "    mod = importlib.util.module_from_spec(spec)",
                "    spec.loader.exec_module(mod)",
                "    return mod",
                "",
                "",
                "module_under_test = _load_module()",
            ]
        )

        for name in import_names:
            lines.append(f"{name} = module_under_test.{name}")

        lines.append("")
        return lines

    def _build_module_path_expression(self) -> str:
        """Render a portable Path expression for the source module."""
        rel = self.relative_source_path
        if rel is None:
            return f"Path({self.source_path.as_posix()!r})"

        expr = "_find_repo_root()"
        for part in rel.parts:
            expr += f" / {part!r}"
        return expr

    def _generate_class_tests(self, class_name: str) -> List[str]:
        """Generate test class for a source class."""
        lines = [
            f"class Test{class_name}:",
            f'    """Test suite for {class_name}."""',
            "",
            "    # Add fixtures as needed (app, client, db, etc.)",
            "",
            "    def test_class_is_importable(self):",
            f'        """Ensure {class_name} is importable and usable by tests."""',
            f"        assert {class_name} is not None",
            "",
        ]
        return lines

    def _generate_function_tests(self, functions: List[str]) -> List[str]:
        """Generate tests for module-level functions."""
        lines = [
            "class TestModuleFunctions:",
            '    """Test suite for module functions."""',
            "",
        ]

        for func_name in sorted(functions):
            lines.extend(
                [
                    f"    def test_{func_name}_is_callable(self):",
                    f'        """Smoke test: {func_name} is callable."""',
                    f"        assert callable({func_name})",
                    "",
                    f"    def test_{func_name}(self):",
                    f'        """TODO: implement behavior test for {func_name}."""',
                    "        # Arrange",
                    "        # (set up test data)",
                    "",
                    "        # Act",
                    f"        # result = {func_name}(...)",
                    "",
                    "        # Assert",
                    (
                        '        pytest.skip("TODO: add assertions '
                        'for this generated test")'
                    ),
                    "",
                ]
            )

        return lines


class TestDiscovery:
    """Find modules without test coverage."""

    EXCLUDED_DIRS = {
        ".git",
        ".pytest_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "instance",
        "logs",
        "node_modules",
        "playwright-report",
        "test_data",
        "vscode-live-locks",
    }

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = (repo_root or ROOT).resolve()
        self.test_dir = str(self.repo_root / "tests")

    def find_untested(self, src_dir: Optional[str] = None) -> List[str]:
        """Find Python modules without corresponding tests."""
        scan_path = Path(src_dir).resolve() if src_dir else self.repo_root

        if not scan_path.exists():
            return []

        if not _is_relative_to(scan_path, self.repo_root):
            return self._find_untested_external(scan_path)

        untested = []
        for source_path in self._iter_repo_source_files(scan_path):
            generator = TestGenerator(str(source_path), repo_root=self.repo_root)
            if not generator.get_test_file().exists():
                untested.append(source_path.relative_to(self.repo_root).as_posix())

        return sorted(untested)

    def _find_untested_external(self, src_dir: Path) -> List[str]:
        """Fallback scan mode for paths outside the repository root."""
        tested = self._get_tested_modules()
        untested = []

        for source_path in self._iter_python_files(src_dir):
            module = source_path.stem
            if module not in tested:
                untested.append(str(source_path))

        return sorted(untested)

    def _iter_repo_source_files(self, scan_path: Path) -> Iterable[Path]:
        """Yield repository Python files that should have tests."""
        if scan_path == self.repo_root:
            for child in sorted(self.repo_root.glob("*.py")):
                if self._is_candidate_source(child):
                    yield child

            for dirname in ("src", "scripts"):
                target = self.repo_root / dirname
                if target.exists():
                    yield from self._iter_python_files(target)
            return

        yield from self._iter_python_files(scan_path)

    def _iter_python_files(self, scan_path: Path) -> Iterable[Path]:
        """Yield candidate Python files under *scan_path*."""
        if scan_path.is_file():
            if self._is_candidate_source(scan_path):
                yield scan_path
            return

        for root, dirs, files in os.walk(scan_path):
            dirs[:] = [d for d in dirs if not self._should_skip_dir(Path(root) / d)]
            for filename in sorted(files):
                candidate = Path(root) / filename
                if self._is_candidate_source(candidate):
                    yield candidate.resolve()

    def _should_skip_dir(self, path: Path) -> bool:
        """Return True when a directory should be excluded from scanning."""
        name = path.name
        if name in self.EXCLUDED_DIRS or name.endswith(".egg-info"):
            return True
        if name.startswith(".") and name not in {".collab"}:
            return True
        return False

    def _is_candidate_source(self, path: Path) -> bool:
        """Return True when a Python file should be considered for test generation."""
        if path.suffix != ".py":
            return False
        if path.name == "__init__.py" or path.name.startswith(("test_", "_")):
            return False

        normalized_parts = set(path.parts)
        if "tests" in normalized_parts:
            return False

        return True

    def _get_tested_modules(self) -> Set[str]:
        """Get set of modules that have tests."""
        tested = set()
        for root, dirs, files in os.walk(self.test_dir):
            for file in files:
                if file.startswith("test_") and file.endswith(".py"):
                    module = file.replace("test_", "").replace(".py", "")
                    tested.add(module)
        return tested


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate pytest test stubs for Python code"
    )
    parser.add_argument("source_file", nargs="?", help="Path to source Python file")
    parser.add_argument(
        "--category",
        choices=["unit", "functional", "integration"],
        help="Test category (auto-detected if not specified)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print output without creating file"
    )
    parser.add_argument(
        "--output-root",
        help="Root directory for generated tests (default: tests/<category>)",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan the repository (or a provided directory) for untested modules",
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing test files"
    )

    args = parser.parse_args()

    # Scan mode
    if args.scan:
        scan_target = args.source_file
        scope_label = scan_target or "repository"
        print(f"\n📊 Untested modules in {scope_label}:\n")
        discovery = TestDiscovery()
        untested = discovery.find_untested(scan_target)

        if untested:
            for module in untested:
                print(f"  • {module}")
            print("\nRun: python scripts/generate_tests.py <module_path>\n")
        else:
            print("✅ All modules have tests!\n")
        return

    # Generate tests for specific file
    if not args.source_file:
        parser.print_help()
        sys.exit(1)

    if not os.path.exists(args.source_file):
        print(f"❌ File not found: {args.source_file}\n")
        sys.exit(1)

    source_path = Path(args.source_file).resolve()
    if source_path.is_dir():
        print("❌ Source path is a directory. Use --scan to inspect directories.\n")
        sys.exit(1)

    # Analyze
    analyzer = CodeAnalyzer(str(source_path))
    entities = analyzer.analyze()

    if not entities:
        print(f"⚠️  No testable entities found in {source_path}\n")
        return

    print(f"🔍 Found {len(entities)} testable entities\n")

    # Generate
    generator = TestGenerator(str(source_path), category=args.category)
    test_code = generator.generate(entities)

    # Determine output path (support custom output root)
    test_file = generator.get_test_file(
        output_root=Path(args.output_root).resolve() if args.output_root else None
    )
    test_dir = test_file.parent

    # Handle existing files
    if test_file.exists() and not args.force and not args.dry_run:
        print(f"⚠️  File exists: {test_file}")
        print("   Use --force to overwrite or --dry-run to preview\n")
        return

    # Dry-run mode
    if args.dry_run:
        print("📋 Generated test template:\n")
        print("=" * 80)
        print(test_code)
        print("=" * 80)
        print("\n✅ Preview mode - no files created\n")
        return

    # Create file
    test_dir.mkdir(parents=True, exist_ok=True)
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(test_code)

    print(f"✅ Created: {test_file}")
    print(f"📝 Next: Fill in test logic and run: pytest {test_file}\n")


if __name__ == "__main__":
    main()
