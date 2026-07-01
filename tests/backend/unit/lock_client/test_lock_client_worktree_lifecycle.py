"""Per-worktree daemon lifecycle tests (issue #168).

Covers the deterministic teardown primitive ``worktree_unregister`` (and its
namespace-scoped helpers) plus the Layer-3 "worktree-gone" fast reap in
``watch()``. These guarantee that finishing work in one worktree (for example
switching chats in a Cursor Agents window) releases that worktree's watcher and
file handles WITHOUT disturbing watchers in sibling worktrees, and that a
removed worktree folder is reaped within a single poll interval instead of the
~60s Layer-2 cycle.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from ._helpers import load_lock_client_module

mod = load_lock_client_module()


def _local_client():
    return mod.LockClient(local_only=True, developer_id="test_user")


# ---------------------------------------------------------------------------
# _state_dir_for_root
# ---------------------------------------------------------------------------
def test_state_dir_for_root_respects_state_dir_env(monkeypatch, tmp_path):
    """COLLAB_STATE_DIR overrides the per-root hash (explicit config wins)."""
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    assert mod._state_dir_for_root(r"C:\any\worktree") == os.path.abspath(str(tmp_path))


def test_state_dir_for_root_hash_stable_and_distinct(monkeypatch):
    """Same root → same dir; different roots → different dirs (isolation)."""
    monkeypatch.delenv("COLLAB_STATE_DIR", raising=False)
    a1 = mod._state_dir_for_root(r"C:\repo\wt-a")
    a2 = mod._state_dir_for_root(r"C:\repo\wt-a")
    b = mod._state_dir_for_root(r"C:\repo\wt-b")
    assert a1 == a2
    assert a1 != b
    assert "collab_runtime_" in os.path.basename(a1)


def test_state_dir_for_root_case_and_slash_insensitive(monkeypatch):
    """Normalization matches _get_state_dir (cross-runtime CLI/extension parity)."""
    monkeypatch.delenv("COLLAB_STATE_DIR", raising=False)
    a = mod._state_dir_for_root("C:/Repo/WT")
    b = mod._state_dir_for_root(r"c:\repo\wt")
    assert a == b


def test_state_dir_env_override_collapses_to_one_shared_namespace(
    monkeypatch, tmp_path
):
    """COLLAB_STATE_DIR is a deliberate single-namespace knob (not a worktree hazard).

    When it is set, the running watcher's ``_get_state_dir`` AND cross-worktree
    targeting via ``_state_dir_for_root`` must resolve to the SAME directory, or
    teardown would miss the watcher. This proves that required parity and documents that
    an explicit override intentionally collapses every root onto one shared namespace
    (mutually exclusive with per-worktree isolation). In normal use the var is unset and
    each worktree hashes to a distinct dir.
    """
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    shared = os.path.abspath(str(tmp_path))
    assert mod._get_state_dir() == shared
    assert mod._state_dir_for_root(r"C:\repo\wt-a") == shared
    assert mod._state_dir_for_root(r"C:\repo\wt-b") == shared


# ---------------------------------------------------------------------------
# worktree_unregister — dispatch
# ---------------------------------------------------------------------------
def test_worktree_unregister_current_delegates_to_daemon_stop(monkeypatch):
    """Targeting the CURRENT project root reuses the in-process daemon_stop path."""
    lc = _local_client()
    called = []

    def _fake_stop():
        called.append(True)
        return True

    monkeypatch.setattr(lc, "daemon_stop", _fake_stop)
    result = lc.worktree_unregister(mod._PROJECT_ROOT)
    assert result is True
    assert called == [True]


def test_worktree_unregister_current_returns_daemon_stop_result(monkeypatch):
    """The current-worktree path returns daemon_stop()'s real result, not a hardcoded
    True — so ``False`` (nothing was running) flows through to the CLI exit code."""
    lc = _local_client()
    monkeypatch.setattr(lc, "daemon_stop", lambda: False)
    assert lc.worktree_unregister(mod._PROJECT_ROOT) is False
    monkeypatch.setattr(lc, "daemon_stop", lambda: True)
    assert lc.worktree_unregister(mod._PROJECT_ROOT) is True


def test_worktree_unregister_empty_path_returns_false(capsys):
    """An empty path is rejected without side effects."""
    lc = _local_client()
    assert lc.worktree_unregister("   ") is False
    assert "requires a worktree path" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# daemon_stop — accurate boolean result (feeds worktree_unregister + exit code)
# ---------------------------------------------------------------------------
def test_daemon_stop_returns_false_when_nothing_running(monkeypatch):
    """daemon_stop reports False when neither a watcher nor a launcher was found."""
    lc = _local_client()
    monkeypatch.setattr(lc, "_read_pid", lambda strict=False: None)
    monkeypatch.setattr(mod, "_is_test_mode", lambda: False)
    monkeypatch.setattr(lc, "_discover_running_watchers", lambda: [])
    monkeypatch.setattr(lc, "_reap_collab_launchers", lambda: 0)
    monkeypatch.setattr(lc, "_terminate_heartbeat_keeper", lambda: None)
    monkeypatch.setattr(lc, "_remove_pid", lambda: None)
    assert lc.daemon_stop() is False


def test_daemon_stop_returns_true_when_watcher_reaped(monkeypatch):
    """daemon_stop reports True once a live watcher is signalled and exits."""
    lc = _local_client()
    monkeypatch.setattr(lc, "_read_pid", lambda strict=False: 4321)
    alive = iter([True, False])
    monkeypatch.setattr(lc, "_is_process_alive", lambda pid: next(alive, False))
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(lc, "_read_pid_file", lambda: {"token": "z"})
    monkeypatch.setattr(lc, "_reap_collab_launchers", lambda: 0)
    monkeypatch.setattr(lc, "_terminate_heartbeat_keeper", lambda: None)
    monkeypatch.setattr(lc, "_remove_pid", lambda: None)
    assert lc.daemon_stop() is True


def test_daemon_stop_returns_true_when_only_launcher_reaped(monkeypatch):
    """A leftover collab.exe wrapper (no live watcher) still counts as a stop."""
    lc = _local_client()
    monkeypatch.setattr(lc, "_read_pid", lambda strict=False: None)
    monkeypatch.setattr(mod, "_is_test_mode", lambda: False)
    monkeypatch.setattr(lc, "_discover_running_watchers", lambda: [])
    monkeypatch.setattr(lc, "_reap_collab_launchers", lambda: 2)
    monkeypatch.setattr(lc, "_terminate_heartbeat_keeper", lambda: None)
    monkeypatch.setattr(lc, "_remove_pid", lambda: None)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    assert lc.daemon_stop() is True


# ---------------------------------------------------------------------------
# worktree_unregister — namespace-scoped teardown
# ---------------------------------------------------------------------------
def _prime_namespace(monkeypatch, tmp_path, *, pid=None, token=None, keeper_pid=None):
    """Create an isolated target worktree state dir with optional PID/keeper files."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    target = str(tmp_path / "wt-target")
    monkeypatch.setattr(mod, "_state_dir_for_root", lambda root: str(state_dir))
    if pid is not None:
        meta = {"pid": pid}
        if token is not None:
            meta["token"] = token
        (state_dir / ".daemon.pid").write_text(json.dumps(meta), encoding="utf-8")
    if keeper_pid is not None:
        (state_dir / ".daemon_keeper.pid").write_text(
            json.dumps({"pid": keeper_pid}), encoding="utf-8"
        )
    return state_dir, target


def test_worktree_unregister_writes_token_stop_request_and_cleans_up(
    monkeypatch, tmp_path
):
    """A live watcher gets a TOKEN: stop request; PID file is removed afterwards."""
    lc = _local_client()
    state_dir, target = _prime_namespace(
        monkeypatch, tmp_path, pid=4242, token="abc123"
    )
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    # Alive for the initial "found" probe, then reports dead so no force-kill.
    alive = iter([True, False, False, False])
    monkeypatch.setattr(
        mod.LockClient,
        "_is_process_alive",
        staticmethod(lambda pid: next(alive, False)),
    )
    terminated = []
    monkeypatch.setattr(lc, "_terminate_process", lambda pid: terminated.append(pid))

    result = lc.worktree_unregister(target)

    assert result is True
    stop_file = state_dir / ".stop_request"
    assert stop_file.read_text(encoding="utf-8") == "TOKEN:abc123"
    # Graceful exit → no force-terminate of the watcher.
    assert terminated == []
    # Stale PID marker removed so status/start no longer see a ghost watcher.
    assert not (state_dir / ".daemon.pid").exists()


def test_worktree_unregister_pid_payload_without_token(monkeypatch, tmp_path):
    """When no session token is recorded, the stop request falls back to PID:."""
    lc = _local_client()
    state_dir, target = _prime_namespace(monkeypatch, tmp_path, pid=777)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: False)
    )
    lc.worktree_unregister(target)
    assert (state_dir / ".stop_request").read_text(encoding="utf-8") == "PID:777"


def test_worktree_unregister_no_watcher_returns_false(monkeypatch, tmp_path):
    """No PID file → idempotent no-op that still writes a stop marker."""
    lc = _local_client()
    state_dir, target = _prime_namespace(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: False)
    )
    result = lc.worktree_unregister(target)
    assert result is False
    assert (state_dir / ".stop_request").read_text(encoding="utf-8") == "PID:0"


def test_worktree_unregister_force_terminates_when_graceful_fails(
    monkeypatch, tmp_path
):
    """A watcher that ignores the stop request is force-terminated by PID."""
    lc = _local_client()
    state_dir, target = _prime_namespace(monkeypatch, tmp_path, pid=9090, token="tok")
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    terminated = []
    monkeypatch.setattr(lc, "_terminate_process", lambda pid: terminated.append(pid))

    result = lc.worktree_unregister(target)

    assert result is True
    assert terminated == [9090]


def test_worktree_unregister_reaps_keeper(monkeypatch, tmp_path):
    """The heartbeat keeper recorded in the target namespace is terminated + cleared."""
    lc = _local_client()
    state_dir, target = _prime_namespace(monkeypatch, tmp_path, keeper_pid=5555)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    terminated = []
    monkeypatch.setattr(lc, "_terminate_process", lambda pid: terminated.append(pid))

    lc.worktree_unregister(target)

    assert 5555 in terminated
    assert not (state_dir / ".daemon_keeper.pid").exists()


def test_worktree_unregister_reaps_scoped_launchers(monkeypatch, tmp_path):
    """A reaped cross-worktree collab.exe wrapper counts as a stop even when no Python
    watcher PID is recorded (closes the daemon_stop parity gap, #168)."""
    lc = _local_client()
    state_dir, target = _prime_namespace(monkeypatch, tmp_path)  # no pid file
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: False)
    )
    seen = {}

    def _fake_scoped_reap(sd, pf, tr):
        seen["args"] = (sd, pf, tr)
        return 1

    monkeypatch.setattr(lc, "_reap_launchers_in_namespace", _fake_scoped_reap)
    # No watcher, but a wrapper was reaped → report success.
    assert lc.worktree_unregister(target) is True
    assert seen["args"][0] == str(state_dir)
    assert os.path.normcase(seen["args"][2]) == os.path.normcase(
        os.path.abspath(target)
    )


def test_reap_launchers_in_namespace_retargets_and_restores_globals(monkeypatch):
    """The scoped reaper points the module namespace globals at the TARGET worktree
    while reaping, then restores the caller's namespace unconditionally."""
    lc = _local_client()
    original = (mod.PID_FILE, mod._PROJECT_ROOT, mod._COLLAB_ROOT)
    captured = {}

    def _fake_reap(self):
        captured["pid_file"] = mod.PID_FILE
        captured["project_root"] = mod._PROJECT_ROOT
        captured["collab_root"] = mod._COLLAB_ROOT
        return 3

    monkeypatch.setattr(mod.LockClient, "_reap_collab_launchers", _fake_reap)

    result = lc._reap_launchers_in_namespace(
        r"C:\st\dir", r"C:\st\dir\.daemon.pid", r"C:\repo\wt-x"
    )

    assert result == 3
    # During the call the globals pointed at the target namespace...
    assert captured["pid_file"] == r"C:\st\dir\.daemon.pid"
    assert captured["project_root"] == r"C:\repo\wt-x"
    assert captured["collab_root"] == r"C:\st\dir"
    # ...and are fully restored afterwards (no leak into the caller's namespace).
    assert (mod.PID_FILE, mod._PROJECT_ROOT, mod._COLLAB_ROOT) == original


def test_reap_launchers_in_namespace_restores_globals_on_error(monkeypatch):
    """If the underlying reap raises, globals are still restored and 0 is returned."""
    lc = _local_client()
    original = (mod.PID_FILE, mod._PROJECT_ROOT, mod._COLLAB_ROOT)

    def _boom(self):
        raise RuntimeError("reap exploded")

    monkeypatch.setattr(mod.LockClient, "_reap_collab_launchers", _boom)
    assert lc._reap_launchers_in_namespace(r"C:\d", r"C:\d\.daemon.pid", r"C:\w") == 0
    assert (mod.PID_FILE, mod._PROJECT_ROOT, mod._COLLAB_ROOT) == original


def test_terminate_keeper_in_dir_no_file_is_noop(monkeypatch, tmp_path):
    """No keeper metadata → nothing terminated, no error."""
    lc = _local_client()
    terminated = []
    monkeypatch.setattr(lc, "_terminate_process", lambda pid: terminated.append(pid))
    lc._terminate_keeper_in_dir(str(tmp_path))
    assert terminated == []


# ---------------------------------------------------------------------------
# worktree_unregister — defensive error paths (fail-safe, never crash)
# ---------------------------------------------------------------------------
def test_worktree_unregister_corrupt_pid_file_is_tolerated(monkeypatch, tmp_path):
    """An unreadable/corrupt PID file must not crash the teardown."""
    lc = _local_client()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / ".daemon.pid").write_text("not-json{", encoding="utf-8")
    monkeypatch.setattr(mod, "_state_dir_for_root", lambda root: str(state_dir))
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: False)
    )
    result = lc.worktree_unregister(str(tmp_path / "wt"))
    assert result is False
    assert (state_dir / ".stop_request").read_text(encoding="utf-8") == "PID:0"


def test_worktree_unregister_fsync_failure_is_tolerated(monkeypatch, tmp_path):
    """A failing os.fsync while writing the stop request is swallowed."""
    lc = _local_client()
    state_dir, target = _prime_namespace(monkeypatch, tmp_path, pid=11, token="t")
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: False)
    )

    def _boom_fsync(_fd):
        raise OSError("fsync unsupported")

    monkeypatch.setattr(mod.os, "fsync", _boom_fsync)
    lc.worktree_unregister(target)
    assert (state_dir / ".stop_request").read_text(encoding="utf-8") == "TOKEN:t"


def test_worktree_unregister_stop_request_write_failure_is_tolerated(
    monkeypatch, tmp_path
):
    """If the stop request cannot be written, teardown still completes cleanly."""
    lc = _local_client()
    state_dir, target = _prime_namespace(monkeypatch, tmp_path, pid=22)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: False)
    )

    def _boom_makedirs(*_a, **_k):
        raise OSError("read-only state dir")

    monkeypatch.setattr(mod.os, "makedirs", _boom_makedirs)
    # Must not raise; no stop request file created.
    assert lc.worktree_unregister(target) is False
    assert not (state_dir / ".stop_request").exists()


def test_worktree_unregister_pid_removal_oserror_is_tolerated(monkeypatch, tmp_path):
    """A failure removing the stale PID marker is logged, not raised."""
    lc = _local_client()
    state_dir, target = _prime_namespace(monkeypatch, tmp_path, pid=33)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: False)
    )

    def _boom_remove(*_a, **_k):
        raise OSError("cannot remove")

    monkeypatch.setattr(mod.os, "remove", _boom_remove)
    assert lc.worktree_unregister(target) is False


def test_terminate_keeper_in_dir_remove_oserror_is_tolerated(monkeypatch, tmp_path):
    """A failure removing the keeper marker is swallowed."""
    lc = _local_client()
    (tmp_path / ".daemon_keeper.pid").write_text(
        json.dumps({"pid": 44}), encoding="utf-8"
    )
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: False)
    )
    monkeypatch.setattr(lc, "_terminate_process", lambda pid: None)

    def _boom_remove(*_a, **_k):
        raise OSError("cannot remove keeper")

    monkeypatch.setattr(mod.os, "remove", _boom_remove)
    lc._terminate_keeper_in_dir(str(tmp_path))  # must not raise


def test_terminate_keeper_in_dir_corrupt_file_is_tolerated(monkeypatch, tmp_path):
    """A corrupt keeper metadata file is swallowed (outer guard)."""
    lc = _local_client()
    (tmp_path / ".daemon_keeper.pid").write_text("{bad json", encoding="utf-8")
    terminated = []
    monkeypatch.setattr(lc, "_terminate_process", lambda pid: terminated.append(pid))
    lc._terminate_keeper_in_dir(str(tmp_path))  # must not raise
    assert terminated == []


# ---------------------------------------------------------------------------
# Layer 3 — prompt worktree-gone reap in watch()
# ---------------------------------------------------------------------------
def _make_layer3_client(monkeypatch, tmp_path):
    from ._helpers import FakeResponse, make_create_client

    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "daemon.pid"))
    monkeypatch.setattr(
        mod, "_get_create_client", lambda: make_create_client(FakeResponse())
    )
    monkeypatch.setattr(mod.LockClient, "_reconcile", lambda self: set())
    monkeypatch.setattr(
        mod.LockClient, "_run_git_status", staticmethod(lambda: ("", True))
    )
    lc = mod.LockClient(developer_id="test_user")

    monkeypatch.setattr(lc, "_get_session_token", lambda: "tok")
    monkeypatch.setattr(lc, "_read_pid", lambda strict=False: os.getpid())
    monkeypatch.setattr(lc, "_register_signal_handlers", lambda: None)
    monkeypatch.setattr(lc, "_start_parent_monitor_thread", lambda: None)
    monkeypatch.setattr(lc, "_scan_remote_locks", lambda: None)
    monkeypatch.setattr(lc, "_prepare_dashboard_server", lambda: (None, None))
    monkeypatch.setattr(lc, "_write_pid", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_get_modified_and_unpushed_files", lambda: ([], True))
    monkeypatch.setattr(mod.os, "getppid", lambda: 12345)
    monkeypatch.setattr(
        mod.LockClient, "_is_process_alive", staticmethod(lambda pid: True)
    )
    monkeypatch.setattr(
        mod.LockClient,
        "_get_process_info_local",
        staticmethod(lambda pid: ("Code.exe", None)),
    )

    tick = [0]
    real_now = datetime.now

    def fast_now():
        tick[0] += 1
        return real_now() + timedelta(seconds=tick[0] * 5)

    monkeypatch.setattr(
        mod,
        "datetime",
        type(
            "FDT",
            (),
            {"now": staticmethod(fast_now), "fromisoformat": datetime.fromisoformat},
        )(),
    )
    return lc


def _run_layer3_watch(monkeypatch, lc):
    reasons = []
    monkeypatch.setattr(
        lc, "_graceful_shutdown", lambda reason=None: reasons.append(reason)
    )
    ticks = [0]

    def _tick_sleep(_s):
        ticks[0] += 1
        if ticks[0] > 3:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _tick_sleep)
    lc._parent_pid = 12345
    lc._initial_ppid = 12345
    lc.watch(interval=0.01, timeout_mins=60)
    return reasons


def test_watch_layer3_reaps_when_worktree_folder_gone(monkeypatch, tmp_path):
    """When the worktree path vanishes, watch() self-exits with worktree_gone."""
    lc = _make_layer3_client(monkeypatch, tmp_path)

    real_isdir = os.path.isdir

    def fake_isdir(p):
        if os.path.normcase(str(p)) == os.path.normcase(mod._PROJECT_ROOT):
            return False
        return real_isdir(p)

    monkeypatch.setattr(mod.os.path, "isdir", fake_isdir)
    monkeypatch.setattr(mod.LockClient, "_verify_worktree_valid", lambda self: False)

    reasons = _run_layer3_watch(monkeypatch, lc)
    assert "worktree_gone" in reasons


def test_watch_layer3_no_false_reap_when_still_valid(monkeypatch, tmp_path):
    """A transient stat miss must NOT reap a still-valid worktree (fail-safe)."""
    lc = _make_layer3_client(monkeypatch, tmp_path)

    real_isdir = os.path.isdir

    def fake_isdir(p):
        if os.path.normcase(str(p)) == os.path.normcase(mod._PROJECT_ROOT):
            return False
        return real_isdir(p)

    monkeypatch.setattr(mod.os.path, "isdir", fake_isdir)
    # Path looks gone, but the authoritative check confirms it is still valid.
    monkeypatch.setattr(mod.LockClient, "_verify_worktree_valid", lambda self: True)
    # Keep Layer-2 interval from firing worktree_invalid during this run.
    monkeypatch.setattr(mod, "_WORKTREE_VALIDITY_CHECK_INTERVAL_SECONDS", 999999.0)

    reasons = _run_layer3_watch(monkeypatch, lc)
    assert "worktree_gone" not in reasons


def test_watch_layer3_disabled_when_interval_zero(monkeypatch, tmp_path):
    """Setting the worktree check interval to 0 disables Layer 3 self-reaping."""
    lc = _make_layer3_client(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_WORKTREE_VALIDITY_CHECK_INTERVAL_SECONDS", 0.0)

    def _boom(self):
        raise AssertionError("_verify_worktree_valid must not run when disabled")

    monkeypatch.setattr(mod.LockClient, "_verify_worktree_valid", _boom)
    monkeypatch.setattr(
        mod.os.path, "isdir", lambda p: False
    )  # even if path looks gone

    reasons = _run_layer3_watch(monkeypatch, lc)
    assert "worktree_gone" not in reasons
