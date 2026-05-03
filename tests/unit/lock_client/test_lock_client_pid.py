import json

from ._helpers import load_lock_client_module

mod = load_lock_client_module()


def test_read_pid_missing(tmp_path):
    mod = load_lock_client_module()
    # Point PID_FILE to a non-existent path
    mod.PID_FILE = str(tmp_path / "missing.pid")
    assert mod.LockClient._read_pid() is None


def test_read_pid_plain_integer(tmp_path):
    mod = load_lock_client_module()
    p = tmp_path / "plain.pid"
    p.write_text("12345")
    mod.PID_FILE = str(p)
    assert mod.LockClient._read_pid() == 12345


def test_read_pid_json_and_invalid(tmp_path):
    mod = load_lock_client_module()
    p = tmp_path / "meta.pid"
    p.write_text(json.dumps({"pid": 54321, "started": "now"}))
    mod.PID_FILE = str(p)
    assert mod.LockClient._read_pid() == 54321

    # Invalid JSON should return None
    p2 = tmp_path / "bad.pid"
    p2.write_text("{not json")
    mod.PID_FILE = str(p2)
    assert mod.LockClient._read_pid() is None


def test_read_pid_file_method(tmp_path):
    mod = load_lock_client_module()
    p = tmp_path / "meta2.pid"
    data = {"pid": 1111, "cwd": "C:/proj"}
    p.write_text(json.dumps(data))
    mod.PID_FILE = str(p)
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


def test_remove_pid_notest_mode(monkeypatch, tmp_path):
    monkeypatch.delenv("COLLAB_TEST_MODE", raising=False)
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(mod, "PID_FILE", str(pid_file))

    mod.LockClient._remove_pid()
    assert not pid_file.exists()
