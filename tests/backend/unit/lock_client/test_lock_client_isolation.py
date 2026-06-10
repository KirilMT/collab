"""Comprehensive tests for production/test environment isolation.

This test module ensures that:
1. Test daemons never interfere with production daemons
2. Test discovery filters don't match production watcher processes
3. PID files are isolated and scoped by namespace
4. Tests cannot kill or affect production locks

This prevents the recurring issue where pytest runs would terminate the
production daemon or cause status flips due to cross-namespace discovery.
"""

import sys
import types
from pathlib import Path

from ._helpers import load_lock_client_module


def test_namespace_filter_rejects_mismatched_watcher(monkeypatch, tmp_path):
    """Discovery must reject watcher processes without matching PID namespace tag."""
    mod = load_lock_client_module()

    # Set up test client with isolated PID file
    test_pid_file = str(tmp_path / "test.pid")
    monkeypatch.setattr(mod, "PID_FILE", test_pid_file)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")

    # Mock psutil to return a watcher WITHOUT the --pid-file tag
    # (simulating a legacy or production watcher)
    class FakeProc:
        def __init__(self, pid, cmdline):
            self.info = {"pid": pid, "cmdline": cmdline}

    def fake_process_iter(attrs=("pid", "cmdline")):
        return [
            # Production watcher WITHOUT namespace tag - should be rejected
            FakeProc(
                5555,
                [
                    "python",
                    ".collab/pycharm/live_locks_watcher.py",
                    # NO --pid-file tag
                ],
            ),
            # Test watcher WITH matching namespace tag - should be accepted
            FakeProc(
                6666,
                [
                    "python",
                    ".collab/pycharm/live_locks_watcher.py",
                    "--pid-file",
                    test_pid_file,
                ],
            ),
        ]

    fake_psutil = types.SimpleNamespace(process_iter=fake_process_iter)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    client = mod.LockClient(local_only=True)
    found_pids = client._discover_running_watchers()

    # Should ONLY find the namespaced test watcher (6666)
    # Should REJECT the untagged production watcher (5555)
    assert 6666 in found_pids, "Namespaced test watcher must be discovered"
    assert (
        5555 not in found_pids
    ), "Unnamespaced production watcher must be filtered out in test mode"


def test_pid_file_namespace_tag_round_trip(monkeypatch, tmp_path):
    """PID metadata (incl.

    the --pid-file namespace tag) survives a real write/read.
    """
    mod = load_lock_client_module()

    test_pid_file = str(tmp_path / "roundtrip.pid")
    test_pid = 9999

    monkeypatch.setattr(mod, "PID_FILE", test_pid_file)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    # The watcher's namespace tag is derived from argv at write time.
    monkeypatch.setattr(
        mod.sys,
        "argv",
        ["-m", "collab.lock_client", "watch", "--pid-file", test_pid_file],
    )

    # Write via the real implementation, then read it back.
    mod.LockClient._write_pid(test_pid)
    assert mod.LockClient._read_pid() == test_pid

    client = mod.LockClient(local_only=True)
    meta = client._read_pid_file()
    assert isinstance(meta, dict)
    assert meta["pid"] == test_pid
    assert "--pid-file" in meta["cmdline"]
    assert test_pid_file in meta["cmdline"]
    # The persisted cmdline must be recognised as belonging to this namespace.
    assert client._extract_pid_file_from_cmdline(meta["cmdline"]) == test_pid_file
    assert client._cmdline_matches_current_pid_namespace(meta["cmdline"]) is True


def test_conflicting_pid_files_are_independent(monkeypatch, tmp_path):
    """_remove_pid on one namespace must not affect another PID file."""
    mod = load_lock_client_module()

    # COLLAB_TEST_MODE=1 makes _remove_pid a no-op; exercise the real removal path.
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")

    test_pid_file = str(tmp_path / "test_independent.pid")
    prod_pid_file = str(tmp_path / "prod_independent.pid")

    # Write the production PID file via the real writer.
    monkeypatch.setattr(mod, "PID_FILE", prod_pid_file)
    mod.LockClient._write_pid(2222)
    assert Path(prod_pid_file).exists()

    # Write the test PID file via the real writer, then remove it.
    monkeypatch.setattr(mod, "PID_FILE", test_pid_file)
    mod.LockClient._write_pid(1111)
    assert Path(test_pid_file).exists()
    mod.LockClient._remove_pid()

    # The test PID file is gone; the production one is untouched and still valid.
    assert not Path(test_pid_file).exists()
    assert mod.LockClient._read_pid() is None
    assert Path(prod_pid_file).exists()
    monkeypatch.setattr(mod, "PID_FILE", prod_pid_file)
    assert mod.LockClient._read_pid() == 2222


def test_discover_watchers_filters_by_strict_namespace(monkeypatch, tmp_path):
    """In test mode, discover_running_watchers uses strict namespace filtering."""
    mod = load_lock_client_module()

    test_pid_file = str(tmp_path / "strict_ns.pid")
    monkeypatch.setattr(mod, "PID_FILE", test_pid_file)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")

    class FakeProc:
        def __init__(self, pid, cmdline):
            self.info = {"pid": pid, "cmdline": cmdline}

    # Multiple watchers with different or missing namespace tags
    def fake_process_iter(attrs=("pid", "cmdline")):
        return [
            # No tag at all
            FakeProc(1111, ["python", ".collab/pycharm/live_locks_watcher.py"]),
            # Wrong namespace tag
            FakeProc(
                2222,
                [
                    "python",
                    ".collab/pycharm/live_locks_watcher.py",
                    "--pid-file",
                    "/other/path.pid",
                ],
            ),
            # Correct namespace tag
            FakeProc(
                3333,
                [
                    "python",
                    ".collab/pycharm/live_locks_watcher.py",
                    "--pid-file",
                    test_pid_file,
                ],
            ),
        ]

    fake_psutil = types.SimpleNamespace(process_iter=fake_process_iter)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    client = mod.LockClient(local_only=True)
    found = client._discover_running_watchers()

    # Only correct namespace should match
    assert 3333 in found
    assert 1111 not in found
    assert 2222 not in found


def test_extract_pid_file_from_cmdline_parsing(monkeypatch, tmp_path):
    """Verify _extract_pid_file_from_cmdline correctly parses namespace tags."""
    mod = load_lock_client_module()

    client = mod.LockClient(local_only=True)

    # Test various cmdline formats
    test_path = str(tmp_path / "test.pid")

    # Standard format with --pid-file flag
    cmdline = f"python watcher.py --pid-file {test_path}"
    extracted = client._extract_pid_file_from_cmdline(cmdline)
    assert extracted == test_path

    # Without --pid-file flag
    cmdline_no_flag = "python watcher.py"
    extracted_no_flag = client._extract_pid_file_from_cmdline(cmdline_no_flag)
    assert extracted_no_flag is None

    # With other flags before --pid-file
    cmdline_multi = f"python watcher.py --verbose --pid-file {test_path} --timeout 30"
    extracted_multi = client._extract_pid_file_from_cmdline(cmdline_multi)
    assert extracted_multi == test_path


def test_cmdline_matches_current_pid_namespace_validation(monkeypatch, tmp_path):
    """Verify namespace matching only accepts properly scoped watchers."""
    mod = load_lock_client_module()

    test_pid_file = str(tmp_path / "validation.pid")
    monkeypatch.setattr(mod, "PID_FILE", test_pid_file)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")

    client = mod.LockClient(local_only=True)

    # Matching namespace - should return True
    matching_cmdline = f"python watcher.py --pid-file {test_pid_file}"
    assert client._cmdline_matches_current_pid_namespace(matching_cmdline)

    # Mismatched namespace - should return False
    other_pid_file = str(tmp_path / "other.pid")
    mismatched_cmdline = f"python watcher.py --pid-file {other_pid_file}"
    assert not client._cmdline_matches_current_pid_namespace(mismatched_cmdline)

    # No namespace tag in test mode - should return False (strict test isolation)
    no_tag_cmdline = "python watcher.py"
    assert not client._cmdline_matches_current_pid_namespace(no_tag_cmdline)


def test_isolation_prevents_cross_namespace_discovery(monkeypatch, tmp_path):
    """Ensure test discovery cannot find production processes."""
    mod = load_lock_client_module()

    test_pid_file = str(tmp_path / "test_ns.pid")
    prod_pid_file = str(tmp_path / "prod_ns.pid")

    # Simulate two independent namespaces
    monkeypatch.setattr(mod, "PID_FILE", test_pid_file)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")

    class FakeProc:
        def __init__(self, pid, cmdline):
            self.info = {"pid": pid, "cmdline": cmdline}

    # One client in test mode
    def fake_iter_test(attrs=("pid", "cmdline")):
        return [
            # Test watcher (namespaced)
            FakeProc(
                11111,
                [
                    "python",
                    ".collab/pycharm/live_locks_watcher.py",
                    "--pid-file",
                    test_pid_file,
                ],
            ),
            # Production watcher (different namespace)
            FakeProc(
                22222,
                [
                    "python",
                    ".collab/pycharm/live_locks_watcher.py",
                    "--pid-file",
                    prod_pid_file,
                ],
            ),
        ]

    fake_psutil = types.SimpleNamespace(process_iter=fake_iter_test)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    # Test discovery should only find its own namespaced process
    client = mod.LockClient(local_only=True)
    found = client._discover_running_watchers()

    assert 11111 in found, "Test watcher in same namespace must be found"
    assert (
        22222 not in found
    ), "Production watcher in different namespace must be filtered"


def test_pid_namespace_isolation_env_override(monkeypatch, tmp_path):
    """COLLAB_PID_FILE override must win over the state-dir derived PID path."""
    mod = load_lock_client_module()

    isolated_pid = str(tmp_path / "isolated.pid")
    monkeypatch.setenv("COLLAB_PID_FILE", isolated_pid)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")

    # The real resolver must honour the env override regardless of state dir/agent.
    resolved = mod.agent_identity.resolve_daemon_pid_path(str(tmp_path), "agent-123")
    assert resolved == isolated_pid

    # Without the override, the path is derived from the state dir (different file).
    monkeypatch.delenv("COLLAB_PID_FILE", raising=False)
    fallback = mod.agent_identity.resolve_daemon_pid_path(str(tmp_path), None)
    assert fallback != isolated_pid
    assert fallback.startswith(str(tmp_path))


def test_namespace_tag_propagation_in_watcher_spawn(monkeypatch, tmp_path):
    """daemon_start must spawn the watcher with the current --pid-file namespace tag."""
    mod = load_lock_client_module()

    test_pid_file = str(tmp_path / "spawn_test.pid")
    monkeypatch.setattr(mod, "PID_FILE", test_pid_file)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    # Use the simpler Unix spawn branch and keep the startup wait instant.
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod.time, "sleep", lambda _x: None)
    monkeypatch.setattr(
        mod.LockClient, "_read_pid", staticmethod(lambda strict=False: None)
    )

    client = mod.LockClient(local_only=True)
    monkeypatch.setattr(client, "_get_parent_ide_pid", lambda: (None, None))
    monkeypatch.setattr(client, "_write_pid", lambda *a, **k: None)
    monkeypatch.setattr(client, "_is_process_alive", lambda pid: False)

    captured: dict = {}

    class FakeProc:
        pid = 4321

    def fake_spawn(argv, **kwargs):
        captured["argv"] = list(argv)
        return FakeProc()

    monkeypatch.setattr(mod.safe_subprocess, "spawn_background", fake_spawn)

    client.daemon_start()

    argv = captured["argv"]
    assert "--pid-file" in argv
    # The flag value must be the current namespace PID file.
    assert argv[argv.index("--pid-file") + 1] == test_pid_file
