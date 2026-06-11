"""Ported coverage tests for scripts/validate_code.py.

Only the cases that exercise code paths not already covered by
``test_validate_code.py`` are retained here.
"""

from __future__ import annotations

from tests.backend.unit.scripts._helpers import load_script_module

validate_code = load_script_module(
    "validate_code.py", "validate_code_ported_under_test"
)


def test_quick_source_only_full_suite(monkeypatch):
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
