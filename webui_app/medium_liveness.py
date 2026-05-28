"""Plan 2026-05-19-003 Unit 5 — Medium liveness probe.

Re-export shim — Wave 1 thin-WebUI refactor (2026-05-28).
Probe logic lives at ``backlink_publisher.publishing.adapters.medium_liveness``.
The store-coupled orchestration function (``medium_liveness_check``) stays
here because it calls ``webui_store.channel_status``.

Invoked from the webui Settings GET handler (via ``_get_medium_status``
in helpers.py) to keep ``channel_status_store["medium"]`` reflecting
reality without depending on the operator triggering a publish or
re-bind to find out.

Design:
  1. **TTL cache**: skip active probe when ``last_verified_at < 5 min``
     ago. Without this cap, every Settings page load would fire a
     headless ``goto('/me')``, training Medium's anti-bot to flag the
     IP.
  2. **Probe-copy isolation**: when an active probe runs, the live
     ``storage_state.json`` is read into memory and passed as a dict to
     ``new_context(storage_state=...)``. The probe NEVER reads the live
     file via path. This way if Cloudflare/Datadome flags the probe
     request, only the in-memory copy is compromised — the live
     credential that headed publish reads is untouched.
  3. **ThreadPoolExecutor 10s budget**: Playwright sync calls block;
     we cap total time-to-verdict and return ``NEEDS_RECHECK`` if the
     probe doesn't finish, so Flask renders without waiting forever.
  4. **Conservative default**: ``MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED``
     defaults to ``False`` until Spike 2 confirms headless ``goto('/me')``
     doesn't reliably trigger anti-bot. When disabled, the probe
     short-circuits to ``NEEDS_RECHECK`` after the cache check.

Outcomes:
   - ``NEVER_BOUND`` — storage_state.json absent → re-bind needed
   - ``EXPIRED`` — store says expired (e.g., publish-time mark_expired)
   - ``CACHED_BOUND`` — within 5-min TTL of last_verified_at
   - ``LOGGED_IN`` — active probe landed on ``/@<user>`` or ``/me/*``
   - ``NEEDS_RECHECK`` — probe disabled, timed out, OR landed on a
     challenge page; live state not mutated (don't claim false-expired
     on Cloudflare hiccup)
"""

from __future__ import annotations

import concurrent.futures
import time

from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.adapters.medium_liveness import (
    LivenessResult,
    MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED,
    _active_probe,
    _load_storage_state_for_probe,
    _storage_state_path,
)


# 5-minute cache on last_verified_at — every Settings page load within
# this window short-circuits to CACHED_BOUND without spawning a probe.
_LIVENESS_TTL_SECONDS = 300


def _last_verified_age_seconds(last_verified_at: str | None) -> float:
    """Seconds since ``last_verified_at`` (ISO string). ``inf`` if absent
    or unparseable."""
    if not last_verified_at:
        return float("inf")
    try:
        from datetime import datetime
        ts = datetime.fromisoformat(last_verified_at)
        now = datetime.fromisoformat(
            datetime.now(ts.tzinfo).isoformat(timespec="seconds")
            if ts.tzinfo
            else datetime.now().isoformat(timespec="seconds")
        )
        return (now - ts).total_seconds()
    except (ValueError, TypeError):
        return float("inf")


def medium_liveness_check(timeout_s: float = 10.0) -> LivenessResult:
    """Determine the live state of the Medium binding.

    Side effects on definite outcomes:
      - ``LOGGED_IN`` → ``mark_verified('medium')`` updates
        ``last_verified_at = now``.
      - ``EXPIRED`` (from probe redirect to ``/m/signin``) →
        ``mark_expired('medium')`` flips the store state.
      - ``NEVER_BOUND``, ``CACHED_BOUND``, ``NEEDS_RECHECK`` are read-only.

    Returns ``NEEDS_RECHECK`` if the probe exceeds ``timeout_s`` so the
    caller (typically ``_get_medium_status`` in helpers.py) can render
    without blocking.
    """
    from webui_store.channel_status import (
        get_status,
        mark_expired,
        mark_verified,
    )

    status = get_status("medium")
    state = status.get("status", "unbound")

    # Fast-path: no credential at all.
    if state == "unbound" or not _storage_state_path().exists():
        return LivenessResult.NEVER_BOUND

    # Fast-path: store already says expired (publish set it, prior probe
    # set it, or operator did). Don't probe; reflect store truth.
    if state == "expired":
        return LivenessResult.EXPIRED

    # Cache: within TTL, return without probe.
    age = _last_verified_age_seconds(status.get("last_verified_at"))
    if age < _LIVENESS_TTL_SECONDS:
        return LivenessResult.CACHED_BOUND

    # Active probe disabled (default until Spike 2 confirms anti-bot
    # behavior). Report needs_recheck so UI shows "needs attention" badge
    # without lying about bound/expired.
    if not MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED:
        return LivenessResult.NEEDS_RECHECK

    storage_state = _load_storage_state_for_probe()
    if storage_state is None:
        return LivenessResult.NEVER_BOUND

    # Run the probe in a worker thread with a hard wall-clock cap.
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_active_probe, storage_state)
            result = future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        log.warn(
            f"medium_liveness: probe exceeded {timeout_s}s budget; needs_recheck"
        )
        return LivenessResult.NEEDS_RECHECK
    except Exception as exc:  # noqa: BLE001 — defensive
        log.warn(
            f"medium_liveness: probe raised: "
            f"{type(exc).__name__}: {exc}"
        )
        return LivenessResult.NEEDS_RECHECK

    # Apply side effects on definite outcomes.
    if result == LivenessResult.LOGGED_IN:
        try:
            mark_verified("medium")
        except Exception as exc:  # noqa: BLE001
            log.warn(
                f"medium_liveness: mark_verified failed: "
                f"{type(exc).__name__}: {exc}"
            )
    elif result == LivenessResult.EXPIRED:
        try:
            mark_expired("medium")
        except Exception as exc:  # noqa: BLE001
            log.warn(
                f"medium_liveness: mark_expired failed: "
                f"{type(exc).__name__}: {exc}"
            )

    return result


__all__ = [
    "LivenessResult",
    "MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED",
    "_active_probe",
    "_load_storage_state_for_probe",
    "_storage_state_path",
    "medium_liveness_check",
]
