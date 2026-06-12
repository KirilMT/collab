"""CLI orchestration layer for the collab package.

Separates command-line interface concerns from the core LockClient library, enabling
plugin/extension architecture for library consumers.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback as _tb
from argparse import ArgumentParser
from contextlib import nullcontext

from . import __version__

logger = logging.getLogger("collab.lock_client")


def _is_truthy_env(name: str, default: bool) -> bool:
    """Return normalized boolean value for an env var."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _should_auto_start_watcher() -> bool:
    """Decide whether CLI should auto-bootstrap the watcher daemon."""
    # Keep pytest deterministic: command tests should not spawn background
    # processes unless a test explicitly opts into that behavior.
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return _is_truthy_env("COLLAB_AUTO_START_WATCHER", default=True)


def _ensure_watcher_running(client: object, command: str) -> bool:
    """Best-effort watcher bootstrap for user-facing commands.

    Returns True when this call started the daemon.
    """
    if command not in {"active", "status"}:
        return False
    if not _should_auto_start_watcher():
        return False

    try:
        if bool(getattr(client, "daemon_status")()):
            return False
    except Exception as exc:
        logger.debug("Failed to determine daemon status before auto-start: %s", exc)

    try:
        logger.info("Auto-starting watcher daemon for command: %s", command)
        getattr(client, "daemon_start")()
        return True
    except Exception as exc:
        logger.warning("Watcher auto-start failed for '%s': %s", command, exc)
        return False


def _run_cli() -> None:
    """CLI entry point for the lock client."""
    # Force UTF-8 on Windows so Unicode symbols (✓, ❌, 🔒) render correctly.
    # Mirrors the proven pattern from validate_code.py, format_code.py, run.py.
    # errors="replace" ensures graceful fallback if the terminal truly cannot
    # handle a character (e.g. bare cmd.exe with cp437).
    for stream in (sys.stdout, sys.stderr):
        reconfig = getattr(stream, "reconfigure", None)
        if callable(reconfig):
            try:
                reconfig(encoding="utf-8", errors="replace")
            except Exception as exc:
                logger.debug("Failed to reconfigure stream encoding: %s", exc)

    # Lazy imports to avoid circular dependency with lock_client
    from .lock_client import _COLLAB_ROOT, LockClient, _quiet_console_loggers

    # Wire up file-based logging early so all CLI output is captured.
    from .logging_config import setup_collab_logging

    setup_collab_logging(collab_dir=_COLLAB_ROOT)

    common = ArgumentParser(add_help=False)
    common.add_argument(
        "--agent-id",
        dest="agent_id",
        default=None,
        help="Agent identity for this session (or set COLLAB_AGENT_ID)",
    )
    common.add_argument(
        "--agent-label",
        dest="agent_label",
        default=None,
        help="Human-readable agent task label (or set COLLAB_AGENT_LABEL)",
    )
    common.add_argument(
        "--agent-kind",
        dest="agent_kind",
        default=None,
        help=(
            "AI runtime family for display, e.g. cursor|claude-code|copilot "
            "(or set COLLAB_AGENT_KIND; auto-detected when possible)"
        ),
    )

    parser = ArgumentParser(
        description="Collaborative File Lock Manager (Supabase)",
        parents=[common],
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"collab-runtime {__version__}",
    )
    sub = parser.add_subparsers(dest="command")

    # acquire
    acq = sub.add_parser("acquire", help="Acquire a lock on a file")
    acq.add_argument("file_path")
    acq.add_argument("--reason", help="Reason for the lock")

    # claim — runtime-agnostic entrypoint for AI agents / IDE hooks. Acquires
    # one or more files as the current AI agent (origin=agent), auto-generating a
    # stable agent identity when none is supplied. Designed to be invoked from
    # any IDE's edit hook (Cursor, Claude Code, Copilot, Gemini, ...).
    clm = sub.add_parser(
        "claim",
        help="Claim file(s) as an AI agent edit (origin=agent)",
    )
    clm.add_argument("file_paths", nargs="+")
    clm.add_argument("--reason", help="Reason for the claim")
    clm.add_argument(
        "--label",
        dest="claim_label",
        help="Task label describing what the agent is working on",
    )

    # release
    rel = sub.add_parser("release", help="Release a lock on a file")
    rel.add_argument("file_path")

    # whoami
    sub.add_parser("whoami", help="Show resolved developer and agent identity")

    # active
    active_parser = sub.add_parser("active", help="List all active locks")
    active_parser.add_argument(
        "--mine",
        action="store_true",
        help="Show only locks held by this developer and agent",
    )

    # status
    st = sub.add_parser("status", help="Check lock status of a file")
    st.add_argument("file_path")

    # release-all
    ra = sub.add_parser(
        "release-all",
        help="Release all locks held by you (including your AI-agent locks)",
    )
    ra.add_argument(
        "--identity-only",
        action="store_true",
        help=(
            "Only release locks for the current identity (developer + agent), "
            "not every lock owned by your developer id"
        ),
    )

    # force-release
    fr = sub.add_parser(
        "force-release",
        help="Force release a lock (own locks only; admin can release any)",
    )
    fr.add_argument("file_path")
    # force-release-all
    sub.add_parser("force-release-all", help="Force release all locks (admin only)")

    # acquire-batch
    ab = sub.add_parser("acquire-batch", help="Acquire locks on multiple files")
    ab.add_argument("file_paths", nargs="+")
    ab.add_argument("--reason")

    # release-batch
    rb = sub.add_parser("release-batch", help="Release locks on multiple files")
    rb.add_argument("file_paths", nargs="+")

    # daemon-start
    ds = sub.add_parser("daemon-start", help="Start the watcher daemon")
    ds.add_argument("--interval", type=int, default=5, help="Poll interval (seconds)")
    ds.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="Idle timeout in minutes (0 = disabled)",
    )
    ds.add_argument("--open-dashboard", action="store_true")

    # daemon-stop
    sub.add_parser("daemon-stop", help="Stop the watcher daemon")

    # daemon-status
    sub.add_parser("daemon-status", help="Check watcher daemon status")

    # daemon-restart
    rst = sub.add_parser(
        "daemon-restart",
        help="Restart the watcher daemon (stop + start)",
    )
    rst.add_argument("--interval", type=int, default=5, help="Poll interval (seconds)")
    rst.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="Idle timeout in minutes (0 = disabled)",
    )
    rst.add_argument("--open-dashboard", action="store_true")

    # cleanup - kill orphaned processes
    sub.add_parser(
        "cleanup", help="Kill all orphaned lock_client processes (preserves locks)"
    )

    # dashboard
    sub.add_parser("dashboard", help="Open the collaborative dashboard")

    # init-hooks (offline: installs bundled git hooks into the current repo)
    ih = sub.add_parser(
        "init-hooks",
        help="Install collab git hooks into the current repository",
    )
    ih.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing non-collab hooks",
    )

    # install-agent-hooks (offline: wires IDE edit hooks for AI-agent
    # attribution into the current repo — Cursor/Claude Code/Junie).
    iah = sub.add_parser(
        "install-agent-hooks",
        help="Install IDE agent-attribution hooks (Cursor/Claude Code/Junie)",
    )
    iah.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing but unparsable hooks config",
    )

    # reconcile
    sub.add_parser("reconcile", help="Sync local git status with Supabase")

    # history
    hp = sub.add_parser("history", help="Show lock history")
    hp.add_argument("file_path", nargs="?")
    hp.add_argument("--limit", type=int, default=20)
    hp.add_argument(
        "--json", action="store_true", dest="json_output", help="Output as raw JSON"
    )

    # history-prune
    hpr = sub.add_parser("history-prune", help="Delete lock history older than N days")
    hpr.add_argument("--days", type=int, default=30, help="Retention window in days")

    # ping
    sub.add_parser("ping", help="Check Supabase connectivity")

    # info
    sub.add_parser("info", help="Show comprehensive status overview")

    # logs
    lg = sub.add_parser("logs", help="Show recent collab log entries")
    lg.add_argument(
        "--lines",
        type=int,
        default=50,
        help="Number of recent log lines to display (default: 50)",
    )
    lg.add_argument(
        "--follow",
        "-f",
        action="store_true",
        help="Follow the log file (tail -f mode, Ctrl+C to stop)",
    )

    # watch (internal, called by daemon-start)
    wp = sub.add_parser("watch", help="Run watcher in foreground")
    wp.add_argument("--interval", type=int, default=5)
    wp.add_argument("--timeout", type=int, default=0)
    wp.add_argument("--open-dashboard", action="store_true")
    wp.add_argument(
        "--daemon",
        action="store_true",
        help="Daemon mode: skip parent-PID liveness check",
    )
    wp.add_argument(
        "--parent-pid", type=int, help="Tie watcher lifecycle to this parent PID"
    )
    wp.add_argument(
        "--parent-name", type=str, help="Name of the parent process for better logging"
    )
    wp.add_argument(
        "--parent-method",
        type=str,
        help=(
            "Detection method used to infer parent PID (vscode_pid|"
            "process_tree|simple_walk|pycharm_hosted|node_parent|"
            "immediate_parent)"
        ),
    )
    wp.add_argument(
        "--heartbeat-file",
        type=str,
        help=(
            "Optional path to a heartbeat file. If missing or stale, "
            "watcher shuts down."
        ),
    )
    wp.add_argument(
        "--heartbeat-grace-seconds",
        type=int,
        default=10,
        help="Heartbeat staleness threshold in seconds before shutdown.",
    )
    wp.add_argument(
        "--pid-file",
        type=str,
        help="PID file path namespace for this watcher instance.",
    )

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    # init-hooks is a purely local filesystem operation: install the bundled
    # git hooks without constructing a LockClient or touching Supabase.
    if args.command == "init-hooks":
        from .githooks import install_hooks

        hooks_summary = install_hooks(force=getattr(args, "force", False))
        installed = hooks_summary.get("installed") or []
        skipped = hooks_summary.get("skipped") or []
        print(f"✓ Installed collab git hooks into {hooks_summary.get('hooks_dir')}")
        if installed:
            print(f"  Installed: {', '.join(installed)}")
        if skipped:
            print(
                "  Skipped (existing non-collab hooks; rerun with --force): "
                f"{', '.join(skipped)}"
            )
        sys.exit(0)

    # install-agent-hooks is a purely local filesystem operation: wire IDE edit
    # hooks for AI-agent attribution without constructing a LockClient.
    if args.command == "install-agent-hooks":
        from .agent_hooks import _print_summary, install_agent_hooks

        _print_summary(install_agent_hooks(force=getattr(args, "force", False)))
        sys.exit(0)

    local_only = args.command in ("daemon-status", "daemon-stop")

    # ``claim`` always runs as an AI agent. Enable agent mode so a stable agent
    # identity is generated/persisted even when the caller (IDE hook) did not set
    # COLLAB_AGENT_ID, and prefer the claim-specific task label.
    resolved_agent_label = getattr(args, "agent_label", None)
    agent_mode: bool | None = None
    if args.command == "claim":
        agent_mode = True
        resolved_agent_label = (
            getattr(args, "claim_label", None) or resolved_agent_label
        )

    client = LockClient(
        local_only=local_only,
        agent_id=getattr(args, "agent_id", None),
        agent_label=resolved_agent_label,
        agent_kind=getattr(args, "agent_kind", None),
        agent_mode=agent_mode,
    )

    # The background watcher must attribute bulk git-status auto-locks to the
    # HUMAN developer, never to an AI agent — even when launched from a terminal
    # that exported agent env vars. A dedicated agent watcher can opt in with
    # COLLAB_WATCHER_AGENT_ID.
    if args.command == "watch":
        watcher_agent = os.getenv("COLLAB_WATCHER_AGENT_ID", "").strip()
        if not watcher_agent:
            client.agent_id = None
            client.agent_label = None
            client.agent_kind = None
            client.origin = "human"
            client._session_token = None

    # Keep CLI output clean by silencing noisy dependency logs (httpx, urllib3,
    # postgrest, supabase) for user-facing commands that can hit network APIs.
    quiet_commands = {
        "acquire",
        "claim",
        "release",
        "active",
        "status",
        "release-all",
        "force-release",
        "force-release-all",
        "acquire-batch",
        "release-batch",
        "daemon-stop",
        "reconcile",
        "history",
        "history-prune",
        "whoami",
        "ping",
        "info",
        "logs",
    }
    quiet_ctx = (
        _quiet_console_loggers() if args.command in quiet_commands else nullcontext()
    )

    with quiet_ctx:
        if args.command == "whoami":
            from . import agent_identity

            summary = agent_identity.identity_summary(
                client.developer_id,
                client.agent_id,
                client.agent_label,
                getattr(client, "agent_kind", None),
            )
            print(f"Developer: {summary['developer_id']}")
            print(f"Mode:      {summary['mode']}")
            if summary["agent_id"]:
                print(f"Agent ID:    {summary['agent_id']}")
            if summary["agent_label"]:
                print(f"Agent label: {summary['agent_label']}")
            if summary["agent_kind"]:
                print(f"Agent kind:  {summary['agent_kind']}")
            sys.exit(0)

        elif args.command == "acquire":
            ok, msg = client.acquire(args.file_path, reason=args.reason)
            if ok:
                print(f"✓ Locked {args.file_path} (ID: {msg})")
            else:
                print(f"✗ Failed to lock {args.file_path}: {msg}")
            sys.exit(0 if ok else 1)

        elif args.command == "claim":
            reason = args.reason or "AI agent edit"
            ok, failed, _msg = client.acquire_multiple(args.file_paths, reason=reason)
            claimed = [fp for fp in args.file_paths if fp not in failed]
            if claimed:
                print(f"✓ Claimed {len(claimed)} file(s) as agent.")
            if failed:
                print(f"✗ Could not claim: {', '.join(failed)}")
            sys.exit(0 if ok else 1)

        elif args.command == "release":
            ok, msg = client.release(args.file_path)
            print(f"{'✓' if ok else '✗'} {msg}")

        elif args.command == "active":
            from .errors import LockServiceUnavailableError

            started_watcher = _ensure_watcher_running(client, "active")
            if started_watcher:
                try:
                    # Prime lock state immediately so `collab active` reflects
                    # current git changes right after watcher bootstrap.
                    client._reconcile()
                except Exception as exc:
                    logger.debug("Auto-start reconcile failed: %s", exc)
            try:
                locks = client.active()
            except LockServiceUnavailableError as exc:
                print(f"❌ Lock service unavailable: {exc.message}")
                if exc.detail:
                    print(f"   {exc.detail}")
                print(
                    "   Local git changes are not listed as locks until the "
                    "service is reachable."
                )
                sys.exit(1)
            if getattr(args, "mine", False):
                locks = [lk for lk in locks if client._lock_owned_by_me(lk)]

            if not locks:
                print("No active locks.")
            else:
                from . import agent_identity

                for lk in locks:
                    owner = agent_identity.format_lock_owner(
                        str(lk.get("developer_id", "?")),
                        lk.get("agent_id"),
                        lk.get("agent_label"),
                        lk.get("agent_kind"),
                    )
                    print(
                        f"  {lk.get('file_path')} — {owner} "
                        f"(branch: {lk.get('branch_name', 'N/A')}, "
                        f"reason: {lk.get('reason', 'N/A')})"
                    )

        elif args.command == "status":
            _ensure_watcher_running(client, "status")
            info = client.get_lock_status(args.file_path)
            if info.get("is_locked"):
                from . import agent_identity

                locked_by = agent_identity.format_lock_owner(
                    str(info.get("locked_by", "?")),
                    info.get("locked_by_agent_id"),
                    info.get("locked_by_agent_label"),
                    info.get("locked_by_agent_kind"),
                )
                acquired_at = info.get("acquired_at")
                can_edit = info.get("can_edit", False)
                edit_note = " (you can edit)" if can_edit else ""
                print(f"🔒 Locked by {locked_by} since {acquired_at}{edit_note}")
            else:
                print("🔓 File is unlocked.")

        elif args.command == "release-all":
            count = client.release_all(
                include_agent=not getattr(args, "identity_only", False)
            )
            print(f"Released {count} lock(s).")

        elif args.command == "force-release":
            ok, msg = client.force_release(args.file_path)
            print(f"{'✓' if ok else '✗'} {msg}")

        elif args.command == "force-release-all":
            if not client.is_admin:
                print("✗ Permission denied: admin required to force-release all locks.")
                sys.exit(1)
            count = client.force_release_all()

            # Minimal, user-facing output
            print(f"✓ Force-released {count} lock(s).")
            sys.exit(0)

        elif args.command == "acquire-batch":
            ok, failed, msg = client.acquire_multiple(
                args.file_paths, reason=getattr(args, "reason", None)
            )
            if ok:
                print(f"✓ Locked {len(args.file_paths)} file(s).")
            else:
                print(f"✗ {msg}. Failed: {failed}")
                sys.exit(1)

        elif args.command == "release-batch":
            ok, count, msg = client.release_multiple(args.file_paths)
            print(f"Released {count} lock(s).")

        elif args.command == "daemon-start":
            open_flag = getattr(args, "open_dashboard", False)
            auto_env = os.getenv("AUTO_OPEN_DASHBOARD", "0").lower() in (
                "1",
                "true",
                "yes",
            )
            client.daemon_start(
                interval=getattr(args, "interval", 5),
                timeout_mins=getattr(args, "timeout", 0),
                open_dashboard=(open_flag or auto_env),
            )

        elif args.command == "daemon-stop":
            # Suppress collab.* info logs from echoing to the terminal while
            # performing the stop action (they will still be written to
            # logs/collab.log).
            client.daemon_stop()

        elif args.command == "daemon-status":
            running = client.daemon_status()
            sys.exit(0 if running else 1)

        elif args.command == "daemon-restart":
            open_flag = getattr(args, "open_dashboard", False)
            auto_env = os.getenv("AUTO_OPEN_DASHBOARD", "0").lower() in (
                "1",
                "true",
                "yes",
            )
            client.daemon_stop()
            client.daemon_start(
                interval=getattr(args, "interval", 5),
                timeout_mins=getattr(args, "timeout", 0),
                open_dashboard=(open_flag or auto_env),
            )

        elif args.command == "cleanup":
            client.cleanup_orphaned_processes()

        elif args.command == "dashboard":
            client.dashboard()
            print("Dashboard local server running. Press Ctrl+C to stop.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

        elif args.command == "reconcile":
            client._reconcile()

        elif args.command == "history":
            fp = getattr(args, "file_path", None)
            rows = client.history(fp, limit=getattr(args, "limit", 20))
            if getattr(args, "json_output", False):
                print(json.dumps(rows, indent=2))
            elif not rows:
                if fp:
                    print(f"No history found for '{fp}'.")
                    print("  Tip: run 'collab history' (no file) to see all.")
                else:
                    print("No lock history found.")
            else:
                # Check if results came from fallback (partial match)
                if fp and rows and rows[0].get("file_path") != fp:
                    actual = rows[0].get("file_path", "")
                    print(
                        f"  (No exact match for '{fp}' — "
                        f"showing partial matches for '{actual.rsplit('/', 1)[-1]}')\n"
                    )
                for row in rows:
                    acquired = row.get("acquired_at", "?")[:19].replace("T", " ")
                    released = row.get("released_at", "?")[:19].replace("T", " ")
                    dev = row.get("developer_id", "?")
                    fpath = row.get("file_path", "?")
                    branch = row.get("branch_name", "")
                    outcome = row.get("outcome", "?")
                    print(
                        f"  {fpath}  @{dev}  "
                        f"[{acquired} → {released}]  "
                        f"branch:{branch}  {outcome}"
                    )

        elif args.command == "history-prune":
            days = int(getattr(args, "days", 30))
            ok, deleted, msg = client.prune_history(retention_days=days)
            if ok:
                print(
                    f"✓ Pruned {deleted} lock history row(s) "
                    f"older than {days} day(s)."
                )
            else:
                print(f"✗ Failed to prune lock history: {msg}")
                sys.exit(1)

        elif args.command == "ping":
            import time as _t
            from socket import create_connection as _connect
            from urllib.parse import urlparse as _urlparse

            url = os.getenv("SUPABASE_URL", "")
            if not url:
                print("✗ SUPABASE_URL is not configured (check .env)")
                sys.exit(1)

            try:
                host = _urlparse(url).hostname or ""
            except Exception:
                print(f"✗ Invalid SUPABASE_URL: {url!r}")
                sys.exit(1)

            if not host:
                print(f"✗ Could not parse hostname from SUPABASE_URL: {url!r}")
                sys.exit(1)

            try:
                start = _t.monotonic()
                with _connect((host, 443), timeout=5.0):
                    pass
                elapsed = _t.monotonic() - start
                print(f"✓ Supabase is reachable " f"({host}, {elapsed*1000:.0f}ms)")
                sys.exit(0)
            except OSError as exc:
                print(f"✗ Cannot reach {host}: {exc}")
                print("   Verify network/VPN and that the Supabase project is active.")
                sys.exit(1)

        elif args.command == "info":
            # Gather daemon status (suppress daemon_status()'s own print output)
            import io as _io

            from . import __version__ as _ver
            from . import agent_identity
            from .lock_client import _COLLAB_ROOT as _root
            from .lock_client import _PROJECT_ROOT as _p_root

            try:
                _old_stdout = sys.stdout
                sys.stdout = _io.StringIO()
                try:
                    daemon_running = client.daemon_status()
                finally:
                    sys.stdout = _old_stdout
            except Exception:
                daemon_running = False
            daemon_state = "RUNNING" if daemon_running else "stopped"

            # Count active locks
            lock_count: int | str
            my_locks: int | str
            try:
                active_locks = client.active()
                lock_count = len(active_locks)
                my_locks = sum(1 for lk in active_locks if client._lock_owned_by_me(lk))
            except Exception:
                lock_count = "?"
                my_locks = "?"

            # Identity summary
            ident = agent_identity.identity_summary(
                client.developer_id,
                client.agent_id,
                client.agent_label,
                getattr(client, "agent_kind", None),
            )

            state_dir = os.path.join(_root, ".collab")

            print(f"collab-runtime v{_ver}")
            print()
            print("── Daemon ──")
            print(f"  Status:      {daemon_state}")
            print(f"  PID file:    {os.path.join(state_dir, '.daemon.pid')}")
            print()
            print("── Identity ──")
            print(f"  Developer:   {ident['developer_id']}")
            print(f"  Mode:        {ident['mode']}")
            if ident.get("agent_id"):
                print(f"  Agent ID:    {ident['agent_id']}")
            if ident.get("agent_label"):
                print(f"  Agent label: {ident['agent_label']}")
            if ident.get("agent_kind"):
                print(f"  Agent kind:  {ident['agent_kind']}")
            print()
            print("── Locks ──")
            print(f"  Active:      {lock_count}")
            print(f"  Mine:        {my_locks}")
            print(f"  Admin:       {'yes' if client.is_admin else 'no'}")
            print()
            print("── Paths ──")
            print(f"  Runtime:     {_root}")
            print(f"  Project:     {_p_root}")
            print(f"  State dir:   {state_dir}")
            print(f"  Log file:    {os.path.join(_root, 'logs', 'collab.log')}")
            sys.exit(0)

        elif args.command == "logs":
            from .lock_client import _COLLAB_ROOT as _root

            log_path = os.path.join(_root, "logs", "collab.log")
            lines = int(getattr(args, "lines", 50))
            follow = getattr(args, "follow", False)

            if not os.path.isfile(log_path):
                print(f"No log file found at {log_path}")
                sys.exit(1)

            if follow:
                # tail -f mode
                print(f"Following {log_path} (Ctrl+C to stop)...")
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(0, 2)  # seek to end
                        while True:
                            line = fh.readline()
                            if line:
                                print(line, end="")
                            else:
                                time.sleep(0.25)
                except KeyboardInterrupt:
                    print()
                sys.exit(0)
            else:
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                        all_lines = fh.readlines()
                    for line in all_lines[-lines:]:
                        print(line, end="")
                except OSError as exc:
                    print(f"✗ Cannot read log file: {exc}")
                    sys.exit(1)
                sys.exit(0)

        elif args.command == "watch":
            # Ensure watcher child process uses the explicit PID namespace passed by
            # daemon-start. This prevents cross-instance status/stop interference.
            if getattr(args, "pid_file", None):
                from . import lock_client as _lc

                _lc.PID_FILE = str(getattr(args, "pid_file"))
            client.watch(
                interval=getattr(args, "interval", 5),
                timeout_mins=getattr(args, "timeout", 0),
                open_dashboard=getattr(args, "open_dashboard", False),
                daemon_mode=getattr(args, "daemon", False),
                parent_pid=getattr(args, "parent_pid", None),
                parent_name=getattr(args, "parent_name", None),
                parent_method=getattr(args, "parent_method", None),
                heartbeat_file=getattr(args, "heartbeat_file", None),
                heartbeat_grace_seconds=getattr(args, "heartbeat_grace_seconds", 10),
            )

        else:
            parser.print_help()


def main() -> None:
    """Entry point for the collab CLI.

    Orchestrates exception handling and error logging for the command-line interface.
    Can be called from run.py, collab CLI, or package consumers that want to invoke the
    CLI programmatically.
    """
    try:
        _run_cli()
    except Exception as exc:
        tb_str = _tb.format_exc()
        # Log to the standard logs/ directory via the structured logger.
        logger.error("Unhandled exception: %s\n%s", exc, tb_str)
        print(
            "FATAL: lock_client crashed — see logs/collab.log",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
