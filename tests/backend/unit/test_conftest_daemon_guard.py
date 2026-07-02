"""Unit tests for the pytest session daemon guard (#183)."""

from __future__ import annotations

import tests.conftest as conf


def test_is_test_watcher_cmdline_lock_client_watch_in_test_ns():
    cmd = (
        r"pythonw.exe C:\Users\x\AppData\Local\Temp\collab_test_abc\..\ "
        r"lock_client.py watch --daemon --pid-file "
        r"C:\Users\x\AppData\Local\Temp\collab_test_abc\daemon.pid"
    )
    # Normalize to a realistic form used on Windows
    cmd = (
        "pythonw.exe lock_client.py watch --daemon "
        "--pid-file C:\\Users\\x\\AppData\\Local\\Temp\\collab_test_abc\\daemon.pid"
    )
    assert conf._is_test_watcher_cmdline(cmd) is True


def test_is_test_watcher_cmdline_live_locks_watcher_in_pytest_of():
    cmd = (
        "python -m collab.live_locks_watcher "
        "--pid-file /tmp/pytest-of-user/pytest-0/test_x/daemon.pid"
    )
    assert conf._is_test_watcher_cmdline(cmd) is True


def test_is_test_watcher_cmdline_python_m_collab_watch():
    cmd = (
        "python -m collab watch --daemon "
        "--pid-file /tmp/collab_pytest_xyz/daemon.pid"
    )
    assert conf._is_test_watcher_cmdline(cmd) is True


def test_is_test_watcher_cmdline_ignores_production_daemon():
    cmd = (
        "python lock_client.py watch --daemon "
        "--pid-file C:\\Users\\dev\\project\\.collab\\daemon.pid"
    )
    assert conf._is_test_watcher_cmdline(cmd) is False


def test_is_test_watcher_cmdline_ignores_unrelated_python():
    assert (
        conf._is_test_watcher_cmdline("python -c import time; time.sleep(8)") is False
    )
    assert conf._is_test_watcher_cmdline("") is False


def test_is_test_namespace_path_helpers():
    assert conf._is_test_namespace_path("/tmp/collab_test_foo/x") is True
    assert conf._is_test_namespace_path("C:/proj/.collab/daemon.pid") is False


def test_print_helpers_do_not_crash_on_empty_list():
    # Smoke: listing should return a list (may be empty on clean machines).
    result = conf.list_orphan_test_watcher_pids()
    assert isinstance(result, list)


def test_iter_pythonish_processes_psutil_fast_path():
    """Psutil fast path returns (pid, cmdline) pairs including this process."""
    rows = conf._iter_pythonish_processes_psutil()
    # psutil is a declared runtime dependency, so the fast path must be active.
    assert rows is not None
    assert isinstance(rows, list)
    pids = {pid for pid, _ in rows}
    import os

    assert os.getpid() in pids


def test_iter_pythonish_processes_uses_fast_path(monkeypatch):
    """When psutil succeeds, the subprocess enumeration is never invoked."""
    sentinel = [(1234, "python -m collab watch --pid-file /tmp/collab_test_x/d.pid")]
    monkeypatch.setattr(conf, "_iter_pythonish_processes_psutil", lambda: sentinel)

    def _boom(*_a, **_k):  # pragma: no cover - must not be called
        raise AssertionError("subprocess fallback should not run when psutil works")

    monkeypatch.setattr(conf.subprocess, "run", _boom)
    assert conf._iter_pythonish_processes() == sentinel


def test_iter_pythonish_processes_falls_back_when_psutil_none(monkeypatch):
    """When psutil is unavailable, enumeration falls back to subprocess scan."""
    monkeypatch.setattr(conf, "_iter_pythonish_processes_psutil", lambda: None)
    rows = conf._iter_pythonish_processes()
    assert isinstance(rows, list)
