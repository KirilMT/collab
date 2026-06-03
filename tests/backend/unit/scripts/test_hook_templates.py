"""Regression tests for collab git hook templates and installer overlay."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.backend.unit.scripts._helpers import ROOT

GIT_SH_CANDIDATES = [
    shutil.which("sh"),
    r"C:\Users\kmartineztamayo\AppData\Local\Programs\Git\bin\sh.exe",
    r"C:\Program Files\Git\bin\sh.exe",
]


@pytest.fixture(scope="module")
def git_sh() -> str:
    """Return a shell executable compatible with git hook scripts."""
    for candidate in GIT_SH_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate
    pytest.skip("Git shell executable not available for hook template tests")


@pytest.fixture()
def hook_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with minimal collab hook runtime."""
    repo = tmp_path / "repo"
    repo.mkdir()

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Hook Tester"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "hook-tester@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / "scripts" / "git-hooks").mkdir(parents=True)
    (repo / ".git" / "hooks").mkdir(parents=True, exist_ok=True)
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")

    for hook_name in ("pre-commit", "post-commit", "pre-push", "commit-msg"):
        source = ROOT / "scripts" / "git-hooks" / hook_name
        target = repo / "scripts" / "git-hooks" / hook_name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    install_source = ROOT / "scripts" / "install_hooks.sh"
    install_target = repo / "scripts" / "install_hooks.sh"
    install_target.write_text(
        install_source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    fake_python = repo / ".venv" / "bin" / "python"
    # Use the absolute path of the running interpreter so that when the
    # pre-push hook prepends .venv/bin to PATH, `python` never resolves
    # back to this wrapper script causing an infinite self-referential loop.
    real_python = sys.executable.replace("\\", "/")
    fake_python.write_text(
        textwrap.dedent(f"""
            #!/bin/sh
            exec "{real_python}" "$@"
            """).lstrip(),
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    fake_pre_commit = repo / ".venv" / "bin" / "pre-commit"
    fake_pre_commit.write_text(
        textwrap.dedent("""
            #!/bin/sh
            stage=""
            while [ $# -gt 0 ]; do
                if [ "$1" = "--hook-stage" ]; then
                    stage="$2"
                    shift 2
                    continue
                fi
                shift
            done
            printf '[fake-pre-commit] %s\n' "$stage"
            if [ "$stage" = "pre-push" ] && [ -n "$FAKE_PRE_PUSH_FAIL" ]; then
                exit 1
            fi
            exit 0
            """).lstrip(),
        encoding="utf-8",
    )
    fake_pre_commit.chmod(0o755)

    collab_hook_helper = repo / "scripts" / "collab_git_hook.py"
    collab_hook_helper.write_text(
        textwrap.dedent("""
            import os
            import sys

            command = sys.argv[1]
            if command == "acquire-staged":
                mode = os.getenv("FAKE_ACQUIRE_MODE", "watcher")
                if mode == "watcher":
                    message = (
                        "[collab] Watcher running (PID: 3500) "
                        "— skipping pre-commit lock acquisition."
                    )
                    print(
                        message,
                        file=sys.stderr,
                    )
                    raise SystemExit(0)
                if mode == "conflict":
                    print(
                        "[collab] Commit blocked due to lock conflicts:",
                        file=sys.stderr,
                    )
                    print("  - conflicted.txt (locked by @otherdev)", file=sys.stderr)
                    raise SystemExit(1)
                print(
                    "[collab] Checking locks for 1 staged file(s)...",
                    file=sys.stderr,
                )
                print("[collab] Locks acquired for 1 staged file(s).", file=sys.stderr)
                raise SystemExit(0)
            if command == "release-all":
                count = os.getenv("FAKE_RELEASE_COUNT", "1")
                print(f"[collab] Released {count} lock(s).", file=sys.stderr)
                raise SystemExit(0)
            raise SystemExit(2)
            """).lstrip(),
        encoding="utf-8",
    )

    return repo


def _run_sh(
    script: Path,
    shell_path: str,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        [shell_path, str(script)],
        cwd=cwd,
        env=run_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def _normalized_output(result: subprocess.CompletedProcess[str]) -> str:
    """Normalize common Git Bash mojibake so assertions stay stable on Windows."""
    return (result.stdout + result.stderr).replace("â€”", "—")


def test_install_hooks_copies_templates_into_git_hooks(hook_repo: Path, git_sh: str):
    result = _run_sh(hook_repo / "scripts" / "install_hooks.sh", git_sh, hook_repo)

    assert result.returncode == 0
    assert "Installed git hooks from scripts/git-hooks/" in result.stdout
    for hook_name in ("pre-commit", "post-commit", "pre-push", "commit-msg"):
        expected = (hook_repo / "scripts" / "git-hooks" / hook_name).read_text(
            encoding="utf-8"
        )
        actual = (hook_repo / ".git" / "hooks" / hook_name).read_text(encoding="utf-8")
        assert actual == expected


def test_pre_commit_hook_prints_watcher_message_then_runs_framework(
    hook_repo: Path,
    git_sh: str,
):
    staged = hook_repo / "tracked.txt"
    staged.write_text("content\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=hook_repo, check=True)

    result = _run_sh(
        hook_repo / "scripts" / "git-hooks" / "pre-commit",
        git_sh,
        hook_repo,
        env={"SKIP": "validate-code"},
    )

    assert result.returncode == 0
    combined = _normalized_output(result)
    assert (
        "[collab] Watcher running (PID: 3500) — skipping pre-commit lock acquisition."
        in combined
    )
    assert "[collab] Locks OK — running project validations..." in combined
    assert "[fake-pre-commit] pre-commit" in combined


def test_pre_commit_hook_blocks_on_conflict(hook_repo: Path, git_sh: str):
    staged = hook_repo / "conflicted.txt"
    staged.write_text("content\n", encoding="utf-8")
    subprocess.run(["git", "add", "conflicted.txt"], cwd=hook_repo, check=True)

    result = _run_sh(
        hook_repo / "scripts" / "git-hooks" / "pre-commit",
        git_sh,
        hook_repo,
        env={"FAKE_ACQUIRE_MODE": "conflict"},
    )

    assert result.returncode == 1
    combined = _normalized_output(result)
    assert "[collab] Commit blocked due to lock conflicts:" in combined
    assert "conflicted.txt" in combined
    assert "[collab] Commit aborted due to file lock conflicts." in combined
    assert "[fake-pre-commit] pre-commit" not in combined
    assert "[collab] Locks OK — running project validations..." not in combined


def _path_without_venv_entries(path_value: str, venv_marker: str) -> str:
    parts = [
        segment
        for segment in path_value.split(os.pathsep)
        if segment and venv_marker.lower() not in segment.lower()
    ]
    return os.pathsep.join(parts) if parts else path_value


def test_pre_commit_hook_uses_project_venv_without_venv_on_path(
    hook_repo: Path,
    git_sh: str,
):
    """IDE git runs hooks without venv; project python path must still work."""
    staged = hook_repo / "no-venv-path.txt"
    staged.write_text("content\n", encoding="utf-8")
    subprocess.run(["git", "add", "no-venv-path.txt"], cwd=hook_repo, check=True)

    stripped_path = _path_without_venv_entries(
        os.environ.get("PATH", ""),
        str(hook_repo / ".venv"),
    )
    result = _run_sh(
        hook_repo / "scripts" / "git-hooks" / "pre-commit",
        git_sh,
        hook_repo,
        env={
            "PATH": stripped_path,
            "VIRTUAL_ENV": "",
            "FAKE_ACQUIRE_MODE": "acquire",
        },
    )

    assert result.returncode == 0
    combined = _normalized_output(result)
    assert "[collab] Checking locks for 1 staged file(s)..." in combined
    assert "[collab] Locks OK — running project validations..." in combined
    assert "[fake-pre-commit] pre-commit" in combined


def test_post_commit_hook_prints_message_and_chains_framework(
    hook_repo: Path,
    git_sh: str,
):
    result = _run_sh(
        hook_repo / "scripts" / "git-hooks" / "post-commit",
        git_sh,
        hook_repo,
    )

    assert result.returncode == 0
    combined = _normalized_output(result)
    assert (
        "[collab] Commit detected. Locks remain active until files are pushed."
        in combined
    )
    assert "[fake-pre-commit] post-commit" in combined


def test_pre_push_hook_keeps_locks_when_validation_fails(hook_repo: Path, git_sh: str):
    result = _run_sh(
        hook_repo / "scripts" / "git-hooks" / "pre-push",
        git_sh,
        hook_repo,
        env={"FAKE_PRE_PUSH_FAIL": "1"},
    )

    assert result.returncode == 1
    combined = _normalized_output(result)
    assert "[fake-pre-commit] pre-push" in combined
    assert "[collab] Pre-push validation failed - keeping locks active." in combined
    assert (
        "[collab] Releasing all locks after successful pre-push validation..."
        not in combined
    )


def test_pre_push_hook_releases_locks_after_success(hook_repo: Path, git_sh: str):
    result = _run_sh(
        hook_repo / "scripts" / "git-hooks" / "pre-push",
        git_sh,
        hook_repo,
        env={"FAKE_RELEASE_COUNT": "4"},
    )

    assert result.returncode == 0
    combined = _normalized_output(result)
    assert "[fake-pre-commit] pre-push" in combined
    assert (
        "[collab] Releasing all locks after successful pre-push validation..."
        in combined
    )
    assert "[collab] Released 4 lock(s)." in combined


def _run_sh_with_arg(
    script: Path,
    shell_path: str,
    cwd: Path,
    arg: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        [shell_path, str(script), arg],
        cwd=cwd,
        env=run_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def test_commit_msg_hook_passes_valid_conventional_commit(hook_repo: Path, git_sh: str):
    msg_file = hook_repo / ".git" / "COMMIT_EDITMSG"
    msg_file.write_text("feat(core): add new feature\n", encoding="utf-8")

    result = _run_sh_with_arg(
        hook_repo / "scripts" / "git-hooks" / "commit-msg",
        git_sh,
        hook_repo,
        arg=str(msg_file),
    )

    assert result.returncode == 0


def test_commit_msg_hook_blocks_invalid_message(hook_repo: Path, git_sh: str):
    msg_file = hook_repo / ".git" / "COMMIT_EDITMSG"
    msg_file.write_text("added some stuff\n", encoding="utf-8")

    result = _run_sh_with_arg(
        hook_repo / "scripts" / "git-hooks" / "commit-msg",
        git_sh,
        hook_repo,
        arg=str(msg_file),
    )

    assert result.returncode == 1
    assert "Conventional Commits" in result.stderr


def test_commit_msg_hook_allows_merge_commit(hook_repo: Path, git_sh: str):
    msg_file = hook_repo / ".git" / "COMMIT_EDITMSG"
    msg_file.write_text("Merge branch 'main' into feature\n", encoding="utf-8")

    result = _run_sh_with_arg(
        hook_repo / "scripts" / "git-hooks" / "commit-msg",
        git_sh,
        hook_repo,
        arg=str(msg_file),
    )

    assert result.returncode == 0


def test_commit_msg_hook_allows_fixup_commit(hook_repo: Path, git_sh: str):
    msg_file = hook_repo / ".git" / "COMMIT_EDITMSG"
    msg_file.write_text("fixup! feat(core): add new feature\n", encoding="utf-8")

    result = _run_sh_with_arg(
        hook_repo / "scripts" / "git-hooks" / "commit-msg",
        git_sh,
        hook_repo,
        arg=str(msg_file),
    )

    assert result.returncode == 0
