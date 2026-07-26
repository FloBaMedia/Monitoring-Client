"""Configuration loading and validation for ServerMetry Agent."""

import configparser
import getpass
import os
import platform
import sys

from models.constants import DEFAULT_API_URL
from models.paths import (
    CONFIG_SECTION,
    LEGACY_CONFIG_SECTION,
    agent_conf_path,
    install_dir,
    legacy_install_dir,
    resolve_install_dir,
)

REQUIRED_FIELDS = [
    ("api_key", "API Key (sp_live_...)", None, True),
]

# Optional fields: read from config/env if present, but never prompted for
# and never required.
OPTIONAL_FIELDS = [
    ("server_id", "SERVERMETRY_SERVER_ID", "SERVERPULSE_SERVER_ID"),
]


def _env(name, legacy_name):
    return (os.environ.get(name, "") or os.environ.get(legacy_name, "")).strip()


def _default_conf_path():
    return agent_conf_path()


def _conf_search_paths(override_path=None):
    if override_path:
        return [override_path]
    paths = []
    for base in (resolve_install_dir(), install_dir(), legacy_install_dir()):
        p = os.path.join(base, "agent.conf")
        if p not in paths:
            paths.append(p)
    paths.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.conf"))
    return paths


def _read_section(cfg, values):
    for sec in (CONFIG_SECTION, LEGACY_CONFIG_SECTION):
        if not cfg.has_section(sec):
            continue
        for key, _, _, _ in REQUIRED_FIELDS:
            if not values.get(key):
                val = cfg.get(sec, key, fallback="").strip()
                if val:
                    values[key] = val
        if not values.get("api_url"):
            api_url = cfg.get(sec, "api_url", fallback="").strip()
            if api_url:
                values["api_url"] = api_url.rstrip("/")
        if "debug" not in values:
            values["debug"] = cfg.get(sec, "debug", fallback="false").strip().lower() in (
                "1",
                "true",
                "yes",
            )
        for key, _, _ in OPTIONAL_FIELDS:
            if not values.get(key):
                val = cfg.get(sec, key, fallback="").strip()
                if val:
                    values[key] = val
        return True
    return False


def load_config(override_path=None):
    """
    Returns (values_dict, conf_path) where values_dict contains whatever keys
    were found (may be incomplete). conf_path is the file that was read, or None
    (env vars) or '' (no file found).

    Priority: ENV vars > config files.
    api_url is optional — when omitted, the agent uses DEFAULT_API_URL from constants.
    """
    from utils.logging import log_debug

    values = {}

    env_url = _env("SERVERMETRY_API_URL", "SERVERPULSE_API_URL")
    env_key = _env("SERVERMETRY_API_KEY", "SERVERPULSE_API_KEY")
    env_debug = _env("SERVERMETRY_DEBUG", "SERVERPULSE_DEBUG").lower() in ("1", "true", "yes")
    if env_url:
        values["api_url"] = env_url.rstrip("/")
    if env_key:
        values["api_key"] = env_key
    if env_debug:
        values["debug"] = True
    for key, env_name, legacy_env_name in OPTIONAL_FIELDS:
        env_val = _env(env_name, legacy_env_name)
        if env_val:
            values[key] = env_val
    if env_url and env_key:
        log_debug("Config loaded from environment variables")
        return values, None

    cfg = configparser.ConfigParser()
    for path in _conf_search_paths(override_path):
        log_debug("Checking config path: {}".format(path))
        if not os.path.exists(path):
            continue
        try:
            # utf-8-sig tolerates a UTF-8 BOM (PowerShell Set-Content -Encoding UTF8)
            cfg.read(path, encoding="utf-8-sig")
            if _read_section(cfg, values):
                log_debug("Config loaded from {}".format(path))
                return values, path
        except Exception as e:
            from utils.logging import log_write
            log_write("WARNING", "Could not read config {}: {}".format(path, e))

    if values:
        log_debug("Partial config from environment variables")
        return values, None

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
            cfg.read(path, encoding="utf-8-sig")
        if not cfg.has_section(CONFIG_SECTION):
            cfg.add_section(CONFIG_SECTION)
        if cfg.has_section(LEGACY_CONFIG_SECTION):
            cfg.remove_section(LEGACY_CONFIG_SECTION)
        for key, val in values.items():
            if key == "debug":
                cfg.set(CONFIG_SECTION, "debug", "true" if val else "false")
            elif key == "api_url" and not val:
                if cfg.has_option(CONFIG_SECTION, "api_url"):
                    cfg.remove_option(CONFIG_SECTION, "api_url")
            else:
                cfg.set(CONFIG_SECTION, key, str(val))
        if platform.system() != "Windows":
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                cfg.write(f)
        else:
            with open(path, "w", encoding="utf-8") as f:
                cfg.write(f)
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

    if conf_path is None and not missing:
        return values

    if conf_path is None:
        log_write("ERROR", "Environment variables set but missing: {}".format(
            ", ".join(k for k, *_ in missing)))
        sys.exit(1)

    save_path = conf_path if conf_path else (override_path or _default_conf_path())

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive:
        log_write("ERROR", "Config incomplete. Missing fields: {}. "
                  "Add them to {} or set SERVERMETRY_API_KEY.".format(
                      ", ".join(k for k, *_ in missing), save_path))
        sys.exit(1)

    print("")
    if conf_path:
        print("Config found at {} but missing fields:".format(conf_path))
    else:
        print("No configuration found. Let's set it up now.")
        print("Config will be saved to: {}".format(save_path))
        print("Default API URL: {}".format(DEFAULT_API_URL))
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
                values[key] = entered
                break
            print("  ✗ This field is required.")

    save_values = {k: v for k, v in values.items() if k != "api_url" or v}
    _save_config(save_path, save_values)
    print("")
    print("  ✓ Config saved to {}".format(save_path))
    print("")

    return values
