# ServerMetry Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)]()

Lightweight monitoring agent for [ServerMetry](https://github.com/FloBaMedia/Monitoring-API). Collects system metrics (CPU, RAM, disk, network, processes) and ships them to the ServerMetry API once per minute.

**Zero external dependencies** — pure Python 3.8+ standard library only.

---

## Features

- CPU, memory, disk, swap, network I/O, and process metrics
- Linux, macOS, and Windows support
- One-line install via curl / PowerShell
- Non-interactive install via environment variables (for automated deployments)
- Remote server configuration: timezone, locale, NTP, DNS, reporting interval
- Agent auto-update from GitHub (opt-in, controlled via the dashboard)

---

## Quick Install

### Linux / macOS

**Important:** Always download and inspect scripts before running them with elevated privileges.

```bash
# 1. Download the installer
curl -fsSL https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent/install.sh -o /tmp/install.sh

# 2. Review the script before running
cat /tmp/install.sh

# 3. Make it executable and run with your credentials
chmod +x /tmp/install.sh
SERVERMETRY_URL=https://your-api.example.com SERVERMETRY_KEY=sp_live_... sudo /tmp/install.sh
```

Or interactive (prompts for API URL and key):

```bash
curl -fsSL https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent/install.sh -o /tmp/install.sh
chmod +x /tmp/install.sh
sudo /tmp/install.sh
```

### Windows (PowerShell, run as Administrator)

```powershell
# 1. Download the installer
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent/install-windows.ps1' -OutFile $env:TEMP\install-servermetry.ps1

# 2. Review the script before running
Get-Content $env:TEMP\install-servermetry.ps1

# 3. Execute with your credentials
& $env:TEMP\install-servermetry.ps1 -ApiUrl "https://your-api.example.com" -ApiKey "sp_live_..."
```

Install Python **for all users** (not just the current account) so the Scheduled Task running as `SYSTEM` can execute it.

> **Tip:** The ServerMetry dashboard generates the exact command with your API key pre-filled after you add a new server.

---

## Installation Details

| | Linux / macOS | Windows |
|---|---|---|
| **Install dir** | `/etc/servermetry/` | `C:\ProgramData\ServerMetry\` |
| **Config file** | `/etc/servermetry/agent.conf` | `C:\ProgramData\ServerMetry\agent.conf` |
| **Log file** | `/var/log/servermetry-agent.log` | `C:\ProgramData\ServerMetry\agent.log` |
| **Scheduler** | crontab (`* * * * *`) | Windows Scheduled Task (every 1 min) |
| **Runs as** | root (via sudo) | SYSTEM |

The installer:
1. Detects Python 3.8+
2. Downloads `agent.py` from this repository
3. Writes the config file with your API URL and key
4. Registers the scheduler entry
5. Runs a dry-run test to verify everything works

---

## Configuration

### Config file

```ini
[servermetry]
api_url = https://your-api.example.com
api_key  = sp_live_...
debug    = false
```

### Environment variables

| Variable | Description |
|---|---|
| `SERVERMETRY_API_URL` | API base URL |
| `SERVERMETRY_API_KEY` | Server API key |
| `SERVERMETRY_DEBUG` | Set to `1` to enable debug logging |

Legacy `SERVERPULSE_*` names are still accepted.

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
| `enableAutoUpdates` | Enables automatic agent self-update from GitHub |

All remote config settings can be managed from the **Config tab** in the ServerMetry dashboard.

---

## Auto-Update

When `enableAutoUpdates` is `true` in the server config, the agent checks for a newer version on every run (at most once per hour):

1. Resolves the latest version via the [GitHub Releases API](https://api.github.com/repos/FloBaMedia/Monitoring-Client/releases/latest) (falls back to parsing `AGENT_VERSION` from `constants.py` on `main` if the Releases API is unavailable)
2. Compares it with the running `AGENT_VERSION`
3. If newer: downloads every agent file from the matching version tag (`vX.Y.Z`, falling back to `main` per-file if a tag is missing a file) into a staging directory
4. Validates every `.py` file with `ast.parse` — if **any** file fails to fetch or parse, the update is aborted and nothing is changed (no partial update)
5. Backs up the current agent tree, then atomically swaps in the staged files; on failure, the backup is restored automatically
6. The new version takes effect on the next scheduled run

If the download or validation fails, the agent logs a warning/error and continues running the current version.

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
│   ├── constants.py          # Shared constants (version, defaults)
│   └── limits.py             # Magic numbers and timeout limits
├── services/
│   ├── linux.py              # Linux metric collectors
│   ├── windows.py            # Windows metric collectors
│   ├── darwin.py             # macOS metric collectors
│   ├── config_applier.py     # Applies remote config to the local system
│   └── updater.py            # Agent self-update logic
└── utils/
    ├── config.py             # Config file loading and interactive setup
    ├── logging.py            # File-based logger with rotation
    ├── validation.py         # Input validation (command injection prevention)
    ├── lock.py               # File locking with stale detection
    └── snapshot.py           # CPU snapshot persistence (cross-platform)
```

---

## Requirements

- Python 3.8 or newer
- No external packages — standard library only
- Root / Administrator privileges for install and for applying system settings (timezone, DNS, NTP)

---

## Uninstall

### Linux / macOS

```bash
sudo bash /etc/servermetry/uninstall.sh
```

### Windows (PowerShell, run as Administrator)

```powershell
powershell -ExecutionPolicy Bypass -File "C:\ProgramData\ServerMetry\uninstall-windows.ps1"
```

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Test your changes: `bash agent/run-local.sh --dry-run`
4. Open a pull request

Please maintain the zero-dependency constraint and Python 3.8 compatibility.

---

## License

[MIT](LICENSE) © FloBaMedia
