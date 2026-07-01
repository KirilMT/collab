"""Git hook runtime shipped with the ``collab`` package.

This module is the consumer-facing counterpart to the repo-internal
``scripts/collab_git_hook.py`` helper. It is importable from an installed wheel
so downstream projects get working hooks via ``pip install collab-runtime`` plus
``collab init-hooks`` — without copying any collab-repo files.

Responsibilities:

* ``acquire-staged`` / ``release-all`` — lock lifecycle orchestration invoked by
  the installed git hooks (``python -m collab.githooks <command>``).
* ``install_hooks`` — copy the bundled hook templates into a consumer repo's git
  hooks directory (honoring ``core.hooksPath`` and worktrees).

The project root is resolved from git at call time (git runs hooks from the
repository top level), so the same module works for any consumer repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from importlib import resources
from pathlib import Path
from typing import Optional

from collab import overlap, safe_subprocess

HOOK_NAMES = (
    "pre-commit",
    "post-commit",
    "pre-push",
    "commit-msg",
    "post-merge",
    "post-checkout",
)
_TEMPLATE_PACKAGE = "collab"
_TEMPLATE_DIR = "hook_templates"


def _hook_log(message: str) -> None:
    """Emit hook progress on stderr and flush for IDE git UIs (VS Code/Cursor)."""
    print(message, file=sys.stderr)
    sys.stderr.flush()


def _run_git(root: Optional[Path], *args: str) -> tuple[int, str]:
    """Run a git subcommand via the safe wrapper; return (returncode, stdout).

    All subprocess execution goes through :mod:`collab.safe_subprocess` so the package
    honors the project's subprocess security invariants (absolute exe resolution + argv
    allowlists). Returns a non-zero code on any failure.
    """
    cwd = str(root) if root is not None else None
    try:
        result = safe_subprocess.capture(["git", *args], policy="git", cwd=cwd)
    except Exception:
        return 1, ""
    return result.returncode, safe_subprocess.decode_output(result.stdout).strip()


def _git_toplevel(start: Optional[Path] = None) -> Path:
    """Return the git repository root, falling back to the working directory."""
    returncode, top = _run_git(start, "rev-parse", "--show-toplevel")
    if returncode == 0 and top:
        return Path(top)
    return start if start is not None else Path.cwd()


def _git_output(root: Path, *args: str) -> str:
    """Run a git command in ``root`` and return stripped stdout."""
    returncode, output = _run_git(root, *args)
    if returncode != 0:
        raise RuntimeError(output or "git failed")
    return output


def _get_staged_files(root: Path) -> list[str]:
    """Return staged files (added/copied/modified/renamed) for the commit."""
    output = _git_output(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [line.strip() for line in output.splitlines() if line.strip()]


def _read_pid_file() -> Optional[int]:
    """Read the watcher PID from the lock client's PID file, if present."""
    from collab.lock_client import PID_FILE

    pid_path = Path(PID_FILE)
    if not pid_path.exists():
        return None

    try:
        raw = pid_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not raw:
        return None

    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        pid = payload.get("pid")
        return pid if isinstance(pid, int) else None

    try:
        return int(raw)
    except ValueError:
        return None


def _pid_is_running(pid: int) -> bool:
    """Return True when a process with ``pid`` is currently alive."""
    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid))
    except Exception:
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True


def _watcher_pid() -> Optional[int]:
    """Return the live watcher PID, or None when no watcher is running."""
    pid = _read_pid_file()
    if pid is None:
        return None
    return pid if _pid_is_running(pid) else None


def _load_env(root: Path) -> None:
    """Load ``.env`` from the consumer repo root so lock checks have credentials."""
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def acquire_staged() -> int:
    """Acquire locks for staged files; used by the installed pre-commit hook."""
    root = _git_toplevel()
    _load_env(root)

    staged_files = _get_staged_files(root)
    if not staged_files:
        return 0

    watcher_pid = _watcher_pid()
    if watcher_pid is not None:
        _hook_log(
            "[collab] Watcher running "
            f"(PID: {watcher_pid}) - skipping pre-commit lock acquisition."
        )
        return 0

    count = len(staged_files)
    file_word = "file" if count == 1 else "files"
    _hook_log(f"[collab] Checking locks for {count} staged {file_word}...")

    from collab.lock_client import LockClient

    try:
        client = LockClient()
        ok, failed, _message = client.acquire_multiple(
            staged_files, reason="pre-commit"
        )
    except Exception as exc:
        _hook_log(f"[collab] Warning: lock check failed: {exc}")
        return 1 if os.getenv("LOCK_STRICT", "0") == "1" else 0

    if ok:
        _hook_log(f"[collab] Locks acquired for {count} staged {file_word}.")
        return 0

    _hook_log("[collab] Commit blocked due to lock conflicts:")
    for file_path in failed:
        try:
            status = client.get_lock_status(file_path)
        except Exception:
            status = {}
        owner = status.get("locked_by") or status.get("developer_id") or "unknown"
        _hook_log(f"  - {file_path} (locked by @{owner})")
    return 1


def warn_cross_branch_overlap(remote: Optional[str] = None) -> int:
    """Print cross-branch overlap warnings; returns non-zero on strict overlap.

    ``remote`` is the push target git passes to the pre-push hook (``$1``); it lets
    the check compare against the correct remote instead of assuming ``origin``.

    Returns ``overlap.EXIT_OVERLAP``/``EXIT_ERROR`` (non-zero) when strict mode
    must block the push, otherwise ``overlap.EXIT_OK``. Resolving the repo root is
    itself guarded so a failure there cannot crash the hook with a traceback: in
    strict mode it fails closed, otherwise it fails open.
    """
    try:
        root = _git_toplevel()
    except Exception as exc:  # pragma: no cover - defensive
        _hook_log(f"[collab] Warning: could not resolve repo root: {exc}")
        return (
            overlap.EXIT_ERROR
            if overlap.is_overlap_strict_enabled()
            else overlap.EXIT_OK
        )
    return overlap.warn_cross_branch_overlap(root, emit=_hook_log, remote=remote)


def release_all() -> int:
    """Release locks held by this developer; used by the pre-push hook.

    Default behavior releases every lock (work is "in progress" only while local). When
    ``COLLAB_PR_CLAIMS=1``, the files changed on the pushed branch are instead retained
    as persistent PR claims so cross-developer edit-time protection extends to the open
    PR; everything else is released as usual.
    """
    root = _git_toplevel()
    _load_env(root)

    from collab.lock_client import LockClient

    try:
        client = LockClient()
        if overlap.is_pr_claims_enabled():
            released = _release_retaining_pr_claims(client, root)
        else:
            released = client.release_all()
    except Exception as exc:
        _hook_log(f"[collab] Warning: lock cleanup failed: {exc}")
        return 0

    _hook_log(f"[collab] Released {released} lock(s).")
    return 0


def _release_retaining_pr_claims(client, root: Path) -> int:
    """Reconcile stale claims, then retain the pushed branch's files as claims.

    NOTE: the pre-push hook runs before the push transport completes, so claims may
    briefly exist for a branch that did not reach the remote if the push then fails;
    the next reconcile (branch-gone) or the DB-side expiry releases those.
    """
    if not client.claims_supported():
        _hook_log(
            "[collab] WARNING: COLLAB_PR_CLAIMS=1 but the Supabase claim migration "
            "is not applied (missing 'is_pr_claim' column). Locks will be RELEASED "
            "on push, not retained as claims. Apply supabase/schema.sql to enable "
            "PR claims, then re-run setup."
        )
        return int(client.release_all())

    try:
        stale = client.reconcile_pr_claims()
        if stale:
            _hook_log(f"[collab] Released {stale} stale PR claim(s).")
    except Exception as exc:
        _hook_log(f"[collab] Warning: PR-claim reconcile failed: {exc}")

    branch, changed = overlap.head_changed_files(root)
    if not changed:
        # No resolvable base / no changed files -> behave exactly as today.
        return int(client.release_all())

    _hook_log(
        f"[collab] Retaining {len(changed)} file(s) as PR claim(s) for "
        f"branch '{branch or '?'}' (COLLAB_PR_CLAIMS=1)."
    )
    return int(client.release_all_except(changed, branch))


def _read_template(name: str) -> str:
    """Return the bundled hook template text for ``name``."""
    resource = resources.files(_TEMPLATE_PACKAGE).joinpath(_TEMPLATE_DIR).joinpath(name)
    return resource.read_text(encoding="utf-8")


def _hooks_dir(root: Path) -> Path:
    """Resolve the git hooks directory, honoring core.hooksPath and worktrees."""
    returncode, value = _run_git(root, "config", "--get", "core.hooksPath")
    if returncode == 0 and value:
        hooks_path = Path(value)
        return hooks_path if hooks_path.is_absolute() else (root / hooks_path)

    returncode, value = _run_git(root, "rev-parse", "--git-path", "hooks")
    if returncode == 0 and value:
        hooks_path = Path(value)
        return hooks_path if hooks_path.is_absolute() else (root / hooks_path)

    return root / ".git" / "hooks"


def _is_collab_hook(text: str) -> bool:
    """Return True when existing hook content was produced by collab."""
    return "collab" in text.lower()


# Marker written into every installed hook so a later ``install_hooks`` run can tell
# whether the on-disk hook matches the packaged template (content fingerprint) and
# auto-reinstall stale hooks WITHOUT a manual --force flag (#181).
_HOOK_MARKER_RE = re.compile(r"# collab-hook v=\S+ fp=([0-9a-f]+)")


def _package_version() -> str:
    """Return the installed collab-runtime version (best-effort)."""
    try:
        from collab import __version__

        return str(__version__)
    except Exception:
        return "0.0.0"


def _template_fingerprint(raw_template: str) -> str:
    """Return a short content fingerprint (sha256) of a raw hook template."""
    normalized = raw_template.replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _stamp_template(raw_template: str, fingerprint: str) -> str:
    """Insert the collab-hook marker line after the shebang (or at the top)."""
    marker = f"# collab-hook v={_package_version()} fp={fingerprint}"
    lines = raw_template.replace("\r\n", "\n").split("\n")
    if lines and lines[0].startswith("#!"):
        lines.insert(1, marker)
    else:
        lines.insert(0, marker)
    return "\n".join(lines)


def _installed_fingerprint(text: str) -> Optional[str]:
    """Return the fingerprint recorded in an installed collab hook, if present."""
    match = _HOOK_MARKER_RE.search(text)
    return match.group(1) if match else None


def _is_precommit_hook(text: str) -> bool:
    """Return True when a hook is managed by the pre-commit framework.

    Collab hooks may legitimately mention ``pre-commit`` (they invoke it), so callers
    must check :func:`_is_collab_hook` first; this only matches the framework's own
    generated hooks so ``install_hooks`` never clobbers the pre-commit slot.
    """
    lowered = text.lower()
    return "hook-impl" in lowered or "generated by pre-commit" in lowered


def _backup_hook(target: Path) -> Optional[str]:
    """Back up an existing hook to ``<hook>.bak`` before overwriting.

    Returns name.
    """
    backup = (
        target.with_suffix(target.suffix + ".bak")
        if target.suffix
        else Path(str(target) + ".bak")
    )
    try:
        backup.write_bytes(target.read_bytes())
        return backup.name
    except OSError:
        return None


def _write_hook(target: Path, content: str) -> None:
    """Write hook *content* with LF endings and best-effort exec bit."""
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    try:
        mode = target.stat().st_mode
        target.chmod(mode | 0o755)
    except OSError:
        pass


def install_hooks(
    project_root: Optional[Path] = None,
    force: bool = False,
) -> dict[str, list[str] | str]:
    """Install (or update) the bundled collab git hooks into the target repository.

    Behavior (#181):

    * **Fresh** hooks are installed and content-stamped with a fingerprint marker.
    * **Collab-managed** hooks are auto-updated when their fingerprint no longer
      matches the packaged template -- so template changes reach existing clones on
      the next ``setup-dev`` / ``collab init-hooks`` run, with no manual flag.
    * **Up-to-date** collab hooks are left untouched.
    * **pre-commit-managed** hooks are never clobbered -- **even with ``force``** --
      because the framework owns that slot and re-claims it on ``pre-commit install``.
      The collab lock lifecycle runs through ``.pre-commit-config.yaml`` instead, so
      these are reported under ``precommit_managed`` (not an error, not actionable).
    * **Custom** (non-collab) hooks are preserved unless ``force`` is set; with
      ``force`` the original is backed up to ``<hook>.bak`` before overwriting, so a
      developer's custom hook is never destroyed silently.

    Returns a summary dict with ``installed`` (all written: fresh + updated),
    ``updated``, ``up_to_date``, ``skipped`` (custom hooks left in place),
    ``precommit_managed`` (framework-owned slots), ``backed_up`` and ``hooks_dir``.
    """
    root = Path(project_root) if project_root is not None else _git_toplevel()
    hooks_dir = _hooks_dir(root)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    updated: list[str] = []
    up_to_date: list[str] = []
    skipped: list[str] = []
    precommit_managed: list[str] = []
    backed_up: list[str] = []

    for name in HOOK_NAMES:
        target = hooks_dir / name
        raw = _read_template(name).replace("\r\n", "\n")
        fingerprint = _template_fingerprint(raw)
        content = _stamp_template(raw, fingerprint)

        if target.exists():
            try:
                existing = target.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                # Cannot read -> do not risk clobbering something unknown.
                skipped.append(name)
                continue

            if _is_collab_hook(existing):
                # Auto-update only when the fingerprint drifted from the package.
                if _installed_fingerprint(existing) == fingerprint:
                    up_to_date.append(name)
                    continue
                _write_hook(target, content)
                installed.append(name)
                updated.append(name)
                continue

            # The pre-commit framework owns this slot: never clobber it (even with
            # --force). The collab lock lifecycle runs via .pre-commit-config.yaml,
            # so this is expected coverage -- reported separately from custom skips.
            if _is_precommit_hook(existing):
                precommit_managed.append(name)
                continue

            # Custom (non-collab) hook: preserve unless forced; back up on force.
            if not force:
                skipped.append(name)
                continue
            backup_name = _backup_hook(target)
            if backup_name:
                backed_up.append(backup_name)
            _write_hook(target, content)
            installed.append(name)
            continue

        _write_hook(target, content)
        installed.append(name)

    return {
        "installed": installed,
        "updated": updated,
        "up_to_date": up_to_date,
        "skipped": skipped,
        "precommit_managed": precommit_managed,
        "backed_up": backed_up,
        "hooks_dir": str(hooks_dir),
    }


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for ``python -m collab.githooks <command>``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            "Usage: python -m collab.githooks "
            "<acquire-staged|release-all|check-overlap|init> [--force]",
            file=sys.stderr,
        )
        return 2

    command = args[0]
    if command == "acquire-staged":
        return acquire_staged()
    if command == "release-all":
        return release_all()
    if command == "check-overlap":
        # Optional positional arg: the push remote git passes to pre-push ($1).
        remote = args[1] if len(args) > 1 and not args[1].startswith("-") else None
        return warn_cross_branch_overlap(remote)
    if command == "init":
        force = "--force" in args[1:]
        summary = install_hooks(force=force)
        installed = summary.get("installed") or []
        updated = summary.get("updated") or []
        skipped = summary.get("skipped") or []
        precommit_managed = summary.get("precommit_managed") or []
        backed_up = summary.get("backed_up") or []
        fresh = [name for name in installed if name not in updated]
        print(f"[collab] Installed git hooks into {summary['hooks_dir']}")
        if fresh:
            print(f"[collab] Installed: {', '.join(fresh)}")
        if updated:
            print(f"[collab] Updated (template changed): {', '.join(updated)}")
        if backed_up:
            print(f"[collab] Backed up before overwrite: {', '.join(backed_up)}")
        if precommit_managed:
            print(
                "[collab] Managed by pre-commit (.pre-commit-config.yaml), left as-is: "
                f"{', '.join(precommit_managed)}"
            )
        if skipped:
            print(
                "[collab] Skipped existing custom hooks (rerun with --force to back "
                f"up & replace): {', '.join(skipped)}"
            )
        return 0

    print(f"[collab] Unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
