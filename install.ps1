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
    foreach ($line in Get-Content -LiteralPath $PSCommandPath) {
        if ($line -notlike "#*") { break }
        $line -replace "^# ?", ""
    }
    exit 0
}

$repo = Split-Path -Parent $PSCommandPath
$core = Join-Path $repo "libexec\claude-launcher-core"

function Say([string]$text) { Write-Host "==> " -ForegroundColor DarkYellow -NoNewline; Write-Host $text }
function Warn([string]$text) { Write-Host "warning: " -ForegroundColor Yellow -NoNewline; Write-Host $text }
function Fail([string]$text) { Write-Host "error: " -ForegroundColor Red -NoNewline; Write-Host $text }

# A Python 3.10+, as the full path to its python.exe, or $null. Tried in the
# order the launcher's .cmd files find one (setup.py's _RUN), so this is the
# Python they'll run: the first python.exe on PATH unless it's in WindowsApps,
# then py -3, then that WindowsApps one. There, python.exe is either the
# Microsoft Store's stand-in, which only offers to install Python and fails
# here with no output, or a real Python from the Store or the Python install
# manager.
function Find-Python {
    $check = "import sys; print(sys.executable) if sys.version_info >= (3, 10) else sys.exit(1)"
    $first = Get-Command -Name python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $launcher = Get-Command -Name py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $candidates = @()
    $store = $null
    if ($first) {
        if ($first.Source -like "*\WindowsApps\*") { $store = $first.Source } else { $candidates += , @($first.Source) }
    }
    if ($launcher) { $candidates += , @($launcher.Source, "-3") }
    if ($store) { $candidates += , @($store) }
    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        $rest = @($candidate | Select-Object -Skip 1)
        $out = & $exe @rest -c $check 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return ($out | Select-Object -Last 1).Trim() }
    }
    return $null
}

# PATH as a new session would have it, after winget changed it.
function Import-MachineAndUserPath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [Environment]::GetEnvironmentVariable("Path", "User")
}

# The install or uninstall; returns the exit code.
function Invoke-Install([string]$IconFile, [switch]$Remove) {
    $steps = @("install", "run")
    if ($Remove) {
        $steps = @("install", "undo")
    } elseif ($IconFile -eq "default") {  # -eq ignores case
        $steps += @("--icon", "default")
    } elseif ($IconFile) {
        # -LiteralPath: [ and ] are wildcards to plain -Path.
        if (-not (Test-Path -LiteralPath $IconFile -PathType Leaf)) {
            Fail "No such file: $IconFile"
            return 1
        }
        $steps += @("--icon", (Resolve-Path -LiteralPath $IconFile).ProviderPath)
    }

    $python = Find-Python
    if (-not $python -and -not $Remove) {
        Warn "claude-launcher needs Python 3.10 or newer, and there isn't one."
        if (-not (Get-Command -Name winget -ErrorAction SilentlyContinue)) {
            Fail "Install Python from https://www.python.org/downloads/, then run this again."
            return 1
        }
        $answer = Read-Host "Install Python 3.13 for you now, with winget? [Y/n]"
        if ($answer -and $answer -notmatch "^[Yy]") {
            Fail "Install Python 3.10 or newer, then run this again."
            return 1
        }
        winget install --exact --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements | Out-Host
        Import-MachineAndUserPath
        $python = Find-Python
        if (-not $python) {
            Fail "Python didn't install; install it from https://www.python.org/downloads/, then run this again."
            return 1
        }
    }
    if (-not $python) {
        Fail "Python 3.10 or newer is needed to uninstall; nothing was changed."
        return 1
    }

    # Run the shared steps, showing their lines: "warning: ..." as a warning,
    # and "error: ..." as the error that stopped them.
    & $python $core @steps | ForEach-Object {
        if ($_ -like "warning: *") { Warn $_.Substring(9) }
        elseif ($_ -like "error: *") { Fail $_.Substring(7) }
        else { Say $_ }
    }
    return $LASTEXITCODE
}

# The core writes UTF-8; read it as UTF-8. Both are put back afterwards, since
# running .\install.ps1 changes the caller's own session.
$savedOutputEncoding = [Console]::OutputEncoding
$savedPythonEncoding = $env:PYTHONIOENCODING
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $env:PYTHONIOENCODING = "utf-8"
    $code = Invoke-Install -IconFile $Icon -Remove:$Uninstall
} finally {
    [Console]::OutputEncoding = $savedOutputEncoding
    if ($null -eq $savedPythonEncoding) {
        Remove-Item -Path Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONIOENCODING = $savedPythonEncoding
    }
}
exit $code
