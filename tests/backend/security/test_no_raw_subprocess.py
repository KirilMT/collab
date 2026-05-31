"""Ensure production code does not add raw subprocess calls outside wrappers."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[3] / "collab"
_ALLOWED = frozenset(
    {"safe_subprocess.py", "platform_probe.py", "subprocess_bridge.py"}
)
_PATTERN = re.compile(r"subprocess\.(run|check_output|Popen|call|check_call)\(")


@pytest.mark.security
def test_no_raw_subprocess_outside_wrappers():
    violations: list[str] = []
    for path in sorted(_SRC.glob("*.py")):
        if path.name in _ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _PATTERN.search(line) and "safe_subprocess" not in line:
                violations.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not violations, "raw subprocess calls:\n" + "\n".join(violations)
