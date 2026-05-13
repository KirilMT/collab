<#
Local helper to build and upload to TestPyPI (PowerShell).

Usage (from repo root):
  powershell -ExecutionPolicy Bypass -File .\scripts\local_publish_test.ps1

This script will:
 - Build sdist and wheel into ./dist
 - Create an ephemeral venv `.venv.verify` and install `twine`
 - Upload the artifacts to TestPyPI using a token you provide
#>
param(
    [string]$Token,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
set-strictmode -version latest

$script:ErrorCount = 0

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   Local Publish Test"
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

function Ensure-Command([string]$name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Host "   $name not found. Install it and try again." -ForegroundColor Red
        Exit 1
    }
}

Ensure-Command python

# ============================================================================
# STEP 1: CLEANUP & PREPARATION
# ============================================================================
Write-Host "[Step 1/6] Preparing environment..." -ForegroundColor Yellow

if (Test-Path "dist") {
    Remove-Item "dist\*" -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "   Cleaning up old build artifacts " -NoNewline
Write-Host "OK" -ForegroundColor Green

Write-Host "   Patching pyproject.toml with unique dev version " -NoNewline
$tomlPath = (Resolve-Path "pyproject.toml").Path
$utf8NoBom = New-Object System.Text.UTF8Encoding $False
$originalToml = [System.IO.File]::ReadAllText($tomlPath, $utf8NoBom)
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
$versionMatch = [regex]::Match($originalToml, '(?m)^version\s*=\s*"([^"]+)"')
$devVersion = "$($versionMatch.Groups[1].Value).dev$timestamp"
$patchedToml = $originalToml -replace '(?m)^version\s*=\s*"([^"]+)"', ("version = `"$devVersion`"")
[System.IO.File]::WriteAllText($tomlPath, $patchedToml, $utf8NoBom)
Write-Host "OK" -ForegroundColor Green

# ============================================================================
# STEP 2: BUILD
# ============================================================================
Write-Host "`n[Step 2/6] Building python artifacts..." -ForegroundColor Yellow

try {
    Write-Host "   Building sdist+wheel into ./dist/ " -NoNewline
    python -m build --sdist --wheel --outdir dist 2>&1 | Out-Null
    Write-Host "OK" -ForegroundColor Green
} catch {
    Write-Host "FAILED" -ForegroundColor Red
    $script:ErrorCount++
} finally {
    Write-Host "   Restoring original pyproject.toml " -NoNewline
    [System.IO.File]::WriteAllText($tomlPath, $originalToml, $utf8NoBom)
    Write-Host "OK" -ForegroundColor Green
}

# ============================================================================
# STEP 3: VENV AND TWINE
# ============================================================================
Write-Host "`n[Step 3/6] Setting up ephemeral venv..." -ForegroundColor Yellow

if (-not (Test-Path ".venv.verify")) {
    Write-Host "   Creating .venv.verify " -NoNewline
    python -m venv .venv.verify
    Write-Host "OK" -ForegroundColor Green
} else {
    Write-Host "   Using existing .venv.verify " -NoNewline
    Write-Host "OK" -ForegroundColor Green
}

$py = Join-Path -Path ".venv.verify" -ChildPath "Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "   Cannot find venv python at $py" -ForegroundColor Red
    Exit 1
}

Write-Host "   Installing twine and build tools " -NoNewline
& $py -m pip install --upgrade pip --quiet 2>&1 | Out-Null
& $py -m pip install --upgrade build twine --quiet 2>&1 | Out-Null
Write-Host "OK" -ForegroundColor Green

# ============================================================================
# STEP 4: UPLOAD TO TESTPYPI
# ============================================================================
Write-Host "`n[Step 4/6] Uploading to TestPyPI..." -ForegroundColor Yellow

$artifacts = Get-ChildItem .\dist -File | Where-Object { $_.Name -match '\.(whl|tar\.gz)$' }
if (-not $artifacts) {
    Write-Host "   No artifacts found in ./dist" -ForegroundColor Red
    Exit 1
}

$token = $null
if ($PSBoundParameters.ContainsKey('Token') -and $Token) {
    $token = $Token
} elseif ($Env:TESTPYPI_TOKEN) {
    $token = $Env:TESTPYPI_TOKEN
} else {
    if (Test-Path ".env") {
        $tokenLine = Get-Content ".env" | Where-Object { $_ -match "^TESTPYPI_TOKEN=(.*)" } | Select-Object -First 1
        if ($tokenLine) {
            $token = $tokenLine -replace "^TESTPYPI_TOKEN=", ""
            $token = $token -replace '^["'']|["'']$', ''
        }
    }
    if (-not $token) {
        $userTokenFile = Join-Path -Path $Env:USERPROFILE -ChildPath ".testpypi_token"
        if (Test-Path $userTokenFile) {
            $token = Get-Content -Raw $userTokenFile
        } else {
            $secure = Read-Host -AsSecureString '   Paste TestPyPI token (input hidden)'
            $ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            try { $token = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($ptr) }
            finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
        }
    }
}

if ([string]::IsNullOrWhiteSpace($token)) {
    Write-Host "   No token available; aborting upload." -ForegroundColor Red
    Exit 1
}

$Env:TWINE_USERNAME = "__token__"
$Env:TWINE_PASSWORD = $token
$files = $artifacts | Select-Object -ExpandProperty FullName

if ($PSBoundParameters.ContainsKey('DryRun') -and $DryRun) {
    Write-Host "   Dry run: skipping upload." -ForegroundColor Gray
} else {
    Write-Host "   Pushing artifacts to TestPyPI " -NoNewline

    # We temporarily set ErrorActionPreference to Continue so we can capture twine output without terminating
    $oldErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $twineOut = & $py -m twine upload --repository-url https://test.pypi.org/legacy/ $files 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldErrorAction

    if ($exitCode -eq 0) {
        Write-Host "OK" -ForegroundColor Green
    } else {
        Write-Host "FAILED" -ForegroundColor Red
        Write-Host $twineOut -ForegroundColor Red
        $script:ErrorCount++
    }
}
Remove-Item Env:TWINE_PASSWORD -ErrorAction SilentlyContinue

# ============================================================================
# STEP 5: VERIFY TESTPYPI INSTALLATION
# ============================================================================
Write-Host "`n[Step 5/6] Verifying installation from TestPyPI..." -ForegroundColor Yellow

if ($PSBoundParameters.ContainsKey('DryRun') -and $DryRun) {
    Write-Host "   Dry run: skipping installation verify." -ForegroundColor Gray
} else {
    Write-Host "   Waiting 10s for TestPyPI to index v$devVersion " -NoNewline
    Start-Sleep -Seconds 10
    Write-Host "OK" -ForegroundColor Green

    Write-Host "   Installing collab-runtime==$devVersion " -NoNewline
    $oldErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $pipOut = & $py -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ "collab-runtime==$devVersion" 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldErrorAction

    if ($exitCode -eq 0) {
        Write-Host "OK" -ForegroundColor Green

        Write-Host "   Testing 'collab --help' execution " -NoNewline
        $collabPath = Join-Path -Path ".venv.verify" -ChildPath "Scripts\collab.exe"

        $oldErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        if (Test-Path $collabPath) {
            $helpOut = & $collabPath --help 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Host "OK" -ForegroundColor Green
            } else {
                Write-Host "FAILED" -ForegroundColor Red
                Write-Host $helpOut -ForegroundColor Red
                $script:ErrorCount++
            }
        } else {
            Write-Host "FAILED (collab.exe not found)" -ForegroundColor Red
            $script:ErrorCount++
        }
        $ErrorActionPreference = $oldErrorAction
    } else {
        Write-Host "FAILED" -ForegroundColor Red
        Write-Host $pipOut -ForegroundColor Red
        $script:ErrorCount++
    }
}

# ============================================================================
# STEP 6: VS CODE EXTENSION
# ============================================================================
Write-Host "`n[Step 6/6] Packaging VS Code Extension..." -ForegroundColor Yellow

$vscodeExtDir = Join-Path (Resolve-Path ".") "vscode-extension\collab-locks"
if (Test-Path $vscodeExtDir) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Push-Location $vscodeExtDir
        try {
            Write-Host "   Copying LICENSE to extension directory " -NoNewline
            Copy-Item "..\..\LICENSE" "LICENSE" -ErrorAction SilentlyContinue
            Write-Host "OK" -ForegroundColor Green

            Write-Host "   Installing NPM dependencies " -NoNewline
            $npmOut = npm ci --silent 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Host "OK" -ForegroundColor Green
            } else {
                Write-Host "FAILED" -ForegroundColor Red
                Write-Host $npmOut -ForegroundColor Red
                $script:ErrorCount++
            }

            Write-Host "   Building VSIX package " -NoNewline
            $vsceOut = npx vsce package 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Host "OK" -ForegroundColor Green
            } else {
                Write-Host "FAILED" -ForegroundColor Red
                Write-Host $vsceOut -ForegroundColor Red
                $script:ErrorCount++
            }
        } catch {
            Write-Host "FAILED" -ForegroundColor Red
            Write-Host $_.Exception.Message -ForegroundColor Red
            $script:ErrorCount++
        } finally {
            # Clean up the copied license so it doesn't clutter the git workspace in the extension dir
            Remove-Item "LICENSE" -ErrorAction SilentlyContinue
            Pop-Location
        }
    } else {
        Write-Host "   npm not found. Skipping VS Code extension packaging." -ForegroundColor Yellow
    }
} else {
    Write-Host "   VS Code extension directory not found." -ForegroundColor Yellow
}

# ============================================================================
# SUMMARY
# ============================================================================
Write-Host "`n========================================" -ForegroundColor Cyan
if ($script:ErrorCount -eq 0) {
    Write-Host "   Publish Test Complete!"
} else {
    Write-Host "   Publish Test completed with $($script:ErrorCount) error(s)" -ForegroundColor Yellow
}
Write-Host "========================================`n" -ForegroundColor Cyan

if ($script:ErrorCount -eq 0) {
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host "                        NEXT STEPS                              " -ForegroundColor Yellow
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  1. Verify TestPyPI package:" -ForegroundColor White
    Write-Host "     https://test.pypi.org/project/collab-runtime/" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  2. Test VS Code extension locally:" -ForegroundColor White
    Write-Host "     code --install-extension vscode-extension\collab-locks\*.vsix" -ForegroundColor Magenta
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host ""
}
