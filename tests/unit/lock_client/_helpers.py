"""Shared helpers for LockClient tests in the lock_client test package."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import mock

# Ensure supabase is mocked so importing the module doesn't raise if supabase
# isn't installed in the test environment.
sys.modules.setdefault("supabase", mock.MagicMock())


def _find_lock_client_path() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        candidate = parent / ".collab" / "core" / "lock_client.py"
        if candidate.exists():
            return candidate
    # Last-resort fallback (older layouts)
    candidate = (
        Path(__file__).resolve().parents[4] / ".collab" / "core" / "lock_client.py"
    )
    if candidate.exists():
        return candidate
    raise FileNotFoundError("lock_client.py not found in repo")


def load_lock_client_module():
    candidate = _find_lock_client_path()
    spec = importlib.util.spec_from_file_location(
        "collab.core.lock_client", str(candidate)
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore[arg-type]
    return mod


def _load_lock_client_module():
    """Backward-compatible alias for older tests that call `_load_lock_client_module()`.

    Prefer `load_lock_client_module()` in new tests.
    """

    return load_lock_client_module()


def _find_watcher_path() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        candidate = parent / ".collab" / "pycharm" / "live_locks_watcher.py"
        if candidate.exists():
            return candidate
    candidate = (
        Path(__file__).resolve().parents[4]
        / ".collab"
        / "pycharm"
        / "live_locks_watcher.py"
    )
    if candidate.exists():
        return candidate
    raise FileNotFoundError("live_locks_watcher.py not found in repo")


def load_watcher_module():
    candidate = _find_watcher_path()
    spec = importlib.util.spec_from_file_location(
        "collab.pycharm.live_locks_watcher", str(candidate)
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore[arg-type]
    return mod


class FakeResponse:
    def __init__(self, status=200, data=None, error=None):
        self.status = status
        self.data = data if data is not None else []
        self.error = error


class FakeClient:
    def __init__(self, resp):
        self._resp = resp

    def rpc(self, *a, **k):
        return self

    def table(self, *a, **k):
        return self

    def select(self, *a, **k):
        return self

    def delete(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def insert(self, *a, **k):
        return self

    def update(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def ilike(self, *a, **k):
        return self

    def execute(self):
        return self._resp


def make_create_client(resp):
    def fake_create(url, key):
        return FakeClient(resp)

    return fake_create


def make_get_create_client(resp):
    def fake_get_create_client():
        def create_client(url, key):
            return FakeClient(resp)

        return create_client

    return fake_get_create_client
