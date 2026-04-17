"""HTTP client for ServerPulse Agent API communication."""

import json
import math
import ssl
import time
import urllib.error
import urllib.request
from models.constants import AGENT_VERSION


def _sanitize(obj):
    """Recursively replace non-finite floats (NaN, Infinity) with 0.0.

    Python's json.dumps emits bare NaN / Infinity tokens for non-finite floats,
    which are not valid JSON and cause JavaScript's JSON.parse to throw.
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return 0.0
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def post_metrics(api_url, api_key, payload, log_debug_fn=None):
    """POST the metrics payload to the API. Returns True on success."""
    from utils.logging import log_write

    url = "{}/api/v1/agent/metrics".format(api_url)
    try:
        body = json.dumps(_sanitize(payload), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as e:
        log_write("ERROR", "post_metrics: failed to serialize payload: {}".format(e))
        return False
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Server-Key", api_key)
    req.add_header("User-Agent", "ServerPulse-Agent/{}".format(AGENT_VERSION))

    if log_debug_fn:
        log_debug_fn("POST {} ({} bytes)".format(url, len(body)))
        log_debug_fn("Payload: {}".format(json.dumps(_sanitize(payload), indent=2)))

    ctx = ssl.create_default_context()
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            elapsed = time.time() - t0
            log_write(
                "INFO",
                "POST /api/v1/agent/metrics → {} ({:.2f}s, {}B)".format(resp.status, elapsed, len(body)),
            )
            return True
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        detail = "(unreadable)"
        try:
            raw = e.read().decode("utf-8", errors="replace")[:2000]
            detail = raw
            err_info = json.loads(raw).get("error", {})
            detail = "{} — {}".format(
                err_info.get("code", "ERROR"),
                err_info.get("message", raw),
            )
        except Exception:
            pass
        log_write(
            "ERROR",
            "POST /api/v1/agent/metrics → {} ({:.2f}s, {}B): {}".format(e.code, elapsed, len(body), detail),
        )
        return False
    except Exception as e:
        log_write("ERROR", "POST /api/v1/agent/metrics failed: {}".format(e))
        return False


def get_config(api_url, api_key, log_debug_fn=None):
    """
    Call GET /api/v1/agent/config to fetch the server configuration.
    Returns (success, config_dict) where config_dict contains timezone, reportIntervalSeconds,
    enableAutoUpdates, customNtp, customDns, locale, extraCommands.
    """
    url = "{}/api/v1/agent/config".format(api_url)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Server-Key", api_key)
    req.add_header("User-Agent", "ServerPulse-Agent/{}".format(AGENT_VERSION))

    if log_debug_fn:
        log_debug_fn("GET {}".format(url))

    ctx = ssl.create_default_context()
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            elapsed = time.time() - t0
            response_body = resp.read().decode("utf-8", errors="replace")
            from utils.logging import log_write
            log_write(
                "INFO",
                "GET /api/v1/agent/config → {} ({:.2f}s)".format(resp.status, elapsed),
            )
            try:
                data = json.loads(response_body)
                return True, data.get("data", {})
            except json.JSONDecodeError:
                log_write("ERROR", "Failed to parse JSON response from get_config")
                return True, {}
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        try:
            body_text = e.read().decode("utf-8", errors="replace")[:2000]
        except Exception:
            body_text = "(unreadable)"
        from utils.logging import log_write
        log_write(
            "ERROR",
            "GET /api/v1/agent/config → {} ({:.2f}s): {}".format(e.code, elapsed, body_text),
        )
        return False, {}
    except Exception as e:
        from utils.logging import log_write
        log_write("ERROR", "GET /api/v1/agent/config failed: {}".format(e))
        return False, {}


def apply_template(api_url, api_key, template_id, server_id, log_debug_fn=None):
    """
    Call POST /api/v1/templates/:id/apply/:serverId to apply a template.
    Returns (success, result_dict) where result_dict contains scriptContent if available.
    """
    url = "{}/api/v1/templates/{}/apply/{}".format(api_url, template_id, server_id)
    body = json.dumps({}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Server-Key", api_key)
    req.add_header("User-Agent", "ServerPulse-Agent/{}".format(AGENT_VERSION))

    if log_debug_fn:
        log_debug_fn("POST {} ({} bytes)".format(url, len(body)))

    ctx = ssl.create_default_context()
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            elapsed = time.time() - t0
            response_body = resp.read().decode("utf-8", errors="replace")
            from utils.logging import log_write
            log_write(
                "INFO",
                "POST /api/v1/templates/{}/apply/{} → {} ({:.2f}s)".format(
                    template_id, server_id, resp.status, elapsed
                ),
            )
            try:
                data = json.loads(response_body)
                return True, data.get("data", {})
            except json.JSONDecodeError:
                log_write("ERROR", "Failed to parse JSON response from applyTemplate")
                return True, {}
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        try:
            body_text = e.read().decode("utf-8", errors="replace")[:2000]
        except Exception:
            body_text = "(unreadable)"
        from utils.logging import log_write
        log_write(
            "ERROR",
            "POST /api/v1/templates/{}/apply/{} → {} ({:.2f}s): {}".format(
                template_id, server_id, e.code, elapsed, body_text
            ),
        )
        return False, {}
    except Exception as e:
        from utils.logging import log_write
        log_write(
            "ERROR", "POST /api/v1/templates/{}/apply/{} failed: {}".format(template_id, server_id, e)
        )
        return False, {}