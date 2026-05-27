"""Hardened LLM endpoint guard + bounded POST helper.

Lifted from ``webui_app/routes/llm.py`` (Plan 2026-05-27-006 Unit 2) so that
the ``generate-backlink-text`` CLI verb and the WebUI can share the same
security primitives without the CLI depending on Flask.

**No ``backlink_publisher.publishing`` import is allowed here.**
"""

from __future__ import annotations

import json
import os

import requests

from backlink_publisher._util.llm_allowlist import is_allowlisted
from backlink_publisher._util.net_safety import _check_url_for_ssrf

# Cap upstream-response size at 64 KB streamed read: a malicious endpoint
# could return a multi-GB body and exhaust memory.
LLM_MAX_RESPONSE_BYTES = 64 * 1024


def guard_llm_endpoint(url: str) -> tuple[str | None, str | None]:
    """Return (rejection_reason, detail) or (None, None) if URL is acceptable.

    Layered gates:
      1. Scheme must be http(s).
      2. Host must be in the LLM allowlist (or operator opted out via
         BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST=1).
      3. SSRF gate (RFC1918, link-local, metadata IPs, etc.) unless the
         loopback exception is opted in via
         BACKLINK_PUBLISHER_LLM_ALLOW_LOOPBACK=1, in which case loopback
         IPs and ``localhost`` are allowed.

    Env vars are read inline (not cached at import) so tests can flip them
    via monkeypatch without ``importlib.reload``.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "scheme_rejected", f"only http/https allowed, got {parsed.scheme!r}"

    if not is_allowlisted(url):
        return (
            "host_not_allowlisted",
            f"host {parsed.hostname!r} is not in the LLM allowlist; "
            f"set BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST=1 to opt out",
        )

    # SSRF check — but skip loopback rejection when operator opted in.
    allow_loopback = (
        os.environ.get("BACKLINK_PUBLISHER_LLM_ALLOW_LOOPBACK", "0") == "1"
    )
    ssrf_reason = _check_url_for_ssrf(url)
    if ssrf_reason is not None:
        if allow_loopback and _is_loopback_host(parsed.hostname):
            return None, None
        return "url_rejected", ssrf_reason

    return None, None


def _is_loopback_host(host: str | None) -> bool:
    """Hostname-level loopback check decoupled from net_safety's reason string.

    Catches 127.0.0.1, ::1, IPv4 loopback aliases (0.0.0.0/8, 127.0.0.0/8),
    and the literal 'localhost'.
    """
    import ipaddress

    if not host:
        return False
    h = host.strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def safe_post_json(
    url: str, headers: dict, payload: dict, timeout: int = 10
) -> tuple[int, object]:
    """Bounded POST with content-type + size guards.  Returns (status, parsed_json).

    Security properties (Plan 2026-05-21-006 Unit 3.1):
    - ``allow_redirects=False``: the SSRF gate is one-shot at input; following
      redirects would re-issue the request (including the Bearer api_key header)
      against an attacker-chosen target, defeating the gate.
    - 3xx responses raise ``ValueError`` instead of following Location.
    - Response body capped at ``LLM_MAX_RESPONSE_BYTES`` to prevent OOM.
    - Content-Type must include "json"; HTML error pages from CDN/WAF are
      rejected before ``json.loads``.

    Raises:
        ValueError: on redirect, bad content-type, body-too-large, or JSON parse error.
        requests.RequestException: on network failure.
    """
    resp = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=timeout,
        stream=True,
        allow_redirects=False,
    )
    if 300 <= resp.status_code < 400:
        raise ValueError(
            f"redirect_not_allowed: upstream returned {resp.status_code}; "
            f"refusing to follow Location header"
        )
    ctype = resp.headers.get("Content-Type", "")
    if "json" not in ctype.lower():
        raise ValueError(f"bad_content_type: {ctype!r}")
    body = b""
    for chunk in resp.iter_content(chunk_size=8192):
        body += chunk
        if len(body) >= LLM_MAX_RESPONSE_BYTES:
            raise ValueError(
                f"response_too_large: exceeded {LLM_MAX_RESPONSE_BYTES} bytes"
            )
    return resp.status_code, json.loads(body)
