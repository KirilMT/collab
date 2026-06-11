"""Tests for scripts/validate_code.py."""

from __future__ import annotations

import importlib.util
import io
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.backend.unit.scripts._helpers import load_script_module

validate_code = load_script_module("validate_code.py", "validate_code_under_test")

# Repo root anchored from this test file: tests/backend/unit/scripts/<file>.py.
REPO_ROOT = Path(__file__).resolve().parents[4]
VALIDATE_CODE_PATH = REPO_ROOT / "scripts" / "validate_code.py"


def test_validate_backend_clears_mypy_cache_on_exists(monkeypatch, tmp_path):
    """Verify that validate_python_backend clears stale .mypy_cache."""
    # Create a mock mypy_cache directory
    cache_dir = tmp_path / ".mypy_cache"
    cache_dir.mkdir()
    (cache_dir / "test_marker.txt").touch()

    # Mock Path(".mypy_cache") to return our test directory
    original_path = Path

    def mock_path(p):
        if p == ".mypy_cache":
            return cache_dir
        return original_path(p)

    monkeypatch.setattr(validate_code, "Path", mock_path)

    # Mock run_command to succeed for all commands
    commands_seen = []

    def mock_run_command(cmd, *_a, **_k):
        commands_seen.append(cmd)
        return True, ""

    monkeypatch.setattr(validate_code, "run_command", mock_run_command)

    # Run with files=None to trigger full backend validation
    validate_code.validate_python_backend(quick=False, files=None)

    # Verify cache was removed (directory should not exist anymore)
    assert not cache_dir.exists(), "mypy_cache should have been removed"


def test_validate_backend_handles_cache_cleanup_error(monkeypatch, capsys):
    """Verify graceful handling when cache cleanup fails."""
    mock_cache = MagicMock()
    mock_cache.exists.return_value = True

    def mock_path(p):
        if p == ".mypy_cache":
            return mock_cache
        return Path(p)

    def mock_rmtree(path):
        raise OSError("Permission denied")

    monkeypatch.setattr(validate_code, "Path", mock_path)
    monkeypatch.setattr(validate_code.shutil, "rmtree", mock_rmtree)

    commands_seen = []

    def mock_run_command(cmd, *_a, **_k):
        commands_seen.append(cmd)
        return True, ""

    monkeypatch.setattr(validate_code, "run_command", mock_run_command)

    # Should not crash even if cache removal fails
    validate_code.validate_python_backend(quick=False, files=None)

    # Verify warning was printed
    captured = capsys.readouterr()
    assert "Could not remove .mypy_cache" in captured.out or "Could not remove" in str(
        captured
    )


def test_format_failure_output_pytest_sections():
    noisy_stdout = "\n".join(
        [f"test_{index:03d} PASSED" for index in range(60)]
        + [
            "============================= FAILURES =============================",
            "__________________________ test_example ___________________________",
            "E       AssertionError: assert 1 == 2",
            "====================== short test summary info ======================",
            "FAILED tests/backend/unit/test_example.py::test_example - AssertionError",
        ]
    )

    formatted = validate_code.format_failure_output(noisy_stdout, "")
    assert "Pytest short summary" in formatted
    assert "FAILED tests/backend/unit/test_example.py::test_example" in formatted
    assert "AssertionError" in formatted
    assert "test_000 PASSED" not in formatted


def test_format_failure_output_generic_fallback():
    stdout = "\n".join([f"line {index}" for index in range(220)])
    formatted = validate_code.format_failure_output(stdout, "")
    assert "First lines" in formatted
    assert "Last lines" in formatted
    assert "line 0" in formatted
    assert "line 219" in formatted


def test_python_module_fallback_command_maps_known_tools():
    cmd = validate_code._python_module_fallback_command(["ruff", "check", "collab"])
    assert cmd is not None
    assert cmd[0].lower() == validate_code.sys.executable.lower()
    assert cmd[1:3] == ["-m", "ruff"]

    diff_cmd = validate_code._python_module_fallback_command(
        ["diff-cover", "coverage.xml"]
    )
    assert diff_cmd is not None
    assert diff_cmd[0].lower() == validate_code.sys.executable.lower()
    assert diff_cmd[1:3] == ["-m", "diff_cover.diff_cover_tool"]

    assert validate_code._python_module_fallback_command(["unknown-tool"]) is None


def test_run_command_merges_env_and_windows_roots(monkeypatch):
    captured = {}

    def _fake_run(*_args, **kwargs):
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(validate_code.subprocess, "run", _fake_run)
    monkeypatch.setenv("SYSTEMDRIVE", "C:")
    monkeypatch.setenv("PROGRAMDATA", r"C:\ProgramData")

    success, _ = validate_code.run_command(
        ["python", "-V"],
        "python version",
        check=False,
        env={"CUSTOM_FLAG": "enabled"},
    )

    assert success is True
    assert captured["env"]["CUSTOM_FLAG"] == "enabled"
    assert captured["env"]["SYSTEMDRIVE"] == "C:"


def test_configure_coverage_data_file_skips_ci_environments(monkeypatch):
    """Verify that CI environment routing is skipped when CI env vars are set."""
    # Clear COVERAGE_FILE if it exists
    monkeypatch.delenv("COVERAGE_FILE", raising=False)

    # Set GITHUB_ACTIONS to simulate GitHub Actions CI
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    # Call the function — should return early due to CI detection
    validate_code._configure_coverage_data_file()

    # Verify COVERAGE_FILE was NOT set (early return due to CI check)
    assert validate_code.os.getenv("COVERAGE_FILE") is None


def test_configure_coverage_data_file_applies_temp_routing_locally(monkeypatch):
    """Verify temp directory routing is applied when NOT in CI or pytest."""
    # Snapshot the real COVERAGE_FILE so the direct os.environ mutation inside
    # the function cannot leak into later tests (monkeypatch.delenv does not
    # record an undo entry when the variable is absent).
    before = validate_code.os.environ.get("COVERAGE_FILE")

    # Clear all guards so the local temp-routing branch is exercised.
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    try:
        # Call the function — should apply temp directory routing.
        validate_code._configure_coverage_data_file()

        # Verify COVERAGE_FILE was set (temp directory routing applied).
        routed = validate_code.os.getenv("COVERAGE_FILE")
        assert routed is not None
        assert "collab" in routed
    finally:
        # Restore the pre-test state regardless of how the assertions resolve.
        if before is None:
            validate_code.os.environ.pop("COVERAGE_FILE", None)
        else:
            validate_code.os.environ["COVERAGE_FILE"] = before


def test_configure_coverage_data_file_skips_under_pytest(monkeypatch):
    """Verify pytest environment causes early return (covers line 71)."""
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_func")

    validate_code._configure_coverage_data_file()

    # Should NOT have set COVERAGE_FILE (early return due to PYTEST_CURRENT_TEST)
    assert validate_code.os.getenv("COVERAGE_FILE") is None


def test_run_command_uses_python_module_resolution(monkeypatch):
    calls = []

    def _fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(validate_code.subprocess, "run", _fake_run)

    success, output = validate_code.run_command(
        ["ruff", "check", "collab"],
        "Ruff linting",
        check=False,
    )

    assert success is True
    assert output == "ok"
    assert len(calls) == 1
    # Should use python -m resolution pre-emptively
    assert calls[0][0].lower() == validate_code.sys.executable.lower()
    assert calls[0][1:3] == ["-m", "ruff"]


def test_get_changed_files_collects_all_three_sources(monkeypatch):
    payloads = [
        "collab/main.py\n",
        "scripts/validate_code.py\n",
        "new_file.py\n",
    ]

    def _run(*_a, **_k):
        return SimpleNamespace(returncode=0, stdout=payloads.pop(0), stderr="")

    monkeypatch.setattr(validate_code.subprocess, "run", _run)
    changed = validate_code._get_changed_files()
    assert "collab/main.py" in changed
    assert "scripts/validate_code.py" in changed
    assert "new_file.py" in changed


def test_expand_input_paths_for_file_and_directory(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    f = pkg / "mod.py"
    f.write_text("x = 1\n", encoding="utf-8")

    expanded = validate_code._expand_input_paths([str(pkg), str(f), "raw\\x.py"])
    assert any(p.endswith("pkg/mod.py") for p in expanded)
    assert any(p.endswith("x.py") for p in expanded)


def test_detect_changed_scopes_branches(monkeypatch):
    monkeypatch.setattr(validate_code, "_get_changed_files", lambda: [])
    scopes = validate_code.detect_changed_scopes()
    assert scopes["full_suite"] is True
    assert scopes["changed_files"] == []

    monkeypatch.setattr(validate_code, "_get_changed_files", lambda: ["pyproject.toml"])
    scopes = validate_code.detect_changed_scopes()
    assert scopes["full_suite"] is True
    assert "Global config changed" in (scopes["reason"] or "")

    monkeypatch.setattr(
        validate_code,
        "_get_changed_files",
        lambda: ["scripts/validate_code.py"],
    )
    scopes = validate_code.detect_changed_scopes()
    assert scopes["full_suite"] is True
    assert "Infrastructure file changed" in (scopes["reason"] or "")

    monkeypatch.setattr(
        validate_code,
        "_get_changed_files",
        lambda: ["collab/lock_client.py"],
    )
    scopes = validate_code.detect_changed_scopes()
    assert scopes["full_suite"] is False
    assert "tests/backend/unit/" in scopes["backend"]


def test_validate_python_backend_full_mode(monkeypatch):
    commands = []

    def _run_command(cmd, *_a, **_k):
        commands.append(cmd)
        return True, ""

    monkeypatch.setattr(validate_code, "run_command", _run_command)
    monkeypatch.setattr(validate_code.os.path, "exists", lambda p: p == "coverage.xml")

    assert validate_code.validate_python_backend(quick=False, files=None) is True
    assert any(cmd and cmd[0] == "isort" for cmd in commands)
    assert any(cmd and cmd[0] == "diff-cover" for cmd in commands)


def test_validate_python_backend_quick_modes(monkeypatch):
    monkeypatch.setattr(validate_code.os.path, "exists", lambda p: p == "coverage.xml")

    seen = []

    def _run_command(cmd, *_a, **_k):
        seen.append(cmd)
        # fail only diff-cover validation command
        if cmd[:2] == ["diff-cover", "coverage.xml"]:
            return False, "below threshold"
        return True, ""

    monkeypatch.setattr(validate_code, "run_command", _run_command)
    monkeypatch.setattr(
        validate_code,
        "detect_changed_scopes",
        lambda *a, **k: {
            "full_suite": False,
            "backend": ["tests/backend/unit/"],
            "frontend": [],
            "reason": None,
            "changed_files": ["collab/main.py", "tests/backend/unit/test_x.py"],
        },
    )

    assert (
        validate_code.validate_python_backend(
            quick=True,
            files=["collab/main.py"],
        )
        is False
    )
    assert any("--include" in cmd for cmd in seen if cmd and cmd[0] == "diff-cover")

    monkeypatch.setattr(
        validate_code,
        "detect_changed_scopes",
        lambda *a, **k: {
            "full_suite": False,
            "backend": [],
            "frontend": [],
            "reason": None,
            "changed_files": [],
        },
    )
    monkeypatch.setattr(validate_code.os.path, "exists", lambda _p: False)
    seen.clear()
    assert (
        validate_code.validate_python_backend(
            quick=True,
            files=["scripts/validate_code.py"],
        )
        is True
    )


def test_validate_python_backend_diff_cover_soft_skip(monkeypatch):
    monkeypatch.setattr(validate_code.os.path, "exists", lambda p: p == "coverage.xml")

    def _run_command(cmd, *_a, **_k):
        if cmd == ["diff-cover", "--version"]:
            return False, "missing"
        return True, ""

    monkeypatch.setattr(validate_code, "run_command", _run_command)
    assert validate_code.validate_python_backend(quick=False, files=None) is True


def test_validate_others_branches(monkeypatch):
    monkeypatch.setattr(
        validate_code.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=1),
    )
    assert validate_code.validate_others(files=["docs/readme.md"]) is True

    calls = []

    def _run(*_a, **_k):
        return SimpleNamespace(returncode=0)

    def _cmd(cmd, *_a, **_k):
        calls.append(cmd)
        return True, ""

    monkeypatch.setattr(validate_code.subprocess, "run", _run)
    monkeypatch.setattr(validate_code, "run_command", _cmd)
    assert validate_code.validate_others(files=["docs/readme.md"]) is True
    assert any(cmd and cmd[0] == "npx" for cmd in calls)


def test_validate_frontend_branches(monkeypatch):
    monkeypatch.setattr(validate_code.shutil, "which", lambda name: None)
    assert validate_code.validate_javascript_frontend(quick=False, files=None) is True

    monkeypatch.setattr(validate_code.shutil, "which", lambda name: "/usr/bin/npm")
    assert (
        validate_code.validate_javascript_frontend(
            quick=False,
            files=["collab/main.py"],
        )
        is True
    )

    calls = []

    def _cmd(cmd, *_a, **_k):
        calls.append(cmd)
        return True, ""

    monkeypatch.setattr(validate_code, "run_command", _cmd)
    monkeypatch.setattr(
        validate_code,
        "_load_package_json_scripts",
        lambda: {
            "test": "jest",
            "test:frontend:e2e:ci": "playwright test",
        },
    )
    monkeypatch.setattr(validate_code, "_has_supabase_credentials", lambda: True)
    monkeypatch.setattr(validate_code, "_has_playwright_test_files", lambda: True)
    assert (
        validate_code.validate_javascript_frontend(
            quick=False,
            files=["collab/dashboard/dashboard-format.js"],
        )
        is True
    )
    assert any(cmd[:3] == ["npm", "run", "test:frontend:e2e:ci"] for cmd in calls)


def test_main_exit_codes(monkeypatch):
    monkeypatch.setattr(validate_code, "validate_python_backend", lambda **_k: True)
    monkeypatch.setattr(
        validate_code,
        "validate_javascript_frontend",
        lambda **_k: True,
    )
    monkeypatch.setattr(validate_code, "validate_others", lambda **_k: True)
    monkeypatch.setattr(validate_code, "_run_cleanup", lambda: None)

    monkeypatch.setattr(validate_code.sys, "argv", ["validate_code.py", "--backend"])
    assert validate_code.main() == 0

    monkeypatch.setattr(validate_code, "validate_python_backend", lambda **_k: False)
    monkeypatch.setattr(validate_code.sys, "argv", ["validate_code.py", "--backend"])
    assert validate_code.main() == 1


def test_main_file_filtering_short_circuit(monkeypatch):
    monkeypatch.setattr(validate_code, "_run_cleanup", lambda: None)
    monkeypatch.setattr(
        validate_code.sys,
        "argv",
        ["validate_code.py", "README.txt"],
    )
    assert validate_code.main() == 0


def test_run_cleanup_output_paths(monkeypatch, capsys):
    monkeypatch.setattr(validate_code, "clean_default", lambda dry_run=False: 2)
    monkeypatch.setattr(validate_code, "clean_packaging", lambda dry_run=False: 0)
    validate_code._run_cleanup()
    assert "Removed 2 generated artifact(s)" in capsys.readouterr().out

    monkeypatch.setattr(validate_code, "clean_default", lambda dry_run=False: 0)
    monkeypatch.setattr(validate_code, "clean_packaging", lambda dry_run=False: 0)
    validate_code._run_cleanup()
    assert "Nothing to clean" in capsys.readouterr().out


def test_resolve_diff_compare_branch_paths(monkeypatch):
    assert validate_code._resolve_diff_compare_branch(quick=True) == ("HEAD", None)

    monkeypatch.setattr(validate_code, "_git_ref_exists", lambda ref: ref == "HEAD~1")
    monkeypatch.setattr(validate_code, "_git_remote_origin_exists", lambda: False)
    branch, warning = validate_code._resolve_diff_compare_branch(quick=False)
    assert branch == "HEAD~1"
    assert warning is not None and "HEAD~1" in warning

    monkeypatch.setattr(validate_code, "_git_ref_exists", lambda _ref: False)
    monkeypatch.setattr(validate_code, "_git_remote_origin_exists", lambda: False)
    branch, warning = validate_code._resolve_diff_compare_branch(quick=False)
    assert branch is None
    assert warning is not None and "Unable to resolve" in warning

    state: dict[str, bool] = {"fetched": False}

    def _fake_ref(ref: str) -> bool:
        if ref == "origin/main":
            return state["fetched"]
        if ref in {"main", "HEAD~1"}:
            return False
        return False

    def _fake_run(cmd, **_kwargs):
        if cmd[:3] == ["git", "fetch", "origin"]:
            state["fetched"] = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(validate_code, "_git_ref_exists", _fake_ref)
    monkeypatch.setattr(validate_code, "_git_remote_origin_exists", lambda: True)
    monkeypatch.setattr(validate_code.subprocess, "run", _fake_run)
    branch, warning = validate_code._resolve_diff_compare_branch(quick=False)
    assert branch == "origin/main"
    assert warning is not None and "resolved after fetching" in warning


def test_validate_backend_diff_cover_unresolved_compare_branch(monkeypatch):
    commands = []

    def _run_command(cmd, *_a, **_k):
        commands.append(cmd)
        return True, ""

    monkeypatch.setattr(validate_code, "run_command", _run_command)
    monkeypatch.setattr(validate_code.os.path, "exists", lambda p: p == "coverage.xml")
    monkeypatch.setattr(
        validate_code,
        "_resolve_diff_compare_branch",
        lambda _quick: (None, "missing compare branch"),
    )

    assert validate_code.validate_python_backend(quick=False, files=None) is False
    assert not any(cmd[:2] == ["diff-cover", "coverage.xml"] for cmd in commands)


def test_validate_backend_diff_cover_warning_and_include_filter(monkeypatch):
    seen = []

    def _run_command(cmd, *_a, **_k):
        seen.append(cmd)
        return True, ""

    monkeypatch.setattr(validate_code, "run_command", _run_command)
    monkeypatch.setattr(validate_code.os.path, "exists", lambda p: p == "coverage.xml")
    monkeypatch.setattr(
        validate_code,
        "detect_changed_scopes",
        lambda *a, **k: {
            "full_suite": False,
            "backend": ["tests/backend/unit/"],
            "frontend": [],
            "reason": None,
            "changed_files": ["scripts/validate_code.py", "README.md", "run.py"],
        },
    )
    monkeypatch.setattr(
        validate_code,
        "_resolve_diff_compare_branch",
        lambda _quick: ("HEAD", "branch warning"),
    )

    assert (
        validate_code.validate_python_backend(
            quick=True, files=["scripts/validate_code.py"]
        )
        is True
    )
    diff_cmd = next(cmd for cmd in seen if cmd[:2] == ["diff-cover", "coverage.xml"])
    assert "--include" in diff_cmd
    assert "scripts/validate_code.py" in diff_cmd
    assert "run.py" in diff_cmd
    assert "README.md" not in diff_cmd


def test_validate_others_early_return_and_exception(monkeypatch):
    assert validate_code.validate_others(files=["collab/main.py"]) is True

    monkeypatch.setattr(
        validate_code.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert validate_code.validate_others(files=["docs/readme.md"]) is True


def test_frontend_validation_plan_full_requires_ci_script_and_supabase():
    scopes = validate_code.detect_changed_scopes(["collab/dashboard/index.html"])
    plan = validate_code._frontend_validation_plan(
        quick=False, files=None, scopes=scopes
    )
    assert plan["playwright_script"] == "test:frontend:e2e:ci"
    assert plan["require_supabase_for_playwright"] is True


def test_frontend_validation_plan_explicit_files_uses_fast_without_supabase(
    monkeypatch,
):
    monkeypatch.setattr(validate_code, "_has_supabase_credentials", lambda: False)
    plan = validate_code._frontend_validation_plan(
        quick=False,
        files=["collab/dashboard/dashboard-format.js"],
        scopes={},
    )
    assert plan["playwright_script"] == "test:frontend:e2e:fast"
    assert plan["require_supabase_for_playwright"] is True


def test_quick_frontend_needs_jest_and_playwright_helpers():
    assert validate_code._quick_frontend_needs_jest(["jest.config.cjs"], False) is True
    assert (
        validate_code._quick_frontend_needs_jest(["collab/lock_client.py"], False)
        is False
    )
    assert validate_code._quick_frontend_needs_jest([], True) is True
    assert (
        validate_code._quick_frontend_needs_playwright(["package.json"], False) is True
    )
    assert (
        validate_code._quick_frontend_needs_playwright(["docs/readme.md"], False)
        is False
    )


def test_validate_frontend_quick_scopes_skip_eslint_jest(monkeypatch, capsys):
    """Quick mode: HTML-only dashboard change runs Playwright fast, skips
    ESLint/Jest."""
    calls = []

    def _cmd(cmd, *_a, **_k):
        calls.append(cmd)
        return True, ""

    scopes = {
        "full_suite": False,
        "backend": [],
        "frontend": ["tests/frontend/"],
        "reason": None,
        "changed_files": ["collab/dashboard/index.html"],
    }
    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(validate_code, "run_command", _cmd)
    monkeypatch.setattr(
        validate_code,
        "_load_package_json_scripts",
        lambda: {
            "test": "jest",
            "test:frontend:e2e:fast": "playwright test --project=chromium",
        },
    )
    monkeypatch.setattr(validate_code, "_has_playwright_test_files", lambda: True)

    assert (
        validate_code.validate_javascript_frontend(
            quick=True, files=None, scopes=scopes
        )
        is True
    )
    out = capsys.readouterr().out
    assert "skipping ESLint" in out
    assert "skipping Jest" in out
    assert any(cmd[:3] == ["npm", "run", "test:frontend:e2e:fast"] for cmd in calls)


def test_validate_frontend_quick_scopes_skip_playwright(monkeypatch, capsys):
    """Quick mode: Jest-only unit change skips Playwright."""
    calls = []

    def _cmd(cmd, *_a, **_k):
        calls.append(cmd)
        return True, ""

    scopes = {
        "full_suite": False,
        "backend": [],
        "frontend": ["tests/frontend/"],
        "reason": None,
        "changed_files": ["tests/frontend/unit/dashboard-format.test.js"],
    }
    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(validate_code, "run_command", _cmd)
    monkeypatch.setattr(
        validate_code,
        "_load_package_json_scripts",
        lambda: {"test": "jest"},
    )
    monkeypatch.setattr(validate_code, "_has_playwright_test_files", lambda: True)

    assert (
        validate_code.validate_javascript_frontend(
            quick=True, files=None, scopes=scopes
        )
        is True
    )
    out = capsys.readouterr().out
    assert "skipping Playwright" in out
    assert ["npm", "run", "test:frontend:e2e:fast"] not in calls
    assert ["npm", "run", "test:frontend:e2e:ci"] not in calls


def test_validate_frontend_fails_without_supabase_credentials(monkeypatch, capsys):
    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(validate_code, "run_command", lambda *_a, **_k: (True, ""))
    monkeypatch.setattr(
        validate_code,
        "_load_package_json_scripts",
        lambda: {
            "test": "jest",
            "test:frontend:e2e:ci": "playwright test",
        },
    )
    monkeypatch.setattr(validate_code, "_has_supabase_credentials", lambda: False)
    monkeypatch.setattr(validate_code, "_has_playwright_test_files", lambda: True)
    monkeypatch.setattr(
        validate_code,
        "detect_changed_scopes",
        lambda *a, **k: {
            "full_suite": False,
            "backend": [],
            "frontend": ["tests/frontend/"],
            "reason": None,
            "changed_files": ["collab/dashboard/index.html"],
        },
    )

    assert validate_code.validate_javascript_frontend(quick=False, files=None) is False
    out = capsys.readouterr().out
    assert "SUPABASE_URL" in out
    assert "[FAIL] E2E Tests" in out


def test_validate_frontend_fails_missing_playwright_script(monkeypatch, capsys):
    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(validate_code, "run_command", lambda *_a, **_k: (True, ""))
    monkeypatch.setattr(
        validate_code,
        "_load_package_json_scripts",
        lambda: {"test": "jest"},
    )
    monkeypatch.setattr(validate_code, "_has_supabase_credentials", lambda: True)
    monkeypatch.setattr(validate_code, "_has_playwright_test_files", lambda: True)
    monkeypatch.setattr(
        validate_code,
        "detect_changed_scopes",
        lambda *a, **k: {
            "full_suite": False,
            "backend": [],
            "frontend": ["tests/frontend/"],
            "reason": None,
            "changed_files": ["collab/dashboard/index.html"],
        },
    )

    assert validate_code.validate_javascript_frontend(quick=False, files=None) is False
    out = capsys.readouterr().out
    assert "package.json missing" in out


def test_validate_frontend_quick_skips_entire_block(monkeypatch, capsys):
    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")
    scopes = {
        "full_suite": False,
        "backend": ["tests/backend/"],
        "frontend": [],
        "reason": None,
        "changed_files": ["collab/foo.py"],
    }
    assert (
        validate_code.validate_javascript_frontend(
            quick=True, files=None, scopes=scopes
        )
        is True
    )
    out = capsys.readouterr().out
    assert "skipping frontend" in out.lower()


def test_validate_frontend_glob_empty_and_failure(monkeypatch):
    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")

    # Non-frontend path in explicit file list short-circuits before any npm commands.
    assert (
        validate_code.validate_javascript_frontend(
            quick=False, files=["collab/main.py"]
        )
        is True
    )

    monkeypatch.setattr(validate_code, "run_command", lambda *_a, **_k: (False, "bad"))
    monkeypatch.setattr(
        validate_code,
        "_load_package_json_scripts",
        lambda: {
            "test": "jest",
            "test:frontend:e2e:ci": "playwright test",
        },
    )
    monkeypatch.setattr(validate_code, "_has_supabase_credentials", lambda: True)
    monkeypatch.setattr(validate_code, "_has_playwright_test_files", lambda: True)
    assert (
        validate_code.validate_javascript_frontend(
            quick=False,
            files=["collab/dashboard/dashboard-format.js"],
        )
        is False
    )


def test_validate_frontend_skips_jest_without_test_script(monkeypatch, capsys):
    seen = []

    def _cmd(cmd, *_a, **_k):
        seen.append(cmd)
        return True, ""

    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(validate_code, "run_command", _cmd)
    monkeypatch.setattr(
        validate_code,
        "_load_package_json_scripts",
        lambda: {"validate": "python scripts/validate_code.py"},
    )
    monkeypatch.setattr(validate_code, "_has_playwright_test_files", lambda: False)

    assert (
        validate_code.validate_javascript_frontend(
            quick=False,
            files=["tests/frontend/playwright/test-utils.js"],
        )
        is True
    )

    out = capsys.readouterr().out
    assert "No npm 'test' script configured — skipping Jest coverage run." in out
    assert ["npm", "run", "test", "--", "--coverage"] not in seen


def test_validate_frontend_skips_playwright_without_test_files(monkeypatch, capsys):
    seen = []

    def _cmd(cmd, *_a, **_k):
        seen.append(cmd)
        return True, ""

    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(validate_code, "run_command", _cmd)
    monkeypatch.setattr(validate_code, "_load_package_json_scripts", lambda: {})
    monkeypatch.setattr(validate_code, "_has_playwright_test_files", lambda: False)

    assert (
        validate_code.validate_javascript_frontend(
            quick=False,
            files=["tests/frontend/playwright/test-utils.js"],
        )
        is True
    )

    out = capsys.readouterr().out
    assert "No Playwright test files found — skipping E2E validation." in out
    assert ["npx", "playwright", "test", "--project=chromium"] not in seen


def test_load_package_json_scripts_handles_missing_invalid_and_non_dict(tmp_path):
    current_dir = Path.cwd()
    try:
        validate_code.os.chdir(tmp_path)

        assert validate_code._load_package_json_scripts() == {}

        package_json = tmp_path / "package.json"
        package_json.write_text("{invalid json", encoding="utf-8")
        assert validate_code._load_package_json_scripts() == {}

        package_json.write_text('{"scripts": []}', encoding="utf-8")
        assert validate_code._load_package_json_scripts() == {}
    finally:
        validate_code.os.chdir(current_dir)


def test_load_package_json_scripts_returns_stringified_scripts(tmp_path):
    current_dir = Path.cwd()
    try:
        validate_code.os.chdir(tmp_path)
        (tmp_path / "package.json").write_text(
            '{"scripts": {"test": "npm test", "lint": 123}}',
            encoding="utf-8",
        )

        scripts = validate_code._load_package_json_scripts()
        assert scripts == {"test": "npm test", "lint": "123"}
    finally:
        validate_code.os.chdir(current_dir)


def test_has_playwright_test_files_handles_missing_and_present(tmp_path):
    current_dir = Path.cwd()
    try:
        validate_code.os.chdir(tmp_path)
        assert validate_code._has_playwright_test_files() is False

        test_dir = tmp_path / "tests" / "frontend" / "playwright"
        test_dir.mkdir(parents=True)
        (test_dir / "sample.spec.ts").write_text(
            "test('x', () => {});\n",
            encoding="utf-8",
        )

        assert validate_code._has_playwright_test_files() is True
    finally:
        validate_code.os.chdir(current_dir)


def test_validate_frontend_fails_when_eslint_command_fails(monkeypatch, capsys):
    calls = []

    def _cmd(cmd, *_a, **_k):
        calls.append(cmd)
        if cmd[:2] == ["npx", "eslint"]:
            return False, "eslint failed"
        return True, ""

    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(validate_code, "run_command", _cmd)
    monkeypatch.setattr(validate_code, "_load_package_json_scripts", lambda: {})
    monkeypatch.setattr(validate_code, "_has_playwright_test_files", lambda: False)

    assert validate_code.validate_javascript_frontend(quick=False, files=None) is False

    out = capsys.readouterr().out
    assert "[FAIL] ESLint" in out
    assert any(cmd[:2] == ["npx", "eslint"] for cmd in calls)


# ---------------------------------------------------------------------------
# _check_cross_platform_code
# ---------------------------------------------------------------------------


def test_cross_platform_check_warns_on_windll_without_type_ignore(
    monkeypatch, capsys, tmp_path
):
    """Staged .py file with bare ctypes.windll triggers a warning."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_mod.py").write_text(
        "kernel32 = ctypes.windll.kernel32\n", encoding="utf-8"
    )

    import subprocess as _sp

    def _mock_run(cmd, **_k):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "test_mod.py"
        return result

    monkeypatch.setattr(_sp, "run", _mock_run)

    validate_code._check_cross_platform_code()
    captured = capsys.readouterr()
    assert "CROSS-PLATFORM" in captured.out
    assert "ctypes.windll" in captured.out


def test_cross_platform_check_skips_type_ignore_line(monkeypatch, capsys, tmp_path):
    """Staged .py file with # type: ignore is skipped."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_mod.py").write_text(
        "kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]\n",
        encoding="utf-8",
    )

    import subprocess as _sp

    def _mock_run(cmd, **_k):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "test_mod.py"
        return result

    monkeypatch.setattr(_sp, "run", _mock_run)

    validate_code._check_cross_platform_code()
    captured = capsys.readouterr()
    assert "CROSS-PLATFORM" not in captured.out


def test_cross_platform_check_no_staged_py_files_is_noop(monkeypatch, capsys):
    """No staged .py files — nothing printed."""
    import subprocess as _sp

    def _mock_run(cmd, **_k):
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        return result

    monkeypatch.setattr(_sp, "run", _mock_run)

    validate_code._check_cross_platform_code()
    captured = capsys.readouterr()
    assert captured.out == ""


def test_cross_platform_check_skips_getattr_pattern(monkeypatch, capsys, tmp_path):
    """Getattr(ctypes, 'windll', None) pattern is safe-ish and skipped."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_mod.py").write_text(
        '_windll = getattr(ctypes, "windll", None)\n', encoding="utf-8"
    )

    import subprocess as _sp

    def _mock_run(cmd, **_k):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "test_mod.py"
        return result

    monkeypatch.setattr(_sp, "run", _mock_run)

    validate_code._check_cross_platform_code()
    captured = capsys.readouterr()
    assert "CROSS-PLATFORM" not in captured.out


def test_cross_platform_check_git_subprocess_failure_is_noop(monkeypatch, capsys):
    """Git diff failure — empty list — noop."""
    import subprocess as _sp

    def _mock_run(cmd, **_k):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        return result

    monkeypatch.setattr(_sp, "run", _mock_run)

    validate_code._check_cross_platform_code()
    captured = capsys.readouterr()
    assert captured.out == ""


def test_cross_platform_check_git_not_installed_is_noop(monkeypatch, capsys):
    """Git not installed — FileNotFoundError — noop."""
    import subprocess as _sp

    def _mock_run(cmd, **_k):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(_sp, "run", _mock_run)

    validate_code._check_cross_platform_code()
    captured = capsys.readouterr()
    assert captured.out == ""


def test_cross_platform_check_binary_file_handled(monkeypatch, capsys, tmp_path):
    """Binary .py file raises UnicodeDecodeError — silently skipped."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bad.py").write_bytes(b"\x80\x81\x82")

    import subprocess as _sp

    def _mock_run(cmd, **_k):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "bad.py"
        return result

    monkeypatch.setattr(_sp, "run", _mock_run)

    validate_code._check_cross_platform_code()
    captured = capsys.readouterr()
    assert captured.out == ""


def test_cross_platform_check_non_py_staged_is_noop(monkeypatch, capsys):
    """Staged non-.py files are silently skipped."""
    import subprocess as _sp

    def _mock_run(cmd, **_k):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "README.md\nindex.html"
        return result

    monkeypatch.setattr(_sp, "run", _mock_run)

    validate_code._check_cross_platform_code()
    captured = capsys.readouterr()
    assert captured.out == ""


def test_validate_frontend_fails_when_jest_command_fails(monkeypatch, capsys):
    calls = []

    def _cmd(cmd, *_a, **_k):
        calls.append(cmd)
        if cmd[:3] == ["npm", "run", "test"]:
            return False, "jest failed"
        return True, ""

    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(validate_code, "run_command", _cmd)
    monkeypatch.setattr(
        validate_code,
        "_load_package_json_scripts",
        lambda: {"test": "jest"},
    )
    monkeypatch.setattr(validate_code, "_has_playwright_test_files", lambda: False)

    assert validate_code.validate_javascript_frontend(quick=False, files=None) is False

    out = capsys.readouterr().out
    assert "[FAIL] Jest Tests" in out
    assert any(cmd[:3] == ["npm", "run", "test"] for cmd in calls)


def test_validate_frontend_fails_when_playwright_command_fails(monkeypatch, capsys):
    calls = []

    def _cmd(cmd, *_a, **_k):
        calls.append(cmd)
        if cmd[:3] == ["npm", "run", "test:frontend:e2e:ci"]:
            return False, "playwright failed"
        return True, ""

    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(validate_code, "run_command", _cmd)
    monkeypatch.setattr(
        validate_code,
        "_load_package_json_scripts",
        lambda: {
            "test": "jest",
            "test:frontend:e2e:ci": "playwright test",
        },
    )
    monkeypatch.setattr(validate_code, "_has_supabase_credentials", lambda: True)
    monkeypatch.setattr(validate_code, "_has_playwright_test_files", lambda: True)
    monkeypatch.setattr(
        validate_code,
        "detect_changed_scopes",
        lambda *a, **k: {
            "full_suite": True,
            "backend": [],
            "frontend": ["tests/frontend/"],
            "reason": None,
            "changed_files": ["package.json"],
        },
    )

    assert validate_code.validate_javascript_frontend(quick=False, files=None) is False

    out = capsys.readouterr().out
    assert "[FAIL] E2E Tests" in out
    assert any(cmd[:3] == ["npm", "run", "test:frontend:e2e:ci"] for cmd in calls)


def test_validate_backend_summary_marks_skipped_checks(monkeypatch):
    seen = []

    def _run_command(cmd, *_a, **_k):
        if cmd[:2] == ["diff-cover", "--version"]:
            return False, "missing"
        return True, ""

    monkeypatch.setattr(
        validate_code,
        "_print_check_summary",
        lambda name, status: seen.append((name, status)),
    )
    monkeypatch.setattr(validate_code, "run_command", _run_command)
    monkeypatch.setattr(validate_code.os.path, "exists", lambda _p: False)
    monkeypatch.setattr(
        validate_code,
        "detect_changed_scopes",
        lambda *a, **k: {
            "full_suite": False,
            "backend": [],
            "frontend": [],
            "reason": None,
            "changed_files": [],
        },
    )

    assert (
        validate_code.validate_python_backend(
            quick=True,
            files=["scripts/validate_code.py"],
        )
        is True
    )
    assert ("Tests", "skipped") in seen
    assert ("Total Coverage Threshold", "skipped") in seen
    assert ("Diff Coverage", "skipped") in seen


def test_validate_frontend_summary_marks_skipped_checks(monkeypatch, capsys):
    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(validate_code, "run_command", lambda *_a, **_k: (True, ""))
    monkeypatch.setattr(validate_code, "_load_package_json_scripts", lambda: {})
    monkeypatch.setattr(validate_code, "_has_playwright_test_files", lambda: False)

    assert validate_code.validate_javascript_frontend(quick=False, files=None) is True
    out = capsys.readouterr().out
    assert "[SKIPPED] Jest Tests" in out
    assert "[SKIPPED] E2E Tests" in out


def test_validate_others_summary_marks_skipped_checks(monkeypatch, capsys):
    monkeypatch.setattr(
        validate_code.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=1),
    )

    assert validate_code.validate_others(files=["docs/readme.md"]) is True
    out = capsys.readouterr().out
    assert "[SKIPPED] Documentation Linting" in out


def test_print_helpers_and_tail_paths(capsys):
    validate_code._print_failure_output("", "")
    out = capsys.readouterr().out
    assert out == ""

    validate_code._print_failure_output("line\n", "")
    out = capsys.readouterr().out
    assert "Failure details" in out

    validate_code._print_output_tail("", "Label", validate_code.Colors.OKCYAN)
    assert capsys.readouterr().out == ""

    long_text = "\n".join(f"line {i}" for i in range(220))
    validate_code._print_output_tail(long_text, "Tail", validate_code.Colors.OKCYAN)
    out = capsys.readouterr().out
    assert "showing last" in out


def test_module_import_branches_for_encoding_and_missing_dotenv(monkeypatch):
    class _DummyStream:
        def __init__(self):
            self.buffer = io.BytesIO()
            self.encoding = "cp1252"

        def write(self, text):
            return len(text)

        def flush(self):
            return None

        def reconfigure(self, **_kwargs):
            raise RuntimeError("no reconfigure")

    dummy_out = _DummyStream()
    dummy_err = _DummyStream()

    monkeypatch.setattr(sys, "stdout", dummy_out)
    monkeypatch.setattr(sys, "stderr", dummy_err)
    monkeypatch.setattr(sys, "platform", "win32", raising=False)
    monkeypatch.setitem(sys.modules, "dotenv", None)

    script_path = VALIDATE_CODE_PATH
    spec = importlib.util.spec_from_file_location(
        "validate_code_import_branch_ut",
        script_path,
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod._load_dotenv is None


def test_git_helper_error_paths(monkeypatch):
    monkeypatch.setattr(
        validate_code.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("x")),
    )
    assert validate_code._git_ref_exists("origin/main") is False
    assert validate_code._git_remote_origin_exists() is False


def test_main_unknown_args_and_multi_category_paths(monkeypatch):
    monkeypatch.setattr(validate_code, "_run_cleanup", lambda: None)
    monkeypatch.setattr(validate_code, "_expand_input_paths", lambda paths: paths)
    monkeypatch.setattr(
        validate_code,
        "validate_javascript_frontend",
        lambda **_k: False,
    )
    monkeypatch.setattr(validate_code, "validate_others", lambda **_k: True)
    monkeypatch.setattr(validate_code, "validate_python_backend", lambda **_k: True)
    monkeypatch.setattr(
        validate_code.sys,
        "argv",
        [
            "validate_code.py",
            "--frontend",
            "--docs",
            "collab/dashboard/app.js",
            "README.md",
            "--unknown-flag",
        ],
    )
    assert validate_code.main() == 1


def test_configure_coverage_data_file_handles_exception(monkeypatch):
    """A failure while building the temp path is swallowed (lines 81-83)."""
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    def _raise(*_a, **_k):
        raise RuntimeError("digest boom")

    monkeypatch.setattr(validate_code.hashlib, "sha1", _raise)
    validate_code._configure_coverage_data_file()
    assert validate_code.os.getenv("COVERAGE_FILE") is None


def test_module_import_handles_reconfigure_failure(monkeypatch):
    """A reconfigure failure on the original stdout is swallowed (102-103)."""

    class _Stream(io.StringIO):
        encoding = "utf-8"

        def reconfigure(self, **_kwargs):
            raise RuntimeError("no reconfigure")

    monkeypatch.setattr(sys, "stdout", _Stream())
    monkeypatch.setattr(sys, "stderr", _Stream())
    monkeypatch.setattr(sys, "__stdout__", _Stream(), raising=False)
    monkeypatch.setattr(sys, "platform", "win32", raising=False)

    script_path = VALIDATE_CODE_PATH
    spec = importlib.util.spec_from_file_location(
        "validate_code_reconfigure_ut", script_path
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Assert a stable side effect of a successful import rather than the
    # tautological ``mod is not None`` — the module must expose its public API.
    assert hasattr(mod, "run_command")
    assert callable(mod.run_command)


def test_dedupe_output_blocks_skips_none_blocks():
    """``None`` blocks are skipped while duplicates collapse (line 178)."""
    result = validate_code._dedupe_output_blocks("alpha", None, "alpha", "beta")
    assert result == ["alpha", "beta"]


def test_extract_coverage_block_returns_marked_region():
    """A coverage marker yields the surrounding region (lines 209-211)."""
    lines = ["intro", "coverage: 87%", "TOTAL 87%", "outro"]
    block = validate_code._extract_coverage_block(lines)
    assert "coverage: 87%" in block


def test_format_failure_output_includes_coverage_details():
    """Coverage markers produce a dedicated section (line 264)."""
    stdout = "\n".join(
        [
            "================ short test summary info ================",
            "FAILED tests/x.py::test_y - boom",
            "coverage: platform linux",
            "TOTAL 80%",
        ]
    )
    formatted = validate_code.format_failure_output(stdout, "")
    assert "Coverage details" in formatted


def test_print_output_tail_short_output_prints_full(capsys):
    """Short output is printed verbatim (line 310)."""
    validate_code._print_output_tail(
        "short\nline", "Label", validate_code.Colors.OKCYAN
    )
    out = capsys.readouterr().out
    assert "short" in out
    assert "Label" in out


def test_get_python_executable_falls_back_to_sys_executable(monkeypatch):
    """A missing venv interpreter falls back to sys.executable (line 345)."""

    class _FakePath:
        def __init__(self, *_a):
            pass

        @property
        def parent(self):
            return self

        def __truediv__(self, _other):
            return self

        def exists(self):
            return False

    monkeypatch.setattr(validate_code, "Path", lambda *_a: _FakePath())
    assert validate_code._get_python_executable() == validate_code.sys.executable


def test_python_module_fallback_command_edge_cases():
    """Empty and path-like commands return None (lines 355, 359)."""
    assert validate_code._python_module_fallback_command([]) is None
    assert validate_code._python_module_fallback_command(["/usr/bin/ruff", "x"]) is None


def test_git_remote_origin_exists_success(monkeypatch):
    """A zero return code reports an existing origin remote (line 395)."""
    monkeypatch.setattr(
        validate_code.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert validate_code._git_remote_origin_exists() is True


def test_resolve_diff_compare_branch_handles_fetch_error(monkeypatch):
    """A fetch OSError is swallowed before falling back (lines 425-426)."""
    monkeypatch.setattr(validate_code, "_git_ref_exists", lambda _ref: False)
    monkeypatch.setattr(validate_code, "_git_remote_origin_exists", lambda: True)

    def _raise(*_a, **_k):
        raise OSError("fetch failed")

    monkeypatch.setattr(validate_code.subprocess, "run", _raise)
    branch, warning = validate_code._resolve_diff_compare_branch(quick=False)
    assert branch is None
    assert warning is not None and "Unable to resolve" in warning


def test_run_command_wraps_windows_npm(monkeypatch):
    """Npm commands are wrapped with ``cmd /c`` on Windows (line 472)."""
    monkeypatch.setattr(validate_code.sys, "platform", "win32")
    captured = {}

    def _run(cmd, **_k):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(validate_code.subprocess, "run", _run)
    success, _ = validate_code.run_command(["npm", "list"], "npm list", check=False)
    assert success is True
    assert captured["cmd"][:2] == ["cmd", "/c"]


def test_run_command_returns_false_on_nonzero(monkeypatch, capsys):
    """A non-zero return code prints failure and returns False (534-537)."""
    monkeypatch.setattr(
        validate_code.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="o", stderr="e"),
    )
    success, output = validate_code.run_command(["python", "-V"], "ver", check=False)
    assert success is False
    assert output == "e"
    assert "[FAIL]" in capsys.readouterr().out


def test_run_command_handles_called_process_error(monkeypatch):
    """A CalledProcessError is reported as a failure (lines 539-542)."""
    err = validate_code.subprocess.CalledProcessError(
        2, ["python"], output="boom-out", stderr="boom-err"
    )

    def _raise(*_a, **_k):
        raise err

    monkeypatch.setattr(validate_code.subprocess, "run", _raise)
    success, output = validate_code.run_command(["python", "-V"], "ver")
    assert success is False
    assert "boom" in output


def test_run_command_handles_file_not_found(monkeypatch):
    """A missing executable is reported clearly (lines 543-546)."""

    def _raise(*_a, **_k):
        raise FileNotFoundError("no python")

    monkeypatch.setattr(validate_code.subprocess, "run", _raise)
    success, output = validate_code.run_command(["python", "-V"], "ver")
    assert success is False
    assert "Command not found" in output


def test_get_changed_files_returns_empty_on_git_error(monkeypatch):
    """A git invocation error yields an empty change set (lines 614-615)."""

    def _raise(*_a, **_k):
        raise FileNotFoundError("git missing")

    monkeypatch.setattr(validate_code.subprocess, "run", _raise)
    assert validate_code._get_changed_files() == []


def test_expand_input_paths_skips_empty_entries():
    """Empty path entries are ignored (line 633)."""
    result = validate_code._expand_input_paths(["", "ghost.py"])
    assert "ghost.py" in result
    assert "" not in result


def test_expand_input_paths_skips_dirs_and_ignored_parts(tmp_path):
    """Directories and ignored path parts are filtered out (lines 639, 641)."""
    pkg = tmp_path / "pkg"
    (pkg / "__pycache__").mkdir(parents=True)
    (pkg / "__pycache__" / "cached.pyc").write_text("x", encoding="utf-8")
    (pkg / "mod.py").write_text("x = 1\n", encoding="utf-8")

    result = validate_code._expand_input_paths([str(pkg)])
    assert any(p.endswith("pkg/mod.py") for p in result)
    assert not any("__pycache__" in p for p in result)


def test_quick_frontend_needs_playwright_full_suite_returns_true():
    """Full-suite mode always requires Playwright (line 741)."""
    assert validate_code._quick_frontend_needs_playwright([], True) is True


def test_validate_python_backend_no_targets_returns_true():
    """File lists with no actionable targets short-circuit (lines 838-839)."""
    assert (
        validate_code.validate_python_backend(quick=False, files=["notes.md"]) is True
    )


def test_validate_backend_djlint_soft_failure_with_files(monkeypatch, capsys):
    """A targeted djlint failure is treated as a soft skip (969, 986-987)."""
    seen = []

    def _run_command(cmd, *_a, **_k):
        seen.append(cmd)
        if "djlint" in cmd:
            return False, "djlint issues"
        return True, ""

    monkeypatch.setattr(validate_code, "run_command", _run_command)
    monkeypatch.setattr(validate_code.os.path, "exists", lambda _p: False)

    result = validate_code.validate_python_backend(
        quick=False, files=["collab/dashboard/index.html"]
    )
    assert result is True
    assert any("djlint" in cmd for cmd in seen)
    assert "DjLint found issues" in capsys.readouterr().out


def test_validate_others_globs_docs_when_no_files(monkeypatch):
    """File-less runs glob default doc targets (lines 1166-1174)."""
    monkeypatch.setattr(
        validate_code.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=1),
    )
    assert validate_code.validate_others(files=None) is True


def test_validate_frontend_full_mode_no_discovered_js(monkeypatch, tmp_path):
    """Full mode with no discoverable JS short-circuits (lines 1284-1294)."""
    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.chdir(tmp_path)
    scopes = {
        "full_suite": True,
        "backend": [],
        "frontend": ["tests/frontend/"],
        "reason": None,
        "changed_files": [],
    }
    assert (
        validate_code.validate_javascript_frontend(
            quick=False, files=None, scopes=scopes
        )
        is True
    )


def test_main_quick_mode_prints_scope_reason(monkeypatch, capsys):
    """Quick mode prints the scope resolution reason (line 1645)."""
    monkeypatch.setattr(validate_code, "_run_cleanup", lambda: None)
    monkeypatch.setattr(validate_code, "validate_python_backend", lambda **_k: True)
    monkeypatch.setattr(
        validate_code, "validate_javascript_frontend", lambda **_k: True
    )
    monkeypatch.setattr(validate_code, "validate_others", lambda **_k: True)
    monkeypatch.setattr(
        validate_code,
        "detect_changed_scopes",
        lambda *a, **k: {
            "full_suite": True,
            "backend": [],
            "frontend": [],
            "reason": "Global config changed",
            "changed_files": [],
        },
    )
    monkeypatch.setattr(validate_code.sys, "argv", ["validate_code.py", "--quick"])
    assert validate_code.main() == 0
    assert "Quick mode: Global config changed" in capsys.readouterr().out


def test_validate_code_dunder_main(monkeypatch):
    """The ``__main__`` guard runs main() and exits cleanly (line 1692)."""

    class _Stream(io.StringIO):
        encoding = "utf-8"

    monkeypatch.setattr(sys, "stdout", _Stream())
    monkeypatch.setattr(sys, "stderr", _Stream())
    # A non-matching file means main() short-circuits to a clean exit.
    monkeypatch.setattr(sys, "argv", ["validate_code.py", "ghost.bin"])

    script_path = VALIDATE_CODE_PATH
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(script_path), run_name="__main__")
    assert exc_info.value.code == 0


def _make_hook_dirs(root: Path) -> tuple[Path, Path]:
    """Create the two hook-template directories under a fake project root."""
    pkg_dir = root / "collab" / "hook_templates"
    scripts_dir = root / "scripts" / "git-hooks"
    pkg_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    return pkg_dir, scripts_dir


def test_validate_hook_shebangs_all_consistent(tmp_path, capsys):
    """Every extensionless hook starting with #!/bin/sh passes (happy path)."""
    pkg_dir, scripts_dir = _make_hook_dirs(tmp_path)
    (pkg_dir / "post-merge").write_text("#!/bin/sh\necho merged\n", encoding="utf-8")
    (scripts_dir / "post-checkout").write_text(
        "#!/bin/sh\necho switched\n", encoding="utf-8"
    )

    assert validate_code._validate_hook_template_shebangs(tmp_path) is True
    assert "Hook template shebangs consistent" in capsys.readouterr().out


def test_validate_hook_shebangs_detects_mismatch(tmp_path, capsys):
    """A hook with the wrong shebang fails and is reported by path."""
    pkg_dir, _ = _make_hook_dirs(tmp_path)
    (pkg_dir / "post-merge").write_text(
        "#!/usr/bin/env sh\necho merged\n", encoding="utf-8"
    )

    assert validate_code._validate_hook_template_shebangs(tmp_path) is False
    out = capsys.readouterr().out
    assert "shebang mismatch" in out
    assert "post-merge" in out


def test_validate_hook_shebangs_missing_dir_is_ok(tmp_path, capsys):
    """When no hook-template directories exist, validation is a no-op success."""
    assert validate_code._validate_hook_template_shebangs(tmp_path) is True
    assert "Hook template shebangs consistent" in capsys.readouterr().out


def test_validate_hook_shebangs_skips_dirs_dotfiles_and_unreadable(tmp_path):
    """Sub-directories, dotfiles, and unreadable files are skipped, not failed."""
    pkg_dir, _ = _make_hook_dirs(tmp_path)
    (pkg_dir / "nested").mkdir()  # directory entry -> skipped
    (pkg_dir / ".keep").write_text("not a shebang\n", encoding="utf-8")  # dotfile
    unreadable = pkg_dir / "post-merge"
    unreadable.write_text("#!/bin/sh\n", encoding="utf-8")

    # Force the read to raise OSError to exercise the except branch.
    real_read_text = Path.read_text

    def boom(self, *a, **k):
        if self == unreadable:
            raise OSError("boom")
        return real_read_text(self, *a, **k)

    import unittest.mock as _mock

    with _mock.patch.object(Path, "read_text", boom):
        assert validate_code._validate_hook_template_shebangs(tmp_path) is True
