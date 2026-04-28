"""HTTP client for ServerPulse API."""

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
    if not isinstance(payload, dict):
        return payload
    result = {}
    for k, v in payload.items():
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v):
                result[k] = None
            else:
                result[k] = v
        elif isinstance(v, dict):
            result[k] = _sanitize_payload(v)
        elif isinstance(v, list):
            result[k] = [
                _sanitize_payload(item) if isinstance(item, dict) else (
                    None if isinstance(item, (float)) and (math.isnan(item) or math.isinf(item)) else item
                )
                for item in v
            ]
        else:
            result[k] = v
    return result


def _request(method, base_url, path, api_key, body=None, timeout=10, log_debug_fn=None):
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    headers = {
        "Content-Type": "application/json",
        "X-Server-Key": api_key,
        "User-Agent": "ServerPulseAgent/{}".format(AGENT_VERSION),
    }

    data = None
    if body is not None:
        try:
            data = json.dumps(_sanitize_payload(body)).encode("utf-8")
        except (TypeError, ValueError):
            return False, None, "JSON serialization failed"

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
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        elapsed = round(time.time() - start, 3)
        if log_debug_fn:
            log_debug_fn("{} {} -> HTTP {} in {}s".format(method, url, e.code, elapsed))
        return False, None, "HTTP {}: {}".format(e.code, body[:200])
    except urllib.error.URLError as e:
        elapsed = round(time.time() - start, 3)
        if log_debug_fn:
            log_debug_fn("{} {} -> ERROR {} in {}s".format(method, url, e.reason, elapsed))
        return False, None, str(e.reason)
    except Exception as e:
        elapsed = round(time.time() - start, 3)
        if log_debug_fn:
            log_debug_fn("{} {} -> ERROR {} in {}s".format(method, url, e, elapsed))
        return False, None, str(e)


def post_metrics(api_url, api_key, metrics, log_debug_fn=None):
    ok, result, err = _request("POST", api_url, "api/v1/agent/metrics", api_key, metrics, timeout=API_POST_TIMEOUT, log_debug_fn=log_debug_fn)
    if not ok:
        return False, None
    config_changed_at = result.get("configChangedAt") if isinstance(result, dict) else None
    return True, config_changed_at


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
