"""CI-only packaging smoke test.

Builds an sdist+wheel and verifies the produced wheel can be installed into an ephemeral
venv and that the installed package exposes the `collab` import surface and a non-empty
`__version__` string. Also asserts dashboard static assets ship in the wheel and survive
install into site-packages.

This test is intentionally marked `packaging` and should only run in CI.
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from collab import dashboard_server


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
    wheels = sorted(dist_dir.glob("*.whl"))
    assert wheels, "no wheel produced"
    wheel_path = wheels[-1]

    required = dashboard_server.shipped_dashboard_relative_paths(
        str(repo_root / "collab")
    )
    assert "dashboard-format.js" in required
    wheel_missing = dashboard_server.missing_wheel_dashboard_files(
        str(wheel_path), required
    )
    assert wheel_missing == (), f"wheel missing dashboard files: {wheel_missing}"
    with zipfile.ZipFile(wheel_path) as archive:
        names = [n.replace("\\", "/") for n in archive.namelist() if "dashboard" in n]
    assert "collab/dashboard/index.html" in names
    assert "collab/dashboard/dashboard-format.js" in names

    subprocess.check_call(
        [str(py), "-m", "pip", "install", "--upgrade", "pip"]
    )  # ensure modern pip
    subprocess.check_call([str(py), "-m", "pip", "install", str(wheel_path)])

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

    # Installed site-packages must include dashboard JS (regression for 0.4.1 wheel gap)
    installed_check = subprocess.check_output(
        [
            str(py),
            "-c",
            (
                "import collab, pathlib; "
                "root = pathlib.Path(collab.__file__).resolve().parent; "
                "fmt = root / 'dashboard' / 'dashboard-format.js'; "
                "idx = root / 'dashboard' / 'index.html'; "
                "assert fmt.is_file(), fmt; "
                "assert idx.is_file(), idx; "
                "print('dashboard assets ok')"
            ),
        ],
        text=True,
    )
    assert "dashboard assets ok" in installed_check
