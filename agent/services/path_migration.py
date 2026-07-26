"""
Migrate legacy ServerPulse install paths to ServerMetry.
Runs once after auto-update (or on agent startup when legacy paths exist).
"""

import configparser
import os
import platform
import shutil
import subprocess
import tempfile

from models.paths import (
    CONFIG_SECTION,
    CRON_MARKER,
    LEGACY_CONFIG_SECTION,
    LEGACY_CRON_MARKER,
    LEGACY_LINUX_LOG_PATH,
    LEGACY_TEMPLATE_CRON_MARKER,
    LEGACY_WINDOWS_LOG_PATH,
    LEGACY_WINDOWS_TASK_NAME,
    LINUX_LOG_PATH,
    MIGRATION_MARKER,
    TEMPLATE_CRON_MARKER,
    WINDOWS_TASK_NAME,
    agent_conf_path,
    agent_py_path,
    install_dir,
    legacy_install_dir,
    resolve_install_dir,
)
from utils.logging import log_write


def _migration_marker_path():
    return os.path.join(install_dir(), MIGRATION_MARKER)


def migration_completed():
    return os.path.isfile(_migration_marker_path())


def _strip_legacy_api_url(url):
    lowered = url.lower()
    if "sp-api.floba-media" in lowered or "sp.floba-media" in lowered:
        return ""
    return url


def _migrate_config_file(conf_path):
    if not os.path.isfile(conf_path):
        return

    cfg = configparser.ConfigParser()
    cfg.read(conf_path, encoding="utf-8-sig")

    source_section = None
    if cfg.has_section(CONFIG_SECTION):
        source_section = CONFIG_SECTION
    elif cfg.has_section(LEGACY_CONFIG_SECTION):
        source_section = LEGACY_CONFIG_SECTION

    if not source_section:
        return

    if not cfg.has_section(CONFIG_SECTION):
        cfg.add_section(CONFIG_SECTION)

    for option in cfg.options(source_section):
        value = cfg.get(source_section, option, fallback="").strip()
        if option == "api_url":
            value = _strip_legacy_api_url(value)
            if not value:
                if cfg.has_option(CONFIG_SECTION, "api_url"):
                    cfg.remove_option(CONFIG_SECTION, "api_url")
                continue
        cfg.set(CONFIG_SECTION, option, value)

    if source_section == LEGACY_CONFIG_SECTION and cfg.has_section(LEGACY_CONFIG_SECTION):
        cfg.remove_section(LEGACY_CONFIG_SECTION)

    with open(conf_path, "w", encoding="utf-8") as f:
        cfg.write(f)


def _copy_tree(src, dest):
    os.makedirs(dest, exist_ok=True)
    for name in os.listdir(src):
        src_path = os.path.join(src, name)
        dest_path = os.path.join(dest, name)
        if os.path.isdir(src_path):
            _copy_tree(src_path, dest_path)
        else:
            shutil.copy2(src_path, dest_path)

def _migrate_linux_cron(legacy_dir, new_dir):
    ok, current, _ = _run(["crontab", "-l"])
    if not ok or not current:
        return

    legacy_agent = os.path.join(legacy_dir, "agent.py").replace("\\", "/")
    new_agent = os.path.join(new_dir, "agent.py").replace("\\", "/")
    lines = current.splitlines()
    new_lines = []
    changed = False

    for line in lines:
        updated = line
        if LEGACY_CRON_MARKER in line or legacy_agent in line:
            updated = updated.replace(legacy_agent, new_agent)
            updated = updated.replace(LEGACY_CRON_MARKER, CRON_MARKER)
            updated = updated.replace(legacy_dir, new_dir)
            changed = True
        if LEGACY_TEMPLATE_CRON_MARKER in updated:
            updated = updated.replace(LEGACY_TEMPLATE_CRON_MARKER, TEMPLATE_CRON_MARKER)
            changed = True
        new_lines.append(updated)

    if not changed:
        return

    new_cron = "\n".join(new_lines) + "\n"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cron", delete=False, encoding="utf-8") as tmp:
            tmp.write(new_cron)
            tmp_path = tmp.name
        _run(["crontab", tmp_path])
        log_write("INFO", "Crontab updated for ServerMetry install path")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _run(cmd, timeout=30):
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        return result.returncode == 0, stdout, stderr
    except Exception as e:
        return False, "", str(e)


def _migrate_windows_task(legacy_dir, new_dir):
    legacy_agent = os.path.join(legacy_dir, "agent.py")
    new_agent = os.path.join(new_dir, "agent.py")
    new_conf = os.path.join(new_dir, "agent.conf")

    ps = (
        "$legacy = '{legacy_task}'; "
        "$new = '{new_task}'; "
        "$task = Get-ScheduledTask -TaskName $legacy -ErrorAction SilentlyContinue; "
        "if ($task) {{ "
        "  $python = (Get-ScheduledTask -TaskName $legacy).Actions[0].Execute; "
        "  Unregister-ScheduledTask -TaskName $legacy -Confirm:$false -ErrorAction SilentlyContinue; "
        "  $action = New-ScheduledTaskAction -Execute $python -Argument '\"{new_agent}\" --config \"{new_conf}\"'; "
        "  $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) "
        "    -RepetitionInterval (New-TimeSpan -Minutes 1) "
        "    -RepetitionDuration (New-TimeSpan -Days 9999); "
        "  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "    -DontStopIfGoingOnBatteries -StartWhenAvailable "
        "    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew; "
        "  $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' "
        "    -LogonType ServiceAccount -RunLevel Highest; "
        "  Register-ScheduledTask -TaskName $new -Action $action -Trigger $trigger "
        "    -Settings $settings -Principal $principal "
        "    -Description 'ServerMetry monitoring agent' -Force | Out-Null; "
        "  Write-Output 'migrated'; "
        "}} else {{ "
        "  $existing = Get-ScheduledTask -TaskName $new -ErrorAction SilentlyContinue; "
        "  if ($existing) {{ Write-Output 'already_new'; }} else {{ Write-Output 'not_found'; }} "
        "}}"
    ).format(
        legacy_task=LEGACY_WINDOWS_TASK_NAME,
        new_task=WINDOWS_TASK_NAME,
        new_agent=new_agent.replace("\\", "\\\\"),
        new_conf=new_conf.replace("\\", "\\\\"),
    )

    ok, stdout, err = _run(["powershell", "-Command", ps], timeout=60)
    if "migrated" in stdout:
        log_write("INFO", "Scheduled task migrated to '{}'".format(WINDOWS_TASK_NAME))
    elif "already_new" in stdout:
        pass
    elif "not_found" in stdout:
        log_write("WARNING", "Legacy scheduled task not found; update install path manually if needed")
    elif not ok:
        log_write("WARNING", "Could not migrate Windows scheduled task: {}".format(err or stdout))


def migrate_install_paths():
    """
    Copy legacy ServerPulse files to ServerMetry paths and update schedulers.
    Idempotent — safe to call multiple times.
    """
    if migration_completed():
        return False

    legacy_dir = legacy_install_dir()
    new_dir = install_dir()

    if not os.path.isdir(legacy_dir):
        try:
            os.makedirs(new_dir, exist_ok=True)
            with open(_migration_marker_path(), "w", encoding="utf-8") as f:
                f.write("ok\n")
        except OSError:
            pass
        return False

    if legacy_dir == new_dir:
        return False

    log_write("INFO", "Migrating agent install from {} to {}".format(legacy_dir, new_dir))

    try:
        os.makedirs(new_dir, exist_ok=True)
        _copy_tree(legacy_dir, new_dir)

        legacy_conf = os.path.join(legacy_dir, "agent.conf")
        new_conf = os.path.join(new_dir, "agent.conf")
        if os.path.isfile(legacy_conf) and not os.path.isfile(new_conf):
            shutil.copy2(legacy_conf, new_conf)
        _migrate_config_file(new_conf if os.path.isfile(new_conf) else legacy_conf)

        if platform.system() == "Windows":
            if os.path.isfile(LEGACY_WINDOWS_LOG_PATH) and not os.path.isfile(
                os.path.join(new_dir, "agent.log")
            ):
                try:
                    shutil.copy2(LEGACY_WINDOWS_LOG_PATH, os.path.join(new_dir, "agent.log"))
                except OSError:
                    pass
            _migrate_windows_task(legacy_dir, new_dir)
        else:
            if os.path.isfile(LEGACY_LINUX_LOG_PATH) and not os.path.isfile(LINUX_LOG_PATH):
                try:
                    shutil.copy2(LEGACY_LINUX_LOG_PATH, LINUX_LOG_PATH)
                except OSError:
                    pass
            _migrate_linux_cron(legacy_dir, new_dir)

        with open(_migration_marker_path(), "w", encoding="utf-8") as f:
            f.write("ok\n")

        log_write("INFO", "ServerMetry path migration complete (agent now uses {})".format(new_dir))
        return True
    except Exception as e:
        log_write("ERROR", "Path migration failed: {}".format(e))
        return False
