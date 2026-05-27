# setup-dev.ps1 - Development Environment Setup
# Calls setup.ps1 for production setup, then adds dev-specific tools
# Usage: .\scripts\setup-dev.ps1 [-Force]

param(
    [switch]$Force = $false
)

# Ensure we are in the project root
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Split-Path -Parent $scriptPath
Set-Location $projectRoot

$script:IsWin = ($PSVersionTable.Platform -eq "Win32NT") -or ($env:OS -match "Windows")

$setupPs1Path = Join-Path $scriptPath 'setup.ps1'
$setupHelperNames = @(
    'Enable-SetupWindowsVirtualTerminal',
    'Set-SetupConsoleUtf8Encoding',
    'Initialize-SetupConsole',
    'Get-SetupStatusToken',
    'Write-SetupEmit',
    'Write-SetupStepHeader',
    'Write-SetupDevStepHeader',
    'Write-SetupBannerLine',
    'Write-SetupRedirectHint'
)
$parseErrors = $null
$setupAst = [System.Management.Automation.Language.Parser]::ParseFile(
    $setupPs1Path,
    [ref]$null,
    [ref]$parseErrors
)
if ($parseErrors) {
    Write-Error "Failed to parse setup.ps1 for console helpers: $($parseErrors -join '; ')"
    exit 1
}
foreach ($statement in $setupAst.EndBlock.Statements) {
    if ($statement -is [System.Management.Automation.Language.FunctionDefinitionAst]) {
        if ($setupHelperNames -contains $statement.Name) {
            . ([scriptblock]::Create($statement.Extent.Text))
        }
    }
}
$script:SetupStepTotal = 10
Initialize-SetupConsole
Write-SetupRedirectHint

Write-SetupBannerLine "`n========================================" -Color Cyan
Write-SetupBannerLine "   Collab Development Setup" -Color Cyan
Write-SetupBannerLine "========================================`n" -Color Cyan

# Error counter for final summary
$script:ErrorCount = 0

# ============================================================================
# STEP 1: RUN PRODUCTION SETUP
# ============================================================================
Write-SetupBannerLine "========================================" -Color Magenta
Write-SetupBannerLine "   PRODUCTION SETUP" -Color Magenta
Write-SetupBannerLine "========================================`n" -Color Magenta

$setupScript = $setupPs1Path
if (-not (Test-Path $setupScript)) {
    Write-Error "setup.ps1 not found at: $setupScript"
    exit 1
}

Write-SetupEmit "Running production setup (setup.ps1)...`n" -Color Yellow

& $setupScript -CalledFromDev -Force:$Force
$productionExitCode = $LASTEXITCODE

if ($productionExitCode -ne 0) {
    Write-Error "`nProduction setup failed. Cannot continue with development setup."
    exit $productionExitCode
}

Write-SetupEmit "`n" -Color Green
Write-SetupBannerLine "========================================" -Color Green
Write-SetupBannerLine "   Production Setup Complete" -Color Green
Write-SetupBannerLine "========================================`n" -Color Green

# ============================================================================
# STEP 2: DEVELOPMENT TOOLS SETUP
# ============================================================================
Write-SetupBannerLine "========================================" -Color Magenta
Write-SetupBannerLine "   DEVELOPMENT TOOLS SETUP" -Color Magenta
Write-SetupBannerLine "========================================`n" -Color Magenta

function Refresh-EnvPath {
    Write-Host "   Refreshing environment variables..." -ForegroundColor Gray
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# Step 1: Check for Node.js (only dev requirement)
Write-SetupDevStepHeader -Step 1 -Message 'Checking Node.js...'

function Check-Node {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        $v = npm --version 2>&1
        if ($v -match "(\d+)\.(\d+)") {
            Write-Host "   Found: " -NoNewline -ForegroundColor White
            Write-Host "npm $v" -NoNewline -ForegroundColor White
            Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
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
                    Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
                }
                else {
                    Write-Host "   Already in system PATH " -NoNewline -ForegroundColor White
                    Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
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
Write-SetupDevStepHeader -Step 2 -Message 'Checking GitHub CLI...'

function Check-GitHubCLI {
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        $v = gh --version 2>&1 | Select-Object -First 1
        if ($v -match "gh version (\S+)") {
            Write-Host "   Found: " -NoNewline -ForegroundColor White
            Write-Host "gh $($Matches[1])" -NoNewline -ForegroundColor White
            Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
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
                Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
            }
            else {
                Write-Host "   Already in system PATH " -NoNewline -ForegroundColor White
                Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
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
Write-SetupDevStepHeader -Step 3 -Message 'Installing Python development tools...'

$venvBin = if ($script:IsWin -or (Test-Path (Join-Path $projectRoot ".venv\Scripts"))) { "Scripts" } else { "bin" }
$pythonExe = if ($script:IsWin) { "python.exe" } else { "python" }
$pipExe = if ($script:IsWin) { "pip.exe" } else { "pip" }

$pipPath = Join-Path $projectRoot ".venv\$venvBin\$pipExe"
$pythonPath = Join-Path $projectRoot ".venv\$venvBin\$pythonExe"

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
        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
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
Write-SetupDevStepHeader -Step 4 -Message 'Setting up JavaScript development tools...'

if (-not (Test-Path "package.json")) {
    Write-Host "   Initializing " -NoNewline -ForegroundColor White
    Write-Host "package.json" -NoNewline -ForegroundColor Magenta
    Write-Host "..." -NoNewline -ForegroundColor White
    npm init -y 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
    }
    else {
        Write-Host " FAILED" -ForegroundColor Red
        $script:ErrorCount++
    }
}
else {
    Write-Host "   package.json already exists " -NoNewline -ForegroundColor White
    Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
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
    Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
}
else {
    Write-Host "   Node package installation " -NoNewline -ForegroundColor White
    Write-Host "FAILED" -ForegroundColor Red
    $script:ErrorCount++
}

# Step 5: Git Template + Pre-commit Hooks
Write-SetupDevStepHeader -Step 5 -Message 'Setting up Conventional Commit template and hooks...'

$templateFile = Join-Path $projectRoot ".gitmessage"
if (Test-Path $templateFile) {
    git config --local commit.template .gitmessage
    Write-Host "   [OK] .gitmessage set as commit template" -ForegroundColor Green
}
else {
    Write-Host "   [WARN] .gitmessage not found, skipping commit template setup" -ForegroundColor Yellow
}

$preCommitExeName = if ($script:IsWin) { "pre-commit.exe" } else { "pre-commit" }
$preCommitExe = Join-Path $projectRoot ".venv\$venvBin\$preCommitExeName"
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
    Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green

    Write-Host "   Installing repository hooks (framework mode)..." -ForegroundColor Yellow
    $hookTypes = @("pre-commit", "pre-push", "commit-msg")
    foreach ($type in $hookTypes) {
        Write-Host "     - Installing $type hook..." -ForegroundColor Gray
        & $preCommitExe install --hook-type $type --overwrite 2>&1 | Out-Null
    }

    if ($LASTEXITCODE -eq 0) {
        Write-Host "   Framework hooks installed " -NoNewline -ForegroundColor White
        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
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
Write-SetupDevStepHeader -Step 6 -Message 'Configure Supabase locking settings...'

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
        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
    }
    else {
        Write-Host "   Missing required Supabase entries in .env " -NoNewline -ForegroundColor White
        Write-Host "WARN" -ForegroundColor Yellow
        $script:ErrorCount++
    }
}

# IDE Auto-Detection & Configuration
# VS Code / Cursor env + process tree, GitHub API User-Agent,
# single extension install target (never Microsoft ``code`` into Cursor when Cursor host is
# detected but only the wrong CLI is on PATH).

function Test-SetupDevAncestorProcessMatch {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$NameWildcards
    )
    try {
        $current = Get-CimInstance -ClassName Win32_Process `
            -Filter "ProcessId=$PID" `
            -ErrorAction Stop
        $guard = 0
        while ($null -ne $current -and $guard -lt 20) {
            $procName = $current.Name
            foreach ($pattern in $NameWildcards) {
                if ($procName -like $pattern) {
                    return $true
                }
            }
            $parentPid = [int]$current.ParentProcessId
            if ($parentPid -le 0) {
                break
            }
            $current = Get-CimInstance -ClassName Win32_Process `
                -Filter "ProcessId=$parentPid" `
                -ErrorAction SilentlyContinue
            $guard++
        }
    }
    catch {
        return $false
    }
    return $false
}

function Test-SetupDevCursorHost {
    if ($null -ne $env:CURSOR_TRACE_ID -and $env:CURSOR_TRACE_ID -ne '') {
        return $true
    }
    if ($null -ne $env:CURSOR_AGENT -and $env:CURSOR_AGENT -ne '') {
        return $true
    }
    return (Test-SetupDevAncestorProcessMatch -NameWildcards @('Cursor*'))
}

function Test-SetupDevCliPathIsUnderCursorInstall {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CliPath
    )
    if ([string]::IsNullOrWhiteSpace($CliPath)) {
        return $false
    }
    return $CliPath -match '(?i)[\\/]Programs[\\/]cursor[\\/]'
}

function Get-SetupDevCursorInstallRootFromProcess {
    try {
        $current = Get-CimInstance -ClassName Win32_Process `
            -Filter "ProcessId=$PID" `
            -ErrorAction Stop
        $guard = 0
        while ($null -ne $current -and $guard -lt 25) {
            if ($current.Name -like 'Cursor*') {
                $exePath = $current.ExecutablePath
                if (-not [string]::IsNullOrWhiteSpace($exePath)) {
                    return (Split-Path -Parent $exePath)
                }
            }
            $parentPid = [int]$current.ParentProcessId
            if ($parentPid -le 0) {
                break
            }
            $current = Get-CimInstance -ClassName Win32_Process `
                -Filter "ProcessId=$parentPid" `
                -ErrorAction SilentlyContinue
            $guard++
        }
    }
    catch {
        return $null
    }
    return $null
}

function Resolve-SetupDevCursorCliPath {
    $fromPath = Get-Command cursor -ErrorAction SilentlyContinue
    if ($fromPath) {
        return $fromPath.Source
    }
    $relCandidates = @(
        'resources\app\bin\cursor.cmd',
        'resources\app\bin\cursor.exe',
        'bin\cursor.cmd'
    )
    foreach ($rootName in @('cursor', 'Cursor')) {
        $root = Join-Path $env:LOCALAPPDATA "Programs\$rootName"
        foreach ($rel in $relCandidates) {
            $p = Join-Path $root $rel
            if (Test-Path -LiteralPath $p) {
                return $p
            }
        }
    }
    $procRoot = Get-SetupDevCursorInstallRootFromProcess
    if ($null -ne $procRoot) {
        foreach ($rel in $relCandidates) {
            $p = Join-Path $procRoot $rel
            if (Test-Path -LiteralPath $p) {
                return $p
            }
        }
    }
    return $null
}

function Resolve-SetupDevCursorBundleCodeShimPath {
    foreach ($rootName in @('cursor', 'Cursor')) {
        $shim = Join-Path $env:LOCALAPPDATA "Programs\$rootName\resources\app\bin\code.cmd"
        if (Test-Path -LiteralPath $shim) {
            return $shim
        }
    }
    $fromPath = Get-Command code -ErrorAction SilentlyContinue
    if ($fromPath -and (Test-SetupDevCliPathIsUnderCursorInstall -CliPath $fromPath.Source)) {
        return $fromPath.Source
    }
    return $null
}

function Resolve-SetupDevMicrosoftVsCodeCliPath {
    $official = Join-Path $env:LOCALAPPDATA 'Programs\Microsoft VS Code\bin\code.cmd'
    if (Test-Path -LiteralPath $official) {
        return $official
    }
    return $null
}

function Resolve-SetupDevVsCodeInstallCliPath {
    $fromPath = Get-Command code -ErrorAction SilentlyContinue
    if ($fromPath) {
        if (-not (Test-SetupDevCliPathIsUnderCursorInstall -CliPath $fromPath.Source)) {
            return $fromPath.Source
        }
    }
    return (Resolve-SetupDevMicrosoftVsCodeCliPath)
}

function Get-SetupDevEditorInstallCli {
    $inCursor = Test-SetupDevCursorHost
    $cursorCli = Resolve-SetupDevCursorCliPath
    $cursorCodeShim = Resolve-SetupDevCursorBundleCodeShimPath
    $vsCodeCli = Resolve-SetupDevVsCodeInstallCliPath

    if ($inCursor) {
        if ($null -ne $cursorCli) {
            return [PSCustomObject][ordered]@{
                Exe          = $cursorCli
                DisplayLabel = 'Cursor'
                SkipInstall  = $false
                SkipReason   = $null
            }
        }
        if ($null -ne $cursorCodeShim) {
            return [PSCustomObject][ordered]@{
                Exe          = $cursorCodeShim
                DisplayLabel = 'Cursor (code shim)'
                SkipInstall  = $false
                SkipReason   = $null
            }
        }
        if ($null -ne $vsCodeCli) {
            return [PSCustomObject][ordered]@{
                Exe          = $null
                DisplayLabel = $null
                SkipInstall  = $true
                SkipReason   = 'Cursor host detected but Cursor install path could not be found; refusing Microsoft VS Code code.cmd so the extension is not installed into the wrong app.'
            }
        }
        return [PSCustomObject][ordered]@{
            Exe          = $null
            DisplayLabel = $null
            SkipInstall  = $true
            SkipReason   = 'Could not locate Cursor or VS Code CLI for extension install.'
        }
    }

    if ($null -ne $vsCodeCli) {
        return [PSCustomObject][ordered]@{
            Exe          = $vsCodeCli
            DisplayLabel = 'VS Code'
            SkipInstall  = $false
            SkipReason   = $null
        }
    }
    if ($null -ne $cursorCli) {
        return [PSCustomObject][ordered]@{
            Exe          = $cursorCli
            DisplayLabel = 'Cursor'
            SkipInstall  = $false
            SkipReason   = $null
        }
    }
    $anyCode = Get-Command code -ErrorAction SilentlyContinue
    if ($anyCode) {
        return [PSCustomObject][ordered]@{
            Exe          = $anyCode.Source
            DisplayLabel = 'code (PATH)'
            SkipInstall  = $false
            SkipReason   = $null
        }
    }
    return [PSCustomObject][ordered]@{
        Exe          = $null
        DisplayLabel = $null
        SkipInstall  = $true
        SkipReason   = 'Neither Cursor nor VS Code CLI could be resolved; skipping collab extension auto-install.'
    }
}

function Get-SetupDevProcessAncestorExeLeafs {
    if (-not $script:IsWin) {
        return @()
    }

    $leaves = [System.Collections.Generic.List[string]]::new()
    $currentPid = $PID
    $guard = 0
    while ($guard++ -lt 30) {
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$currentPid" -ErrorAction SilentlyContinue
        if (-not $cim) {
            break
        }

        $leaf = [System.IO.Path]::GetFileNameWithoutExtension([string]$cim.Name)
        if ([string]::IsNullOrWhiteSpace($leaf)) {
            break
        }

        [void]$leaves.Add($leaf)
        $ppid = [int]$cim.ParentProcessId
        if ($ppid -le 0 -or $ppid -eq $currentPid) {
            break
        }

        $currentPid = $ppid
    }

    return , $leaves.ToArray()
}

function Get-SetupDevDetectedIdeKind {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    foreach ($envKey in @(
            'VSCODE_PID', 'VSCODE_CWD', 'VSCODE_IPC_HOOK', 'VSCODE_IPC_HOOK_CLI',
            'VSCODE_CRASH_REPORTER_PROCESS_TYPE'
        )) {
        $val = [Environment]::GetEnvironmentVariable($envKey)
        if (-not [string]::IsNullOrWhiteSpace($val)) {
            return 'vscode_family'
        }
    }

    if (Test-SetupDevAncestorProcessMatch -NameWildcards @('Cursor*', 'Code.exe', 'code.exe')) {
        return 'vscode_family'
    }

    $ancestorLeaves = Get-SetupDevProcessAncestorExeLeafs

    $vscodeProcs = @('Cursor', 'Code', 'Code - Insiders', 'Antigravity', 'VSCodium', 'codium')
    foreach ($v in $vscodeProcs) {
        if ($ancestorLeaves -contains $v) {
            return 'vscode_family'
        }
    }

    $jetbrainsProcs = @(
        'idea64', 'PyCharm64', 'pycharm64', 'Rider64', 'rider64', 'WebStorm64', 'PhpStorm64',
        'CLion64', 'GoLand64', 'RubyMine64', 'devenv'
    )

    foreach ($j in $jetbrainsProcs) {
        if ($ancestorLeaves -contains $j) {
            return 'jetbrains'
        }
    }

    if ($env:TERMINAL_EMULATOR -like '*JetBrains*') {
        return 'jetbrains'
    }

    if ($env:TERM_PROGRAM -in @('vscode', 'Antigravity', 'cursor', 'Cursor')) {
        return 'vscode_family'
    }

    if ($env:CURSOR_TRACE_ID -or $env:CURSOR_AGENT) {
        return 'vscode_family'
    }

    if ((Test-Path (Join-Path $ProjectRoot '.vscode')) -or (Test-Path (Join-Path $ProjectRoot '.cursor'))) {
        return 'vscode_family'
    }

    if (Test-Path (Join-Path $ProjectRoot '.idea')) {
        return 'jetbrains'
    }

    return 'unknown'
}

function Invoke-SetupDevCollabLocksVsixInstall {
    [CmdletBinding()]
    param()

    $ghHeaders = @{ 'User-Agent' = 'collab-setup-dev' }
    $editorCli = Get-SetupDevEditorInstallCli
    if ($editorCli.SkipInstall) {
        Write-Host "     - $($editorCli.SkipReason)" -ForegroundColor Yellow
        Write-Host '     - Manual: download .vsix from https://github.com/KirilMT/collab/releases/latest' -ForegroundColor Gray
        Write-Host '       then: cursor --install-extension <path-to.vsix>   (Cursor)' -ForegroundColor Gray
        Write-Host '       or:  code --install-extension <path-to.vsix>     (VS Code)' -ForegroundColor Gray
        return
    }

    Write-Host "     - Collab extension installer: $($editorCli.DisplayLabel)" -ForegroundColor Gray
    Write-Host "       $($editorCli.Exe)" -ForegroundColor DarkGray
    Write-Host '     - Fetching latest Collab Locks .vsix from GitHub Releases...' -ForegroundColor Gray

    $tempVsix = Join-Path ([System.IO.Path]::GetTempPath()) 'collab-locks-latest.vsix'
    try {
        $releaseUrl = 'https://api.github.com/repos/KirilMT/collab/releases/latest'
        $releaseInfo = Invoke-RestMethod -Uri $releaseUrl -Headers $ghHeaders -ErrorAction Stop
        $vsixAsset = $releaseInfo.assets | Where-Object { $_.name -match '\.vsix$' } | Select-Object -First 1
        if (-not $vsixAsset) {
            Write-Host '     - No .vsix asset on latest GitHub release (non-fatal)' -ForegroundColor Yellow
            return
        }

        Invoke-WebRequest -Uri $vsixAsset.browser_download_url -OutFile $tempVsix -Headers $ghHeaders -ErrorAction Stop
        $installExe = $editorCli.Exe
        $installOutput = & $installExe --install-extension $tempVsix --force 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            Write-Host "     - $installExe --install-extension failed (non-fatal):" -ForegroundColor Yellow
            Write-Host "       $installOutput" -ForegroundColor Gray
        }
        else {
            Write-Host '     - Installed extension ' -NoNewline -ForegroundColor White
            Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
            Write-Host "       ($($vsixAsset.name) -> $($editorCli.DisplayLabel))" -ForegroundColor Gray
        }
    }
    catch {
        Write-Host "     - VSIX download/install failed (non-fatal): $($_.Exception.Message)" -ForegroundColor Yellow
    }
    finally {
        Remove-Item -LiteralPath $tempVsix -ErrorAction SilentlyContinue
    }
}

Write-Host "`n   Detecting IDE environment..." -ForegroundColor Yellow

$detectedIDE = Get-SetupDevDetectedIdeKind -ProjectRoot $projectRoot
Write-Host "     - IDE kind: $detectedIDE" -ForegroundColor Gray

switch ($detectedIDE) {
    'vscode_family' {
        $ancestorLeaves = Get-SetupDevProcessAncestorExeLeafs
        if (($ancestorLeaves -contains 'Cursor') -or (Test-SetupDevCursorHost)) {
            Write-Host '     - Cursor / VS Code-compatible IDE detected' -ForegroundColor Gray
        }
        else {
            Write-Host '     - VS Code-compatible IDE detected' -ForegroundColor Gray
        }

        Invoke-SetupDevCollabLocksVsixInstall

        $vscodeExtDir = Join-Path $projectRoot 'vscode-extension\collab-locks'
        $packageJson = Join-Path $vscodeExtDir 'package.json'
        if (Test-Path $packageJson) {
            try {
                Push-Location $vscodeExtDir
                npm install --silent 2>$null
                Pop-Location
                Write-Host '     - VS Code extension workspace deps (npm) ' -NoNewline -ForegroundColor White
                Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
            }
            catch {
                Pop-Location
                Write-Host '     - VS Code extension npm install failed (non-fatal)' -ForegroundColor Yellow
            }
        }
    }
    'jetbrains' {
        Write-Host '     - JetBrains IDE detected' -ForegroundColor Gray
        $ideaRunConfigDir = Join-Path $projectRoot '.idea\runConfigurations'
        $xmlSrc = Join-Path $projectRoot 'pycharm\Collab_Lock_Watcher.xml'
        if (Test-Path $xmlSrc) {
            try {
                New-Item -ItemType Directory -Force -Path $ideaRunConfigDir -ErrorAction SilentlyContinue | Out-Null
                Copy-Item -Path $xmlSrc -Destination (Join-Path $ideaRunConfigDir 'Collab_Lock_Watcher.xml') -Force
                Write-Host '     - PyCharm run configuration installed ' -NoNewline -ForegroundColor White
                Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
                Write-Host '     - Open Run > Collab Lock Watcher to start the watcher in PyCharm.' -ForegroundColor Gray
            }
            catch {
                Write-Host '     - PyCharm run config install failed (non-fatal)' -ForegroundColor Yellow
            }
        }
    }
    default {
        Write-Host '     - No specific IDE detected from env / process / workspace hints' -ForegroundColor Gray
        Write-Host '     - Attempting Collab Locks VSIX install with resolved editor CLI...' -ForegroundColor Gray
        Invoke-SetupDevCollabLocksVsixInstall
    }
}

# Final Summary
Write-SetupBannerLine "`n========================================" -Color Cyan
if ($script:ErrorCount -eq 0) {
    Write-SetupBannerLine "   Development Setup Complete!" -Color Green
    Write-SetupEmit "   (Production + Dev Tools + Daemon Active)" -Color Gray
}
else {
    Write-SetupEmit "   Setup completed with $($script:ErrorCount) warning(s)" -Color Yellow
}
Write-SetupBannerLine "========================================`n" -Color Cyan

Write-Host ""
Write-SetupBannerLine "================================================================" -Color Cyan
Write-SetupEmit "                        NEXT STEPS                              " -Color Yellow
Write-SetupBannerLine "================================================================" -Color Cyan
Write-Host ""
switch ($detectedIDE) {
    'vscode_family' {
        Write-Host "  1. Collab Locks VSIX and workspace extension deps were applied when possible." -ForegroundColor White
        Write-Host "     Press " -NoNewline -ForegroundColor Gray
        Write-Host "F1 > 'Developer: Reload Window'" -NoNewline -ForegroundColor Magenta
        Write-Host " if locks don't appear." -ForegroundColor Gray
    }
    default {
        Write-Host "  1. Collaborative daemon should be active (Core Step 10)." -ForegroundColor White
        Write-Host "     Use " -NoNewline -ForegroundColor Gray
        Write-Host "'collab active'" -NoNewline -ForegroundColor Magenta
        Write-Host " to verify." -ForegroundColor Gray
    }
}
Write-Host ""
Write-Host "  2. Activate the virtual environment (if not already active):" -ForegroundColor White
Write-Host "     .\.venv\Scripts\Activate.ps1" -ForegroundColor Magenta
Write-Host "     Agent shells often skip activation; use .\.venv\Scripts\python.exe when PATH is wrong." -ForegroundColor Gray
Write-Host ""
Write-Host "  3. Run quality checks:" -ForegroundColor White
Write-Host "     python scripts\format_code.py" -ForegroundColor Magenta
Write-Host "     python scripts\validate_code.py --quick" -ForegroundColor Magenta
Write-Host ""
Write-SetupBannerLine "================================================================" -Color Cyan
Write-Host ""
