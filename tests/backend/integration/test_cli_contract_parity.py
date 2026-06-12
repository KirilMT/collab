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
from pathlib import Path

import pytest

# Repo root anchored from this file (tests/backend/integration/<file>.py ->
# parents[3] is the repository root) so subprocesses never depend on the
# caller's CWD. Verified at import time below to fail loudly if the layout
# changes.
_REPO_ROOT = Path(__file__).resolve().parents[3]
assert (
    _REPO_ROOT / "pyproject.toml"
).is_file(), f"Expected repo root with pyproject.toml at {_REPO_ROOT}"


def run_collab_cli(*args: str, expect_success: bool = True) -> tuple[int, str, str]:
    """Execute collab CLI via python -m collab.__main__ and capture results.

    IMPORTANT: Runs in a fully isolated environment so destructive commands
    (release-all, cleanup, etc.) never touch the real Supabase database.

    Args:
        *args: Command arguments (e.g., "active", "status", "file.py")
        expect_success: If True, assert exit code is 0

    Returns:
        Tuple of (exit_code, stdout, stderr)
    """
    cmd = [sys.executable, "-m", "collab.__main__"] + list(args)

    # Build an isolated env that prevents the subprocess from hitting
    # real Supabase even if it re-loads .env (python-dotenv's load_dotenv
    # does NOT override pre-existing env vars by default).
    isolated_env = os.environ.copy()
    isolated_env.update(
        {
            "COLLAB_TEST_MODE": "1",
            "SUPABASE_URL": "http://localhost:54321",
            "SUPABASE_ANON_KEY": "test-anon-key-integration",
            "COLLAB_SILENT_DAEMON": "1",
            "COLLAB_AUTO_START_WATCHER": "0",
        }
    )

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_REPO_ROOT),
        env=isolated_env,
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
            "daemon-restart",
            "history",
            "reconcile",
            "ping",
            "info",
            "logs",
        ]

        for cmd in expected_commands:
            assert cmd in help_output, f"Command '{cmd}' not found in help output"

    def test_version_flag(self) -> None:
        """Verify --version prints the installed version and exits 0."""
        exit_code, stdout, _ = run_collab_cli("--version")
        assert exit_code == 0
        assert "collab-runtime" in stdout

    def test_daemon_restart_command_available(self) -> None:
        """Verify 'daemon-restart' command is registered and exits cleanly."""
        exit_code, _, stderr = run_collab_cli(
            "daemon-restart",
            expect_success=False,
        )
        # daemon-restart may fail in isolated env (no Supabase), but must be a
        # recognized command (argparse must not reject it as an unknown choice)
        # and must not crash with an unhandled traceback.
        assert exit_code in (0, 1)
        assert "invalid choice" not in stderr.lower()
        assert "traceback (most recent call last)" not in stderr.lower()

    def test_ping_command_available(self) -> None:
        """Verify 'ping' command is available."""
        exit_code, stdout, _ = run_collab_cli(
            "ping",
            expect_success=False,
        )
        # ping may fail in isolated env (no real Supabase)
        assert exit_code in (0, 1)
        assert len(stdout) > 0

    def test_info_command_available(self) -> None:
        """Verify 'info' command is available."""
        exit_code, stdout, _ = run_collab_cli(
            "info",
            expect_success=False,
        )
        assert exit_code in (0, 1)
        assert "collab-runtime" in stdout

    def test_logs_command_available(self) -> None:
        """Verify 'logs' command is available."""
        exit_code, stdout, _ = run_collab_cli(
            "logs",
            expect_success=False,
        )
        # logs may succeed (log file exists) or fail (no log file)
        assert exit_code in (0, 1)


class TestBackwardCompatibilityInvocation:
    """Verify all documented invocation patterns work identically."""

    def test_run_py_entrypoint(self) -> None:
        """Verify 'python run.py' backward compatibility entrypoint."""
        result = subprocess.run(
            [sys.executable, str(_REPO_ROOT / "run.py"), "--help"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
        )
        assert result.returncode == 0
        assert len(result.stdout) > 0

    def test_collab_console_script_exists(self) -> None:
        """Verify 'collab' console script is registered in package."""
        # Check pyproject.toml registration
        with open(_REPO_ROOT / "pyproject.toml") as f:
            content = f.read()
            assert 'collab = "collab.lock_client:main"' in content


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
        # Should fail (non-zero exit) with a meaningful argparse error on stderr
        assert exit_code != 0
        assert stderr.strip(), "expected an error message on stderr"
        lowered = stderr.lower()
        assert (
            "invalid choice" in lowered
            or "usage" in lowered
            or "nonexistent-command" in lowered
        )


class TestCLIDashboardAssetIntegrity:
    """Verify dashboard assets are properly bundled in package."""

    def test_dashboard_index_html_exists(self) -> None:
        """Verify dashboard HTML asset is present."""
        dashboard_path = _REPO_ROOT / "collab" / "dashboard" / "index.html"
        assert dashboard_path.exists(), f"Dashboard not found at {dashboard_path}"

    def test_dashboard_command_available(self) -> None:
        """Verify dashboard command is registered."""
        _, help_output, _ = run_collab_cli("--help")
        assert "dashboard" in help_output.lower()
