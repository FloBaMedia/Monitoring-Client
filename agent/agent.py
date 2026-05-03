#!/usr/bin/env python3
"""ServerPulse Agent — run with -h or --help for usage."""

import json
import os
import platform
import socket
import subprocess
import sys


def _bootstrap():
    """Download any missing module files from GitHub before imports run."""
    import ssl
    import urllib.request

    _BOOTSTRAP_BASE = "https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent"
    _BOOTSTRAP_FILES = [
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

    script_dir = os.path.dirname(os.path.abspath(__file__))
    missing = [
        f for f in _BOOTSTRAP_FILES
        if not os.path.isfile(os.path.join(script_dir, f.replace("/", os.sep)))
    ]
    if not missing:
        return

    print("Bootstrap: downloading {} missing module file(s)...".format(len(missing)))
    ctx = ssl.create_default_context()
    failed = []
    for rel_path in missing:
        url = _BOOTSTRAP_BASE + "/" + rel_path
        dest = os.path.join(script_dir, rel_path.replace("/", os.sep))
        dest_dir = os.path.dirname(dest)
        if dest_dir and not os.path.isdir(dest_dir):
            os.makedirs(dest_dir)
        try:
            with urllib.request.urlopen(url, timeout=15, context=ctx) as resp:
                content = resp.read()
            with open(dest, "wb") as f:
                f.write(content)
            print("  + {}".format(rel_path))
        except Exception as e:
            print("  ERROR: could not download {}: {}".format(rel_path, e))
            failed.append(rel_path)

    if failed:
        print("Bootstrap failed for: {}".format(", ".join(failed)))
        print("Please check your internet connection or install manually.")
        sys.exit(1)

    print("Bootstrap complete.\n")


_bootstrap()

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
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return "up", None
    except socket.timeout:
        return "down", "Connection timed out"
    except ConnectionRefusedError:
        return "down", "Connection refused"
    except OSError as e:
        return "down", str(e)


HELP_TEXT = """\
ServerPulse Agent {version}

Usage: python3 agent.py [OPTIONS]

Options:
  (none)                          Send metrics once and exit
  --info                          Show agent version and configuration info
  --check                         Collect and display metrics locally, no upload
  --dry-run                       Collect metrics, print JSON — no HTTP request
  --check-update                  Check if a newer agent version is available
  --update                        Download and apply the latest agent version now
  --update-status                 Show auto-update schedule (last check, next check)
  --discover-ports                Scan all listening TCP ports and report to server
  --apply-template <id>           Fetch and execute a config template by ID
    --schedule <cron|remove>      Schedule or remove the template as a cron job
  --config <path>                 Path to config file (default: /etc/serverpulse/config.ini)
  --no-apply-config               Skip fetching and applying remote config changes
  --debug                         Enable verbose debug logging
  -h, --help                      Show this help message
"""


def parse_args():
    """Minimal arg parsing without argparse."""
    args = sys.argv[1:]

    if "-h" in args or "--help" in args:
        print(HELP_TEXT.format(version=AGENT_VERSION))
        sys.exit(0)

    info = "--info" in args
    dry_run = "--dry-run" in args
    check = "--check" in args
    check_update = "--check-update" in args
    force_update = "--update" in args
    update_status = "--update-status" in args
    debug = "--debug" in args
    no_apply_config = "--no-apply-config" in args
    discover_ports = "--discover-ports" in args
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

    _KNOWN_FLAGS = {
        "--info", "--dry-run", "--check", "--check-update", "--update", "--update-status",
        "--debug", "--no-apply-config", "--discover-ports",
        "--config", "--apply-template", "--schedule", "-h", "--help",
    }
    _VALUE_FLAGS = {"--config", "--apply-template", "--schedule"}
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in _VALUE_FLAGS:
            skip_next = True
            continue
        if arg.startswith("-") and arg not in _KNOWN_FLAGS:
            print("Unknown option: {}\n".format(arg))
            print(HELP_TEXT.format(version=AGENT_VERSION))
            sys.exit(1)

    return info, dry_run, check, check_update, force_update, update_status, debug, config_path, template_id, schedule, no_apply_config, discover_ports


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
    info, dry_run, check, check_update, force_update, show_update_status, cli_debug, config_override, template_id, schedule, no_apply_config, discover_ports = parse_args()
    DEBUG = cli_debug

    values, conf_path = load_config(config_override)

    if info:
        api_url = values.get("api_url", DEFAULT_API_URL)
        server_id = values.get("server_id", "(not configured)")
        print("ServerPulse Agent")
        print("  Version:    {}".format(AGENT_VERSION))
        print("  Python:     {}".format(platform.python_version()))
        print("  Platform:   {} {} ({})".format(
            platform.system(), platform.release(), platform.machine()
        ))
        print("  Hostname:   {}".format(platform.node()))
        print("  Config:     {}".format(conf_path))
        print("  API URL:    {}".format(api_url))
        print("  Server ID:  {}".format(server_id))
        sys.exit(0)

    if show_update_status:
        try:
            from services.updater import update_status
        except ImportError:
            print("ERROR: services/updater.py is missing. Run with --update to bootstrap.")
            sys.exit(1)
        auto_updates = values.get("enable_auto_updates")
        update_status(auto_updates_enabled=auto_updates if auto_updates is not None else None)
        sys.exit(0)

    if check_update or force_update:
        try:
            from services.updater import check_and_update, check_version
        except ImportError:
            print(
                "ERROR: services/updater.py is missing. Run:\n"
                "  curl -o /etc/serverpulse/services/updater.py "
                "https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent/services/updater.py"
            )
            sys.exit(1)

        if check_update:
            ok = check_version(log_debug_fn=lambda msg: log_debug(msg, debug_flag=cli_debug) if cli_debug else None)
            sys.exit(0 if ok else 1)

        print("Checking for updates (forced)...")
        result = check_and_update(
            force=True,
            log_debug_fn=lambda msg: log_debug(msg, debug_flag=cli_debug) if cli_debug else None,
        )
        if result == "updated":
            print("Agent updated successfully. Restart the agent to use the new version.")
            sys.exit(0)
        elif result == "up_to_date":
            print("Agent is already up to date.")
            sys.exit(0)
        else:
            print("Update failed or was skipped. Check logs for details.")
            sys.exit(1)

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

    if discover_ports:
        _system = platform.system()
        if _system != "Linux":
            log_write("ERROR", "--discover-ports is only supported on Linux")
            sys.exit(1)
        from services.linux import read_listening_ports
        from client.api import post_discovered_ports
        ports = read_listening_ports()
        print("Found {} listening TCP port(s): {}".format(
            len(ports), ", ".join(str(p["port"]) for p in ports) if ports else "none"
        ))
        ok, err = post_discovered_ports(api_url, api_key, ports)
        if ok:
            print("Reported to server successfully.")
        else:
            log_write("ERROR", "Failed to report ports: {}".format(err))
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
            protocol = (svc.get("protocol") or "TCP").upper()
            if protocol == "UDP":
                continue  # UDP requires app-level probing; skip silently
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
