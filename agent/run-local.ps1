<#
.SYNOPSIS
    ServerPulse - Local Test Runner for Windows
    Runs the agent once without installing anything.
    Config is stored in .\agent.conf (gitignored).
.PARAMETER DryRun
    Print collected metrics as JSON without sending to the API.
.PARAMETER DebugMode
    Enable verbose debug output.
.EXAMPLE
    .\run-local.ps1                        # real POST to the API
    .\run-local.ps1 -DryRun               # print metrics, no HTTP request
    .\run-local.ps1 -DryRun -DebugMode    # dry-run with verbose output
#>

param(
    [switch]$DryRun,
    [switch]$DebugMode
)

$ErrorActionPreference = "Stop"

$ScriptDir     = Split-Path -Parent $MyInvocation.MyCommand.Path
$AgentPath     = Join-Path $ScriptDir "agent.py"
$ConfPath      = Join-Path $ScriptDir "agent.conf"
$DefaultApiUrl = "https://api.yourdomain.com"

function Write-Info { param($msg) Write-Host "[INFO]  $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "[WARN]  $msg" -ForegroundColor Yellow }

# Find Python
$PythonExe = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $null = & $cmd -c "import sys; sys.exit(0 if sys.version_info>=(3,6) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $PythonExe = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
            break
        }
    } catch { }
}

if (-not $PythonExe) {
    Write-Host "[ERROR] Python 3.6+ not found. Download from https://www.python.org" -ForegroundColor Red
    exit 1
}

Write-Info "Using Python at $PythonExe"

# Create local config if missing
if (-not (Test-Path $ConfPath)) {
    Write-Host ""
    Write-Host "No local config found - creating $ConfPath" -ForegroundColor Cyan
    Write-Host ""

    do {
        $ApiKeySecure = Read-Host "  API Key (sp_live_...)" -AsSecureString
        $ApiKeyBSTR   = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($ApiKeySecure)
        $ApiKey       = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($ApiKeyBSTR)
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ApiKeyBSTR)
        if ($ApiKey.Length -lt 8) {
            Write-Warn "API key too short. Try again."
        }
    } while ($ApiKey.Length -lt 8)

    $ConfContent = "[serverpulse]`napi_url = $DefaultApiUrl`napi_key = $ApiKey`n"
    Set-Content -Path $ConfPath -Value $ConfContent -Encoding UTF8

    Write-Info "Config saved to $ConfPath"
    Write-Host ""
}

# Build argument list
$AgentArgs = @("--config", $ConfPath)
if ($DryRun)     { $AgentArgs += "--dry-run" }
if ($DebugMode)  { $AgentArgs += "--debug"   }

# Run the agent
Write-Host ""
Write-Info "Running agent (no Scheduled Task, no system files)..."
Write-Host "---------------------------------------------"
& $PythonExe $AgentPath @AgentArgs
