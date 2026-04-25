"""Windows metric collectors for ServerPulse Agent."""

import json
import os
import platform
import subprocess
import time
from datetime import datetime, timezone

from models.limits import CPU_SNAP_INTERVAL_SEC, STATE_ENCODING, TOP_PROCESS_LIMIT
from utils.lock import FileLock
from utils.logging import log_write
from utils.snapshot import CpuSnapStore

_CPU_SNAP_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".cpu_snap",
)
_LOCK_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".cpu_snap.lock",
)
_snap_store = CpuSnapStore(_CPU_SNAP_FILE)


def _ps(ps_cmd, timeout=15):
    from utils.logging import log_write
    try:
        return subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            universal_newlines=True,
        ).strip()
    except Exception as e:
        log_write("WARNING", "powershell failed: {}".format(e))
        return ""


def _ps_json(ps_cmd, timeout=15):
    raw = _ps(ps_cmd, timeout=timeout)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _win_cpu():
    data = _ps_json(
        "Get-CimInstance Win32_Processor"
        " | Select-Object LoadPercentage,NumberOfCores"
        " | ConvertTo-Json -Compress"
    )
    if data:
        try:
            if isinstance(data, list):
                pcts = [float(d.get("LoadPercentage") or 0) for d in data]
                cores = sum(int(d.get("NumberOfCores") or 0) for d in data)
                return round(sum(pcts) / len(pcts), 2), cores
            return (
                float(data.get("LoadPercentage") or 0),
                int(data.get("NumberOfCores") or 0),
            )
        except (ValueError, TypeError, ZeroDivisionError):
            pass

    log_write("WARNING", "Get-CimInstance CPU failed, falling back to WMIC")
    try:
        out = subprocess.check_output(
            ["wmic", "cpu", "get", "LoadPercentage,NumberOfCores", "/FORMAT:CSV"],
            stderr=subprocess.DEVNULL, timeout=15, universal_newlines=True,
        )
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        if len(lines) >= 2:
            headers = lines[0].split(",")
            row = dict(zip(headers, lines[1].split(",")))
            return float(row.get("LoadPercentage") or 0), int(row.get("NumberOfCores") or 0)
    except Exception:
        pass
    return 0.0, 0


def _win_cpu_info():
    data = _ps_json(
        "Get-CimInstance Win32_Processor"
        " | Select-Object Name,MaxClockSpeed,NumberOfLogicalProcessors"
        " | ConvertTo-Json -Compress"
    )
    if data:
        try:
            if isinstance(data, list):
                d = data[0]
                threads = sum(int(x.get("NumberOfLogicalProcessors") or 0) for x in data)
            else:
                d = data
                threads = int(d.get("NumberOfLogicalProcessors") or 0)
            model = d.get("Name") or None
            mhz = int(d.get("MaxClockSpeed") or 0) or None
            return model, mhz, threads or 1
        except (ValueError, TypeError):
            pass
    return None, None, 1


def _win_cpu_perf_raw():
    raw = _ps(
        "$s=(Get-Counter '\\Processor(_Total)\\% Processor Time')"
        ".CounterSamples[0];"
        "@{rv=[long]$s.RawValue;sv=[long]$s.SecondValue}|ConvertTo-Json -Compress"
    )
    try:
        d = json.loads(raw)
        return int(d["rv"]), int(d["sv"])
    except Exception:
        return None


def _win_memory():
    data = _ps_json(
        "Get-CimInstance Win32_OperatingSystem"
        " | Select-Object TotalVisibleMemorySize,FreePhysicalMemory,"
        "TotalVirtualMemorySize,FreeVirtualMemory"
        " | ConvertTo-Json -Compress"
    )
    if data:
        if isinstance(data, list):
            data = data[0]
        try:
            total_kb = int(data.get("TotalVisibleMemorySize") or 0)
            free_kb = int(data.get("FreePhysicalMemory") or 0)
            virt_total_kb = int(data.get("TotalVirtualMemorySize") or 0)
            virt_free_kb = int(data.get("FreeVirtualMemory") or 0)
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
    data = _ps_json(
        "Get-CimInstance Win32_LogicalDisk"
        " | Where-Object {$_.Size -gt 0}"
        " | Select-Object DeviceID,Size,FreeSpace,FileSystem"
        " | ConvertTo-Json -Compress"
    )
    if data is None:
        return []
    if isinstance(data, dict):
        data = [data]
    result = []
    for row in data:
        try:
            device_id = (row.get("DeviceID") or "").strip()
            size = int(row.get("Size") or 0)
            free = int(row.get("FreeSpace") or 0)
            fs = (row.get("FileSystem") or "").strip()
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
    data = _ps_json(
        "Get-NetAdapterStatistics -ErrorAction SilentlyContinue"
        " | Where-Object {$_.ReceivedBytes -gt 0 -or $_.SentBytes -gt 0}"
        " | Select-Object Name,ReceivedBytes,SentBytes,"
        "ReceivedUnicastPackets,SentUnicastPackets"
        " | ConvertTo-Json -Compress"
    )
    if data is not None:
        if isinstance(data, dict):
            data = [data]
        result = []
        for row in data:
            try:
                name = (row.get("Name") or "").strip()
                if not name:
                    continue
                result.append({
                    "name": name,
                    "rxBytes": int(row.get("ReceivedBytes") or 0),
                    "txBytes": int(row.get("SentBytes") or 0),
                    "rxPackets": int(row.get("ReceivedUnicastPackets") or 0),
                    "txPackets": int(row.get("SentUnicastPackets") or 0),
                })
            except (ValueError, TypeError):
                continue
        if result:
            return result

    try:
        out = subprocess.check_output(
            ["netstat", "-e"], stderr=subprocess.DEVNULL,
            timeout=10, universal_newlines=True,
        )
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0].lower() == "bytes":
                return [{"name": "total", "rxBytes": int(parts[1]),
                         "txBytes": int(parts[2]), "rxPackets": 0, "txPackets": 0}]
    except Exception:
        pass
    return []


def _win_io():
    raw = _ps(
        "$c=(Get-Counter"
        " '\\PhysicalDisk(_Total)\\Disk Read Bytes/sec',"
        " '\\PhysicalDisk(_Total)\\Disk Write Bytes/sec'"
        " -ErrorAction SilentlyContinue).CounterSamples;"
        "if($c){@($c[0].CookedValue,$c[1].CookedValue)|ConvertTo-Json -Compress}"
    )
    try:
        vals = json.loads(raw)
        if isinstance(vals, list) and len(vals) == 2:
            return round(float(vals[0]) / 1024, 2), round(float(vals[1]) / 1024, 2)
    except Exception:
        pass
    return 0.0, 0.0


def _win_open_files():
    raw = _ps(
        "(Get-Process -ErrorAction SilentlyContinue"
        " | Measure-Object -Property Handles -Sum).Sum"
    )
    try:
        return int(float(raw)) if raw else 0
    except (ValueError, TypeError):
        return 0


def _win_processes():
    raw = _ps("(Get-Process -ErrorAction SilentlyContinue | Measure-Object).Count")
    try:
        return int(raw) if raw else 0
    except (ValueError, TypeError):
        return 0


def _win_top_processes(limit=TOP_PROCESS_LIMIT):
    sleep_ms = int(CPU_SNAP_INTERVAL_SEC * 1000)
    script = (
        "$s1=Get-Process -ErrorAction SilentlyContinue"
        " | Select-Object Id,Name,CPU,WorkingSet;"
        "Start-Sleep -Milliseconds {};"
        "$s2=Get-Process -ErrorAction SilentlyContinue"
        " | Select-Object Id,Name,CPU,WorkingSet;"
        "$m=@{{}};"
        "$s1|ForEach-Object{{$m[$_.Id]=@{{c=$_.CPU;w=$_.WorkingSet;n=$_.Name}}}};"
        "$out=@();"
        "$s2|ForEach-Object{{"
        "  if($m.ContainsKey($_.Id)){{"
        "    $d=[Math]::Max(0,$_.CPU-$m[$_.Id].c);"
        "    $p=[Math]::Round($d/{}*100,1);"
        "    $out+=@{{pid=$_.Id;name=$_.Name;cpuPercent=$p;"
        "            memMb=[Math]::Round($_.WorkingSet/1MB,1);user=''}}"
        "  }}"
        "}};"
        "$out|Sort-Object cpuPercent -Desc"
        " |Select-Object -First {}"
        " |ConvertTo-Json -Compress"
    ).format(sleep_ms, CPU_SNAP_INTERVAL_SEC, limit)
    data = _ps_json(script, timeout=20)
    if not data:
        return []
    if isinstance(data, dict):
        data = [data]
    result = []
    for row in data:
        try:
            result.append({
                "pid": int(row.get("pid") or 0),
                "name": str(row.get("name") or ""),
                "cpuPercent": round(float(row.get("cpuPercent") or 0), 1),
                "memMb": round(float(row.get("memMb") or 0), 1),
                "user": "",
            })
        except (ValueError, TypeError):
            continue
    return result


def _win_uptime():
    raw = _ps(
        "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime"
        " | Get-Date -UFormat '%s'"
    )
    try:
        if raw:
            return max(0, int(time.time() - float(raw)))
    except (ValueError, TypeError):
        pass

    try:
        data = _ps_json(
            "Get-CimInstance Win32_OperatingSystem"
            " | Select-Object LastBootUpTime | ConvertTo-Json -Compress"
        )
        if data:
            boot_str = str(data.get("LastBootUpTime") or "")
            if boot_str.startswith("/Date("):
                ms = int(boot_str[6:boot_str.index(")")])
                return max(0, int(time.time() - ms / 1000))
    except Exception:
        pass

    log_write("WARNING", "uptime unavailable")
    return 0


def collect_windows_metrics():
    from models.constants import AGENT_VERSION

    with FileLock(_LOCK_FILE, timeout=30) as lock:
        if not lock._acquired:
            log_write("WARNING", "cpu_snap locked by another process, skipping snapshot update")

    cpu_pct, cpu_cores = _win_cpu()
    cpu_model, cpu_mhz, cpu_threads = _win_cpu_info()

    snap = _win_cpu_perf_raw()
    cpu_avg_1min = None
    if snap is not None:
        rv1, sv1 = snap
        prev = _snap_store.load()
        if prev is not None:
            rv0, sv0 = prev
            if rv0 is not None and sv1 is not None and (sv1 - sv0) > 0:
                cpu_avg_1min = round(
                    max(0.0, min(100.0, (rv1 - rv0) / (sv1 - sv0) * 100.0)), 2
                )
        _snap_store.save(rv1, sv1)

    top_processes = _win_top_processes()
    mem_total, mem_used, mem_pct, swap_total, swap_used = _win_memory()
    disks = _win_disk()
    networks = _win_network()
    proc_count = _win_processes()
    uptime = _win_uptime()
    io_read, io_write = _win_io()
    open_files = _win_open_files()

    return {
        "os": platform.version(),
        "kernelVersion": platform.release(),
        "uptimeSeconds": uptime,
        "agentVersion": AGENT_VERSION,
        "cpuModel": cpu_model,
        "cpuMhz": cpu_mhz,
        "cpuThreads": cpu_threads,
        "cpuUsagePercent": cpu_pct,
        "cpuAvg1MinPercent": cpu_avg_1min,
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
        "topProcesses": top_processes,
        "openFiles": open_files,
        "ioReadKbps": io_read,
        "ioWriteKbps": io_write,
    }
