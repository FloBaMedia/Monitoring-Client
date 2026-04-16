# ServerPulse – Monitoring Client (Agent)

> Lightweight, dependency-free system monitoring agent. Collects CPU, memory, disk, network and process metrics every minute and ships them to the ServerPulse API.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Goals & Non-Goals](#goals--non-goals)
3. [Architecture](#architecture)
4. [Technology Decision](#technology-decision)
5. [Repository Structure](#repository-structure)
6. [Configuration](#configuration)
7. [Collected Metrics](#collected-metrics)
8. [Agent Behavior](#agent-behavior)
9. [Installation](#installation)
   - [Linux (automated)](#linux-automated)
   - [Linux (manual)](#linux-manual)
   - [Windows](#windows)
10. [Local Development & Testing](#local-development--testing)
11. [Debug Mode](#debug-mode)
12. [Logging](#logging)
13. [Security](#security)
14. [API Reference](#api-reference)
15. [Design Rules](#design-rules)

---

## Project Overview

The **ServerPulse Monitoring Client** is the agent component of the ServerPulse platform. It runs on each monitored server, collects system metrics at a configurable interval, and POSTs them to the central ServerPulse API.

The agent is intentionally minimal:
- **Single Python file** — `agent.py` is the entire agent. No packages, no build step, no virtual environment.
- **Zero external dependencies** — only Python 3.6+ standard library (`json`, `urllib`, `configparser`, `subprocess`, `platform`, `os`, `ssl`).
- **Stateless** — each execution is independent. State is managed by the scheduler (cron / Scheduled Task), not the agent.
- **Cross-platform** — runs on any Linux distribution with Python 3.6+ and on Windows.

---

## Goals & Non-Goals

### Goals

- Collect accurate, low-overhead system metrics once per minute
- Work on any modern Linux server without installing anything extra
- Support Windows as a first-class platform
- Be auditable — a sysadmin can read and understand the entire agent in 10 minutes
- Self-configure on first run via an interactive setup wizard
- Validate configuration on every startup and prompt for missing values
- Ship a single POST per run — no persistent connections, no daemon

### Non-Goals

- **Not a daemon** — the agent does not run in the background itself. Use cron or Scheduled Tasks.
- **Not a metrics aggregator** — raw per-minute data is sent to the API; aggregation happens server-side.
- **Not a log shipper** — only system metrics are collected, not application logs.
- **No sub-minute resolution** — the minimum interval is 1 minute (cron limitation). For finer granularity, use a systemd timer.
- **No alert logic** — thresholds and alerting are handled by the API/dashboard, not the agent.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Monitored Server                        │
│                                                              │
│   Cron / Scheduled Task (every 1 min)                        │
│          │                                                   │
│          ▼                                                   │
│   ┌─────────────────────────────────────────────────┐        │
│   │                   agent.py                      │        │
│   │                                                 │        │
│   │  1. load_config()   ← agent.conf / ENV vars     │        │
│   │  2. ensure_config() ← prompt if incomplete      │        │
│   │  3. collect_*_metrics()                         │        │
│   │       ├── /proc/stat  (CPU delta, 100ms sleep)  │        │
│   │       ├── /proc/meminfo                         │        │
│   │       ├── /proc/mounts + os.statvfs()           │        │
│   │       ├── /proc/net/dev                         │        │
│   │       ├── /proc/diskstats (IO delta)            │        │
│   │       └── /proc/uptime, /etc/os-release, …      │        │
│   │  4. post_metrics()  → HTTPS POST                │        │
│   │  5. log result      → agent.log                 │        │
│   └─────────────────────────────────────────────────┘        │
│                          │                                   │
└──────────────────────────┼───────────────────────────────────┘
                           │  HTTPS  POST /api/v1/agent/metrics
                           │  Header: X-Server-Key: <api_key>
                           ▼
                  ┌─────────────────┐
                  │  ServerPulse API │
                  │  (separate repo) │
                  └─────────────────┘
```

### Execution Model

Each cron invocation is a **complete, independent run**:

```
start → config → metrics → POST → log → exit
```

No state is kept between runs. The 100 ms delta measurement for CPU usage and disk I/O (reading `/proc` twice with a short sleep) is the only blocking operation; everything else is a single file read.

---

## Technology Decision

| Criterion              | Bash | **Python 3** | Go Binary | Node.js |
|------------------------|------|--------------|-----------|---------|
| Pre-installed on Linux | ✅   | ✅           | ❌        | ❌      |
| Windows support        | ❌   | ✅           | ✅        | ✅      |
| No external deps       | ✅   | ✅           | ✅        | ❌      |
| JSON handling          | ✗    | ✅           | ✅        | ✅      |
| Complex system queries | fragile | ✅        | ✅        | ✅      |
| Single file            | ✅   | ✅           | ❌ (build)| ❌      |

**Decision: Python 3 (stdlib only)**

Python 3.6+ is pre-installed on all modern Linux distributions:

| Distro          | Status |
|-----------------|--------|
| Ubuntu 18.04+   | ✅     |
| Debian 9+       | ✅     |
| CentOS 7+       | ✅ (`python3` or `python36`) |
| RHEL 8+         | ✅     |
| Fedora / Arch   | ✅     |
| Alpine          | ✅ (`apk add python3`) |
| Windows         | ✅ (installable, WMIC-based metrics) |

---

## Repository Structure

```
Monitoring-Client/
├── agent/
│   ├── agent.py              ← The entire agent (single file, ~900 lines)
│   ├── install.sh            ← Linux one-liner installer
│   └── install-windows.ps1   ← Windows PowerShell installer
├── SERVERPULSE-AGENT-PROMPT.md  ← Original implementation specification
└── README.md
```

### agent.py – Internal Structure

The file is divided into 7 clearly labeled sections:

| Section | Content |
|---------|---------|
| 1 – Constants | `AGENT_VERSION`, `DEFAULT_API_URL`, `REQUIRED_FIELDS`, filesystem/disk filters |
| 2 – Logging | `log_write()`, `log_debug()`, log rotation |
| 3 – Config Loading | `load_config()`, `ensure_config()`, `_save_config()` |
| 4 – Linux Metrics | One collector function per metric, single `_sample_proc_delta()` with the 100 ms sleep |
| 5 – Windows Metrics | `_wmic()` helper + per-metric collectors, PowerShell fallback |
| 6 – HTTP POST | `post_metrics()` via `urllib.request` |
| 7 – Entry Point | `parse_args()`, `main()` |

---

## Configuration

### Config File

Config is searched in this priority order:

| Priority | Path |
|----------|------|
| 1 | `SERVERPULSE_API_URL` + `SERVERPULSE_API_KEY` environment variables |
| 2 | `C:\ProgramData\ServerPulse\agent.conf` (Windows) |
| 3 | `/etc/serverpulse/agent.conf` (Linux, system-wide / root) |
| 4 | `~/.config/serverpulse/agent.conf` (Linux, user-level) |
| 5 | `./agent.conf` (same directory as `agent.py`, fallback) |

Override with `--config /path/to/agent.conf`.

### Format

```ini
[serverpulse]
api_url = https://api.yourdomain.com
api_key = sp_live_xxxxxxxxxxxxxxxxxxxxxxxx
debug   = false
```

| Key       | Required | Default                       | Description |
|-----------|----------|-------------------------------|-------------|
| `api_url` | Yes      | `https://api.yourdomain.com`  | Base URL of the ServerPulse API |
| `api_key` | Yes      | —                             | Server-specific API key (`sp_live_…`) |
| `debug`   | No       | `false`                       | Enable verbose debug logging |

### First-Run Setup Wizard

If a required field is missing (including the very first run with no config at all), the agent **prompts interactively** when attached to a terminal:

```
Config found at /etc/serverpulse/agent.conf but missing fields:

  API Key (sp_live_...): ········

  ✓ Config saved to /etc/serverpulse/agent.conf
```

The API key is entered as a hidden password (no echo). The config file is written with `chmod 600` on Linux.

When running non-interactively (cron, CI, piped input), missing config causes an immediate `ERROR` log entry and `exit 1`.

### Environment Variables

```bash
export SERVERPULSE_API_URL=https://api.yourdomain.com
export SERVERPULSE_API_KEY=sp_live_xxx
export SERVERPULSE_DEBUG=true   # optional
```

Environment variables take priority over all config files.

---

## Collected Metrics

### Linux

| Field | Source | Notes |
|-------|--------|-------|
| `cpuUsagePercent` | `/proc/stat` | Two reads 100 ms apart → delta calculation |
| `cpuCores` | `/proc/cpuinfo` | Count of `processor` entries |
| `loadAvg1/5/15` | `/proc/loadavg` | First three fields |
| `memTotalMb` | `/proc/meminfo` `MemTotal` | In MiB |
| `memUsedMb` | `MemTotal − MemAvailable` | Excludes cache/buffers |
| `memUsagePercent` | Derived | `usedMb / totalMb × 100` |
| `swapTotalMb` | `/proc/meminfo` `SwapTotal` | |
| `swapUsedMb` | `SwapTotal − SwapFree` | |
| `diskUsages[]` | `os.statvfs()` on `/proc/mounts` | Skips: tmpfs, devtmpfs, sysfs, proc, overlay, squashfs, … |
| `networkInterfaces[]` | `/proc/net/dev` | rx/tx bytes + packets per interface (excludes `lo`) |
| `processCount` | Count of numeric dirs in `/proc/` | |
| `openFiles` | `/proc/sys/fs/file-nr` field 0 | |
| `ioReadKbps` | `/proc/diskstats` | Delta over 100 ms, only `sd*`/`nvme*`/`vd*` whole disks |
| `ioWriteKbps` | `/proc/diskstats` | Same |
| `os` | `/etc/os-release` `PRETTY_NAME` | |
| `kernelVersion` | `platform.release()` | |
| `uptimeSeconds` | `/proc/uptime` field 0 | |
| `agentVersion` | Hardcoded constant | Currently `1.0.0` |

**CPU & IO delta:** Both measurements share a single 100 ms sleep — `/proc/stat` and `/proc/diskstats` are both read before and after the same sleep. Total blocking time per run: ~100 ms.

### Windows

| Field | Source | Notes |
|-------|--------|-------|
| `cpuUsagePercent` | `wmic cpu get LoadPercentage` | Falls back to PowerShell `Get-WmiObject` |
| `cpuCores` | `wmic cpu get NumberOfCores` | |
| `loadAvg1/5/15` | — | Always `0.0` (not available on Windows) |
| `memTotalMb` / `memUsedMb` | `wmic OS get TotalVisibleMemorySize,FreePhysicalMemory` | |
| `swapTotalMb` / `swapUsedMb` | `wmic OS get TotalVirtualMemorySize,FreeVirtualMemory` | Virtual memory minus physical |
| `diskUsages[]` | `wmic logicaldisk get DeviceID,Size,FreeSpace,FileSystem` | Per drive letter |
| `networkInterfaces[]` | `wmic nic where NetEnabled=TRUE` → fallback `netstat -e` | WMIC property deprecated on Win11; `netstat -e` gives aggregate |
| `processCount` | `wmic process get processid` count | |
| `openFiles` | — | Always `0` (not available without additional tooling) |
| `ioReadKbps` / `ioWriteKbps` | — | Always `0.0` |
| `os` | `platform.version()` | |
| `kernelVersion` | `platform.release()` | |
| `uptimeSeconds` | `wmic os get LastBootUpTime` → delta to now | |

---

## Agent Behavior

### Execution Flow (per run)

```
1. Parse CLI args (--dry-run, --debug, --config)
2. load_config()       → read config file or env vars
3. ensure_config()     → check for missing required fields, prompt if needed
4. collect metrics     → platform-specific collectors
5. if --dry-run        → print JSON to stdout, exit 0
6. post_metrics()      → HTTPS POST to API
7. log result          → success (INFO) or failure (ERROR)
8. exit 0 or exit 1
```

### Error Handling

- Each individual metric collector is wrapped in `try/except`. A failed metric logs a `WARNING` and returns a safe default (`0` or `[]`). It never aborts the run.
- HTTP 4xx/5xx: logs `ERROR` with full response body (up to 2000 chars), returns `exit 1`.
- Network timeout/error: logs `ERROR`, returns `exit 1`.
- Missing config in non-interactive mode: logs `ERROR`, returns `exit 1`.
- Logging failures are silently swallowed — the agent never crashes due to a log write error.

### Execution Interval

**Recommended: every minute via cron**

```cron
* * * * * /usr/bin/python3 /etc/serverpulse/agent.py
```

For sub-minute resolution, use a systemd timer:

```ini
# /etc/systemd/system/serverpulse-agent.timer
[Timer]
OnBootSec=30s
OnUnitActiveSec=30s
```

---

## Installation

### Linux (automated)

```bash
curl -fsSL https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent/install.sh | sudo bash
```

The installer:
1. Checks for Python 3.6+ (`python3` or `python`)
2. Downloads `agent.py` to `/etc/serverpulse/agent.py`
3. Prompts for API URL and API Key
4. Writes `/etc/serverpulse/agent.conf` with `chmod 600`
5. Adds a crontab entry (idempotent — won't duplicate)
6. Runs a dry-run to verify the installation

### Linux (manual)

```bash
# 1. Create directory and download agent
sudo mkdir -p /etc/serverpulse
sudo curl -o /etc/serverpulse/agent.py \
  https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent/agent.py

# 2. Run the agent – it will prompt for config on first start
sudo python3 /etc/serverpulse/agent.py

# 3. Add crontab entry
(sudo crontab -l 2>/dev/null; \
  echo "* * * * * /usr/bin/python3 /etc/serverpulse/agent.py") | sudo crontab -
```

### Windows

Run PowerShell as Administrator:

```powershell
powershell -ExecutionPolicy Bypass -File install-windows.ps1
```

Or manually:

```powershell
# 1. Create directory and download agent
New-Item -ItemType Directory -Force -Path "C:\ProgramData\ServerPulse"
Invoke-WebRequest -Uri "https://.../agent.py" -OutFile "C:\ProgramData\ServerPulse\agent.py"

# 2. Run the agent – prompts for config on first start
python "C:\ProgramData\ServerPulse\agent.py"
```

The Windows installer creates a **Scheduled Task** (runs as SYSTEM, highest privilege, every minute, overlapping runs ignored).

---

## Local Development & Testing

No build step required. Clone the repo and run directly.

### 1. Create a local config

```ini
# agent/agent.conf
[serverpulse]
api_url = https://api.yourdomain.com
api_key = sp_live_your_key_here
```

### 2. Dry-run (no HTTP request, no log file)

```bash
python agent/agent.py --dry-run
```

Prints the full collected JSON payload to stdout. Log output goes to stderr.

### 3. Dry-run with debug output

```bash
python agent/agent.py --dry-run --debug
```

Shows which config file was loaded, all DEBUG messages, and the full payload before it would be sent.

### 4. Override config path

```bash
python agent/agent.py --dry-run --config agent/agent.conf
```

### 5. Live run

```bash
python agent/agent.py --config agent/agent.conf
```

Sends a real POST to the API. Requires valid `api_url` and `api_key`.

### CLI Flags

| Flag | Description |
|------|-------------|
| `--dry-run` | Collect metrics, print JSON, skip HTTP POST |
| `--debug` | Enable verbose debug logging to stderr |
| `--config <path>` | Override config file path |

---

## Debug Mode

Debug mode can be activated three ways (all equivalent):

```bash
# 1. CLI flag
python agent/agent.py --debug

# 2. Config file
# debug = true  (in agent.conf)

# 3. Environment variable
SERVERPULSE_DEBUG=true python agent/agent.py
```

**What debug mode shows:**

- Which config paths are being checked and which one was loaded
- Agent version, platform, and active flags at startup
- The full JSON payload and target URL before the HTTP POST
- `Metrics collected successfully` confirmation
- All `WARNING` / `ERROR` log messages printed to stderr in addition to the log file

---

## Logging

| Platform | Log file |
|----------|----------|
| Linux    | `/var/log/serverpulse-agent.log` |
| Windows  | `C:\ProgramData\ServerPulse\agent.log` |

**Log format:**

```
[2025-01-15 14:32:01] INFO    POST /api/v1/agent/metrics → 201 (0.23s)
[2025-01-15 14:32:01] WARNING disk: statvfs(/run/user/1000) failed: Permission denied
[2025-01-15 14:32:01] ERROR   POST /api/v1/agent/metrics → 401 (0.12s): {"error":"invalid key"}
[2025-01-15 14:32:01] DEBUG   Config loaded from /etc/serverpulse/agent.conf
```

**Rotation:** When the log file exceeds 1 MB it is renamed to `agent.log.1` (overwriting any previous backup) and a fresh `agent.log` is started. No external `logrotate` configuration needed.

**Dry-run / Debug:** Log lines are written to `stderr` instead of (or in addition to) the log file.

---

## Security

| Measure | Detail |
|---------|--------|
| Config file permissions | `chmod 600` on creation — only the owning user/root can read the API key |
| HTTPS only | `ssl.create_default_context()` — certificate validation is enforced, no option to disable |
| Request timeout | 10 seconds — the agent never hangs on an unresponsive API |
| API key isolation | The key is never passed to child processes via environment variables |
| API key input | Hidden password prompt (`getpass`) during interactive setup — key is never echoed |
| Agent privileges | Runs as root on Linux (required for full `/proc` access). A dedicated `serverpulse` user with `CAP_SYS_PTRACE` can be used as an alternative |

---

## API Reference

### Endpoint

```
POST {api_url}/api/v1/agent/metrics
```

### Headers

```
Content-Type:  application/json
X-Server-Key:  <api_key>
User-Agent:    ServerPulse-Agent/1.0.0
```

### Payload Schema

```jsonc
{
  // System
  "os":               "Ubuntu 22.04.3 LTS",
  "kernelVersion":    "5.15.0-91-generic",
  "uptimeSeconds":    86400,
  "agentVersion":     "1.0.0",

  // CPU
  "cpuUsagePercent":  23.5,
  "cpuCores":         4,
  "loadAvg1":         0.52,
  "loadAvg5":         0.31,
  "loadAvg15":        0.18,

  // Memory
  "memTotalMb":       8192,
  "memUsedMb":        4096,
  "memUsagePercent":  50.0,
  "swapTotalMb":      2048,
  "swapUsedMb":       512,

  // Disk (array, one entry per mounted filesystem)
  "diskUsages": [
    {
      "mountpoint":   "/",
      "totalGb":      100.0,
      "usedGb":       45.2,
      "usagePercent": 45.2,
      "filesystem":   "ext4"
    }
  ],

  // Network (array, one entry per interface, excluding loopback)
  "networkInterfaces": [
    {
      "name":       "eth0",
      "rxBytes":    1048576,
      "txBytes":    524288,
      "rxPackets":  8192,
      "txPackets":  4096
    }
  ],

  // Processes & I/O
  "processCount":   150,
  "openFiles":      1024,
  "ioReadKbps":     12.5,
  "ioWriteKbps":    8.3
}
```

**Side effect:** The API endpoint internally calls `updateServerSeen()` on every successful metrics POST, setting the server status to `ONLINE`. No separate heartbeat endpoint is required.

---

## Design Rules

These rules govern all changes to the agent:

1. **Single file** — `agent.py` must remain a single, self-contained file. No helper modules, no packages.

2. **Zero external dependencies** — only Python 3.6+ standard library. No `pip install` ever.

3. **Python 3.6 compatibility** — minimum target is Python 3.6. Avoid syntax or APIs introduced in later versions (e.g., `subprocess.run(capture_output=True)` requires 3.7 → use `subprocess.check_output` instead).

4. **Stateless** — the agent must not write any state between runs (no SQLite, no pickle, no counter files). The only write operations are the log file and the config file.

5. **No crash on metric failure** — every individual metric collector wraps its logic in `try/except` and returns a safe default. One broken metric must never abort the entire run.

6. **One sleep per run** — CPU and IO delta measurements share a single `_sample_proc_delta()` call with one `time.sleep(0.1)`. No additional sleeps anywhere.

7. **Secure by default** — config files are created with `chmod 600`, API keys are never echoed to the terminal, HTTPS certificate validation is always on.

8. **Non-interactive when unattended** — `ensure_config()` only prompts for input when `sys.stdin.isatty() and sys.stdout.isatty()`. All other paths exit cleanly with a log entry.

9. **Idempotent installation** — the crontab installer checks before adding an entry. Running the installer twice must not create duplicate entries.

10. **`DEFAULT_API_URL` is the single source of truth** — the default API URL lives only in the constant `DEFAULT_API_URL` at the top of `agent.py`. It is pre-filled during setup and never hard-coded elsewhere.
