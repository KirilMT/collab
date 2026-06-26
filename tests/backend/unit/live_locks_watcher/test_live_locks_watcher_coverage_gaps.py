"""Targeted tests closing source-coverage gaps in ``live_locks_watcher``.

Each test exercises a concrete, reachable branch that the broader suite leaves
uncovered. The tests deliberately avoid touching real network, subprocess, or platform
APIs by patching the established module seams (``_git_capture_text``, ``create_client``,
``_run_git_status_porcelain``, ``signal``/``atexit``, etc.).
"""

from __future__ import annotations

import builtins
import importlib
import importlib.util
import logging
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

from collab.safe_subprocess import CaptureResult

from ._helpers import load_watcher_module, reload_watcher_module


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "collab").exists():
            return parent
    raise FileNotFoundError("Could not locate repository root")


# ---------------------------------------------------------------------------
# Import-time optional-dependency / environment branches (reload required)
# ---------------------------------------------------------------------------


def _colorama_init_boom() -> None:
    raise RuntimeError("colorama init boom")


def test_colorama_init_exception_is_swallowed(monkeypatch):
    """Colorama ``init()`` raising must be swallowed while keeping color on.

    Covers the ``try/except`` around ``_colorama_init()`` (lines 52-54).
    """
    fake_colorama = types.SimpleNamespace(
        Fore=types.SimpleNamespace(GREEN="G", YELLOW="Y", CYAN="C", MAGENTA="M"),
        Style=types.SimpleNamespace(RESET_ALL="R"),
        init=_colorama_init_boom,
    )
    monkeypatch.setitem(sys.modules, "colorama", fake_colorama)
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())

    mod = reload_watcher_module("collab.live_locks_watcher_colorama_boom")

    assert mod._HAS_COLORAMA is True


def test_read_clean_env_path_strips_inline_comment(monkeypatch):
    """Inline ``#`` comments are stripped from path-like env overrides (line 70)."""
    mod = load_watcher_module()
    monkeypatch.setenv("COLLAB_COVGAP_PATHVAR", "/real/path   # trailing comment")
    assert mod._read_clean_env_path("COLLAB_COVGAP_PATHVAR") == "/real/path"


def test_read_clean_env_path_comment_only_returns_none(monkeypatch):
    """A comment-only value collapses to empty and returns ``None`` (line 72)."""
    mod = load_watcher_module()
    monkeypatch.setenv("COLLAB_COVGAP_PATHVAR2", "#only a comment")
    assert mod._read_clean_env_path("COLLAB_COVGAP_PATHVAR2") is None


def test_collab_root_defaults_to_project_root(monkeypatch):
    """Without COLLAB_HOME/COLLAB_STATE_DIR, the collab root is the project root.

    Covers the module-level ``else`` assignment (line 84).
    """
    monkeypatch.delenv("COLLAB_HOME", raising=False)
    monkeypatch.delenv("COLLAB_STATE_DIR", raising=False)

    mod = reload_watcher_module("collab.live_locks_watcher_default_root")

    assert mod._COLLAB_ROOT == mod._PROJECT_ROOT


def test_logging_config_import_failure_falls_back(monkeypatch):
    """A broken ``logging_config`` import disables the proxy hook (lines 96-97).

    The reloaded module is itself a package (it carries ``submodule_search_locations``),
    so ``from . import logging_config`` resolves to a submodule of that fake package;
    poisoning that entry in ``sys.modules`` forces the import to raise.
    """
    fake_name = "collab.live_locks_watcher_no_logcfg"
    monkeypatch.setitem(sys.modules, f"{fake_name}.logging_config", None)

    mod = reload_watcher_module(fake_name)

    assert mod._setup_collab_logging_obj is None


def test_stdout_reconfigure_failure_is_swallowed(monkeypatch):
    """A stream whose ``reconfigure`` raises is tolerated at import (lines 128-129)."""
    reconfigure_calls: list = []

    class _BadStream:
        def __init__(self, real):
            self._real = real

        def reconfigure(self, **_kwargs):
            reconfigure_calls.append(_kwargs)
            raise RuntimeError("reconfigure boom")

        def write(self, data):
            return self._real.write(data)

        def flush(self):
            return self._real.flush()

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(sys, "stdout", _BadStream(sys.stdout))

    # The reload must complete despite reconfigure() raising, proving the
    # import-time try/except actually swallowed the failure.
    mod = reload_watcher_module("collab.live_locks_watcher_bad_stdout")

    assert hasattr(mod, "logger")
    # The failing reconfigure path was genuinely exercised (spy fired) with the
    # expected UTF-8 arguments, and the raised error did not propagate.
    assert reconfigure_calls
    assert reconfigure_calls[0].get("encoding") == "utf-8"


def test_supabase_origin_inspection_exception_is_swallowed(monkeypatch):
    """Errors while inspecting the supabase spec origin are swallowed (line 206)."""
    fake_spec = types.SimpleNamespace(origin=12345)  # int origin breaks abspath()
    fake_supa = types.SimpleNamespace(create_client=lambda *_a, **_k: object())

    def _find_spec(name):
        if name == "supabase":
            return fake_spec
        return None

    monkeypatch.setattr(importlib.util, "find_spec", _find_spec)
    monkeypatch.setitem(sys.modules, "supabase", fake_supa)

    mod = reload_watcher_module("collab.live_locks_watcher_bad_origin")

    assert callable(mod.create_client)


def test_supabase_without_create_client_logs_error(monkeypatch, caplog):
    """An installed supabase lacking ``create_client`` logs and stays None.

    Covers lines 211-214.
    """
    site_pkg_origin = str(
        _repo_root() / ".venv" / "Lib" / "site-packages" / "supabase" / "__init__.py"
    )
    fake_spec = types.SimpleNamespace(origin=site_pkg_origin)
    fake_supa = types.SimpleNamespace()  # no create_client attribute

    def _find_spec(name):
        if name == "supabase":
            return fake_spec
        return None

    monkeypatch.setattr(importlib.util, "find_spec", _find_spec)
    monkeypatch.setitem(sys.modules, "supabase", fake_supa)

    # The error is emitted at import-time, so capture across the reload. The
    # module logger name is fixed regardless of the reloaded module name.
    with caplog.at_level(logging.ERROR, logger="collab.pycharm_watcher"):
        mod = reload_watcher_module("collab.live_locks_watcher_no_create_client")

    assert mod.create_client is None
    assert (
        "The installed 'supabase' package does not expose 'create_client'."
        in caplog.text
    )


# ---------------------------------------------------------------------------
# Identity / git helper branches
# ---------------------------------------------------------------------------


def test_get_developer_id_falls_back_on_git_exception(monkeypatch):
    """A raising git lookup falls back to the username env var (lines 313-314)."""
    mod = load_watcher_module()

    def _boom(_argv, **_kw):
        raise RuntimeError("git boom")

    monkeypatch.setattr(mod, "_git_capture_text", _boom)
    monkeypatch.setenv("USERNAME", "fallback_user")
    monkeypatch.delenv("USER", raising=False)

    assert mod._get_developer_id() == "fallback_user"


def test_lock_owned_by_us_returns_false_without_developer(monkeypatch):
    """Ownership check short-circuits to False without a developer id (line 341)."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", None)

    assert mod._lock_owned_by_us({"developer_id": "someone"}) is False


def test_is_same_machine_token_git_exception_and_agent_candidates(monkeypatch, caplog):
    """Token check tolerates git errors and also probes the no-agent variant.

    Covers the git-config exception handler (lines 393-394) and the
    ``agent_candidates.append(None)`` branch (line 407). A token built for the (dev,
    agent=None) seed must still match even though ``AGENT_ID`` is set, proving the no-
    agent variant is actually tried and matched.
    """
    from collab import agent_identity

    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "devx")
    monkeypatch.setattr(mod, "AGENT_ID", "agent-123")
    monkeypatch.setattr(mod.socket, "gethostname", lambda: "host-w")
    monkeypatch.setattr(mod.os.path, "abspath", lambda _p: "C:/repo")

    def _boom(_argv, **_kw):
        raise RuntimeError("git boom")

    monkeypatch.setattr(mod, "_git_capture_text", _boom)

    # Token for the human (agent=None) variant on this machine.
    none_variant_seed = agent_identity.session_token_seed(
        "devx", None, "host-w", "c:/repo"
    )
    none_variant_token = agent_identity.session_token_from_seed(none_variant_seed)

    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        matched_none_variant = mod._is_same_machine_token(none_variant_token)
        no_match = mod._is_same_machine_token("does-not-match-any-variant")

    assert matched_none_variant is True  # agent_candidates.append(None) matched
    assert no_match is False
    # The raising git lookup was swallowed via the debug-logged handler.
    assert "git config user.name lookup failed in token check" in caplog.text


# ---------------------------------------------------------------------------
# Overlap / processing / git-status helper branches
# ---------------------------------------------------------------------------


def test_maybe_warn_cross_branch_overlap_no_reports(monkeypatch):
    """With checks enabled but no reports, the helper returns early (line 688)."""
    mod = load_watcher_module()
    from collab import overlap

    monkeypatch.setattr(overlap, "is_overlap_check_enabled", lambda: True)
    monkeypatch.setattr(overlap, "detect_cross_branch_overlaps", lambda _root: [])
    monkeypatch.setattr(mod, "_last_overlap_warn_at", 0.0)

    mod._maybe_warn_cross_branch_overlap()

    # Returned before recording the warn timestamp, so it stays unchanged.
    assert mod._last_overlap_warn_at == 0.0


def test_process_new_files_ephemeral_skips_rpc(monkeypatch, caplog):
    """Ephemeral developers log a marker and skip the acquire RPC (lines 706-710)."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "test_dev_ephemeral")
    monkeypatch.setattr(mod, "_maybe_warn_cross_branch_overlap", lambda: None)

    class _Client:
        def rpc(self, *_a, **_k):
            raise AssertionError("ephemeral developers must not call rpc")

    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod._process_new_files(_Client(), "main", {"collab/x.py"})

    assert "[EPHEMERAL]" in caplog.text


def test_get_modified_and_unpushed_handles_git_exceptions(monkeypatch):
    """Both git phases swallow exceptions and yield an empty set.

    Covers the porcelain-status handler (lines 943-944) and the diff-base resolution
    handler (lines 957-960).
    """
    mod = load_watcher_module()

    def _status_boom():
        raise RuntimeError("status boom")

    def _resolve_boom():
        raise RuntimeError("resolve boom")

    monkeypatch.setattr(mod, "_git_capture_status_porcelain", _status_boom)
    monkeypatch.setattr(mod, "_resolve_lock_diff_base_ref", _resolve_boom)

    assert mod._get_modified_and_unpushed_files() == set()


# ---------------------------------------------------------------------------
# Interactive post-restart conflict — dashboard option
# ---------------------------------------------------------------------------


def test_handle_post_restart_conflict_opens_dashboard(monkeypatch, capsys):
    """Choice 3 opens the dashboard URL and tolerates a browser failure.

    Covers lines 1293-1297 (print + ``webbrowser.open`` + exception handling).
    """
    mod = load_watcher_module()
    choices = iter(["3", "1"])

    class _Stdin:
        @staticmethod
        def isatty():
            return True

    monkeypatch.setattr(sys, "stdin", _Stdin())
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(choices))
    monkeypatch.setattr(mod, "_dashboard_url", "http://127.0.0.1:9999/dash")

    def _open_boom(_url):
        raise RuntimeError("no browser available")

    monkeypatch.setattr(mod.webbrowser, "open", _open_boom)

    mod._handle_post_restart_conflict(
        object(),
        "collab/x.py",
        {"owner": "dev", "branch": "main", "reason": "r"},
    )

    out = capsys.readouterr().out
    assert "Opening dashboard" in out
    assert "Could not open browser" in out


# ---------------------------------------------------------------------------
# PID file write cleanup
# ---------------------------------------------------------------------------


def test_write_pid_file_cleanup_failure_is_logged(monkeypatch, tmp_path, caplog):
    """When the atomic replace fails and cleanup also fails, both are logged.

    Covers the cleanup ``try/except`` (lines 1496-1499).
    """
    mod = load_watcher_module()
    pid_file = tmp_path / "x.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    def _replace_boom(_src, _dst):
        raise OSError("replace boom")

    def _unlink_boom(_path):
        raise OSError("unlink boom")

    monkeypatch.setattr(mod.os, "replace", _replace_boom)
    monkeypatch.setattr(mod.os, "unlink", _unlink_boom)

    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        mod._write_pid_file(1234)

    assert "PID temp-file cleanup failed" in caplog.text


# ---------------------------------------------------------------------------
# _shorten_process_label branches
# ---------------------------------------------------------------------------


def test_shorten_process_label_none_returns_none():
    """A falsy label short-circuits to ``None`` (line 1625)."""
    mod = load_watcher_module()
    assert mod._shorten_process_label(None) is None


def test_shorten_process_label_basename_exception(monkeypatch):
    """A failing ``os.path.basename`` is swallowed per token (lines 1637-1638)."""
    mod = load_watcher_module()

    def _basename_boom(_p):
        raise RuntimeError("basename boom")

    monkeypatch.setattr(mod.os.path, "basename", _basename_boom)

    out = mod._shorten_process_label("some/path/token")

    assert isinstance(out, str)
    assert "some/path/token" in out


def test_shorten_process_label_truncates_long_token():
    """An over-long result is truncated with an ellipsis (line 1650)."""
    mod = load_watcher_module()
    out = mod._shorten_process_label("a" * 200, max_tokens=4, max_len=20)
    assert out is not None
    assert out.endswith("...")
    assert len(out) <= 20


def test_shorten_process_label_returns_original_on_exception():
    """An unexpected error returns the original label unchanged (lines 1652-1654)."""
    mod = load_watcher_module()

    class _BadLabel:
        def __bool__(self):
            return True

        def split(self):
            raise RuntimeError("split boom")

    bad = _BadLabel()
    assert mod._shorten_process_label(bad) is bad


# ---------------------------------------------------------------------------
# _existing_watcher_running branches
# ---------------------------------------------------------------------------


def test_existing_watcher_running_empty_pid_file(monkeypatch, tmp_path):
    """An empty PID file reports no running watcher (line 1669)."""
    mod = load_watcher_module()
    pid_file = tmp_path / "empty.pid"
    pid_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    assert mod._existing_watcher_running() == (False, None, None, None)


def test_existing_watcher_running_non_integer_plain_pid(monkeypatch, tmp_path):
    """A non-numeric plain PID file reports no running watcher (lines 1685-1686)."""
    mod = load_watcher_module()
    pid_file = tmp_path / "bad.pid"
    pid_file.write_text("not-a-number", encoding="utf-8")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    assert mod._existing_watcher_running() == (False, None, None, None)


def test_existing_watcher_running_json_without_pid(monkeypatch, tmp_path):
    """JSON metadata lacking a pid reports no running watcher (line 1689)."""
    mod = load_watcher_module()
    pid_file = tmp_path / "nopid.pid"
    pid_file.write_text('{"cmdline": "python x"}', encoding="utf-8")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    assert mod._existing_watcher_running() == (False, None, None, None)


def test_existing_watcher_running_plain_pid_matches_watcher(monkeypatch, tmp_path):
    """A live plain-PID whose cmdline matches is reported running.

    Covers lines 1760-1764 (resolve cmdline, match, return running).
    """
    mod = load_watcher_module()
    pid_file = tmp_path / "plain.pid"
    pid_file.write_text("4242", encoding="utf-8")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))
    monkeypatch.setattr(mod, "_is_process_alive", lambda _pid: True)
    monkeypatch.setattr(
        mod,
        "_get_cmdline_for_pid_local",
        lambda _pid: "python -m collab.live_locks_watcher",
    )

    running, pid, cmdline, _entry = mod._existing_watcher_running()

    assert running is True
    assert pid == 4242
    assert "live_locks_watcher" in cmdline


def test_existing_watcher_running_outer_exception(monkeypatch, tmp_path):
    """An unexpected error inside the check returns the safe default (1766-1767)."""
    mod = load_watcher_module()
    pid_file = tmp_path / "plain2.pid"
    pid_file.write_text("4242", encoding="utf-8")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    def _alive_boom(_pid):
        raise RuntimeError("alive boom")

    monkeypatch.setattr(mod, "_is_process_alive", _alive_boom)

    assert mod._existing_watcher_running() == (False, None, None, None)


# ---------------------------------------------------------------------------
# main() startup / loop branches
# ---------------------------------------------------------------------------


def _main_common(monkeypatch, mod):
    """Apply the shared startup stubs needed to drive ``main`` deterministically."""
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://t.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setattr(mod, "desktop_notify", None)
    monkeypatch.setattr(mod, "_get_developer_id", lambda: "me")
    monkeypatch.setattr(
        mod, "_existing_watcher_running", lambda: (False, None, None, None)
    )
    monkeypatch.setattr(mod, "_get_parent_ide_pid_local", lambda: None)
    monkeypatch.setattr(mod, "_reconcile_on_startup", lambda _client: None)
    monkeypatch.setattr(mod, "_scan_remote_locks", lambda _client: None)
    monkeypatch.setattr(mod, "_start_dashboard_server", lambda: None)
    monkeypatch.setattr(mod, "create_client", lambda _u, _k: mock.MagicMock())
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])


def _interrupt_sleep(monkeypatch, mod):
    def _sleep(_x):
        raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)


# ---------------------------------------------------------------------------
# Worktree-aware sibling scanning (#150)
# ---------------------------------------------------------------------------


def _make_capture_result(returncode=0, stdout=b"", timed_out=False):
    """Build a minimal CaptureResult for test injection."""
    return CaptureResult(
        argv=("git", "test"),
        returncode=returncode,
        stdout=stdout,
        stderr=b"",
        timed_out=timed_out,
    )


def test_watcher_get_sibling_worktree_dirty_files_empty(monkeypatch):
    """No sibling worktrees → empty set."""
    mod = load_watcher_module()
    monkeypatch.setattr(
        mod.safe_subprocess,
        "capture",
        lambda *a, **k: _make_capture_result(stdout=b""),
    )
    result = mod._get_sibling_worktree_dirty_files()
    assert result == set()


def test_watcher_get_sibling_worktree_dirty_files_command_fails(monkeypatch):
    """Git worktree list fails → empty set."""
    mod = load_watcher_module()
    monkeypatch.setattr(
        mod.safe_subprocess,
        "capture",
        lambda *a, **k: _make_capture_result(returncode=1),
    )
    result = mod._get_sibling_worktree_dirty_files()
    assert result == set()


def test_watcher_get_sibling_worktree_dirty_files_timed_out(monkeypatch):
    """Git worktree list times out → empty set."""
    mod = load_watcher_module()
    monkeypatch.setattr(
        mod.safe_subprocess,
        "capture",
        lambda *a, **k: _make_capture_result(timed_out=True),
    )
    result = mod._get_sibling_worktree_dirty_files()
    assert result == set()


def test_watcher_get_sibling_worktree_dirty_files_skips_own(monkeypatch):
    """Only our own worktree listed → empty set."""
    mod = load_watcher_module()
    our_root = mod._PROJECT_ROOT
    wt_output = (f"worktree {our_root}\n" f"branch refs/heads/main\n").encode()
    monkeypatch.setattr(
        mod.safe_subprocess,
        "capture",
        lambda *a, **k: _make_capture_result(stdout=wt_output),
    )
    result = mod._get_sibling_worktree_dirty_files()
    assert result == set()


def test_watcher_get_sibling_worktree_dirty_files_finds_dirty(monkeypatch, tmp_path):
    """Sibling worktree has dirty files → returned in result."""
    mod = load_watcher_module()
    sibling = tmp_path / "sibling_wt"
    sibling.mkdir()
    our_root = mod._PROJECT_ROOT
    wt_output = (
        f"worktree {our_root}\n"
        f"branch refs/heads/main\n"
        f"worktree {sibling}\n"
        f"branch refs/heads/feat/other\n"
    ).encode()
    status_output = b" M collab/live_locks_watcher.py\n M AGENTS.md\n"

    call_count = {"n": 0}

    def _capture(argv, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _make_capture_result(stdout=wt_output)
        return _make_capture_result(stdout=status_output)

    monkeypatch.setattr(mod.safe_subprocess, "capture", _capture)
    monkeypatch.setattr(
        mod.safe_subprocess, "decode_output", lambda b: b.decode("utf-8")
    )

    result = mod._get_sibling_worktree_dirty_files()
    assert "collab/live_locks_watcher.py" in result
    assert "AGENTS.md" in result


def test_watcher_get_modified_and_unpushed_includes_worktree(monkeypatch):
    """_get_modified_and_unpushed_files folds in sibling worktree dirty files."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_git_capture_status_porcelain", lambda: "")
    monkeypatch.setattr(mod, "_resolve_lock_diff_base_ref", lambda: "origin/main")
    monkeypatch.setattr(
        mod, "_get_sibling_worktree_dirty_files", lambda: {"collab/x.py"}
    )

    result = mod._get_modified_and_unpushed_files()
    assert "collab/x.py" in result


def test_watcher_get_modified_and_unpushed_worktree_scan_exception(monkeypatch):
    """Sibling worktree scan failure is swallowed."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_git_capture_status_porcelain", lambda: " M README.md")
    monkeypatch.setattr(mod, "_resolve_lock_diff_base_ref", lambda: "origin/main")

    def _boom():
        raise RuntimeError("worktree scan boom")

    monkeypatch.setattr(mod, "_get_sibling_worktree_dirty_files", _boom)

    result = mod._get_modified_and_unpushed_files()
    # Git-modified files are still returned despite worktree scan failure.
    assert "README.md" in result


def test_process_new_files_cross_branch_advisory(monkeypatch, caplog):
    """Cross-branch advisory warns when same-dev lock exists on different branch."""
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "DEVELOPER_ID", "alice")
    monkeypatch.setattr(mod, "AGENT_ID", None)
    monkeypatch.setattr(mod, "_maybe_warn_cross_branch_overlap", lambda: None)
    monkeypatch.setattr(mod, "_HAS_COLORAMA", False)

    rpc_data = [
        {
            "status": "acquired",
            "token": "tok-xyz",
            "existing_branch": "feat/other",
        }
    ]

    # Build a proper chain: client.rpc(...).execute().data
    fake_execute_result = mock.MagicMock()
    fake_execute_result.data = rpc_data

    fake_rpc_result = mock.MagicMock()
    fake_rpc_result.execute.return_value = fake_execute_result

    fake_client = mock.MagicMock()
    fake_client.rpc.return_value = fake_rpc_result

    # Also need table().insert().execute() for lock tracking
    fake_table = mock.MagicMock()
    fake_insert = mock.MagicMock()
    fake_insert.execute.return_value = mock.MagicMock(data=[])
    fake_table.insert.return_value = fake_insert
    fake_client.table.return_value = fake_table

    monkeypatch.setattr(mod, "SESSION_TOKEN", "sess-tok")
    monkeypatch.setattr(mod, "_local_owned_locks", set())
    monkeypatch.setattr(mod, "_lock_acquired_at", {})

    from datetime import datetime

    monkeypatch.setattr(mod, "datetime", mock.MagicMock())
    mod.datetime.now.return_value = datetime(2025, 1, 1)

    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        mod._process_new_files(fake_client, "main", {"collab/x.py"})

    assert any("CROSS-BRANCH" in r.message for r in caplog.records)


def test_watcher_get_sibling_worktree_dirty_files_status_fails(monkeypatch, tmp_path):
    """Git status in sibling worktree fails → graceful skip."""
    mod = load_watcher_module()
    sibling = tmp_path / "sibling_wt"
    sibling.mkdir()
    our_root = mod._PROJECT_ROOT
    wt_output = (
        f"worktree {our_root}\n"
        f"branch refs/heads/main\n"
        f"worktree {sibling}\n"
        f"branch refs/heads/feat/other\n"
    ).encode()

    call_count = {"n": 0}

    def _capture(argv, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _make_capture_result(stdout=wt_output)
        return _make_capture_result(returncode=1)  # status fails

    monkeypatch.setattr(mod.safe_subprocess, "capture", _capture)
    monkeypatch.setattr(
        mod.safe_subprocess, "decode_output", lambda b: b.decode("utf-8")
    )

    result = mod._get_sibling_worktree_dirty_files()
    assert result == set()


def test_watcher_get_sibling_worktree_dirty_files_nonexistent_dir(
    monkeypatch, tmp_path
):
    """Worktree path that doesn't exist on disk is skipped."""
    mod = load_watcher_module()
    nonexistent = str(tmp_path / "nonexistent_wt")
    our_root = mod._PROJECT_ROOT
    wt_output = (
        f"worktree {our_root}\n"
        f"branch refs/heads/main\n"
        f"worktree {nonexistent}\n"
        f"branch refs/heads/feat/other\n"
    ).encode()

    monkeypatch.setattr(
        mod.safe_subprocess,
        "capture",
        lambda *a, **k: _make_capture_result(stdout=wt_output),
    )
    monkeypatch.setattr(
        mod.safe_subprocess, "decode_output", lambda b: b.decode("utf-8")
    )

    result = mod._get_sibling_worktree_dirty_files()
    assert result == set()


def test_watcher_get_sibling_worktree_dirty_files_scan_exception(monkeypatch, tmp_path):
    """Exception during sibling status scan is caught and skipped."""
    mod = load_watcher_module()
    sibling = tmp_path / "sibling_wt"
    sibling.mkdir()
    our_root = mod._PROJECT_ROOT
    wt_output = (
        f"worktree {our_root}\n"
        f"branch refs/heads/main\n"
        f"worktree {sibling}\n"
        f"branch refs/heads/feat/other\n"
    ).encode()

    call_count = {"n": 0}

    def _capture(argv, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _make_capture_result(stdout=wt_output)
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(mod.safe_subprocess, "capture", _capture)
    monkeypatch.setattr(
        mod.safe_subprocess, "decode_output", lambda b: b.decode("utf-8")
    )

    result = mod._get_sibling_worktree_dirty_files()
    assert result == set()


def test_main_assigns_pid_file_when_env_unset(monkeypatch, tmp_path):
    """Without COLLAB_PID_FILE, main resolves a runtime PID path (line 1821)."""
    mod = load_watcher_module()
    _main_common(monkeypatch, mod)
    monkeypatch.delenv("COLLAB_PID_FILE", raising=False)
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())
    _interrupt_sleep(monkeypatch, mod)

    mod.main()

    assert str(tmp_path) in str(mod.PID_FILE)


def test_main_pid_fallback_oserror_is_swallowed(monkeypatch, tmp_path):
    """When metadata write fails and the plain-PID fallback also fails on OSError.

    Covers the inner ``except OSError: pass`` (lines 1909-1910).
    """
    mod = load_watcher_module()
    _main_common(monkeypatch, mod)
    bad_pid = tmp_path / "no" / "dir" / "x.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(bad_pid))

    def _write_boom(_pid, parent_pid=None):
        raise RuntimeError("metadata write boom")

    monkeypatch.setattr(mod, "_write_pid_file", _write_boom)
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())
    _interrupt_sleep(monkeypatch, mod)

    mod.main()

    assert not bad_pid.exists()


def test_main_registers_atexit_and_signal_handlers(monkeypatch, tmp_path):
    """Non-test-mode startup registers atexit + signal handlers (incl.

    SIGTERM).     Covers lines 1914, 1917-1919 (signal handler body) and 1922 (SIGTERM
    registration on non-Windows platforms).
    """
    mod = load_watcher_module()
    _main_common(monkeypatch, mod)
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "x.pid"))
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    def _noop_shutdown():
        return None

    monkeypatch.setattr(mod, "_graceful_shutdown", _noop_shutdown)

    registered: list = []
    monkeypatch.setattr(
        mod.atexit, "register", lambda func, *a, **k: registered.append(func) or func
    )

    monkeypatch.setattr(mod.sys, "platform", "linux")
    handlers: dict = {}
    monkeypatch.setattr(
        mod.signal, "signal", lambda sig, handler: handlers.__setitem__(sig, handler)
    )

    _interrupt_sleep(monkeypatch, mod)

    mod.main()

    assert _noop_shutdown in registered
    assert mod.signal.SIGTERM in handlers
    assert mod.signal.SIGINT in handlers

    with pytest.raises(SystemExit):
        handlers[mod.signal.SIGINT](2, None)


def test_main_loop_git_status_error_continues(monkeypatch, tmp_path):
    """A git-status error inside the loop logs, sleeps, and continues (line 2016)."""
    mod = load_watcher_module()
    _main_common(monkeypatch, mod)
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "x.pid"))

    calls = [0]

    def _git_status():
        calls[0] += 1
        if calls[0] == 1:
            return set()
        raise RuntimeError("git status failed")

    monkeypatch.setattr(mod, "_run_git_status_porcelain", _git_status)

    sleeps = [0]

    def _sleep(_x):
        sleeps[0] += 1
        if sleeps[0] >= 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)

    mod.main()

    assert sleeps[0] >= 2


def test_main_loop_processes_new_and_released_files(monkeypatch, tmp_path):
    """A changing git-status set drives acquire and release helpers.

    Covers the ``current_modified != last_modified`` block (lines 2018-2036).
    """
    mod = load_watcher_module()
    _main_common(monkeypatch, mod)
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "x.pid"))
    monkeypatch.setattr(mod, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(mod, "_filter_agent_held_new_files", lambda _c, nf: nf)

    seq = [set(), {"collab/x.py"}, set()]
    calls = [0]

    def _git_status():
        idx = min(calls[0], len(seq) - 1)
        calls[0] += 1
        return set(seq[idx])

    monkeypatch.setattr(mod, "_run_git_status_porcelain", _git_status)

    processed: dict = {"new": [], "released": []}
    monkeypatch.setattr(
        mod,
        "_process_new_files",
        lambda _c, _b, nf: processed["new"].append(set(nf)),
    )
    monkeypatch.setattr(
        mod,
        "_process_releases",
        lambda _c, rel: processed["released"].append(set(rel)),
    )

    sleeps = [0]

    def _sleep(_x):
        sleeps[0] += 1
        if sleeps[0] >= 3:
            raise KeyboardInterrupt()

    monkeypatch.setattr(mod.time, "sleep", _sleep)

    mod.main()

    assert {"collab/x.py"} in processed["new"]
    assert {"collab/x.py"} in processed["released"]


def test_main_loop_generic_exception_logged_and_notified(monkeypatch, tmp_path, caplog):
    """A non-KeyboardInterrupt loop error is logged and notified (lines 2078-2080)."""
    mod = load_watcher_module()
    _main_common(monkeypatch, mod)
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "x.pid"))
    monkeypatch.setattr(mod, "_run_git_status_porcelain", lambda: set())

    notified: list = []
    monkeypatch.setattr(
        mod, "_notify", lambda title, msg: notified.append((title, msg))
    )

    def _sleep(_x):
        raise RuntimeError("loop boom")

    monkeypatch.setattr(mod.time, "sleep", _sleep)

    with caplog.at_level(logging.ERROR, logger=mod.logger.name):
        mod.main()

    assert any("Watcher loop error" in r.message for r in caplog.records)
    assert notified and notified[0][0] == "Watcher Error"


# ---------------------------------------------------------------------------
# __main__ guard — unhandled exception path
# ---------------------------------------------------------------------------


def test_dunder_main_handles_unhandled_exception(monkeypatch):
    """Running as ``__main__`` catches a non-SystemExit error and exits 1.

    Covers the module ``__main__`` guard (lines 2086-2102, 2105) by executing the real
    source as ``__main__`` while forcing ``main`` to raise before its own exit paths.
    Compiling with the true file path keeps coverage attribution on
    ``collab/live_locks_watcher.py``.
    """
    from collab import agent_identity

    module_path = _repo_root() / "collab" / "live_locks_watcher.py"
    code = compile(module_path.read_text(encoding="utf-8"), str(module_path), "exec")

    monkeypatch.setenv("SUPABASE_URL", "https://t.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    monkeypatch.setattr(sys, "argv", ["live_locks_watcher.py"])

    def _resolve_boom(*_a, **_k):
        raise RuntimeError("agent resolve boom")

    monkeypatch.setattr(agent_identity, "resolve_agent_id", _resolve_boom)

    namespace = {
        "__name__": "__main__",
        "__file__": str(module_path),
        "__package__": "collab",
    }

    with pytest.raises(SystemExit) as exc:
        exec(code, namespace)

    assert exc.value.code == 1
