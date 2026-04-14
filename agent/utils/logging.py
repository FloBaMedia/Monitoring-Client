"""Logging utilities for ServerPulse Agent."""

import os
import platform
import sys
from datetime import datetime, timezone

LOG_MAX_BYTES = 1_048_576


def _get_log_path():
    if platform.system() == "Windows":
        return os.path.join("C:\\ProgramData\\ServerPulse", "agent.log")
    return "/var/log/serverpulse-agent.log"


def log_write(level, message, dry_run=False, debug=False):
    """Write a log line. In dry-run or debug mode also writes to stderr."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = "[{}] {:<7} {}\n".format(ts, level, message)

    if dry_run or debug:
        sys.stderr.write(line)

    if dry_run:
        return

    log_path = _get_log_path()
    try:
        log_dir = os.path.dirname(log_path)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        if os.path.exists(log_path) and os.path.getsize(log_path) > LOG_MAX_BYTES:
            backup = log_path + ".1"
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(log_path, backup)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def log_debug(message, debug_flag=False):
    """Write a DEBUG line – only when debug mode is active."""
    if debug_flag:
        log_write("DEBUG", message, debug=True)