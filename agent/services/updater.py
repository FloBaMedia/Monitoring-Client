"""
ServerPulse Agent self-updater.
Fetches the latest agent.py from GitHub, compares versions, and updates in-place.
"""

import ast
import os
import platform
import re
import shutil
import ssl
import time
import urllib.error
import urllib.request

from models.constants import AGENT_VERSION
from models.limits import UPDATE_FETCH_TIMEOUT
from utils.lock import FileLock, atomic_write
from utils.logging import log_write

GITHUB_BASE_URL = "https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent"
GITHUB_RAW_URL = GITHUB_BASE_URL + "/agent.py"
UPDATE_CHECK_INTERVAL = 3600

_MODULE_FILES = [
    "client/__init__.py",
    "client/api.py",
    "models/__init__.py",
    "models/constants.py",
    "models/limits.py",
    "services/__init__.py",
    "services/config_applier.py",
    "services/linux.py",
    "services/darwin.py",
    "services/windows.py",
    "services/updater.py",
    "utils/__init__.py",
    "utils/config.py",
    "utils/logging.py",
    "utils/validation.py",
    "utils/lock.py",
    "utils/snapshot.py",
]

_STATE_PATHS = {
    "Linux": "/etc/serverpulse/.update_check_ts",
    "Darwin": "/etc/serverpulse/.update_check_ts",
    "Windows": r"C:\ProgramData\ServerPulse\.update_check_ts",
}

_LOCK_PATHS = {
    "Linux": "/etc/serverpulse/.update.lock",
    "Darwin": "/etc/serverpulse/.update.lock",
    "Windows": r"C:\ProgramData\ServerPulse\.update.lock",
}

_INSTALL_PATHS = {
    "Linux": "/etc/serverpulse/agent.py",
    "Darwin": "/etc/serverpulse/agent.py",
    "Windows": r"C:\ProgramData\ServerPulse\agent.py",
}


def _installed_path():
    candidate = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "agent.py")
    )
    if os.path.isfile(candidate):
        return candidate
    return _INSTALL_PATHS.get(platform.system(), candidate)


def _parse_version(content):
    m = re.search(r'AGENT_VERSION\s*=\s*["\']([^"\']+)["\']', content)
    return m.group(1) if m else None


def _version_tuple(version_str):
    try:
        return tuple(int(x) for x in version_str.strip().split("."))
    except Exception:
        return (0,)


def _fetch(url, timeout=UPDATE_FETCH_TIMEOUT):
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
            return True, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        log_write("WARNING", "Auto-update: HTTP {} fetching {}".format(e.code, url))
        return False, ""
    except Exception as e:
        log_write("WARNING", "Auto-update: Failed to fetch {}: {}".format(url, e))
        return False, ""


def _state_path():
    return _STATE_PATHS.get(platform.system(), os.path.join(os.path.dirname(_installed_path()), ".update_check_ts"))


def _lock_path():
    return _LOCK_PATHS.get(platform.system(), os.path.join(os.path.dirname(_installed_path()), ".update.lock"))


def _read_last_check_ts():
    try:
        with open(_state_path(), "r") as f:
            return float(f.read().strip())
    except Exception:
        return 0.0


def _write_last_check_ts():
    try:
        dir_path = os.path.dirname(_state_path())
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path)
        with open(_state_path(), "w") as f:
            f.write(str(time.time()))
    except Exception as e:
        log_write("WARNING", "Auto-update: could not write state file: {}".format(e))


def check_and_update(log_debug_fn=None):
    elapsed = time.time() - _read_last_check_ts()
    if elapsed < UPDATE_CHECK_INTERVAL:
        if log_debug_fn:
            log_debug_fn(
                "Auto-update: skipping check ({:.0f}s / {}s since last check)".format(
                    elapsed, UPDATE_CHECK_INTERVAL
                )
            )
        return "skipped"

    lock = FileLock(_lock_path(), timeout=60)
    if not lock.acquire(blocking=False):
        if log_debug_fn:
            log_debug_fn("Auto-update: already running, skipping")
        return "skipped"

    try:
        _write_last_check_ts()

        if log_debug_fn:
            log_debug_fn("Auto-update: checking {} for latest version".format(GITHUB_RAW_URL))

        ok, remote_content = _fetch(GITHUB_RAW_URL)
        if not ok or not remote_content:
            log_write("WARNING", "Auto-update: could not reach GitHub – skipping")
            return "skipped"

        remote_version = _parse_version(remote_content)
        if not remote_version:
            log_write("WARNING", "Auto-update: could not parse version from remote file – skipping")
            return "skipped"

        if log_debug_fn:
            log_debug_fn(
                "Auto-update: local={}, remote={}".format(AGENT_VERSION, remote_version)
            )

        if _version_tuple(remote_version) <= _version_tuple(AGENT_VERSION):
            log_write("INFO", "Auto-update: already up to date (v{})".format(AGENT_VERSION))
            return "up_to_date"

        log_write(
            "INFO",
            "Auto-update: new version available ({} → {}), applying…".format(
                AGENT_VERSION, remote_version
            ),
        )

        target_path = _installed_path()
        install_dir = os.path.dirname(target_path)
        backup_path = target_path + ".bak"

        try:
            try:
                ast.parse(remote_content)
            except SyntaxError as e:
                log_write("ERROR", "Auto-update: downloaded agent.py has syntax error – aborting: {}".format(e))
                return "skipped"

            if os.path.isfile(target_path):
                shutil.copy2(target_path, backup_path)
                if log_debug_fn:
                    log_debug_fn("Auto-update: backup written to {}".format(backup_path))

            atomic_write(target_path, remote_content, encoding="utf-8")

            for rel_path in _MODULE_FILES:
                mod_url = GITHUB_BASE_URL + "/" + rel_path
                ok, mod_content = _fetch(mod_url)
                if not ok:
                    log_write("WARNING", "Auto-update: could not fetch {} – skipping module".format(rel_path))
                    continue
                mod_dest = os.path.join(install_dir, rel_path.replace("/", os.sep))
                mod_dir = os.path.dirname(mod_dest)
                if mod_dir and not os.path.isdir(mod_dir):
                    os.makedirs(mod_dir)
                atomic_write(mod_dest, mod_content, encoding="utf-8")
                if log_debug_fn:
                    log_debug_fn("Auto-update: updated {}".format(rel_path))

            log_write(
                "INFO",
                "Auto-update: successfully updated to v{} (backup: {})".format(
                    remote_version, backup_path
                ),
            )
            return "updated"

        except PermissionError:
            log_write(
                "WARNING",
                "Auto-update: no write permission for {} – run agent as root/admin".format(target_path),
            )
            return "skipped"
        except Exception as e:
            log_write("ERROR", "Auto-update: failed to write new version: {}".format(e))
            return "skipped"
    finally:
        lock.release()
