import json
import os

from ._helpers import load_lock_client_module

mod = load_lock_client_module()


def test_read_pid_missing(monkeypatch, tmp_path):
    mod = load_lock_client_module()
    # Point PID_FILE to a non-existent path
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "missing.pid"))
    assert mod.LockClient._read_pid() is None


def test_read_pid_plain_integer(monkeypatch, tmp_path):
    mod = load_lock_client_module()
    p = tmp_path / "plain.pid"
    p.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(p))
    assert mod.LockClient._read_pid() == 12345


def test_read_pid_json_and_invalid(monkeypatch, tmp_path):
    mod = load_lock_client_module()
    p = tmp_path / "meta.pid"
    p.write_text(json.dumps({"pid": 54321, "started": "now"}))
    monkeypatch.setattr(mod, "PID_FILE", str(p))
    assert mod.LockClient._read_pid() == 54321

    # Invalid JSON should return None
    p2 = tmp_path / "bad.pid"
    p2.write_text("{not json")
    monkeypatch.setattr(mod, "PID_FILE", str(p2))
    assert mod.LockClient._read_pid() is None


def test_read_pid_file_method(monkeypatch, tmp_path):
    mod = load_lock_client_module()
    p = tmp_path / "meta2.pid"
    data = {"pid": 1111, "cwd": "C:/proj"}
    p.write_text(json.dumps(data))
    monkeypatch.setattr(mod, "PID_FILE", str(p))
    client = mod.LockClient(local_only=True)
    meta = client._read_pid_file()
    assert isinstance(meta, dict)
    assert meta.get("pid") == 1111


def test_write_pid_fallback_on_replace_failure(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    replace_called = [False]

    def failing_replace(src, dst):
        replace_called[0] = True
        if not mod.os.path.exists(pid_file):
            raise OSError("Access denied")

    monkeypatch.setattr(mod.os, "replace", failing_replace)

    mod.LockClient._write_pid(12345, parent_pid=678, token="tok123")
    assert pid_file.exists()
    data = json.loads(pid_file.read_text())
    assert data["pid"] == 12345


def test_touch_pid_heartbeat_updates_mtime(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text('{"pid": 1}')
    stale_mtime = 1_000_000.0
    os.utime(pid_file, (stale_mtime, stale_mtime))
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    mod.LockClient._touch_pid_heartbeat()

    assert os.path.getmtime(pid_file) > stale_mtime


def test_touch_pid_heartbeat_missing_file_is_noop(monkeypatch, tmp_path):
    missing = str(tmp_path / "missing.pid")
    monkeypatch.setattr(mod, "PID_FILE", missing)
    mod.LockClient._touch_pid_heartbeat()
    # A missing PID file must not be created by the heartbeat touch.
    assert not os.path.exists(mod.PID_FILE)


def test_touch_pid_heartbeat_oserror_is_swallowed(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("1")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    stale_mtime = 1_000_000.0
    os.utime(pid_file, (stale_mtime, stale_mtime))

    calls: list = []

    def boom(path, times):
        calls.append((path, times))
        raise OSError("permission denied")

    monkeypatch.setattr(mod.os, "utime", boom)

    # Must not raise even though utime fails.
    mod.LockClient._touch_pid_heartbeat()

    # utime was attempted exactly once and the failure left mtime untouched.
    assert len(calls) == 1
    assert os.path.getmtime(pid_file) == stale_mtime


def test_remove_pid_notest_mode(monkeypatch, tmp_path):
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    mod.LockClient._remove_pid()
    assert not pid_file.exists()
