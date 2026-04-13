AGENT_VERSION = "1.0.0"
LOG_MAX_BYTES = 1_048_576  # 1 MB
DEFAULT_API_URL = "https://api.yourdomain.com"

SKIP_FILESYSTEMS = {
    "tmpfs", "devtmpfs", "sysfs", "proc", "cgroup", "cgroup2",
    "pstore", "bpf", "tracefs", "securityfs", "debugfs",
    "hugetlbfs", "mqueue", "fusectl", "configfs", "ramfs",
    "devpts", "overlay", "squashfs", "autofs", "rpc_pipefs",
}

DISK_PREFIXES = ("sd", "nvme", "vd", "xvd", "hd")