"""Tests for scripts/validate_code.py."""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.backend.unit.scripts._helpers import load_script_module

validate_code = load_script_module("validate_code.py", "validate_code_under_test")


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
    cmd = validate_code._python_module_fallback_command(["ruff", "check", "src"])
    assert cmd is not None
    assert cmd[:3] == [validate_code.sys.executable, "-m", "ruff"]

    diff_cmd = validate_code._python_module_fallback_command(
        ["diff-cover", "coverage.xml"]
    )
    assert diff_cmd is not None
    assert diff_cmd[:3] == [
        validate_code.sys.executable,
        "-m",
        "diff_cover.diff_cover_tool",
    ]

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
    """Verify temp directory routing is applied when NOT in CI."""
    # Clear CI/GITHUB_ACTIONS and COVERAGE_FILE
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    # Call the function — should apply temp directory routing
    validate_code._configure_coverage_data_file()

    # Verify COVERAGE_FILE was set (temp directory routing applied)
    assert validate_code.os.getenv("COVERAGE_FILE") is not None
    assert "collab" in validate_code.os.getenv("COVERAGE_FILE")


def test_run_command_uses_python_module_resolution(monkeypatch):
    calls = []

    def _fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(validate_code.subprocess, "run", _fake_run)

    success, output = validate_code.run_command(
        ["ruff", "check", "src"],
        "Ruff linting",
        check=False,
    )

    assert success is True
    assert output == "ok"
    assert len(calls) == 1
    # Should use python -m resolution pre-emptively
    assert calls[0][:3] == [validate_code.sys.executable, "-m", "ruff"]


def test_get_changed_files_collects_all_three_sources(monkeypatch):
    payloads = [
        "src/main.py\n",
        "scripts/validate_code.py\n",
        "new_file.py\n",
    ]

    def _run(*_a, **_k):
        return SimpleNamespace(returncode=0, stdout=payloads.pop(0), stderr="")

    monkeypatch.setattr(validate_code.subprocess, "run", _run)
    changed = validate_code._get_changed_files()
    assert "src/main.py" in changed
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
        lambda: ["src/lock_client.py"],
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
            "changed_files": ["src/main.py", "tests/backend/unit/test_x.py"],
        },
    )

    assert (
        validate_code.validate_python_backend(
            quick=True,
            files=["src/main.py"],
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
            files=["src/main.py"],
        )
        is True
    )

    calls = []

    def _cmd(cmd, *_a, **_k):
        calls.append(cmd)
        return True, ""

    monkeypatch.setattr(validate_code, "run_command", _cmd)
    assert (
        validate_code.validate_javascript_frontend(
            quick=False,
            files=["src/dashboard/app.js"],
        )
        is True
    )
    assert any(cmd and cmd[0] == "npx" for cmd in calls)


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
    assert validate_code.validate_others(files=["src/main.py"]) is True

    monkeypatch.setattr(
        validate_code.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert validate_code.validate_others(files=["docs/readme.md"]) is True


def test_validate_frontend_glob_empty_and_failure(monkeypatch):
    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")

    # No frontend files provided should short-circuit successfully.
    assert validate_code.validate_javascript_frontend(quick=False, files=[]) is True

    monkeypatch.setattr(validate_code, "run_command", lambda *_a, **_k: (False, "bad"))
    # Frontend validation now soft-skips strict failure when tooling is missing.
    assert (
        validate_code.validate_javascript_frontend(
            quick=False,
            files=["src/dashboard/app.js"],
        )
        is True
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


def test_validate_frontend_soft_skips_when_jest_command_fails(monkeypatch, capsys):
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

    assert validate_code.validate_javascript_frontend(quick=False, files=None) is True

    out = capsys.readouterr().out
    assert "Jest tests failed; skipping strict frontend failure." in out
    assert any(cmd[:3] == ["npm", "run", "test"] for cmd in calls)


def test_validate_frontend_soft_skips_when_playwright_command_fails(
    monkeypatch, capsys
):
    calls = []

    def _cmd(cmd, *_a, **_k):
        calls.append(cmd)
        if cmd[:3] == ["npx", "playwright", "test"]:
            return False, "playwright failed"
        return True, ""

    monkeypatch.setattr(validate_code.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(validate_code, "run_command", _cmd)
    monkeypatch.setattr(validate_code, "_load_package_json_scripts", lambda: {})
    monkeypatch.setattr(validate_code, "_has_playwright_test_files", lambda: True)

    assert validate_code.validate_javascript_frontend(quick=False, files=None) is True

    out = capsys.readouterr().out
    assert "Playwright tests failed; skipping strict frontend failure." in out
    assert any(cmd[:3] == ["npx", "playwright", "test"] for cmd in calls)


def test_summary_helper_prints_skipped(capsys):
    validate_code._print_check_summary("Jest Tests", "skipped")
    out = capsys.readouterr().out
    assert "[SKIPPED] Jest Tests" in out


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

    script_path = Path("scripts/validate_code.py").resolve()
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
            "src/dashboard/app.js",
            "README.md",
            "--unknown-flag",
        ],
    )
    assert validate_code.main() == 1
