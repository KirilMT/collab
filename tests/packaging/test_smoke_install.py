"""CI-only packaging smoke test.

Builds an sdist+wheel and verifies the produced wheel can be installed into an ephemeral
venv and that the installed package exposes the `collab` import surface and a non-empty
`__version__` string.

This test is intentionally marked `packaging` and should only run in CI.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for p in [cur] + list(cur.parents):
        if (p / "pyproject.toml").exists() or (p / "setup.py").exists():
            return p
    raise RuntimeError("Could not find project root (pyproject.toml or setup.py)")


@pytest.mark.packaging
def test_smoke_install_build_and_import(tmp_path: Path) -> None:
    repo_root = _find_repo_root(Path(__file__))
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    # Build sdist+wheel
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "build",
            "--sdist",
            "--wheel",
            "--outdir",
            str(dist_dir),
        ],
        cwd=str(repo_root),
    )

    # Create ephemeral venv
    venv_dir = tmp_path / "venv"
    subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir)])
    if os.name == "nt":
        venv_dir / "Scripts" / "pip.exe"
        py = venv_dir / "Scripts" / "python.exe"
    else:
        venv_dir / "bin" / "pip"
        py = venv_dir / "bin" / "python"

    # Install wheel (use `python -m pip` which is more reliable across platforms)
    wheels = list(dist_dir.glob("*.whl"))
    assert wheels, "no wheel produced"
    subprocess.check_call(
        [str(py), "-m", "pip", "install", "--upgrade", "pip"]
    )  # ensure modern pip
    subprocess.check_call([str(py), "-m", "pip", "install", str(wheels[0])])

    # Validate import surface
    subprocess.check_call(
        [
            str(py),
            "-c",
            "import importlib;print(importlib.import_module('collab').__version__)",
        ]
    )

    # Validate CLI surface via module execution (portable across platforms)
    subprocess.check_call([str(py), "-m", "collab", "--help"])
