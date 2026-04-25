"""Input validation utilities for ServerPulse Agent – prevents command injection."""

import re

_ALLOWED_TIMEZONES = None

def _load_allowed_timezones():
    global _ALLOWED_TIMEZONES
    if _ALLOWED_TIMEZONES is not None:
        return _ALLOWED_TIMEZONES
    try:
        import os
        zones = set()
        tz_dir = "/usr/share/zoneinfo"
        if os.path.isdir(tz_dir):
            for root, dirs, files in os.walk(tz_dir):
                for f in files:
                    if f != "leap-seconds.list" and f != "leapseconds" and f != "tzdata.zi":
                        zone = os.path.relpath(os.path.join(root, f), tz_dir)
                        zones.add(zone.replace(os.sep, "/"))
        zones.update(["UTC", "Local", "Etc/UTC", "Etc/GMT"])
        _ALLOWED_TIMEZONES = frozenset(zones)
    except Exception:
        _ALLOWED_TIMEZONES = frozenset()
    return _ALLOWED_TIMEZONES


def validate_timezone(tz: str) -> bool:
    if not tz or len(tz) > 64:
        return False
    if not re.match(r"^[A-Za-z0-9_/+-]+$", tz):
        return False
    allowed = _load_allowed_timezones()
    if allowed:
        return tz in allowed
    return bool(re.match(r"^[A-Za-z]+/[A-Za-z_0-9+-]+$", tz))


def validate_ip(ip: str) -> bool:
    if not ip or len(ip) > 45:
        return False
    ipv4_pattern = r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$"
    if re.match(ipv4_pattern, ip):
        return True
    if ":" in ip:
        parts = ip.split(":")
        if len(parts) <= 8 and all(len(p) <= 4 for p in parts):
            return True
    return False


def validate_dns_list(dns_list) -> bool:
    if not isinstance(dns_list, list) or len(dns_list) > 8:
        return False
    if not dns_list:
        return False
    return all(validate_ip(d) for d in dns_list)


def validate_ntp_server(ntp: str) -> bool:
    if not ntp or len(ntp) > 255:
        return False
    return bool(re.match(r"^[a-zA-Z0-9.\-_]+$", ntp))


def validate_hostname(host: str) -> bool:
    if not host or len(host) > 253:
        return False
    return bool(re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$", host))


def validate_cron_interval(seconds: int) -> bool:
    return isinstance(seconds, int) and 60 <= seconds <= 604800


def sanitize_shell_arg(arg: str) -> str:
    if not isinstance(arg, str):
        arg = str(arg)
    return arg.replace("'", "'\\''").replace("\n", " ").replace("\r", "")


def validate_and_sanitize_timezone(tz: str) -> tuple:
    if not validate_timezone(tz):
        return False, None
    return True, tz


def validate_and_sanitize_dns(dns_list) -> tuple:
    if not validate_dns_list(dns_list):
        return False, None
    return True, dns_list


def validate_and_sanitize_ntp(ntp: str) -> tuple:
    if not validate_ntp_server(ntp):
        return False, None
    return True, ntp


def validate_and_sanitize_interval(seconds: int) -> tuple:
    if not validate_cron_interval(seconds):
        return False, 60
    return True, seconds
