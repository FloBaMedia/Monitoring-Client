"""Windows metric collectors for ServerMetry Agent."""

import json
import os
import platform
import subprocess
import time

from models.limits import CPU_SNAP_INTERVAL_SEC, TOP_PROCESS_LIMIT
from utils.lock import FileLock
from utils.logging import log_write
from utils.snapshot import CpuSnapStore

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CPU_SNAP_FILE = os.path.join(_AGENT_DIR, ".cpu_snap")
_LOCK_FILE = os.path.join(_AGENT_DIR, ".cpu_snap.lock")
_snap_store = CpuSnapStore(_CPU_SNAP_FILE)

# Optical / empty volumes that produce near-zero sizes
_SKIP_FILESYSTEMS = {"CDFS", "UDF", "CDROM"}


def _ps(ps_cmd, timeout=30):
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


def _ps_json(ps_cmd, timeout=30):
    raw = _ps(ps_cmd, timeout=timeout)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _collect_base_metrics():
    """
    Gather CPU/memory/disk/network/IO/process/uptime in a single PowerShell process
    to avoid spawning many powershell.exe instances per run.
    """
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$out = @{}

# CPU load + core count
$cpus = @(Get-CimInstance Win32_Processor | Select-Object LoadPercentage,NumberOfCores,Name,MaxClockSpeed,NumberOfLogicalProcessors)
if ($cpus.Count -gt 0) {
  $pcts = @($cpus | ForEach-Object { [double]($_.LoadPercentage) })
  $out.cpuUsagePercent = [Math]::Round(($pcts | Measure-Object -Average).Average, 2)
  $out.cpuCores = [int](($cpus | Measure-Object -Property NumberOfCores -Sum).Sum)
  $out.cpuModel = [string]$cpus[0].Name
  $out.cpuMhz = if ($cpus[0].MaxClockSpeed) { [int]$cpus[0].MaxClockSpeed } else { $null }
  $out.cpuThreads = [int](($cpus | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum)
  if (-not $out.cpuThreads) { $out.cpuThreads = 1 }
} else {
  $out.cpuUsagePercent = 0.0
  $out.cpuCores = 0
  $out.cpuModel = $null
  $out.cpuMhz = $null
  $out.cpuThreads = 1
}

# CPU perf raw (for 1-min average across runs)
try {
  $s = (Get-Counter '\Processor(_Total)\% Processor Time').CounterSamples[0]
  $out.cpuPerf = @{ rv = [long]$s.RawValue; sv = [long]$s.SecondValue }
} catch {
  $out.cpuPerf = $null
}

# Memory / swap
$os = Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory,TotalVirtualMemorySize,FreeVirtualMemory,LastBootUpTime
if ($os) {
  $totalKb = [long]$os.TotalVisibleMemorySize
  $freeKb = [long]$os.FreePhysicalMemory
  $virtTotalKb = [long]$os.TotalVirtualMemorySize
  $virtFreeKb = [long]$os.FreeVirtualMemory
  $totalMb = [int]($totalKb / 1024)
  $usedMb = [int](($totalKb - $freeKb) / 1024)
  $out.memTotalMb = $totalMb
  $out.memUsedMb = $usedMb
  $out.memUsagePercent = if ($totalMb -gt 0) { [Math]::Round(($usedMb / $totalMb) * 100.0, 2) } else { 0.0 }
  $swapTotalMb = [Math]::Max(0, [int](($virtTotalKb - $totalKb) / 1024))
  $swapFreeMb = [Math]::Max(0, [int]($virtFreeKb / 1024))
  $out.swapTotalMb = $swapTotalMb
  $out.swapUsedMb = [Math]::Max(0, $swapTotalMb - $swapFreeMb)
  if ($os.LastBootUpTime) {
    $boot = [DateTime]$os.LastBootUpTime
    $out.uptimeSeconds = [Math]::Max(0, [int]((Get-Date) - $boot).TotalSeconds)
  } else {
    $out.uptimeSeconds = 0
  }
} else {
  $out.memTotalMb = 0; $out.memUsedMb = 0; $out.memUsagePercent = 0.0
  $out.swapTotalMb = 0; $out.swapUsedMb = 0; $out.uptimeSeconds = 0
}

# Disks — skip optical / empty volumes
$disks = @()
Get-CimInstance Win32_LogicalDisk | Where-Object {
  $_.Size -gt 1MB -and $_.FileSystem -and @('CDFS','UDF') -notcontains $_.FileSystem.ToUpper()
} | ForEach-Object {
  $size = [double]$_.Size
  $free = [double]$_.FreeSpace
  $totalGb = [Math]::Round($size / 1GB, 2)
  $usedGb = [Math]::Round(($size - $free) / 1GB, 2)
  if ($totalGb -le 0) { return }
  $disks += @{
    mountpoint = [string]$_.DeviceID
    totalGb = $totalGb
    usedGb = $usedGb
    usagePercent = [Math]::Round(($usedGb / $totalGb) * 100.0, 2)
    filesystem = [string]$_.FileSystem
  }
}
$out.diskUsages = $disks

# Network
$nets = @()
$stats = Get-NetAdapterStatistics -ErrorAction SilentlyContinue | Where-Object {
  $_.ReceivedBytes -gt 0 -or $_.SentBytes -gt 0
}
if ($stats) {
  $stats | ForEach-Object {
    $nets += @{
      name = [string]$_.Name
      rxBytes = [long]$_.ReceivedBytes
      txBytes = [long]$_.SentBytes
      rxPackets = [long]$_.ReceivedUnicastPackets
      txPackets = [long]$_.SentUnicastPackets
    }
  }
}
$out.networkInterfaces = $nets

# Disk IO (instant cooked values)
try {
  $c = (Get-Counter '\PhysicalDisk(_Total)\Disk Read Bytes/sec','\PhysicalDisk(_Total)\Disk Write Bytes/sec' -ErrorAction SilentlyContinue).CounterSamples
  if ($c -and $c.Count -ge 2) {
    $out.ioReadKbps = [Math]::Round([double]$c[0].CookedValue / 1024, 2)
    $out.ioWriteKbps = [Math]::Round([double]$c[1].CookedValue / 1024, 2)
  } else {
    $out.ioReadKbps = 0.0; $out.ioWriteKbps = 0.0
  }
} catch {
  $out.ioReadKbps = 0.0; $out.ioWriteKbps = 0.0
}

$procs = @(Get-Process -ErrorAction SilentlyContinue)
$out.processCount = $procs.Count
$out.openFiles = [long](($procs | Measure-Object -Property Handles -Sum).Sum)

$out | ConvertTo-Json -Compress -Depth 6
"""
    return _ps_json(script, timeout=45)


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


def _network_fallback():
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


def _filter_disks(disks):
    result = []
    if not disks:
        return result
    if isinstance(disks, dict):
        disks = [disks]
    for row in disks:
        try:
            fs = (row.get("filesystem") or "").strip().upper()
            if fs in _SKIP_FILESYSTEMS:
                continue
            total_gb = float(row.get("totalGb") or 0)
            if total_gb <= 0:
                continue
            result.append({
                "mountpoint": str(row.get("mountpoint") or "").strip(),
                "totalGb": round(total_gb, 2),
                "usedGb": round(float(row.get("usedGb") or 0), 2),
                "usagePercent": round(float(row.get("usagePercent") or 0), 2),
                "filesystem": (row.get("filesystem") or "").strip(),
            })
        except (ValueError, TypeError):
            continue
    return result


def collect_windows_metrics():
    from models.constants import AGENT_VERSION

    base = _collect_base_metrics() or {}

    cpu_pct = float(base.get("cpuUsagePercent") or 0)
    cpu_cores = int(base.get("cpuCores") or 0)
    cpu_model = base.get("cpuModel") or None
    cpu_mhz = base.get("cpuMhz")
    if cpu_mhz is not None:
        try:
            cpu_mhz = int(cpu_mhz) or None
        except (ValueError, TypeError):
            cpu_mhz = None
    cpu_threads = int(base.get("cpuThreads") or 1) or 1

    cpu_avg_1min = None
    cpu_perf = base.get("cpuPerf")
    with FileLock(_LOCK_FILE, timeout=30) as lock:
        if not lock._acquired:
            log_write("WARNING", "cpu_snap locked by another process, skipping snapshot update")
        elif isinstance(cpu_perf, dict):
            try:
                rv1 = int(cpu_perf.get("rv"))
                sv1 = int(cpu_perf.get("sv"))
                prev = _snap_store.load()
                if prev is not None:
                    rv0, sv0 = prev
                    if rv0 is not None and sv0 is not None and (sv1 - sv0) > 0:
                        cpu_avg_1min = round(
                            max(0.0, min(100.0, (rv1 - rv0) / (sv1 - sv0) * 100.0)), 2
                        )
                _snap_store.save(rv1, sv1)
            except (ValueError, TypeError, ZeroDivisionError):
                pass

    top_processes = _win_top_processes()

    networks = base.get("networkInterfaces") or []
    if isinstance(networks, dict):
        networks = [networks]
    if not networks:
        networks = _network_fallback()
    else:
        normalized = []
        for row in networks:
            try:
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                normalized.append({
                    "name": name,
                    "rxBytes": int(row.get("rxBytes") or 0),
                    "txBytes": int(row.get("txBytes") or 0),
                    "rxPackets": int(row.get("rxPackets") or 0),
                    "txPackets": int(row.get("txPackets") or 0),
                })
            except (ValueError, TypeError):
                continue
        networks = normalized

    return {
        "os": platform.version(),
        "kernelVersion": platform.release(),
        "uptimeSeconds": int(base.get("uptimeSeconds") or 0),
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
        "memTotalMb": int(base.get("memTotalMb") or 0),
        "memUsedMb": int(base.get("memUsedMb") or 0),
        "memUsagePercent": float(base.get("memUsagePercent") or 0),
        "swapTotalMb": int(base.get("swapTotalMb") or 0),
        "swapUsedMb": int(base.get("swapUsedMb") or 0),
        "diskUsages": _filter_disks(base.get("diskUsages")),
        "networkInterfaces": networks,
        "processCount": int(base.get("processCount") or 0),
        "topProcesses": top_processes,
        "openFiles": int(base.get("openFiles") or 0),
        "ioReadKbps": float(base.get("ioReadKbps") or 0),
        "ioWriteKbps": float(base.get("ioWriteKbps") or 0),
    }
