"""Lifecycle error boundary tests for PID parsing (Phase 5.1)."""

from __future__ import annotations

import json

import pytest

from collab.errors import PidParseError
from collab.lock_client import LockClient


def test_read_pid_strict_raises_on_invalid_json(tmp_path, monkeypatch):
    pid_path = tmp_path / ".daemon.pid"
    pid_path.write_text('{"pid": "not-an-int"}', encoding="utf-8")
    monkeypatch.setattr("collab.lock_client.PID_FILE", str(pid_path))
    with pytest.raises(PidParseError):
        LockClient._read_pid(strict=True)


def test_read_pid_strict_raises_on_non_integer(tmp_path, monkeypatch):
    pid_path = tmp_path / ".daemon.pid"
    pid_path.write_text("not-a-pid", encoding="utf-8")
    monkeypatch.setattr("collab.lock_client.PID_FILE", str(pid_path))
    with pytest.raises(PidParseError):
        LockClient._read_pid(strict=True)


def test_read_pid_non_strict_returns_none(tmp_path, monkeypatch):
    pid_path = tmp_path / ".daemon.pid"
    pid_path.write_text("not-a-pid", encoding="utf-8")
    monkeypatch.setattr("collab.lock_client.PID_FILE", str(pid_path))
    assert LockClient._read_pid(strict=False) is None


def test_read_pid_strict_accepts_json_metadata(tmp_path, monkeypatch):
    pid_path = tmp_path / ".daemon.pid"
    pid_path.write_text(json.dumps({"pid": 4242}), encoding="utf-8")
    monkeypatch.setattr("collab.lock_client.PID_FILE", str(pid_path))
    assert LockClient._read_pid(strict=True) == 4242


def test_read_pid_strict_raises_on_invalid_json_document(tmp_path, monkeypatch):
    pid_path = tmp_path / ".daemon.pid"
    pid_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr("collab.lock_client.PID_FILE", str(pid_path))
    with pytest.raises(PidParseError, match="invalid JSON"):
        LockClient._read_pid(strict=True)


def test_read_pid_strict_raises_when_pid_file_unreadable(tmp_path, monkeypatch):
    import builtins

    pid_path = tmp_path / ".daemon.pid"
    pid_path.write_text("123", encoding="utf-8")
    monkeypatch.setattr("collab.lock_client.PID_FILE", str(pid_path))

    real_open = builtins.open

    def _open(path, *args, **kwargs):
        if str(path) == str(pid_path):
            raise OSError("permission denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)
    with pytest.raises(PidParseError, match="Could not read PID file"):
        LockClient._read_pid(strict=True)
