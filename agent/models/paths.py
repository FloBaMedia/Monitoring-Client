"""Install paths and branding constants for ServerMetry Agent."""

import os
import platform

# ServerMetry (current)
LINUX_INSTALL_DIR = "/etc/servermetry"
WINDOWS_INSTALL_DIR = r"C:\ProgramData\ServerMetry"
USER_CONFIG_DIR = os.path.expanduser("~/.config/servermetry")

# Legacy ServerPulse paths (migrated on agent update)
LEGACY_LINUX_INSTALL_DIR = "/etc/serverpulse"
LEGACY_WINDOWS_INSTALL_DIR = r"C:\ProgramData\ServerPulse"
LEGACY_USER_CONFIG_DIR = os.path.expanduser("~/.config/serverpulse")

CONFIG_SECTION = "servermetry"
LEGACY_CONFIG_SECTION = "serverpulse"

CRON_MARKER = "servermetry/agent.py"
LEGACY_CRON_MARKER = "serverpulse/agent.py"

WINDOWS_TASK_NAME = "ServerMetryAgent"
LEGACY_WINDOWS_TASK_NAME = "ServerPulseAgent"

LINUX_LOG_PATH = "/var/log/servermetry-agent.log"
LEGACY_LINUX_LOG_PATH = "/var/log/serverpulse-agent.log"

TEMPLATE_CRON_MARKER = "# servermetry-template-"
LEGACY_TEMPLATE_CRON_MARKER = "# serverpulse-template-"

MIGRATION_MARKER = ".servermetry-migrated"


def install_dir():
    """Preferred install directory for the current platform."""
    if platform.system() == "Windows":
        return WINDOWS_INSTALL_DIR
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return LINUX_INSTALL_DIR
    return USER_CONFIG_DIR


def legacy_install_dir():
    """Legacy ServerPulse install directory for the current platform."""
    if platform.system() == "Windows":
        return LEGACY_WINDOWS_INSTALL_DIR
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return LEGACY_LINUX_INSTALL_DIR
    return LEGACY_USER_CONFIG_DIR


def resolve_install_dir():
    """Return the directory that currently holds agent.py, preferring ServerMetry."""
    new_dir = install_dir()
    legacy_dir = legacy_install_dir()
    if os.path.isfile(os.path.join(new_dir, "agent.py")):
        return new_dir
    if os.path.isfile(os.path.join(legacy_dir, "agent.py")):
        return legacy_dir
    return new_dir


def agent_conf_path(base_dir=None):
    return os.path.join(base_dir or resolve_install_dir(), "agent.conf")


def agent_py_path(base_dir=None):
    return os.path.join(base_dir or resolve_install_dir(), "agent.py")


def windows_log_path():
    return os.path.join(WINDOWS_INSTALL_DIR, "agent.log")


def legacy_windows_log_path():
    return os.path.join(LEGACY_WINDOWS_INSTALL_DIR, "agent.log")
