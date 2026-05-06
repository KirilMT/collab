"""Root configuration for pytest - handles environment setup and test isolation.

This module ensures consistent test execution across all test directories by:
- Loading environment variables from .env
- Setting global TESTING mode to prevent external API calls
- Configuring collab test mode isolation
"""

import atexit
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

# Load .env file BEFORE any test collection to ensure all test fixtures have
# access to configured environment variables (SUPABASE_URL, credentials, etc.)
load_dotenv()

# Set TESTING globally for all pytest runs to ensure any code checking
# os.environ["TESTING"] behaves safely in test mode
os.environ["TESTING"] = "1"

# Global collab test isolation to prevent repository-wide pytest runs from
# interfering with production watcher daemons or real lock states
_collab_test_runtime_dir = tempfile.mkdtemp(prefix="collab_pytest_")
os.environ.setdefault("COLLAB_RUNTIME_DIR", _collab_test_runtime_dir)
os.environ.setdefault("COLLAB_TEST_MODE", "1")


def _cleanup_root_coverage_artifacts() -> None:
    """Remove root-level coverage artifacts after normal pytest runs.

    The validation pipeline sets COLLAB_KEEP_ROOT_COVERAGE=1 so coverage data
    remains available for subsequent `coverage report`/`diff-cover` commands.
    """
    if os.getenv("COLLAB_KEEP_ROOT_COVERAGE") == "1":
        return

    root = Path(__file__).resolve().parent
    for artifact in root.glob(".coverage*"):
        try:
            artifact.unlink()
        except Exception:
            pass

    coverage_xml = root / "coverage.xml"
    if coverage_xml.exists():
        try:
            coverage_xml.unlink()
        except Exception:
            pass


atexit.register(_cleanup_root_coverage_artifacts)

# Configure pytest markers for organized test categorization
pytest_plugins: list = []


def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line(
        "markers", "unit: mark test as a unit test (fast, isolated)"
    )
    config.addinivalue_line(
        "markers",
        "integration: mark test as an integration test (requires external services)",
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow (deferred in quick mode)"
    )
    config.addinivalue_line("markers", "daemon: mark test as daemon-related")
    config.addinivalue_line("markers", "watcher: mark test as watcher-related")
