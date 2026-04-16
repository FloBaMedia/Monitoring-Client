"""Configuration loading and validation for ServerPulse Agent."""

import configparser
import getpass
import os
import platform
import sys

from models.constants import DEFAULT_API_URL

REQUIRED_FIELDS = [
    ("api_url", "API URL",              DEFAULT_API_URL, False),
    ("api_key", "API Key (sp_live_...)", None,           True),
]


def _default_conf_path():
    """Return the preferred writable config path for the current platform/user."""
    if platform.system() == "Windows":
        return "C:\\ProgramData\\ServerPulse\\agent.conf"
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return "/etc/serverpulse/agent.conf"
    return os.path.expanduser("~/.config/serverpulse/agent.conf")


def _conf_search_paths(override_path=None):
    if override_path:
        return [override_path]
    return [
        "C:\\ProgramData\\ServerPulse\\agent.conf",
        "/etc/serverpulse/agent.conf",
        os.path.expanduser("~/.config/serverpulse/agent.conf"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.conf"),
    ]


def load_config(override_path=None):
    """
    Returns (values_dict, conf_path) where values_dict contains whatever keys
    were found (may be incomplete). conf_path is the file that was read, or None
    (env vars) or '' (no file found).

    Priority: ENV vars > config files.
    """
    from utils.logging import log_debug

    values = {}

    env_url   = os.environ.get("SERVERPULSE_API_URL", "").strip()
    env_key   = os.environ.get("SERVERPULSE_API_KEY", "").strip()
    env_debug = os.environ.get("SERVERPULSE_DEBUG", "").strip().lower() in ("1", "true", "yes")
    if env_url:
        values["api_url"] = env_url.rstrip("/")
    if env_key:
        values["api_key"] = env_key
    if env_debug:
        values["debug"] = True
    if env_url and env_key:
        log_debug("Config loaded from environment variables")
        return values, None

    cfg = configparser.ConfigParser()
    for path in _conf_search_paths(override_path):
        log_debug("Checking config path: {}".format(path))
        if not os.path.exists(path):
            continue
        try:
            cfg.read(path, encoding="utf-8")
            sec = "serverpulse"
            if cfg.has_section(sec):
                for key, _, _, _ in REQUIRED_FIELDS:
                    val = cfg.get(sec, key, fallback="").strip()
                    if val:
                        values[key] = val.rstrip("/") if key == "api_url" else val
                values["debug"] = cfg.get(sec, "debug", fallback="false").strip().lower() in ("1", "true", "yes")
                log_debug("Config loaded from {}".format(path))
                return values, path
        except Exception as e:
            from utils.logging import log_write
            log_write("WARNING", "Could not read config {}: {}".format(path, e))

    return values, ""


def _save_config(path, values):
    """Write all known values back to the config file."""
    from utils.logging import log_debug

    try:
        conf_dir = os.path.dirname(path)
        if conf_dir and not os.path.exists(conf_dir):
            os.makedirs(conf_dir)
        cfg = configparser.ConfigParser()
        if os.path.exists(path):
            cfg.read(path, encoding="utf-8")
        if not cfg.has_section("serverpulse"):
            cfg.add_section("serverpulse")
        for key, val in values.items():
            if key == "debug":
                cfg.set("serverpulse", "debug", "true" if val else "false")
            else:
                cfg.set("serverpulse", key, str(val))
        with open(path, "w", encoding="utf-8") as f:
            cfg.write(f)
        if not platform.system() == "Windows":
            os.chmod(path, 0o600)
        log_debug("Config saved to {}".format(path))
    except Exception as e:
        from utils.logging import log_write
        log_write("WARNING", "Could not save config to {}: {}".format(path, e))


def ensure_config(values, conf_path, override_path=None):
    """
    Check that all REQUIRED_FIELDS are present. Prompt for any that are missing,
    then save the updated config back to the file.
    Returns the completed values dict (guaranteed to have all required keys).
    Exits if non-interactive and values are still missing.
    """
    from utils.logging import log_write

    missing = [
        (key, label, default, secret)
        for key, label, default, secret in REQUIRED_FIELDS
        if not values.get(key)
    ]

    if not missing:
        return values

    if conf_path is None:
        log_write("ERROR", "Environment variables set but missing: {}".format(
            ", ".join(k for k, *_ in missing)))
        sys.exit(1)

    save_path = conf_path if conf_path else (override_path or _default_conf_path())

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive:
        log_write("ERROR", "Config incomplete. Missing fields: {}. "
                  "Add them to {} or set env vars.".format(
                      ", ".join(k for k, *_ in missing), save_path))
        sys.exit(1)

    print("")
    if conf_path:
        print("Config found at {} but missing fields:".format(conf_path))
    else:
        print("No configuration found. Let's set it up now.")
        print("Config will be saved to: {}".format(save_path))
    print("")

    for key, label, default, secret in missing:
        while True:
            try:
                if secret:
                    entered = getpass.getpass("  {}: ".format(label)).strip()
                elif default:
                    entered = input("  {} [{}]: ".format(label, default)).strip()
                    if not entered:
                        entered = default
                else:
                    entered = input("  {}: ".format(label)).strip()
            except (KeyboardInterrupt, EOFError):
                print("\nSetup cancelled.")
                sys.exit(1)

            if entered:
                values[key] = entered.rstrip("/") if key == "api_url" else entered
                break
            print("  ✗ This field is required.")

    _save_config(save_path, values)
    print("")
    print("  ✓ Config saved to {}".format(save_path))
    print("")

    return values