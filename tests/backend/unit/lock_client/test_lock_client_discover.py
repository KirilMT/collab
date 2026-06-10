import os
import sys
import types

from ._helpers import load_lock_client_module, patch_subprocess


def test_discover_running_watchers_with_psutil_workspace_match(monkeypatch):
    mod = load_lock_client_module()

    class FakeProc:
        def __init__(self, pid, cmdline):
            self.info = {"pid": pid, "cmdline": cmdline}

    def fake_process_iter(attrs=("pid", "cmdline")):
        # One matching watcher (references project root), one ignored (current pid)
        return [
            FakeProc(
                4242,
                [
                    mod._PROJECT_ROOT,
                    ".collab/pycharm/live_locks_watcher.py",
                    "--pid-file",
                    mod.PID_FILE,
                ],
            ),
            FakeProc(os.getpid(), ["python", "other"]),
        ]

    fake_psutil = types.SimpleNamespace(process_iter=fake_process_iter)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    client = mod.LockClient(local_only=True)
    found = client._discover_running_watchers()
    assert isinstance(found, list)
    assert 4242 in found


# -- appended migrated process-helper tests from extra split --
mod = load_lock_client_module()


def test_is_process_alive_win32_no_psutil_ctypes_fallback(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "psutil", raising=False)

    fake_ctypes = types.SimpleNamespace(
        windll=types.SimpleNamespace(
            kernel32=types.SimpleNamespace(
                OpenProcess=lambda a, b, c: 123,
                GetExitCodeProcess=lambda h, ec: (setattr(ec, "value", 259) or True),
                CloseHandle=lambda h: None,
                GetLastError=lambda: 0,
            )
        ),
        c_ulong=lambda v: type("ULong", (), {"value": v})(),
        byref=lambda x: x,
        Structure=type("Structure", (), {}),
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    result = mod.LockClient._is_process_alive(os.getpid())
    assert result is True


def test_is_process_alive_win32_process_exited(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "psutil", raising=False)

    fake_ctypes = types.SimpleNamespace(
        windll=types.SimpleNamespace(
            kernel32=types.SimpleNamespace(
                OpenProcess=lambda a, b, c: 123,
                GetExitCodeProcess=lambda h, ec: (setattr(ec, "value", 1) or True),
                CloseHandle=lambda h: None,
                GetLastError=lambda: 0,
            )
        ),
        c_ulong=lambda v: type("ULong", (), {"value": v})(),
        byref=lambda x: x,
        Structure=type("Structure", (), {}),
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    result = mod.LockClient._is_process_alive(99999)
    assert result is False


def test_is_process_alive_win32_access_denied(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "psutil", raising=False)

    class FakeKernel32:
        def OpenProcess(self, a, b, c):
            return 0

        def GetLastError(self):
            return 5

        def CloseHandle(self, h):
            pass

        def GetExitCodeProcess(self, h, ec):
            return False

    fake_ctypes = types.SimpleNamespace(
        windll=types.SimpleNamespace(kernel32=FakeKernel32()),
        c_ulong=lambda v: type("ULong", (), {"value": v})(),
        byref=lambda x: x,
        Structure=type("Structure", (), {}),
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    result = mod.LockClient._is_process_alive(4)
    assert result is True


def test_is_process_alive_win32_tasklist_fallback(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    # Force ImportError on every ``import psutil`` so the psutil fast paths are
    # skipped and we genuinely reach the tasklist fallback.
    monkeypatch.setitem(sys.modules, "psutil", None)

    class FailKernel32:
        def OpenProcess(self, a, b, c):
            raise Exception("ctypes failing")

        def GetLastError(self):
            return 0

    fake_ctypes = types.SimpleNamespace(
        windll=types.SimpleNamespace(kernel32=FailKernel32()),
        c_ulong=lambda v: type("ULong", (), {"value": v})(),
        byref=lambda x: x,
        Structure=type("Structure", (), {}),
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    # The real fallback (platform_probe.is_pid_alive_tasklist) runs the tasklist
    # query through safe_subprocess/platform_probe -> subprocess_bridge.run.
    def fake_run(cmd, **kw):
        return types.SimpleNamespace(
            stdout=f"python.exe {os.getpid()} Console 1 12,345 K\n",
            returncode=0,
        )

    patch_subprocess(monkeypatch, run=fake_run)

    result = mod.LockClient._is_process_alive(os.getpid())
    assert result is True


def test_is_process_alive_unix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: None)
    result = mod.LockClient._is_process_alive(12345)
    assert result is True


def test_is_process_alive_unix_dead(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def raise_lookup(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(mod.os, "kill", raise_lookup)
    result = mod.LockClient._is_process_alive(99999)
    assert result is False


def test_discover_running_watchers_no_psutil_win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    # Force ImportError on ``import psutil`` so the psutil fast path is skipped
    # and discovery falls back to the Windows tasklist enumeration branch.
    monkeypatch.setitem(sys.modules, "psutil", None)

    # The tasklist branch yields a candidate PID that is NOT the current process
    # (os.getpid() candidates are filtered out as self).
    monkeypatch.setattr(mod.platform_probe, "iter_tasklist_python_pids", lambda: [555])

    client = mod.LockClient(local_only=True)
    # A real watcher cmdline: matches the watcher heuristic (collab + watch),
    # references the workspace (.collab) and carries the current --pid-file tag.
    cmdline = (
        "python -m collab.lock_client watch .collab " f'--pid-file "{mod.PID_FILE}"'
    )
    monkeypatch.setattr(client, "_get_cmdline_for_pid", lambda pid: cmdline)

    result = client._discover_running_watchers()
    assert result == [555]


def test_discover_running_watchers_unix_no_psutil(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    # Force ImportError on ``import psutil`` so discovery uses the ps fallback.
    monkeypatch.setitem(sys.modules, "psutil", None)

    monkeypatch.setattr(
        mod.platform_probe,
        "ps_pid_cmd_csv",
        lambda: "12345 python /path/.collab/core/lock_client.py watch\n",
    )

    client = mod.LockClient(local_only=True)
    # In COLLAB_TEST_MODE a cmdline WITHOUT --pid-file is rejected, so the
    # discovered watcher must carry the current --pid-file namespace tag.
    cmdline = (
        "python /path/.collab/core/lock_client.py watch " f'--pid-file "{mod.PID_FILE}"'
    )
    monkeypatch.setattr(client, "_get_cmdline_for_pid", lambda pid: cmdline)

    result = client._discover_running_watchers()
    assert 12345 in result


def test_get_process_info_local_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    client = mod.LockClient(local_only=True)
    name, ppid = client._get_process_info_local(12345)
    assert name is None and ppid is None


def test_get_process_info_local_no_wmic_tasklist_fallback(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "psutil", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda cmd: None if cmd == "wmic" else cmd)

    def fake_run(cmd, **kw):
        return types.SimpleNamespace(
            stdout='"python.exe","12345","Console","1","12345 K"\n',
            returncode=0,
        )

    patch_subprocess(monkeypatch, run=fake_run)

    client = mod.LockClient(local_only=True)
    name = client._get_process_name_via_tasklist(12345)
    assert name == "python.exe"
