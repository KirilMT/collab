"""Tests for the `collab init-hooks` CLI command in collab/main.py."""

from __future__ import annotations

import sys

import pytest


def _stub_runtime(monkeypatch):
    """Silence logging/stream side effects so init-hooks runs in isolation."""
    import collab.lock_client as lc
    import collab.logging_config as logging_config
    import collab.main as main_mod

    monkeypatch.setattr(
        main_mod, "setup_collab_logging", lambda **_k: None, raising=False
    )
    monkeypatch.setattr(lc, "_COLLAB_ROOT", ".collab", raising=False)
    monkeypatch.setattr(logging_config, "setup_collab_logging", lambda **_k: None)


def test_init_hooks_command_installs_and_exits_zero(monkeypatch, capsys):
    import collab.githooks as githooks
    import collab.main as main_mod

    _stub_runtime(monkeypatch)

    captured = {}

    def _fake(force=False):
        captured["force"] = force
        return {
            "installed": ["pre-commit", "pre-push"],
            "skipped": ["commit-msg"],
            "hooks_dir": "/repo/.git/hooks",
        }

    monkeypatch.setattr(githooks, "install_hooks", _fake)
    monkeypatch.setattr(sys, "argv", ["collab", "init-hooks"])

    with pytest.raises(SystemExit) as exc:
        main_mod._run_cli()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "Installed collab git hooks into /repo/.git/hooks" in out
    assert "pre-commit, pre-push" in out
    assert "Skipped" in out
    assert captured["force"] is False


def test_init_hooks_command_forwards_force(monkeypatch, capsys):
    import collab.githooks as githooks
    import collab.main as main_mod

    _stub_runtime(monkeypatch)

    captured = {}

    def _fake(force=False):
        captured["force"] = force
        return {
            "installed": ["pre-commit"],
            "skipped": [],
            "hooks_dir": "/repo/.git/hooks",
        }

    monkeypatch.setattr(githooks, "install_hooks", _fake)
    monkeypatch.setattr(sys, "argv", ["collab", "init-hooks", "--force"])

    with pytest.raises(SystemExit) as exc:
        main_mod._run_cli()
    assert exc.value.code == 0
    assert captured["force"] is True
    out = capsys.readouterr().out
    assert "Skipped" not in out


def test_init_hooks_reports_updated_and_backed_up(monkeypatch, capsys):
    """Auto-updated + backed-up hooks are reported without double-listing (#181)."""
    import collab.githooks as githooks
    import collab.main as main_mod

    _stub_runtime(monkeypatch)

    def _fake(force=False):
        # ``installed`` includes fresh writes AND auto-updated hooks; ``pre-push``
        # here was auto-updated, ``post-commit`` is a fresh install.
        return {
            "installed": ["post-commit", "pre-push"],
            "updated": ["pre-push"],
            "up_to_date": ["commit-msg"],
            "backed_up": ["custom.bak"],
            "skipped": [],
            "hooks_dir": "/repo/.git/hooks",
        }

    monkeypatch.setattr(githooks, "install_hooks", _fake)
    monkeypatch.setattr(sys, "argv", ["collab", "init-hooks"])

    with pytest.raises(SystemExit) as exc:
        main_mod._run_cli()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    # Fresh install shown, auto-updated hook excluded from the "Installed" line.
    assert "Installed: post-commit" in out
    assert "Installed: post-commit, pre-push" not in out
    assert "Updated (template changed): pre-push" in out
    assert "Backed up before overwrite: custom.bak" in out


def test_init_hooks_reports_precommit_managed_without_force_hint(monkeypatch, capsys):
    """Framework-owned slots are reported as managed, not as --force-able skips
    (#181)."""
    import collab.githooks as githooks
    import collab.main as main_mod

    _stub_runtime(monkeypatch)

    def _fake(force=False):
        return {
            "installed": ["post-commit", "post-merge", "post-checkout"],
            "updated": [],
            "skipped": [],
            "precommit_managed": ["pre-commit", "pre-push", "commit-msg"],
            "backed_up": [],
            "hooks_dir": "/repo/.git/hooks",
        }

    monkeypatch.setattr(githooks, "install_hooks", _fake)
    monkeypatch.setattr(sys, "argv", ["collab", "init-hooks"])

    with pytest.raises(SystemExit) as exc:
        main_mod._run_cli()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "Managed by pre-commit" in out
    assert "pre-commit, pre-push, commit-msg" in out
    assert "rerun with --force" not in out
