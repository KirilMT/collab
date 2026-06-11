"""Tests for editable install detection and warning in LockClient."""

from __future__ import annotations

import json

from ._helpers import load_lock_client_module

mod = load_lock_client_module()


def test_warn_if_non_editable_outside_source_tree(monkeypatch, tmp_path):
    """No warning is emitted when not in a source tree (lock_client.py missing)."""
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))
    lc = mod.LockClient(local_only=True)

    output = []
    monkeypatch.setattr("builtins.print", lambda x: output.append(x))
    lc._warn_if_non_editable()
    assert not output


def test_warn_if_non_editable_detects_editable(monkeypatch, tmp_path):
    """No warning is emitted when an editable install is detected."""
    # Create the source tree marker
    source_dir = tmp_path / "collab"
    source_dir.mkdir()
    (source_dir / "lock_client.py").touch()
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))

    class FakeDist:
        def read_text(self, name):
            if name == "direct_url.json":
                return json.dumps({"dir_info": {"editable": True}})
            return None

    monkeypatch.setattr("importlib.metadata.distribution", lambda name: FakeDist())

    lc = mod.LockClient(local_only=True)
    output = []
    monkeypatch.setattr("builtins.print", lambda x: output.append(x))
    lc._warn_if_non_editable()
    assert not output


def test_warn_if_non_editable_emits_warning(monkeypatch, tmp_path):
    """A warning is emitted for non-editable installs in a source tree."""
    source_dir = tmp_path / "collab"
    source_dir.mkdir()
    (source_dir / "lock_client.py").touch()
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))

    class FakeDist:
        def read_text(self, name):
            if name == "direct_url.json":
                return json.dumps({"dir_info": {"editable": False}})
            return None

    monkeypatch.setattr("importlib.metadata.distribution", lambda name: FakeDist())

    lc = mod.LockClient(local_only=True)
    output = []
    monkeypatch.setattr("builtins.print", lambda x: output.append(x))
    lc._warn_if_non_editable()
    assert any(
        "WARNING: collab is installed as a non-editable package" in s for s in output
    )


def test_warn_if_non_editable_metadata_missing(monkeypatch, tmp_path):
    """A warning is emitted if metadata distribution cannot be found (Exception
    path)."""
    source_dir = tmp_path / "collab"
    source_dir.mkdir()
    (source_dir / "lock_client.py").touch()
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))

    def _boom(name):
        raise RuntimeError("dist not found")

    monkeypatch.setattr("importlib.metadata.distribution", _boom)

    lc = mod.LockClient(local_only=True)
    output = []
    monkeypatch.setattr("builtins.print", lambda x: output.append(x))
    lc._warn_if_non_editable()
    assert any(
        "WARNING: collab is installed as a non-editable package" in s for s in output
    )
