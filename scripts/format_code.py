#!/usr/bin/env python3
"""Code Formatting Script - Single Source of Truth for ALL formatting.

REQUIREMENT: For YAML formatting, you must have both 'prettier' and
'prettier-plugin-yaml' installed as dev dependencies:
    npm install --save-dev prettier prettier-plugin-yaml

Actively formats code using configured formatters:
- Whitespace: trailing whitespace removal, EOF newline normalization (ALL text files)
- Python: ruff (lint fixing + unsafe fixes), isort, black, docformatter
- JavaScript/CSS: prettier (quiet in format mode)
- Documentation: prettier (quiet in format mode, with prettier-plugin-yaml for YAML)
- Templates: djlint (Jinja2/HTML)

ARCHITECTURE:
    format_code.py  = the ONLY tool that MODIFIES files (formatter).
    pre-commit hooks = CHECK-ONLY gate (--check mode, never modify files).

Usage:
    python scripts/format_code.py              # Format everything
    python scripts/format_code.py --backend    # Python only
    python scripts/format_code.py --frontend   # JS + templates only
    python scripts/format_code.py --check      # Check only (pre-commit)
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from cleanup import clean_caches  # noqa: E402

# UTF-8 for Windows + ANSI colors (standardized - matches setup scripts)
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined,union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined,union-attr]

GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"
MAGENTA = "\033[95m"


class CodeFormatter:
    """Handles code formatting with colored, professional, numbered output."""

    # NOTE: .html/.htm are intentionally excluded from whitespace pass because
    # djlint can re-introduce trailing whitespace in template line wrapping.
    TEXT_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".css",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".env",
            ".md",
            ".txt",
            ".rst",
            ".sh",
            ".bash",
            ".ps1",
            ".bat",
            ".cmd",
            ".sql",
            ".xml",
            ".csv",
        }
    )

    def __init__(self, check_only: bool = False, files: Optional[list[str]] = None):
        self.check_only = check_only
        self.files = files
        self.root_dir = Path(__file__).parent.parent
        self.failed_tools: list[tuple[str, str, bool]] = []

    def _get_targets(
        self, extensions: tuple[str, ...], default: list[str]
    ) -> list[str]:
        if not self.files:
            return default
        return [f for f in self.files if f.lower().endswith(extensions)]

    def _prepare_env(self) -> dict:
        env = os.environ.copy()
        scripts_dir = "Scripts" if sys.platform == "win32" else "bin"
        venv_scripts = self.root_dir / ".venv" / scripts_dir
        if venv_scripts.exists():
            env["PATH"] = f"{venv_scripts}{os.pathsep}{env.get('PATH', '')}"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _get_python_executable(self) -> str:
        """Prefer repository .venv Python for module-based tool execution."""
        scripts_dir = "Scripts" if sys.platform == "win32" else "bin"
        python_name = "python.exe" if sys.platform == "win32" else "python"
        venv_python = self.root_dir / ".venv" / scripts_dir / python_name
        if venv_python.exists():
            return str(venv_python)
        return sys.executable

    def _exec(
        self, cmd: list[str], suppress_output: bool = False
    ) -> tuple[bool, Optional[subprocess.CompletedProcess]]:
        try:
            if sys.platform == "win32" and cmd[0] in ("npm", "npx"):
                cmd = ["cmd", "/c"] + cmd
            result = subprocess.run(
                cmd,
                cwd=self.root_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                env=self._prepare_env(),
            )
            if not suppress_output:
                printed = False
                if result.stdout.strip():
                    for line in result.stdout.strip().splitlines():
                        print(f"       {line}")
                    printed = True
                if result.stderr.strip():
                    for line in result.stderr.strip().splitlines():
                        print(f"       {line}", file=sys.stderr)
                    printed = True
                if result.returncode == 0 and not printed:
                    print("       All checks passed!")
            return result.returncode == 0, result
        except FileNotFoundError:
            if not suppress_output:
                print(f"       Tool not found: {cmd[0]}")
            return False, None
        except Exception as exc:
            if not suppress_output:
                print(f"       Error: {exc}")
            return False, None

    def _run_tool_step(
        self,
        description: str,
        fix_cmd: Optional[list[str]],
        check_cmd: Optional[list[str]],
        section: str,
        section_idx: int,
        section_total: int,
    ) -> bool:
        """Run one formatting tool step with fully standardized output."""
        step_header = f"[{section.upper()} {section_idx}/{section_total}] {description}"
        print(f"\n{CYAN}{step_header}...{RESET}")

        if self.check_only:
            cmd = check_cmd or fix_cmd
            assert cmd is not None, "At least one of fix_cmd or check_cmd required"
            print(f"   {MAGENTA}Command: {' '.join(cmd)}{RESET}")
            success, _ = self._exec(cmd)
            if success:
                print(f"   {GREEN}✅ {description} - SUCCESS{RESET}")
            else:
                print(f"   {RED}❌ {description} - ISSUES FOUND{RESET}")
                self.failed_tools.append((step_header, description, False))
            return success

        primary_cmd = fix_cmd if fix_cmd is not None else check_cmd
        assert primary_cmd is not None, "At least one of fix_cmd or check_cmd required"
        print(f"   {MAGENTA}Command: {' '.join(primary_cmd)}{RESET}")
        success, _ = self._exec(primary_cmd)

        if success:
            print(f"   {GREEN}✅ {description} - SUCCESS{RESET}")
            return True

        print(f"   {RED}❌ {description} - ISSUES FOUND{RESET}")

        if check_cmd:
            print(f"\n   {MAGENTA}Command: {' '.join(check_cmd)}{RESET}")
            check_ok, _ = self._exec(check_cmd, suppress_output=True)
            if check_ok:
                print(
                    f"   {GREEN}✅ {description} (check) - All issues fixed - "
                    f"no further action needed.{RESET}"
                )
            else:
                print(
                    f"   {RED}❌ {description} (check) - Issues remain - "
                    f"manual fix required.{RESET}"
                )
                self.failed_tools.append((step_header, description, True))
            return check_ok

        self.failed_tools.append((step_header, description, False))
        return False

    def normalize_whitespace(self) -> bool:
        print("\n" + "=" * 80)
        print(f"{BOLD}WHITESPACE & EOF NORMALIZATION{RESET}")
        print("=" * 80)

        if self.files:
            tracked_files = self.files
        else:
            try:
                proc = subprocess.run(
                    ["git", "ls-files"],
                    cwd=self.root_dir,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=True,
                )
                tracked_files = [f for f in proc.stdout.strip().split("\n") if f]
            except (subprocess.CalledProcessError, FileNotFoundError):
                print("   ⚠️  Could not list git files - skipping whitespace")
                return True

        issues: list[str] = []
        fix = not self.check_only

        for rel_path in tracked_files:
            filepath = self.root_dir / rel_path
            if (
                filepath.suffix.lower() not in self.TEXT_EXTENSIONS
                or not filepath.is_file()
            ):
                continue

            try:
                raw = filepath.read_bytes()
            except OSError:
                continue
            if not raw or b"\x00" in raw:
                continue

            fixed = self._normalize_whitespace(raw)
            if fixed != raw:
                issues.append(rel_path)
                if fix:
                    filepath.write_bytes(fixed)

        if not issues:
            print(f"   {GREEN}✅ Whitespace & EOF - all files clean{RESET}")
            return True

        if fix:
            print(f"   {GREEN}✅ Fixed whitespace/EOF in {len(issues)} file(s){RESET}")
            return True

        print(f"   {RED}❌ {len(issues)} file(s) have whitespace/EOF issues{RESET}")
        for rel_file in issues[:10]:
            print(f"      - {rel_file}")
        if len(issues) > 10:
            print(f"      ... and {len(issues) - 10} more")
        return False

    @staticmethod
    def _normalize_whitespace(content: bytes) -> bytes:
        uses_crlf = b"\r\n" in content
        normalized = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        lines = normalized.split(b"\n")
        stripped = [line.rstrip(b" \t") for line in lines]
        result = b"\n".join(stripped).rstrip(b"\n") + b"\n"
        if uses_crlf:
            result = result.replace(b"\n", b"\r\n")
        return result

    def format_python(self) -> bool:
        targets = self._get_targets(
            (".py",),
            [
                "src",
                "tests",
                "scripts",
            ],
        )
        if not targets:
            return True

        print("\n" + "=" * 80)
        print(f"{BOLD}BACKEND CODE FORMATTING{RESET}")
        print("=" * 80)

        flake8_exclude = (
            "--exclude="
            ".venv,node_modules,__pycache__,.git,.pytest_cache,"
            "htmlcov,playwright-report"
        )
        flake8_opts = [
            flake8_exclude,
            "--count",
            "--show-source",
            "--statistics",
            "--max-line-length=88",
        ]

        steps: list[tuple[str, Optional[list[str]], list[str]]] = [
            (
                "Import sorting (isort)",
                ["isort"] + targets,
                ["isort"] + targets + ["--check-only"],
            ),
            (
                "Code formatting (black)",
                ["black"] + targets,
                ["black", "--check"] + targets,
            ),
            (
                "Docstring formatting (docformatter)",
                ["docformatter", "--in-place", "-r"] + targets,
                ["docformatter", "--check", "-r"] + targets,
            ),
            (
                "Ruff linting & fixing",
                ["ruff", "check", "--no-cache"] + targets + ["--fix", "--unsafe-fixes"],
                ["ruff", "check", "--no-cache"] + targets,
            ),
            (
                "Final linting (flake8)",
                None,
                ["flake8"] + targets + flake8_opts,
            ),
        ]

        all_passed = True
        for idx, (desc, fix_cmd, check_cmd) in enumerate(steps, 1):
            all_passed &= self._run_tool_step(
                desc, fix_cmd, check_cmd, "BACKEND", idx, len(steps)
            )
        return all_passed

    def _check_prettier(self) -> bool:
        npm_cmd = ["cmd", "/c", "npm"] if sys.platform == "win32" else ["npm"]
        result = subprocess.run(
            npm_cmd + ["list", "prettier"],
            cwd=self.root_dir,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return False

        plugin_result = subprocess.run(
            npm_cmd + ["list", "prettier-plugin-yaml"],
            cwd=self.root_dir,
            capture_output=True,
            check=False,
        )
        if plugin_result.returncode != 0:
            print(
                "   ⚠️  prettier-plugin-yaml not installed - YAML files will NOT be "
                "formatted!\n"
                "      Run: npm install --save-dev prettier-plugin-yaml"
            )
            return False
        return True

    def _filter_glob_targets(self, patterns: list[str]) -> list[str]:
        return [pattern for pattern in patterns if list(self.root_dir.glob(pattern))]

    def format_frontend(self) -> bool:
        base_targets = self._filter_glob_targets(
            [
                "src/**/*.js",
                "src/**/*.css",
                "tests/**/*.js",
            ]
        )

        targets = self._get_targets(
            (".js", ".jsx", ".ts", ".tsx", ".css", ".scss"),
            base_targets,
        )
        if not targets:
            return True

        print("\n" + "=" * 80)
        print(f"{BOLD}FRONTEND CODE FORMATTING{RESET}")
        print("=" * 80)

        if not self._check_prettier():
            print("   ℹ️  Prettier not installed - skipping frontend")
            return True

        return self._run_tool_step(
            "JavaScript/CSS (prettier)",
            ["npx", "prettier", "--write", "--log-level", "silent"] + targets,
            ["npx", "prettier", "--check"] + targets,
            "FRONTEND",
            1,
            1,
        )

    def format_docs(self) -> bool:
        doc_targets = self._get_targets(
            (".md", ".json"),
            self._filter_glob_targets(
                [
                    "docs/**/*.md",
                    "*.md",
                    "*.json",
                    ".github/**/*.md",
                    "tests/**/*.md",
                    ".agents/**/*.md",
                ]
            ),
        )
        if not doc_targets:
            return True

        print("\n" + "=" * 80)
        print(f"{BOLD}DOCUMENTATION FORMATTING{RESET}")
        print("=" * 80)

        return self._run_tool_step(
            "Markdown/JSON (prettier)",
            ["npx", "prettier", "--write", "--log-level", "silent"] + doc_targets,
            ["npx", "prettier", "--check"] + doc_targets,
            "DOCS",
            1,
            1,
        )

    def format_yaml(self) -> bool:
        exclude_dirs = {".venv", "node_modules", ".git", "__pycache__"}
        yaml_files = []
        for ext in ("*.yaml", "*.yml"):
            for path in self.root_dir.rglob(ext):
                if not any(part in exclude_dirs for part in path.parts):
                    yaml_files.append(str(path))
        if not yaml_files:
            return True

        print("\n" + "=" * 80)
        print(f"{BOLD}YAML FORMATTING & LINTING{RESET}")
        print("=" * 80)

        all_passed = True
        all_passed &= self._run_tool_step(
            "YAML (prettier)",
            ["npx", "prettier", "--write", "--log-level", "silent"] + yaml_files,
            ["npx", "prettier", "--check"] + yaml_files,
            "YAML",
            1,
            2,
        )
        all_passed &= self._run_tool_step(
            "YAML (yamllint)",
            None,
            ["yamllint", "--strict"] + yaml_files,
            "YAML",
            2,
            2,
        )
        return all_passed

    def format_templates(self) -> bool:
        template_dirs = ["src/dashboard"]
        targets = self._get_targets((".html", ".htm"), template_dirs)
        if not targets:
            return True

        python = self._get_python_executable()
        djlint_check, _ = self._exec([python, "-m", "djlint", "--version"], True)
        if not djlint_check:
            print("\n" + "=" * 80)
            print(f"{BOLD}JINJA2 TEMPLATE FORMATTING{RESET}")
            print("=" * 80)
            print("   ℹ️  djlint not installed - skipping template formatting")
            return True

        print("\n" + "=" * 80)
        print(f"{BOLD}JINJA2 TEMPLATE FORMATTING{RESET}")
        print("=" * 80)

        description = "Jinja2 templates (djlint)"
        step_header = "[TEMPLATES 1/1] Jinja2 templates (djlint)"
        fix_cmd = [python, "-m", "djlint"] + targets + ["--reformat", "--quiet"]
        check_cmd = [python, "-m", "djlint"] + targets + ["--check"]

        print(f"\n{CYAN}{step_header}...{RESET}")
        print(f"   {MAGENTA}Command: {' '.join(fix_cmd)}{RESET}")

        fix_ok, _ = self._exec(fix_cmd, suppress_output=True)
        if fix_ok:
            print("       All checks passed!")
            print(f"   {GREEN}✅ {description} - SUCCESS{RESET}")
            return True

        print(
            f"   {CYAN}ℹ️  {description} - changes applied; "
            f"running verification check...{RESET}"
        )
        print(f"\n   {MAGENTA}Command: {' '.join(check_cmd)}{RESET}")
        check_ok, _ = self._exec(check_cmd, suppress_output=True)

        if check_ok:
            print(
                f"   {GREEN}✅ {description} - All issues fixed - "
                f"no further action needed.{RESET}"
            )
            return True

        print(
            f"   {RED}❌ {description} (check) - Issues remain - "
            f"manual fix required.{RESET}"
        )
        self._exec(check_cmd, suppress_output=False)
        self.failed_tools.append((step_header, description, True))
        return False

    def print_summary(self) -> None:
        print("\n" + "=" * 80)
        print(f"{BOLD}FORMATTING SUMMARY{RESET}")
        print("=" * 80)
        if not self.failed_tools:
            mode = "check" if self.check_only else "formatting"
            print(f"   {GREEN}✅ All {mode} operations completed successfully!{RESET}")
            return

        print(f"   {RED}❌ {len(self.failed_tools)} operation(s) failed{RESET}")
        for step_header, _, _ in self.failed_tools:
            print(f"      - {step_header}")
        print(f"\n   {RED}⚠️  Review the errors above and fix manually.{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Format code using configured formatters"
    )
    parser.add_argument("--backend", action="store_true", help="Python only")
    parser.add_argument(
        "--frontend", action="store_true", help="JS/CSS + templates only"
    )
    parser.add_argument("--docs", action="store_true", help="Markdown/JSON only")
    parser.add_argument("--check", action="store_true", help="Check only (pre-commit)")
    parser.add_argument(
        "--update-hooks", action="store_true", help="pre-commit autoupdate"
    )
    parser.add_argument("files", nargs="*", help="Specific files to format")

    args = parser.parse_args()

    run_all = not (args.backend or args.frontend or args.docs)
    format_backend = args.backend or run_all
    format_frontend = args.frontend or run_all
    format_docs = args.docs or run_all

    formatter = CodeFormatter(
        check_only=args.check, files=args.files if args.files else None
    )

    all_passed = True
    all_passed &= formatter.normalize_whitespace()

    if format_backend:
        all_passed &= formatter.format_python()

    if format_frontend:
        all_passed &= formatter.format_frontend()
        all_passed &= formatter.format_templates()

    if format_docs:
        all_passed &= formatter.format_docs()

    all_passed &= formatter.format_yaml()

    formatter.print_summary()

    print("\n" + "=" * 80)
    print(f"{BOLD}CLEANUP{RESET}")
    print("=" * 80)
    count = clean_caches(dry_run=False)
    if count:
        print(f"   {GREEN}✅ Removed {count} cache artifact(s){RESET}")
    else:
        print(f"   ✨ Repo already clean{RESET}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
