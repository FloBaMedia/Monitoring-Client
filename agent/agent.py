#!/usr/bin/env python3
"""
ServerPulse Agent v1.0.0
Module-based Python 3.6+ monitoring agent. No external dependencies.
Usage:
  python agent.py                          # normal run (reads config, POSTs metrics)
  python agent.py --dry-run                # print collected metrics as JSON, no HTTP
  python agent.py --config /path/to.conf  # override config file path
  python agent.py --apply-template <id>                    # fetch and execute a server template
  python agent.py --apply-template <id> --schedule "0 3 * * *"  # schedule template via cron
  python agent.py --apply-template <id> --schedule remove        # remove scheduled cron entry
  python agent.py --no-apply-config       # skip fetching and applying remote config
"""

import json
import os
import platform
import socket
import subprocess
import sys

from models.constants import AGENT_VERSION, DEFAULT_API_URL
from models.limits import SCRIPT_EXEC_TIMEOUT, STATE_ENCODING
from utils.config import ensure_config, load_config
from utils.lock import FileLock, atomic_write
from utils.logging import log_debug, log_write

_CONFIG_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".config_state")
_CONFIG_LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".config_state.lock")


def _load_config_state():
    try:
        with open(_CONFIG_STATE_FILE, "r", encoding=STATE_ENCODING) as f:
            data = json.load(f)
        return data.get("configChangedAt"), data.get("config", {}), data.get("services", [])
    except Exception:
        return None, {}, []


def _save_config_state(config_changed_at, config_dict, services=None):
    try:
        atomic_write(_CONFIG_STATE_FILE, json.dumps({
            "configChangedAt": config_changed_at,
            "config": config_dict,
            "services": services or [],
        }), encoding=STATE_ENCODING)
    except Exception as e:
        log_write("WARNING", "config_state: could not write state file: {}".format(e))


def _check_service_port(port, protocol=None, timeout=3):
    """TCP connect check. Returns ("up", None) or ("down", error_string)."""
    if protocol and protocol.upper() == "UDP":
        return "up", None  # UDP unreliable without app-level protocol
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return "up", None
    except socket.timeout:
        return "down", "Connection timed out"
    except ConnectionRefusedError:
        return "down", "Connection refused"
    except OSError as e:
        return "down", str(e)


def parse_args():
    """Minimal arg parsing without argparse."""
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    check = "--check" in args
    debug = "--debug" in args
    no_apply_config = "--no-apply-config" in args
    config_path = None
    template_id = None
    schedule = None
    if "--config" in args:
        idx = args.index("--config")
        if idx + 1 < len(args):
            config_path = args[idx + 1]
    if "--apply-template" in args:
        idx = args.index("--apply-template")
        if idx + 1 < len(args):
            template_id = args[idx + 1]
    if "--schedule" in args:
        idx = args.index("--schedule")
        if idx + 1 < len(args):
            schedule = args[idx + 1]
    return dry_run, check, debug, config_path, template_id, schedule, no_apply_config


def _print_check(metrics):
    """Print a compact human-readable summary of collected metrics."""
    disks = metrics.get("diskUsages", [])
    nets = metrics.get("networkInterfaces", [])
    top = metrics.get("topProcesses", [])
    print("  OS:        {}".format(metrics.get("os", "?")))
    print("  Kernel:    {}".format(metrics.get("kernelVersion", "?")))
    print("  CPU:       {}% (cores: {}, threads: {})".format(
        metrics.get("cpuUsagePercent", 0),
        metrics.get("cpuCores", "?"),
        metrics.get("cpuThreads", "?"),
    ))
    print("  RAM:       {}% ({}/{} MB)".format(
        metrics.get("memUsagePercent", 0),
        metrics.get("memUsedMb", 0),
        metrics.get("memTotalMb", 0),
    ))
    print("  Disks:     {} found{}".format(
        len(disks),
        " — " + ", ".join(
            "{} ({:.0f}%)".format(d["mountpoint"], d["usagePercent"]) for d in disks[:3]
        ) if disks else "",
    ))
    print("  Network:   {} interface(s)".format(len(nets)))
    print("  Processes: {}".format(metrics.get("processCount", 0)))
    if metrics.get("pendingUpdates") is not None:
        print("  Updates:   {} pending ({} security)".format(
            metrics.get("pendingUpdates", 0),
            metrics.get("pendingSecurityUpdates", 0),
        ))
    if top:
        print("  Top proc:  {} ({:.1f}% CPU)".format(top[0]["name"], top[0]["cpuPercent"]))


def execute_script(script_content, log_debug_fn=None):
    if log_debug_fn:
        log_debug_fn("Executing script ({} chars)".format(len(script_content)))

    log_write("INFO", "Executing server setup script...")

    is_windows = platform.system() == "Windows"
    if is_windows:
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-Command", script_content]
        shell = False
    else:
        cmd = ["bash", "-c", script_content]
        shell = False

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=SCRIPT_EXEC_TIMEOUT,
            shell=shell,
        )
        result.stdout = result.stdout.decode("utf-8", errors="replace")
        result.stderr = result.stderr.decode("utf-8", errors="replace")
        if result.stdout:
            for line in result.stdout.splitlines():
                log_write("INFO", "[script] {}".format(line))
        if result.stderr:
            for line in result.stderr.splitlines():
                log_write("WARNING", "[script] {}".format(line))

        success = result.returncode == 0
        if success:
            log_write("INFO", "Script executed successfully (exit code: {})".format(result.returncode))
        else:
            log_write("ERROR", "Script failed with exit code: {}".format(result.returncode))

        return success, result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        log_write("ERROR", "Script execution timed out after {} seconds".format(SCRIPT_EXEC_TIMEOUT))
        return False, "", "Timeout after {} seconds".format(SCRIPT_EXEC_TIMEOUT), -1
    except Exception as e:
        log_write("ERROR", "Script execution failed: {}".format(e))
        return False, "", str(e), -1


def apply_template_script(api_url, api_key, template_id, server_id, log_debug_fn=None):
    from client.api import apply_template

    log_write("INFO", "Fetching template {} for server {}...".format(template_id, server_id))
    success, result = apply_template(api_url, api_key, template_id, server_id, log_debug_fn=log_debug_fn)

    if not success:
        log_write("ERROR", "Failed to fetch template from API")
        return False

    script_content = result.get("scriptContent")
    if not script_content:
        log_write("INFO", "Template has no scriptContent to execute")
        return True

    return execute_script(script_content, log_debug_fn=log_debug_fn)[0]


def main():
    dry_run, check, cli_debug, config_override, template_id, schedule, no_apply_config = parse_args()
    DEBUG = cli_debug

    values, conf_path = load_config(config_override)

    if values.get("debug"):
        DEBUG = True

    if DEBUG:
        log_debug("ServerPulse Agent {} starting (debug mode)".format(AGENT_VERSION), debug_flag=DEBUG)
        log_debug("Platform: {} {}".format(platform.system(), platform.release()), debug_flag=DEBUG)
        log_debug("dry_run={}".format(dry_run), debug_flag=DEBUG)
        if template_id:
            log_debug("template_id={}".format(template_id), debug_flag=DEBUG)

    if not dry_run:
        values = ensure_config(values, conf_path, config_override)

    api_url = values.get("api_url", DEFAULT_API_URL)
    api_key = values.get("api_key", "")

    if template_id:
        if schedule is not None:
            from services.config_applier import schedule_template
            ok = schedule_template(template_id, schedule)
            sys.exit(0 if ok else 1)

        server_id = values.get("server_id", "")
        if not server_id:
            log_write("ERROR", "server_id not configured. Cannot apply template.")
            sys.exit(1)
        ok = apply_template_script(
            api_url, api_key, template_id, server_id, log_debug_fn=lambda msg: log_debug(msg, debug_flag=DEBUG)
        )
        sys.exit(0 if ok else 1)

    config_lock = FileLock(_CONFIG_LOCK_FILE, timeout=30)
    if not config_lock.acquire(blocking=False):
        log_write("WARNING", "Config state locked by another process, skipping config update")
        no_apply_config = True

    stored_changed_at, remote_config, stored_services = (None, {}, []) if (dry_run or no_apply_config) else _load_config_state()

    if not no_apply_config:
        config_lock.release()

    _system = platform.system()
    if _system == "Windows":
        from services.windows import collect_windows_metrics
        metrics = collect_windows_metrics()
    elif _system == "Darwin":
        from services.darwin import collect_darwin_metrics
        metrics = collect_darwin_metrics()
    else:
        from services.linux import collect_linux_metrics
        metrics = collect_linux_metrics()

    log_debug("Metrics collected successfully", debug_flag=DEBUG)

    # Check service ports from last-known config
    if stored_services and not dry_run and not check:
        service_statuses = []
        for svc in stored_services:
            port = svc.get("port")
            if not port:
                continue
            protocol = svc.get("protocol") or "TCP"
            status, error = _check_service_port(int(port), protocol)
            entry = {"serviceId": svc["id"], "status": status}
            if error:
                entry["error"] = error
            service_statuses.append(entry)
        if service_statuses:
            metrics["serviceStatuses"] = service_statuses

    if check:
        _print_check(metrics)
        sys.exit(0)

    if dry_run:
        print(json.dumps(metrics, indent=2))
        sys.exit(0)

    from client.api import post_metrics
    ok, config_changed_at = post_metrics(
        api_url, api_key, metrics, log_debug_fn=lambda msg: log_debug(msg, debug_flag=DEBUG)
    )

    if ok and not no_apply_config and config_changed_at != stored_changed_at:
        from client.api import get_config
        from services.config_applier import apply_config

        if stored_changed_at is None:
            log_debug("No cached config — fetching for the first time", debug_flag=DEBUG)
        else:
            log_debug("Config changed on server — re-fetching", debug_flag=DEBUG)

        config_ok, fetched_config, fetched_services = get_config(
            api_url, api_key, log_debug_fn=lambda msg: log_debug(msg, debug_flag=DEBUG)
        )
        if config_ok and fetched_config:
            apply_config(fetched_config, log_debug_fn=lambda msg: log_debug(msg, debug_flag=DEBUG))
            remote_config = fetched_config
            stored_services = fetched_services
            _save_config_state(config_changed_at, remote_config, fetched_services)
        else:
            log_debug("Could not fetch config from server", debug_flag=DEBUG)

    if ok and not no_apply_config and remote_config.get("enableAutoUpdates"):
        from services.updater import check_and_update
        check_and_update(log_debug_fn=lambda msg: log_debug(msg, debug_flag=DEBUG))

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
