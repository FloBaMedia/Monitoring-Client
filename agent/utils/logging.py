"""Logging utilities for ServerPulse Agent."""

import os
import platform
import sys
from datetime import datetime

from models.limits import LOG_MAX_BYTES, LOG_MAX_BACKUPS

_LOG_TO_STDERR = None


def _get_log_path():
    if platform.system() == "Windows":
        return os.path.join("C:\\ProgramData\\ServerPulse", "agent.log")
    return "/var/log/serverpulse-agent.log"


def _ensure_log_dir(log_path):
    log_dir = os.path.dirname(log_path)
    if log_dir and not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir)
        except OSError:
            return False
    return True


def _rotate_log(log_path):
    if not os.path.exists(log_path):
        return

    if os.path.getsize(log_path) <= LOG_MAX_BYTES:
        return

    for i in range(LOG_MAX_BACKUPS - 1, 0, -1):
        src = "{}.{}".format(log_path, i)
        dst = "{}.{}".format(log_path, i + 1)
        try:
            if os.path.exists(dst):
                os.remove(dst)
            if os.path.exists(src):
                os.rename(src, dst)
        except OSError:
            pass

    backup = "{}.1".format(log_path)
    try:
        if os.path.exists(backup):
            os.remove(backup)
        os.rename(log_path, backup)
    except OSError:
        pass


def log_write(level, message, debug=False):
    global _LOG_TO_STDERR
    if _LOG_TO_STDERR is None:
        _LOG_TO_STDERR = os.environ.get("SERVERPULSE_DEBUG", "").strip().lower() in ("1", "true", "yes")

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[{}] {:<7} {}\n".format(ts, level, message)

    if debug or _LOG_TO_STDERR:
        sys.stderr.write(line)

    log_path = _get_log_path()
    try:
        if not _ensure_log_dir(log_path):
            sys.stderr.write("[{}] {:<7} log_write: could not create log dir\n".format(ts, "ERROR"))
            return

        _rotate_log(log_path)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        sys.stderr.write("[{}] {:<7} log_write: could not write to {}: {}\n".format(ts, "ERROR", log_path, e))


def log_debug(message, debug_flag=False):
    if debug_flag:
        log_write("DEBUG", message, debug=True)
