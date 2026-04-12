#!/usr/bin/env python3
"""
ServerPulse Agent v1.0.0
Single-file Python 3.6+ monitoring agent. No external dependencies.
Usage:
  python agent.py                          # normal run (reads config, POSTs metrics)
  python agent.py --dry-run                # print collected metrics as JSON, no HTTP
  python agent.py --config /path/to.conf  # override config file path
"""

import configparser
import json
import os
import platform
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# ── SECTION 1: CONSTANTS ────────────────────────────────────────────────────

AGENT_VERSION = "1.0.0"
LOG_MAX_BYTES = 1_048_576  # 1 MB
DEFAULT_API_URL = "https://api.yourdomain.com"

SKIP_FILESYSTEMS = {
    "tmpfs", "devtmpfs", "sysfs", "proc", "cgroup", "cgroup2",
    "pstore", "bpf", "tracefs", "securityfs", "debugfs",
    "hugetlbfs", "mqueue", "fusectl", "configfs", "ramfs",
    "devpts", "overlay", "squashfs", "autofs", "rpc_pipefs",
}

# Only track whole physical disks (not partitions)
DISK_PREFIXES = ("sd", "nvme", "vd", "xvd", "hd")

# Global flags – set in main()
DRY_RUN = False
DEBUG = False


# ── SECTION 2: LOGGING ──────────────────────────────────────────────────────

def _get_log_path():
    if platform.system() == "Windows":
        return os.path.join("C:\\ProgramData\\ServerPulse", "agent.log")
    return "/var/log/serverpulse-agent.log"


def log_write(level, message):
    """Write a log line. In dry-run or debug mode also writes to stderr."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = "[{}] {:<7} {}\n".format(ts, level, message)

    if DRY_RUN or DEBUG:
        sys.stderr.write(line)

    if DRY_RUN:
        return

    log_path = _get_log_path()
    try:
        log_dir = os.path.dirname(log_path)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # Rotate if over limit
        if os.path.exists(log_path) and os.path.getsize(log_path) > LOG_MAX_BYTES:
            backup = log_path + ".1"
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(log_path, backup)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # Never crash on logging failure
        pass


def log_debug(message):
    """Write a DEBUG line – only when debug mode is active."""
    if DEBUG:
        log_write("DEBUG", message)


# ── SECTION 3: CONFIG LOADING ───────────────────────────────────────────────

# Fields that must be present. Each entry: key → (prompt label, default value, is_secret)
# default=None means the user must supply a value; default=str means it's pre-filled.
REQUIRED_FIELDS = [
    ("api_url", "API URL",              DEFAULT_API_URL, False),
    ("api_key", "API Key (sp_live_...)", None,           True),
]


def _default_conf_path():
    """Return the preferred writable config path for the current platform/user."""
    if platform.system() == "Windows":
        return "C:\\ProgramData\\ServerPulse\\agent.conf"
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return "/etc/serverpulse/agent.conf"
    return os.path.expanduser("~/.config/serverpulse/agent.conf")


def _conf_search_paths(override_path=None):
    if override_path:
        return [override_path]
    return [
        "C:\\ProgramData\\ServerPulse\\agent.conf",
        "/etc/serverpulse/agent.conf",
        os.path.expanduser("~/.config/serverpulse/agent.conf"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.conf"),
    ]


def load_config(override_path=None):
    """
    Returns (values_dict, conf_path) where values_dict contains whatever keys
    were found (may be incomplete). conf_path is the file that was read, or None
    (env vars) or '' (no file found).

    Priority: ENV vars > config files.
    """
    values = {}

    # 1. Environment variables
    env_url   = os.environ.get("SERVERPULSE_API_URL", "").strip()
    env_key   = os.environ.get("SERVERPULSE_API_KEY", "").strip()
    env_debug = os.environ.get("SERVERPULSE_DEBUG", "").strip().lower() in ("1", "true", "yes")
    if env_url:
        values["api_url"] = env_url.rstrip("/")
    if env_key:
        values["api_key"] = env_key
    if env_debug:
        values["debug"] = True
    if env_url and env_key:
        log_debug("Config loaded from environment variables")
        return values, None  # None = env, no file to patch

    # 2. Config files
    cfg = configparser.ConfigParser()
    for path in _conf_search_paths(override_path):
        log_debug("Checking config path: {}".format(path))
        if not os.path.exists(path):
            continue
        try:
            cfg.read(path, encoding="utf-8")
            sec = "serverpulse"
            if cfg.has_section(sec):
                for key, _, _, _ in REQUIRED_FIELDS:
                    val = cfg.get(sec, key, fallback="").strip()
                    if val:
                        values[key] = val.rstrip("/") if key == "api_url" else val
                values["debug"] = cfg.get(sec, "debug", fallback="false").strip().lower() in ("1", "true", "yes")
                log_debug("Config loaded from {}".format(path))
                return values, path
        except Exception as e:
            log_write("WARNING", "Could not read config {}: {}".format(path, e))

    return values, ""  # '' = no file found yet


def _save_config(path, values):
    """Write all known values back to the config file."""
    try:
        conf_dir = os.path.dirname(path)
        if conf_dir and not os.path.exists(conf_dir):
            os.makedirs(conf_dir)
        cfg = configparser.ConfigParser()
        # Preserve existing keys we don't know about
        if os.path.exists(path):
            cfg.read(path, encoding="utf-8")
        if not cfg.has_section("serverpulse"):
            cfg.add_section("serverpulse")
        for key, val in values.items():
            if key == "debug":
                cfg.set("serverpulse", "debug", "true" if val else "false")
            else:
                cfg.set("serverpulse", key, str(val))
        with open(path, "w", encoding="utf-8") as f:
            cfg.write(f)
        if not platform.system() == "Windows":
            os.chmod(path, 0o600)
        log_debug("Config saved to {}".format(path))
    except Exception as e:
        log_write("WARNING", "Could not save config to {}: {}".format(path, e))


def ensure_config(values, conf_path, override_path=None):
    """
    Check that all REQUIRED_FIELDS are present. Prompt for any that are missing,
    then save the updated config back to the file.
    Returns the completed values dict (guaranteed to have all required keys).
    Exits if non-interactive and values are still missing.
    """
    import getpass

    missing = [
        (key, label, default, secret)
        for key, label, default, secret in REQUIRED_FIELDS
        if not values.get(key)
    ]

    if not missing:
        return values  # nothing to do

    # conf_path=None means env vars were used – we can't patch those, just error out
    if conf_path is None:
        log_write("ERROR", "Environment variables set but missing: {}".format(
            ", ".join(k for k, *_ in missing)))
        sys.exit(1)

    # Determine target file for saving
    save_path = conf_path if conf_path else (override_path or _default_conf_path())

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive:
        log_write("ERROR", "Config incomplete. Missing fields: {}. "
                  "Add them to {} or set env vars.".format(
                      ", ".join(k for k, *_ in missing), save_path))
        sys.exit(1)

    # Interactive prompt for missing fields
    print("")
    if conf_path:
        print("Config found at {} but missing fields:".format(conf_path))
    else:
        print("No configuration found. Let's set it up now.")
        print("Config will be saved to: {}".format(save_path))
    print("")

    for key, label, default, secret in missing:
        while True:
            try:
                if secret:
                    entered = getpass.getpass("  {}: ".format(label)).strip()
                elif default:
                    entered = input("  {} [{}]: ".format(label, default)).strip()
                    if not entered:
                        entered = default
                else:
                    entered = input("  {}: ".format(label)).strip()
            except (KeyboardInterrupt, EOFError):
                print("\nSetup cancelled.")
                sys.exit(1)

            if entered:
                values[key] = entered.rstrip("/") if key == "api_url" else entered
                break
            print("  ✗ This field is required.")

    _save_config(save_path, values)
    print("")
    print("  ✓ Config saved to {}".format(save_path))
    print("")

    return values


# ── SECTION 4: LINUX METRIC COLLECTORS ─────────────────────────────────────

def _parse_proc_stat():
    """Read /proc/stat and return the aggregate CPU line fields as ints."""
    try:
        with open("/proc/stat", "r") as f:
            for line in f:
                if line.startswith("cpu "):
                    parts = line.split()
                    return [int(x) for x in parts[1:]]
    except Exception:
        pass
    return None


def _parse_proc_diskstats():
    """
    Read /proc/diskstats and return dict of {devname: (sectors_read, sectors_written)}.
    Only includes whole physical disks (no partitions).
    """
    result = {}
    try:
        with open("/proc/diskstats", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 10:
                    continue
                devname = parts[2]
                # Filter: must start with a known prefix
                if not any(devname.startswith(p) for p in DISK_PREFIXES):
                    continue
                # Skip partitions: sd* → no trailing digit after alpha part; nvme* → no 'p' suffix
                if devname.startswith("nvme"):
                    # nvme0n1 = whole disk, nvme0n1p1 = partition
                    if "p" in devname.split("n")[-1]:
                        continue
                elif devname[-1].isdigit():
                    # sda1, sdb2, etc. are partitions
                    continue
                sectors_read = int(parts[5])
                sectors_written = int(parts[9])
                result[devname] = (sectors_read, sectors_written)
    except Exception:
        pass
    return result


def _calc_cpu_delta(snap0, snap1):
    """Calculate CPU usage % from two /proc/stat snapshots."""
    if snap0 is None or snap1 is None or len(snap0) < 4 or len(snap1) < 4:
        return 0.0
    # Fields: user nice system idle iowait irq softirq steal ...
    # idle + iowait = idle time
    idle0 = snap0[3] + (snap0[4] if len(snap0) > 4 else 0)
    idle1 = snap1[3] + (snap1[4] if len(snap1) > 4 else 0)
    total0 = sum(snap0)
    total1 = sum(snap1)
    delta_total = total1 - total0
    delta_idle = idle1 - idle0
    if delta_total <= 0:
        return 0.0
    return round((1.0 - delta_idle / delta_total) * 100.0, 2)


def _calc_io_delta(snap0, snap1, elapsed):
    """Calculate IO read/write kbps from two diskstats snapshots."""
    read_sectors = 0
    write_sectors = 0
    all_devs = set(snap0.keys()) | set(snap1.keys())
    for dev in all_devs:
        r0, w0 = snap0.get(dev, (0, 0))
        r1, w1 = snap1.get(dev, (0, 0))
        read_sectors += max(0, r1 - r0)
        write_sectors += max(0, w1 - w0)
    if elapsed <= 0:
        return 0.0, 0.0
    # 1 sector = 512 bytes → kbps
    read_kbps = round((read_sectors * 512 / 1024) / elapsed, 2)
    write_kbps = round((write_sectors * 512 / 1024) / elapsed, 2)
    return read_kbps, write_kbps


def _sample_proc_delta(interval=0.1):
    """
    Single 100ms sleep to capture CPU + IO deltas.
    Returns (cpu_percent, io_read_kbps, io_write_kbps).
    """
    snap0_cpu = _parse_proc_stat()
    snap0_disk = _parse_proc_diskstats()
    t0 = time.time()
    time.sleep(interval)
    snap1_cpu = _parse_proc_stat()
    snap1_disk = _parse_proc_diskstats()
    elapsed = time.time() - t0

    cpu_pct = _calc_cpu_delta(snap0_cpu, snap1_cpu)
    io_read, io_write = _calc_io_delta(snap0_disk, snap1_disk, elapsed)
    return cpu_pct, io_read, io_write


def _read_cpu_cores():
    try:
        count = 0
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("processor"):
                    count += 1
        return max(count, 1)
    except Exception as e:
        log_write("WARNING", "cpu_cores unavailable: {}".format(e))
        return 0


def _read_load_avg():
    try:
        with open("/proc/loadavg", "r") as f:
            parts = f.read().split()
            return float(parts[0]), float(parts[1]), float(parts[2])
    except Exception as e:
        log_write("WARNING", "loadavg unavailable: {}".format(e))
        return 0.0, 0.0, 0.0


def _read_memory():
    """Returns (total_mb, used_mb, usage_pct, swap_total_mb, swap_used_mb)."""
    result = {"MemTotal": 0, "MemAvailable": 0, "SwapTotal": 0, "SwapFree": 0}
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                for key in result:
                    if line.startswith(key + ":"):
                        result[key] = int(line.split()[1])  # value in kB
    except Exception as e:
        log_write("WARNING", "meminfo unavailable: {}".format(e))
        return 0, 0, 0.0, 0, 0

    total_mb = result["MemTotal"] // 1024
    available_mb = result["MemAvailable"] // 1024
    used_mb = total_mb - available_mb
    usage_pct = round((used_mb / total_mb) * 100.0, 2) if total_mb > 0 else 0.0
    swap_total_mb = result["SwapTotal"] // 1024
    swap_used_mb = (result["SwapTotal"] - result["SwapFree"]) // 1024
    return total_mb, used_mb, usage_pct, swap_total_mb, swap_used_mb


def _read_disk_usages():
    """Returns list of disk usage dicts from /proc/mounts."""
    result = []
    seen_mountpoints = set()
    try:
        with open("/proc/mounts", "r") as f:
            mounts = f.readlines()
    except Exception as e:
        log_write("WARNING", "disk: cannot read /proc/mounts: {}".format(e))
        return result

    for line in mounts:
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mountpoint, fs_type = parts[0], parts[1], parts[2]
        if fs_type in SKIP_FILESYSTEMS:
            continue
        if mountpoint in seen_mountpoints:
            continue
        seen_mountpoints.add(mountpoint)
        try:
            st = os.statvfs(mountpoint)
            if st.f_blocks == 0:
                continue
            total_gb = round((st.f_blocks * st.f_frsize) / (1024 ** 3), 2)
            free_gb = round((st.f_bavail * st.f_frsize) / (1024 ** 3), 2)
            used_gb = round(total_gb - free_gb, 2)
            usage_pct = round((used_gb / total_gb) * 100.0, 2) if total_gb > 0 else 0.0
            result.append({
                "mountpoint": mountpoint,
                "totalGb": total_gb,
                "usedGb": used_gb,
                "usagePercent": usage_pct,
                "filesystem": fs_type,
            })
        except Exception as e:
            log_write("WARNING", "disk: statvfs({}) failed: {}".format(mountpoint, e))
            continue

    return result


def _read_network_interfaces():
    """Returns list of network interface stats from /proc/net/dev."""
    result = []
    try:
        with open("/proc/net/dev", "r") as f:
            lines = f.readlines()
    except Exception as e:
        log_write("WARNING", "network: cannot read /proc/net/dev: {}".format(e))
        return result

    for line in lines[2:]:  # skip 2 header lines
        if ":" not in line:
            continue
        name, data = line.split(":", 1)
        name = name.strip()
        if name == "lo":
            continue
        fields = data.split()
        if len(fields) < 10:
            continue
        try:
            result.append({
                "name": name,
                "rxBytes": int(fields[0]),
                "rxPackets": int(fields[1]),
                "txBytes": int(fields[8]),
                "txPackets": int(fields[9]),
            })
        except (ValueError, IndexError) as e:
            log_write("WARNING", "network: parse error for {}: {}".format(name, e))

    return result


def _read_process_count():
    try:
        return sum(1 for d in os.listdir("/proc") if d.isdigit())
    except Exception as e:
        log_write("WARNING", "process_count unavailable: {}".format(e))
        return 0


def _read_open_files():
    try:
        with open("/proc/sys/fs/file-nr", "r") as f:
            return int(f.read().split()[0])
    except Exception as e:
        log_write("WARNING", "open_files unavailable: {}".format(e))
        return 0


def _read_os_info():
    try:
        with open("/etc/os-release", "r") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return platform.system() + " " + platform.release()


def _read_uptime():
    try:
        with open("/proc/uptime", "r") as f:
            return int(float(f.read().split()[0]))
    except Exception as e:
        log_write("WARNING", "uptime unavailable: {}".format(e))
        return 0


def collect_linux_metrics():
    """Collect all metrics on Linux. Includes a single 100ms sleep for CPU/IO delta."""
    cpu_pct, io_read, io_write = _sample_proc_delta()
    cpu_cores = _read_cpu_cores()
    load1, load5, load15 = _read_load_avg()
    mem_total, mem_used, mem_pct, swap_total, swap_used = _read_memory()
    disks = _read_disk_usages()
    networks = _read_network_interfaces()
    proc_count = _read_process_count()
    open_files = _read_open_files()
    os_info = _read_os_info()
    uptime = _read_uptime()
    kernel = platform.release()

    return {
        "os": os_info,
        "kernelVersion": kernel,
        "uptimeSeconds": uptime,
        "agentVersion": AGENT_VERSION,
        "cpuUsagePercent": cpu_pct,
        "cpuCores": cpu_cores,
        "loadAvg1": load1,
        "loadAvg5": load5,
        "loadAvg15": load15,
        "memTotalMb": mem_total,
        "memUsedMb": mem_used,
        "memUsagePercent": mem_pct,
        "swapTotalMb": swap_total,
        "swapUsedMb": swap_used,
        "diskUsages": disks,
        "networkInterfaces": networks,
        "processCount": proc_count,
        "openFiles": open_files,
        "ioReadKbps": io_read,
        "ioWriteKbps": io_write,
    }


# ── SECTION 5: WINDOWS METRIC COLLECTORS ────────────────────────────────────

def _wmic(query):
    """
    Run a WMIC query and return list of dicts keyed by CSV column header.
    Falls back to empty list on any error.
    """
    try:
        cmd = ["wmic"] + query.split() + ["/FORMAT:CSV"]
        out = subprocess.check_output(
            cmd,
            stderr=subprocess.DEVNULL,
            timeout=15,
            universal_newlines=True,
        )
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        if len(lines) < 2:
            return []
        headers = lines[0].split(",")
        rows = []
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) == len(headers):
                rows.append(dict(zip(headers, parts)))
        return rows
    except Exception as e:
        log_write("WARNING", "wmic failed ({}): {}".format(query[:60], e))
        return []


def _win_powershell(ps_cmd):
    """Run a PowerShell command and return stdout string. Empty string on error."""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            stderr=subprocess.DEVNULL,
            timeout=15,
            universal_newlines=True,
        )
        return out.strip()
    except Exception as e:
        log_write("WARNING", "powershell failed: {}".format(e))
        return ""


def _win_cpu():
    """Returns (cpu_usage_pct, cpu_cores)."""
    rows = _wmic("cpu get LoadPercentage,NumberOfCores")
    if rows:
        try:
            pct = float(rows[0].get("LoadPercentage") or 0)
            cores = int(rows[0].get("NumberOfCores") or 0)
            return pct, cores
        except (ValueError, TypeError):
            pass

    # PowerShell fallback
    ps = _win_powershell(
        "(Get-WmiObject Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average"
    )
    try:
        pct = float(ps) if ps else 0.0
    except ValueError:
        pct = 0.0

    ps_cores = _win_powershell(
        "(Get-WmiObject Win32_Processor).NumberOfCores"
    )
    try:
        cores = int(ps_cores.split()[0]) if ps_cores else 0
    except (ValueError, IndexError):
        cores = 0

    return pct, cores


def _win_memory():
    """Returns (total_mb, used_mb, usage_pct, swap_total_mb, swap_used_mb)."""
    rows = _wmic("OS get TotalVisibleMemorySize,FreePhysicalMemory,TotalVirtualMemorySize,FreeVirtualMemory")
    if rows:
        try:
            total_kb = int(rows[0].get("TotalVisibleMemorySize") or 0)
            free_kb = int(rows[0].get("FreePhysicalMemory") or 0)
            virt_total_kb = int(rows[0].get("TotalVirtualMemorySize") or 0)
            virt_free_kb = int(rows[0].get("FreeVirtualMemory") or 0)
            total_mb = total_kb // 1024
            used_mb = (total_kb - free_kb) // 1024
            usage_pct = round((used_mb / total_mb) * 100.0, 2) if total_mb > 0 else 0.0
            swap_total_mb = max(0, (virt_total_kb - total_kb) // 1024)
            swap_used_mb = max(0, swap_total_mb - (virt_free_kb // 1024))
            return total_mb, used_mb, usage_pct, swap_total_mb, swap_used_mb
        except (ValueError, TypeError, ZeroDivisionError):
            pass
    return 0, 0, 0.0, 0, 0


def _win_disk():
    """Returns list of disk usage dicts."""
    result = []
    rows = _wmic("logicaldisk get DeviceID,Size,FreeSpace,FileSystem")
    for row in rows:
        try:
            device_id = row.get("DeviceID", "").strip()
            size = int(row.get("Size") or 0)
            free = int(row.get("FreeSpace") or 0)
            fs = row.get("FileSystem", "").strip()
            if size == 0:
                continue
            total_gb = round(size / (1024 ** 3), 2)
            used_gb = round((size - free) / (1024 ** 3), 2)
            usage_pct = round((used_gb / total_gb) * 100.0, 2) if total_gb > 0 else 0.0
            result.append({
                "mountpoint": device_id,
                "totalGb": total_gb,
                "usedGb": used_gb,
                "usagePercent": usage_pct,
                "filesystem": fs,
            })
        except (ValueError, TypeError):
            continue
    return result


def _win_network():
    """Returns list of network interface dicts."""
    result = []
    rows = _wmic("nic where NetEnabled=TRUE get Name,BytesReceivedPerSecond,BytesSentPerSecond")
    for row in rows:
        try:
            name = row.get("Name", "").strip()
            rx = int(row.get("BytesReceivedPerSecond") or 0)
            tx = int(row.get("BytesSentPerSecond") or 0)
            if not name:
                continue
            result.append({
                "name": name,
                "rxBytes": rx,
                "txBytes": tx,
                "rxPackets": 0,
                "txPackets": 0,
            })
        except (ValueError, TypeError):
            continue

    # Fallback: netstat -e gives aggregate bytes (single entry)
    if not result:
        try:
            out = subprocess.check_output(
                ["netstat", "-e"],
                stderr=subprocess.DEVNULL,
                timeout=10,
                universal_newlines=True,
            )
            for line in out.splitlines():
                parts = line.split()
                if len(parts) == 3 and parts[0].lower() == "bytes":
                    result.append({
                        "name": "total",
                        "rxBytes": int(parts[1]),
                        "txBytes": int(parts[2]),
                        "rxPackets": 0,
                        "txPackets": 0,
                    })
                    break
        except Exception:
            pass

    return result


def _win_processes():
    try:
        rows = _wmic("process get processid")
        # subtract 1 for header row already removed by _wmic, but _wmic returns data rows only
        return max(0, len(rows))
    except Exception:
        return 0


def _win_uptime():
    """Returns uptime in seconds."""
    try:
        rows = _wmic("os get LastBootUpTime")
        if rows:
            boot_str = rows[0].get("LastBootUpTime", "").strip()
            # Format: YYYYMMDDHHmmss.xxxxxx+offset
            if len(boot_str) >= 14:
                boot_dt = datetime.strptime(boot_str[:14], "%Y%m%d%H%M%S")
                delta = datetime.now(timezone.utc).replace(tzinfo=None) - boot_dt
                return max(0, int(delta.total_seconds()))
    except Exception as e:
        log_write("WARNING", "uptime unavailable: {}".format(e))
    return 0


def collect_windows_metrics():
    """Collect all metrics on Windows using WMIC (+ PowerShell fallback)."""
    cpu_pct, cpu_cores = _win_cpu()
    mem_total, mem_used, mem_pct, swap_total, swap_used = _win_memory()
    disks = _win_disk()
    networks = _win_network()
    proc_count = _win_processes()
    uptime = _win_uptime()

    return {
        "os": platform.version(),
        "kernelVersion": platform.release(),
        "uptimeSeconds": uptime,
        "agentVersion": AGENT_VERSION,
        "cpuUsagePercent": cpu_pct,
        "cpuCores": cpu_cores,
        "loadAvg1": 0.0,
        "loadAvg5": 0.0,
        "loadAvg15": 0.0,
        "memTotalMb": mem_total,
        "memUsedMb": mem_used,
        "memUsagePercent": mem_pct,
        "swapTotalMb": swap_total,
        "swapUsedMb": swap_used,
        "diskUsages": disks,
        "networkInterfaces": networks,
        "processCount": proc_count,
        "openFiles": 0,
        "ioReadKbps": 0.0,
        "ioWriteKbps": 0.0,
    }


# ── SECTION 6: HTTP POST ─────────────────────────────────────────────────────

def post_metrics(api_url, api_key, payload):
    """POST the metrics payload to the API. Returns True on success."""
    url = "{}/api/v1/agent/metrics".format(api_url)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Server-Key", api_key)
    req.add_header("User-Agent", "ServerPulse-Agent/{}".format(AGENT_VERSION))

    log_debug("POST {} ({} bytes)".format(url, len(body)))
    log_debug("Payload: {}".format(json.dumps(payload, indent=2)))

    ctx = ssl.create_default_context()  # validates certificate
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            elapsed = time.time() - t0
            log_write(
                "INFO",
                "POST /api/v1/agent/metrics → {} ({:.2f}s)".format(resp.status, elapsed),
            )
            return True
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        try:
            body_text = e.read().decode("utf-8", errors="replace")[:2000]
        except Exception:
            body_text = "(unreadable)"
        log_write(
            "ERROR",
            "POST /api/v1/agent/metrics → {} ({:.2f}s): {}".format(e.code, elapsed, body_text),
        )
        return False
    except Exception as e:
        log_write("ERROR", "POST /api/v1/agent/metrics failed: {}".format(e))
        return False


# ── SECTION 7: ENTRY POINT ───────────────────────────────────────────────────

def parse_args():
    """Minimal arg parsing without argparse. Returns (dry_run, debug, config_path)."""
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    debug = "--debug" in args
    config_path = None
    if "--config" in args:
        idx = args.index("--config")
        if idx + 1 < len(args):
            config_path = args[idx + 1]
    return dry_run, debug, config_path


def main():
    global DRY_RUN, DEBUG

    dry_run, cli_debug, config_override = parse_args()
    DRY_RUN = dry_run
    if cli_debug:
        DEBUG = True

    # Load whatever is in the config (may be incomplete)
    values, conf_path = load_config(config_override)

    # Config file can also enable debug mode
    if values.get("debug"):
        DEBUG = True

    if DEBUG:
        log_debug("ServerPulse Agent {} starting (debug mode)".format(AGENT_VERSION))
        log_debug("Platform: {} {}".format(platform.system(), platform.release()))
        log_debug("dry_run={}".format(dry_run))

    # Validate and interactively fill in any missing required fields (skip for dry-run)
    if not dry_run:
        values = ensure_config(values, conf_path, config_override)

    api_url = values.get("api_url", DEFAULT_API_URL)
    api_key = values.get("api_key", "")

    # Detect platform and collect metrics
    is_windows = platform.system() == "Windows"
    if is_windows:
        metrics = collect_windows_metrics()
    else:
        metrics = collect_linux_metrics()

    log_debug("Metrics collected successfully")

    # Dry run: print JSON and exit
    if dry_run:
        print(json.dumps(metrics, indent=2))
        sys.exit(0)

    # POST metrics
    ok = post_metrics(api_url, api_key, metrics)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
