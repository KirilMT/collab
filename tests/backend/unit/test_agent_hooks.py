"""Unit tests for collab.agent_hooks (IDE attribution runner + installer)."""

from __future__ import annotations

import json
import os

import pytest
import pytest as _pytest

from collab import agent_hooks


@_pytest.fixture(autouse=True)
def _silence_diag_log(monkeypatch):
    """Keep unit tests from writing to ``logs/agent_hooks.log``."""
    monkeypatch.setenv("COLLAB_AGENT_HOOKS_DEBUG", "0")


# --------------------------------------------------------------------------- #
# Event parsing
# --------------------------------------------------------------------------- #


def test_extract_paths_from_varied_keys():
    event = {
        "file_path": "a.py",
        "edits": [{"path": "b/c.py"}],
        "nested": {"absolutePath": "d.txt"},
        "uri": "file:///C:/repo/e.py",
        "ignored": "not-a-path",
    }
    paths = agent_hooks._extract_paths(event)
    assert "a.py" in paths
    assert "b/c.py" in paths
    assert "d.txt" in paths
    assert any(p.endswith("e.py") for p in paths)
    assert "not-a-path" not in paths


def test_extract_paths_handles_list_of_strings():
    event = {"path": ["one.py", "two/three.py", "skip"]}
    paths = agent_hooks._extract_paths(event)
    assert "one.py" in paths
    assert "two/three.py" in paths


def test_normalize_path_variants():
    assert agent_hooks._normalize_path("file:///C:/repo/x.py").endswith("x.py")
    assert agent_hooks._normalize_path("test") == "test"
    assert agent_hooks._normalize_path("Makefile") == "Makefile"
    assert agent_hooks._normalize_path("has space.py") is None
    assert agent_hooks._normalize_path("dir/sub/file.py") == "dir/sub/file.py"
    assert agent_hooks._normalize_path("") is None
    assert agent_hooks._normalize_path("line\nbreak.py") is None


def test_extract_paths_accepts_extensionless_basename():
    assert agent_hooks._extract_paths({"file_path": "test"}) == ["test"]


def test_read_event_edge_cases():
    assert agent_hooks._read_event("   ") == {}
    assert agent_hooks._read_event("not-json{") == {}
    assert agent_hooks._read_event("[1, 2]") == {}
    assert agent_hooks._read_event('{"file_path": "a.py"}') == {"file_path": "a.py"}


def test_read_event_strips_utf8_bom():
    # Cursor on Windows prefixes the payload with a UTF-8 BOM; it must parse.
    payload = '\ufeff{"file_path": "a.py"}'
    assert agent_hooks._read_event(payload) == {"file_path": "a.py"}


def test_read_event_tolerates_leading_garbage_before_brace():
    payload = 'XX{"file_path": "a.py"}'
    assert agent_hooks._read_event(payload) == {"file_path": "a.py"}


def test_read_stdin_text_decodes_bom_from_buffer(monkeypatch):
    import io

    raw = '\ufeff{"file_path": "a.py"}'.encode("utf-8-sig")

    class _Stdin:
        buffer = io.BytesIO(raw)

    monkeypatch.setattr(agent_hooks.sys, "stdin", _Stdin())
    text = agent_hooks._read_stdin_text()
    assert text.startswith("{") or text.lstrip("\ufeff").startswith("{")
    assert agent_hooks._read_event(text) == {"file_path": "a.py"}


def test_read_stdin_text_handles_read_failure(monkeypatch):
    class _Boom:
        def read(self):
            raise OSError("no stdin")

    monkeypatch.setattr(agent_hooks.sys, "stdin", _Boom())
    assert agent_hooks._read_stdin_text() == ""


def test_read_event_reads_stdin(monkeypatch):
    import io

    monkeypatch.setattr(agent_hooks.sys, "stdin", io.StringIO('{"path": "z.py"}'))
    assert agent_hooks._read_event() == {"path": "z.py"}

    class Boom:
        def read(self):
            raise OSError("no stdin")

    monkeypatch.setattr(agent_hooks.sys, "stdin", Boom())
    assert agent_hooks._read_event() == {}


def test_stable_agent_id_precedence(monkeypatch):
    monkeypatch.setenv("COLLAB_AGENT_ID", "agent-explicit")
    assert agent_hooks._stable_agent_id({}) == "agent-explicit"

    monkeypatch.delenv("COLLAB_AGENT_ID", raising=False)
    a = agent_hooks._stable_agent_id({"conversationId": "conv-1"})
    b = agent_hooks._stable_agent_id({"conversationId": "conv-1"})
    assert a == b and a.startswith("agent-")
    assert agent_hooks._stable_agent_id({"conversationId": "conv-2"}) != a

    # No session anywhere -> falls back to a cwd-derived stable id.
    monkeypatch.delenv("COLLAB_AGENT_ID", raising=False)
    cwd_id = agent_hooks._stable_agent_id({})
    assert cwd_id.startswith("agent-")


def test_detect_kind(monkeypatch):
    monkeypatch.delenv("COLLAB_AGENT_KIND", raising=False)
    for env_name in ("CURSOR_TRACE_ID", "CLAUDE_CODE", "GITHUB_COPILOT_AGENT_ID"):
        monkeypatch.delenv(env_name, raising=False)
    assert agent_hooks._detect_kind() == "other"
    monkeypatch.setenv("CURSOR_TRACE_ID", "t")
    assert agent_hooks._detect_kind() == "cursor"
    monkeypatch.setenv("COLLAB_AGENT_KIND", "claude-code")
    assert agent_hooks._detect_kind() == "claude-code"


def test_detect_kind_from_event_cursor(monkeypatch):
    monkeypatch.delenv("COLLAB_AGENT_KIND", raising=False)
    for env_name in ("CURSOR_TRACE_ID", "CLAUDE_CODE", "GITHUB_COPILOT_AGENT_ID"):
        monkeypatch.delenv(env_name, raising=False)
    event = {"cursor_version": "1.2.3", "file_path": "a.py"}
    assert agent_hooks._detect_kind_from_event(event) == "cursor"
    assert agent_hooks._detect_kind(event) == "cursor"


def test_detect_kind_from_event_claude(monkeypatch):
    monkeypatch.delenv("COLLAB_AGENT_KIND", raising=False)
    event = {"hook_event_name": "PostToolUse", "tool_name": "Write"}
    assert agent_hooks._detect_kind_from_event(event) == "claude-code"


def test_detect_kind_from_event_unknown_returns_none():
    assert agent_hooks._detect_kind_from_event({"file_path": "a.py"}) is None
    assert agent_hooks._detect_kind_from_event(None) is None


def test_detect_kind_explicit_env_wins_over_event(monkeypatch):
    monkeypatch.setenv("COLLAB_AGENT_KIND", "composer")
    assert agent_hooks._detect_kind({"cursor_version": "1.0"}) == "composer"


def test_run_ide_hook_sets_cursor_kind_from_event(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COLLAB_AGENT_KIND", raising=False)
    for env_name in ("CURSOR_TRACE_ID", "CLAUDE_CODE", "GITHUB_COPILOT_AGENT_ID"):
        monkeypatch.delenv(env_name, raising=False)
    captured: dict = {}
    monkeypatch.setattr(
        agent_hooks.safe_subprocess,
        "spawn_background",
        lambda cmd, **k: captured.update(env=k.get("env", {})),
    )
    payload = json.dumps({"cursor_version": "1.2.3", "file_path": "src/a.py"})
    agent_hooks.run_ide_hook(["--from-ide-hook"], stdin_text=payload)
    assert captured["env"]["COLLAB_AGENT_KIND"] == "cursor"


def test_detect_kind_handles_import_failure(monkeypatch):
    monkeypatch.delenv("COLLAB_AGENT_KIND", raising=False)

    def _boom(*_a, **_k):
        raise RuntimeError("no module")

    # Force the agent_identity lookup to raise; runner must degrade to "other".
    monkeypatch.setattr(
        agent_hooks, "_detect_kind", agent_hooks._detect_kind
    )  # keep reference
    import collab.agent_identity as ai

    monkeypatch.setattr(ai, "detect_agent_runtime_label", _boom)
    assert agent_hooks._detect_kind() == "other"


def test_resolve_label(monkeypatch):
    monkeypatch.setenv("COLLAB_AGENT_LABEL", "from-env")
    assert agent_hooks._resolve_label({"title": "x"}) == "from-env"
    monkeypatch.delenv("COLLAB_AGENT_LABEL", raising=False)
    assert agent_hooks._resolve_label({"title": "from-event"}) == "from-event"
    assert agent_hooks._resolve_label({}) is None


# --------------------------------------------------------------------------- #
# Runner: run_ide_hook
# --------------------------------------------------------------------------- #


def test_diag_log_writes_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_AGENT_HOOKS_DEBUG", "1")
    monkeypatch.chdir(tmp_path)
    agent_hooks._diag_log("hello-diag")
    log = tmp_path / "logs" / "agent_hooks.log"
    assert log.exists()
    assert "hello-diag" in log.read_text(encoding="utf-8")


def test_diag_log_disabled_is_silent(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLAB_AGENT_HOOKS_DEBUG", "0")
    monkeypatch.chdir(tmp_path)
    agent_hooks._diag_log("should-not-write")
    assert not (tmp_path / "logs").exists()


def test_hook_enabled(monkeypatch):
    monkeypatch.delenv("COLLAB_AGENT_HOOKS", raising=False)
    assert agent_hooks._hook_enabled(["--from-ide-hook"]) is True
    assert agent_hooks._hook_enabled([]) is False
    monkeypatch.setenv("COLLAB_AGENT_HOOKS", "1")
    assert agent_hooks._hook_enabled([]) is True


def test_run_ide_hook_disabled_is_noop(monkeypatch):
    monkeypatch.delenv("COLLAB_AGENT_HOOKS", raising=False)
    called = {"run": False}
    monkeypatch.setattr(
        agent_hooks.safe_subprocess,
        "spawn_background",
        lambda *a, **k: called.__setitem__("run", True),
    )
    assert agent_hooks.run_ide_hook([], stdin_text='{"file_path": "a.py"}') == 0
    assert called["run"] is False


def test_run_ide_hook_no_paths_is_noop(monkeypatch):
    called = {"run": False}
    monkeypatch.setattr(
        agent_hooks.safe_subprocess,
        "spawn_background",
        lambda *a, **k: called.__setitem__("run", True),
    )
    assert agent_hooks.run_ide_hook(["--from-ide-hook"], stdin_text="{}") == 0
    assert called["run"] is False


def test_run_ide_hook_invokes_claim(monkeypatch):
    monkeypatch.delenv("COLLAB_AGENT_LABEL", raising=False)
    monkeypatch.delenv("COLLAB_AGENT_ID", raising=False)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})

    monkeypatch.setattr(agent_hooks.safe_subprocess, "spawn_background", fake_run)
    rc = agent_hooks.run_ide_hook(
        ["--from-ide-hook"],
        stdin_text='{"file_path": "collab/app.py", "title": "fix-ci"}',
    )
    assert rc == 0
    assert "claim" in captured["cmd"]
    assert "collab/app.py" in captured["cmd"]
    assert "--label" in captured["cmd"]
    assert "fix-ci" in captured["cmd"]
    assert "--reason" in captured["cmd"]
    assert captured["env"]["COLLAB_AGENT_MODE"] == "1"
    assert captured["env"]["COLLAB_AGENT_ID"]
    assert captured["env"]["COLLAB_AGENT_KIND"]


def test_run_ide_hook_without_label(monkeypatch):
    monkeypatch.delenv("COLLAB_AGENT_LABEL", raising=False)
    captured = {}
    monkeypatch.setattr(
        agent_hooks.safe_subprocess,
        "spawn_background",
        lambda cmd, **k: captured.update(cmd=cmd),
    )
    agent_hooks.run_ide_hook(["--from-ide-hook"], stdin_text='{"path": "a.py"}')
    assert "--label" not in captured["cmd"]


def test_run_ide_hook_windows_uses_detached_flags(monkeypatch):
    # Isolate the spawn-branch assertion from path resolution: forcing os.name
    # changes pathlib's flavour, so stub the repo filter to always accept.
    monkeypatch.setattr(agent_hooks, "_is_repo_path", lambda *_a, **_k: True)
    monkeypatch.setattr(agent_hooks.os, "name", "nt")
    captured: dict = {}
    monkeypatch.setattr(
        agent_hooks.safe_subprocess,
        "spawn_background",
        lambda cmd, **k: captured.update(k),
    )
    agent_hooks.run_ide_hook(["--from-ide-hook"], stdin_text='{"path": "a.py"}')
    assert captured.get("creationflags") == agent_hooks._WIN_DETACHED_FLAGS
    assert "start_new_session" not in captured


def test_run_ide_hook_posix_uses_new_session(monkeypatch):
    monkeypatch.setattr(agent_hooks, "_is_repo_path", lambda *_a, **_k: True)
    monkeypatch.setattr(agent_hooks.os, "name", "posix")
    captured: dict = {}
    monkeypatch.setattr(
        agent_hooks.safe_subprocess,
        "spawn_background",
        lambda cmd, **k: captured.update(k),
    )
    agent_hooks.run_ide_hook(["--from-ide-hook"], stdin_text='{"path": "a.py"}')
    assert captured.get("start_new_session") is True
    assert "creationflags" not in captured


def test_run_ide_hook_subprocess_error_fails_open(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr(agent_hooks.safe_subprocess, "spawn_background", boom)
    assert (
        agent_hooks.run_ide_hook(["--from-ide-hook"], stdin_text='{"path": "a.py"}')
        == 0
    )


# --------------------------------------------------------------------------- #
# Repo-path filtering and windowless spawn
# --------------------------------------------------------------------------- #


def test_workspace_roots_includes_cwd_and_event_roots():
    roots = agent_hooks._workspace_roots({"workspace_roots": ["/repo/a", "/repo/b"]})
    assert "/repo/a" in roots and "/repo/b" in roots
    assert os.getcwd() in roots


def test_workspace_roots_ignores_non_string_entries():
    roots = agent_hooks._workspace_roots({"workspace_roots": [1, "", "/repo"]})
    assert roots == ["/repo", os.getcwd()] or roots == ["/repo"]


def test_is_repo_path_accepts_in_repo_relative(tmp_path):
    assert agent_hooks._is_repo_path("collab/app.py", [str(tmp_path)]) is True


def test_is_repo_path_rejects_dot_git(tmp_path):
    assert agent_hooks._is_repo_path(".git/COMMIT_EDITMSG", [str(tmp_path)]) is False
    nested = str(tmp_path / ".git" / "COMMIT_MSG.txt")
    assert agent_hooks._is_repo_path(nested, [str(tmp_path)]) is False


def test_is_repo_path_rejects_outside_repo(tmp_path):
    outside = str(tmp_path.parent / "elsewhere" / "image.png")
    assert agent_hooks._is_repo_path(outside, [str(tmp_path)]) is False


def test_run_ide_hook_filters_non_repo_paths(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    captured: dict = {}
    monkeypatch.setattr(
        agent_hooks.safe_subprocess,
        "spawn_background",
        lambda cmd, **k: captured.update(cmd=cmd),
    )
    outside = str(tmp_path.parent / "chat" / "image.png").replace("\\", "/")
    payload = json.dumps({"file_path": [".git/COMMIT_MSG.txt", outside, "src/app.py"]})
    agent_hooks.run_ide_hook(["--from-ide-hook"], stdin_text=payload)
    assert "src/app.py" in captured["cmd"]
    assert ".git/COMMIT_MSG.txt" not in captured["cmd"]
    assert outside not in captured["cmd"]


def test_run_ide_hook_all_paths_filtered_is_noop(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    called = {"run": False}
    monkeypatch.setattr(
        agent_hooks.safe_subprocess,
        "spawn_background",
        lambda *a, **k: called.__setitem__("run", True),
    )
    rc = agent_hooks.run_ide_hook(
        ["--from-ide-hook"], stdin_text='{"file_path": ".git/COMMIT_MSG.txt"}'
    )
    assert rc == 0
    assert called["run"] is False


def test_windowless_python_posix_uses_sys_executable(monkeypatch):
    monkeypatch.setattr(agent_hooks.os, "name", "posix")
    assert agent_hooks._windowless_python() == agent_hooks.sys.executable


def test_windowless_python_windows_prefers_pythonw(monkeypatch):
    monkeypatch.setattr(agent_hooks.os, "name", "nt")
    monkeypatch.setattr(agent_hooks.os.path, "exists", lambda _p: True)
    result = agent_hooks._windowless_python()
    assert result.endswith("pythonw.exe")


def test_run_ide_hook_uses_windowless_python(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(agent_hooks, "_windowless_python", lambda: "/usr/bin/pythonw")
    captured: dict = {}
    monkeypatch.setattr(
        agent_hooks.safe_subprocess,
        "spawn_background",
        lambda cmd, **k: captured.update(cmd=cmd),
    )
    agent_hooks.run_ide_hook(["--from-ide-hook"], stdin_text='{"path": "a.py"}')
    assert captured["cmd"][0] == "/usr/bin/pythonw"


# --------------------------------------------------------------------------- #
# Installer helpers
# --------------------------------------------------------------------------- #


def test_venv_python_prefers_project_venv(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_hooks.os, "name", "nt")
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    py = scripts / "python.exe"
    py.write_text("", encoding="utf-8")
    assert agent_hooks._venv_python(tmp_path) == str(py)


def test_venv_python_posix(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_hooks.os, "name", "posix")
    binp = tmp_path / ".venv" / "bin"
    binp.mkdir(parents=True)
    py = binp / "python"
    py.write_text("", encoding="utf-8")
    assert agent_hooks._venv_python(tmp_path) == str(py)


def test_venv_python_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_hooks.sys, "executable", "/usr/bin/python3")
    assert agent_hooks._venv_python(tmp_path) == "/usr/bin/python3"


def test_hook_command_contains_runner(tmp_path):
    cmd = agent_hooks._hook_command(tmp_path)
    assert "collab.agent_hooks run-hook --from-ide-hook" in cmd


# --------------------------------------------------------------------------- #
# Installer: Cursor
# --------------------------------------------------------------------------- #


def test_install_cursor_fresh(tmp_path):
    status = agent_hooks._install_cursor(tmp_path, "CMD", force=False)
    assert status == "installed"
    data = json.loads((tmp_path / ".cursor" / "hooks.json").read_text())
    assert data["hooks"]["afterFileEdit"][0]["command"] == "CMD"
    assert data["version"] == 1


def test_install_cursor_idempotent(tmp_path):
    cmd = "py -m collab.agent_hooks run-hook --from-ide-hook"
    agent_hooks._install_cursor(tmp_path, cmd, force=False)
    assert agent_hooks._install_cursor(tmp_path, cmd, force=False) == "current"


def test_install_cursor_updates_changed_command(tmp_path):
    cfg = tmp_path / ".cursor" / "hooks.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "afterFileEdit": [{"command": "old -m collab.agent_hooks run-hook"}]
                },
            }
        ),
        encoding="utf-8",
    )
    assert agent_hooks._install_cursor(tmp_path, "NEWCMD", force=False) == "updated"
    data = json.loads(cfg.read_text())
    assert data["hooks"]["afterFileEdit"][0]["command"] == "NEWCMD"


def test_install_cursor_preserves_other_entries(tmp_path):
    cfg = tmp_path / ".cursor" / "hooks.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "afterFileEdit": [{"command": "user-formatter.sh"}],
                    "beforeShellExecution": [{"command": "guard.sh"}],
                },
            }
        ),
        encoding="utf-8",
    )
    assert agent_hooks._install_cursor(tmp_path, "CMD", force=False) == "installed"
    data = json.loads(cfg.read_text())
    commands = [e["command"] for e in data["hooks"]["afterFileEdit"]]
    assert "user-formatter.sh" in commands
    assert "CMD" in commands
    assert data["hooks"]["beforeShellExecution"][0]["command"] == "guard.sh"


def test_install_cursor_skips_unparsable(tmp_path):
    cfg = tmp_path / ".cursor" / "hooks.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{ broken json", encoding="utf-8")
    assert agent_hooks._install_cursor(tmp_path, "CMD", force=False) == "skipped"
    # File is left untouched.
    assert cfg.read_text() == "{ broken json"


def test_install_cursor_force_overwrites_unparsable(tmp_path):
    cfg = tmp_path / ".cursor" / "hooks.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{ broken json", encoding="utf-8")
    assert agent_hooks._install_cursor(tmp_path, "CMD", force=True) == "installed"
    data = json.loads(cfg.read_text())
    assert data["hooks"]["afterFileEdit"][0]["command"] == "CMD"


def test_install_cursor_repairs_malformed_types(tmp_path):
    cfg = tmp_path / ".cursor" / "hooks.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps({"hooks": {"afterFileEdit": "not-a-list"}}), encoding="utf-8"
    )
    assert agent_hooks._install_cursor(tmp_path, "CMD", force=False) == "installed"
    data = json.loads(cfg.read_text())
    assert data["hooks"]["afterFileEdit"][0]["command"] == "CMD"


# --------------------------------------------------------------------------- #
# Installer: Claude Code
# --------------------------------------------------------------------------- #


def test_install_claude_fresh(tmp_path):
    assert agent_hooks._install_claude(tmp_path, "CMD", force=False) == "installed"
    data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    group = data["hooks"]["PostToolUse"][0]
    assert group["matcher"] == "Edit|Write|MultiEdit"
    assert group["hooks"][0]["command"] == "CMD"
    assert group["hooks"][0]["type"] == "command"


def test_install_claude_idempotent(tmp_path):
    cmd = "py -m collab.agent_hooks run-hook --from-ide-hook"
    agent_hooks._install_claude(tmp_path, cmd, force=False)
    assert agent_hooks._install_claude(tmp_path, cmd, force=False) == "current"


def test_install_claude_updates_changed_command(tmp_path):
    cfg = tmp_path / ".claude" / "settings.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Edit",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "old -m collab.agent_hooks run-hook",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    assert agent_hooks._install_claude(tmp_path, "NEWCMD", force=False) == "updated"
    data = json.loads(cfg.read_text())
    assert data["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "NEWCMD"


def test_install_claude_preserves_existing(tmp_path):
    cfg = tmp_path / ".claude" / "settings.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps(
            {
                "model": "claude-x",
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "audit.sh"}],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    assert agent_hooks._install_claude(tmp_path, "CMD", force=False) == "installed"
    data = json.loads(cfg.read_text())
    assert data["model"] == "claude-x"
    assert len(data["hooks"]["PostToolUse"]) == 2


def test_install_claude_skips_unparsable(tmp_path):
    cfg = tmp_path / ".claude" / "settings.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("nope{", encoding="utf-8")
    assert agent_hooks._install_claude(tmp_path, "CMD", force=False) == "skipped"


def test_install_claude_repairs_malformed_types(tmp_path):
    cfg = tmp_path / ".claude" / "settings.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps({"hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": "x"}]}}),
        encoding="utf-8",
    )
    assert agent_hooks._install_claude(tmp_path, "CMD", force=False) == "installed"


# --------------------------------------------------------------------------- #
# Installer: Junie
# --------------------------------------------------------------------------- #


def test_install_junie_fresh(tmp_path):
    assert agent_hooks._install_junie(tmp_path) == "installed"
    text = (tmp_path / ".junie" / "guidelines.md").read_text()
    assert agent_hooks._JUNIE_BEGIN in text
    assert agent_hooks._JUNIE_END in text
    assert "collab claim" in text


def test_install_junie_idempotent(tmp_path):
    agent_hooks._install_junie(tmp_path)
    assert agent_hooks._install_junie(tmp_path) == "current"


def test_install_junie_updates_block(tmp_path):
    cfg = tmp_path / ".junie" / "guidelines.md"
    cfg.parent.mkdir(parents=True)
    begin = agent_hooks._JUNIE_BEGIN
    end = agent_hooks._JUNIE_END
    cfg.write_text(
        f"# Guidelines\n\n{begin}\nstale\n{end}\n",
        encoding="utf-8",
    )
    assert agent_hooks._install_junie(tmp_path) == "updated"
    text = cfg.read_text()
    assert "stale" not in text
    assert "# Guidelines" in text
    assert "collab claim" in text


def test_install_junie_appends_to_existing(tmp_path):
    cfg = tmp_path / ".junie" / "guidelines.md"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("# My project rules\n", encoding="utf-8")
    assert agent_hooks._install_junie(tmp_path) == "installed"
    text = cfg.read_text()
    assert "# My project rules" in text
    assert agent_hooks._JUNIE_BEGIN in text


# --------------------------------------------------------------------------- #
# install_agent_hooks orchestration + CLI dispatch
# --------------------------------------------------------------------------- #


def test_install_agent_hooks_all(tmp_path):
    summary = agent_hooks.install_agent_hooks(project_root=tmp_path)
    assert summary["results"] == {
        "cursor": "installed",
        "claude": "installed",
        "junie": "installed",
    }
    assert (tmp_path / ".cursor" / "hooks.json").exists()
    assert (tmp_path / ".claude" / "settings.json").exists()
    assert (tmp_path / ".junie" / "guidelines.md").exists()
    assert "collab.agent_hooks" in summary["command"]


def test_install_agent_hooks_handles_target_exception(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(agent_hooks, "_install_cursor", boom)
    summary = agent_hooks.install_agent_hooks(project_root=tmp_path)
    assert summary["results"]["cursor"] == "skipped"
    assert summary["results"]["claude"] == "installed"


def test_install_agent_hooks_default_root(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_hooks, "_git_toplevel", lambda: tmp_path)
    summary = agent_hooks.install_agent_hooks()
    assert summary["root"] == str(tmp_path)


def test_git_toplevel_returns_path():
    assert agent_hooks._git_toplevel().exists()


def test_print_summary(capsys, tmp_path):
    summary = agent_hooks.install_agent_hooks(project_root=tmp_path)
    agent_hooks._print_summary(summary)
    out = capsys.readouterr().out
    assert ".cursor/hooks.json" in out
    assert ".claude/settings.json" in out
    assert ".junie/guidelines.md" in out


def test_main_install(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_hooks, "_git_toplevel", lambda: tmp_path)
    assert agent_hooks._main(["install"]) == 0
    assert (tmp_path / ".cursor" / "hooks.json").exists()


def test_main_run_hook(monkeypatch):
    called = {}

    def fake_run(args):
        called["args"] = args
        return 0

    monkeypatch.setattr(agent_hooks, "run_ide_hook", fake_run)
    assert agent_hooks._main(["run-hook", "--from-ide-hook"]) == 0
    assert called["args"] == ["--from-ide-hook"]


def test_main_usage():
    assert agent_hooks._main([]) == 2


@pytest.mark.parametrize("truthy", ["1", "true", "YES", "on"])
def test_truthy(monkeypatch, truthy):
    monkeypatch.setenv("X_FLAG", truthy)
    assert agent_hooks._truthy("X_FLAG") is True
    monkeypatch.setenv("X_FLAG", "no")
    assert agent_hooks._truthy("X_FLAG") is False
