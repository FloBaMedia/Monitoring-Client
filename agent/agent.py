#!/usr/bin/env python3
"""
ServerPulse Agent v1.0.0
Module-based Python 3.6+ monitoring agent. No external dependencies.
Usage:
  python agent.py                          # normal run (reads config, POSTs metrics)
  python agent.py --dry-run                # print collected metrics as JSON, no HTTP
  python agent.py --config /path/to.conf  # override config file path
  python agent.py --apply-template <id>   # fetch and execute a server template
"""

import platform
import subprocess
import sys

from models.constants import AGENT_VERSION, DEFAULT_API_URL
from utils.config import ensure_config, load_config
from utils.logging import log_debug, log_write


def parse_args():
    """Minimal arg parsing without argparse. Returns (dry_run, debug, config_path, template_id)."""
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    debug = "--debug" in args
    config_path = None
    template_id = None
    if "--config" in args:
        idx = args.index("--config")
        if idx + 1 < len(args):
            config_path = args[idx + 1]
    if "--apply-template" in args:
        idx = args.index("--apply-template")
        if idx + 1 < len(args):
            template_id = args[idx + 1]
    return dry_run, debug, config_path, template_id


def execute_script(script_content, log_debug_fn=None):
    """
    Execute a shell script locally.
    Returns (success, stdout, stderr, returncode).
    """
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
            capture_output=True,
            text=True,
            timeout=300,
            shell=shell,
        )
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
        log_write("ERROR", "Script execution timed out after 300 seconds")
        return False, "", "Timeout after 300 seconds", -1
    except Exception as e:
        log_write("ERROR", "Script execution failed: {}".format(e))
        return False, "", str(e), -1


def apply_template_script(api_url, api_key, template_id, server_id, log_debug_fn=None):
    """
    Fetch a template from the API and execute its scriptContent if available.
    """
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
    dry_run, cli_debug, config_override, template_id = parse_args()
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
        server_id = values.get("server_id", "")
        if not server_id:
            log_write("ERROR", "server_id not configured. Cannot apply template.")
            sys.exit(1)
        ok = apply_template_script(
            api_url, api_key, template_id, server_id, log_debug_fn=lambda msg: log_debug(msg, debug_flag=DEBUG)
        )
        sys.exit(0 if ok else 1)

    is_windows = platform.system() == "Windows"
    if is_windows:
        from services.windows import collect_windows_metrics
        metrics = collect_windows_metrics()
    else:
        from services.linux import collect_linux_metrics
        metrics = collect_linux_metrics()

    log_debug("Metrics collected successfully", debug_flag=DEBUG)

    if dry_run:
        import json
        print(json.dumps(metrics, indent=2))
        sys.exit(0)

    from client.api import post_metrics
    ok = post_metrics(api_url, api_key, metrics, log_debug_fn=lambda msg: log_debug(msg, debug_flag=DEBUG))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()