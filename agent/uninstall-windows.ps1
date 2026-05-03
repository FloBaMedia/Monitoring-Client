<#
.SYNOPSIS
    ServerPulse Agent Uninstaller for Windows
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File uninstall-windows.ps1
#>

$ErrorActionPreference = "SilentlyContinue"

$InstallDir = "C:\ProgramData\ServerPulse"
$TaskName   = "ServerPulseAgent"

function Write-Info  { param($msg) Write-Host "[INFO]  $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "[WARN]  $msg" -ForegroundColor Yellow }

function Confirm-Action {
    param($prompt)
    $ans = Read-Host "$prompt [y/N]"
    return $ans -match "^[yY]$"
}

function Exit-Script {
    param([int]$Code = 0)
    $IsNewWindow = ($Host.Name -eq 'ConsoleHost') -and
                   ([System.Diagnostics.Process]::GetCurrentProcess().MainWindowHandle -ne [IntPtr]::Zero) -and
                   ($null -eq $env:WT_SESSION) -and
                   ($null -eq $env:TERM_PROGRAM)
    if ($IsNewWindow -or $Code -ne 0) {
        Write-Host ""
        Write-Host "Press Enter to close this window..." -ForegroundColor DarkGray
        $null = Read-Host
    }
    exit $Code
}

# ── Admin check ───────────────────────────────────────────────────────────────
$currentPrincipal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host ""
    Write-Host "[ERROR] This uninstaller must be run as Administrator." -ForegroundColor Red
    Write-Host ""
    Write-Host "Please right-click PowerShell and choose 'Run as Administrator'," -ForegroundColor White
    Write-Host "then re-run this script." -ForegroundColor White
    Exit-Script 1
}

Write-Host ""
Write-Host "ServerPulse Agent Uninstaller" -ForegroundColor Cyan
Write-Host "─────────────────────────────────────────────"
Write-Host ""
Write-Host "This will remove:"
Write-Host "  * Scheduled Task '$TaskName'"
Write-Host "  * All files in $InstallDir"
Write-Host ""

if (-not (Confirm-Action "Continue with uninstallation?")) {
    Write-Host "Aborted."
    Exit-Script 0
}
Write-Host ""

# ── 1. Remove Scheduled Task ──────────────────────────────────────────────────
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Info "Scheduled Task '$TaskName' removed."
} else {
    Write-Info "Scheduled Task '$TaskName' not found – skipping."
}

# ── 2. Remove install directory ───────────────────────────────────────────────
if (Test-Path $InstallDir) {
    $logPath = "$InstallDir\agent.log"
    $hasLog  = Test-Path $logPath

    if ($hasLog) {
        Write-Host ""
        if (Confirm-Action "Also delete log file $logPath?") {
            Remove-Item -Recurse -Force $InstallDir
            Write-Info "Removed $InstallDir (including log)."
        } else {
            Get-ChildItem $InstallDir -Exclude "agent.log","agent.log.1" | Remove-Item -Recurse -Force
            Write-Info "Removed agent files. Log kept at $logPath"
        }
    } else {
        Remove-Item -Recurse -Force $InstallDir
        Write-Info "Removed $InstallDir"
    }
} else {
    Write-Info "$InstallDir not found – skipping."
}

Write-Host ""
Write-Info "ServerPulse Agent has been uninstalled."
Write-Host ""

Exit-Script 0
