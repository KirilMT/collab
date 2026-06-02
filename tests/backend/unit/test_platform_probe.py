"""Unit tests for platform_probe (Phase 5.2)."""

from __future__ import annotations

import types

import pytest

from collab import platform_probe
from tests.backend.subprocess_testing import patch_subprocess


def _completed(stdout: str = "", returncode: int = 0) -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=returncode, stdout=stdout)


def test_require_pid_rejects_invalid():
    with pytest.raises(ValueError):
        platform_probe.get_cmdline(0)


def test_tasklist_image_rejects_unknown(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")
    with pytest.raises(ValueError):
        platform_probe.tasklist_csv_for_image("cmd.exe")


def test_get_cmdline_unix_uses_procfs_helper(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "linux")
    monkeypatch.setattr(platform_probe, "wmic_cmdline", lambda _pid: None)
    monkeypatch.setattr(platform_probe, "powershell_cmdline", lambda _pid: None)
    monkeypatch.setattr(platform_probe, "_unix_cmdline", lambda _pid: "python -m watch")
    assert platform_probe.get_cmdline(99) == "python -m watch"


def test_get_cmdline_unix_procfs_null_separated(monkeypatch):
    """Parse /proc/pid/cmdline null-separated argv via the public get_cmdline API."""
    import builtins

    monkeypatch.setattr(platform_probe.sys, "platform", "linux")
    monkeypatch.setattr(
        platform_probe.os.path,
        "exists",
        lambda p: p == "/proc/555/cmdline",
    )

    class _FH:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"python\x00.collab/core/lock_client.py\x00watch\x00"

    monkeypatch.setattr(builtins, "open", lambda *a, **k: _FH())
    assert platform_probe.get_cmdline(555) == "python .collab/core/lock_client.py watch"


def test_resolve_returns_none_when_executable_missing(monkeypatch):
    monkeypatch.setattr(platform_probe.shutil, "which", lambda _name: None)
    monkeypatch.setattr(platform_probe.safe_subprocess, "is_test_mode", lambda: False)
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")
    assert platform_probe.is_pid_alive_tasklist(42) is False


def test_resolve_uses_abspath_when_which_finds_executable(monkeypatch):
    monkeypatch.setattr(platform_probe.shutil, "which", lambda _name: "tasklist")
    monkeypatch.setattr(platform_probe.os.path, "abspath", lambda p: f"/abs/{p}")
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")

    def _run(argv, **kwargs):
        return _completed(stdout="42")

    patch_subprocess(monkeypatch, run=_run)
    assert platform_probe.is_pid_alive_tasklist(42) is True


def test_resolve_which_exception_returns_none(monkeypatch):
    def _boom(_name):
        raise OSError("which failed")

    monkeypatch.setattr(platform_probe.shutil, "which", _boom)
    monkeypatch.setattr(platform_probe.safe_subprocess, "is_test_mode", lambda: False)
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")
    assert platform_probe.tasklist_csv_for_pid(9) == ""


def test_is_pid_alive_tasklist_non_windows():
    assert platform_probe.is_pid_alive_tasklist(1) is False


def test_tasklist_csv_for_image_non_windows():
    assert platform_probe.tasklist_csv_for_image("python.exe") == ""


def test_run_platform_nonzero_and_exception(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "linux")
    monkeypatch.setattr(platform_probe.shutil, "which", lambda _name: "ps")

    def _fail_run(*_a, **_k):
        raise RuntimeError("run failed")

    patch_subprocess(monkeypatch, run=_fail_run)
    assert platform_probe.ps_aux() == ""

    def _nonzero_run(*_a, **_k):
        return _completed(stdout="out", returncode=1)

    patch_subprocess(monkeypatch, run=_nonzero_run)
    assert platform_probe.ps_pid_cmd_csv() == ""


def test_taskkill_force_tree_and_swallows_errors(monkeypatch):
    calls: list[list[str]] = []

    def _run(argv, **kwargs):
        calls.append(list(argv))
        raise RuntimeError("taskkill failed")

    monkeypatch.setattr(platform_probe.safe_subprocess, "run", _run)
    platform_probe.taskkill_force(99, tree=True)
    assert "/T" in calls[0]


def test_wmic_and_powershell_windows_paths(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")
    monkeypatch.setattr(platform_probe.shutil, "which", lambda name: name)

    def _run(argv, **kwargs):
        joined = " ".join(argv)
        if "wmic" in joined and "/value" in joined:
            return _completed(
                stdout="Name=python.exe\nParentProcessId=100\nCommandLine=foo\n"
            )
        if "wmic" in joined and "Name,ParentProcessId" in joined:
            return _completed(stdout="Name ParentProcessId\npython.exe 100\n")
        if "wmic" in joined and "CommandLine" in joined:
            return _completed(
                stdout="CommandLine\npython -m collab.lock_client watch\n"
            )
        if "powershell" in joined.lower():
            return _completed(stdout="python -m watch")
        if "tasklist" in joined.lower() and "CSV" in joined:
            return _completed(stdout='"python.exe","555","Console","1","999 K"\n')
        return _completed(stdout="")

    patch_subprocess(monkeypatch, run=_run)
    assert platform_probe.wmic_cmdline(555) == "python -m collab.lock_client watch"
    assert "commandline=foo" in platform_probe.wmic_cmdline_value(555)
    name, ppid = platform_probe.wmic_process_name_and_ppid(555)
    assert name == "python.exe"
    assert ppid == 100
    name2, ppid2 = platform_probe.wmic_process_name_and_ppid_value(555)
    assert name2 == "python.exe"
    assert ppid2 == 100
    assert platform_probe.powershell_cmdline(555) == "python -m watch"
    assert platform_probe.tasklist_csv_for_pid(555)
    assert 555 in platform_probe.iter_tasklist_python_pids()


def test_wmic_non_windows_returns_empty():
    assert platform_probe.wmic_cmdline(1) is None
    assert platform_probe.wmic_cmdline_value(1) == ""
    assert platform_probe.wmic_process_name_and_ppid(1) == (None, None)
    assert platform_probe.powershell_cmdline(1) is None


def test_wmic_process_name_parse_value_error(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")
    monkeypatch.setattr(platform_probe.shutil, "which", lambda _name: "wmic")

    def _run(argv, **kwargs):
        return _completed(stdout="Name ParentProcessId\npython.exe not-a-number\n")

    patch_subprocess(monkeypatch, run=_run)
    name, ppid = platform_probe.wmic_process_name_and_ppid(7)
    assert name == "python.exe"
    assert ppid is None


def test_resolve_returns_bare_name_in_test_mode(monkeypatch):
    monkeypatch.setattr(platform_probe.shutil, "which", lambda _name: None)
    monkeypatch.setattr(platform_probe.safe_subprocess, "is_test_mode", lambda: True)
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")

    def _run(argv, **kwargs):
        assert argv[0] == "tasklist"
        return _completed(stdout="")

    patch_subprocess(monkeypatch, run=_run)
    assert platform_probe.tasklist_csv_for_pid(1) == ""


def test_iter_tasklist_skips_malformed_csv_rows(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")
    monkeypatch.setattr(platform_probe.shutil, "which", lambda _name: "tasklist")

    def _run(argv, **kwargs):
        return _completed(
            stdout='"bad","notint","x"\n"python.exe","12","Console","1","1 K"\n'
        )

    patch_subprocess(monkeypatch, run=_run)
    assert 12 in platform_probe.iter_tasklist_python_pids()


def test_powershell_and_ps_helpers_noop_off_windows(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "linux")
    assert platform_probe.powershell_cmdline(1) is None
    assert platform_probe.ps_aux() == ""
    assert platform_probe.ps_pid_cmd_csv() == ""


def test_wmic_cmdline_value_empty_off_windows():
    assert platform_probe.wmic_cmdline_value(1) == ""


def test_get_cmdline_windows_falls_back_to_powershell(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")
    monkeypatch.setattr(platform_probe, "wmic_cmdline", lambda _pid: None)
    monkeypatch.setattr(platform_probe, "powershell_cmdline", lambda _pid: "ps-line")
    assert platform_probe.get_cmdline(3) == "ps-line"


def test_windows_helpers_no_executable_on_path(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")
    monkeypatch.setattr(platform_probe.shutil, "which", lambda _name: None)
    monkeypatch.setattr(platform_probe.safe_subprocess, "is_test_mode", lambda: False)
    assert platform_probe.wmic_cmdline(5) is None
    assert platform_probe.wmic_cmdline_value(5) == ""
    assert platform_probe.wmic_process_name_and_ppid(5) == (None, None)
    assert platform_probe.wmic_process_name_and_ppid_value(5) == (None, None)
    assert platform_probe.powershell_cmdline(5) is None
    assert platform_probe.tasklist_csv_for_image("python.exe") == ""


def test_ps_helpers_return_empty_on_windows(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")
    assert platform_probe.ps_aux() == ""
    assert platform_probe.ps_pid_cmd_csv() == ""


def test_iter_tasklist_skips_blank_csv_lines(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")
    monkeypatch.setattr(platform_probe.shutil, "which", lambda _name: "tasklist")

    def _run(argv, **kwargs):
        return _completed(stdout='\n"python.exe","77","Console","1","1 K"\n')

    patch_subprocess(monkeypatch, run=_run)
    assert platform_probe.iter_tasklist_python_pids() == [77]


def test_iter_collab_launcher_pids_non_windows(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "linux")
    assert platform_probe.iter_collab_launcher_pids() == []


def test_iter_collab_launcher_pids_no_tasklist(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")
    monkeypatch.setattr(platform_probe.shutil, "which", lambda _name: None)
    monkeypatch.setattr(platform_probe.safe_subprocess, "is_test_mode", lambda: False)
    assert platform_probe.iter_collab_launcher_pids() == []


def test_iter_collab_launcher_pids_parses_both_images(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")
    monkeypatch.setattr(platform_probe.shutil, "which", lambda _name: "tasklist")

    def _run(argv, **kwargs):
        joined = " ".join(argv)
        if "collab.exe" in joined:
            return _completed(stdout='"collab.exe","321","Console","1","10 K"\n')
        if "collab-watcher.exe" in joined:
            return _completed(
                stdout='"collab-watcher.exe","654","Console","1","10 K"\n'
            )
        return _completed(stdout="")

    patch_subprocess(monkeypatch, run=_run)
    pids = platform_probe.iter_collab_launcher_pids()
    assert 321 in pids
    assert 654 in pids


def test_iter_collab_launcher_pids_skips_blank_lines(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")
    monkeypatch.setattr(platform_probe.shutil, "which", lambda _name: "tasklist")

    def _run(argv, **kwargs):
        if "collab.exe" in " ".join(argv):
            return _completed(stdout="\n\n")
        return _completed(stdout="")

    patch_subprocess(monkeypatch, run=_run)
    assert platform_probe.iter_collab_launcher_pids() == []


def test_iter_collab_launcher_pids_skips_malformed_and_dedupes(monkeypatch):
    monkeypatch.setattr(platform_probe.sys, "platform", "win32")
    monkeypatch.setattr(platform_probe.shutil, "which", lambda _name: "tasklist")

    def _run(argv, **kwargs):
        if "collab.exe" in " ".join(argv):
            return _completed(
                stdout=(
                    '\n"bad","notint","x"\n'
                    '"collab.exe","88","Console","1","1 K"\n'
                    '"collab.exe","88","Console","1","1 K"\n'
                )
            )
        return _completed(stdout="")

    patch_subprocess(monkeypatch, run=_run)
    assert platform_probe.iter_collab_launcher_pids() == [88]
