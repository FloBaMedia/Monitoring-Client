#!/usr/bin/env python3
"""
ServerPulse Agent v1.0.0
Module-based Python 3.6+ monitoring agent. No external dependencies.
Usage:
  python agent.py                          # normal run (reads config, POSTs metrics)
  python agent.py --dry-run                # print collected metrics as JSON, no HTTP
  python agent.py --config /path/to.conf  # override config file path
"""

import platform
import sys

from models.constants import AGENT_VERSION, DEFAULT_API_URL
from utils.config import ensure_config, load_config
from utils.logging import log_debug, log_write


def parse_args():
    """Minimal arg parsing without argparse. Returns (dry_run, debug, config_path)."""
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    debug = "--debug" in args
    config_path = None
    if "--config" in args:
        idx = args.index("--config")
        if idx + 1 < len(args):
            config_path = args[idx + 1]
    return dry_run, debug, config_path


def main():
    dry_run, cli_debug, config_override = parse_args()
    DEBUG = cli_debug

    values, conf_path = load_config(config_override)

    if values.get("debug"):
        DEBUG = True

    if DEBUG:
        log_debug("ServerPulse Agent {} starting (debug mode)".format(AGENT_VERSION), debug_flag=DEBUG)
        log_debug("Platform: {} {}".format(platform.system(), platform.release()), debug_flag=DEBUG)
        log_debug("dry_run={}".format(dry_run), debug_flag=DEBUG)

    if not dry_run:
        values = ensure_config(values, conf_path, config_override)

    api_url = values.get("api_url", DEFAULT_API_URL)
    api_key = values.get("api_key", "")

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