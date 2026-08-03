"""HTTP client for ServerMetry API."""

import json
import math
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin

from models.constants import AGENT_VERSION
from models.limits import API_POST_TIMEOUT, API_GET_TIMEOUT, API_TEMPLATE_TIMEOUT


def _sanitize_payload(payload):
    """Normalize payload for JSON + Zod: drop null/NaN/Inf keys (optional ≠ null)."""
    if not isinstance(payload, dict):
        return payload
    result = {}
    for k, v in payload.items():
        if v is None:
            continue
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v):
                continue
            result[k] = v
        elif isinstance(v, dict):
            result[k] = _sanitize_payload(v)
        elif isinstance(v, list):
            cleaned = []
            for item in v:
                if isinstance(item, dict):
                    cleaned.append(_sanitize_payload(item))
                elif isinstance(item, float) and (math.isnan(item) or math.isinf(item)):
                    continue
                elif item is None:
                    continue
                else:
                    cleaned.append(item)
            result[k] = cleaned
        else:
            result[k] = v
    return result


def _request(method, base_url, path, api_key, body=None, timeout=10, log_debug_fn=None, retries=0, backoff=0.5):
    """
    Perform a single HTTP request, optionally retrying on transient failures
    (URLError / timeouts / HTTP 5xx). HTTP 4xx responses are never retried,
    since retrying won't change a client-side error.
    """
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    headers = {
        "Content-Type": "application/json",
        "X-Server-Key": api_key,
        "User-Agent": "ServerMetryAgent/{}".format(AGENT_VERSION),
    }

    data = None
    if body is not None:
        try:
            data = json.dumps(_sanitize_payload(body)).encode("utf-8")
        except (TypeError, ValueError):
            return False, None, "JSON serialization failed"

    attempt = 0
    while True:
        start = time.time()
        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                resp_body = resp.read().decode("utf-8", errors="replace")
                elapsed = round(time.time() - start, 3)
                if log_debug_fn:
                    log_debug_fn("{} {} -> {} in {}s".format(method, url, resp.status, elapsed))
                try:
                    result = json.loads(resp_body) if resp_body else {}
                    return True, result, None
                except json.JSONDecodeError:
                    return True, resp_body, None
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            elapsed = round(time.time() - start, 3)
            if log_debug_fn:
                log_debug_fn("{} {} -> HTTP {} in {}s".format(method, url, e.code, elapsed))
            if e.code >= 500 and attempt < retries:
                attempt += 1
                if log_debug_fn:
                    log_debug_fn("{} {} -> retrying ({}/{}) after HTTP {}".format(method, url, attempt, retries, e.code))
                time.sleep(backoff * attempt)
                continue
            return False, None, "HTTP {}: {}".format(e.code, resp_body[:200])
        except urllib.error.URLError as e:
            elapsed = round(time.time() - start, 3)
            if log_debug_fn:
                log_debug_fn("{} {} -> ERROR {} in {}s".format(method, url, e.reason, elapsed))
            if attempt < retries:
                attempt += 1
                if log_debug_fn:
                    log_debug_fn("{} {} -> retrying ({}/{}) after {}".format(method, url, attempt, retries, e.reason))
                time.sleep(backoff * attempt)
                continue
            return False, None, str(e.reason)
        except Exception as e:
            elapsed = round(time.time() - start, 3)
            if log_debug_fn:
                log_debug_fn("{} {} -> ERROR {} in {}s".format(method, url, e, elapsed))
            return False, None, str(e)


def post_metrics(api_url, api_key, metrics, log_debug_fn=None):
    """Returns (ok, config_changed_at, enable_auto_updates, commands, err).

    enable_auto_updates is the live server flag from the metrics response when
    present, otherwise None (older API). err is None on success.
    """
    ok, result, err = _request(
        "POST", api_url, "api/v1/agent/metrics", api_key, metrics,
        timeout=API_POST_TIMEOUT, log_debug_fn=log_debug_fn, retries=2,
    )
    if not ok:
        return False, None, None, [], err
    data = result.get("data", {}) if isinstance(result, dict) else {}
    config_changed_at = data.get("configChangedAt") if isinstance(data, dict) else None
    commands = data.get("commands", []) if isinstance(data, dict) else []
    # Older APIs omit the field — keep None so the caller can fall back to cache.
    # JSON null must also stay None (bool(None) is False and would wrongly disable
    # updates); only an explicit boolean false should turn auto-updates off.
    if isinstance(data, dict) and "enableAutoUpdates" in data:
        raw_flag = data.get("enableAutoUpdates")
        if raw_flag is None:
            enable_auto_updates = None
        else:
            enable_auto_updates = bool(raw_flag)
    else:
        enable_auto_updates = None
    return True, config_changed_at, enable_auto_updates, commands, None


def get_config(api_url, api_key, log_debug_fn=None):
    ok, result, err = _request("GET", api_url, "api/v1/agent/config", api_key, timeout=API_GET_TIMEOUT, log_debug_fn=log_debug_fn)
    if not ok:
        return False, None, []
    data = result.get("data", {}) if isinstance(result, dict) else {}
    config = data.get("config") if isinstance(data, dict) else None
    services = data.get("services", []) if isinstance(data, dict) else []
    return True, config, services


def post_discovered_ports(api_url, api_key, ports, log_debug_fn=None):
    ok, result, err = _request("POST", api_url, "api/v1/agent/ports", api_key, {"ports": ports}, timeout=API_POST_TIMEOUT, log_debug_fn=log_debug_fn)
    if not ok:
        return False, err
    return True, None


def apply_template(api_url, api_key, template_id, server_id, log_debug_fn=None):
    path = "api/v1/templates/{}/apply/{}".format(template_id, server_id)
    return _request("POST", api_url, path, api_key, timeout=API_TEMPLATE_TIMEOUT, log_debug_fn=log_debug_fn)
