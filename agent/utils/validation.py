"""Input validation utilities for ServerMetry Agent – prevents command injection."""

import re

_ALLOWED_TIMEZONES = None
_ZONEINFO_LOADED = False

# Common IANA → Windows timezone IDs (used by config_applier on Windows)
IANA_TO_WINDOWS_TZ = {
    "UTC": "UTC",
    "Etc/UTC": "UTC",
    "Etc/GMT": "UTC",
    "Europe/Berlin": "W. Europe Standard Time",
    "Europe/Vienna": "W. Europe Standard Time",
    "Europe/Zurich": "W. Europe Standard Time",
    "Europe/Rome": "W. Europe Standard Time",
    "Europe/Amsterdam": "W. Europe Standard Time",
    "Europe/Brussels": "Romance Standard Time",
    "Europe/Paris": "Romance Standard Time",
    "Europe/Madrid": "Romance Standard Time",
    "Europe/London": "GMT Standard Time",
    "Europe/Dublin": "GMT Standard Time",
    "Europe/Lisbon": "GMT Standard Time",
    "Europe/Warsaw": "Central European Standard Time",
    "Europe/Prague": "Central Europe Standard Time",
    "Europe/Budapest": "Central Europe Standard Time",
    "Europe/Stockholm": "W. Europe Standard Time",
    "Europe/Oslo": "W. Europe Standard Time",
    "Europe/Copenhagen": "Romance Standard Time",
    "Europe/Helsinki": "FLE Standard Time",
    "Europe/Athens": "GTB Standard Time",
    "Europe/Istanbul": "Turkey Standard Time",
    "Europe/Moscow": "Russian Standard Time",
    "America/New_York": "Eastern Standard Time",
    "America/Chicago": "Central Standard Time",
    "America/Denver": "Mountain Standard Time",
    "America/Los_Angeles": "Pacific Standard Time",
    "America/Phoenix": "US Mountain Standard Time",
    "America/Toronto": "Eastern Standard Time",
    "America/Vancouver": "Pacific Standard Time",
    "America/Sao_Paulo": "E. South America Standard Time",
    "Asia/Tokyo": "Tokyo Standard Time",
    "Asia/Shanghai": "China Standard Time",
    "Asia/Hong_Kong": "China Standard Time",
    "Asia/Singapore": "Singapore Standard Time",
    "Asia/Kolkata": "India Standard Time",
    "Asia/Dubai": "Arabian Standard Time",
    "Australia/Sydney": "AUS Eastern Standard Time",
    "Australia/Melbourne": "AUS Eastern Standard Time",
    "Pacific/Auckland": "New Zealand Standard Time",
}


def _load_allowed_timezones():
    global _ALLOWED_TIMEZONES, _ZONEINFO_LOADED
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
            _ZONEINFO_LOADED = len(zones) > 0
        zones.update(["UTC", "Local", "Etc/UTC", "Etc/GMT"])
        zones.update(IANA_TO_WINDOWS_TZ.keys())
        zones.update(IANA_TO_WINDOWS_TZ.values())
        _ALLOWED_TIMEZONES = frozenset(zones)
    except Exception:
        _ALLOWED_TIMEZONES = frozenset(["UTC", "Local", "Etc/UTC", "Etc/GMT"])
        _ZONEINFO_LOADED = False
    return _ALLOWED_TIMEZONES


def _looks_like_iana(tz):
    return bool(re.match(r"^[A-Za-z]+/[A-Za-z0-9_+-]+$", tz))


def _looks_like_windows_tz(tz):
    # e.g. "W. Europe Standard Time", "UTC"
    if tz == "UTC":
        return True
    return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9_ .+-]{1,63}$", tz)) and (
        " " in tz or tz.endswith("Time") or tz in IANA_TO_WINDOWS_TZ.values()
    )


def validate_timezone(tz):
    if not tz or len(tz) > 64:
        return False
    if not re.match(r"^[A-Za-z0-9_ /.+-]+$", tz):
        return False
    allowed = _load_allowed_timezones()
    if tz in allowed:
        return True
    # On Windows (no zoneinfo tree) accept IANA and Windows timezone IDs
    if not _ZONEINFO_LOADED:
        return _looks_like_iana(tz) or _looks_like_windows_tz(tz)
    return False


def resolve_windows_timezone(tz):
    """Return a Windows timezone ID for Set-TimeZone, or None if unknown."""
    if not tz:
        return None
    if tz in IANA_TO_WINDOWS_TZ:
        return IANA_TO_WINDOWS_TZ[tz]
    if _looks_like_windows_tz(tz) or tz == "UTC":
        return tz
    return None


def validate_ip(ip):
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


def validate_dns_list(dns_list):
    if not isinstance(dns_list, list) or len(dns_list) > 8:
        return False
    if not dns_list:
        return False
    return all(validate_ip(d) for d in dns_list)


def validate_ntp_server(ntp):
    if not ntp or len(ntp) > 255:
        return False
    return bool(re.match(r"^[a-zA-Z0-9.\-_]+$", ntp))


def validate_hostname(host):
    if not host or len(host) > 253:
        return False
    return bool(re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$", host))


def validate_cron_interval(seconds):
    return isinstance(seconds, int) and 60 <= seconds <= 604800


def sanitize_shell_arg(arg):
    if not isinstance(arg, str):
        arg = str(arg)
    return arg.replace("'", "'\\''").replace("\n", " ").replace("\r", "")


def validate_and_sanitize_timezone(tz):
    if not validate_timezone(tz):
        return False, None
    return True, tz


def validate_and_sanitize_dns(dns_list):
    if not validate_dns_list(dns_list):
        return False, None
    return True, dns_list


def validate_and_sanitize_ntp(ntp):
    if not validate_ntp_server(ntp):
        return False, None
    return True, ntp


def validate_and_sanitize_interval(seconds):
    if not validate_cron_interval(seconds):
        return False, 60
    return True, seconds
