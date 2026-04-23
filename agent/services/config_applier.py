"""
ServerPulse config applier – applies server configuration to the local system.
All functions are best-effort: failures are logged but never abort the agent run.
"""

import os
import platform
import re
import subprocess
import tempfile

from utils.logging import log_write


def _run(cmd, timeout=30):
    """Run a subprocess command. Returns (success, stdout, stderr)."""
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
    except subprocess.TimeoutExpired:
        return False, "", "timeout after {}s".format(timeout)
    except Exception as e:
        return False, "", str(e)


# ── Timezone ──────────────────────────────────────────────────────────────────

def apply_timezone(timezone):
    """Set system timezone cross-platform. Returns True on success."""
    if not timezone:
        return True
    if platform.system() == "Windows":
        ok, _, err = _run(
            ["powershell", "-Command", "Set-TimeZone -Id '{}'".format(timezone)]
        )
    else:
        ok, _, err = _run(["timedatectl", "set-timezone", timezone])

    if ok:
        log_write("INFO", "Timezone set to {}".format(timezone))
    else:
        log_write("WARNING", "Failed to set timezone {}: {}".format(timezone, err))
    return ok


# ── Locale ────────────────────────────────────────────────────────────────────

def apply_locale(locale):
    """Set system locale (Linux via localectl). Returns True on success."""
    if not locale:
        return True
    if platform.system() != "Linux":
        return True  # Only supported on Linux

    ok, _, err = _run(["localectl", "set-locale", "LANG={}".format(locale)])
    if ok:
        log_write("INFO", "Locale set to {}".format(locale))
    else:
        log_write("WARNING", "Failed to set locale {}: {}".format(locale, err))
    return ok


# ── NTP ───────────────────────────────────────────────────────────────────────

def apply_ntp(custom_ntp):
    """Configure a custom NTP server. Returns True on success."""
    if not custom_ntp:
        return True

    if platform.system() == "Windows":
        ok, _, err = _run([
            "w32tm", "/config",
            "/manualpeerlist:{}".format(custom_ntp),
            "/syncfromflags:manual",
            "/reliable:YES",
            "/update",
        ])
        if ok:
            _run(["net", "stop", "w32time"])
            _run(["net", "start", "w32time"])
    else:
        conf_path = "/etc/systemd/timesyncd.conf"
        try:
            try:
                with open(conf_path, "r") as f:
                    content = f.read()
            except FileNotFoundError:
                content = "[Time]\n"

            if re.search(r"^NTP\s*=", content, re.MULTILINE):
                content = re.sub(
                    r"^NTP\s*=.*$",
                    "NTP={}".format(custom_ntp),
                    content,
                    flags=re.MULTILINE,
                )
            elif "[Time]" in content:
                content = content.replace("[Time]", "[Time]\nNTP={}".format(custom_ntp), 1)
            else:
                content += "\n[Time]\nNTP={}\n".format(custom_ntp)

            with open(conf_path, "w") as f:
                f.write(content)
            ok, _, err = _run(["systemctl", "restart", "systemd-timesyncd"])
        except PermissionError:
            log_write("WARNING", "No permission to update timesyncd.conf – run agent as root")
            return False
        except Exception as e:
            log_write("WARNING", "Failed to configure NTP: {}".format(e))
            return False

    if ok:
        log_write("INFO", "NTP server set to {}".format(custom_ntp))
    else:
        log_write("WARNING", "Failed to configure NTP {}: {}".format(custom_ntp, err))
    return ok


# ── DNS ───────────────────────────────────────────────────────────────────────

def apply_dns(custom_dns):
    """Configure custom DNS servers (list of IP strings). Returns True on success."""
    if not custom_dns or not isinstance(custom_dns, list):
        return True

    if platform.system() == "Windows":
        ps_cmd = (
            "$adapter = (Get-NetAdapter | Where-Object {{ $_.Status -eq 'Up' }} "
            "| Select-Object -First 1).Name; "
            "Set-DnsClientServerAddress -InterfaceAlias $adapter -ServerAddresses ({})".format(
                ", ".join("'{}'".format(d) for d in custom_dns)
            )
        )
        ok, _, err = _run(["powershell", "-Command", ps_cmd])
    else:
        try:
            # Note: systems using systemd-resolved or NetworkManager may
            # regenerate /etc/resolv.conf on the next network event, overwriting
            # these changes. For persistent DNS, configure via those services instead.
            lines = ["# Managed by ServerPulse Agent\n"]
            for dns in custom_dns:
                lines.append("nameserver {}\n".format(dns))
            with open("/etc/resolv.conf", "w") as f:
                f.writelines(lines)
            ok, err = True, ""
        except PermissionError:
            log_write("WARNING", "No permission to update /etc/resolv.conf – run agent as root")
            return False
        except Exception as e:
            log_write("WARNING", "Failed to update DNS: {}".format(e))
            return False

    if ok:
        log_write("INFO", "DNS servers set to {}".format(", ".join(custom_dns)))
    else:
        log_write("WARNING", "Failed to set DNS servers: {}".format(err))
    return ok


# ── Schedule ──────────────────────────────────────────────────────────────────

def _interval_to_cron(interval_seconds):
    """
    Convert an interval in seconds to a cron expression.
    Minimum resolution is 1 minute (cron limitation).
    """
    minutes = max(1, round(interval_seconds / 60))
    if minutes == 1:
        return "* * * * *"
    if minutes <= 30 and 60 % minutes == 0:
        return "*/{} * * * *".format(minutes)
    if minutes <= 60:
        return "0 * * * *"
    hours = round(minutes / 60)
    if hours == 1:
        return "0 * * * *"
    if hours <= 12 and 24 % hours == 0:
        return "0 */{} * * *".format(hours)
    return "0 0 * * *"  # fallback: daily


def update_schedule(interval_seconds):
    """
    Update the cron (Linux) or Scheduled Task (Windows) to the given interval.
    No-op if interval is already 60 seconds (the install default).
    Returns True if updated successfully or no change needed.
    """
    if not interval_seconds or interval_seconds == 60:
        return True

    if platform.system() == "Windows":
        task_name = "ServerPulseAgent"
        interval_minutes = max(1, round(interval_seconds / 60))
        ps_cmd = (
            "$t = Get-ScheduledTask -TaskName '{task}' -ErrorAction SilentlyContinue; "
            "if ($t) {{ "
            "  $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) "
            "    -RepetitionInterval (New-TimeSpan -Minutes {minutes}); "
            "  Set-ScheduledTask -TaskName '{task}' -Trigger $trigger | Out-Null; "
            "  Write-Output 'updated'; "
            "}} else {{ Write-Output 'not_found'; }}"
        ).format(task=task_name, minutes=interval_minutes)

        ok, stdout, err = _run(["powershell", "-Command", ps_cmd])
        if "updated" in stdout:
            log_write("INFO", "Scheduled task interval updated to {} minutes".format(interval_minutes))
            return True
        if "not_found" in stdout:
            log_write("WARNING", "Scheduled task '{}' not found; cannot update interval".format(task_name))
            return False
        log_write("WARNING", "Failed to update scheduled task: {}".format(err))
        return False
    else:
        cron_expr = _interval_to_cron(interval_seconds)
        _, current_cron, _ = _run(["crontab", "-l"])

        lines = current_cron.splitlines() if current_cron else []
        new_lines = []
        updated = False

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(line)
                continue
            if "serverpulse/agent.py" in line:
                parts = stripped.split()
                # cron line format: min hour dom mon dow cmd [args...]
                if len(parts) >= 6:
                    cmd_part = " ".join(parts[5:])
                    new_lines.append("{} {}".format(cron_expr, cmd_part))
                    updated = True
                    continue
            new_lines.append(line)

        if not updated:
            log_write("WARNING", "ServerPulse cron entry not found; cannot update interval")
            return False

        new_cron = "\n".join(new_lines) + "\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cron", delete=False) as tmp:
            tmp.write(new_cron)
            tmp_path = tmp.name

        ok, _, err = _run(["crontab", tmp_path])
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        if ok:
            log_write("INFO", "Cron interval updated to '{}' ({} seconds)".format(cron_expr, interval_seconds))
            return True
        log_write("WARNING", "Failed to update crontab: {}".format(err))
        return False


# ── Main entry point ──────────────────────────────────────────────────────────

def apply_config(config, log_debug_fn=None):
    """
    Apply all settings from the API config dict to the local system.
    Returns reportIntervalSeconds so the caller can use it for scheduling.
    """
    if not config:
        return 60

    timezone = config.get("timezone")
    locale = config.get("locale")
    custom_ntp = config.get("customNtp")
    custom_dns = config.get("customDns")
    interval = config.get("reportIntervalSeconds", 60) or 60

    if log_debug_fn:
        log_debug_fn(
            "Applying config: timezone={}, locale={}, ntp={}, dns={}, interval={}s".format(
                timezone, locale, custom_ntp, custom_dns, interval
            )
        )

    if timezone:
        apply_timezone(timezone)
    if locale:
        apply_locale(locale)
    if custom_ntp:
        apply_ntp(custom_ntp)
    if custom_dns:
        apply_dns(custom_dns)
    if interval != 60:
        update_schedule(interval)

    return interval
