# setup-dev.ps1 - Development Environment Setup
# Calls setup.ps1 for production setup, then adds dev-specific tools

# Ensure we are in the project root
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Split-Path -Parent $scriptPath
Set-Location $projectRoot

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "   Collab Development Setup" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# Error counter for final summary
$script:ErrorCount = 0

# ============================================================================
# STEP 1: RUN PRODUCTION SETUP
# ============================================================================
Write-Host "========================================" -ForegroundColor Magenta
Write-Host "   PRODUCTION SETUP" -ForegroundColor Magenta
Write-Host "========================================`n" -ForegroundColor Magenta

$setupScript = Join-Path $scriptPath "setup.ps1"
if (-not (Test-Path $setupScript)) {
    Write-Error "setup.ps1 not found at: $setupScript"
    exit 1
}

Write-Host "Running production setup (setup.ps1)...`n" -ForegroundColor Yellow

& $setupScript -CalledFromDev
$productionExitCode = $LASTEXITCODE

if ($productionExitCode -ne 0) {
    Write-Error "`nProduction setup failed. Cannot continue with development setup."
    exit $productionExitCode
}

Write-Host "`n" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "   Production Setup Complete" -ForegroundColor Green
Write-Host "========================================`n" -ForegroundColor Green

# ============================================================================
# STEP 2: DEVELOPMENT TOOLS SETUP
# ============================================================================
Write-Host "========================================" -ForegroundColor Magenta
Write-Host "   DEVELOPMENT TOOLS SETUP" -ForegroundColor Magenta
Write-Host "========================================`n" -ForegroundColor Magenta

function Refresh-EnvPath {
    Write-Host "   Refreshing environment variables..." -ForegroundColor Gray
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# Step 1: Check for Node.js (only dev requirement)
Write-Host "[Dev Step 1/6] Checking Node.js..." -ForegroundColor Yellow

function Check-Node {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        $v = npm --version 2>&1
        if ($v -match "(\d+)\.(\d+)") {
            Write-Host "   Found: " -NoNewline -ForegroundColor White
            Write-Host "npm $v" -NoNewline -ForegroundColor White
            Write-Host " OK" -ForegroundColor Green
            return $true
        }
    }

    $nodeLocations = @(
        "${env:ProgramFiles}\nodejs",
        "${env:ProgramFiles(x86)}\nodejs",
        "${env:LOCALAPPDATA}\Programs\nodejs",
        "C:\Program Files\nodejs",
        "C:\Program Files (x86)\nodejs"
    )

    $nodeFound = $false
    foreach ($location in $nodeLocations) {
        if (Test-Path "$location\node.exe") {
            Write-Host "   Found Node.js at: $location" -ForegroundColor Yellow

            if ($env:Path -notlike "*$location*") {
                $env:Path = "$location;$env:Path"
            }

            try {
                $currentPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
                if ($currentPath -notlike "*$location*") {
                    $newPath = "$location;$currentPath"
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

            $nodeFound = $true
            break
        }
    }

    if ($nodeFound) {
        if (Get-Command npm -ErrorAction SilentlyContinue) {
            $v = npm --version 2>&1
            if ($v -match "(\d+)\.(\d+)") {
                Write-Host "   Version: " -NoNewline -ForegroundColor White
                Write-Host "npm $v" -ForegroundColor Green
                return $true
            }
        }
    }

    return $false
}

if (-not (Check-Node)) {
    Write-Warning "   Node.js/npm not found. Attempting automatic installation via winget..."

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            $nodeIds = @("OpenJS.NodeJS", "OpenJS.NodeJS.LTS")
            $installed = $false

            foreach ($id in $nodeIds) {
                Write-Host "   Trying package ID: $id..." -ForegroundColor Gray
                $startTime = Get-Date
                $process = Start-Process -FilePath "winget" -ArgumentList "install -e --id $id --silent --accept-package-agreements --accept-source-agreements" -NoNewWindow -PassThru -Wait
                $duration = (Get-Date) - $startTime

                if ($process.ExitCode -eq 0) {
                    Write-Host "   Node.js installed successfully (took $([int]$duration.TotalSeconds) seconds)" -ForegroundColor Green
                    $installed = $true
                    break
                }
            }

            if ($installed) {
                Refresh-EnvPath

                if (-not (Check-Node)) {
                    Write-Warning "   Node.js installed but not found in PATH."
                    Write-Host "   Please restart your terminal and run this script again." -ForegroundColor Yellow
                    exit 1
                }
            }
            else {
                Write-Warning "   Automatic installation via winget failed."
                Write-Host ""
                Write-Host "   Please install Node.js manually:" -ForegroundColor Yellow
                Write-Host "   1. Download from: https://nodejs.org/" -ForegroundColor White
                Write-Host "   2. Run installer (npm is included)" -ForegroundColor White
                Write-Host "   3. Restart terminal and run this script again" -ForegroundColor White
                exit 1
            }
        }
        catch {
            Write-Warning "   Automatic installation failed: $_"
            Write-Host "   Please install Node.js manually from https://nodejs.org" -ForegroundColor Yellow
            exit 1
        }
    }
    else {
        Write-Error "   Node.js not found and winget not available."
        Write-Host "   Please install Node.js manually from https://nodejs.org" -ForegroundColor Yellow
        exit 1
    }
}

# Step 2: Check for GitHub CLI
Write-Host "`n[Dev Step 2/6] Checking GitHub CLI..." -ForegroundColor Yellow

function Check-GitHubCLI {
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        $v = gh --version 2>&1 | Select-Object -First 1
        if ($v -match "gh version (\S+)") {
            Write-Host "   Found: " -NoNewline -ForegroundColor White
            Write-Host "gh $($Matches[1])" -NoNewline -ForegroundColor White
            Write-Host " OK" -ForegroundColor Green
            return $true
        }
    }

    $ghPath = "C:\Program Files\GitHub CLI\gh.exe"
    if (Test-Path $ghPath) {
        Write-Host "   Found GitHub CLI at: $ghPath" -ForegroundColor Yellow
        $ghDir = Split-Path -Parent $ghPath
        $env:Path = "$ghDir;$env:Path"

        try {
            $currentPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
            if ($currentPath -notlike "*$ghDir*") {
                $newPath = "$ghDir;$currentPath"
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

        if (Get-Command gh -ErrorAction SilentlyContinue) {
            $v = gh --version 2>&1 | Select-Object -First 1
            if ($v -match "gh version (\S+)") {
                Write-Host "   Version: " -NoNewline -ForegroundColor White
                Write-Host "gh $($Matches[1])" -ForegroundColor Green
                return $true
            }
        }
    }

    return $false
}

if (-not (Check-GitHubCLI)) {
    Write-Warning "   GitHub CLI not found. Attempting automatic installation via winget..."

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            Write-Host "   Trying package ID: GitHub.cli..." -ForegroundColor Gray
            $startTime = Get-Date
            $process = Start-Process -FilePath "winget" -ArgumentList "install -e --id GitHub.cli --silent --accept-package-agreements --accept-source-agreements" -NoNewWindow -PassThru -Wait
            $duration = (Get-Date) - $startTime

            if ($process.ExitCode -eq 0) {
                Write-Host "   GitHub CLI installed successfully (took $([int]$duration.TotalSeconds) seconds)" -ForegroundColor Green
                Refresh-EnvPath

                if (-not (Check-GitHubCLI)) {
                    Write-Warning "   GitHub CLI installed but not found in PATH."
                    Write-Host "   Please restart your terminal to use 'gh' command." -ForegroundColor Yellow
                }
            }
            else {
                Write-Warning "   Automatic installation via winget failed."
                Write-Host "   GitHub CLI is optional but recommended for PR creation." -ForegroundColor Yellow
                Write-Host "   You can install it later from: https://cli.github.com" -ForegroundColor Gray
            }
        }
        catch {
            Write-Warning "   Automatic installation failed: $_"
            Write-Host "   GitHub CLI is optional. Install from: https://cli.github.com" -ForegroundColor Gray
        }
    }
    else {
        Write-Host "   GitHub CLI not found (optional)." -ForegroundColor Gray
        Write-Host "   Install from: https://cli.github.com" -ForegroundColor Gray
    }
}

# Step 3: Python Development Tools
Write-Host "`n[Dev Step 3/6] Installing Python development tools..." -ForegroundColor Yellow

$pipPath = ".\.venv\Scripts\pip.exe"
$pythonPath = ".\.venv\Scripts\python.exe"
$pipPath = $pipPath.Replace('..\', '.\')
$pythonPath = $pythonPath.Replace('..\', '.\')

if (-not (Test-Path $pipPath)) {
    Write-Error "   pip not found at $pipPath"
    Write-Error "   Production setup may have failed. Run setup.ps1 manually."
    exit 1
}

if (Test-Path "requirements-dev.txt") {
    Write-Host "   Ensuring all dev dependencies are installed and up-to-date..." -ForegroundColor White
    & $pipPath install --upgrade --upgrade-strategy only-if-needed -r requirements-dev.txt > $null 2>&1

    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "   Python dev dependencies are present and up-to-date " -NoNewline -ForegroundColor White
        Write-Host "OK" -ForegroundColor Green
    }
    else {
        Write-Host ""
        Write-Host "   Installation " -NoNewline -ForegroundColor White
        Write-Host "FAILED" -ForegroundColor Red
        $script:ErrorCount++
    }
}
else {
    Write-Warning "   requirements-dev.txt not found."
    $script:ErrorCount++
}

# Step 4: JavaScript Development Tools
Write-Host "`n[Dev Step 4/6] Setting up JavaScript development tools..." -ForegroundColor Yellow

if (-not (Test-Path "package.json")) {
    Write-Host "   Initializing " -NoNewline -ForegroundColor White
    Write-Host "package.json" -NoNewline -ForegroundColor Magenta
    Write-Host "..." -NoNewline -ForegroundColor White
    npm init -y 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host " OK" -ForegroundColor Green
    }
    else {
        Write-Host " FAILED" -ForegroundColor Red
        $script:ErrorCount++
    }
}
else {
    Write-Host "   package.json already exists " -NoNewline -ForegroundColor White
    Write-Host "OK" -ForegroundColor Green
}

Write-Host "   Installing " -NoNewline -ForegroundColor White
Write-Host "prettier + prettier-plugin-yaml" -NoNewline -ForegroundColor Magenta
Write-Host "..." -ForegroundColor White

$env:npm_config_loglevel = "error"
npm install --save-dev prettier prettier-plugin-yaml 2>&1 |
Where-Object { $_ -notmatch "^npm warn" -and $_ -notmatch "^npm notice" } |
Out-Null
$env:npm_config_loglevel = $null

if ($LASTEXITCODE -eq 0) {
    Write-Host "   Node formatter packages installed " -NoNewline -ForegroundColor White
    Write-Host "OK" -ForegroundColor Green
}
else {
    Write-Host "   Node package installation " -NoNewline -ForegroundColor White
    Write-Host "FAILED" -ForegroundColor Red
    $script:ErrorCount++
}

# Step 5: Git Template + Pre-commit Hooks
Write-Host "`n[Dev Step 5/6] Setting up Conventional Commit template and hooks..." -ForegroundColor Yellow

$templateFile = Join-Path $projectRoot ".gitmessage"
if (Test-Path $templateFile) {
    git config --local commit.template .gitmessage
    Write-Host "   [OK] .gitmessage set as commit template" -ForegroundColor Green
}
else {
    Write-Host "   [WARN] .gitmessage not found, skipping commit template setup" -ForegroundColor Yellow
}

$preCommitExe = ".\.venv\Scripts\pre-commit.exe"
$hasPreCommit = $false

if (Test-Path $preCommitExe) {
    $hasPreCommit = $true
}
elseif (Get-Command pre-commit -ErrorAction SilentlyContinue) {
    $preCommitExe = "pre-commit"
    $hasPreCommit = $true
}

if ($hasPreCommit) {
    Write-Host "   Using: " -NoNewline -ForegroundColor White
    $preCommitVersion = & $preCommitExe --version 2>&1
    Write-Host "$preCommitVersion " -NoNewline -ForegroundColor White
    Write-Host "OK" -ForegroundColor Green

    Write-Host "   Installing repository hooks (framework mode)..." -ForegroundColor Yellow
    $hookTypes = @("pre-commit", "pre-push", "commit-msg")
    foreach ($type in $hookTypes) {
        Write-Host "     - Installing $type hook..." -ForegroundColor Gray
        & $preCommitExe install --hook-type $type --overwrite 2>&1 | Out-Null
    }

    if ($LASTEXITCODE -eq 0) {
        Write-Host "   Framework hooks installed " -NoNewline -ForegroundColor White
        Write-Host "OK" -ForegroundColor Green
    }
    else {
        Write-Host "   Pre-commit hook install " -NoNewline -ForegroundColor White
        Write-Host "FAILED" -ForegroundColor Red
        $script:ErrorCount++
    }
}
else {
    Write-Host "   Pre-commit not found " -NoNewline -ForegroundColor White
    Write-Host "SKIPPED" -ForegroundColor Yellow
    Write-Host "   (Will be installed via requirements-dev.txt later)" -ForegroundColor Gray
}

# Step 6: Supabase Setup (required for shared locking)
Write-Host "`n[Dev Step 6/6] Configure Supabase locking settings..." -ForegroundColor Yellow

$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path $envFile)) {
    if (Test-Path (Join-Path $projectRoot ".env.example")) {
        Copy-Item (Join-Path $projectRoot ".env.example") $envFile -Force
        Write-Host "   Created .env from .env.example" -ForegroundColor Green
    }
    else {
        Write-Error "   .env and .env.example not found. Cannot configure Supabase."
        $script:ErrorCount++
    }
}

if (Test-Path $envFile) {
    Write-Host "   Supabase configuration is required for live collaborative locks." -ForegroundColor White
    Write-Host "   Ensure these keys are set in .env:" -ForegroundColor Gray
    Write-Host "     - SUPABASE_URL" -ForegroundColor Gray
    Write-Host "     - SUPABASE_ANON_KEY" -ForegroundColor Gray
    Write-Host "     - SUPABASE_SERVICE_ROLE_KEY (optional)" -ForegroundColor Gray

    $envText = Get-Content $envFile -Raw
    $hasUrl = $envText -match "(?m)^SUPABASE_URL="
    $hasAnon = $envText -match "(?m)^SUPABASE_ANON_KEY="

    if ($hasUrl -and $hasAnon) {
        Write-Host "   Supabase key entries present in .env " -NoNewline -ForegroundColor White
        Write-Host "OK" -ForegroundColor Green
    }
    else {
        Write-Host "   Missing required Supabase entries in .env " -NoNewline -ForegroundColor White
        Write-Host "WARN" -ForegroundColor Yellow
        $script:ErrorCount++
    }
}

# IDE Auto-Detection & Configuration
Write-Host "`n   Detecting IDE environment..." -ForegroundColor Yellow

$detectedIDE = $null
if ($env:TERM_PROGRAM -eq "vscode") {
    $detectedIDE = "vscode"
}
elseif ($env:TERMINAL_EMULATOR -like "*JetBrains*") {
    $detectedIDE = "jetbrains"
}
elseif (Test-Path (Join-Path $projectRoot ".vscode")) {
    $detectedIDE = "vscode"
}
elseif (Test-Path (Join-Path $projectRoot ".idea")) {
    $detectedIDE = "jetbrains"
}

switch ($detectedIDE) {
    "vscode" {
        Write-Host "     - VS Code detected" -ForegroundColor Gray
        $vscodeExtDir = Join-Path $projectRoot "vscode-extension\collab-locks"
        $packageJson = Join-Path $vscodeExtDir "package.json"
        if (Test-Path $packageJson) {
            try {
                Push-Location $vscodeExtDir
                npm install --silent 2>$null
                Pop-Location
                Write-Host "     - VS Code extension dependencies installed " -NoNewline -ForegroundColor White
                Write-Host "OK" -ForegroundColor Green
            }
            catch {
                Pop-Location
                Write-Host "     - VS Code extension npm install failed (non-fatal)" -ForegroundColor Yellow
            }
        }
    }
    "jetbrains" {
        Write-Host "     - PyCharm/IntelliJ detected" -ForegroundColor Gray
        $ideaRunConfigDir = Join-Path $projectRoot ".idea\runConfigurations"
        $xmlSrc = Join-Path $projectRoot "pycharm\Collab_Lock_Watcher.xml"
        if (Test-Path $xmlSrc) {
            try {
                New-Item -ItemType Directory -Force -Path $ideaRunConfigDir | Out-Null
                Copy-Item -Path $xmlSrc -Destination (Join-Path $ideaRunConfigDir "Collab_Lock_Watcher.xml") -Force
                Write-Host "     - PyCharm run configuration installed " -NoNewline -ForegroundColor White
                Write-Host "OK" -ForegroundColor Green
                Write-Host "     - Open Run > Collab Lock Watcher to start the watcher." -ForegroundColor Gray
            }
            catch {
                Write-Host "     - PyCharm run config install failed (non-fatal)" -ForegroundColor Yellow
            }
        }
    }
    default {
        Write-Host "     - No IDE detected" -ForegroundColor Gray
    }
}

# Final Summary
Write-Host "`n========================================" -ForegroundColor Cyan
if ($script:ErrorCount -eq 0) {
    Write-Host "   Development Setup Complete!" -ForegroundColor Green
    Write-Host "   (Production + Dev Tools)" -ForegroundColor Gray
}
else {
    Write-Host "   Setup completed with $($script:ErrorCount) warning(s)" -ForegroundColor Yellow
}
Write-Host "========================================`n" -ForegroundColor Cyan

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "                        NEXT STEPS                              " -ForegroundColor Yellow
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
switch ($detectedIDE) {
    "vscode" {
        Write-Host "  1. Install the Collab Locks extension in VS Code:" -ForegroundColor White
        Write-Host "     Press F1 > 'Developer: Install Extension from Location...'" -ForegroundColor Magenta
        Write-Host "     Select vscode-extension\collab-locks\ and reload VS Code." -ForegroundColor Magenta
        Write-Host "     Extension activation will start daemon automatically." -ForegroundColor Gray
    }
    default {
        Write-Host "  1. Start watcher manually if needed:" -ForegroundColor White
        Write-Host "     python -m src.main daemon-start" -ForegroundColor Magenta
    }
}
Write-Host ""
Write-Host "  2. Activate the virtual environment (if not already active):" -ForegroundColor White
Write-Host "     .\.venv\Scripts\Activate.ps1" -ForegroundColor Magenta
Write-Host ""
Write-Host "  3. Run quality checks:" -ForegroundColor White
Write-Host "     python scripts\format_code.py" -ForegroundColor Magenta
Write-Host "     python scripts\validate_code.py --quick" -ForegroundColor Magenta
Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
