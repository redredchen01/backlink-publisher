"""Publish-attempt event emission — Plan 2026-05-28-001 Unit 1.

Emits one structured JSON line per browser-tier dispatch attempt so the
operator has a baseline observability signal before behavioral changes
(health gate, circuit breaker) in Units 2–3.

Design constraints:
- Never raises — side-effect only.
- Writes to ``opencli_logger`` (stderr-captured by WebUI subprocess pipe).
- Browser-tier only in v1; HTTP API channels are not covered here.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from backlink_publisher._util.logger import opencli_logger as _log


class Outcome(str, Enum):
    SUCCESS = "success"
    AUTH_EXPIRED = "auth_expired"
    AUTH_BANNED = "auth_banned"
    EXTERNAL_ERROR = "external_error"
    TRANSIENT = "transient"


def emit_attempt(
    platform: str,
    outcome: Outcome,
    duration_ms: float,
    run_id: str | None = None,
    **extra: Any,
) -> None:
    """Emit a ``publish_attempt`` event.

    Never raises — any internal error is swallowed so reliability events
    never break a publish run.
    """
    try:
        payload: dict[str, Any] = {
            "event": "publish_attempt",
            "platform": platform,
            "outcome": outcome.value if isinstance(outcome, Outcome) else str(outcome),
            "duration_ms": round(duration_ms, 1),
        }
        if run_id is not None:
            payload["run_id"] = run_id
        payload.update(extra)
        _log.info(payload)
    except Exception:  # noqa: BLE001
        pass


def now_ms() -> float:
    """Monotonic timestamp in milliseconds — for duration measurement."""
    return time.monotonic() * 1000
