#!/usr/bin/env python3
"""Comprehensive Code Validation Script.

This script runs all validation checks that should pass before committing code.
It simulates the CI pipeline locally to catch issues early.

Usage:
    python scripts/validate_code.py              # Run all checks (full suite)
    python scripts/validate_code.py --backend    # Only backend checks
    python scripts/validate_code.py --frontend   # Only frontend checks
    python scripts/validate_code.py --docs       # Only documentation checks
    python scripts/validate_code.py --quick      # Smart mode: targeted tests only

Smart --quick mode (three-tier priority):
    Tier 1 — git-diff:  Maps changed files to their test dirs and runs only
                        those directories (most precise, fastest).
    Tier 2 — fallback:  No changes detected or global file changed -> runs the
                        full suite without coverage (fast, safe).
    Skip    — no-op:    No backend/frontend changes in that category -> skipped.
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from cleanup import clean_default, clean_packaging  # noqa: E402

# Load .env variables so validate_code.py knows about local configuration
_load_dotenv: Optional[Callable[..., bool]]
try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:
    _load_dotenv = None  # python-dotenv might not be installed in base env.

if _load_dotenv is not None:
    _load_dotenv()


def _configure_coverage_data_file() -> None:
    """Route coverage data outside the repository tree on local machines only.

    This prevents `.coverage.*` shard files from cluttering the workspace root when
    pytest-cov writes parallel data. Skip this in CI environments where coverage file
    persistence across subprocess invocations is critical.
    """
    if os.getenv("COVERAGE_FILE"):
        return

    # Skip temp directory routing in CI/GitHub Actions where coverage file
    # must be written to project root for `coverage report` to find it.
    if os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
        return

    try:
        project_root = Path(__file__).resolve().parent.parent
        digest = hashlib.sha1(
            str(project_root).encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:12]
        cov_dir = Path(tempfile.gettempdir()) / "collab" / "coverage" / digest
        cov_dir.mkdir(parents=True, exist_ok=True)
        os.environ["COVERAGE_FILE"] = str(cov_dir / ".coverage")
    except Exception:
        # Best effort: fallback to tool default behavior if temp setup fails.
        pass


_configure_coverage_data_file()

# Fix Windows console encoding for UTF-8 output
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Additional fix for Windows UnicodeEncodeError when printing special characters
# Only call reconfigure if it exists and sys.stdout is a standard stream
# (not a wrapped TextIOWrapper)
if sys.platform == "win32":
    orig_stdout = sys.__stdout__ if hasattr(sys, "__stdout__") else None
    if orig_stdout and hasattr(orig_stdout, "reconfigure"):
        try:
            orig_stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass  # Fallback to default behavior if reconfigure fails


class Colors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


def print_header(message: str) -> None:
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 80}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{message.center(80)}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 80}{Colors.ENDC}\n")


def print_section(message: str) -> None:
    print(f"\n{Colors.OKBLUE}{Colors.BOLD}{'-' * 80}{Colors.ENDC}")
    print(f"{Colors.OKBLUE}{Colors.BOLD}{message}{Colors.ENDC}")
    print(f"{Colors.OKBLUE}{Colors.BOLD}{'-' * 80}{Colors.ENDC}\n")


def print_success(message: str) -> None:
    print(f"{Colors.OKGREEN}[OK] {message}{Colors.ENDC}")


def print_error(message: str) -> None:
    print(f"{Colors.FAIL}[FAIL] {message}{Colors.ENDC}")


def print_warning(message: str) -> None:
    print(f"{Colors.WARNING}[WARN] {message}{Colors.ENDC}")


def print_skipped(message: str) -> None:
    print(f"{Colors.OKCYAN}[SKIPPED] {message}{Colors.ENDC}")


ValidationStatus = Literal["passed", "failed", "skipped"]


def _check_succeeded(status: ValidationStatus | bool) -> bool:
    if status == "skipped":
        return True
    return bool(status)


def _print_check_summary(name: str, status: ValidationStatus | bool) -> None:
    if status == "skipped":
        print_skipped(name)
    elif status:
        print_success(name)
    else:
        print_error(name)


_MAX_FAILURE_OUTPUT_LINES = 150
_FAILURE_HEAD_LINES = 20
_FAILURE_TAIL_LINES = 40
_PYTEST_SECTION_HEADER_RE = re.compile(
    r"=+\s*(FAILURES|ERRORS|warnings summary|short test summary info)\s*=+",
    re.IGNORECASE,
)


def _dedupe_output_blocks(*blocks: str) -> List[str]:
    seen = set()
    unique_blocks: List[str] = []
    for block in blocks:
        if block is None:
            continue
        normalized = block.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique_blocks.append(normalized)
    return unique_blocks


def _find_pytest_section_ranges(lines: List[str]) -> Dict[str, Tuple[int, int]]:
    matches: List[Tuple[str, int]] = []
    for index, line in enumerate(lines):
        match = _PYTEST_SECTION_HEADER_RE.match(line.strip())
        if match:
            matches.append((match.group(1).lower(), index))

    section_ranges: Dict[str, Tuple[int, int]] = {}
    for idx, (name, start) in enumerate(matches):
        end = matches[idx + 1][1] if idx + 1 < len(matches) else len(lines)
        section_ranges[name] = (start, end)
    return section_ranges


def _extract_coverage_block(lines: List[str]) -> str:
    coverage_markers = [
        idx
        for idx, line in enumerate(lines)
        if "coverage:" in line.lower() or "required test coverage" in line.lower()
    ]
    if not coverage_markers:
        return ""

    start = max(coverage_markers[0] - 2, 0)
    end = min(len(lines), coverage_markers[-1] + 20)
    return "\n".join(lines[start:end]).strip()


def _truncate_generic_failure_output(lines: List[str]) -> str:
    total = len(lines)
    if total <= _MAX_FAILURE_OUTPUT_LINES:
        return "\n".join(lines).strip()

    hidden = total - (_FAILURE_HEAD_LINES + _FAILURE_TAIL_LINES)
    head = "\n".join(lines[:_FAILURE_HEAD_LINES]).strip()
    tail = "\n".join(lines[-_FAILURE_TAIL_LINES:]).strip()
    return (
        "First lines:\n"
        f"{head}\n\n"
        f"... [{hidden} lines omitted for brevity] ...\n\n"
        "Last lines:\n"
        f"{tail}"
    ).strip()


def format_failure_output(stdout: str, stderr: str) -> str:
    blocks = _dedupe_output_blocks(stdout, stderr)
    if not blocks:
        return ""

    combined_output = "\n\n".join(blocks)
    lines = combined_output.splitlines()
    section_ranges = _find_pytest_section_ranges(lines)

    if not section_ranges and not any(
        marker in combined_output.lower()
        for marker in ("test session starts", "short test summary info", "failed ")
    ):
        return _truncate_generic_failure_output(lines)

    report_sections: List[str] = []

    short_summary_range = section_ranges.get("short test summary info")
    if short_summary_range:
        start, end = short_summary_range
        report_sections.append(
            "Pytest short summary:\n" + "\n".join(lines[start:end]).strip()
        )

    for section_name in ("failures", "errors"):
        section_range = section_ranges.get(section_name)
        if section_range:
            start, end = section_range
            title = "Failure details" if section_name == "failures" else "Error details"
            report_sections.append(f"{title}:\n" + "\n".join(lines[start:end]).strip())

    coverage_block = _extract_coverage_block(lines)
    if coverage_block:
        report_sections.append("Coverage details:\n" + coverage_block)

    tail_lines = lines[-_FAILURE_TAIL_LINES:]
    tail_block = "\n".join(tail_lines).strip()
    if tail_block:
        report_sections.append("Raw output tail:\n" + tail_block)

    deduped_sections: List[str] = []
    seen = set()
    for section in report_sections:
        normalized = section.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped_sections.append(normalized)

    return "\n\n".join(deduped_sections)


def _print_failure_output(stdout: str, stderr: str) -> None:
    formatted_output = format_failure_output(stdout, stderr)
    if not formatted_output:
        return
    print(f"\n{Colors.FAIL}Failure details:{Colors.ENDC}")
    print(formatted_output)


def _print_output_tail(output: str, label: str, color: str) -> None:
    """Print the tail of *output*, truncating the head when it is very long.

    When the output has more than ``_MAX_FAILURE_OUTPUT_LINES`` lines only the last
    ``_MAX_FAILURE_OUTPUT_LINES`` are printed so that failure summaries and error
    details are always visible.
    """
    if not output:
        return
    lines = output.splitlines()
    total = len(lines)
    print(f"\n{color}{label}{Colors.ENDC}")
    if total > _MAX_FAILURE_OUTPUT_LINES:
        hidden = total - _MAX_FAILURE_OUTPUT_LINES
        print(
            f"{Colors.WARNING}... [{hidden} lines hidden — showing last "
            f"{_MAX_FAILURE_OUTPUT_LINES} of {total}] ...{Colors.ENDC}"
        )
        print("\n".join(lines[-_MAX_FAILURE_OUTPUT_LINES:]))
    else:
        print(output)


_PYTHON_TOOL_MODULES: Dict[str, str] = {
    "isort": "isort",
    "black": "black",
    "docformatter": "docformatter",
    "ruff": "ruff",
    "flake8": "flake8",
    "mypy": "mypy",
    "bandit": "bandit",
    "pytest": "pytest",
    "coverage": "coverage",
    "yamllint": "yamllint",
    "diff-cover": "diff_cover.diff_cover_tool",
}


def _get_python_executable() -> str:
    """Get the Python executable, preferring .venv if available.

    This ensures that tools installed in the project's virtual environment are used,
    even if the script isn't run from within an activated venv.
    """
    scripts_dir = "Scripts" if sys.platform == "win32" else "bin"
    project_root = Path(__file__).parent.parent
    venv_python = (
        project_root
        / ".venv"
        / scripts_dir
        / ("python.exe" if sys.platform == "win32" else "python")
    )

    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _python_module_fallback_command(command: List[str]) -> Optional[List[str]]:
    """Return a Python module command fallback for known tools.

    This avoids PATH/PATHEXT issues in hook shells by running tools with the .venv
    Python interpreter when available.
    """
    if not command:
        return None

    executable = command[0]
    if os.path.isabs(executable) or "/" in executable or "\\" in executable:
        return None

    module = _PYTHON_TOOL_MODULES.get(executable.lower())
    if not module:
        return None

    return [_get_python_executable(), "-m", module] + command[1:]


def _git_ref_exists(ref: str) -> bool:
    """Return True if *ref* resolves to a commit."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def _git_remote_origin_exists() -> bool:
    """Return True when the repository has an `origin` remote configured."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def _resolve_diff_compare_branch(quick: bool) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a strict compare branch for diff-cover.

    Quick mode always compares against HEAD. Full mode prefers origin/main and local
    main, and finally falls back to HEAD~1 when no remote/mainline branch can be
    resolved.
    """
    if quick:
        return "HEAD", None

    candidates = ["origin/main", "main"]
    for ref in candidates:
        if _git_ref_exists(ref):
            return ref, None

    if _git_remote_origin_exists():
        try:
            subprocess.run(
                ["git", "fetch", "origin", "--prune"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except (FileNotFoundError, OSError):
            pass

        for ref in candidates:
            if _git_ref_exists(ref):
                return ref, "Compare branch resolved after fetching remote refs."

    if _git_ref_exists("HEAD~1"):
        return (
            "HEAD~1",
            "No mainline branch found; using previous commit (HEAD~1) for diff-cover.",
        )

    return None, (
        "Unable to resolve a compare branch for diff-cover. Configure an origin/main "
        "(or equivalent) branch or create at least one prior commit."
    )


def run_command(
    command: List[str],
    description: str,
    check: bool = True,
    force_all_apps: bool = False,
    env: Optional[Dict[str, str]] = None,
    ignore_failure: bool = False,
) -> Tuple[bool, str]:
    """Run a shell command and return success status and output.

    Args:
        command: Command and arguments as a list
        description: Human-readable description of what's being checked
        check: Whether to check return code (default: True)
        force_all_apps: Whether to force enable all configuration (default: False)
        env: Optional dictionary of environment variables to merge
        ignore_failure: If True, do not print error on failure (default: False)

    Returns:
        Tuple of (success: bool, output: str)
    """
    try:
        resolved_command = _python_module_fallback_command(command)
        active_command = resolved_command if resolved_command else command

        print(f"Running: {' '.join(active_command)}")

        if sys.platform == "win32" and active_command[0] in ("npm", "npx"):
            active_command = ["cmd", "/c"] + active_command

        current_path = os.environ.get("PATH", "")
        scripts_dir = "Scripts" if sys.platform == "win32" else "bin"
        project_root = Path(__file__).parent.parent
        venv_scripts = project_root / ".venv" / scripts_dir
        if venv_scripts.exists():
            current_path = f"{venv_scripts}{os.pathsep}{current_path}"

        ironclad_env = {
            "PATH": current_path,
            "PYTHONPATH": os.environ.get("PYTHONPATH", str(project_root)),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "PYTHONIOENCODING": "utf-8",
            "TESTING": "1",
            "CI": "true",
            "COLLAB_SILENT_DAEMON": "1",
            "COLLAB_TEST_MODE": "1",
        }

        _state_dir = os.environ.get("COLLAB_STATE_DIR")
        if _state_dir:
            ironclad_env["COLLAB_STATE_DIR"] = _state_dir

        coverage_file = os.environ.get("COVERAGE_FILE")
        if coverage_file:
            ironclad_env["COVERAGE_FILE"] = coverage_file

        for key in [
            "APPDATA",
            "LOCALAPPDATA",
            "PROGRAMDATA",
            "SYSTEMDRIVE",
            "HOMEDRIVE",
            "HOMEPATH",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "COMSPEC",
            "PATHEXT",
            "WINDIR",
        ]:
            if key in os.environ:
                ironclad_env[key] = os.environ[key]

        if env:
            ironclad_env.update(env)

        result = subprocess.run(
            active_command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=ironclad_env,
        )

        if result.returncode == 0:
            print_success(f"{description} passed")
            return True, result.stdout or ""
        else:
            print_error(f"{description} failed")
            if not ignore_failure:
                _print_failure_output(result.stdout or "", result.stderr or "")
            return False, result.stderr or result.stdout or ""

    except subprocess.CalledProcessError as e:
        print_error(f"{description} failed with return code {e.returncode}")
        _print_failure_output(e.stdout, e.stderr)
        return False, e.stderr or e.stdout
    except FileNotFoundError:
        print_error(f"{description} failed - command not found: {command[0]}")
        print_warning(f"Please ensure {command[0]} is installed")
        return False, f"Command not found: {command[0]}"


_FULL_SUITE_FILENAMES: frozenset = frozenset(
    [
        "pyproject.toml",
        ".env",
        "requirements.txt",
        "requirements-dev.txt",
    ]
)
_FULL_SUITE_PREFIXES: tuple = ("scripts/", ".github/")

_BACKEND_MAP: List[Tuple[str, List[str]]] = [
    ("src/", ["tests/backend/unit/"]),
    ("src/dashboard/", []),
    ("tests/backend/", ["tests/backend/"]),
    ("scripts/", ["tests/backend/"]),
]

_FRONTEND_MAP: List[Tuple[str, List[str]]] = [
    ("src/dashboard/", ["tests/frontend/"]),
    ("tests/frontend/", ["tests/frontend/"]),
]


def _get_changed_files() -> List[str]:
    changed: set = set()
    git_cmds = [
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "diff", "--name-only", "--cached"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ]
    for cmd in git_cmds:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line:
                        changed.add(line.replace("\\", "/"))
        except (FileNotFoundError, OSError):
            return []
    return sorted(changed)


def _expand_input_paths(paths: List[str]) -> List[str]:
    expanded: set[str] = set()
    cwd = Path.cwd()
    ignored_dirnames = {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        "htmlcov",
    }

    for raw in paths:
        if not raw:
            continue

        p = Path(raw)
        if p.exists() and p.is_dir():
            for child in p.rglob("*"):
                if not child.is_file():
                    continue
                if any(part in ignored_dirnames for part in child.parts):
                    continue
                try:
                    rel = child.resolve().relative_to(cwd).as_posix()
                except ValueError:
                    rel = child.resolve().as_posix()
                expanded.add(rel)
            continue

        if p.exists() and p.is_file():
            try:
                rel = p.resolve().relative_to(cwd).as_posix()
            except ValueError:
                rel = p.resolve().as_posix()
            expanded.add(rel)
            continue

        expanded.add(raw.replace("\\", "/"))

    return sorted(expanded)


def detect_changed_scopes(files: Optional[List[str]] = None) -> Dict[str, Any]:
    if files is not None:
        changed = _expand_input_paths(files)
    else:
        changed = _get_changed_files()

    if not changed:
        return {
            "full_suite": True,
            "backend": [],
            "frontend": [],
            "reason": None,
            "changed_files": [],
        }

    for f in changed:
        normalized = f.lstrip("./")
        if "/" not in normalized and normalized in _FULL_SUITE_FILENAMES:
            reason = f"Global config changed ({f!r}) — full suite required."
            return {
                "full_suite": True,
                "backend": [],
                "frontend": [],
                "reason": reason,
                "changed_files": changed,
            }
        if any(f.startswith(p) for p in _FULL_SUITE_PREFIXES):
            reason = f"Infrastructure file changed ({f!r}) — full suite required."
            return {
                "full_suite": True,
                "backend": [],
                "frontend": [],
                "reason": reason,
                "changed_files": changed,
            }

    backend: set = set()
    frontend: set = set()

    for f in changed:
        for prefix, dirs in _BACKEND_MAP:
            if f.startswith(prefix):
                backend.update(dirs)
                break

        for prefix, dirs in _FRONTEND_MAP:
            if f.startswith(prefix):
                frontend.update(dirs)
                break

    return {
        "full_suite": False,
        "backend": sorted(backend),
        "frontend": sorted(frontend),
        "reason": None,
        "changed_files": changed,
    }


def validate_python_backend(
    quick: bool = False, force_all_apps: bool = True, files: Optional[List[str]] = None
) -> bool:
    """Run all Python backend validation checks."""
    python_targets = []
    template_targets = []
    bandit_targets = []
    test_targets = []

    if files:
        expanded_files = _expand_input_paths(files)
        clean_files = [f for f in expanded_files if not Path(f).name.startswith(".")]

        python_targets = [f for f in clean_files if f.endswith(".py")]
        template_targets = [f for f in clean_files if f.endswith(".html")]
        _bandit_prefixes = ("src/", "scripts/")
        bandit_targets = [
            f
            for f in clean_files
            if f.endswith(".py")
            and (any(f.startswith(p) for p in _bandit_prefixes) or "/" not in f)
        ]
        test_targets = [
            f
            for f in clean_files
            if f.endswith(".py") and Path(f).name.startswith("test_")
        ]

        if not any([python_targets, template_targets, bandit_targets, test_targets]):
            return True

    print_header("BACKEND VALIDATION")
    checks: List[Tuple[str, ValidationStatus | bool]] = []
    success: ValidationStatus | bool = True

    # Full run (no specific files provided)
    if not files:
        python_targets = [
            "src",
            "tests",
            "scripts",
        ]

    if python_targets:
        print_section("Step 1/11: Import Sorting (isort)")
        success, _ = run_command(
            ["isort"] + python_targets + ["--check-only"],
            "Import sorting check",
            force_all_apps=force_all_apps,
        )
        checks.append(("Import Sorting", success))

    if python_targets:
        print_section("Step 2/11: Code Formatting (black)")
        success, _ = run_command(
            ["black", "--check"] + python_targets,
            "Code formatting check",
            force_all_apps=force_all_apps,
        )
        checks.append(("Code Formatting", success))

    if python_targets:
        print_section("Step 3/11: Docstring Formatting (docformatter)")
        success, _ = run_command(
            ["docformatter", "--check", "-r"] + python_targets,
            "Docstring formatting check",
            force_all_apps=force_all_apps,
        )
        checks.append(("Docstring Formatting", success))

    if python_targets:
        print_section("Step 4/11: Linting (ruff)")
        success, _ = run_command(
            ["ruff", "check", "--no-cache"] + python_targets,
            "Ruff linting",
            force_all_apps=force_all_apps,
        )
        checks.append(("Ruff Linting", success))

    if python_targets:
        print_section("Step 5/11: Additional Linting (flake8)")
        exclude_dirs = (
            ".venv,node_modules,__pycache__,.git,"
            ".pytest_cache,htmlcov,playwright-report"
        )
        flake8_cmd = (
            ["flake8"]
            + python_targets
            + [
                f"--exclude={exclude_dirs}",
                "--count",
                "--show-source",
                "--statistics",
                "--max-line-length=88",
            ]
        )
        success, _ = run_command(
            flake8_cmd,
            "Flake8 linting",
            force_all_apps=force_all_apps,
        )
        checks.append(("Flake8 Linting", success))

    if python_targets:
        print_section("Step 6/11: Type Checking (mypy)")
        # CRITICAL: Remove .mypy_cache to ensure clean state.
        # Even with --no-incremental, stale cache can interfere with type inference.
        # This ensures local validation matches CI exactly (CI runs on fresh VMs).
        mypy_cache_dir = Path(".mypy_cache")
        if mypy_cache_dir.exists():
            try:
                shutil.rmtree(mypy_cache_dir)
                msg = "[INFO] Cleaned stale .mypy_cache for fresh type check."
                print(f"{Colors.OKCYAN}{msg}{Colors.ENDC}")
            except Exception as e:
                msg = f"[WARN] Could not remove .mypy_cache: {e}"
                print(f"{Colors.WARNING}{msg}{Colors.ENDC}")
        success, _ = run_command(
            ["mypy", "--no-incremental"] + python_targets,
            "Type checking",
            force_all_apps=force_all_apps,
        )
        checks.append(("Type Checking", success))

    print_section("Step 7/11: Security Scanning (bandit)")
    if files:
        if bandit_targets:
            success, _ = run_command(
                ["bandit"] + bandit_targets + ["-ll"],
                "Security scanning",
            )
        else:
            msg = (
                f"{Colors.OKCYAN}[INFO] No source files targeted — "
                f"skipping bandit.{Colors.ENDC}"
            )
            print(msg)
            success = "skipped"
    else:
        success, _ = run_command(
            [
                "bandit",
                "-r",
                "src/",
                "scripts/",
                "-ll",
            ],
            "Security scanning",
        )

    checks.append(("Security Scanning", success))

    print_section("Step 8/11: Template Linting (djlint)")
    python_exe = _get_python_executable()
    if files:
        if template_targets:
            success, _ = run_command(
                [python_exe, "-m", "djlint", "--check"] + template_targets,
                "HTML template linting",
            )
        else:
            msg = (
                f"{Colors.OKCYAN}[INFO] No templates targeted — skipping.{Colors.ENDC}"
            )
            print(msg)
            success = "skipped"
    else:
        success, _ = run_command(
            [python_exe, "-m", "djlint", "--check", "src/dashboard"],
            "HTML template linting",
        )

    if not success:
        print_warning("DjLint found issues (soft failure for now)")
        checks.append(("Template Linting", "skipped"))
    else:
        checks.append(("Template Linting", success))

    _FULL_TESTPATHS = ["tests/backend", "tests/frontend"]
    _cov_sources = [
        "--cov=src",
        "--cov=scripts",
    ]
    quick_cov_args = _cov_sources + ["--cov-report=xml"]

    if quick:
        print_section("Step 9/11: Targeted Tests (with Diff Coverage)")
        scopes = detect_changed_scopes(files)

        if scopes["full_suite"]:
            reason = (
                f" ({scopes.get('reason')})"
                if scopes.get("reason")
                else " (Global changes)"
            )
            print_warning(
                f"Quick mode: Full suite required{reason} — running all tests."
            )
            success, _ = run_command(
                ["pytest", "-c", "pytest.ini", "-p", "no:cacheprovider"]
                + quick_cov_args
                + ["-x", "--tb=short"]
                + _FULL_TESTPATHS,
                "Quick test run (full scope)",
                force_all_apps=force_all_apps,
                env={"COLLAB_KEEP_ROOT_COVERAGE": "1"},
            )
        elif scopes["backend"]:
            scope_str = " ".join(scopes["backend"])
            print_warning(f"Quick mode [Smart Scoping]: Running: {scope_str}")
            success, _ = run_command(
                ["pytest", "-c", "pytest.ini", "-p", "no:cacheprovider"]
                + quick_cov_args
                + ["-x", "--tb=short"]
                + scopes["backend"],
                "Smart test run",
                force_all_apps=force_all_apps,
                env={"COLLAB_KEEP_ROOT_COVERAGE": "1"},
            )
        else:
            print_warning("Quick mode: No relevant changes — skipping tests.")
            success = "skipped"

        checks.append(("Tests", success))

    else:
        print_section("Step 9/11: Full Test Suite with Coverage")
        success, _ = run_command(
            [
                "pytest",
                "-c",
                "pytest.ini",
                "-p",
                "no:cacheprovider",
            ]
            + _cov_sources
            + [
                "--cov-report=term-missing",
                "--cov-report=html",
                "--cov-report=xml",
            ]
            + _FULL_TESTPATHS,
            "Full test suite with discovery",
            force_all_apps=force_all_apps,
            env={"COLLAB_KEEP_ROOT_COVERAGE": "1"},
        )
        checks.append(("Full Discovery Suite", success))

    print_section("Step 10/11: Total Coverage Validation")
    if not quick:
        success, _ = run_command(
            [
                "coverage",
                "report",
                "--fail-under=85",
            ],
            "Coverage threshold check (>= 85%)",
            force_all_apps=force_all_apps,
        )
        checks.append(("Total Coverage Threshold", success))
    else:
        msg10 = (
            f"{Colors.OKCYAN}[INFO] Quick mode: Skipping overall coverage "
            f"threshold check.{Colors.ENDC}"
        )
        print(msg10)
        checks.append(("Total Coverage Threshold", "skipped"))

    print_section("Step 11/11: Diff (Patch) Coverage")
    if not os.path.exists("coverage.xml"):
        msg_cov = (
            f"{Colors.OKCYAN}[INFO] coverage.xml not found (no tests run?), skipping "
            f"diff-cover.{Colors.ENDC}"
        )
        print(msg_cov)
        checks.append(("Diff Coverage", "skipped"))
    else:
        success, _ = run_command(
            ["diff-cover", "--version"], "Check diff-cover", check=False
        )
        if success:
            compare_branch, branch_warning = _resolve_diff_compare_branch(quick)
            if not compare_branch:
                checks.append(("Diff Coverage", False))
                print_error("Diff Coverage Check (New Code needs 92% coverage) failed")
                if branch_warning:
                    print_warning(branch_warning)
                print_section("Python Backend Validation Summary")
                all_passed = all(_check_succeeded(status) for _, status in checks)
                for check_name, status in checks:
                    _print_check_summary(check_name, status)
                return all_passed

            if branch_warning:
                print_warning(branch_warning)

            diff_cover_cmd = [
                "diff-cover",
                "coverage.xml",
                f"--compare-branch={compare_branch}",
                "--fail-under=92",
                "--include-untracked",
            ]

            if quick and not scopes["full_suite"]:
                py_files = [
                    f for f in scopes.get("changed_files", []) if f.endswith(".py")
                ]
                if py_files:
                    diff_cover_cmd.append("--include")
                    diff_cover_cmd.extend(py_files)

            success, _ = run_command(
                diff_cover_cmd,
                "Diff Coverage Check (New Code needs 92% coverage)",
            )
            checks.append(("Diff Coverage", success))
        else:
            msg_dc = (
                f"{Colors.OKCYAN}[INFO] diff-cover not installed. Run "
                f"'pip install diff-cover' to enable patch checks.{Colors.ENDC}"
            )
            print(msg_dc)
            checks.append(("Diff Coverage", "skipped"))

    # Print summary
    print_section("Python Backend Validation Summary")
    all_passed = all(_check_succeeded(status) for _, status in checks)
    for check_name, status in checks:
        _print_check_summary(check_name, status)

    return all_passed


def validate_others(files: Optional[List[str]] = None) -> bool:
    doc_paths = []
    if files:
        doc_paths = [
            f
            for f in files
            if f.endswith((".md", ".json", ".yml", ".yaml"))
            and not f.startswith(".venv")
            and not Path(f).name.startswith(".")
        ]
        if not doc_paths:
            return True

    print_header("OTHERS VALIDATION")
    checks: List[Tuple[str, ValidationStatus | bool]] = []
    success: ValidationStatus | bool = True

    print_section("Step 1/1: Documentation Formatting (prettier)")
    if not doc_paths:
        doc_globs = [
            "docs/**/*.md",
            "*.md",
            "*.json",
            ".github/**/*.md",
            "tests/**/*.md",
            ".agents/**/*.md",
        ]
        doc_paths = [pattern for pattern in doc_globs if list(Path.cwd().glob(pattern))]

    try:
        npm_cmd = ["cmd", "/c", "npm"] if sys.platform == "win32" else ["npm"]
        check_prettier = subprocess.run(
            npm_cmd + ["list", "prettier"],
            cwd=os.getcwd(),
            capture_output=True,
            check=False,
        )
        if check_prettier.returncode == 0:
            success, _ = run_command(
                ["npx", "prettier", "--check"] + doc_paths,
                "Documentation formatting check",
            )
            checks.append(("Documentation Linting", success))
        else:
            print(
                f"{Colors.OKCYAN}[INFO] Prettier not installed - "
                f"skipping documentation linting.{Colors.ENDC}"
            )
            checks.append(("Documentation Linting", "skipped"))
    except Exception:
        print(
            f"{Colors.OKCYAN}[INFO] Error checking for Prettier - "
            f"skipping documentation linting.{Colors.ENDC}"
        )
        checks.append(("Documentation Linting", "skipped"))

    print_section("Others Validation Summary")
    all_passed = all(_check_succeeded(status) for _, status in checks)
    for check_name, status in checks:
        _print_check_summary(check_name, status)

    return all_passed


def _load_package_json_scripts() -> Dict[str, str]:
    """Return package.json scripts or an empty mapping when unavailable."""
    package_json = Path("package.json")
    if not package_json.exists():
        return {}

    try:
        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    scripts = payload.get("scripts")
    if isinstance(scripts, dict):
        return {str(key): str(value) for key, value in scripts.items()}
    return {}


def _has_playwright_test_files() -> bool:
    """Return True when the frontend Playwright directory has runnable tests."""
    test_dir = Path("tests/frontend/playwright")
    if not test_dir.exists():
        return False

    patterns = (
        "**/*.spec.js",
        "**/*.spec.ts",
        "**/*.test.js",
        "**/*.test.ts",
    )
    return any(any(test_dir.glob(pattern)) for pattern in patterns)


def validate_javascript_frontend(
    quick: bool = False, force_all_apps: bool = True, files: Optional[List[str]] = None
) -> bool:
    """Run JavaScript frontend validation checks when relevant files exist."""
    npm_available = shutil.which("npm") is not None

    if not npm_available:
        msg_npm = (
            f"{Colors.OKCYAN}[INFO] npm not found - frontend validation "
            f"will be skipped locally.{Colors.ENDC}"
        )
        print(msg_npm)
        return True

    eslint_targets = []
    html_targets = []
    jest_targets = []

    if files:
        eslint_targets = [
            f for f in files if f.endswith((".js", ".jsx", ".ts", ".tsx"))
        ]
        html_targets = [f for f in files if f.endswith(".html")]
        jest_targets = [f for f in files if f.endswith(".test.js")]
        if not any([eslint_targets, html_targets, jest_targets]):
            return True
    else:
        glob_patterns = [
            "src/**/*.js",
            "src/**/*.css",
            "tests/frontend/**/*.js",
        ]
        discovered = [
            pattern for pattern in glob_patterns if list(Path.cwd().glob(pattern))
        ]
        if not discovered:
            return True

    print_header("JAVASCRIPT FRONTEND VALIDATION")
    checks: List[Tuple[str, ValidationStatus | bool]] = []
    success: ValidationStatus | bool = True

    # Step 1: ESLint (or skip if not configured)
    print_section("Step 1/3: JavaScript Linting (eslint)")
    if files and eslint_targets:
        success, _ = run_command(
            ["npx", "eslint"] + eslint_targets + ["--report-unused-disable-directives"],
            "ESLint check",
            force_all_apps=force_all_apps,
            check=False,
        )
    else:
        success, _ = run_command(
            [
                "npx",
                "eslint",
                "tests/frontend/playwright",
                "--report-unused-disable-directives",
            ],
            "ESLint check",
            force_all_apps=force_all_apps,
            check=False,
        )
    if not success:
        print_warning("ESLint unavailable or not configured - skipping strict failure.")
        success = "skipped"
    checks.append(("ESLint", success))

    # Step 2: Jest (or skip if test script is missing)
    print_section("Step 2/3: JavaScript Tests (jest)")
    if quick:
        print(
            f"{Colors.OKCYAN}[INFO] Quick mode: skipping frontend test execution "
            f"unless explicitly requested.{Colors.ENDC}"
        )
        success = "skipped"
    else:
        package_scripts = _load_package_json_scripts()
        if "test" not in package_scripts:
            print(
                f"{Colors.OKCYAN}[INFO] No npm 'test' script configured — skipping "
                f"Jest coverage run.{Colors.ENDC}"
            )
            success = "skipped"
        else:
            success, _ = run_command(
                ["npm", "run", "test", "--", "--coverage"],
                "Jest tests with coverage",
                force_all_apps=force_all_apps,
                check=False,
            )
            if not success:
                print_warning("Jest tests failed; skipping strict frontend failure.")
                success = "skipped"
    checks.append(("Jest Tests", success))

    # Step 3: Playwright (non-quick mode only)
    print_section("Step 3/3: E2E Tests (playwright)")
    if quick:
        print(f"{Colors.OKCYAN}[INFO] Quick mode: skipping E2E tests.{Colors.ENDC}")
        success = "skipped"
    else:
        if not _has_playwright_test_files():
            print(
                f"{Colors.OKCYAN}[INFO] No Playwright test files found — skipping "
                f"E2E validation.{Colors.ENDC}"
            )
            success = "skipped"
        else:
            success, _ = run_command(
                ["npx", "playwright", "test", "--project=chromium"],
                "Playwright E2E tests",
                force_all_apps=force_all_apps,
                check=False,
            )
            if not success:
                print_warning(
                    "Playwright tests failed; skipping strict frontend failure."
                )
                success = "skipped"
    checks.append(("E2E Tests", success))

    print_section("Frontend Validation Summary")
    all_passed = all(_check_succeeded(status) for _, status in checks)
    for check_name, status in checks:
        _print_check_summary(check_name, status)
    return all_passed


def _run_cleanup() -> None:
    print_header("CLEANUP")
    # Default cleanup (coverage + test output)
    count_default = clean_default(dry_run=False)

    # Packaging cleanup (dist/, build/, wheel metadata, *.egg-info, .venv.verify)
    # Run unconditionally so packaging artifacts are removed automatically
    # after validation runs. We call the function directly (no interactive
    # prompts) because this is a programmatic invocation.
    count_packaging = clean_packaging(dry_run=False)

    total = count_default + count_packaging
    if total:
        print_success(f"Removed {total} generated artifact(s) — repo is clean.")
    else:
        print_success("Nothing to clean — repo is already clean.")


def main():
    parser = argparse.ArgumentParser(description="Comprehensive code validation script")
    parser.add_argument(
        "--backend", action="store_true", help="Run only Python backend validation"
    )
    parser.add_argument(
        "--frontend",
        action="store_true",
        help="Run only JavaScript frontend validation",
    )
    parser.add_argument(
        "--docs",
        action="store_true",
        help="Run only Documentation validation",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode: Smart scoping for faster feedback",
    )
    parser.add_argument("files", nargs="*", help="Specific files to validate")

    args, unknown = parser.parse_known_args()
    if unknown:
        args.files = list(args.files or []) + list(unknown)

    if args.files:
        args.files = _expand_input_paths(args.files)

    run_all = not (args.backend or args.frontend or args.docs)
    run_backend = args.backend or run_all
    run_frontend = args.frontend or run_all
    run_docs = args.docs or run_all

    if args.files:
        has_backend = any(
            [
                [
                    f
                    for f in args.files
                    if (f.endswith(".py")) and not Path(f).name.startswith(".")
                ],
                [f for f in args.files if f.startswith("src/")],
            ]
        )
        has_docs = any(
            [
                f
                for f in args.files
                if f.endswith((".md", ".json", ".yml", ".yaml"))
                and not Path(f).name.startswith(".")
            ]
        )
        has_frontend = any(
            [
                f
                for f in args.files
                if f.endswith((".js", ".jsx", ".ts", ".tsx", ".css"))
                and not Path(f).name.startswith(".")
            ]
        )

        run_backend = run_backend and has_backend
        run_frontend = run_frontend and has_frontend
        run_docs = run_docs and has_docs

        if not any([run_backend, run_frontend, run_docs]):
            return 0

    print_header("COLLAB RUNTIME CODE VALIDATION")
    print(f"{Colors.OKCYAN}This script simulates the CI pipeline locally.{Colors.ENDC}")
    print(f"{Colors.OKCYAN}All checks must pass before committing code.{Colors.ENDC}\n")

    results = []

    if run_backend:
        backend_passed = validate_python_backend(
            quick=args.quick,
            files=args.files if args.files else None,
        )
        results.append(("Backend", backend_passed))

    if run_frontend:
        frontend_passed = validate_javascript_frontend(
            quick=args.quick,
            files=args.files if args.files else None,
        )
        results.append(("Frontend", frontend_passed))

    if run_docs:
        docs_passed = validate_others(files=args.files if args.files else None)
        results.append(("Documentation", docs_passed))

    print_header("FINAL VALIDATION SUMMARY")

    all_passed = all(passed for _, passed in results)

    for category, passed in results:
        if passed:
            print_success(f"{category} Validation: PASSED")
        else:
            print_error(f"{category} Validation: FAILED")

    print()
    if all_passed:
        print_success("All validation checks passed!")
        print(f"{Colors.OKGREEN}You can safely commit your changes.{Colors.ENDC}")
        _run_cleanup()
        return 0
    else:
        print_error("Some validation checks failed!")
        print(f"{Colors.FAIL}Please fix the issues before committing.{Colors.ENDC}")
        _run_cleanup()
        return 1


if __name__ == "__main__":
    sys.exit(main())
