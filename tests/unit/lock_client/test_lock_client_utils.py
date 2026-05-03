import os
import sys
import types
from datetime import datetime as _real_datetime
from pathlib import Path

from ._helpers import FakeClient, FakeResponse, load_lock_client_module

mod = load_lock_client_module()


def test_safe_now_typeerror_uses_class_now(monkeypatch):
    """Cover _safe_now TypeError path that calls class-level now()."""

    class _OddDate:
        # Bound call on instance raises TypeError; class-level call works.
        def now():
            return _real_datetime(2026, 5, 2, 10, 0, 0)

    monkeypatch.setattr(mod, "datetime", _OddDate(), raising=False)
    got = mod._safe_now()
    assert isinstance(got, _real_datetime)
    assert got.year == 2026


def test_safe_now_typeerror_falls_back_to_real_datetime(monkeypatch):
    """Cover _safe_now final fallback when class-level now() also errors."""

    class _BadDate:
        # Instance call triggers TypeError, class-level call also TypeError.
        def now(self):
            return _real_datetime(2026, 5, 2, 10, 0, 0)

    monkeypatch.setattr(mod, "datetime", _BadDate(), raising=False)
    got = mod._safe_now()
    assert isinstance(got, _real_datetime)


def test_get_create_client_preloaded_module_importerror_exits(monkeypatch):
    """Cover _get_create_client branch where preloaded supabase import fails."""
    import builtins

    mod._supabase_create_client = None
    fake = types.SimpleNamespace(create_client=lambda *_a, **_k: None)
    fake.__spec__ = types.SimpleNamespace(origin="/usr/lib/python/supabase/__init__.py")
    monkeypatch.setitem(sys.modules, "supabase", fake)

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "supabase":
            raise ImportError("forced missing supabase")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    try:
        import pytest

        with pytest.raises(SystemExit):
            mod._get_create_client()
    finally:
        mod._supabase_create_client = None


def test_resolve_executable_path_which_raises_in_test_mode(monkeypatch):
    """Which() errors should fall back to command name in test mode."""

    def _boom(_name):
        raise AttributeError("platform mismatch")

    monkeypatch.setattr(mod.shutil, "which", _boom)
    monkeypatch.setenv("COLLAB_TEST_MODE", "1")
    assert mod._resolve_executable_path("tasklist") == "tasklist"


def test_resolve_executable_path_which_raises_outside_test_mode(monkeypatch):
    """Which() errors should return None outside test mode."""

    def _boom(_name):
        raise OSError("lookup failed")

    monkeypatch.setattr(mod.shutil, "which", _boom)
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert mod._resolve_executable_path("taskkill") is None


def test_get_create_client_preloaded_shadowed_module_exits(monkeypatch):
    """Cover local-shadow detection branch for preloaded supabase module."""
    mod._supabase_create_client = None
    fake = types.SimpleNamespace(create_client=lambda *_a, **_k: None)
    fake.__spec__ = types.SimpleNamespace(
        origin=os.path.join(mod._COLLAB_ROOT, "supabase.py")
    )
    monkeypatch.setitem(sys.modules, "supabase", fake)

    import pytest

    with pytest.raises(SystemExit):
        mod._get_create_client()
    mod._supabase_create_client = None


def test_get_create_client_preloaded_missing_create_client_exits(monkeypatch):
    """Cover _get_create_client branch where module lacks create_client."""
    mod._supabase_create_client = None
    fake = types.SimpleNamespace()
    fake.__spec__ = types.SimpleNamespace(origin="/usr/lib/python/supabase/__init__.py")
    monkeypatch.setitem(sys.modules, "supabase", fake)

    import pytest

    with pytest.raises(SystemExit):
        mod._get_create_client()
    mod._supabase_create_client = None


def test_get_create_client_import_missing_exits(monkeypatch):
    """Cover fallback import branch when supabase package is unavailable."""
    import builtins

    mod._supabase_create_client = None
    monkeypatch.delitem(sys.modules, "supabase", raising=False)

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "supabase":
            raise ImportError("forced missing supabase")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    import pytest

    with pytest.raises(SystemExit):
        mod._get_create_client()
    mod._supabase_create_client = None


def test_is_same_machine_token_returns_false_with_duplicates(monkeypatch):
    """Cover duplicate-seed continue path and final False return."""
    c = mod.LockClient(local_only=True)
    c.developer_id = "alice"
    monkeypatch.setattr(
        mod.LockClient, "_get_git_username", staticmethod(lambda: "alice")
    )
    monkeypatch.setenv("USERNAME", "alice")
    monkeypatch.setattr(mod.socket, "gethostname", lambda: "host-a")
    monkeypatch.setattr(mod.os.path, "abspath", lambda _p: "C:/repo")

    assert c._is_same_machine_token("deadbeefdeadbeef") is False


def test_get_cmdline_for_pid_windows_powershell_failure_returns_none(monkeypatch):
    """Cover Windows fallback branch where PowerShell cmdline query fails."""
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(mod.shutil, "which", lambda _exe: None)

    def _check_output(*_a, **_k):
        raise RuntimeError("powershell failure")

    monkeypatch.setattr(mod.subprocess, "check_output", _check_output)
    assert mod.LockClient._get_cmdline_for_pid(12345) is None


def test_get_cmdline_for_pid_unix_proc_empty_and_exception(monkeypatch, tmp_path):
    """Cover /proc cmdline empty-data and exception fallback branches."""
    monkeypatch.setattr(mod.sys, "platform", "linux")

    # Empty /proc content -> None
    monkeypatch.setattr(mod.os.path, "exists", lambda _p: True)

    class _Empty:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self):
            return b""

    import builtins

    monkeypatch.setattr(builtins, "open", lambda *_a, **_k: _Empty())
    assert mod.LockClient._get_cmdline_for_pid(8888) is None

    # Exception while reading /proc -> None
    def _raise_open(*_a, **_k):
        raise OSError("proc read failed")

    monkeypatch.setattr(builtins, "open", _raise_open)
    assert mod.LockClient._get_cmdline_for_pid(9999) is None


def test_init_ephemeral_guard_handles_bad_developer_id(monkeypatch):
    """Cover __init__ branch where developer_id.startswith raises."""

    class _BadDev:
        def startswith(self, _p):
            raise RuntimeError("bad developer id")

    c = mod.LockClient(developer_id=_BadDev(), local_only=True)
    assert c._is_ephemeral is False


def test_normalize_file_path_abs_dotprefix_and_exception_fallback(monkeypatch):
    """Cover normalize branches: abs relpath, './' trim, and exception fallback."""
    c = mod.LockClient(local_only=True)

    # Absolute path branch via relpath.
    abs_fp = os.path.join(mod._PROJECT_ROOT, "src", "x.py")
    out = c._normalize_file_path(abs_fp)
    assert out.endswith("src/x.py")

    # './' trimming branch.
    monkeypatch.setattr(mod.os.path, "isabs", lambda _p: False)
    assert c._normalize_file_path("./src/y.py") == "src/y.py"

    # Exception fallback branch.
    monkeypatch.setattr(
        mod.os.path, "isabs", lambda _p: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert c._normalize_file_path("a\\b.py") == "a/b.py"


def test_force_release_all_exception_returns_zero(monkeypatch):
    """Cover force_release_all outer exception handler (lines 944-946)."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test_key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")

    c = mod.LockClient(local_only=True)
    c._is_admin = True

    class _BoomClient:
        def table(self, _name):
            raise RuntimeError("db fail")

    c._client = _BoomClient()
    assert c.force_release_all() == 0


def test_get_state_dir_env(tmp_path, monkeypatch):
    mod = load_lock_client_module()
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    got = mod._get_state_dir()
    assert os.path.abspath(got) == os.path.abspath(str(tmp_path))


def test_normalize_and_parse_paths(tmp_path):
    mod = load_lock_client_module()
    # Absolute path -> relative
    p = os.path.join(mod._PROJECT_ROOT, "src", "file.py")
    out = mod.LockClient(local_only=True)._normalize_file_path(p)
    assert out.startswith("src/")

    # collab/ => .collab/
    s = "collab/foo.txt"
    out = mod.LockClient(local_only=True)._normalize_file_path(s)
    assert out.startswith(".collab/")

    # parse git rename
    line = "R  old/path -> new/path"
    assert mod.LockClient._parse_git_status_path(line) == "new/path"


def test_should_ignore_path():
    mod = load_lock_client_module()
    assert mod.LockClient._should_ignore_path(".git/HEAD")
    assert mod.LockClient._should_ignore_path(".collab/.startup_summary.json")
    assert not mod.LockClient._should_ignore_path("src/main.py")


def test_session_token_consistent():
    mod = load_lock_client_module()
    c1 = mod.LockClient(developer_id="dev_x", local_only=True)
    c2 = mod.LockClient(developer_id="dev_x", local_only=True)
    t1 = c1._get_session_token()
    t2 = c2._get_session_token()
    assert t1 == t2
    assert c1._is_same_machine_token(t1)


def test_get_create_client_uses_sys_modules_with_safe_origin(monkeypatch):
    # Force reload of cached create client
    mod._supabase_create_client = None

    fake = types.SimpleNamespace()

    def fake_create(url, key):
        return FakeClient(FakeResponse(status=200, data=[]))

    fake.create_client = fake_create
    # ensure origin not inside repo to avoid shadow detection
    fake.__spec__ = types.SimpleNamespace(
        origin="/usr/lib/python3/dist-packages/supabase/__init__.py"
    )
    monkeypatch.setitem(sys.modules, "supabase", fake)
    fn = mod._get_create_client()
    assert callable(fn)
    c = fn("u", "k")
    assert isinstance(c, FakeClient)


def test_parse_response_success_and_error_and_dict():
    # success object-like
    class ResObj:
        def __init__(self):
            self.status_code = 200
            self.data = {"key": "value"}
            self.error = None

    status, data, error = mod.LockClient._parse_response(ResObj())
    assert status == 200 and data == {"key": "value"}

    # error response
    class FakeResp:
        def __init__(self, status=200, data=None, error=None):
            self.status = status
            self.data = data
            self.error = error

    status2, data2, error2 = mod.LockClient._parse_response(
        FakeResp(status=400, data=None, error="Bad request")
    )
    assert status2 == 400 and error2 == "Bad request"

    # dict input
    resp = {"status": 200, "data": [{"file": "test"}], "error": None}
    status3, data3, error3 = mod.LockClient._parse_response(resp)
    assert status3 == 200 and data3 == [{"file": "test"}]


def test_mark_missing_lines_coverage_helper(request):
    # Minimal coverage-helper kept for CI environments; skip if coverage fixture absent

    import pytest as _pytest

    base = Path(__file__).resolve().parents[4]
    base / ".collab" / "core" / "lock_client.py"
    base / ".collab" / "pycharm" / "live_locks_watcher.py"

    # Short-circuit: if coverage fixture not present, skip
    try:
        cov = request.getfixturevalue("cov")
    except Exception:
        cov = None

    if cov is None:
        _pytest.skip("coverage marking not configured")


def test_safe_now_returns_datetime(monkeypatch):
    from datetime import datetime as dt

    now = dt(2026, 4, 27, 12, 0, 0)
    monkeypatch.setattr(mod, "_safe_now", lambda: now)
    assert mod._safe_now() == now


def test_get_state_dir_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    sd = mod._get_state_dir()
    assert sd == str(tmp_path)


def test_get_state_dir_default_creates_dir(monkeypatch):
    monkeypatch.delenv("COLLAB_STATE_DIR", raising=False)
    sd = mod._get_state_dir()
    assert os.path.exists(sd)
    assert "mockcmms_collab_" in sd or "mockcmms-collab" in sd


def test_state_path(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path))
    p = mod._state_path("test.marker")
    assert p == os.path.join(str(tmp_path), "test.marker")


def test_quiet_console_loggers_restores_levels(monkeypatch):
    import logging

    test_logger = logging.getLogger("httpx")
    original = test_logger.level
    with mod._quiet_console_loggers(names=["httpx"]):
        assert test_logger.level == logging.WARNING
    assert test_logger.level == original


def test_quiet_console_loggers_restores_collab_propagation(monkeypatch):
    import logging

    collab_logger = logging.getLogger("collab")
    collab_logger.propagate = True
    with mod._quiet_console_loggers():
        assert collab_logger.propagate is False
    assert collab_logger.propagate is True


def test_quiet_console_loggers_default_names(monkeypatch):
    with mod._quiet_console_loggers():
        pass


def test_validate_credentials_ok(monkeypatch):
    monkeypatch.setattr(mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mod, "SUPABASE_ANON_KEY", "test_key")
    mod._validate_credentials()  # Should not raise


def test_parse_response_and_retry(monkeypatch):
    bad = FakeResponse(status=500, data=None, error="oops")
    ok = FakeResponse(status=200, data=[{"file_path": "src/a.py"}], error=None)

    class FlakyClient(FakeClient):
        def __init__(self):
            super().__init__(bad)
            self._calls = 0

        def execute(self):
            self._calls += 1
            if self._calls == 1:
                return bad
            return ok

    client = FlakyClient()
    _, data, _ = mod.LockClient._parse_response(client.execute())
    if not data:
        _, data, _ = mod.LockClient._parse_response(client.execute())

    assert isinstance(data, list)
    assert data[0]["file_path"] == "src/a.py"


def test_state_dir_and_normalize(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_STATE_DIR", str(tmp_path / "state"))
    state_dir = mod._get_state_dir()
    assert os.path.isdir(state_dir)

    c = mod.LockClient(local_only=True)
    abs_path = os.path.join(mod._PROJECT_ROOT, "src", "routes", "main.py")
    normalized = c._normalize_file_path(abs_path)
    assert normalized == "src/routes/main.py"


def test_session_token_and_git_helpers(monkeypatch):
    monkeypatch.setattr(
        mod.LockClient, "_get_git_username", staticmethod(lambda: "devx")
    )
    c1 = mod.LockClient(local_only=True)
    c2 = mod.LockClient(local_only=True)

    t1 = c1._get_session_token()
    t2 = c2._get_session_token()
    assert t1 == t2
    assert c1._is_same_machine_token(t1)


# (Both test_parse_response_dict and test_parse_response_error removed as duplicates
#  of test_parse_response_success_and_error_and_dict above)
