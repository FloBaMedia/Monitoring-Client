# ServerPulse Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.6+](https://img.shields.io/badge/python-3.6%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)]()

Lightweight monitoring agent for [ServerPulse](https://github.com/FloBaMedia/Monitoring-API). Collects system metrics (CPU, RAM, disk, network, processes) and ships them to the ServerPulse API once per minute.

**Zero external dependencies** — pure Python 3.6+ standard library only.

---

## Features

- CPU, memory, disk, swap, network I/O, and process metrics
- Linux, macOS, and Windows support
- One-line install via curl / PowerShell
- Non-interactive install via environment variables (for automated deployments)
- Remote server configuration: timezone, locale, NTP, DNS, reporting interval
- Agent auto-update from GitHub (opt-in, controlled via the dashboard)
- Extra commands executed after each metrics report

---

## Quick Install

### Linux / macOS

```bash
curl -sSL https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent/install.sh | \
  SERVERPULSE_URL=https://your-api.example.com SERVERPULSE_KEY=sp_live_... bash
```

Or interactive (prompts for API URL and key):

```bash
curl -sSL https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent/install.sh | sudo bash
```

### Windows (PowerShell, run as Administrator)

```powershell
$env:SERVERPULSE_URL='https://your-api.example.com'
$env:SERVERPULSE_KEY='sp_live_...'
iex (iwr -useb 'https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent/install-windows.ps1').Content
```

Or with explicit parameters:

```powershell
powershell -ExecutionPolicy Bypass -File install-windows.ps1 `
  -ApiUrl "https://your-api.example.com" `
  -ApiKey "sp_live_..."
```

> **Tip:** The ServerPulse dashboard generates the exact command with your API key pre-filled after you add a new server.

---

## Installation Details

| | Linux / macOS | Windows |
|---|---|---|
| **Install dir** | `/etc/serverpulse/` | `C:\ProgramData\ServerPulse\` |
| **Config file** | `/etc/serverpulse/agent.conf` | `C:\ProgramData\ServerPulse\agent.conf` |
| **Log file** | `/var/log/serverpulse-agent.log` | `C:\ProgramData\ServerPulse\agent.log` |
| **Scheduler** | crontab (`* * * * *`) | Windows Scheduled Task (every 1 min) |
| **Runs as** | root (via sudo) | SYSTEM |

The installer:
1. Detects Python 3.6+
2. Downloads `agent.py` from this repository
3. Writes the config file with your API URL and key
4. Registers the scheduler entry
5. Runs a dry-run test to verify everything works

---

## Configuration

### Config file

```ini
[serverpulse]
api_url = https://your-api.example.com
api_key  = sp_live_...
debug    = false
```

### Environment variables

| Variable | Description |
|---|---|
| `SERVERPULSE_API_URL` | API base URL |
| `SERVERPULSE_API_KEY` | Server API key |
| `SERVERPULSE_DEBUG` | Set to `1` to enable debug logging |

Environment variables take priority over the config file.

### Remote configuration

The agent fetches its configuration from `GET /api/v1/agent/config` on every run and applies the following settings locally:

| Field | Effect |
|---|---|
| `timezone` | Sets system timezone (`timedatectl` / `Set-TimeZone`) |
| `locale` | Sets system locale (`localectl`) |
| `customNtp` | Configures NTP server (`timesyncd.conf` / `w32tm`) |
| `customDns` | Updates DNS servers (`/etc/resolv.conf` / `Set-DnsClientServerAddress`) |
| `reportIntervalSeconds` | Updates the cron / scheduled task interval |
| `extraCommands` | Executes commands after each metrics report |
| `enableAutoUpdates` | Enables automatic agent self-update from GitHub |

All remote config settings can be managed from the **Config tab** in the ServerPulse dashboard.

---

## Auto-Update

When `enableAutoUpdates` is `true` in the server config, the agent checks GitHub for a newer version on every run:

1. Downloads `agent.py` from this repository
2. Compares `AGENT_VERSION` with the running version
3. If newer: backs up the current file (`agent.py.bak`), validates the download (Python syntax check), then replaces in-place
4. The new version takes effect on the next scheduled run

If the download fails or is invalid, the agent logs a warning and continues with the current version.

---

## CLI Reference

```
python agent.py                          # collect metrics and POST to API
python agent.py --dry-run                # print collected metrics as JSON, no HTTP
python agent.py --config /path/to.conf  # override config file path
python agent.py --apply-template <id>   # fetch and execute a server script template
python agent.py --no-apply-config       # skip fetching remote config
python agent.py --debug                 # verbose logging to stderr
```

---

## Local Development

```bash
# Clone
git clone https://github.com/FloBaMedia/Monitoring-Client.git
cd Monitoring-Client/agent

# Single dry run (no HTTP)
bash run-local.sh --dry-run

# Single real run (requires valid API key in agent.conf or env vars)
bash run-local.sh

# Watch mode — reruns every 10 seconds
bash run-local.sh --watch --interval 10

# Windows equivalent
.\run-local.ps1 --dry-run
```

The local runner uses `--debug` by default so output appears in the terminal. If a local `agent.conf` is found next to the script it is used automatically; otherwise the agent falls back to the system config path or prompts interactively.

---

## Repository Structure

```
agent/
├── agent.py                  # Entry point and CLI argument handling
├── install.sh                # Linux/macOS installer
├── install-windows.ps1       # Windows installer
├── uninstall.sh              # Linux/macOS uninstaller
├── uninstall-windows.ps1     # Windows uninstaller
├── run-local.sh              # Local development runner (Linux/macOS)
├── run-local.ps1             # Local development runner (Windows)
├── client/
│   └── api.py                # HTTP client for all API calls
├── models/
│   └── constants.py          # Shared constants (version, defaults)
├── services/
│   ├── linux.py              # Linux/macOS metric collectors
│   ├── windows.py            # Windows metric collectors
│   ├── config_applier.py     # Applies remote config to the local system
│   └── updater.py            # Agent self-update logic
└── utils/
    ├── config.py             # Config file loading and interactive setup
    └── logging.py            # File-based logger with rotation
```

---

## Requirements

- Python 3.6 or newer
- No external packages — standard library only
- Root / Administrator privileges for install and for applying system settings (timezone, DNS, NTP)

---

## Uninstall

### Linux / macOS

```bash
sudo bash /etc/serverpulse/uninstall.sh
```

### Windows (PowerShell, run as Administrator)

```powershell
powershell -ExecutionPolicy Bypass -File "C:\ProgramData\ServerPulse\uninstall-windows.ps1"
```

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Test your changes: `bash agent/run-local.sh --dry-run`
4. Open a pull request

Please maintain the zero-dependency constraint and Python 3.6 compatibility.

---

## License

[MIT](LICENSE) © FloBaMedia
