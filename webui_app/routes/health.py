"""/ce:health — publishing health dashboard (read-only).

Plan 2026-05-25-006 / U3. On load, runs the single-flight project-on-read
backstop (U1) so WebUI-sourced and crash-stranded outcomes are reflected, then
the read-only aggregations (U2), and renders them with honest empty / freshness
/ gap states. GET-only → the CSRF guard (mutating verbs only) does not apply.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone

from flask import Blueprint

from ..helpers.contexts import _render
from ..helpers._request_cache import _g_cache

bp = Blueprint("health", __name__)

_log = logging.getLogger(__name__)

# Last-resort body when even rendering the degraded dashboard fails (R5: a GET
# of /ce:health must never 500 — an honest "unavailable" beats a stack trace).
_FALLBACK_HTML = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<title>Publishing Health</title></head><body>"
    "<main style=\"font-family:system-ui;max-width:40rem;margin:3rem auto;\">"
    "<h1>Publishing Health</h1>"
    "<p>The health dashboard is temporarily unavailable; data may be incomplete. "
    "Please retry shortly.</p><p><a href=\"/\">Home</a></p>"
    "</main></body></html>"
)


@bp.route("/ce:health", methods=["GET"])
def ce_health():
    def _build():
        # U1 backstop first (single-flight, never raises) so the aggregates
        # below read freshened data; then U2 aggregations.
        from ..health_metrics import (
            DEFAULT_WINDOW_DAYS,
            Health,
            SuccessRate,
            _window_start,
            build_health,
        )
        from ..services.health_projection import project_on_read

        projection = project_on_read()
        try:
            health = build_health()
        except Exception as exc:  # noqa: BLE001 — R5: degrade, never 500 the page
            _log.warning("health: aggregation failed, rendering degraded: %s", exc)
            health = Health(
                window_days=DEFAULT_WINDOW_DAYS,
                since_utc=_window_start(
                    datetime.now(timezone.utc), DEFAULT_WINDOW_DAYS
                ),
                success=SuccessRate(),
            )
            projection = dataclasses.replace(
                projection,
                degraded=True,
                degraded_reason=projection.degraded_reason
                or f"{type(exc).__name__}: {exc}",
            )
        return projection, health

    try:
        projection, health = _g_cache("health_agg", _build)
        return _render("health.html", health=health, projection=projection)
    except Exception as exc:  # noqa: BLE001 — R5: even a render/context error must not 500
        _log.error("health: dashboard render failed, serving minimal fallback: %s", exc)
        return _FALLBACK_HTML, 200
