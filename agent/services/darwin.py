"""macOS metric collectors for ServerPulse Agent."""

import os
import platform
import subprocess
import time

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


def _sysctl(key):
    try:
        return subprocess.check_output(
            ["sysctl", "-n", key],
            stderr=subprocess.DEVNULL, timeout=5, universal_newlines=True,
        ).strip()
    except Exception:
        return ""


def _cmd(args, timeout=10):
    try:
        return subprocess.check_output(
            args, stderr=subprocess.DEVNULL, timeout=timeout, universal_newlines=True,
        )
    except Exception:
        return ""


def _read_cpu():
    raw = _cmd(["top", "-l", "2", "-n", "0", "-s", "1"], timeout=5)
    cpu_pct = 0.0
    for line in reversed(raw.splitlines()):
        if "CPU usage:" in line:
            try:
                idle_part = [p for p in line.split(",") if "idle" in p][0]
                idle_pct = float(idle_part.strip().split("%")[0])
                cpu_pct = round(max(0.0, min(100.0, 100.0 - idle_pct)), 2)
            except Exception:
                pass
            break

    threads = int(_sysctl("hw.logicalcpu") or 0) or 1
    cores = int(_sysctl("hw.physicalcpu") or 0) or threads
    model = _sysctl("machdep.cpu.brand_string") or platform.processor() or "Unknown"

    mhz = None
    hz_raw = _sysctl("hw.cpufrequency_max")
    if hz_raw:
        try:
            mhz = int(int(hz_raw) / 1_000_000)
        except (ValueError, OverflowError):
            pass

    return cpu_pct, cores, threads, model, mhz


def _read_load_avg():
    raw = _sysctl("vm.loadavg")
    try:
        parts = raw.strip("{ }").split()
        return float(parts[0]), float(parts[1]), float(parts[2])
    except Exception:
        return 0.0, 0.0, 0.0


def _read_memory():
    total_bytes = int(_sysctl("hw.memsize") or 0)
    total_mb = total_bytes // (1024 * 1024)

    vm_raw = _cmd(["vm_stat"])
    pages = {}
    page_size = 4096
    for line in vm_raw.splitlines():
        if "page size of" in line:
            try:
                page_size = int(line.split("page size of")[1].split("bytes")[0].strip())
            except Exception:
                pass
        if ":" in line:
            key, _, val = line.partition(":")
            try:
                pages[key.strip()] = int(val.strip().rstrip("."))
            except ValueError:
                pass

    free = pages.get("Pages free", 0)
    inactive = pages.get("Pages inactive", 0)
    speculative = pages.get("Pages speculative", 0)
    available_mb = ((free + inactive + speculative) * page_size) // (1024 * 1024)
    used_mb = max(0, total_mb - available_mb)
    usage_pct = round((used_mb / total_mb) * 100.0, 2) if total_mb > 0 else 0.0

    swap_total_mb = 0
    swap_used_mb = 0
    swap_raw = _sysctl("vm.swapusage")
    try:
        def _parse_size(s):
            s = s.strip()
            if s.endswith("G"):
                return int(float(s[:-1]) * 1024)
            if s.endswith("K"):
                return max(1, int(float(s[:-1]) // 1024))
            return int(float(s.rstrip("M")))

        parts = swap_raw.split()
        for i, p in enumerate(parts):
            if p == "total" and i + 2 < len(parts):
                swap_total_mb = _parse_size(parts[i + 2])
            elif p == "used" and i + 2 < len(parts):
                swap_used_mb = _parse_size(parts[i + 2])
    except Exception:
        pass

    return total_mb, used_mb, usage_pct, swap_total_mb, swap_used_mb


def _read_disk_usages():
    result = []
    seen = set()
    raw = _cmd(["df", "-kl"])
    _SKIP_PREFIXES = ("/dev", "/private/var/folders", "/var/folders", "/System/Volumes/VM")
    for line in raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        device, blocks, used_blk, mountpoint = parts[0], parts[1], parts[2], parts[5]
        if mountpoint in seen:
            continue
        if any(mountpoint.startswith(p) for p in _SKIP_PREFIXES):
            continue
        seen.add(mountpoint)
        try:
            total_gb = round(int(blocks) * 1024 / (1024 ** 3), 2)
            used_gb = round(int(used_blk) * 1024 / (1024 ** 3), 2)
            if total_gb == 0:
                continue
            result.append({
                "mountpoint": mountpoint,
                "totalGb": total_gb,
                "usedGb": used_gb,
                "usagePercent": round((used_gb / total_gb) * 100.0, 2),
                "filesystem": device,
            })
        except (ValueError, ZeroDivisionError):
            continue
    return result


def _read_network_interfaces():
    result = []
    seen = set()
    raw = _cmd(["netstat", "-ib"])
    for line in raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 10:
            continue
        name = parts[0]
        if name in seen or name.startswith("lo"):
            continue
        if not parts[2].startswith("<"):
            continue
        seen.add(name)
        try:
            result.append({
                "name": name,
                "rxBytes": int(parts[6]),
                "rxPackets": int(parts[4]),
                "txBytes": int(parts[9]),
                "txPackets": int(parts[7]),
            })
        except (ValueError, IndexError):
            continue
    return result


def _read_io():
    raw = _cmd(["iostat", "-c", "2", "-w", "1"], timeout=5)
    data_lines = []
    for line in raw.splitlines():
        parts = line.split()
        if not parts:
            continue
        try:
            float(parts[0])
            data_lines.append(parts)
        except (ValueError, IndexError):
            pass
    if len(data_lines) >= 2:
        try:
            mb_s = float(data_lines[1][2])
            half_kbps = round(mb_s * 1024 / 2, 2)
            return half_kbps, half_kbps
        except (ValueError, IndexError):
            pass
    return 0.0, 0.0


def _read_process_count():
    raw = _cmd(["ps", "ax"])
    return max(0, len(raw.splitlines()) - 1)


def _read_top_processes(limit=TOP_PROCESS_LIMIT):
    try:
        raw = _cmd(["ps", "axo", "pid,pcpu,rss,user,comm", "-r"])
        results = []
        for line in raw.splitlines()[1:]:
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            try:
                results.append({
                    "pid": int(parts[0]),
                    "cpuPercent": round(float(parts[1]), 1),
                    "memMb": round(int(parts[2]) / 1024.0, 1),
                    "user": parts[3],
                    "name": os.path.basename(parts[4].strip()),
                })
            except (ValueError, IndexError):
                continue
        results.sort(key=lambda p: p["cpuPercent"], reverse=True)
        return results[:limit]
    except Exception:
        return []


def _read_open_files():
    raw = _sysctl("kern.num_files")
    try:
        return int(raw) if raw else 0
    except ValueError:
        return 0


def _read_uptime():
    raw = _sysctl("kern.boottime")
    try:
        sec_str = raw.split("sec =")[1].split(",")[0].strip()
        return max(0, int(time.time()) - int(sec_str))
    except Exception:
        return 0


def collect_darwin_metrics():
    from models.constants import AGENT_VERSION

    with FileLock(_LOCK_FILE, timeout=30) as lock:
        if not lock._acquired:
            log_write("WARNING", "cpu_snap locked by another process, skipping snapshot update")

    cpu_pct, cpu_cores, cpu_threads, cpu_model, cpu_mhz = _read_cpu()

    prev_cpu = _snap_store.load()
    if prev_cpu is not None:
        prev_val = prev_cpu[0] if prev_cpu[0] is not None else None
        if prev_val is not None:
            cpu_avg_1min = round((cpu_pct + prev_val) / 2.0, 2)
        else:
            cpu_avg_1min = None
    else:
        cpu_avg_1min = None
    _snap_store.save(cpu_pct, time.time())

    load1, load5, load15 = _read_load_avg()
    mem_total, mem_used, mem_pct, swap_total, swap_used = _read_memory()
    disks = _read_disk_usages()
    networks = _read_network_interfaces()
    top_processes = _read_top_processes()
    proc_count = _read_process_count()
    open_files = _read_open_files()
    io_read, io_write = _read_io()
    uptime = _read_uptime()

    os_ver = _sysctl("kern.osproductversion")
    os_info = "macOS {}".format(os_ver) if os_ver else platform.system() + " " + platform.release()

    return {
        "os": os_info,
        "kernelVersion": platform.release(),
        "uptimeSeconds": uptime,
        "agentVersion": AGENT_VERSION,
        "cpuModel": cpu_model,
        "cpuMhz": cpu_mhz,
        "cpuThreads": cpu_threads,
        "cpuUsagePercent": cpu_pct,
        "cpuAvg1MinPercent": cpu_avg_1min,
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
        "topProcesses": top_processes,
        "openFiles": open_files,
        "ioReadKbps": io_read,
        "ioWriteKbps": io_write,
    }
