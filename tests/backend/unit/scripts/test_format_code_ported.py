"""Ported coverage tests for scripts/format_code.py.

Only the cases that exercise code paths not already covered by
``test_format_code.py`` are retained here.
"""

from __future__ import annotations

import io
import sys
from unittest import mock

from tests.backend.unit.scripts._helpers import load_script_module

format_code = load_script_module("format_code.py", "format_code_ported_under_test")


class CaptureStdout:
    def __enter__(self):
        self._stdout = sys.stdout
        sys.stdout = self._stringio = io.StringIO()
        return self._stringio

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self._stdout


def make_sequential_mock(call_map):
    counters = {k: 0 for k in call_map}

    def side_effect(cmd, *args, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "git" in cmd_str and "ls-files" in cmd_str:
            m = mock.Mock()
            m.returncode = 0
            m.stdout = "collab/main.py\n"
            m.stderr = ""
            return m
        if "prettier" in cmd_str or "npm list" in cmd_str:
            m = mock.Mock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m
        for tool, responses in call_map.items():
            if tool in cmd_str:
                idx = counters.get(tool, 0)
                if idx < len(responses):
                    counters[tool] = idx + 1
                    rc, out, err = responses[idx]
                else:
                    rc, out, err = responses[-1]
                m = mock.Mock()
                m.returncode = rc
                m.stdout = out
                m.stderr = err
                return m
        m = mock.Mock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    return side_effect


BACKEND_TOOLS = [
    "Import sorting (isort)",
    "Code formatting (black)",
    "Docstring formatting (docformatter)",
    "Ruff linting & fixing",
    "Final linting (flake8)",
]


def test_scenario_clean(monkeypatch):
    monkeypatch.setattr(
        format_code.CodeFormatter,
        "_get_targets",
        lambda self, ext, default: ["collab/main.py"] if ".py" in ext else [],
    )
    call_map = {
        "ruff": [(0, "", "")],
        "isort": [(0, "", "")],
        "black": [(0, "", "")],
        "docformatter": [(0, "", "")],
        "flake8": [(0, "", "")],
        "yamllint": [(0, "", "")],
    }
    with mock.patch("subprocess.run", side_effect=make_sequential_mock(call_map)):
        with mock.patch("sys.argv", ["format_code.py", "--backend"]):
            with CaptureStdout() as out:
                format_code.main()
            output = out.getvalue()

    for tool in BACKEND_TOOLS:
        assert f"✅ {tool} - SUCCESS" in output


def test_run_tool_step_no_check_cmd():
    formatter = format_code.CodeFormatter()
    formatter.check_only = False

    fail_result = mock.Mock(stdout="error", stderr="", returncode=1)
    with mock.patch.object(formatter, "_exec", return_value=(False, fail_result)):
        with CaptureStdout():
            result = formatter._run_tool_step("Broken tool", ["fix"], None, "S", 1, 1)

    assert result is False
    assert any("Broken tool" in str(entry) for entry in formatter.failed_tools)
