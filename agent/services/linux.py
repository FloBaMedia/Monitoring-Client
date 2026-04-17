"""Linux metric collectors for ServerPulse Agent."""

import os
import platform
import time
from models.constants import SKIP_FILESYSTEMS, DISK_PREFIXES


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
                if not any(devname.startswith(p) for p in DISK_PREFIXES):
                    continue
                if devname.startswith("nvme"):
                    if "p" in devname.split("n")[-1]:
                        continue
                elif devname[-1].isdigit():
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
        from utils.logging import log_write
        log_write("WARNING", "cpu_cores unavailable: {}".format(e))
        return 0


def _read_cpu_info():
    """Returns (cpu_model, cpu_mhz, cpu_threads) from /proc/cpuinfo."""
    model = None
    mhz = None
    threads = 0
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("processor"):
                    threads += 1
                elif model is None and line.startswith("model name"):
                    model = line.split(":", 1)[1].strip()
                elif mhz is None and line.startswith("cpu MHz"):
                    try:
                        mhz = int(float(line.split(":", 1)[1].strip()))
                    except (ValueError, OverflowError):
                        pass
    except Exception as e:
        from utils.logging import log_write
        log_write("WARNING", "cpu_info unavailable: {}".format(e))
    return model, mhz, max(threads, 1)


def _read_load_avg():
    try:
        with open("/proc/loadavg", "r") as f:
            parts = f.read().split()
            return float(parts[0]), float(parts[1]), float(parts[2])
    except Exception as e:
        from utils.logging import log_write
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
                        result[key] = int(line.split()[1])
    except Exception as e:
        from utils.logging import log_write
        log_write("WARNING", "meminfo unavailable: {}".format(e))
        return 0, 0, 0.0, 0, 0

    total_mb = result["MemTotal"] // 1024
    available_mb = result["MemAvailable"] // 1024
    used_mb = total_mb - available_mb
    usage_pct = round((used_mb / total_mb) * 100.0, 2) if total_mb > 0 else 0.0
    swap_total_mb = result["SwapTotal"] // 1024
    swap_used_mb = (result["SwapTotal"] - result["SwapFree"]) // 1024
    return total_mb, used_mb, usage_pct, swap_total_mb, swap_used_mb


_DISK_MAX_ENTRIES = 50


def _read_disk_usages():
    """Returns list of disk usage dicts from /proc/mounts."""
    from utils.logging import log_write

    result = []
    seen_mountpoints = set()
    seen_fs_ids = set()  # (f_blocks, f_frsize) — dedup bind-mounts of the same underlying fs
    try:
        with open("/proc/mounts", "r") as f:
            mounts = f.readlines()
    except Exception as e:
        log_write("WARNING", "disk: cannot read /proc/mounts: {}".format(e))
        return result

    for line in mounts:
        if len(result) >= _DISK_MAX_ENTRIES:
            break
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
            # Skip entries whose underlying filesystem was already reported (e.g. Docker
            # bind-mounts of /etc/hosts that point at the same device as /).
            fs_id = (st.f_blocks, st.f_frsize)
            if fs_id in seen_fs_ids:
                continue
            seen_fs_ids.add(fs_id)
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


_NETWORK_MAX_ENTRIES = 20


def _read_network_interfaces():
    """Returns list of network interface stats from /proc/net/dev."""
    from utils.logging import log_write

    result = []
    try:
        with open("/proc/net/dev", "r") as f:
            lines = f.readlines()
    except Exception as e:
        log_write("WARNING", "network: cannot read /proc/net/dev: {}".format(e))
        return result

    for line in lines[2:]:
        if len(result) >= _NETWORK_MAX_ENTRIES:
            break
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


def _read_top_processes(limit=10):
    """Return top `limit` processes sorted by CPU % using two /proc snapshots."""
    try:
        def _read_proc_stats():
            stats = {}
            for pid in os.listdir("/proc"):
                if not pid.isdigit():
                    continue
                try:
                    with open("/proc/{}/stat".format(pid), "r") as f:
                        parts = f.read().split()
                    name = parts[1].strip("()")
                    utime = int(parts[13])
                    stime = int(parts[14])
                    stats[pid] = {"name": name, "ticks": utime + stime}
                except Exception:
                    pass
            return stats

        snap0 = _read_proc_stats()
        time.sleep(0.2)
        snap1 = _read_proc_stats()

        mem_total = 0
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        mem_total = int(line.split()[1]) * 1024
                        break
        except Exception:
            pass

        hz = 100  # typical Linux USER_HZ
        results = []
        for pid, s1 in snap1.items():
            if pid not in snap0:
                continue
            delta = s1["ticks"] - snap0[pid]["ticks"]
            cpu_pct = round((delta / hz) / 0.2 * 100.0, 1)
            mem_mb = 0.0
            user = ""
            try:
                with open("/proc/{}/status".format(pid), "r") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            mem_mb = round(int(line.split()[1]) / 1024.0, 1)
                        elif line.startswith("Uid:"):
                            uid = line.split()[1]
                            try:
                                import pwd
                                user = pwd.getpwuid(int(uid)).pw_name
                            except Exception:
                                user = uid
            except Exception:
                pass
            results.append({
                "pid": int(pid),
                "name": s1["name"],
                "cpuPercent": cpu_pct,
                "memMb": mem_mb,
                "user": user,
            })

        results.sort(key=lambda p: p["cpuPercent"], reverse=True)
        return results[:limit]
    except Exception as e:
        from utils.logging import log_write
        log_write("WARNING", "top_processes unavailable: {}".format(e))
        return []


def _read_process_count():
    try:
        return sum(1 for d in os.listdir("/proc") if d.isdigit())
    except Exception as e:
        from utils.logging import log_write
        log_write("WARNING", "process_count unavailable: {}".format(e))
        return 0


def _read_open_files():
    try:
        with open("/proc/sys/fs/file-nr", "r") as f:
            return int(f.read().split()[0])
    except Exception as e:
        from utils.logging import log_write
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
        from utils.logging import log_write
        log_write("WARNING", "uptime unavailable: {}".format(e))
        return 0


def collect_linux_metrics():
    """Collect all metrics on Linux. Includes a single 100ms sleep for CPU/IO delta."""
    from models.constants import AGENT_VERSION

    cpu_pct, io_read, io_write = _sample_proc_delta()
    cpu_pct = max(0.0, min(100.0, cpu_pct))
    cpu_cores = _read_cpu_cores()
    cpu_model, cpu_mhz, cpu_threads = _read_cpu_info()
    load1, load5, load15 = _read_load_avg()
    mem_total, mem_used, mem_pct, swap_total, swap_used = _read_memory()
    disks = _read_disk_usages()
    networks = _read_network_interfaces()
    proc_count = _read_process_count()
    top_processes = _read_top_processes()
    open_files = _read_open_files()
    os_info = _read_os_info()
    uptime = _read_uptime()
    kernel = platform.release()

    return {
        "os": os_info,
        "kernelVersion": kernel,
        "uptimeSeconds": uptime,
        "agentVersion": AGENT_VERSION,
        "cpuModel": cpu_model,
        "cpuMhz": cpu_mhz,
        "cpuThreads": cpu_threads,
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
        "topProcesses": top_processes,
        "openFiles": open_files,
        "ioReadKbps": io_read,
        "ioWriteKbps": io_write,
    }