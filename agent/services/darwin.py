"""macOS metric collectors for ServerPulse Agent."""

import json
import os
import platform
import subprocess
import time

# State file for cpuAvg1MinPercent — same pattern as linux.py
_CPU_SNAP_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".cpu_snap",
)


# ── Shell helpers ─────────────────────────────────────────────────────────────

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


# ── CPU ───────────────────────────────────────────────────────────────────────

def _read_cpu():
    """
    Returns (cpu_pct, cpu_cores, cpu_threads, cpu_model, cpu_mhz).
    Uses `top -l 2 -n 0 -s 1` for a 1-second CPU delta (same philosophy as linux.py).
    """
    raw = _cmd(["top", "-l", "2", "-n", "0", "-s", "1"], timeout=5)
    cpu_pct = 0.0
    # Parse the LAST "CPU usage:" line (second sample = delta, not boot-time average)
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
    hz_raw = _sysctl("hw.cpufrequency_max")  # Intel only; absent on Apple Silicon
    if hz_raw:
        try:
            mhz = int(int(hz_raw) / 1_000_000)
        except (ValueError, OverflowError):
            pass

    return cpu_pct, cores, threads, model, mhz


# ── CPU avg via state file ────────────────────────────────────────────────────
# macOS has no /proc/stat raw tick counters accessible from shell.
# We store the previous run's CPU% and average two consecutive minute readings.

def _load_cpu_snap():
    try:
        with open(_CPU_SNAP_FILE, "r") as f:
            d = json.load(f)
        return d.get("cpu_pct")
    except Exception:
        return None


def _save_cpu_snap(cpu_pct):
    try:
        with open(_CPU_SNAP_FILE, "w") as f:
            json.dump({"cpu_pct": cpu_pct, "ts": time.time()}, f)
    except Exception as e:
        from utils.logging import log_write
        log_write("WARNING", "cpu_snap: could not write state file: {}".format(e))


# ── Load average ──────────────────────────────────────────────────────────────

def _read_load_avg():
    raw = _sysctl("vm.loadavg")
    # Format: "{ 0.12 0.34 0.45 }"
    try:
        parts = raw.strip("{ }").split()
        return float(parts[0]), float(parts[1]), float(parts[2])
    except Exception:
        return 0.0, 0.0, 0.0


# ── Memory ────────────────────────────────────────────────────────────────────

def _read_memory():
    """Returns (total_mb, used_mb, usage_pct, swap_total_mb, swap_used_mb)."""
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

    # Swap from `sysctl vm.swapusage` — format: "total = 2048.00M  used = 512.00M  free = ..."
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


# ── Disk ──────────────────────────────────────────────────────────────────────

def _read_disk_usages():
    """Returns list of disk usage dicts from `df -kl` (local filesystems only)."""
    result = []
    seen = set()
    raw = _cmd(["df", "-kl"])
    _SKIP_PREFIXES = ("/dev", "/private/var/folders", "/var/folders", "/System/Volumes/VM")
    for line in raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        # df -k columns: Filesystem 1K-blocks Used Available Capacity Mounted-on
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


# ── Network ───────────────────────────────────────────────────────────────────

def _read_network_interfaces():
    """Returns list of network interface stats from `netstat -ib`."""
    result = []
    seen = set()
    raw = _cmd(["netstat", "-ib"])
    # Header: Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes Drop
    for line in raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 10:
            continue
        name = parts[0]
        if name in seen or name.startswith("lo"):
            continue
        # Skip alias entries (no <Link#N> in Network column)
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


# ── I/O ───────────────────────────────────────────────────────────────────────

def _read_io():
    """
    Returns (read_kbps, write_kbps) from `iostat -c 2 -w 1`.
    The second sample is the 1-second delta — more accurate than the cumulative boot average.
    """
    raw = _cmd(["iostat", "-c", "2", "-w", "1"], timeout=5)
    # Columns: KB/t tps MB/s  us sy id  1m 5m 15m
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
            # MB/s column is index 2 — total throughput; split 50/50 (best we can do)
            mb_s = float(data_lines[1][2])
            half_kbps = round(mb_s * 1024 / 2, 2)
            return half_kbps, half_kbps
        except (ValueError, IndexError):
            pass
    return 0.0, 0.0


# ── Processes ─────────────────────────────────────────────────────────────────

def _read_process_count():
    raw = _cmd(["ps", "ax"])
    return max(0, len(raw.splitlines()) - 1)


def _read_top_processes(limit=10):
    """Returns top processes sorted by CPU% using `ps axo`."""
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


# ── Misc ──────────────────────────────────────────────────────────────────────

def _read_open_files():
    """Returns kernel open-file count from sysctl."""
    raw = _sysctl("kern.num_files")
    try:
        return int(raw) if raw else 0
    except ValueError:
        return 0


def _read_uptime():
    raw = _sysctl("kern.boottime")
    # "{ sec = 1234567890, usec = 123456 } Mon Apr 18 10:00:00 2026"
    try:
        sec_str = raw.split("sec =")[1].split(",")[0].strip()
        return max(0, int(time.time()) - int(sec_str))
    except Exception:
        return 0


# ── Main collector ────────────────────────────────────────────────────────────

def collect_darwin_metrics():
    """Collect all metrics on macOS using native CLI tools."""
    from models.constants import AGENT_VERSION

    cpu_pct, cpu_cores, cpu_threads, cpu_model, cpu_mhz = _read_cpu()

    # cpuAvg1MinPercent: average of current and previous run's CPU%
    prev_cpu = _load_cpu_snap()
    if prev_cpu is not None:
        cpu_avg_1min = round((cpu_pct + prev_cpu) / 2.0, 2)
    else:
        cpu_avg_1min = None
    _save_cpu_snap(cpu_pct)

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
