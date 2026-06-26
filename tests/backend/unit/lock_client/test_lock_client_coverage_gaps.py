"""Targeted tests closing source-coverage gaps in ``collab/lock_client.py``.

Each test exercises a concrete, previously-uncovered branch with real behavioral
assertions (no smoke tests). Module globals are mutated only through
``monkeypatch.setattr`` to avoid cross-test leakage.
"""

from __future__ import annotations

import logging
import os
import sys
import types
from datetime import datetime as _real_datetime
from datetime import timedelta
from unittest import mock

import pytest

from ._helpers import (
    FakeClient,
    FakeResponse,
    load_lock_client_module,
    make_create_client,
)

mod = load_lock_client_module()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------
def test_read_clean_env_path_strips_inline_comment(monkeypatch):
    """Inline ``# comment`` suffix is stripped from path-like env values."""
    monkeypatch.setenv("COLLAB_GAP_PATH", "/tmp/data  # trailing note")
    assert mod._read_clean_env_path("COLLAB_GAP_PATH") == "/tmp/data"


def test_read_clean_env_path_comment_only_returns_none(monkeypatch):
    """A value that is only a comment resolves to ``None``."""
    monkeypatch.setenv("COLLAB_GAP_PATH", "# only a comment")
    assert mod._read_clean_env_path("COLLAB_GAP_PATH") is None


def test_resolve_project_root_honors_override(monkeypatch, tmp_path):
    """``COLLAB_PROJECT_ROOT`` override is returned as an absolute path."""
    monkeypatch.setenv("COLLAB_PROJECT_ROOT", str(tmp_path))
    assert mod._resolve_project_root() == os.path.abspath(str(tmp_path))


def test_resolve_runtime_root_falls_back_to_project_root(monkeypatch):
    """Without home/state overrides, the project root is returned verbatim."""
    monkeypatch.delenv("COLLAB_HOME", raising=False)
    monkeypatch.delenv("COLLAB_STATE_DIR", raising=False)
    assert mod._resolve_runtime_root("/some/project") == "/some/project"


def test_refresh_pid_file_updates_global_for_agent(monkeypatch, tmp_path):
    """``_refresh_pid_file`` rewrites PID_FILE when no env override is set."""
    monkeypatch.delenv("COLLAB_PID_FILE", raising=False)
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "PID_FILE", "sentinel-original")

    mod._refresh_pid_file("agent-77")

    expected = mod.agent_identity.resolve_daemon_pid_path(
        mod._get_state_dir(), "agent-77"
    )
    assert mod.PID_FILE == expected
    assert mod.PID_FILE != "sentinel-original"


# ---------------------------------------------------------------------------
# _get_create_client defensive branches
# ---------------------------------------------------------------------------
def test_get_create_client_preloaded_origin_inspection_error(monkeypatch):
    """A failure while inspecting a preloaded module's origin is swallowed."""
    monkeypatch.setattr(mod, "_supabase_create_client", None)

    def _create(url, key):
        return FakeClient(FakeResponse())

    fake = types.SimpleNamespace(create_client=_create)
    fake.__spec__ = types.SimpleNamespace(origin="/opt/site-packages/supabase/x.py")
    monkeypatch.setitem(sys.modules, "supabase", fake)

    real_abspath = os.path.abspath

    def _boom_abspath(path):
        if "supabase" in str(path):
            raise RuntimeError("origin inspection failed")
        return real_abspath(path)

    monkeypatch.setattr(mod.os.path, "abspath", _boom_abspath)

    fn = mod._get_create_client()
    assert fn is _create


def test_get_create_client_fallback_import_registers_module(monkeypatch):
    """Fallback import path resolves create_client from a freshly imported module."""
    import builtins

    monkeypatch.setattr(mod, "_supabase_create_client", None)
    monkeypatch.delitem(sys.modules, "supabase", raising=False)
    real_import = builtins.__import__

    def _create(url, key):
        return FakeClient(FakeResponse())

    def _fake_import(name, *args, **kwargs):
        if name == "supabase":
            m = types.ModuleType("supabase")
            m.create_client = _create
            m.__spec__ = types.SimpleNamespace(
                origin="/opt/site-packages/supabase/__init__.py"
            )
            sys.modules["supabase"] = m
            return m
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert mod._get_create_client() is _create


def test_get_create_client_fallback_import_not_registered(monkeypatch):
    """Fallback import where sys.modules lacks the module hits the origin=None
    branch."""
    import builtins

    monkeypatch.setattr(mod, "_supabase_create_client", None)
    monkeypatch.delitem(sys.modules, "supabase", raising=False)
    real_import = builtins.__import__

    def _create(url, key):
        return FakeClient(FakeResponse())

    def _fake_import(name, *args, **kwargs):
        if name == "supabase":
            m = types.ModuleType("supabase")
            m.create_client = _create
            # Intentionally do NOT register into sys.modules.
            return m
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert mod._get_create_client() is _create


def test_get_create_client_fallback_import_shadow_exits(monkeypatch):
    """Fallback import resolving inside the repo is treated as a shadow and exits."""
    import builtins

    monkeypatch.setattr(mod, "_supabase_create_client", None)
    monkeypatch.delitem(sys.modules, "supabase", raising=False)
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "supabase":
            m = types.ModuleType("supabase")
            m.create_client = lambda *a, **k: None
            m.__spec__ = types.SimpleNamespace(
                origin=os.path.join(mod._COLLAB_ROOT, "supabase", "__init__.py")
            )
            sys.modules["supabase"] = m
            return m
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    with pytest.raises(SystemExit):
        mod._get_create_client()


def test_get_create_client_fallback_origin_inspection_error(monkeypatch):
    """An origin-inspection error in the fallback path is swallowed."""
    import builtins

    monkeypatch.setattr(mod, "_supabase_create_client", None)
    monkeypatch.delitem(sys.modules, "supabase", raising=False)
    real_import = builtins.__import__

    def _create(url, key):
        return FakeClient(FakeResponse())

    def _fake_import(name, *args, **kwargs):
        if name == "supabase":
            m = types.ModuleType("supabase")
            m.create_client = _create
            m.__spec__ = types.SimpleNamespace(origin="/opt/pkg/supabase/__init__.py")
            sys.modules["supabase"] = m
            return m
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    real_abspath = os.path.abspath

    def _boom_abspath(path):
        if "supabase" in str(path):
            raise RuntimeError("origin failure")
        return real_abspath(path)

    monkeypatch.setattr(mod.os.path, "abspath", _boom_abspath)
    assert mod._get_create_client() is _create


# ---------------------------------------------------------------------------
# Lock-service reachability probe
# ---------------------------------------------------------------------------
def test_ensure_lock_service_reachable_success(monkeypatch):
    """A successful socket connection returns without raising."""
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "anon", raising=False)

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    captured = {}

    def _create_connection(address, timeout=None):
        captured["address"] = address
        return _Conn()

    monkeypatch.setattr(mod.socket, "create_connection", _create_connection)
    mod._ensure_lock_service_reachable()
    assert captured["address"] == ("abc.supabase.co", 443)


# ---------------------------------------------------------------------------
# Same-machine token resolution with an agent identity
# ---------------------------------------------------------------------------
def test_is_same_machine_token_agent_appends_none_variant(monkeypatch):
    """When an agent id is present the human (None) variant is also tried.

    A token generated for the (dev_id, agent=None) seed must still be recognized as
    same-machine even though this client has a non-None ``agent_id`` — proving the
    ``agent_candidates.append(None)`` branch is exercised and matched.
    """
    c = mod.LockClient(local_only=True)
    c.developer_id = "alice"
    c.agent_id = "agent-1"
    monkeypatch.setattr(
        mod.LockClient, "_get_git_username", staticmethod(lambda: "alice")
    )
    monkeypatch.setenv("USERNAME", "alice")
    monkeypatch.setattr(mod.socket, "gethostname", lambda: "host-a")
    monkeypatch.setattr(mod.os.path, "abspath", lambda _p: "C:/repo")

    # Build the token for the *human* (agent=None) variant on this machine.
    none_variant_seed = mod.agent_identity.session_token_seed(
        "alice", None, "host-a", "c:/repo"
    )
    none_variant_token = mod.agent_identity.session_token_from_seed(none_variant_seed)

    assert c._is_same_machine_token(none_variant_token) is True
    assert c._is_same_machine_token("deadbeefdeadbeef") is False


# ---------------------------------------------------------------------------
# release() delete-stage error paths
# ---------------------------------------------------------------------------
class _ReleaseClient:
    """Fake client whose pre-check succeeds but whose DELETE stage misbehaves."""

    def __init__(self, owner, delete_action):
        self._owner = owner
        self._delete_action = delete_action
        self._is_delete = False

    def table(self, *a, **k):
        return self

    def select(self, *a, **k):
        self._is_delete = False
        return self

    def delete(self, *a, **k):
        self._is_delete = True
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        if self._is_delete:
            return self._delete_action()
        return FakeResponse(status=200, data=[{"developer_id": self._owner}])


def test_release_delete_raises_returns_api_error(monkeypatch):
    """An exception during the DELETE stage is reported as an API error."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod.time, "sleep", lambda _x: None)

    def _raise():
        raise ValueError("delete blew up")

    client = _ReleaseClient("releaser", _raise)
    monkeypatch.setattr(mod, "_get_create_client", lambda: (lambda u, k: client))

    lc = mod.LockClient(developer_id="releaser")
    ok, msg = lc.release("tmp/x")
    assert ok is False
    assert "API Error" in msg
    assert "delete blew up" in msg


def test_release_delete_error_response(monkeypatch):
    """A DELETE response carrying an error field is reported as an API error."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod.time, "sleep", lambda _x: None)

    def _error_resp():
        return FakeResponse(status=400, data=None, error="delete rejected")

    client = _ReleaseClient("releaser", _error_resp)
    monkeypatch.setattr(mod, "_get_create_client", lambda: (lambda u, k: client))

    lc = mod.LockClient(developer_id="releaser")
    ok, msg = lc.release("tmp/x")
    assert ok is False
    assert "delete rejected" in msg


# ---------------------------------------------------------------------------
# active() sandbox degradation
# ---------------------------------------------------------------------------
def test_active_sandbox_unreachable_returns_empty(monkeypatch):
    """In sandbox mode an unreachable stub yields an empty active-lock list."""
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setenv("SUPABASE_URL", "https://localhost:54321")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://localhost:54321", raising=False)
    monkeypatch.setattr(mod.time, "sleep", lambda _x: None)

    class _Boom:
        def table(self, *a, **k):
            return self

        def select(self, *a, **k):
            return self

        def execute(self):
            raise RuntimeError("Connection refused by stub")

    monkeypatch.setattr(mod, "_get_create_client", lambda: (lambda u, k: _Boom()))
    lc = mod.LockClient(developer_id="sandbox_user")
    assert lc.active() == []


# ---------------------------------------------------------------------------
# prune_history RPC error -> fallback
# ---------------------------------------------------------------------------
def test_prune_history_rpc_error_triggers_fallback(monkeypatch):
    """An RPC error response raises internally and triggers the REST fallback."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod.time, "sleep", lambda _x: None)
    monkeypatch.setattr(
        mod,
        "_get_create_client",
        lambda: make_create_client(
            FakeResponse(status=200, data=None, error="prune rpc missing")
        ),
    )

    lc = mod.LockClient(developer_id="pruner")
    ok, deleted, msg = lc.prune_history(retention_days=30)
    # Fallback uses .lt() which the fake client lacks -> graceful API error.
    assert ok is False
    assert deleted == 0
    assert "API Error" in msg


# ---------------------------------------------------------------------------
# cleanup_orphaned_processes branches
# ---------------------------------------------------------------------------
def test_cleanup_skips_non_watcher_cmdline(monkeypatch, capsys):
    """A python process whose cmdline lacks lock_client is not killed."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod.platform_probe, "iter_tasklist_python_pids", lambda: [4242])
    monkeypatch.setattr(mod.os, "getpid", lambda: 1)
    monkeypatch.setattr(mod.shutil, "which", lambda _name: None)

    import psutil

    proc = mock.MagicMock()
    proc.cmdline.return_value = ["python", "unrelated_script.py"]
    monkeypatch.setattr(psutil, "Process", lambda _pid: proc)

    killed_pids = []
    monkeypatch.setattr(
        mod.platform_probe,
        "taskkill_force",
        lambda pid, tree=False: killed_pids.append(pid),
    )
    monkeypatch.setattr(mod.os.path, "exists", lambda _p: False)

    lc = mod.LockClient(developer_id="cleaner")
    lc.cleanup_orphaned_processes()
    assert killed_pids == []
    assert "No orphaned lock_client processes found." in capsys.readouterr().out


def test_cleanup_handles_no_such_process(monkeypatch):
    """A psutil NoSuchProcess during inspection is skipped via continue."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod.platform_probe, "iter_tasklist_python_pids", lambda: [4242])
    monkeypatch.setattr(mod.os, "getpid", lambda: 1)

    import psutil

    def _raise_nsp(_pid):
        raise psutil.NoSuchProcess(_pid)

    monkeypatch.setattr(psutil, "Process", _raise_nsp)
    monkeypatch.setattr(mod.os.path, "exists", lambda _p: False)

    killed = []
    monkeypatch.setattr(
        mod.platform_probe,
        "taskkill_force",
        lambda pid, tree=False: killed.append(pid),
    )
    lc = mod.LockClient(developer_id="cleaner")
    lc.cleanup_orphaned_processes()
    assert killed == []


def test_cleanup_wmic_inspection_error(monkeypatch, caplog):
    """When psutil cannot inspect and WMIC raises, the error is logged, not raised."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod.platform_probe, "iter_tasklist_python_pids", lambda: [4242])
    monkeypatch.setattr(mod.os, "getpid", lambda: 1)
    monkeypatch.setattr(mod.shutil, "which", lambda _name: "C:/Windows/wmic.exe")

    import psutil

    def _raise_generic(_pid):
        raise RuntimeError("psutil broke")

    monkeypatch.setattr(psutil, "Process", _raise_generic)

    def _wmic_boom(_pid):
        raise RuntimeError("wmic broke")

    monkeypatch.setattr(mod.platform_probe, "wmic_cmdline_value", _wmic_boom)
    monkeypatch.setattr(mod.os.path, "exists", lambda _p: False)

    killed = []
    monkeypatch.setattr(
        mod.platform_probe,
        "taskkill_force",
        lambda pid, tree=False: killed.append(pid),
    )

    lc = mod.LockClient(developer_id="cleaner")
    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        lc.cleanup_orphaned_processes()

    assert killed == []  # WMIC failure means the PID is never killed
    assert "Error checking PID 4242 via WMIC" in caplog.text


def test_cleanup_unix_malformed_ps_line(monkeypatch):
    """On Unix a malformed ps row (non-int pid) is skipped without killing."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        mod.platform_probe,
        "ps_aux",
        lambda: "user collab_pytest_ python lock_client.py watch",
    )
    killed = []
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: killed.append(pid))
    lc = mod.LockClient(developer_id="cleaner")
    lc.cleanup_orphaned_processes()  # ValueError on int(parts[1]) is swallowed
    assert killed == []


def test_cleanup_unix_ps_aux_error(monkeypatch, caplog):
    """On Unix a ps_aux failure is logged as a warning and handled gracefully."""
    monkeypatch.setattr(sys, "platform", "linux")

    def _boom():
        raise RuntimeError("ps failed")

    monkeypatch.setattr(mod.platform_probe, "ps_aux", _boom)
    lc = mod.LockClient(developer_id="cleaner")
    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        lc.cleanup_orphaned_processes()  # must not raise
    assert "Error scanning for orphaned processes" in caplog.text


def test_cleanup_log_file_lock_inspection(monkeypatch, capsys):
    """When no orphans are found, locked log files are reported per file."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod.platform_probe, "iter_tasklist_python_pids", lambda: [])
    monkeypatch.setattr(mod.os.path, "exists", lambda _p: True)

    import builtins

    real_open = builtins.open
    calls = {"n": 0}

    def _open(path, mode="r", *args, **kwargs):
        if str(path).endswith(".log") and "a" in mode:
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError("locked")
            raise OSError("other failure")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)

    lc = mod.LockClient(developer_id="cleaner")
    lc.cleanup_orphaned_processes()
    out = capsys.readouterr().out
    assert "is LOCKED by another process" in out


# ---------------------------------------------------------------------------
# watch() loop branches
# ---------------------------------------------------------------------------
def _setup_watch(monkeypatch, tmp_path, *, developer_id="watch_user"):
    """Wire a LockClient for deterministic, side-effect-free watch() runs."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "daemon.pid"))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    lc = mod.LockClient(developer_id=developer_id)
    monkeypatch.setattr(lc, "_reconcile", lambda: set())
    monkeypatch.setattr(lc, "_scan_remote_locks", lambda: None)
    monkeypatch.setattr(lc, "_register_signal_handlers", lambda: None)
    monkeypatch.setattr(lc, "_start_parent_monitor_thread", lambda: None)
    monkeypatch.setattr(lc, "_prepare_dashboard_server", lambda: (None, None))
    monkeypatch.setattr(lc, "_write_pid", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_get_session_token", lambda: "tok")
    monkeypatch.setattr(lc, "_read_pid", lambda strict=False: os.getpid())
    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", lambda: ([], True))
    monkeypatch.setattr(lc, "_get_parent_ide_pid", lambda: (4242, "process_tree"))
    return lc


def _install_fast_now(monkeypatch):
    """Make ``datetime.now()`` advance 5 seconds per call to trigger checks."""
    tick = {"n": 0}

    def fast_now():
        tick["n"] += 1
        return _real_datetime.now() + timedelta(seconds=tick["n"] * 5)

    monkeypatch.setattr(
        mod,
        "datetime",
        type(
            "FDT",
            (),
            {
                "now": staticmethod(fast_now),
                "fromisoformat": _real_datetime.fromisoformat,
            },
        )(),
    )


def _keep_stop_file(monkeypatch):
    """Prevent watch() startup from deleting the ``.stop_request`` marker."""
    real_remove = os.remove

    def _remove(path):
        if str(path).endswith(".stop_request"):
            return None
        return real_remove(path)

    monkeypatch.setattr(mod.os, "remove", _remove)


def test_watch_open_dashboard_failure_logged(monkeypatch, tmp_path):
    """A dashboard-open failure is caught so the watcher keeps running."""
    lc = _setup_watch(monkeypatch, tmp_path)

    def _bad_dashboard():
        raise RuntimeError("no browser")

    monkeypatch.setattr(lc, "dashboard", _bad_dashboard)
    shutdown = []
    monkeypatch.setattr(lc, "_graceful_shutdown", lambda *a, **k: shutdown.append(1))
    monkeypatch.setattr(mod.time, "sleep", mock.Mock(side_effect=KeyboardInterrupt))

    lc.watch(interval=1, timeout_mins=60, open_dashboard=True)
    assert shutdown  # finally-block shutdown ran


def _assert_parent_method_unknown(monkeypatch, lc, caplog):
    """Drive watch() one parent-check cycle and assert parent_method=='unknown'.

    With no explicit ``parent_pid`` the loop logs the immediate-parent check line that
    embeds ``via=<parent_method>``; a fallback to ``unknown`` is therefore directly
    observable.
    """
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    monkeypatch.setattr(lc, "_get_process_info_local", lambda _pid: ("x", None))
    monkeypatch.setattr(mod.os, "getppid", lambda: 5000)
    # Keep the resolved parent alive so the loop does not exit early via the
    # parent-terminated branch; we only want to observe parent_method=='unknown'.
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _pid: True)
    )
    _install_fast_now(monkeypatch)

    counter = {"n": 0}

    def _sleep(_x):
        counter["n"] += 1
        if counter["n"] > 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)
    reasons = []
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda reason=None: reasons.append(reason)
    )
    lc._initial_ppid = 5000

    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        lc.watch(interval=1, timeout_mins=60)

    assert "via=unknown" in caplog.text
    # Only the finally-block cleanup shutdown ran (reason=None); the failed
    # parent-method detection must not trigger an extra, reason-bearing shutdown.
    assert reasons == [None]


def test_watch_parent_method_detection_exception(monkeypatch, tmp_path, caplog):
    """A failure detecting the parent method defaults it to 'unknown'."""
    lc = _setup_watch(monkeypatch, tmp_path)

    def _boom():
        raise RuntimeError("detection failed")

    monkeypatch.setattr(lc, "_get_parent_ide_pid", _boom)
    _assert_parent_method_unknown(monkeypatch, lc, caplog)


def test_watch_parent_method_unknown_when_not_detected(monkeypatch, tmp_path, caplog):
    """When detection returns no method, parent_method defaults to 'unknown'."""
    lc = _setup_watch(monkeypatch, tmp_path)
    monkeypatch.setattr(lc, "_get_parent_ide_pid", lambda: (None, None))
    _assert_parent_method_unknown(monkeypatch, lc, caplog)


def _drive_stop_watch(
    monkeypatch,
    tmp_path,
    payload,
    *,
    session_token="tok",
    read_pid=None,
    exit_after=2,
):
    """Run watch() with a persistent stop-request file and capture shutdowns."""
    lc = _setup_watch(monkeypatch, tmp_path)
    monkeypatch.setattr(lc, "_get_session_token", lambda: session_token)
    if read_pid is not None:
        monkeypatch.setattr(lc, "_read_pid", read_pid)
    _keep_stop_file(monkeypatch)
    (tmp_path / ".stop_request").write_text(payload, encoding="utf-8")
    _install_fast_now(monkeypatch)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _pid: True)
    )
    monkeypatch.setattr(lc, "_get_process_info_local", lambda _pid: ("ide.exe", None))
    monkeypatch.setattr(mod.os, "getppid", lambda: 5000)

    counter = {"n": 0}

    def _sleep(_x):
        counter["n"] += 1
        if counter["n"] > exit_after:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)
    reasons = []
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda reason=None: reasons.append(reason)
    )
    lc._initial_ppid = 5000
    lc.watch(
        interval=1,
        timeout_mins=60,
        daemon_mode=True,
        parent_pid=6000,
        parent_method="vscode_pid",
    )
    return reasons


def test_watch_stop_request_token_match(monkeypatch, tmp_path):
    """A TOKEN: stop request matching the session token triggers shutdown."""
    reasons = _drive_stop_watch(monkeypatch, tmp_path, "TOKEN:tok")
    assert "stop_requested" in reasons


def test_watch_stop_request_pid_match(monkeypatch, tmp_path):
    """A PID: stop request matching the running PID triggers shutdown."""
    reasons = _drive_stop_watch(monkeypatch, tmp_path, f"PID:{os.getpid()}")
    assert "stop_requested" in reasons


def test_watch_stop_request_token_session_token_error(monkeypatch, tmp_path):
    """If reading the session token fails, the TOKEN: request never matches."""
    lc = _setup_watch(monkeypatch, tmp_path)

    # The first two calls happen during watch() startup (PID write + debug log)
    # and must succeed; the in-loop stop-request lookup is the one that fails.
    calls = {"n": 0}

    def _bad_token():
        calls["n"] += 1
        if calls["n"] >= 3:
            raise RuntimeError("token unavailable")
        return "tok"

    monkeypatch.setattr(lc, "_get_session_token", _bad_token)
    _keep_stop_file(monkeypatch)
    (tmp_path / ".stop_request").write_text("TOKEN:tok", encoding="utf-8")
    _install_fast_now(monkeypatch)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _pid: True)
    )
    monkeypatch.setattr(lc, "_get_process_info_local", lambda _pid: ("ide.exe", None))
    monkeypatch.setattr(mod.os, "getppid", lambda: 5000)

    counter = {"n": 0}

    def _sleep(_x):
        counter["n"] += 1
        if counter["n"] > 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)
    reasons = []
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda reason=None: reasons.append(reason)
    )
    lc._initial_ppid = 5000
    lc.watch(
        interval=1,
        timeout_mins=60,
        daemon_mode=True,
        parent_pid=6000,
        parent_method="vscode_pid",
    )
    assert "stop_requested" not in reasons


def test_watch_stop_request_pid_invalid(monkeypatch, tmp_path):
    """A PID: stop request with a non-numeric payload is ignored."""
    reasons = _drive_stop_watch(monkeypatch, tmp_path, "PID:not-a-pid")
    assert "stop_requested" not in reasons


def test_watch_stop_request_numeric_invalid(monkeypatch, tmp_path):
    """A bare non-numeric payload is ignored without crashing the loop."""
    reasons = _drive_stop_watch(monkeypatch, tmp_path, "garbage-text")
    assert "stop_requested" not in reasons


def test_watch_stop_request_read_exception(monkeypatch, tmp_path):
    """A failure reading the stop-request file degrades to an empty payload."""
    lc = _setup_watch(monkeypatch, tmp_path)
    _keep_stop_file(monkeypatch)
    (tmp_path / ".stop_request").write_text("TOKEN:tok", encoding="utf-8")
    _install_fast_now(monkeypatch)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _pid: True)
    )
    monkeypatch.setattr(lc, "_get_process_info_local", lambda _pid: ("ide.exe", None))
    monkeypatch.setattr(mod.os, "getppid", lambda: 5000)

    import builtins

    real_open = builtins.open

    def _open(path, mode="r", *args, **kwargs):
        if str(path).endswith(".stop_request") and "r" in mode:
            raise OSError("cannot read stop file")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)

    counter = {"n": 0}

    def _sleep(_x):
        counter["n"] += 1
        if counter["n"] > 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)
    reasons = []
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda reason=None: reasons.append(reason)
    )
    lc._initial_ppid = 5000
    lc.watch(
        interval=1,
        timeout_mins=60,
        daemon_mode=True,
        parent_pid=6000,
        parent_method="vscode_pid",
    )
    # Empty payload never matches, so no stop-triggered shutdown occurred.
    assert "stop_requested" not in reasons


def test_watch_stop_request_read_pid_exception(monkeypatch, tmp_path):
    """A failure resolving the watcher PID falls back to os.getpid()."""

    def _bad_read_pid(strict=False):
        raise RuntimeError("pid read failed")

    reasons = _drive_stop_watch(
        monkeypatch, tmp_path, "PID:999999", read_pid=_bad_read_pid
    )
    assert "stop_requested" not in reasons


def test_watch_stop_request_outer_exception(monkeypatch, tmp_path, caplog):
    """An unexpected error in the stop-file block is caught and logged."""
    lc = _setup_watch(monkeypatch, tmp_path)
    _install_fast_now(monkeypatch)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _pid: True)
    )
    monkeypatch.setattr(lc, "_get_process_info_local", lambda _pid: ("ide.exe", None))
    monkeypatch.setattr(mod.os, "getppid", lambda: 5000)

    real_exists = os.path.exists

    def _exists(path):
        if str(path).endswith(".stop_request"):
            raise RuntimeError("exists check failed")
        return real_exists(path)

    monkeypatch.setattr(mod.os.path, "exists", _exists)

    counter = {"n": 0}

    def _sleep(_x):
        counter["n"] += 1
        if counter["n"] > 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)
    reasons = []
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda reason=None: reasons.append(reason)
    )
    lc._initial_ppid = 5000
    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        lc.watch(
            interval=1,
            timeout_mins=60,
            daemon_mode=True,
            parent_pid=6000,
            parent_method="vscode_pid",
        )
    assert "Stop-request polling failed" in caplog.text
    assert "stop_requested" not in reasons  # the error did not trigger a stop


def test_watch_heartbeat_check_exception(monkeypatch, tmp_path, caplog):
    """An exception in the heartbeat health check is caught and logged."""
    lc = _setup_watch(monkeypatch, tmp_path)
    heartbeat = tmp_path / ".heartbeat"
    heartbeat.write_text("alive", encoding="utf-8")
    _install_fast_now(monkeypatch)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _pid: True)
    )
    monkeypatch.setattr(lc, "_get_process_info_local", lambda _pid: ("ide.exe", None))
    monkeypatch.setattr(mod.os, "getppid", lambda: 5000)

    def _hb_boom(_startup):
        raise RuntimeError("heartbeat check failed")

    monkeypatch.setattr(lc, "_heartbeat_should_shutdown", _hb_boom)

    counter = {"n": 0}

    def _sleep(_x):
        counter["n"] += 1
        if counter["n"] > 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)
    reasons = []
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda reason=None: reasons.append(reason)
    )
    lc._initial_ppid = 5000
    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        lc.watch(
            interval=1,
            timeout_mins=60,
            daemon_mode=True,
            parent_pid=6000,
            parent_method="vscode_pid",
            heartbeat_file=str(heartbeat),
        )
    assert "Heartbeat check exception" in caplog.text
    # The heartbeat failure is swallowed, so no heartbeat-reasoned shutdown
    # fires; only the finally-block cleanup runs (reason=None).
    assert reasons == [None]


def test_watch_parent_name_unresolvable_zombie_shutdown(monkeypatch, tmp_path):
    """Parent alive but name unresolvable for 2 checks triggers zombie shutdown."""
    lc = _setup_watch(monkeypatch, tmp_path)
    _install_fast_now(monkeypatch)

    parent_checks = {"n": 0}

    def _alive(_pid):
        parent_checks["n"] += 1
        return True

    monkeypatch.setattr(mod.LockClient, "_is_process_alive", staticmethod(_alive))
    monkeypatch.setattr(mod.os, "getppid", lambda: 5000)

    def _proc_boom(_pid):
        raise RuntimeError("name lookup failed")

    monkeypatch.setattr(lc, "_get_process_info_local", _proc_boom)
    monkeypatch.setattr(mod.time, "sleep", lambda _x: None)

    reasons = []
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda reason=None: reasons.append(reason)
    )
    lc._initial_ppid = 5000
    lc.watch(
        interval=1,
        timeout_mins=60,
        daemon_mode=True,
        parent_pid=6000,
        parent_name="ide.exe",
        parent_method="vscode_pid",
    )
    # Zombie detection only fires after >=2 parent checks find the name
    # unresolvable.
    assert parent_checks["n"] >= 2
    # The zombie path calls _graceful_shutdown() with no reason, then the
    # finally-block runs it again; neither is a "stop_requested" shutdown.
    assert reasons == [None, None]
    assert "stop_requested" not in reasons


def test_watch_parent_name_resolves_again_resets_streak(monkeypatch, tmp_path, caplog):
    """A recovered parent name resets the unresolved streak without shutting down."""
    lc = _setup_watch(monkeypatch, tmp_path)
    _install_fast_now(monkeypatch)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _pid: True)
    )
    monkeypatch.setattr(mod.os, "getppid", lambda: 5000)

    seq = iter([(None, None), ("Code.exe", None)])

    def _proc(_pid):
        return next(seq, ("Code.exe", None))

    monkeypatch.setattr(lc, "_get_process_info_local", _proc)

    counter = {"n": 0}

    def _sleep(_x):
        counter["n"] += 1
        if counter["n"] > 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)
    reasons = []
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda reason=None: reasons.append(reason)
    )
    lc._initial_ppid = 5000
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        lc.watch(
            interval=1,
            timeout_mins=60,
            daemon_mode=True,
            parent_pid=6000,
            parent_name="ide.exe",
            parent_method="vscode_pid",
        )
    # First check: name unresolvable (streak=1); second: resolved -> streak reset.
    assert "Resetting streak" in caplog.text
    # The recovered name resets the streak so no zombie shutdown fires; only the
    # finally-block cleanup runs (reason=None).
    assert reasons == [None]


def test_watch_immediate_parent_name_resolution_exception(
    monkeypatch, tmp_path, caplog
):
    """A failure resolving the immediate parent name is swallowed (logs 'unknown')."""
    lc = _setup_watch(monkeypatch, tmp_path)
    _install_fast_now(monkeypatch)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda _pid: True)
    )
    monkeypatch.setattr(mod.os, "getppid", lambda: 5000)

    def _proc(pid):
        if pid == 5000:
            raise RuntimeError("immediate parent lookup failed")
        return ("ide.exe", None)

    monkeypatch.setattr(lc, "_get_process_info_local", _proc)

    counter = {"n": 0}

    def _sleep(_x):
        counter["n"] += 1
        if counter["n"] > 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)
    reasons = []
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda reason=None: reasons.append(reason)
    )
    lc._initial_ppid = 5000
    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        lc.watch(
            interval=1,
            timeout_mins=60,
            daemon_mode=True,
            parent_pid=6000,
            parent_method="vscode_pid",
        )
    # The swallowed lookup leaves the immediate parent name unresolved ('unknown'),
    # surfaced in the parent-check debug line; the watcher keeps running until the
    # KeyboardInterrupt, so only the finally-block cleanup runs (reason=None).
    assert "immediate parent: unknown" in caplog.text
    assert reasons == [None]


def test_watch_releases_locks_for_cleaned_files(monkeypatch, tmp_path):
    """Files that become clean while git is OK are released and logged."""
    # Disable minimum hold time so the test can verify rapid release behavior.
    monkeypatch.setattr(mod, "_min_auto_lock_hold_seconds", lambda: 0)
    lc = _setup_watch(monkeypatch, tmp_path)
    seq = iter([(["a.py"], True), ([], True)])
    monkeypatch.setattr(
        lc, "_get_modified_and_unpushed_files", lambda: next(seq, ([], True))
    )
    monkeypatch.setattr(lc, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(lc, "acquire_multiple", lambda *a, **k: (True, [], "ok"))

    released = []

    def _release(files):
        released.extend(files)
        return True, len(files), ""

    monkeypatch.setattr(lc, "release_multiple", _release)
    monkeypatch.setattr(lc, "_graceful_shutdown", lambda *a, **k: None)

    counter = {"n": 0}

    def _sleep(_x):
        counter["n"] += 1
        if counter["n"] > 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)
    lc.watch(interval=1, timeout_mins=60, daemon_mode=True)
    assert released == ["a.py"]


def test_watch_defers_release_for_young_locks(monkeypatch, tmp_path):
    """Locks acquired less than _min_auto_lock_hold_seconds ago are NOT released."""
    # Set a short hold time so the test is fast but still exercises the filter.
    monkeypatch.setattr(mod, "_min_auto_lock_hold_seconds", lambda: 60)
    lc = _setup_watch(monkeypatch, tmp_path)

    # First call: a.py is dirty → acquire.  Second call: clean → should NOT release
    # because the lock is only ~1s old (< 60s minimum).
    seq = iter([(["a.py"], True), ([], True)])
    monkeypatch.setattr(
        lc, "_get_modified_and_unpushed_files", lambda: next(seq, ([], True))
    )
    monkeypatch.setattr(lc, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(lc, "acquire_multiple", lambda *a, **k: (True, [], "ok"))

    released = []

    def _release(files):
        released.extend(files)
        return True, len(files), ""

    monkeypatch.setattr(lc, "release_multiple", _release)
    monkeypatch.setattr(lc, "_graceful_shutdown", lambda *a, **k: None)

    counter = {"n": 0}

    def _sleep(_x):
        counter["n"] += 1
        if counter["n"] > 4:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)
    lc.watch(interval=1, timeout_mins=60, daemon_mode=True)
    # The lock should NOT have been released because it's too young.
    assert released == []


def test_watch_loop_iteration_exception_recovers(monkeypatch, tmp_path, caplog):
    """A generic error inside the loop is logged and the loop sleeps and retries."""
    lc = _setup_watch(monkeypatch, tmp_path)

    def _boom():
        raise RuntimeError("loop body failed")

    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", _boom)
    monkeypatch.setattr(lc, "_graceful_shutdown", lambda *a, **k: None)

    counter = {"n": 0}

    def _sleep(_x):
        counter["n"] += 1
        if counter["n"] >= 1:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)
    with caplog.at_level(logging.ERROR, logger=mod.logger.name):
        lc.watch(interval=1, timeout_mins=60, daemon_mode=True)
    # The error is logged and the loop reaches its recovery sleep (>=1 call).
    assert "Error in watcher loop" in caplog.text
    assert counter["n"] >= 1


# ---------------------------------------------------------------------------
# _reconcile() startup-summary failure branches
# ---------------------------------------------------------------------------
def _reconcile_client(monkeypatch, tmp_path):
    """Return a reconcile-ready LockClient with no real locks or git changes."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("COLLAB_SILENT_DAEMON", raising=False)
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse(data=[]))
    )
    lc = mod.LockClient(developer_id="reconciler")
    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", lambda: ([], True))
    return lc


def test_reconcile_repo_summary_write_failure(monkeypatch, tmp_path, caplog):
    """A failure writing the repo-root summary copy is logged and non-fatal."""
    lc = _reconcile_client(monkeypatch, tmp_path)
    repo_summary = os.path.join(mod._COLLAB_ROOT, ".startup_summary.json")

    import builtins

    real_open = builtins.open

    def _open(path, mode="r", *args, **kwargs):
        if os.path.abspath(str(path)) == os.path.abspath(repo_summary):
            raise OSError("repo summary write blocked")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)
    # Run the cleanup thread synchronously and make it a no-op target.
    monkeypatch.setattr(
        mod.threading,
        "Thread",
        lambda *a, **k: types.SimpleNamespace(start=lambda: None),
    )
    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        result = lc._reconcile()
    assert isinstance(result, set)  # reconcile completed and returned its git set
    assert "Failed to write repo startup summary" in caplog.text


def test_reconcile_cleanup_marker_removal_failure(monkeypatch, tmp_path):
    """The marker-cleanup worker swallows a failure in its delay/loop body."""
    lc = _reconcile_client(monkeypatch, tmp_path)

    worker_ran = {"v": False}

    class _SyncThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target is not None:
                worker_ran["v"] = True
                self._target()

    monkeypatch.setattr(mod.threading, "Thread", _SyncThread)

    # Raising inside the worker's delay exercises its outer guard.
    def _sleep_boom(_x):
        raise RuntimeError("worker delay failed")

    monkeypatch.setattr(mod.time, "sleep", _sleep_boom)
    result = lc._reconcile()  # worker runs synchronously; the failure is swallowed
    assert worker_ran["v"]  # the cleanup worker body actually executed
    assert isinstance(result, set)  # the swallowed worker error did not propagate


def test_reconcile_summary_block_outer_exception(monkeypatch, tmp_path):
    """A failure writing the primary summary file is swallowed by the outer guard."""
    lc = _reconcile_client(monkeypatch, tmp_path)
    summary_file = mod._state_path(".startup_summary.json")
    open_attempts = {"n": 0}

    import builtins

    real_open = builtins.open

    def _open(path, mode="r", *args, **kwargs):
        if os.path.abspath(str(path)) == os.path.abspath(summary_file):
            open_attempts["n"] += 1
            raise OSError("summary write blocked")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)
    result = lc._reconcile()  # must not raise
    assert open_attempts["n"] >= 1  # the guarded summary write was actually attempted
    assert isinstance(result, set)


# ---------------------------------------------------------------------------
# Process/cmdline helper branches
# ---------------------------------------------------------------------------
def test_extract_pid_file_from_empty_cmdline():
    """An empty cmdline yields no --pid-file value."""
    assert mod.LockClient._extract_pid_file_from_cmdline("") is None


def test_cmdline_namespace_abspath_exception(monkeypatch):
    """An abspath failure on a parsed --pid-file value resolves to no-match."""
    lc = mod.LockClient(local_only=True)
    real_abspath = os.path.abspath

    def _selective(path):
        if str(path) == "C:/x.pid":
            raise RuntimeError("abspath failed")
        return real_abspath(path)

    monkeypatch.setattr(mod.os.path, "abspath", _selective)
    assert (
        lc._cmdline_matches_current_pid_namespace('watch --pid-file "C:/x.pid"')
        is False
    )


def test_cmdline_namespace_legacy_default_outside_test_mode(monkeypatch):
    """A legacy watcher with no --pid-file matches only the default PID path."""
    lc = mod.LockClient(local_only=True)
    monkeypatch.setattr(mod, "_is_test_mode", lambda: False)
    default_pid = os.path.abspath(os.path.join(mod._COLLAB_ROOT, ".daemon.pid"))
    monkeypatch.setattr(mod, "PID_FILE", default_pid)
    assert lc._cmdline_matches_current_pid_namespace("python -m collab watch") is True


def test_is_process_alive_win32_openprocess_zero(monkeypatch):
    """OpenProcess returning 0 with a non-access-denied error means dead."""
    monkeypatch.setattr(sys, "platform", "win32")
    import ctypes

    import psutil

    def _raise(_pid):
        raise RuntimeError("psutil status failed")

    monkeypatch.setattr(psutil, "Process", _raise)

    kernel32 = mock.MagicMock()
    kernel32.OpenProcess.return_value = 0
    kernel32.GetLastError.return_value = 87  # not ERROR_ACCESS_DENIED (5)
    windll = mock.MagicMock()
    windll.kernel32 = kernel32
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)

    assert mod.LockClient._is_process_alive(13579) is False


def test_is_process_alive_win32_psutil_pid_exists_error(monkeypatch):
    """When ctypes and psutil.pid_exists fail, tasklist is the final fallback."""
    monkeypatch.setattr(sys, "platform", "win32")
    import ctypes

    import psutil

    def _raise(_pid):
        raise RuntimeError("psutil status failed")

    monkeypatch.setattr(psutil, "Process", _raise)
    monkeypatch.setattr(
        psutil,
        "pid_exists",
        lambda _pid: (_ for _ in ()).throw(RuntimeError("pid_exists failed")),
    )

    kernel32 = mock.MagicMock()
    kernel32.OpenProcess.side_effect = RuntimeError("openprocess failed")
    windll = mock.MagicMock()
    windll.kernel32 = kernel32
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)
    monkeypatch.setattr(mod.platform_probe, "is_pid_alive_tasklist", lambda _pid: True)

    assert mod.LockClient._is_process_alive(24680) is True


def test_discover_watchers_win32_tasklist_error(monkeypatch):
    """A tasklist enumeration failure during discovery is logged, not raised."""
    monkeypatch.setattr(sys, "platform", "win32")
    import psutil

    def _iter_boom(*_a, **_k):
        raise RuntimeError("no psutil iter")

    monkeypatch.setattr(psutil, "process_iter", _iter_boom)

    def _tasklist_boom():
        raise RuntimeError("tasklist failed")

    monkeypatch.setattr(mod.platform_probe, "iter_tasklist_python_pids", _tasklist_boom)
    lc = mod.LockClient(local_only=True)
    assert lc._discover_running_watchers() == []


def test_discover_watchers_unix_blank_line_and_ps_error(monkeypatch):
    """On Unix, blank ps rows are skipped and a ps failure is handled."""
    monkeypatch.setattr(sys, "platform", "linux")
    import psutil

    def _iter_boom(*_a, **_k):
        raise RuntimeError("no psutil iter")

    monkeypatch.setattr(psutil, "process_iter", _iter_boom)
    monkeypatch.setattr(mod.platform_probe, "ps_pid_cmd_csv", lambda: "\n\n   \n")
    lc = mod.LockClient(local_only=True)
    assert lc._discover_running_watchers() == []


def test_discover_watchers_unix_ps_pid_cmd_error(monkeypatch):
    """A ps_pid_cmd_csv failure during Unix discovery is swallowed."""
    monkeypatch.setattr(sys, "platform", "linux")
    import psutil

    def _iter_boom(*_a, **_k):
        raise RuntimeError("no psutil iter")

    monkeypatch.setattr(psutil, "process_iter", _iter_boom)

    def _ps_boom():
        raise RuntimeError("ps failed")

    monkeypatch.setattr(mod.platform_probe, "ps_pid_cmd_csv", _ps_boom)
    lc = mod.LockClient(local_only=True)
    assert lc._discover_running_watchers() == []


def test_discover_watchers_skips_wrong_namespace(monkeypatch):
    """A matching watcher cmdline in another PID namespace is excluded."""
    monkeypatch.setattr(sys, "platform", "win32")
    import psutil

    def _iter_boom(*_a, **_k):
        raise RuntimeError("no psutil iter")

    monkeypatch.setattr(psutil, "process_iter", _iter_boom)
    monkeypatch.setattr(mod.platform_probe, "iter_tasklist_python_pids", lambda: [4242])
    monkeypatch.setattr(mod.os, "getpid", lambda: 1)

    lc = mod.LockClient(local_only=True)
    monkeypatch.setattr(
        lc,
        "_get_cmdline_for_pid",
        lambda _pid: 'python -m collab watch --pid-file "C:/other/ns.pid"',
    )
    monkeypatch.setattr(
        lc, "_cmdline_matches_current_pid_namespace", lambda _cmd: False
    )
    assert lc._discover_running_watchers() == []


def test_get_process_info_local_psutil_generic_then_wmic(monkeypatch):
    """A generic psutil failure falls through to the WMIC name/ppid lookup."""
    monkeypatch.setattr(sys, "platform", "win32")
    import psutil

    def _raise(_pid):
        raise RuntimeError("psutil failed")

    monkeypatch.setattr(psutil, "Process", _raise)
    monkeypatch.setattr(
        mod.platform_probe,
        "wmic_process_name_and_ppid_value",
        lambda _pid: ("Code.exe", 4242),
    )
    lc = mod.LockClient(local_only=True)
    name, ppid = lc._get_process_info_local(321)
    assert name == "Code.exe"
    assert ppid == 4242


def test_get_process_info_local_wmic_then_tasklist_errors(monkeypatch):
    """When WMIC and tasklist both fail, the lookup returns (None, None)."""
    monkeypatch.setattr(sys, "platform", "win32")
    import psutil

    def _raise(_pid):
        raise RuntimeError("psutil failed")

    monkeypatch.setattr(psutil, "Process", _raise)

    def _wmic_boom(_pid):
        raise RuntimeError("wmic failed")

    def _tasklist_boom(_pid):
        raise RuntimeError("tasklist failed")

    monkeypatch.setattr(
        mod.platform_probe, "wmic_process_name_and_ppid_value", _wmic_boom
    )
    monkeypatch.setattr(mod.platform_probe, "tasklist_csv_for_pid", _tasklist_boom)
    lc = mod.LockClient(local_only=True)
    assert lc._get_process_info_local(321) == (None, None)


def test_get_parent_ide_pid_immediate_parent_exception(monkeypatch):
    """A failure in the immediate-parent fallback yields the unknown result."""
    lc = mod.LockClient(local_only=True)
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.delenv("PYCHARM_HOSTED", raising=False)
    # Force the process-tree walk to find nothing.
    monkeypatch.setattr(lc, "_get_process_info_local", lambda _pid: (None, None))

    def _boom():
        raise RuntimeError("getppid failed")

    monkeypatch.setattr(mod.os, "getppid", _boom)
    pid, method = lc._get_parent_ide_pid()
    assert pid is None
    assert method == "unknown"


def test_get_process_name_via_tasklist_error(monkeypatch):
    """A tasklist failure during name lookup returns None."""

    def _boom(_pid):
        raise RuntimeError("tasklist failed")

    monkeypatch.setattr(mod.platform_probe, "tasklist_csv_for_pid", _boom)
    lc = mod.LockClient(local_only=True)
    assert lc._get_process_name_via_tasklist(999) is None


def test_min_auto_lock_hold_seconds_default(monkeypatch):
    """The default minimum hold time is 300 seconds."""
    monkeypatch.delenv("COLLAB_MIN_AUTO_LOCK_HOLD_SECONDS", raising=False)
    assert mod._min_auto_lock_hold_seconds() == 300
