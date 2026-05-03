from __future__ import annotations

import os

import pytest

from ._helpers import load_watcher_module


@pytest.fixture(autouse=True)
def isolate_collab(monkeypatch, tmp_path):
    """
    Autouse fixture: ensure the watcher module uses a test-local `.collab`
    root and PID file so tests cannot accidentally modify repository files.
    """
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "_COLLAB_ROOT", str(tmp_path))
    pid = tmp_path / f"pytest_collab_{os.getpid()}.daemon.pid"
    monkeypatch.setattr(mod, "PID_FILE", str(pid))
    yield
