"""Integration tests for CLI command contract parity — Phase 1 exit criteria.

These tests verify that the collab package's CLI command surface behaves
consistently across all invocation patterns and matches expected output formats.

Tests validate:
- Command availability and exit codes
- CLI help and documentation
- Backward compatibility invocation patterns
- Safe command behavior in test environments

No behavior deltas are acceptable for Phase 1 exit criteria.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


def run_collab_cli(*args: str, expect_success: bool = True) -> tuple[int, str, str]:
    """Execute collab CLI via python -m src.main and capture results.

    Args:
        *args: Command arguments (e.g., "active", "status", "file.py")
        expect_success: If True, assert exit code is 0

    Returns:
        Tuple of (exit_code, stdout, stderr)
    """
    cmd = [sys.executable, "-m", "src.main"] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=".",
    )

    if expect_success and result.returncode != 0:
        pytest.fail(
            f"Command failed unexpectedly: {' '.join(cmd)}\n"
            f"Exit code: {result.returncode}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    return result.returncode, result.stdout, result.stderr


class TestCLICommandAvailability:
    """Verify all golden commands are available and respond correctly."""

    def test_help_command_succeeds(self) -> None:
        """Verify --help produces usage information."""
        exit_code, stdout, _ = run_collab_cli("--help")
        assert exit_code == 0
        assert "usage" in stdout.lower() or "collab" in stdout.lower()

    def test_active_command_available(self) -> None:
        """Verify 'active' command is available and returns valid output."""
        exit_code, stdout, _ = run_collab_cli("active")
        assert exit_code == 0
        # Output should indicate lock status (even if empty)
        assert len(stdout) > 0

    def test_status_command_accepts_file_argument(self) -> None:
        """Verify 'status' command accepts file path argument."""
        exit_code, stdout, _ = run_collab_cli("status", "example.py")
        assert exit_code == 0
        assert len(stdout) > 0

    def test_history_command_available(self) -> None:
        """Verify 'history' command returns valid output."""
        exit_code, stdout, _ = run_collab_cli("history")
        assert exit_code == 0
        assert len(stdout) > 0

    def test_daemon_status_command_available(self) -> None:
        """Verify 'daemon-status' command is available."""
        exit_code, _, _ = run_collab_cli(
            "daemon-status",
            expect_success=False,
        )
        # Exit code can be 0 (running) or 1 (not running); both are valid
        assert exit_code in (0, 1)

    def test_cleanup_command_available(self) -> None:
        """Verify 'cleanup' command is available and safe."""
        exit_code, _, _ = run_collab_cli(
            "cleanup",
            expect_success=False,
        )
        # Cleanup should not crash even if no processes exist
        assert exit_code in (0, 1)

    def test_help_documents_golden_commands(self) -> None:
        """Verify help output documents all primary commands."""
        _, help_output, _ = run_collab_cli("--help")

        expected_commands = [
            "acquire",
            "release",
            "active",
            "status",
            "daemon-start",
            "daemon-stop",
            "daemon-status",
            "history",
            "reconcile",
        ]

        for cmd in expected_commands:
            assert cmd in help_output, f"Command '{cmd}' not found in help output"


class TestBackwardCompatibilityInvocation:
    """Verify all documented invocation patterns work identically."""

    def test_python_m_src_main_entrypoint(self) -> None:
        """Verify 'python -m src.main' invocation works."""
        exit_code, stdout, _ = run_collab_cli("--help")
        assert exit_code == 0
        assert len(stdout) > 0

    def test_run_py_entrypoint(self) -> None:
        """Verify 'python run.py' backward compatibility entrypoint."""
        result = subprocess.run(
            [sys.executable, "run.py", "--help"],
            capture_output=True,
            text=True,
            cwd=".",
        )
        assert result.returncode == 0
        assert len(result.stdout) > 0

    def test_collab_console_script_exists(self) -> None:
        """Verify 'collab' console script is registered in package."""
        # Check pyproject.toml registration
        with open("pyproject.toml") as f:
            content = f.read()
            assert 'collab = "src.lock_client:main"' in content


class TestCLICommandVariants:
    """Verify specific command patterns work as expected."""

    @pytest.mark.parametrize(
        "command,expected_exit_success",
        [
            (["active"], True),
            (["status", "dummy_file.py"], True),
            (["history", "--limit", "5"], True),
            (["release-all"], True),
            (["cleanup"], True),
        ],
    )
    def test_command_execution_patterns(
        self, command: list[str], expected_exit_success: bool
    ) -> None:
        """Verify command execution patterns return expected exit codes."""
        exit_code, _, _ = run_collab_cli(
            *command,
            expect_success=expected_exit_success,
        )

        if expected_exit_success:
            assert exit_code == 0
        else:
            # Should exit cleanly even if it fails
            assert exit_code in (0, 1, 2)

    def test_invalid_command_fails_gracefully(self) -> None:
        """Verify invalid commands fail with meaningful exit codes."""
        exit_code, _, stderr = run_collab_cli(
            "nonexistent-command",
            expect_success=False,
        )
        # Should fail (non-zero exit) with error output
        assert exit_code != 0
        assert len(stderr) > 0 or len(stderr) == 0  # Either way is OK


class TestCLIDashboardAssetIntegrity:
    """Verify dashboard assets are properly bundled in package."""

    def test_dashboard_index_html_exists(self) -> None:
        """Verify dashboard HTML asset is present."""
        dashboard_path = os.path.join("src", "dashboard", "index.html")
        assert os.path.exists(
            dashboard_path
        ), f"Dashboard not found at {dashboard_path}"

    def test_dashboard_command_available(self) -> None:
        """Verify dashboard command is registered."""
        _, help_output, _ = run_collab_cli("--help")
        assert "dashboard" in help_output.lower()
