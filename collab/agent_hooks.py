"""Automatic AI-agent attribution: IDE edit-hook runner + installer.

This module is the single source of truth for *strict user-vs-agent attribution*
automation. It does two things:

1. **Runner** (:func:`run_ide_hook`): a runtime-agnostic ``afterFileEdit`` /
   ``PostToolUse`` hook handler. Any IDE/agent that can run a command after an
   edit and pass event JSON on stdin (Cursor, Claude Code, ...) funnels through
   here. It extracts the edited path(s) and runs ``collab claim`` so the lock is
   attributed to the AI agent (``origin=agent``) — never the human.

2. **Installer** (:func:`install_agent_hooks`): idempotently wires the runner
   into every supported IDE/agent that exposes a per-edit hook, plus generates a
   guidelines file for agents that do not. This is invoked automatically by the
   dev setup scripts and via ``collab install-agent-hooks`` so a developer never
   has to configure attribution by hand.

Why a ``--from-ide-hook`` flag instead of the ``COLLAB_AGENT_HOOKS`` env gate?
Cursor's ``afterFileEdit`` and Claude Code's ``PostToolUse`` hooks fire **only**
for genuine agent edits (not human typing). When the runner is invoked from such
a hook (signalled by ``--from-ide-hook``) it self-enables, so attribution works
with zero manual environment setup. The env gate remains supported for ad-hoc
pipelines.

Design invariants:
    * **Fail open**: editing must never be blocked by lock bookkeeping. Every
      error path returns success and mutates nothing.
    * **Idempotent installs**: re-running never duplicates entries and preserves
      any unrelated user configuration.
    * **No PATH assumptions**: installed commands use the project's ``.venv``
      interpreter by absolute path.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional

from . import safe_subprocess

# DETACHED_PROCESS | CREATE_NO_WINDOW — fully detach the claim on Windows so it
# survives the short-lived IDE hook process and never opens a console window.
_WIN_DETACHED_FLAGS = 0x00000008 | 0x08000000

# --------------------------------------------------------------------------- #
# Event parsing (shared by every IDE/agent payload shape)
# --------------------------------------------------------------------------- #

# Keys that commonly carry an edited file path across IDE hook payloads.
_PATH_KEYS = (
    "file_path",
    "filePath",
    "path",
    "absolute_path",
    "absolutePath",
    "target",
    "uri",
)
# Keys that commonly carry a session/conversation identifier.
_SESSION_KEYS = (
    "conversation_id",
    "conversationId",
    "session_id",
    "sessionId",
    "thread_id",
    "threadId",
    "chat_id",
    "chatId",
    "generation_id",
    "generationId",
)
# Keys that may carry a human-friendly task description.
_LABEL_KEYS = ("title", "summary", "task", "label", "name")

_FROM_IDE_HOOK_FLAG = "--from-ide-hook"

# Marker used to detect our own command in third-party config files so installs
# stay idempotent and never clobber unrelated entries.
_COMMAND_MARKER = "collab.agent_hooks"

_JUNIE_BEGIN = (
    "<!-- BEGIN collab-agent-attribution (managed by `collab install-agent-hooks`) -->"
)
_JUNIE_END = "<!-- END collab-agent-attribution -->"


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _diag_log(message: str) -> None:
    """Append a diagnostic line to ``logs/agent_hooks.log`` (best-effort).

    This is the source of truth for answering "did the IDE actually invoke the hook?".
    It must never raise — observability cannot break editing. Disabled by default; opt
    in for troubleshooting by setting ``COLLAB_AGENT_HOOKS_DEBUG=1``.
    """
    if not _truthy("COLLAB_AGENT_HOOKS_DEBUG"):
        return
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] pid={os.getpid()} {message}\n"
    # Prefer the workspace ``logs/`` dir (works in any repo); fall back to a
    # single temp file so installed-package use never tries to write into
    # site-packages.
    candidates = (Path(os.getcwd()) / "logs", Path(tempfile.gettempdir()))
    for log_dir in candidates:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / "agent_hooks.log").open("a", encoding="utf-8") as handle:
                handle.write(line)
            return
        except Exception:
            continue


def _walk(obj: Any) -> Iterable[Any]:
    """Yield every nested dict/list node (depth-first)."""
    yield obj
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _first_str(event: dict[str, Any], keys: Iterable[str]) -> Optional[str]:
    for node in _walk(event):
        if isinstance(node, dict):
            for key in keys:
                val = node.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    return None


def _looks_like_path(value: str) -> bool:
    """Return True when *value* plausibly names a file path or basename.

    Cursor often sends relative names like ``README.md`` or extension-less basenames
    like ``test`` / ``Makefile`` with no directory separator. The old rule required a
    slash or a dotted extension, so those edits were silently skipped and only the human
    auto-watcher lock remained.
    """
    val = value.strip()
    if not val or "\n" in val or " " in val:
        return False
    if "/" in val or "\\" in val:
        return True
    if re.search(r"\.\w{1,8}$", val):
        return True
    # Extension-less single-segment names (test, Makefile, LICENSE, …).
    return bool(re.match(r"^[\w][\w.\-]{0,255}$", val))


def _normalize_path(value: str) -> Optional[str]:
    val = value.strip()
    if val.startswith("file://"):
        val = val[len("file://") :]
        # Strip a leading slash on Windows-style /C:/... paths.
        if re.match(r"^/[A-Za-z]:", val):
            val = val[1:]
    if not _looks_like_path(val):
        return None
    return val


def _extract_paths(event: dict[str, Any]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for node in _walk(event):
        if not isinstance(node, dict):
            continue
        for key in _PATH_KEYS:
            val = node.get(key)
            if isinstance(val, str):
                norm = _normalize_path(val)
                if norm and norm not in seen:
                    seen.add(norm)
                    found.append(norm)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        norm = _normalize_path(item)
                        if norm and norm not in seen:
                            seen.add(norm)
                            found.append(norm)
    return found


def _workspace_roots(event: dict[str, Any]) -> list[str]:
    """Return candidate repository roots: the IDE ``workspace_roots`` plus cwd."""
    roots: list[str] = []
    raw = event.get("workspace_roots")
    if isinstance(raw, list):
        roots = [r for r in raw if isinstance(r, str) and r.strip()]
    cwd = os.getcwd()
    if cwd not in roots:
        roots.append(cwd)
    return roots


def _is_repo_path(path: str, roots: list[str]) -> bool:
    """True only for files inside a workspace root and outside any ``.git`` dir.

    Prevents claiming things that are not project working files: IDE chat
    attachments stored outside the repo, and VCS internals like
    ``.git/COMMIT_EDITMSG``.
    """
    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = Path(roots[0]) / candidate if roots else candidate
        resolved = candidate.resolve()
    except Exception:
        return False
    if ".git" in resolved.parts:
        return False
    for root in roots:
        try:
            resolved.relative_to(Path(root).resolve())
            return True
        except Exception:
            continue
    return False


def _windowless_python() -> str:
    """Return ``pythonw.exe`` on Windows so the detached claim shows no console.

    Falls back to the current interpreter elsewhere (or if ``pythonw`` is missing). This
    is what eliminates the flashing terminal windows.
    """
    if os.name == "nt":
        candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if os.path.exists(candidate):
            return candidate
    return sys.executable


def _read_stdin_text() -> str:
    """Read the hook payload from stdin, tolerant of Windows BOM/encoding.

    Cursor on Windows writes the event JSON to stdin prefixed with a UTF-8 BOM (and the
    console code page may not be UTF-8). Reading the raw bytes and decoding with
    ``utf-8-sig`` strips the BOM and forces UTF-8 regardless of the locale, which is
    what broke attribution: a BOM makes ``json.loads`` raise, so the event parsed as
    empty and nothing was ever claimed.
    """
    try:
        buffer = getattr(sys.stdin, "buffer", None)
        if buffer is not None:
            data = buffer.read()
            if isinstance(data, (bytes, bytearray)):
                return bytes(data).decode("utf-8-sig", errors="replace")
        text = sys.stdin.read()
        return text if isinstance(text, str) else ""
    except Exception:
        return ""


def _read_event(stdin_text: Optional[str] = None) -> dict[str, Any]:
    if stdin_text is None:
        stdin_text = _read_stdin_text()
    if not stdin_text:
        return {}
    # Strip any leading BOM(s) and surrounding whitespace before parsing.
    text = stdin_text.lstrip("\ufeff").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        # Last-resort tolerance for a leading BOM/garbage that survived decoding:
        # retry from the first JSON object brace.
        brace = text.find("{")
        if brace <= 0:
            return {}
        try:
            data = json.loads(text[brace:])
        except Exception:
            return {}
    return data if isinstance(data, dict) else {}


def _stable_agent_id(event: dict[str, Any]) -> str:
    explicit = os.getenv("COLLAB_AGENT_ID", "").strip()
    if explicit:
        return explicit
    session = _first_str(event, _SESSION_KEYS)
    if session:
        digest = hashlib.sha1(
            session.encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:8]
        return f"agent-{digest}"
    # Last resort: a per-working-directory stable id so a single session reuses
    # one identity instead of generating a new one on every edit.
    seed = os.path.abspath(os.getcwd()).lower()
    digest = hashlib.sha1(seed.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"agent-{digest}"


def _detect_kind_from_event(event: Optional[dict[str, Any]]) -> Optional[str]:
    """Infer the AI runtime family from the hook payload itself.

    Cursor stamps every hook event with ``cursor_version`` (present in both the
    ``afterFileEdit`` and tool-use events), which is a reliable, env-independent marker.
    Real Claude Code hooks instead carry ``PreToolUse``/``PostToolUse`` event names.
    This keeps the dashboard badge informative (icon + "Cursor") even when the IDE does
    not export runtime env markers to the hook process.
    """
    if not isinstance(event, dict):
        return None
    if "cursor_version" in event:
        return "cursor"
    hook_name = str(event.get("hook_event_name") or "")
    if hook_name in {"PreToolUse", "PostToolUse"}:
        return "claude-code"
    return None


def _detect_kind(event: Optional[dict[str, Any]] = None) -> str:
    explicit = os.getenv("COLLAB_AGENT_KIND", "").strip().lower()
    if explicit:
        return explicit
    from_event = _detect_kind_from_event(event)
    if from_event:
        return from_event
    try:
        from . import agent_identity

        detected = agent_identity.detect_agent_runtime_label()
    except Exception:
        detected = None
    return (detected or "other").strip().lower() or "other"


def _resolve_label(event: dict[str, Any]) -> Optional[str]:
    explicit = os.getenv("COLLAB_AGENT_LABEL", "").strip()
    if explicit:
        return explicit
    return _first_str(event, _LABEL_KEYS)


def _hook_enabled(argv: list[str]) -> bool:
    """Return True when the runner should claim edits.

    Self-enables when invoked from a genuine IDE edit hook (``--from-ide-hook``) because
    such hooks fire only for agent edits. Otherwise requires the explicit
    ``COLLAB_AGENT_HOOKS`` opt-in (for ad-hoc pipelines / manual wiring).
    """
    if _FROM_IDE_HOOK_FLAG in argv:
        return True
    return _truthy("COLLAB_AGENT_HOOKS")


def run_ide_hook(
    argv: Optional[list[str]] = None,
    *,
    stdin_text: Optional[str] = None,
) -> int:
    """Claim edited file(s) as an AI agent.

    Always returns 0 (fail open).
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    _diag_log(f"invoked argv={argv} cwd={os.getcwd()!r}")
    if not _hook_enabled(argv):
        _diag_log("disabled: no --from-ide-hook flag and COLLAB_AGENT_HOOKS unset")
        return 0

    raw = stdin_text if stdin_text is not None else _read_stdin_text()
    _diag_log(f"raw stdin len={len(raw or '')} sample={(raw or '')[:300]!r}")

    event = _read_event(raw)
    if event:
        _diag_log(f"event keys={sorted(event)[:12]}")
    else:
        _diag_log("empty/invalid event payload on stdin")
    all_paths = _extract_paths(event)
    roots = _workspace_roots(event)
    paths = [p for p in all_paths if _is_repo_path(p, roots)]
    skipped = [p for p in all_paths if p not in paths]
    if skipped:
        _diag_log(f"skipped non-repo/.git paths: {skipped}")
    if not paths:
        _diag_log("no claimable repo file paths in event -> nothing to claim")
        return 0

    env = dict(os.environ)
    env["COLLAB_AGENT_MODE"] = "1"
    env["COLLAB_AGENT_ID"] = _stable_agent_id(event)
    env["COLLAB_AGENT_KIND"] = _detect_kind(event)
    label = _resolve_label(event)
    if label:
        env["COLLAB_AGENT_LABEL"] = label

    cmd = [_windowless_python(), "-m", "collab", "claim", *paths]
    if label:
        cmd += ["--label", label]
    cmd += ["--reason", "AI agent edit"]

    # Fire-and-forget: spawn the claim fully detached and return immediately.
    # The network claim must NOT run inside the hook process, or Cursor's
    # afterFileEdit execution timeout could kill it mid-flight (leaving only the
    # human auto-watcher lock). Detaching also guarantees edits are never delayed.
    try:
        kwargs: dict[str, Any] = {
            "policy": "agent_claim",
            "cwd": os.getcwd(),
            "env": env,
        }
        if os.name == "nt":
            kwargs["creationflags"] = _WIN_DETACHED_FLAGS
        else:
            kwargs["start_new_session"] = True
        safe_subprocess.spawn_background(cmd, **kwargs)
        _diag_log(
            f"spawned detached claim for {len(paths)} path(s): {paths} label={label!r}"
        )
    except Exception as exc:  # pragma: no cover - defensive, must fail open
        _diag_log(f"spawn failed (fail-open): {exc!r}")
        return 0
    return 0


# --------------------------------------------------------------------------- #
# Installer
# --------------------------------------------------------------------------- #


def _git_toplevel() -> Path:
    try:
        from .githooks import _git_toplevel as _gt

        return _gt()
    except Exception:
        return Path.cwd()


def _venv_python(root: Path) -> str:
    """Return the absolute project ``.venv`` interpreter, or a best-effort path.

    Installed hook commands must not rely on ``PATH`` or shell activation, which are
    routinely absent in IDE/agent shells.
    """
    is_win = os.name == "nt"
    candidate = (
        root / ".venv" / "Scripts" / "python.exe"
        if is_win
        else root / ".venv" / "bin" / "python"
    )
    if candidate.is_file():
        return str(candidate)
    # Fall back to the current interpreter (already correct when setup runs via
    # the venv python), else a bare name as a last resort.
    return sys.executable or ("python.exe" if is_win else "python3")


def _hook_command(root: Path) -> str:
    """Return the shell command string IDEs invoke after an agent edit."""
    py = _venv_python(root)
    # Double quotes around the interpreter path work in both cmd.exe and POSIX
    # sh, covering paths that contain spaces.
    return f'"{py}" -m {_COMMAND_MARKER} run-hook {_FROM_IDE_HOOK_FLAG}'


def _load_json(path: Path) -> tuple[Optional[Any], bool]:
    """Return ``(data, existed)``.

    ``data`` is None when the file is unparsable.
    """
    if not path.exists():
        return None, False
    try:
        return json.loads(path.read_text(encoding="utf-8")), True
    except Exception:
        return None, True


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def _command_is_ours(command: Any) -> bool:
    return isinstance(command, str) and _COMMAND_MARKER in command


def _install_cursor(root: Path, command: str, force: bool) -> str:
    """Merge our ``afterFileEdit`` hook into ``.cursor/hooks.json``."""
    cfg = root / ".cursor" / "hooks.json"
    data, existed = _load_json(cfg)
    if data is None and existed and not force:
        return "skipped"  # existing file is unparsable; never clobber silently
    if not isinstance(data, dict):
        data = {}

    data.setdefault("version", 1)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    entries = hooks.get("afterFileEdit")
    if not isinstance(entries, list):
        entries = []
        hooks["afterFileEdit"] = entries

    for entry in entries:
        if isinstance(entry, dict) and _command_is_ours(entry.get("command")):
            if entry.get("command") == command:
                return "current"
            entry["command"] = command
            _write_json(cfg, data)
            return "updated"

    entries.append({"command": command})
    _write_json(cfg, data)
    return "installed"


def _install_claude(root: Path, command: str, force: bool) -> str:
    """Merge our ``PostToolUse`` hook into ``.claude/settings.json``."""
    cfg = root / ".claude" / "settings.json"
    matcher = "Edit|Write|MultiEdit"
    data, existed = _load_json(cfg)
    if data is None and existed and not force:
        return "skipped"
    if not isinstance(data, dict):
        data = {}

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    groups = hooks.get("PostToolUse")
    if not isinstance(groups, list):
        groups = []
        hooks["PostToolUse"] = groups

    # Find any existing group that already carries our command.
    for group in groups:
        if not isinstance(group, dict):
            continue
        inner = group.get("hooks")
        if not isinstance(inner, list):
            continue
        for hook in inner:
            if isinstance(hook, dict) and _command_is_ours(hook.get("command")):
                if hook.get("command") == command:
                    return "current"
                hook["command"] = command
                _write_json(cfg, data)
                return "updated"

    groups.append(
        {
            "matcher": matcher,
            "hooks": [{"type": "command", "command": command}],
        }
    )
    _write_json(cfg, data)
    return "installed"


def _junie_block() -> str:
    return (
        f"{_JUNIE_BEGIN}\n\n"
        "## Collaborative lock attribution (AI agent)\n\n"
        "This project uses the `collab` runtime for collaborative file locking. "
        "JetBrains IDEs do not expose a per-edit hook, so to keep the locks "
        "dashboard accurate (showing **AI Agent** vs **User**), after you create "
        "or modify any file(s) you MUST immediately claim them as an AI agent "
        "using the project virtual environment (`.venv`):\n\n"
        "```\n"
        'collab claim <path> [<path> ...] --label "<short task>" '
        '--reason "AI agent edit"\n'
        "```\n\n"
        "- Claim every file you edit. Do not rely on the background watcher — it "
        "attributes edits to the human developer by design.\n"
        "- Claiming marks the lock `origin=agent` so the work is shown as "
        "AI-agent work, with your task as the label.\n"
        f"{_JUNIE_END}\n"
    )


def _install_junie(root: Path) -> str:
    """Write/refresh a managed attribution block in ``.junie/guidelines.md``."""
    cfg = root / ".junie" / "guidelines.md"
    block = _junie_block()
    if cfg.exists():
        try:
            current = cfg.read_text(encoding="utf-8")
        except Exception:
            current = ""
        if _JUNIE_BEGIN in current and _JUNIE_END in current:
            pattern = re.compile(
                re.escape(_JUNIE_BEGIN) + r".*?" + re.escape(_JUNIE_END),
                re.DOTALL,
            )
            replaced = pattern.sub(block.rstrip("\n"), current)
            if replaced == current:
                return "current"
            cfg.write_text(replaced, encoding="utf-8", newline="\n")
            return "updated"
        # Append the managed block, preserving existing guidelines.
        sep = "" if current.endswith("\n") or not current else "\n"
        new_text = current + sep + "\n" + block
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(new_text, encoding="utf-8", newline="\n")
        return "installed"

    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(block, encoding="utf-8", newline="\n")
    return "installed"


def install_agent_hooks(
    project_root: Optional[Path] = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Install AI-agent attribution hooks for every supported IDE/agent.

    Writes/merges, idempotently and non-destructively:
        * ``.cursor/hooks.json``      — Cursor ``afterFileEdit``
        * ``.claude/settings.json``   — Claude Code ``PostToolUse``
        * ``.junie/guidelines.md``    — JetBrains/Junie instructions (no native hook)

    Returns a summary mapping ``target -> status`` where status is one of
    ``installed``, ``updated``, ``current`` or ``skipped``.
    """
    root = Path(project_root) if project_root is not None else _git_toplevel()
    command = _hook_command(root)

    results: dict[str, str] = {}
    try:
        results["cursor"] = _install_cursor(root, command, force)
    except Exception:
        results["cursor"] = "skipped"
    try:
        results["claude"] = _install_claude(root, command, force)
    except Exception:
        results["claude"] = "skipped"
    try:
        results["junie"] = _install_junie(root)
    except Exception:
        results["junie"] = "skipped"

    return {"root": str(root), "command": command, "results": results}


def _print_summary(summary: dict[str, Any]) -> None:
    labels = {
        "cursor": ".cursor/hooks.json (Cursor afterFileEdit)",
        "claude": ".claude/settings.json (Claude Code PostToolUse)",
        "junie": ".junie/guidelines.md (JetBrains/Junie)",
    }
    print("✓ Agent attribution hooks configured (no manual steps required).")
    print(f"  Project root: {summary.get('root')}")
    for key, label in labels.items():
        status = summary.get("results", {}).get(key, "skipped")
        print(f"  - {label}: {status}")
    print(
        "  Note: plain VS Code + Copilot has no per-edit hook; agents there "
        "should run `collab claim` (see AGENTS.md / file-locking skill)."
    )


def _main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "run-hook":
        return run_ide_hook(args[1:])
    if args and args[0] == "install":
        force = "--force" in args[1:]
        _print_summary(install_agent_hooks(force=force))
        return 0
    print(
        "Usage: python -m collab.agent_hooks <run-hook|install> [--force]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
