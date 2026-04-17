#Requires -RunAsAdministrator
<#
.SYNOPSIS
    ServerPulse Agent Installer for Windows
.DESCRIPTION
    Downloads the agent.py, creates the config, and registers a Scheduled Task
    to run the agent every minute.
    API URL and API Key can be passed as parameters or environment variables
    (SERVERPULSE_URL / SERVERPULSE_KEY) to run non-interactively.
.EXAMPLE
    # Interactive
    powershell -ExecutionPolicy Bypass -File install-windows.ps1

    # Non-interactive (e.g. from a setup command)
    powershell -ExecutionPolicy Bypass -File install-windows.ps1 -ApiUrl "https://api.example.com" -ApiKey "sp_live_..."

    # Via environment variables
    $env:SERVERPULSE_URL="https://api.example.com"; $env:SERVERPULSE_KEY="sp_live_..."; powershell -ExecutionPolicy Bypass -File install-windows.ps1
#>
param(
    [string]$ApiUrl = $env:SERVERPULSE_URL,
    [string]$ApiKey = $env:SERVERPULSE_KEY
)

$ErrorActionPreference = "Stop"

$InstallDir  = "C:\ProgramData\ServerPulse"
$AgentPath   = "$InstallDir\agent.py"
$ConfPath    = "$InstallDir\agent.conf"
$LogPath     = "$InstallDir\agent.log"
$GithubBase  = "https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent"
$TaskName    = "ServerPulseAgent"

# All module files that must be present alongside agent.py
$ModuleFiles = @(
    "client/__init__.py",
    "client/api.py",
    "models/__init__.py",
    "models/constants.py",
    "services/__init__.py",
    "services/config_applier.py",
    "services/linux.py",
    "services/darwin.py",
    "services/windows.py",
    "utils/__init__.py",
    "utils/config.py",
    "utils/logging.py"
)

function Write-Info  { param($msg) Write-Host "[INFO]  $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "[WARN]  $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "[ERROR] $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "ServerPulse Agent Installer for Windows" -ForegroundColor Cyan
Write-Host "─────────────────────────────────────────────"

# ── 1. Find Python ────────────────────────────────────────────────────────────
Write-Info "Looking for Python 3.6+..."

$PythonExe = $null
$candidates = @("python", "python3", "py")
foreach ($cmd in $candidates) {
    try {
        $ver = & $cmd -c "import sys; v=sys.version_info; print('{}.{}'.format(v.major,v.minor))" 2>$null
        $ok  = & $cmd -c "import sys; sys.exit(0 if sys.version_info>=(3,6) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $PythonExe = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
            Write-Info "Found Python $ver at $PythonExe"
            break
        }
    } catch { }
}

if (-not $PythonExe) {
    Write-Err "Python 3.6+ not found."
    Write-Err "Download from https://www.python.org/downloads/ and re-run this installer."
    exit 1
}

# ── 2. Create install directory ───────────────────────────────────────────────
Write-Info "Creating $InstallDir ..."
foreach ($sub in @("", "\client", "\models", "\services", "\utils")) {
    New-Item -ItemType Directory -Force -Path "$InstallDir$sub" | Out-Null
}

# ── 3. Download agent files ───────────────────────────────────────────────────
Write-Info "Downloading agent files ..."
try {
    Invoke-WebRequest -Uri "$GithubBase/agent.py" -OutFile $AgentPath -UseBasicParsing
    foreach ($mod in $ModuleFiles) {
        $dest = "$InstallDir\" + $mod.Replace("/", "\")
        Invoke-WebRequest -Uri "$GithubBase/$mod" -OutFile $dest -UseBasicParsing
    }
    Write-Info "Agent files downloaded to $InstallDir"
} catch {
    Write-Err "Download failed: $_"
    exit 1
}

# ── 4. Config (params / env vars or interactive) ──────────────────────────────
$ApiUrl = $ApiUrl.TrimEnd("/")

if ($ApiUrl -and $ApiKey) {
    Write-Info "Using API URL and API Key from parameters / environment variables."
} else {
    Write-Host ""
    Write-Host "Please enter your ServerPulse configuration:" -ForegroundColor White

    if (-not $ApiUrl) {
        do {
            $ApiUrl = Read-Host "  API URL (e.g. https://api.yourdomain.com)"
            $ApiUrl = $ApiUrl.TrimEnd("/")
            if (-not ($ApiUrl -match "^https?://")) {
                Write-Warn "URL must start with http:// or https://"
            }
        } while (-not ($ApiUrl -match "^https?://"))
    } else {
        Write-Info "Using API URL from parameter: $ApiUrl"
    }

    if (-not $ApiKey) {
        do {
            $ApiKeySecure = Read-Host "  API Key (sp_live_...)" -AsSecureString
            $ApiKeyBSTR   = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($ApiKeySecure)
            $ApiKey       = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($ApiKeyBSTR)
            [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ApiKeyBSTR)
            if ($ApiKey.Length -lt 8) {
                Write-Warn "API key seems too short. Please try again."
            }
        } while ($ApiKey.Length -lt 8)
    } else {
        Write-Info "Using API Key from parameter."
    }
}

# ── 5. Write config ───────────────────────────────────────────────────────────
$ConfContent = @"
[serverpulse]
api_url = $ApiUrl
api_key = $ApiKey
"@
Set-Content -Path $ConfPath -Value $ConfContent -Encoding UTF8

# Restrict config file permissions to current user only
try {
    $acl = Get-Acl $ConfPath
    $acl.SetAccessRuleProtection($true, $false)  # disable inheritance, remove inherited rules
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
        "FullControl",
        "Allow"
    )
    $acl.AddAccessRule($rule)
    Set-Acl -Path $ConfPath -AclObject $acl
    Write-Info "Config written to $ConfPath (restricted permissions)"
} catch {
    Write-Warn "Could not restrict config permissions: $_"
    Write-Info "Config written to $ConfPath"
}

# ── 6. Register Scheduled Task ────────────────────────────────────────────────
Write-Info "Registering Scheduled Task '$TaskName' (runs every minute)..."

# Remove existing task if present
try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
} catch { }

$action   = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$AgentPath`" --config `"$ConfPath`""

# Trigger: repeat every 1 minute indefinitely
$trigger  = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1)

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "ServerPulse monitoring agent – sends system metrics every minute." `
        -Force | Out-Null
    Write-Info "Scheduled Task '$TaskName' created (runs as SYSTEM, every minute)."
} catch {
    Write-Warn "Could not register as SYSTEM. Trying current user..."
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -RunLevel Highest `
        -Description "ServerPulse monitoring agent – sends system metrics every minute." `
        -Force | Out-Null
    Write-Info "Scheduled Task '$TaskName' created (runs as current user, every minute)."
}

# ── 7. First test run (dry-run) ───────────────────────────────────────────────
Write-Host ""
Write-Info "Running first test (dry-run, no HTTP request)..."
Write-Host "─────────────────────────────────────────────"
try {
    & $PythonExe $AgentPath --dry-run --config $ConfPath
} catch {
    Write-Warn "Dry-run produced an error: $_"
}
Write-Host "─────────────────────────────────────────────"

Write-Host ""
Write-Info "Installation complete!"
Write-Info "The agent will run every minute via Scheduled Tasks."
Write-Info "Logs: $LogPath"
Write-Host ""
Write-Host "To run the agent manually now (sends real data):" -ForegroundColor White
Write-Host "  & `"$PythonExe`" `"$AgentPath`" --config `"$ConfPath`"" -ForegroundColor Gray
Write-Host ""
Write-Host "To view the Scheduled Task:" -ForegroundColor White
Write-Host "  Get-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Gray
Write-Host ""
