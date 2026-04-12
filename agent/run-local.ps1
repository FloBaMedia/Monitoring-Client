<#
.SYNOPSIS
    ServerPulse - Local Test Runner for Windows
    Runs the agent without installing anything.
    Config is stored in .\agent.conf (gitignored).
.PARAMETER DryRun
    Print collected metrics as JSON without sending to the API.
.PARAMETER DebugMode
    Enable verbose debug output.
.PARAMETER Watch
    Loop continuously until Ctrl+C.
.PARAMETER Interval
    Seconds between runs in watch mode (default: 60).
.EXAMPLE
    .\run-local.ps1                             # single real POST
    .\run-local.ps1 -DryRun                    # single dry-run
    .\run-local.ps1 -Watch                     # loop every 60s
    .\run-local.ps1 -Watch -Interval 10        # loop every 10s
    .\run-local.ps1 -Watch -DryRun -Interval 5 # loop dry-run every 5s
#>

param(
    [switch]$DryRun,
    [switch]$DebugMode,
    [switch]$Watch,
    [int]$Interval = 60
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

# Info: if no local agent.conf exists the agent uses its system config
# (C:\ProgramData\ServerPulse\agent.conf) or prompts via ensure_config
if (Test-Path $ConfPath) {
    Write-Info "Using local config: $ConfPath"
} else {
    Write-Info "No local agent.conf found - using system config or prompting on first run"
}

# Build agent argument list
# --debug is always on for local runs so output appears in the terminal
# Only pass --config if a local agent.conf exists; otherwise the agent
# uses its normal search order (system config, env vars, etc.)
$AgentArgs = @("--debug")
if (Test-Path $ConfPath) { $AgentArgs += @("--config", $ConfPath) }
if ($DryRun) { $AgentArgs += "--dry-run" }

# Single run function
function Invoke-Agent {
    Write-Host "---------------------------------------------"
    & $PythonExe $AgentPath @AgentArgs
}

# Watch mode or single run
if ($Watch) {
    Write-Info "Watch mode - running every ${Interval}s. Press Ctrl+C to stop."
    $run = 1
    try {
        while ($true) {
            Write-Host ""
            Write-Info "Run #$run  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
            try { Invoke-Agent } catch { Write-Warn "Agent error: $_" }
            $run++
            Write-Info "Next run in ${Interval}s ..."
            Start-Sleep -Seconds $Interval
        }
    } finally {
        Write-Host ""
        Write-Info "Stopped."
    }
} else {
    Write-Host ""
    Write-Info "Running agent (no Scheduled Task, no system files)..."
    Invoke-Agent
}
