"""Regression tests for setup-script collab hook overlay wiring."""

from __future__ import annotations

from tests.backend.unit.scripts._helpers import ROOT


def _script_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_setup_ps1_overlays_collab_hooks_after_pre_commit_install():
    text = _script_text("scripts/setup.ps1")

    install_index = text.index("& $preCommitExe install --hook-type $type --overwrite")
    overlay_index = text.index(
        '$sourceHooksDir = Join-Path $projectRoot "scripts\\git-hooks"'
    )

    assert overlay_index > install_index
    assert '$targetHooksDir = Join-Path $projectRoot ".git\\hooks"' in text
    assert (
        '$hookNames = @("pre-commit", "post-commit", "pre-push", "commit-msg")' in text
    )
    assert "Copy-Item -Path $src -Destination $dst -Force" in text
    assert "Collab hook overlay installed " in text
    assert "Collab hook templates missing (scripts/git-hooks/) " in text


def test_setup_ps1_only_overlays_after_successful_hook_install():
    text = _script_text("scripts/setup.ps1")

    success_branch = text.index("if (-not $hookInstallFailed) {")
    overlay_index = text.index(
        '$sourceHooksDir = Join-Path $projectRoot "scripts\\git-hooks"'
    )
    warn_branch = text.index(
        'Write-Host "   Git hook installation " ' "-NoNewline -ForegroundColor White"
    )

    assert success_branch < overlay_index < warn_branch


def test_setup_sh_runs_install_hooks_after_pre_commit_install():
    text = _script_text("scripts/setup.sh")

    install_index = text.index(
        'if ! pre-commit install --hook-type "$hook_type" '
        "--overwrite >/dev/null 2>&1; then"
    )
    overlay_index = text.index(
        'if [ -f "$PROJECT_ROOT/scripts/install_hooks.sh" ]; then'
    )

    assert overlay_index > install_index
    assert (
        'if sh "$PROJECT_ROOT/scripts/install_hooks.sh" >/dev/null 2>&1; then' in text
    )
    assert 'print_success "Collab hook overlay installed"' in text
    assert "Warning: collab hook overlay installation failed." in text
    assert "Warning: scripts/install_hooks.sh not found." in text


def test_setup_sh_only_overlays_in_success_branch():
    text = _script_text("scripts/setup.sh")

    success_branch = text.index("if [ $HOOK_INSTALL_FAILED -eq 0 ]; then")
    overlay_index = text.index(
        'if [ -f "$PROJECT_ROOT/scripts/install_hooks.sh" ]; then'
    )
    warn_branch = text.index(
        'echo "   ${YELLOW}Warning: pre-commit hook installation failed.${NC}" ' ">&2"
    )

    assert success_branch < overlay_index < warn_branch


def test_setup_dev_ps1_installs_vscode_extension_dependencies_from_root_path():
    text = _script_text("scripts/setup-dev.ps1")

    needle = r"$vscodeExtDir = Join-Path $projectRoot 'editors\vscode\collab-locks'"
    assert needle in text
    assert "npm install --silent 2>$null" in text
    assert "VS Code extension workspace deps (npm) " in text


def test_setup_dev_ps1_pycharm_installs_run_config():
    text = _script_text("scripts/setup-dev.ps1")

    assert "return 'jetbrains'" in text
    assert (
        r"$ideaRunConfigDir = Join-Path $projectRoot '.idea\runConfigurations'" in text
    )
    assert (
        r"$xmlSrc = Join-Path $projectRoot 'editors\pycharm\Collab_Lock_Watcher.xml'"
        in text
    )
    assert "Copy-Item -Path $xmlSrc" in text
    assert "PyCharm run configuration installed " in text


def test_install_hooks_sh_includes_commit_msg():
    text = _script_text("scripts/install_hooks.sh")

    assert "commit-msg" in text
    assert "pre-commit post-commit pre-push commit-msg" in text


def test_setup_ps1_skips_collab_reinstall_when_healthy():
    text = _script_text("scripts/setup.ps1")

    assert "function Test-SetupCollabInstallHealthy" in text
    assert "already installed and healthy" in text
    assert "(use -Force to reinstall)" in text
    assert "[switch]$Force = $false" in text


def test_setup_sh_skips_collab_reinstall_when_healthy():
    text = _script_text("scripts/setup.sh")

    assert "setup_collab_install_healthy" in text
    assert "already installed and healthy" in text
    assert "--force" in text
    assert "SKIP_COLLAB_REINSTALL" in text


def test_setup_dev_ps1_forwards_force_to_production_setup():
    text = _script_text("scripts/setup-dev.ps1")

    assert "[switch]$Force = $false" in text
    assert "& $setupScript -CalledFromDev -Force:$Force" in text


def test_setup_dev_sh_forwards_force_to_production_setup():
    text = _script_text("scripts/setup-dev.sh")

    assert "FORCE=false" in text
    assert "SETUP_ARGS=(--called-from-dev)" in text
    assert "SETUP_ARGS+=(--force)" in text
    assert './scripts/setup.sh "${SETUP_ARGS[@]}"' in text
