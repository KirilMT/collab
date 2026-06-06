"""Additional ported coverage tests for scripts/validate_code.py."""

from __future__ import annotations

import shutil
from unittest.mock import MagicMock

from tests.backend.unit.scripts._helpers import load_script_module

validate_code = load_script_module(
    "validate_code.py", "validate_code_ported_under_test"
)


class TestValidatePythonBackendPaths:
    def test_full_mode_no_files(self, monkeypatch):
        monkeypatch.setattr(
            validate_code,
            "run_command",
            lambda cmd, desc, **kwargs: (True, ""),
        )
        monkeypatch.setattr(
            validate_code.os.path,
            "exists",
            lambda p: p == "coverage.xml",
        )
        assert validate_code.validate_python_backend(quick=False, files=None) is True

    def test_quick_with_test_files(self, monkeypatch):
        monkeypatch.setattr(
            validate_code,
            "run_command",
            lambda cmd, desc, **kwargs: (True, ""),
        )
        monkeypatch.setattr(
            validate_code.os.path,
            "exists",
            lambda p: p == "coverage.xml",
        )
        assert (
            validate_code.validate_python_backend(
                quick=True,
                files=["tests/backend/unit/test_foo.py", "collab/main.py"],
            )
            is True
        )

    def test_quick_source_only_full_suite(self, monkeypatch):
        monkeypatch.setattr(
            validate_code,
            "run_command",
            lambda cmd, desc, **kwargs: (True, ""),
        )
        monkeypatch.setattr(
            validate_code.os.path,
            "exists",
            lambda p: p == "coverage.xml",
        )
        monkeypatch.setattr(
            validate_code,
            "detect_changed_scopes",
            lambda *args, **kwargs: {
                "full_suite": True,
                "backend": [],
                "frontend": [],
                "reason": "x",
                "changed_files": [],
            },
        )
        assert (
            validate_code.validate_python_backend(
                quick=True,
                files=["collab/main.py"],
            )
            is True
        )

    def test_quick_no_files_backend_scope(self, monkeypatch):
        monkeypatch.setattr(
            validate_code,
            "run_command",
            lambda cmd, desc, **kwargs: (True, ""),
        )
        monkeypatch.setattr(
            validate_code.os.path,
            "exists",
            lambda p: p == "coverage.xml",
        )
        monkeypatch.setattr(
            validate_code,
            "detect_changed_scopes",
            lambda *args, **kwargs: {
                "full_suite": False,
                "backend": ["tests/backend/unit/"],
                "frontend": [],
                "reason": None,
                "changed_files": ["collab/main.py"],
            },
        )
        assert validate_code.validate_python_backend(quick=True, files=None) is True

    def test_quick_no_relevant_scopes_skips_tests(self, monkeypatch):
        monkeypatch.setattr(
            validate_code,
            "run_command",
            lambda cmd, desc, **kwargs: (True, ""),
        )
        monkeypatch.setattr(
            validate_code.os.path,
            "exists",
            lambda p: p == "coverage.xml",
        )
        monkeypatch.setattr(
            validate_code,
            "detect_changed_scopes",
            lambda *args, **kwargs: {
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
                files=["collab/main.py"],
            )
            is True
        )


def test_validate_python_backend_expands_directory_targets(tmp_path, monkeypatch):
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "mod.py").write_text("x = 1\n", encoding="utf-8")

    commands = []

    def _fake_run_command(cmd, desc, **kwargs):
        commands.append(cmd)
        return True, ""

    monkeypatch.setattr(validate_code, "run_command", _fake_run_command)
    monkeypatch.setattr(validate_code.os.path, "exists", lambda p: p == "coverage.xml")

    result = validate_code.validate_python_backend(quick=False, files=[str(pkg)])
    assert result is True
    assert any(cmd and cmd[0] == "isort" for cmd in commands)


def test_diff_cover_not_installed_soft_passes(monkeypatch):
    monkeypatch.setattr(validate_code.os.path, "exists", lambda p: p == "coverage.xml")

    def mock_run(cmd, desc, **kwargs):
        if "diff-cover" in cmd and "--version" in cmd:
            return False, "not found"
        return True, ""

    monkeypatch.setattr(validate_code, "run_command", mock_run)
    result = validate_code.validate_python_backend(quick=False, files=None)
    assert result is True


def test_diff_cover_fails_reports_failure(monkeypatch):
    monkeypatch.setattr(validate_code.os.path, "exists", lambda p: p == "coverage.xml")

    def mock_run(cmd, desc, **kwargs):
        if "diff-cover" in cmd and "--version" in cmd:
            return True, "diff-cover 1.0"
        if "diff-cover" in cmd and "coverage.xml" in cmd:
            return False, "Coverage below 95%"
        return True, ""

    monkeypatch.setattr(validate_code, "run_command", mock_run)
    result = validate_code.validate_python_backend(quick=False, files=None)
    assert result is False


def test_validate_others_prettier_not_installed(monkeypatch):
    monkeypatch.setattr(
        validate_code.subprocess,
        "run",
        lambda *a, **kw: MagicMock(returncode=1),
    )
    assert validate_code.validate_others(files=["docs/readme.md"]) is True


def test_validate_others_prettier_installed(monkeypatch):
    monkeypatch.setattr(
        validate_code.subprocess,
        "run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="ok", stderr=""),
    )
    monkeypatch.setattr(
        validate_code,
        "run_command",
        lambda cmd, desc, **kw: (True, ""),
    )
    assert validate_code.validate_others(files=["docs/readme.md"]) is True


def test_validate_frontend_branches(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert validate_code.validate_javascript_frontend(quick=False, files=None) is True

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/npm")
    monkeypatch.setattr(validate_code, "run_command", lambda *a, **kw: (True, ""))
    monkeypatch.setattr(
        validate_code,
        "detect_changed_scopes",
        lambda *args, **kwargs: {
            "full_suite": False,
            "backend": [],
            "frontend": ["collab/dashboard/app.js"],
            "reason": None,
            "changed_files": ["collab/dashboard/app.js"],
        },
    )
    assert (
        validate_code.validate_javascript_frontend(
            quick=True,
            files=["collab/dashboard/app.js"],
        )
        is True
    )


def test_detect_changed_scopes_reason_paths(monkeypatch):
    monkeypatch.setattr(validate_code, "_get_changed_files", lambda: ["scripts/x.py"])
    scopes = validate_code.detect_changed_scopes()
    assert scopes["full_suite"] is True
    assert "Infrastructure file changed" in (scopes["reason"] or "")

    monkeypatch.setattr(validate_code, "_get_changed_files", lambda: ["collab\\app.py"])
    scopes = validate_code.detect_changed_scopes()
    assert "collab\\app.py" in scopes["changed_files"]
