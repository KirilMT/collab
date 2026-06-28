# setup.ps1 - Enhanced Collab Installation Script
# Provides detailed feedback and error handling for Windows environments
# Supports non-interactive mode for automation and CI provisioning

# Accept parameters
param(
    [switch]$CalledFromDev = $false,
    [switch]$NonInteractive = $false,
    [switch]$Force = $false
)

# Ensure we are in the project root
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Split-Path -Parent $scriptPath
Set-Location $projectRoot

$script:IsWin = ($PSVersionTable.Platform -eq "Win32NT") -or ($env:OS -match "Windows")
$script:SetupStepTotal = 10

function Enable-SetupWindowsVirtualTerminal {
    if (-not ($script:IsWin)) {
        return $true
    }

    try {
        if (-not ([System.Management.Automation.PSTypeName]'SetupConsoleApi').Type) {
            Add-Type -Namespace SetupConsoleApi -Name NativeMethods -MemberDefinition @'
[DllImport("kernel32.dll", SetLastError = true)]
public static extern IntPtr GetStdHandle(int nStdHandle);
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool GetConsoleMode(IntPtr hConsoleHandle, out uint lpMode);
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool SetConsoleMode(IntPtr hConsoleHandle, uint dwMode);
'@ -ErrorAction Stop
        }
        $handle = [SetupConsoleApi.NativeMethods]::GetStdHandle(-11)
        $mode = [uint32]0
        if (-not [SetupConsoleApi.NativeMethods]::GetConsoleMode($handle, [ref]$mode)) {
            return $false
        }
        $vt = [uint32]0x0004
        if (($mode -band $vt) -ne 0) {
            return $true
        }
        return [SetupConsoleApi.NativeMethods]::SetConsoleMode($handle, $mode -bor $vt)
    }
    catch {
        return $false
    }
}

function Set-SetupConsoleUtf8Encoding {
    if ([Console]::IsOutputRedirected) {
        return $false
    }

    if ($script:IsWin) {
        try {
            $null = & cmd.exe /c 'chcp 65001 >nul'
        }
        catch {
            # Non-fatal; emoji may fall back to ASCII tokens.
        }
    }

    try {
        $utf8 = [System.Text.UTF8Encoding]::new($false)
        [Console]::OutputEncoding = $utf8
        [Console]::InputEncoding = $utf8
        $OutputEncoding = $utf8
    }
    catch {
        return $false
    }

    try {
        $codePage = [Console]::OutputEncoding.CodePage
        return ($codePage -eq 65001)
    }
    catch {
        return $false
    }
}

function Initialize-SetupConsole {
    $script:SetupUseAnsi = $false
    $script:SetupUseEmoji = $false
    $script:SetupAnsiCodes = @{
        White   = '37'
        Green   = '32'
        Yellow  = '33'
        Red     = '31'
        Cyan    = '36'
        Magenta = '35'
        Gray    = '90'
    }

    if ([Console]::IsOutputRedirected) {
        return
    }

    $utf8Ready = Set-SetupConsoleUtf8Encoding

    try {
        if ($PSStyle.OutputRendering) {
            $PSStyle.OutputRendering = 'Ansi'
        }
    }
    catch {
        # Older hosts: rely on explicit ANSI or Write-Host.
    }

    if ($script:IsWin) {
        $script:SetupUseAnsi = Enable-SetupWindowsVirtualTerminal
    }
    else {
        $script:SetupUseAnsi = $true
    }

    $script:SetupUseEmoji = $script:SetupUseAnsi -and $utf8Ready -and -not $env:CI -and -not $env:TF_BUILD
}

function Get-SetupStatusToken {
    param(
        [ValidateSet('OK', 'WARN', 'FAILED', 'SKIP')]
        [string]$Status
    )

    if ($script:SetupUseEmoji) {
        switch ($Status) {
            'OK' { return ' ✅' }
            'WARN' { return ' ⚠️' }
            'FAILED' { return ' ❌' }
            'SKIP' { return ' ⏭️' }
        }
    }

    switch ($Status) {
        'OK' { return ' OK' }
        'WARN' { return ' WARN' }
        'FAILED' { return ' FAILED' }
        'SKIP' { return ' SKIPPED' }
    }
}

function Write-SetupEmit {
    param(
        [string]$Text,
        [string]$Color = 'White',
        [switch]$NoNewline
    )

    if ($script:SetupUseAnsi -and $script:SetupAnsiCodes.ContainsKey($Color)) {
        $esc = [char]27
        $code = $script:SetupAnsiCodes[$Color]
        $styled = '{0}[{1}m{2}{0}[0m' -f $esc, $code, $Text
        if ($NoNewline) {
            [Console]::Out.Write($styled)
        }
        else {
            [Console]::Out.WriteLine($styled)
        }
        return
    }

    $hostParams = @{ Object = $Text }
    if ($NoNewline) {
        $hostParams['NoNewline'] = $true
    }
    if ($Color -and $Color -ne 'Default') {
        $hostParams['ForegroundColor'] = $Color
    }
    Write-Host @hostParams
}

function Write-SetupStepHeader {
    param(
        [int]$Step,
        [string]$Message,
        [int]$Total = 0
    )

    if ($Total -le 0) {
        $Total = $script:SetupStepTotal
    }

    Write-SetupEmit "`n[Step $Step/$Total] $Message" -Color Yellow
}

function Write-SetupDevStepHeader {
    param(
        [int]$Step,
        [string]$Message,
        [int]$Total = 6
    )

    Write-SetupEmit "`n[Dev Step $Step/$Total] $Message" -Color Yellow
}

function Write-SetupBannerLine {
    param([string]$Text, [string]$Color = 'Cyan')

    Write-SetupEmit $Text -Color $Color
}

function Write-SetupRedirectHint {
    if (-not [Console]::IsOutputRedirected) {
        return
    }

    Write-Host ""
    Write-Host "   [setup] Output is redirected (Code Runner / CI). Colors and emoji are disabled." -ForegroundColor Yellow
    Write-Host "   [setup] For full output, run in the integrated terminal:" -ForegroundColor Yellow
    Write-Host "           .\scripts\setup-dev.ps1" -ForegroundColor White
    Write-Host ""
}

function Test-IsPlaceholderValue {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }

    # Standard placeholder patterns that indicate an unconfigured value.
    # Pre-filled team values (like a real Supabase URL) do NOT match these.
    $placeholderPatterns = @(
        '^your[_-]',
        '^your[_-]?project',
        '^example',
        '^CHANGE_ME',
        '^change[_-]?me',
        '^<team-',          # angle-bracket template placeholders
        '^replace[_-]?me',
        '^TODO'
    )

    foreach ($pattern in $placeholderPatterns) {
        if ($Value -match $pattern) {
            return $true
        }
    }

    return $false
}

Initialize-SetupConsole
Write-SetupRedirectHint

# Only show header if not called from dev script
if (-not $CalledFromDev) {
    Write-SetupBannerLine "`n========================================" -Color Cyan
    Write-SetupBannerLine "   Collab Installation Script" -Color Cyan
    Write-SetupBannerLine "========================================`n" -Color Cyan
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

function Get-SetupCollabSitePackagesDir {
    param([string]$PythonExe)

    $venvScriptsDir = Split-Path -Parent (Resolve-Path $PythonExe).Path
    $venvRootDir = Split-Path -Parent $venvScriptsDir
    return Join-Path $venvRootDir 'Lib\site-packages'
}

function Test-SetupCollabPipOrphans {
    param([string]$SitePackagesDir)

    if (-not (Test-Path $SitePackagesDir)) {
        return $false
    }

    return [bool](
        Get-ChildItem -LiteralPath $SitePackagesDir -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like '~ollab*' -or $_.Name -like '~collab*' }
    )
}

function Remove-SetupCollabPipOrphans {
    param([string]$SitePackagesDir)

    if (-not (Test-Path $SitePackagesDir)) {
        return
    }

    # Remove stale non-editable copy that takes priority over .pth files
    $staleCollabDir = Join-Path $SitePackagesDir 'collab'
    if (Test-Path $staleCollabDir) {
        Write-Host "   Removing stale non-editable copy: collab\..." -ForegroundColor Yellow
        Remove-Item -LiteralPath $staleCollabDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    # Remove pip rename orphans (~ollab_runtime-*.dist-info, ~collab-*.dist-info)
    Get-ChildItem -LiteralPath $SitePackagesDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like '~ollab*' -or $_.Name -like '~collab*' } |
        ForEach-Object {
            Write-Host "   Removing broken pip artifact: $($_.Name)..." -ForegroundColor Yellow
            Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
}

function Test-SetupCollabInstallHealthy {
    param(
        [string]$PythonExe,
        [string]$CollabExe,
        [string]$ProjectRoot,
        [bool]$ExpectEditable
    )

    $sitePackagesDir = Get-SetupCollabSitePackagesDir -PythonExe $PythonExe
    if (Test-SetupCollabPipOrphans -SitePackagesDir $sitePackagesDir) {
        return $false
    }

    & $PythonExe -c "import collab.lock_client" 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $false
    }

    if ($ExpectEditable) {
        $isEditable = & $PythonExe -c "import json, importlib.metadata; dist = importlib.metadata.distribution('collab-runtime'); data = dist.read_text('direct_url.json'); print(json.loads(data).get('dir_info', {}).get('editable', False) if data else False)" 2>$null
        if ($isEditable -ne "True") {
            return $false
        }
    }

    & $PythonExe -m pip show collab-runtime 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        return $false
    }

    # Unrelated PyPI package named "collab" breaks the console script.
    & $PythonExe -m pip show collab 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        return $false
    }

    if (-not (Test-Path $CollabExe)) {
        return $false
    }

    & $CollabExe --help 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Stop-SetupCollabDaemonForReinstall {
    param(
        [string]$CollabExe,
        [string]$PythonExe,
        [string]$ProjectRoot
    )

    if (-not (Test-Path $CollabExe)) {
        return
    }

    Write-Host "   Stopping collab daemon (unlock collab.exe for package upgrade)..." -ForegroundColor Gray
    & $CollabExe daemon-stop 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0 -and (Test-Path (Join-Path $ProjectRoot 'collab\lock_client.py'))) {
        & $PythonExe -m collab.lock_client daemon-stop 2>&1 | Out-Null
    }
    Start-Sleep -Milliseconds 800
}

# Function to refresh environment variables without restart
function Refresh-EnvPath {
    Write-Host "   Refreshing environment variables..." -ForegroundColor Gray
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# Read the canonical Python version from .python-version at project root.
# This is the single source of truth for which Python interpreter the
# project is developed and tested against.
function Read-TargetPythonVersion {
    $versionFile = Join-Path $projectRoot ".python-version"
    if (-not (Test-Path $versionFile)) {
        Write-Error "   FATAL: .python-version not found at project root."
        Write-Host "   This file declares the canonical Python version for this project." -ForegroundColor Gray
        Write-Host "   Create it with the target version, e.g.: echo 3.12 > .python-version" -ForegroundColor Gray
        exit 1
    }
    $raw = (Get-Content $versionFile -Raw).Trim()
    if ($raw -match '^(\d+)\.(\d+)$') {
        return @{
            Major  = [int]$Matches[1]
            Minor  = [int]$Matches[2]
            String = "$($Matches[1]).$($Matches[2])"
        }
    }
    Write-Error "   FATAL: Invalid .python-version format: '$raw'. Expected 'X.Y' (e.g., '3.12')."
    exit 1
}

# Attempt to install the target Python version via winget.
# Returns $true on success, $false on failure.
function Install-PythonViaWinget {
    param([hashtable]$Target)

    $pkgId = "Python.Python.$($Target.Major).$($Target.Minor)"
    Write-Host "   Attempting to install $pkgId via winget..." -ForegroundColor Gray

    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $proc = Start-Process -FilePath "winget" `
            -ArgumentList "install -e --id $pkgId --silent --accept-package-agreements --accept-source-agreements" `
            -NoNewWindow -PassThru -Wait
        $sw.Stop()

        if ($proc.ExitCode -eq 0) {
            Write-Host "   Python $($Target.String) installed successfully (took $([int]$sw.Elapsed.TotalSeconds) s)" -ForegroundColor Green
            return $true
        }
        else {
            Write-Host "   winget returned exit code $($proc.ExitCode) for $pkgId" -ForegroundColor Red
            return $false
        }
    }
    catch {
        Write-Host "   winget invocation failed: $_" -ForegroundColor Red
        return $false
    }
}

# Step 1: Check Prerequisites
Write-SetupStepHeader -Step 1 -Message 'Checking prerequisites...'

# Read the canonical Python version ONCE, used throughout the script.
$script:TargetPython = Read-TargetPythonVersion

# Step 1.1: Check for Python — ENFORCE the version declared in .python-version.
function Check-Python {
    param([hashtable]$Target)

    # Helper: try to resolve Python by auto-installing the target version.
    function Resolve-PythonMismatch {
        param([hashtable]$Target, [string]$FoundVersion)

        Write-Host "   " -NoNewline
        Write-Host "Python $FoundVersion" -NoNewline -ForegroundColor Yellow
        Write-Host " is installed, but this project requires " -NoNewline -ForegroundColor White
        Write-Host "Python $($Target.String)" -ForegroundColor Cyan
        Write-Host "   (declared in .python-version)." -ForegroundColor Gray
        Write-Host ""
        Write-Host "   Using a different Python version can cause:" -ForegroundColor Gray
        Write-Host "   - Missing package wheels (no cp3XX wheel for your version)" -ForegroundColor Gray
        Write-Host "   - API incompatibilities in pinned dependencies" -ForegroundColor Gray
        Write-Host "   - 'Works on my machine' test failures" -ForegroundColor Gray
        Write-Host ""

        # Guard against infinite recursion.
        if ($script:TargetPythonResolved) {
            Write-Error "   Auto-resolution already attempted — cannot proceed."
            exit 1
        }

        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Host "   Attempting to auto-install Python $($Target.String) via winget..." -ForegroundColor Yellow
            if (Install-PythonViaWinget -Target $Target) {
                Refresh-EnvPath
                $script:TargetPythonResolved = $true
                return (Check-Python -Target $Target)
            }
            Write-Host "   Auto-install failed. Falling through to manual instructions..." -ForegroundColor Yellow
        }

        # Cannot auto-resolve — hard stop with clear instructions.
        Write-Error "`n   Cannot proceed without Python $($Target.String)."
        Write-Host ""
        Write-Host "   To fix this:" -ForegroundColor Yellow
        Write-Host "   1. Install Python $($Target.String) from:" -ForegroundColor White
        Write-Host "      https://www.python.org/downloads/release/python-$($Target.Major)$($Target.Minor)0/" -ForegroundColor Cyan
        Write-Host "   2. Ensure it is on your PATH (check 'Add Python to PATH' during install)" -ForegroundColor White
        Write-Host "   3. Restart your terminal and run this script again" -ForegroundColor White
        Write-Host ""
        Write-Host "   Or, if Python $($Target.String) IS installed but not on PATH:" -ForegroundColor Gray
        Write-Host "   - Add it manually via: set PATH=C:\Path\To\Python$($Target.Major)$($Target.Minor);%PATH%" -ForegroundColor Gray
        exit 1
    }

    # ---- Check 1: python on PATH ----
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $v = python --version 2>&1
        if ($v -match 'Python (\d+)\.(\d+)') {
            $sysMajor = [int]$Matches[1]
            $sysMinor = [int]$Matches[2]

            if ($sysMajor -eq $Target.Major -and $sysMinor -eq $Target.Minor) {
                Write-Host "   Found: " -NoNewline -ForegroundColor White
                Write-Host "$v" -NoNewline -ForegroundColor Green
                Write-Host " (matches .python-version)" -ForegroundColor Gray
                Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
                return $true
            }
            else {
                return (Resolve-PythonMismatch -Target $Target -FoundVersion "$sysMajor.$sysMinor")
            }
        }
        else {
            Write-Warning "   Could not parse Python version from: $v"
            return $false
        }
    }

    # ---- Check 2: scan known install paths for the TARGET version only ----
    $searchPaths = @(
        "${env:LOCALAPPDATA}\Programs\Python\Python$($Target.Major)$($Target.Minor)\python.exe",
        "C:\Python$($Target.Major)$($Target.Minor)\python.exe"
    )

    foreach ($location in $searchPaths) {
        if (Test-Path $location) {
            Write-Host "   Found Python at: $location" -ForegroundColor Green
            $pythonDir = Split-Path -Parent $location
            $env:Path = "$pythonDir;$pythonDir\Scripts;$env:Path"

            try {
                $currentPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
                if ($currentPath -notlike "*$pythonDir*") {
                    $newPath = "$pythonDir;$pythonDir\Scripts;$currentPath"
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

$script:TargetPythonResolved = $false

if (-not (Check-Python -Target $script:TargetPython)) {
    Write-Warning "   Python $($script:TargetPython.String) not found. Attempting automatic installation via winget..."

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if (Install-PythonViaWinget -Target $script:TargetPython) {
            Refresh-EnvPath
            if (-not (Check-Python -Target $script:TargetPython)) {
                Write-Warning "   Python installed but not found in PATH."
                Write-Host "   Please restart your terminal and run this script again." -ForegroundColor Yellow
                exit 1
            }
        }
        else {
            Write-Error "   Automatic installation of Python $($script:TargetPython.String) via winget failed."
            Write-Host ""
            Write-Host "   Please install Python $($script:TargetPython.String) manually:" -ForegroundColor Yellow
            Write-Host "   1. Download from: https://www.python.org/downloads/release/python-$($script:TargetPython.Major)$($script:TargetPython.Minor)0/" -ForegroundColor White
            Write-Host "   2. Run installer and check 'Add Python to PATH'" -ForegroundColor White
            Write-Host "   3. Restart terminal and run this script again" -ForegroundColor White
            exit 1
        }
    }
    else {
        Write-Error "   Python $($script:TargetPython.String) not found and winget not available."
        Write-Host "   Please install Python $($script:TargetPython.String) manually from https://www.python.org" -ForegroundColor Yellow
        exit 1
    }
}

# Step 1.2: Check for Git
function Check-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        $v = git --version
        Write-Host "   Found: " -NoNewline -ForegroundColor White
        Write-Host "$v" -NoNewline -ForegroundColor White
        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
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
Write-SetupStepHeader -Step 2 -Message 'Setting up virtual environment...'

# Validate existing .venv: if it was created by a different Python version
# than what .python-version declares, delete it so we get a clean venv.
$venvNeedsRecreate = $false
if (Test-Path ".venv") {
    $pyvenvCfg = ".\.venv\pyvenv.cfg"
    $venvVersionKnown = $false
    if (Test-Path $pyvenvCfg) {
        $cfgContent = Get-Content $pyvenvCfg -Raw
        if ($cfgContent -match 'version\s*=\s*(\d+\.\d+)') {
            $venvPythonVersion = $Matches[1]
            $venvVersionKnown = $true
            if ($venvPythonVersion -ne $script:TargetPython.String) {
                Write-Host "   Existing .venv uses Python $venvPythonVersion, but .python-version requires $($script:TargetPython.String)" -ForegroundColor Yellow
                Write-Host "   Recreating .venv with the correct Python version..." -ForegroundColor Gray
                $venvNeedsRecreate = $true
            }
        }
    }
    # Safety: if we cannot determine the venv Python version
    # (missing pyvenv.cfg or unparseable), recreate to be safe.
    if (-not $venvVersionKnown) {
        Write-Host "   Cannot determine .venv Python version — recreating to be safe" -ForegroundColor Yellow
        Write-Host "   (pyvenv.cfg missing or unparseable)" -ForegroundColor Gray
        $venvNeedsRecreate = $true
    }
    if ($venvNeedsRecreate) {
        Remove-Item -Recurse -Force ".venv" -ErrorAction Stop
        Write-Host "   Old .venv removed." -ForegroundColor Gray
    }
}

if (-not (Test-Path ".venv")) {
    Write-Host "   Creating " -NoNewline -ForegroundColor White
    Write-Host ".venv" -NoNewline -ForegroundColor Magenta
    Write-Host "..." -NoNewline -ForegroundColor White
    python -m venv .venv
    if ($LASTEXITCODE -eq 0) {
        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
    }
    else {
        Write-SetupEmit (Get-SetupStatusToken 'FAILED') -Color Red
        Write-Error "Failed to create virtual environment."
        exit 1
    }
}
else {
    Write-Host "   Virtual environment already exists (Python $($script:TargetPython.String)) " -NoNewline -ForegroundColor White
    Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
}

# Step 3: Install Dependencies
Write-SetupStepHeader -Step 3 -Message 'Installing core dependencies...'

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
    Write-Host "..." -NoNewline -ForegroundColor White

    # Run pip with --upgrade to ensure packages are updated when
    # Dependabot bumps version pins in requirements.txt.
    $pipInstall = & $pipPath install --upgrade --upgrade-strategy only-if-needed -r requirements.txt --quiet --no-warn-script-location 2>&1

    if ($LASTEXITCODE -eq 0) {
        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
    }
    else {
        Write-SetupEmit (Get-SetupStatusToken 'FAILED') -Color Red
        Write-Host "`n   Error details:" -ForegroundColor Yellow
        $pipInstall | ForEach-Object { Write-Host "   $_" -ForegroundColor Red }
        exit 1
    }
}
else {
    Write-Warning "   requirements.txt not found. Skipping core dependencies."
    $script:ErrorCount++
}

# Step 4: Install Collab Package
Write-SetupStepHeader -Step 4 -Message 'Installing collab package...'

$pythonResolved = (Resolve-Path $pythonPath).Path
$venvScriptsDir = Split-Path -Parent $pythonResolved
$sitePackagesDir = Get-SetupCollabSitePackagesDir -PythonExe $pythonResolved
$collabExeCandidate = Join-Path $venvScriptsDir 'collab.exe'
$expectEditable = Test-Path (Join-Path $projectRoot 'collab\lock_client.py')

$collabSpec = $env:COLLAB_RUNTIME_SPEC
if (-not $collabSpec) {
    if ($expectEditable) {
        $collabSpec = '-e .'
    } else {
        $collabSpec = 'collab-runtime'
    }
}

$skipCollabReinstall = $false
if (-not $Force -and -not $env:COLLAB_RUNTIME_SPEC) {
    if (Test-SetupCollabInstallHealthy -PythonExe $pythonResolved -CollabExe $collabExeCandidate -ProjectRoot $projectRoot -ExpectEditable $expectEditable) {
        $skipCollabReinstall = $true
    }
}

if ($skipCollabReinstall) {
    $collabCheckOutput = (& $pythonPath -m pip show collab-runtime 2>&1)
    $installedVersion = ''
    if ($LASTEXITCODE -eq 0) {
        $versionLine = $collabCheckOutput | Where-Object { $_ -match '^Version:' }
        if ($versionLine) {
            $installedVersion = ($versionLine -replace '^Version:\s*', '').Trim()
        }
    }
    if ($installedVersion) {
        Write-Host "   collab-runtime $installedVersion already installed and healthy " -NoNewline -ForegroundColor White
    } else {
        Write-Host "   collab-runtime already installed and healthy " -NoNewline -ForegroundColor White
    }
    Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
    Write-Host "   (use -Force to reinstall)" -ForegroundColor Gray
}
else {
    # Interrupted pip installs can leave rename orphans (e.g. ~ollab_runtime-*.dist-info).
    # Stop the daemon first so Windows releases collab.exe during reinstall.
    Stop-SetupCollabDaemonForReinstall -CollabExe $collabExeCandidate -PythonExe $pythonResolved -ProjectRoot $projectRoot
    Remove-SetupCollabPipOrphans -SitePackagesDir $sitePackagesDir

    & $pythonPath -m pip uninstall collab-runtime -y --quiet 2>&1 | Out-Null

    Write-Host "   Checking for conflicting 'collab' package..." -ForegroundColor Gray
    & $pythonPath -m pip uninstall collab -y --quiet 2>&1 | Out-Null

    if ($env:COLLAB_RUNTIME_SPEC) {
        Write-Host "   Installing defined spec: $collabSpec..." -ForegroundColor Gray
    } elseif ($expectEditable) {
        Write-Host "   Detected collab source repository. Using editable install..." -ForegroundColor Gray
    } else {
        Write-Host "   Installing latest collab-runtime from registry..." -ForegroundColor Gray
    }

    Write-Host "   Installing " -NoNewline -ForegroundColor White
    Write-Host "$collabSpec" -NoNewline -ForegroundColor Magenta
    Write-Host "..." -NoNewline -ForegroundColor White

    & $pipPath install $collabSpec --quiet --no-warn-script-location 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $collabCheckOutput = (& $pythonPath -m pip show collab-runtime 2>&1)
        if ($LASTEXITCODE -eq 0) {
            $versionLine = $collabCheckOutput | Where-Object { $_ -match '^Version:' }
            if ($versionLine) {
                $installedVersion = $versionLine -replace '^Version:\s*', ''
                Write-Host "   collab-runtime $installedVersion installed " -NoNewline -ForegroundColor White
                Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
            }
        } else {
            Write-Host "   collab package installed " -NoNewline -ForegroundColor White
            Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
        }
    }
    else {
        Write-Host "   collab package installation " -NoNewline -ForegroundColor White
        Write-SetupEmit (Get-SetupStatusToken 'FAILED') -Color Red
        Write-Warning "Check the output above for errors."
        $script:ErrorCount++
    }
}

# Step 5: Environment Configuration
Write-SetupStepHeader -Step 5 -Message 'Configuring environment...'
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "   Created " -NoNewline -ForegroundColor White
        Write-Host ".env" -NoNewline -ForegroundColor Magenta
        Write-Host " from " -NoNewline -ForegroundColor White
        Write-Host ".env.example" -NoNewline -ForegroundColor Magenta
        Write-Host " " -NoNewline
        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
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
    Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
}

# Step 6: Collaborative locking prerequisites
Write-SetupStepHeader -Step 6 -Message 'Validating collaborative locking prerequisites...'

if (Test-SupabaseImport -PythonExe $pythonPath) {
    Write-Host "   supabase-py import check " -NoNewline -ForegroundColor White
    Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
}
else {
    Write-Host "   supabase-py import check " -NoNewline -ForegroundColor White
    Write-SetupEmit (Get-SetupStatusToken 'WARN') -Color Yellow
    Write-Host "   Installing supabase and python-dotenv into .venv..." -ForegroundColor Gray
    & $pythonPath -m pip install supabase python-dotenv --quiet
    if ($LASTEXITCODE -eq 0 -and (Test-SupabaseImport -PythonExe $pythonPath)) {
        Write-Host "   supabase-py installed " -NoNewline -ForegroundColor White
        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
    }
    else {
        Write-Host "   supabase-py installation " -NoNewline -ForegroundColor White
        Write-SetupEmit (Get-SetupStatusToken 'FAILED') -Color Red
        $script:ErrorCount++
    }
}

$envPath = Join-Path $projectRoot ".env"
if (Test-Path $envPath) {
    $envContent = Get-Content $envPath -Raw

    $urlMatch = [regex]::Match($envContent, '(?m)^SUPABASE_URL=(.+)$')
    $anonMatch = [regex]::Match($envContent, '(?m)^SUPABASE_ANON_KEY=(.+)$')

    $urlValue = if ($urlMatch.Success) { $urlMatch.Groups[1].Value.Trim() } else { '' }
    $anonValue = if ($anonMatch.Success) { $anonMatch.Groups[1].Value.Trim() } else { '' }

    $urlIsPlaceholder = Test-IsPlaceholderValue $urlValue
    $anonIsPlaceholder = Test-IsPlaceholderValue $anonValue

    if ((-not $urlIsPlaceholder) -and (-not $anonIsPlaceholder)) {
        Write-Host "   SUPABASE_URL: using pre-configured team value " -NoNewline -ForegroundColor White
        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
    }
    elseif ($urlIsPlaceholder -and $anonIsPlaceholder) {
        Write-Host "   Supabase credentials in .env " -NoNewline -ForegroundColor White
        Write-SetupEmit (Get-SetupStatusToken 'WARN') -Color Yellow
        Write-Host "   Set SUPABASE_URL and SUPABASE_ANON_KEY to real values." -ForegroundColor Gray
        if (-not $CalledFromDev) {
            $script:ErrorCount++
        }
    }
    else {
        # Partial config — one is set, one is placeholder
        if ($urlIsPlaceholder) {
            Write-Host "   SUPABASE_URL is still a placeholder " -NoNewline -ForegroundColor White
            Write-SetupEmit (Get-SetupStatusToken 'WARN') -Color Yellow
        }
        if ($anonIsPlaceholder) {
            Write-Host "   SUPABASE_ANON_KEY is still a placeholder " -NoNewline -ForegroundColor White
            Write-SetupEmit (Get-SetupStatusToken 'WARN') -Color Yellow
        }
        if (-not $CalledFromDev) {
            $script:ErrorCount++
        }
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
        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green

        $sourceHooksDir = Join-Path $projectRoot "scripts\git-hooks"
        $targetHooksDir = Join-Path $projectRoot ".git\hooks"
        $hookNames = @("pre-commit", "post-commit", "pre-push", "commit-msg", "post-merge", "post-checkout")
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
                Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
            }
            else {
                Write-Host "   Collab hook overlay " -NoNewline -ForegroundColor White
                Write-SetupEmit (Get-SetupStatusToken 'WARN') -Color Yellow
                $script:ErrorCount++
            }
        }
        else {
            Write-Host "   Collab hook templates missing (scripts/git-hooks/) " -NoNewline -ForegroundColor White
            Write-SetupEmit (Get-SetupStatusToken 'WARN') -Color Yellow
            $script:ErrorCount++
        }
    }
    else {
        Write-Host "   Git hook installation " -NoNewline -ForegroundColor White
        Write-SetupEmit (Get-SetupStatusToken 'WARN') -Color Yellow
        $script:ErrorCount++
    }
}
else {
    Write-Host "   pre-commit not available in .venv " -NoNewline -ForegroundColor White
    Write-SetupEmit (Get-SetupStatusToken 'SKIP') -Color Yellow
    Write-Host "   Run scripts/setup-dev.ps1 to install and register repository hooks." -ForegroundColor Gray
}

# Step 6: VS Code Extension Installation (Optional)
Write-SetupStepHeader -Step 7 -Message 'Installing VS Code extension (optional)...'

Write-Host "   Fetching extension from GitHub Releases..." -ForegroundColor Gray

$ideCommands = @("code", "code-insiders", "cursor", "codium", "antigravity")
$cliFound = $false
foreach ($ide in $ideCommands) {
    if (Get-Command $ide -ErrorAction SilentlyContinue) {
        $cliFound = $true
        break
    }
}

if ($cliFound) {
    try {
        $tempVsix = if ($IsWindows) { Join-Path $env:TEMP "collab-locks-latest.vsix" } else { "/tmp/collab-locks-latest.vsix" }
        # Since this is a public repo, we can fetch the latest release asset via GitHub API
        $releaseUrl = "https://api.github.com/repos/KirilMT/collab/releases/latest"
        $releaseInfo = Invoke-RestMethod -Uri $releaseUrl -ErrorAction Stop

        $vsixAsset = $releaseInfo.assets | Where-Object { $_.name -match '\.vsix$' } | Select-Object -First 1

        if ($vsixAsset) {
            Invoke-WebRequest -Uri $vsixAsset.browser_download_url -OutFile $tempVsix -ErrorAction Stop

            foreach ($ide in $ideCommands) {
                if (Get-Command $ide -ErrorAction SilentlyContinue) {
                    Write-Host "   Installing into $ide... " -NoNewline -ForegroundColor Gray
                    & $ide --install-extension $tempVsix --force 2>&1 | Out-Null

                    if ($LASTEXITCODE -eq 0) {
                        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
                    } else {
                        Write-SetupEmit (Get-SetupStatusToken 'WARN') -Color Yellow
                    }
                }
            }
            Remove-Item $tempVsix -ErrorAction SilentlyContinue
        } else {
            Write-Host "   No .vsix asset found on latest GitHub release " -NoNewline -ForegroundColor White
            Write-SetupEmit (Get-SetupStatusToken 'WARN') -Color Yellow
        }
    }
    catch {
        Write-Host "   VS Code extension installation failed " -NoNewline -ForegroundColor White
        Write-SetupEmit (Get-SetupStatusToken 'WARN') -Color Yellow
    }
}
else {
    Write-Host "   No supported IDE CLIs found. Extension must be installed manually:" -ForegroundColor Gray
    Write-Host "     1. Open your IDE (VS Code, Cursor, Antigravity)" -ForegroundColor Gray
    Write-Host "     2. Go to Extensions -> '...' -> 'Install from VSIX'" -ForegroundColor Gray
}

# Step 7: Smoke Tests (Health Checks)
Write-SetupStepHeader -Step 8 -Message 'Running smoke tests...'

$smokeTestsPassed = $true

# Test 1: Check if collab command is available
Write-Host "   Testing collab command availability..." -ForegroundColor Gray
$venvBin = if ($script:IsWin -or (Test-Path (Join-Path $projectRoot ".venv\Scripts"))) { "Scripts" } else { "bin" }
$collabExe = if ($script:IsWin) { "collab.exe" } else { "collab" }
$collabCmd = Join-Path $projectRoot ".venv\$venvBin\$collabExe"
if (Test-Path $collabCmd) {
    # Use --help for health check because collab CLI does not expose --version.
    & $collabCmd --help 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   collab command available " -NoNewline -ForegroundColor White
        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
    }
    else {
        Write-Host "   collab command available " -NoNewline -ForegroundColor White
        Write-SetupEmit (Get-SetupStatusToken 'WARN') -Color Yellow
        $smokeTestsPassed = $false
    }
}
else {
    Write-Host "   collab command not found " -NoNewline -ForegroundColor White
    Write-SetupEmit (Get-SetupStatusToken 'WARN') -Color Yellow
    $smokeTestsPassed = $false
}

# Test 2: Verify supabase credentials (if .env exists)
Write-Host "   Validating Supabase configuration..." -ForegroundColor Gray
if (Test-Path ".env") {
    $envContent = Get-Content ".env" -Raw
    $urlSmokeMatch = [regex]::Match($envContent, '(?m)^SUPABASE_URL=(.+)$')
    $anonSmokeMatch = [regex]::Match($envContent, '(?m)^SUPABASE_ANON_KEY=(.+)$')

    $urlSmokeVal = if ($urlSmokeMatch.Success) { $urlSmokeMatch.Groups[1].Value.Trim() } else { '' }
    $anonSmokeVal = if ($anonSmokeMatch.Success) { $anonSmokeMatch.Groups[1].Value.Trim() } else { '' }

    if ($urlSmokeVal -and $anonSmokeVal -and
        (-not (Test-IsPlaceholderValue $urlSmokeVal)) -and
        (-not (Test-IsPlaceholderValue $anonSmokeVal))) {
        Write-Host "   Supabase configuration present " -NoNewline -ForegroundColor White
        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
    }
    else {
        Write-Host "   Supabase credentials not set " -NoNewline -ForegroundColor White
        Write-SetupEmit (Get-SetupStatusToken 'WARN') -Color Yellow
        $smokeTestsPassed = $false
    }
}

if ($smokeTestsPassed) {
    Write-Host "   All smoke tests passed " -NoNewline -ForegroundColor White
    Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
}

# Step 8: Ensuring Collaborative Daemon is running
Write-SetupStepHeader -Step 9 -Message 'Ensuring Collaborative Daemon is running...'

if (Test-Path $pythonPath) {
    $collabExe = Join-Path (Split-Path $pythonPath) "collab.exe"
    & $collabExe daemon-status | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   Starting daemon in background..." -ForegroundColor Gray
        & $collabExe daemon-start
        if ($LASTEXITCODE -eq 0) {
            Write-Host "   Daemon started successfully " -NoNewline -ForegroundColor White
            Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
        } else {
            Write-Host "   Failed to start daemon " -NoNewline -ForegroundColor White
            Write-SetupEmit (Get-SetupStatusToken 'WARN') -Color Yellow
        }
    } else {
        Write-Host "   Daemon is already running " -NoNewline -ForegroundColor White
        Write-SetupEmit (Get-SetupStatusToken 'OK') -Color Green
    }
}

# Step 9: Final Verification
Write-SetupStepHeader -Step 10 -Message 'Final verification...'
& $collabExe daemon-status

# Final Summary - Only show if not called from dev script
if (-not $CalledFromDev) {
    Write-Host "`n========================================" -ForegroundColor Cyan
    if ($script:ErrorCount -eq 0) {
        Write-Host "   Installation Complete!" -ForegroundColor Green
        Write-Host "   (Production + Daemon Active)" -ForegroundColor Gray
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
    Write-Host "  2. Verify collab is installed and working:" -ForegroundColor White
    Write-Host "     collab active" -ForegroundColor Magenta
    Write-Host ""
    Write-Host "  3. (Optional) Setup development environment:" -ForegroundColor White
    Write-Host "     .\scripts\setup-dev.ps1" -ForegroundColor Magenta
    Write-Host ""
    Write-Host "  Locking works out of the box — no manual Supabase setup needed." -ForegroundColor Gray
    Write-Host "  Force-release via dashboard requires SUPABASE_SERVICE_ROLE_KEY" -ForegroundColor Gray
    Write-Host "  in your .env (obtain from a maintainer; never commit it)." -ForegroundColor Gray
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host ""
}

if ($script:ErrorCount -gt 0) {
    exit $script:ErrorCount
} elseif (-not $smokeTestsPassed) {
    exit 1
} else {
    exit 0
}
