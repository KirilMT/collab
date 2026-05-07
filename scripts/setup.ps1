# setup.ps1 - Enhanced Collab Installation Script
# Provides detailed feedback and error handling for Windows environments

# Accept parameter to suppress header/footer when called from dev script
param(
    [switch]$CalledFromDev = $false
)

# Ensure we are in the project root
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Split-Path -Parent $scriptPath
Set-Location $projectRoot

# Only show header if not called from dev script
if (-not $CalledFromDev) {
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "   Collab Installation Script" -ForegroundColor Cyan
    Write-Host "========================================`n" -ForegroundColor Cyan
}

# Error counter for final summary
$script:ErrorCount = 0

function Test-SupabaseImport {
    param(
        [string]$PythonExe
    )

    if (-not (Test-Path $PythonExe)) {
        return $false
    }

    & $PythonExe -c "import supabase" 2>$null
    return ($LASTEXITCODE -eq 0)
}

# Function to refresh environment variables without restart
function Refresh-EnvPath {
    Write-Host "   Refreshing environment variables..." -ForegroundColor Gray
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# Step 1: Check Prerequisites
Write-Host "[Step 1/5] Checking prerequisites..." -ForegroundColor Yellow

# Step 1.1: Check for Python
function Check-Python {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $v = python --version 2>&1
        if ($v -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 12) {
                Write-Host "   Found: " -NoNewline -ForegroundColor White
                Write-Host "$v" -NoNewline -ForegroundColor White
                Write-Host " OK" -ForegroundColor Green
                return $true
            }
            else {
                Write-Warning "   Found: $v (Python 3.12+ recommended)"
                return $true
            }
        }
    }

    $pythonLocations = @(
        "${env:LOCALAPPDATA}\Programs\Python\Python312\python.exe",
        "${env:LOCALAPPDATA}\Programs\Python\Python311\python.exe",
        "${env:LOCALAPPDATA}\Programs\Python\Python310\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe"
    )

    foreach ($location in $pythonLocations) {
        if (Test-Path $location) {
            Write-Host "   Found Python at: $location" -ForegroundColor Yellow
            $pythonDir = Split-Path -Parent $location
            $env:Path = "$pythonDir;$pythonDir\Scripts;$env:Path"

            try {
                $currentPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
                if ($currentPath -notlike "*$pythonDir*") {
                    $newPath = "$pythonDir;$pythonDir\Scripts;$currentPath"
                    [System.Environment]::SetEnvironmentVariable("Path", $newPath, "User")
                    Write-Host "   Added to system PATH " -NoNewline -ForegroundColor White
                    Write-Host "OK" -ForegroundColor Green
                }
                else {
                    Write-Host "   Already in system PATH " -NoNewline -ForegroundColor White
                    Write-Host "OK" -ForegroundColor Green
                }
            }
            catch {
                Write-Warning "   Could not add to system PATH (may require admin rights). Added to current session only."
            }

            if (Get-Command python -ErrorAction SilentlyContinue) {
                $v = python --version 2>&1
                Write-Host "   Version: " -NoNewline -ForegroundColor White
                Write-Host "$v" -ForegroundColor Green
                return $true
            }
        }
    }

    return $false
}

if (-not (Check-Python)) {
    Write-Warning "   Python not found. Attempting automatic installation via winget..."

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            $pythonIds = @("Python.Python.3.12", "Python.Python.3.11", "Python.Python.3")
            $installed = $false

            foreach ($id in $pythonIds) {
                Write-Host "   Trying package ID: $id..." -ForegroundColor Gray
                $startTime = Get-Date
                $process = Start-Process -FilePath "winget" -ArgumentList "install -e --id $id --silent --accept-package-agreements --accept-source-agreements" -NoNewWindow -PassThru -Wait
                $duration = (Get-Date) - $startTime

                if ($process.ExitCode -eq 0) {
                    Write-Host "   Python installed successfully (took $([int]$duration.TotalSeconds) seconds)" -ForegroundColor Green
                    $installed = $true
                    break
                }
            }

            if ($installed) {
                Refresh-EnvPath
                if (-not (Check-Python)) {
                    Write-Warning "   Python installed but not found in PATH."
                    Write-Host "   Please restart your terminal and run this script again." -ForegroundColor Yellow
                    exit 1
                }
            }
            else {
                Write-Warning "   Automatic installation via winget failed."
                Write-Host ""
                Write-Host "   Please install Python manually:" -ForegroundColor Yellow
                Write-Host "   1. Download from: https://www.python.org/downloads/" -ForegroundColor White
                Write-Host "   2. Run installer and check 'Add Python to PATH'" -ForegroundColor White
                Write-Host "   3. Restart terminal and run this script again" -ForegroundColor White
                exit 1
            }
        }
        catch {
            Write-Warning "   Automatic installation failed: $_"
            Write-Host "   Please install Python manually from https://www.python.org" -ForegroundColor Yellow
            exit 1
        }
    }
    else {
        Write-Error "   Python not found and winget not available."
        Write-Host "   Please install Python manually from https://www.python.org" -ForegroundColor Yellow
        exit 1
    }
}

# Step 1.2: Check for Git
function Check-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        $v = git --version
        Write-Host "   Found: " -NoNewline -ForegroundColor White
        Write-Host "$v" -NoNewline -ForegroundColor White
        Write-Host " OK" -ForegroundColor Green
        return $true
    }
    return $false
}

if (-not (Check-Git)) {
    Write-Warning "   Git not found. Attempting automatic installation via winget..."

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            Write-Host "   Trying package ID: Git.Git..." -ForegroundColor Gray
            $startTime = Get-Date
            $process = Start-Process -FilePath "winget" -ArgumentList "install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements" -NoNewWindow -PassThru -Wait
            $duration = (Get-Date) - $startTime

            if ($process.ExitCode -eq 0) {
                Write-Host "   Git installed successfully (took $([int]$duration.TotalSeconds) seconds)" -ForegroundColor Green
                Refresh-EnvPath

                if (-not (Check-Git)) {
                    Write-Warning "   Git installed but not found in PATH."
                    Write-Host "   Please restart your terminal and run this script again." -ForegroundColor Yellow
                    exit 1
                }
            }
            else {
                Write-Warning "   Automatic installation via winget failed."
                Write-Host ""
                Write-Host "   Please install Git manually:" -ForegroundColor Yellow
                Write-Host "   1. Download from: https://git-scm.com/downloads" -ForegroundColor White
                Write-Host "   2. Restart terminal and run this script again" -ForegroundColor White
                exit 1
            }
        }
        catch {
            Write-Warning "   Automatic installation failed: $_"
            Write-Host "   Please install Git manually from https://git-scm.com" -ForegroundColor Yellow
            exit 1
        }
    }
    else {
        Write-Error "   Git not found and winget not available."
        Write-Host "   Please install Git manually from https://git-scm.com" -ForegroundColor Yellow
        exit 1
    }
}

# Step 2: Create Virtual Environment
Write-Host "`n[Step 2/5] Setting up virtual environment..." -ForegroundColor Yellow
if (-not (Test-Path ".venv")) {
    Write-Host "   Creating " -NoNewline -ForegroundColor White
    Write-Host ".venv" -NoNewline -ForegroundColor Magenta
    Write-Host "..." -NoNewline -ForegroundColor White
    python -m venv .venv
    if ($LASTEXITCODE -eq 0) {
        Write-Host " OK" -ForegroundColor Green
    }
    else {
        Write-Host " FAILED" -ForegroundColor Red
        Write-Error "Failed to create virtual environment."
        exit 1
    }
}
else {
    Write-Host "   Virtual environment already exists " -NoNewline -ForegroundColor White
    Write-Host "OK" -ForegroundColor Green
}

# Step 3: Install Dependencies
Write-Host "`n[Step 3/5] Installing core dependencies..." -ForegroundColor Yellow

$pipPath = ".\.venv\Scripts\pip.exe"
$pythonPath = ".\.venv\Scripts\python.exe"

if (-not (Test-Path $pipPath)) {
    Write-Error "   pip not found at $pipPath"
    Write-Error "   Virtual environment may be corrupted. Try deleting .venv and running again."
    exit 1
}

Write-Host "   Checking " -NoNewline -ForegroundColor White
Write-Host "pip" -NoNewline -ForegroundColor Magenta
Write-Host "..." -NoNewline -ForegroundColor White

$pipOutput = (& $pythonPath -m pip install --upgrade pip 2>&1) -join " "
$pipExitCode = $LASTEXITCODE

if ($pipExitCode -eq 0) {
    $pipVersionOutput = (& $pythonPath -m pip --version 2>&1) -join " "
    $pipVersion = ""
    if ($pipVersionOutput -match "pip ([0-9]+\.[0-9]+(\.[0-9]+)?)") {
        $pipVersion = $Matches[1]
    }

    if ($pipOutput -match "Requirement already satisfied") {
        Write-Host " up to date " -NoNewline -ForegroundColor Green
        if ($pipVersion) { Write-Host "(v$pipVersion)" -ForegroundColor Gray } else { Write-Host "" }
    }
    else {
        Write-Host " upgraded " -NoNewline -ForegroundColor Green
        if ($pipVersion) { Write-Host "(v$pipVersion)" -ForegroundColor Gray } else { Write-Host "" }
    }
}
else {
    Write-Host " FAILED (non-critical)" -ForegroundColor Yellow
    Write-Warning "Could not upgrade pip, continuing with existing version..."
}

if (Test-Path "requirements.txt") {
    Write-Host "   Installing core dependencies from " -NoNewline -ForegroundColor White
    Write-Host "requirements.txt" -NoNewline -ForegroundColor Magenta
    Write-Host "..." -ForegroundColor White
    Write-Host ""

    & $pipPath install -r requirements.txt

    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "   Core dependencies installed " -NoNewline -ForegroundColor White
        Write-Host "OK" -ForegroundColor Green
    }
    else {
        Write-Host ""
        Write-Host "   Core dependencies installation " -NoNewline -ForegroundColor White
        Write-Host "FAILED" -ForegroundColor Red
        Write-Error "pip install failed. Check the output above for errors."
        exit 1
    }
}
else {
    Write-Warning "   requirements.txt not found. Skipping core dependencies."
    $script:ErrorCount++
}

# Step 4: Install Collab Package
Write-Host "`n[Step 4/5] Installing collab package..." -ForegroundColor Yellow
Write-Host "   Installing " -NoNewline -ForegroundColor White
Write-Host "collab" -NoNewline -ForegroundColor Magenta
Write-Host " ..." -ForegroundColor White

& $pipPath install .
if ($LASTEXITCODE -eq 0) {
    Write-Host "   collab package installed " -NoNewline -ForegroundColor White
    Write-Host "OK" -ForegroundColor Green
}
else {
    Write-Host "   collab package installation " -NoNewline -ForegroundColor White
    Write-Host "FAILED" -ForegroundColor Red
    Write-Warning "Check the output above for errors."
    $script:ErrorCount++
}

# Step 5: Environment Configuration
Write-Host "`n[Step 5/5] Configuring environment..." -ForegroundColor Yellow
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "   Created " -NoNewline -ForegroundColor White
        Write-Host ".env" -NoNewline -ForegroundColor Magenta
        Write-Host " from " -NoNewline -ForegroundColor White
        Write-Host ".env.example" -NoNewline -ForegroundColor Magenta
        Write-Host " " -NoNewline
        Write-Host "OK" -ForegroundColor Green
    }
    else {
        Write-Warning "   .env.example not found. You will need to create .env manually."
        $script:ErrorCount++
    }
}
else {
    Write-Host "   " -NoNewline
    Write-Host ".env" -NoNewline -ForegroundColor Magenta
    Write-Host " already exists " -NoNewline -ForegroundColor White
    Write-Host "OK" -ForegroundColor Green
}

# Locking Setup Checks (mockCMMS parity)
Write-Host "`n[Locking Setup] Validating collaborative locking prerequisites..." -ForegroundColor Yellow

if (Test-SupabaseImport -PythonExe $pythonPath) {
    Write-Host "   supabase-py import check " -NoNewline -ForegroundColor White
    Write-Host "OK" -ForegroundColor Green
}
else {
    Write-Host "   supabase-py import check " -NoNewline -ForegroundColor White
    Write-Host "WARN" -ForegroundColor Yellow
    Write-Host "   Installing supabase and python-dotenv into .venv..." -ForegroundColor Gray
    & $pythonPath -m pip install supabase python-dotenv --quiet
    if ($LASTEXITCODE -eq 0 -and (Test-SupabaseImport -PythonExe $pythonPath)) {
        Write-Host "   supabase-py installed " -NoNewline -ForegroundColor White
        Write-Host "OK" -ForegroundColor Green
    }
    else {
        Write-Host "   supabase-py installation " -NoNewline -ForegroundColor White
        Write-Host "FAILED" -ForegroundColor Red
        $script:ErrorCount++
    }
}

$envPath = Join-Path $projectRoot ".env"
if (Test-Path $envPath) {
    $envContent = Get-Content $envPath -Raw
    $hasUrl = $envContent -match "(?m)^SUPABASE_URL=(?!\s*$)(?!your)"
    $hasAnon = $envContent -match "(?m)^SUPABASE_ANON_KEY=(?!\s*$)(?!your)"

    if ($hasUrl -and $hasAnon) {
        Write-Host "   Supabase credentials in .env " -NoNewline -ForegroundColor White
        Write-Host "OK" -ForegroundColor Green
    }
    else {
        Write-Host "   Supabase credentials in .env " -NoNewline -ForegroundColor White
        Write-Host "WARN" -ForegroundColor Yellow
        Write-Host "   Set SUPABASE_URL and SUPABASE_ANON_KEY to real values." -ForegroundColor Gray
        $script:ErrorCount++
    }
}

$preCommitExe = ".\.venv\Scripts\pre-commit.exe"
if (Test-Path $preCommitExe) {
    Write-Host "   Installing git hooks via pre-commit..." -ForegroundColor Gray
    $hookTypes = @("pre-commit", "pre-push", "commit-msg")
    $hookInstallFailed = $false
    foreach ($type in $hookTypes) {
        & $preCommitExe install --hook-type $type --overwrite 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $hookInstallFailed = $true
            break
        }
    }

    if (-not $hookInstallFailed) {
        Write-Host "   Git hooks installed " -NoNewline -ForegroundColor White
        Write-Host "OK" -ForegroundColor Green

        $sourceHooksDir = Join-Path $projectRoot "hooks"
        $targetHooksDir = Join-Path $projectRoot ".git\hooks"
        $hookNames = @("pre-commit", "post-commit", "pre-push", "commit-msg")
        $overlayFailed = $false

        if (Test-Path $sourceHooksDir) {
            foreach ($hookName in $hookNames) {
                $src = Join-Path $sourceHooksDir $hookName
                $dst = Join-Path $targetHooksDir $hookName
                if (-not (Test-Path $src)) {
                    $overlayFailed = $true
                    break
                }
                Copy-Item -Path $src -Destination $dst -Force
            }

            if (-not $overlayFailed) {
                Write-Host "   Collab hook overlay installed " -NoNewline -ForegroundColor White
                Write-Host "OK" -ForegroundColor Green
            }
            else {
                Write-Host "   Collab hook overlay " -NoNewline -ForegroundColor White
                Write-Host "WARN" -ForegroundColor Yellow
                $script:ErrorCount++
            }
        }
        else {
            Write-Host "   Collab hook templates missing (hooks/) " -NoNewline -ForegroundColor White
            Write-Host "WARN" -ForegroundColor Yellow
            $script:ErrorCount++
        }
    }
    else {
        Write-Host "   Git hook installation " -NoNewline -ForegroundColor White
        Write-Host "WARN" -ForegroundColor Yellow
        $script:ErrorCount++
    }
}
else {
    Write-Host "   pre-commit not available in .venv " -NoNewline -ForegroundColor White
    Write-Host "SKIPPED" -ForegroundColor Yellow
    Write-Host "   Run scripts/setup-dev.ps1 to install and register repository hooks." -ForegroundColor Gray
}

# Final Summary - Only show if not called from dev script
if (-not $CalledFromDev) {
    Write-Host "`n========================================" -ForegroundColor Cyan
    if ($script:ErrorCount -eq 0) {
        Write-Host "   Installation Complete!" -ForegroundColor Green
    }
    else {
        Write-Host "   Installation completed with $($script:ErrorCount) warning(s)" -ForegroundColor Yellow
    }
    Write-Host "========================================`n" -ForegroundColor Cyan

    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host "                        NEXT STEPS                              " -ForegroundColor Yellow
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  1. Activate the virtual environment:" -ForegroundColor White
    Write-Host "     .\.venv\Scripts\Activate.ps1" -ForegroundColor Magenta
    Write-Host ""
    Write-Host "  2. Run a quick lock check:" -ForegroundColor White
    Write-Host "     python -m src active" -ForegroundColor Magenta
    Write-Host ""
    Write-Host "  3. (Optional) Setup development environment:" -ForegroundColor White
    Write-Host "     .\scripts\setup-dev.ps1" -ForegroundColor Magenta
    Write-Host ""
    Write-Host "  4. Ensure .env includes real Supabase values:" -ForegroundColor White
    Write-Host "     SUPABASE_URL and SUPABASE_ANON_KEY" -ForegroundColor Magenta
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host ""
}
