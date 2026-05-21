"""Defensive security primitives for the WebUI.

Extracted from webui_app/helpers/__init__.py in Plan 2026-05-21-007 Unit 3.
Covers CSRF, loopback guards, safe redirects, OAuth URI, bind-origin checks.

_TRUTHY_BYPASS is the single canonical definition; url_meta.py's duplicate
is removed by this unit.
"""

from __future__ import annotations

import os
import re
import secrets
from urllib.parse import urlparse

from flask import abort, request, session


_FLASK_PORT = int(os.environ.get('PORT', 8888))
_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_TRUTHY_BYPASS = {"1", "true", "yes"}

# Max length for any operator-visible flash message embedded in a redirect URL.
# Long messages get truncated rather than rejected because the operator still
# needs *some* hint of what went wrong.
_FLASH_MSG_MAX_LEN = 200


def _check_localhost():
    if request.remote_addr not in _LOOPBACK_HOSTS:
        abort(403)


def _safe_flash_redirect(path: str, *, flash_type: str = "", msg: str = "",
                         fragment: str = ""):
    """Return a Flask redirect Response with a sanitized ``flash_msg`` query.

    Plan 2026-05-21-006 Unit 3.4 / F26: centralised sanitisation for flash
    messages embedded in redirect URLs. Strips CR/LF, tabs; caps to
    ``_FLASH_MSG_MAX_LEN``; URL-quotes the result.
    """
    from urllib.parse import quote
    from flask import redirect as _flask_redirect

    safe_msg = msg.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
    safe_msg = safe_msg.strip()[:_FLASH_MSG_MAX_LEN]
    parts = []
    if flash_type:
        parts.append(f"flash_type={quote(flash_type, safe='')}")
    if safe_msg:
        parts.append(f"flash_msg={quote(safe_msg, safe='')}")
    qs = ('?' + '&'.join(parts)) if parts else ''
    frag = ('#' + fragment) if fragment else ''
    return _flask_redirect(path + qs + frag)


def _safe_referrer_redirect(default: str = '/'):
    """Same-origin guard for ``redirect(request.referrer or '/')``.

    Plan 2026-05-21-006 Unit 3.3 / F8: checks that the referrer's
    scheme + host match ``request.host_url``; falls back to ``default``.
    """
    from flask import redirect as _flask_redirect
    referrer = request.referrer or ''
    if not referrer:
        return _flask_redirect(default)
    try:
        ref = urlparse(referrer)
        host = urlparse(request.host_url)
    except Exception:
        return _flask_redirect(default)
    if (ref.scheme, ref.netloc) != (host.scheme, host.netloc):
        return _flask_redirect(default)
    return _flask_redirect(referrer)


def _validate_webui_run_id(run_id):
    if not run_id or not _RUN_ID_RE.match(run_id):
        abort(400)


def _oauth_callback_uri():
    return f'http://localhost:{_FLASK_PORT}/settings/blogger/oauth-callback'


def _resolve_bind_host() -> str:
    host = os.environ.get("BIND_HOST", "127.0.0.1")
    if host in _LOOPBACK_HOSTS:
        return host
    if os.environ.get("BACKLINK_PUBLISHER_ALLOW_NETWORK") == "1":
        return host
    raise RuntimeError(
        f"refusing to bind to non-loopback host {host!r}: this WebUI has "
        "minimal auth. Set BACKLINK_PUBLISHER_ALLOW_NETWORK=1 to opt in to "
        "network exposure (only do this on a trusted network)."
    )


def _ensure_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _check_csrf_or_abort() -> None:
    """Accept CSRF token from ``request.form['csrf_token']`` (form POST) OR
    ``X-CSRFToken`` header (JS fetch with JSON body).

    Plan 2026-05-19-006 Unit 4 — SEC-4 review recommendation.
    """
    token = request.form.get("csrf_token") or request.headers.get("X-CSRFToken", "")
    expected = session.get("csrf_token", "")
    if not token or not expected or not secrets.compare_digest(token, expected):
        abort(403)


def _check_bind_origin_or_abort() -> None:
    """Reject browser-originated cross-origin POSTs and DNS rebinding.

    Decision tree:
      1. ``Origin`` present and allowlisted → check Referer if also present.
      2. ``Origin`` present but not allowlisted (including ``null``) → 403.
      3. ``Origin`` absent + ``Referer`` present and allowlisted → pass.
      4. ``Origin`` absent + ``Referer`` absent → 403.
      5. When both present, BOTH must allowlist.
    """
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")

    def _is_allowlisted(url: str | None) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme != "http":
            return False
        host = (parsed.hostname or "").lower()
        if host not in _LOOPBACK_HOSTS:
            return False
        if parsed.port != _FLASK_PORT:
            return False
        return True

    origin_ok = _is_allowlisted(origin) if origin else None
    referer_ok = _is_allowlisted(referer) if referer else None

    if origin is not None and not origin_ok:
        abort(403)
    if referer is not None and not referer_ok:
        abort(403)
    if origin is None and referer is None:
        abort(403)


def _refuse_when_allow_network() -> None:
    """Hard-disable bind endpoints when BACKLINK_PUBLISHER_ALLOW_NETWORK=1."""
    if os.environ.get("BACKLINK_PUBLISHER_ALLOW_NETWORK") == "1":
        from flask import make_response, jsonify
        response = make_response(
            jsonify(
                error="bind_disabled_under_allow_network",
                message=(
                    "Bind endpoints are disabled when "
                    "BACKLINK_PUBLISHER_ALLOW_NETWORK=1. Bind in v1 "
                    "requires loopback-only access; un-set the env var, "
                    "bind locally, then re-export it."
                ),
            ),
            403,
        )
        abort(response)
