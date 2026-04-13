"""Windows metric collectors for ServerPulse Agent."""

import platform
import subprocess
import time
from datetime import datetime, timezone


def _wmic(query):
    """
    Run a WMIC query and return list of dicts keyed by CSV column header.
    Falls back to empty list on any error.
    """
    from utils.logging import log_write

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
    from utils.logging import log_write

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
        return max(0, len(rows))
    except Exception:
        return 0


def _win_uptime():
    """Returns uptime in seconds."""
    from utils.logging import log_write

    try:
        rows = _wmic("os get LastBootUpTime")
        if rows:
            boot_str = rows[0].get("LastBootUpTime", "").strip()
            if len(boot_str) >= 14:
                boot_dt = datetime.strptime(boot_str[:14], "%Y%m%d%H%M%S")
                delta = datetime.now(timezone.utc).replace(tzinfo=None) - boot_dt
                return max(0, int(delta.total_seconds()))
    except Exception as e:
        log_write("WARNING", "uptime unavailable: {}".format(e))
    return 0


def collect_windows_metrics():
    """Collect all metrics on Windows using WMIC (+ PowerShell fallback)."""
    from models.constants import AGENT_VERSION

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