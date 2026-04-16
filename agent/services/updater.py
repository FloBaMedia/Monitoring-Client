"""
ServerPulse Agent self-updater.
Fetches the latest agent.py from GitHub, compares versions, and updates in-place.
Mirrors the same URL used by the Linux/Windows installers.
"""

import os
import platform
import re
import shutil
import ssl
import sys
import urllib.error
import urllib.request

from models.constants import AGENT_VERSION
from utils.logging import log_write

GITHUB_RAW_URL = (
    "https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent/agent.py"
)

# Where the installers place agent.py
_INSTALL_PATHS = {
    "Linux": "/etc/serverpulse/agent.py",
    "Darwin": "/etc/serverpulse/agent.py",
    "Windows": r"C:\ProgramData\ServerPulse\agent.py",
}


def _installed_path():
    """Return the path of the currently running agent.py."""
    # Use __file__ of the main agent script (two levels up from services/)
    candidate = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "agent.py")
    )
    if os.path.isfile(candidate):
        return candidate
    # Fallback to OS-default install path
    return _INSTALL_PATHS.get(platform.system(), candidate)


def _parse_version(content):
    """Extract AGENT_VERSION string from Python source content. Returns None if not found."""
    m = re.search(r'AGENT_VERSION\s*=\s*["\']([^"\']+)["\']', content)
    return m.group(1) if m else None


def _version_tuple(version_str):
    """Convert '1.2.3' to (1, 2, 3) for comparison."""
    try:
        return tuple(int(x) for x in version_str.strip().split("."))
    except Exception:
        return (0,)


def _fetch(url, timeout=15):
    """Download text content from a URL. Returns (success, content_str)."""
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


def check_and_update(log_debug_fn=None):
    """
    Check GitHub for a newer agent version. If found and enableAutoUpdates is True,
    download the new agent.py, back up the old one, and replace it in-place.

    The new version takes effect on the next scheduled run (no restart needed for
    one-shot / cron-based agents).

    Returns:
        'updated'    – new version was downloaded and installed
        'up_to_date' – already running the latest version
        'skipped'    – update check failed (network error, parse error, etc.)
    """
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

    # Newer version available – apply update
    log_write(
        "INFO",
        "Auto-update: new version available ({} → {}), applying…".format(
            AGENT_VERSION, remote_version
        ),
    )

    target_path = _installed_path()
    backup_path = target_path + ".bak"

    try:
        # 1. Back up current file
        if os.path.isfile(target_path):
            shutil.copy2(target_path, backup_path)
            if log_debug_fn:
                log_debug_fn("Auto-update: backup written to {}".format(backup_path))

        # 2. Write new file atomically (temp file → rename)
        tmp_path = target_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(remote_content)

        # Basic sanity check: new file must be parseable Python
        import ast
        try:
            ast.parse(remote_content)
        except SyntaxError as e:
            os.remove(tmp_path)
            log_write("ERROR", "Auto-update: downloaded file has syntax error – aborting: {}".format(e))
            return "skipped"

        os.replace(tmp_path, target_path)

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
        # Attempt rollback
        if os.path.isfile(backup_path) and os.path.isfile(target_path + ".tmp"):
            try:
                os.remove(target_path + ".tmp")
            except Exception:
                pass
        return "skipped"
