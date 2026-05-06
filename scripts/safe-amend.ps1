param(
    [ValidateSet('amend', 'cleanup')]
    [string]$Command = 'amend',

    # Amend parameters
    [switch]$NoEdit,
    [string]$Author,
    [switch]$SkipPreCommit,
    [switch]$KeepStash,
    [switch]$DryRun,

    # Cleanup parameters
    [switch]$All,
    [string]$Stash,
    [switch]$Force,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CommitArgs
)

$ErrorActionPreference = "Stop"

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$IgnoreExitCode
    )

    & git @Arguments
    $exitCode = $LASTEXITCODE

    if (-not $IgnoreExitCode -and $exitCode -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $exitCode"
    }

    return $exitCode
}

function Test-HasUnstagedChanges {
    $porcelain = git status --porcelain
    foreach ($line in $porcelain) {
        if ($line.Length -lt 2) { continue }
        if ($line[0] -eq '?' -and $line[1] -eq '?') { continue }
        if ($line[1] -ne ' ') { return $true }
    }
    return $false
}

function Get-SafeAmendStashes {
    $allStashes = & git stash list
    $safeAmendStashes = @()

    foreach ($line in $allStashes) {
        if ($line -match "safe-amend-\d{8}-\d{6}") {
            $safeAmendStashes += @{
                Ref   = ($line -split ":")[0]
                Full  = $line
                Date  = ([regex]::Match($line, 'safe-amend-(\d{8})-(\d{6})').Groups[1].Value)
                Time  = ([regex]::Match($line, 'safe-amend-(\d{8})-(\d{6})').Groups[2].Value)
            }
        }
    }

    return $safeAmendStashes
}

if ($Command -eq 'cleanup') {
    $stashes = Get-SafeAmendStashes

    if ($stashes.Count -eq 0) {
        Write-Host "No safe-amend stashes found." -ForegroundColor Yellow
        exit 0
    }

    if ($All) {
        if (-not $Force) {
            $prompt = Read-Host "Are you sure you want to drop ALL $($stashes.Count) safe-amend stashes? (y/N)"
            if ($prompt -notmatch '^y(es)?$') {
                Write-Host "Cleanup aborted." -ForegroundColor Yellow
                exit 0
            }
        }
        foreach ($stash in $stashes) {
            Invoke-Git -Arguments "stash", "drop", $stash.Ref
            Write-Host "Dropped $($stash.Ref)" -ForegroundColor Green
        }
        exit 0
    }

    Write-Host "Available safe-amend stashes:" -ForegroundColor Cyan
    foreach ($stash in $stashes) {
        Write-Host "  $($stash.Full)"
    }
    exit 0
}

# --- Amend Logic ---

$hasUnstaged = Test-HasUnstagedChanges

if (-not $hasUnstaged) {
    Write-Host "No unstaged tracked changes detected. Running standard git commit --amend." -ForegroundColor Cyan
    $cmd = @("commit", "--amend")
    if ($NoEdit) { $cmd += "--no-edit" }
    if ($Author) { $cmd += "--author=$Author" }
    if ($SkipPreCommit) { $cmd += "--no-verify" }
    $cmd += $CommitArgs

    if ($DryRun) {
        Write-Host "[DRY RUN] git $($cmd -join ' ')" -ForegroundColor DarkGray
        exit 0
    }

    Invoke-Git -Arguments $cmd
    exit 0
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stashName = "safe-amend-$timestamp"

Write-Host "1. Tracking unstaged changes..." -ForegroundColor Cyan
Write-Host "   (Stashing unstaged changes as '$stashName')" -ForegroundColor Gray

if ($DryRun) {
    Write-Host "[DRY RUN] git stash push --keep-index --message '$stashName'" -ForegroundColor DarkGray
} else {
    Invoke-Git -Arguments "stash", "push", "--keep-index", "--message", $stashName
}

Write-Host "`n2. Running git commit --amend..." -ForegroundColor Cyan
$cmd = @("commit", "--amend")
if ($NoEdit) { $cmd += "--no-edit" }
if ($Author) { $cmd += "--author=$Author" }
if ($SkipPreCommit) { $cmd += "--no-verify" }
$cmd += $CommitArgs

if ($DryRun) {
    Write-Host "[DRY RUN] git $($cmd -join ' ')" -ForegroundColor DarkGray
} else {
    # We ignore exit code here so we can restore stash even if pre-commit fails
    & git $cmd
    $commitExitCode = $LASTEXITCODE
}

Write-Host "`n3. Restoring saved unstaged changes..." -ForegroundColor Cyan
if ($DryRun) {
    Write-Host "[DRY RUN] git stash apply stash@{0}" -ForegroundColor DarkGray
} else {
    # Only keep the stash if apply succeeds
    try {
        Invoke-Git -Arguments "stash", "apply", "stash@{0}"
        Write-Host "✅ Restored your unstaged changes." -ForegroundColor Green

        if (-not $KeepStash -and $commitExitCode -eq 0) {
            Write-Host "   (Dropping temporary stash...)" -ForegroundColor Gray
            Invoke-Git -Arguments "stash", "drop", "stash@{0}"
        } elseif (-not $commitExitCode -eq 0) {
            Write-Host "   (Keeping stash because commit failed. You can drop it later with '-Command cleanup')" -ForegroundColor Yellow
        }
    } catch {
        Write-Error "Failed to restore unstaged changes safely from stash@{0}!"
        Write-Error "Your unstaged changes are safe in the stash named '$stashName'."
        Write-Error "You can manually restore them using: git stash pop stash@{0}"
    }
}

if (-not $DryRun -and $commitExitCode -ne 0) {
    exit $commitExitCode
}
