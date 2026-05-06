"""Tests for scripts/format_code.py."""

from __future__ import annotations

import io
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from tests.backend.unit.scripts._helpers import load_script_module

format_code = load_script_module("format_code.py", "format_code_under_test")


class CaptureStdout:
    def __enter__(self):
        self._stdout = sys.stdout
        sys.stdout = self._stringio = io.StringIO()
        return self._stringio

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self._stdout


def _mock_completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_normalize_whitespace_preserves_crlf():
    raw = b"a  \r\n\tb\t\r\n\r\n"
    normalized = format_code.CodeFormatter._normalize_whitespace(raw)
    assert normalized == b"a\r\n\tb\r\n"


def test_exec_success_and_exceptions(monkeypatch):
    formatter = format_code.CodeFormatter()

    monkeypatch.setattr(
        format_code.subprocess,
        "run",
        lambda *a, **k: _mock_completed(0, "ok", ""),
    )
    ok, result = formatter._exec(["tool", "arg"])
    assert ok is True
    assert result is not None

    monkeypatch.setattr(
        format_code.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("missing")),
    )
    ok, result = formatter._exec(["missing"])
    assert ok is False
    assert result is None

    monkeypatch.setattr(
        format_code.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    ok, result = formatter._exec(["bad"])
    assert ok is False
    assert result is None


def test_run_tool_step_check_only_paths(monkeypatch):
    formatter = format_code.CodeFormatter(check_only=True)

    monkeypatch.setattr(formatter, "_exec", lambda *a, **k: (True, _mock_completed()))
    assert formatter._run_tool_step("Tool", ["fix"], ["check"], "S", 1, 1) is True

    monkeypatch.setattr(formatter, "_exec", lambda *a, **k: (False, _mock_completed(1)))
    assert formatter._run_tool_step("Tool2", ["fix"], ["check"], "S", 1, 1) is False
    assert any("Tool2" in x[1] for x in formatter.failed_tools)


def test_run_tool_step_fix_then_check_paths(monkeypatch):
    formatter = format_code.CodeFormatter(check_only=False)

    calls = {"n": 0}

    def _exec(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return False, _mock_completed(1, "fail", "")
        return True, _mock_completed(0, "", "")

    monkeypatch.setattr(formatter, "_exec", _exec)
    assert formatter._run_tool_step("Tool", ["fix"], ["check"], "S", 1, 1) is True

    calls["n"] = 0

    def _exec_fail(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return False, _mock_completed(1, "fail", "")
        return False, _mock_completed(1, "still", "")

    monkeypatch.setattr(formatter, "_exec", _exec_fail)
    assert formatter._run_tool_step("ToolFail", ["fix"], ["check"], "S", 1, 1) is False


def test_normalize_whitespace_no_files(monkeypatch):
    formatter = format_code.CodeFormatter(files=["not-python.bin"])
    with CaptureStdout():
        assert formatter.normalize_whitespace() is True


def test_format_python_invokes_five_steps(monkeypatch):
    formatter = format_code.CodeFormatter(files=["a.py"])
    seen = []
    monkeypatch.setattr(
        formatter,
        "_run_tool_step",
        lambda desc, *_a, **_k: seen.append(desc) or True,
    )
    assert formatter.format_python() is True
    assert len(seen) == 5
    assert "Final linting (flake8)" in seen


def test_check_prettier_and_filter_targets(monkeypatch, tmp_path):
    formatter = format_code.CodeFormatter()
    monkeypatch.setattr(formatter, "root_dir", tmp_path)
    (tmp_path / "a.js").write_text("x", encoding="utf-8")

    call_index = {"n": 0}

    def _run(*_a, **_k):
        call_index["n"] += 1
        # prettier installed, plugin missing
        if call_index["n"] == 1:
            return _mock_completed(0)
        return _mock_completed(1)

    monkeypatch.setattr(format_code.subprocess, "run", _run)
    assert formatter._check_prettier() is False

    assert formatter._filter_glob_targets(["*.js", "*.css"]) == ["*.js"]


def test_format_frontend_docs_yaml(monkeypatch):
    formatter = format_code.CodeFormatter(files=["x.js", "doc.md", "a.yaml"])

    monkeypatch.setattr(formatter, "_check_prettier", lambda: True)
    monkeypatch.setattr(formatter, "_run_tool_step", lambda *_a, **_k: True)
    monkeypatch.setattr(
        formatter,
        "_filter_glob_targets",
        lambda _patterns: ["src/**/*.js", "docs/**/*.md"],
    )

    assert formatter.format_frontend() is True
    assert formatter.format_docs() is True


def test_format_yaml_with_files(monkeypatch, tmp_path):
    formatter = format_code.CodeFormatter()
    monkeypatch.setattr(formatter, "root_dir", tmp_path)
    y1 = tmp_path / "a.yaml"
    y2 = tmp_path / "b.yml"
    y1.write_text("k: v\n", encoding="utf-8")
    y2.write_text("k: v\n", encoding="utf-8")

    called = []
    monkeypatch.setattr(
        formatter,
        "_run_tool_step",
        lambda desc, *_a, **_k: called.append(desc) or True,
    )
    assert formatter.format_yaml() is True
    assert called == ["YAML (prettier)", "YAML (yamllint)"]


@pytest.mark.parametrize("djlint_version_ok", [False, True])
def test_format_templates_paths(monkeypatch, djlint_version_ok):
    formatter = format_code.CodeFormatter(files=["src/dashboard/index.html"])

    exec_calls = {"n": 0}

    def _exec(_cmd, suppress_output=False):
        exec_calls["n"] += 1
        # version check
        if exec_calls["n"] == 1:
            return djlint_version_ok, _mock_completed(0 if djlint_version_ok else 1)
        # fix/check pass
        return True, _mock_completed(0)

    monkeypatch.setattr(formatter, "_exec", _exec)
    assert formatter.format_templates() is True


def test_format_templates_check_failure(monkeypatch):
    formatter = format_code.CodeFormatter(files=["src/dashboard/index.html"])

    exec_calls = {"n": 0}

    def _exec(_cmd, suppress_output=False):
        exec_calls["n"] += 1
        if exec_calls["n"] == 1:
            return True, _mock_completed(0)  # version
        if exec_calls["n"] == 2:
            return False, _mock_completed(1)  # fix failed
        if exec_calls["n"] == 3:
            return False, _mock_completed(1)  # check failed
        return False, _mock_completed(1)

    monkeypatch.setattr(formatter, "_exec", _exec)
    assert formatter.format_templates() is False
    assert formatter.failed_tools


def test_print_summary_success_and_failure(capsys):
    formatter = format_code.CodeFormatter()
    formatter.print_summary()
    out = capsys.readouterr().out
    assert "All formatting operations completed successfully" in out

    formatter.failed_tools.append(("[X] step", "step", True))
    formatter.print_summary()
    out = capsys.readouterr().out
    assert "operation(s) failed" in out


def test_main_paths(monkeypatch):
    monkeypatch.setattr(format_code, "clean_caches", lambda dry_run=False: 0)

    formatter = format_code.CodeFormatter()
    monkeypatch.setattr(format_code, "CodeFormatter", lambda **_k: formatter)
    monkeypatch.setattr(formatter, "normalize_whitespace", lambda: True)
    monkeypatch.setattr(formatter, "format_python", lambda: True)
    monkeypatch.setattr(formatter, "format_frontend", lambda: True)
    monkeypatch.setattr(formatter, "format_templates", lambda: True)
    monkeypatch.setattr(formatter, "format_docs", lambda: True)
    monkeypatch.setattr(formatter, "format_yaml", lambda: True)
    monkeypatch.setattr(formatter, "print_summary", lambda: None)

    with mock.patch("sys.argv", ["format_code.py", "--backend"]):
        assert format_code.main() == 0

    monkeypatch.setattr(formatter, "format_python", lambda: False)
    with mock.patch("sys.argv", ["format_code.py", "--backend"]):
        assert format_code.main() == 1


def test_get_targets_and_python_executable_paths(monkeypatch, tmp_path):
    formatter = format_code.CodeFormatter(files=None)
    assert formatter._get_targets((".py",), ["src", "tests"]) == ["src", "tests"]

    monkeypatch.setattr(formatter, "root_dir", tmp_path)
    monkeypatch.setattr(format_code.sys, "platform", "win32")
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    exe = scripts_dir / "python.exe"
    exe.write_text("", encoding="utf-8")
    assert formatter._get_python_executable().endswith("python.exe")

    exe.unlink()
    assert formatter._get_python_executable() == format_code.sys.executable


def test_exec_stderr_and_git_lsfiles_failure(monkeypatch, capsys):
    formatter = format_code.CodeFormatter()

    monkeypatch.setattr(
        format_code.subprocess,
        "run",
        lambda *_a, **_k: _mock_completed(0, "", "warn\nerr"),
    )
    ok, _ = formatter._exec(["tool"])
    assert ok is True
    assert "warn" in capsys.readouterr().err

    def _raise(*_a, **_k):
        raise FileNotFoundError("git missing")

    monkeypatch.setattr(format_code.subprocess, "run", _raise)
    with CaptureStdout() as out:
        assert formatter.normalize_whitespace() is True
    assert "Could not list git files" in out.getvalue()


def test_normalize_whitespace_issues_and_skips(monkeypatch, tmp_path):
    formatter = format_code.CodeFormatter(check_only=False)
    monkeypatch.setattr(formatter, "root_dir", tmp_path)

    raw_file = tmp_path / "bad.py"
    raw_file.write_bytes(b"a  \n")

    bin_file = tmp_path / "binary.py"
    bin_file.write_bytes(b"\x00\x01\x02")

    tmp_path / "missing.py"

    monkeypatch.setattr(
        format_code.subprocess,
        "run",
        lambda *_a, **_k: _mock_completed(
            0,
            "bad.py\nbinary.py\nmissing.py\n",
            "",
        ),
    )

    original_read_bytes = Path.read_bytes

    def _patched_read_bytes(path_obj):
        if path_obj.name == "missing.py":
            raise OSError("gone")
        return original_read_bytes(path_obj)

    monkeypatch.setattr(Path, "read_bytes", _patched_read_bytes)

    assert formatter.normalize_whitespace() is True
    assert raw_file.read_bytes().endswith(b"\n")

    # Check-only path should report remaining issues and return False.
    formatter_check = format_code.CodeFormatter(check_only=True)
    monkeypatch.setattr(formatter_check, "root_dir", tmp_path)
    raw_file.write_bytes(b"b  \n")
    monkeypatch.setattr(
        format_code.subprocess,
        "run",
        lambda *_a, **_k: _mock_completed(0, "bad.py\n", ""),
    )
    assert formatter_check.normalize_whitespace() is False


def test_prettier_and_target_early_returns(monkeypatch):
    formatter = format_code.CodeFormatter(files=["x.py"])  # no frontend/docs targets

    monkeypatch.setattr(formatter, "_filter_glob_targets", lambda _p: [])
    assert formatter.format_frontend() is True
    assert formatter.format_docs() is True

    monkeypatch.setattr(formatter, "_check_prettier", lambda: False)
    monkeypatch.setattr(formatter, "_filter_glob_targets", lambda _p: ["src/**/*.js"])
    monkeypatch.setattr(
        formatter,
        "_get_targets",
        lambda _ext, default: default,
    )
    assert formatter.format_frontend() is True

    monkeypatch.setattr(formatter, "root_dir", Path("."))
    monkeypatch.setattr(Path, "rglob", lambda self, _ext: iter(()))
    assert formatter.format_yaml() is True


def test_dunder_main_path(monkeypatch):
    monkeypatch.setattr(format_code, "clean_caches", lambda dry_run=False: 0)
    monkeypatch.setattr(
        format_code.CodeFormatter, "normalize_whitespace", lambda self: True
    )
    monkeypatch.setattr(format_code.CodeFormatter, "format_python", lambda self: True)
    monkeypatch.setattr(format_code.CodeFormatter, "format_frontend", lambda self: True)
    monkeypatch.setattr(
        format_code.CodeFormatter, "format_templates", lambda self: True
    )
    monkeypatch.setattr(format_code.CodeFormatter, "format_docs", lambda self: True)
    monkeypatch.setattr(format_code.CodeFormatter, "format_yaml", lambda self: True)
    monkeypatch.setattr(format_code.CodeFormatter, "print_summary", lambda self: None)
    monkeypatch.setattr(sys, "argv", ["format_code.py"])

    with pytest.raises(SystemExit) as exc:
        runpy.run_path("scripts/format_code.py", run_name="__main__")
    assert exc.value.code == 0
