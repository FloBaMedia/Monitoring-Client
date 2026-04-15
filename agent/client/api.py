"""HTTP client for ServerPulse Agent API communication."""

import json
import ssl
import time
import urllib.error
import urllib.request
from models.constants import AGENT_VERSION


def post_metrics(api_url, api_key, payload, log_debug_fn=None):
    """POST the metrics payload to the API. Returns True on success."""
    url = "{}/api/v1/agent/metrics".format(api_url)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Server-Key", api_key)
    req.add_header("User-Agent", "ServerPulse-Agent/{}".format(AGENT_VERSION))

    if log_debug_fn:
        log_debug_fn("POST {} ({} bytes)".format(url, len(body)))
        log_debug_fn("Payload: {}".format(json.dumps(payload, indent=2)))

    ctx = ssl.create_default_context()
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            elapsed = time.time() - t0
            from utils.logging import log_write
            log_write(
                "INFO",
                "POST /api/v1/agent/metrics → {} ({:.2f}s)".format(resp.status, elapsed),
            )
            return True
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        try:
            body_text = e.read().decode("utf-8", errors="replace")[:2000]
        except Exception:
            body_text = "(unreadable)"
        from utils.logging import log_write
        log_write(
            "ERROR",
            "POST /api/v1/agent/metrics → {} ({:.2f}s): {}".format(e.code, elapsed, body_text),
        )
        return False
    except Exception as e:
        from utils.logging import log_write
        log_write("ERROR", "POST /api/v1/agent/metrics failed: {}".format(e))
        return False


ApplyTemplateResult = {
    "alertRulesCreated": int,
    "configUpdated": bool,
    "serverUpdated": bool,
    "scriptContent": str | None,
}


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