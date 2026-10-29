# Set up claude-launcher on Windows: claude-launcher.cmd on PATH, its Claude
# Code hook, and the "Claude Code" profile in Windows Terminal. Safe to re-run:
# it updates whatever is already installed. The steps themselves are in
# src/claude_launcher/install.py, the same as install.sh's.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1 [-Icon FILE.png|default]
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
#
# It needs Python 3.10 or newer, and offers to install it with winget if
# there's none.
param(
    [string]$Icon = "",
    [switch]$Uninstall,
    [switch]$Help
)
$ErrorActionPreference = "Continue"  # "Stop" makes a native program's stderr fatal in PowerShell 5.1
if ($Help) {
    Get-Content $PSCommandPath | Select-Object -Skip 1 -First 9 | ForEach-Object { $_ -replace "^# ?", "" }
    exit 0
}

$repo = Split-Path -Parent $PSCommandPath
$core = Join-Path $repo "libexec\claude-launcher-core"
# The core writes UTF-8; read it as UTF-8.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

function Say([string]$text) { Write-Host "==> " -ForegroundColor DarkYellow -NoNewline; Write-Host $text }
function Warn([string]$text) { Write-Host "warning: " -ForegroundColor Yellow -NoNewline; Write-Host $text }
function Fail([string]$text) { Write-Host "error: " -ForegroundColor Red -NoNewline; Write-Host $text }

# A Python 3.10+, as the full path to its python.exe, or $null. Skips the
# Microsoft Store's python.exe, which only offers to install Python.
function Find-Python {
    $check = "import sys; print(sys.executable) if sys.version_info >= (3, 10) else sys.exit(1)"
    foreach ($try in @(@("py", "-3"), @("python"), @("python3"))) {
        $cmd = Get-Command $try[0] -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $cmd -or $cmd.Source -like "*\WindowsApps\*") { continue }
        $rest = @($try | Select-Object -Skip 1)
        $out = & $cmd.Source @rest -c $check 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return ($out | Select-Object -Last 1).Trim() }
    }
    return $null
}

function Update-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [Environment]::GetEnvironmentVariable("Path", "User")
}

$python = Find-Python
if (-not $python -and -not $Uninstall) {
    Warn "claude-launcher needs Python 3.10 or newer, and there isn't one."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "Install Python from https://www.python.org/downloads/, then run this again."
        exit 1
    }
    $answer = Read-Host "Install Python 3.13 for you now, with winget? [Y/n]"
    if ($answer -and $answer -notmatch "^[Yy]") {
        Fail "Install Python 3.10 or newer, then run this again."
        exit 1
    }
    winget install --exact --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
    Update-Path
    $python = Find-Python
    if (-not $python) {
        Fail "Python didn't install; install it from https://www.python.org/downloads/, then run this again."
        exit 1
    }
}
if (-not $python) { Fail "Python 3.10 or newer is needed to uninstall; nothing was changed."; exit 1 }

# Run the shared steps, showing their lines: "warning: ..." as a warning, and
# "error: ..." as the error that stopped them.
if ($Uninstall) {
    $steps = @("install", "undo")
} else {
    $steps = @("install", "run")
    if ($Icon -and $Icon -ne "default") { $Icon = (Resolve-Path $Icon).Path }
    if ($Icon) { $steps += @("--icon", $Icon) }
}
& $python $core @steps | ForEach-Object {
    if ($_ -like "warning: *") { Warn $_.Substring(9) }
    elseif ($_ -like "error: *") { Fail $_.Substring(7) }
    else { Say $_ }
}
exit $LASTEXITCODE
