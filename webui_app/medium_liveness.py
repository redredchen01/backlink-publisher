"""Plan 2026-05-19-003 Unit 5 — Medium liveness probe.

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
import enum
import json
import time
from pathlib import Path
from typing import Any

from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.config.loader import _config_dir


# Plan 003 Unit 5 / Unit 0 spike output: default OFF until Spike 2
# confirms headless probe doesn't trip Cloudflare/Datadome on real
# Medium accounts. Flip to True once spike runs report no challenges.
#
# Known limit (PR #83 adversarial review, P1 #5): when the probe runs
# against medium.com, Cloudflare may issue a FRESH cf_clearance cookie
# to the probe context. Because we use probe-copy isolation (the live
# storage_state.json is never mutated), that fresh clearance never
# propagates back. The IP-reputation side is implicit: the next real
# publish from this host sends the OLD cf_clearance, which Cloudflare
# may treat as a fingerprint mismatch and challenge. Before flipping
# this to True, rate-limit liveness probes well below the 5-min TTL —
# 1x/day is sufficient for "is the badge green" UX without depleting
# the publish path's clearance budget.
MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED: bool = False


# 5-minute cache on last_verified_at — every Settings page load within
# this window short-circuits to CACHED_BOUND without spawning a probe.
_LIVENESS_TTL_SECONDS = 300


class LivenessResult(enum.Enum):
    """Outcomes of ``medium_liveness_check``. UI maps to badge states."""

    NEVER_BOUND = "never_bound"
    EXPIRED = "expired"
    CACHED_BOUND = "cached_bound"
    LOGGED_IN = "logged_in"
    NEEDS_RECHECK = "needs_recheck"


def _storage_state_path() -> Path:
    """Single source of truth for the bound credential. Matches the
    constant in ``MediumBrowserAdapter`` (Plan 003 Unit 6)."""
    return _config_dir() / "medium-storage-state.json"


def _last_verified_age_seconds(last_verified_at: str | None) -> float:
    """Seconds since ``last_verified_at`` (ISO string). ``inf`` if absent
    or unparseable."""
    if not last_verified_at:
        return float("inf")
    try:
        from datetime import datetime
        ts = datetime.fromisoformat(last_verified_at)
        # Datetime arithmetic is timezone-aware when both have tzinfo.
        now = datetime.fromisoformat(
            datetime.now(ts.tzinfo).isoformat(timespec="seconds")
            if ts.tzinfo
            else datetime.now().isoformat(timespec="seconds")
        )
        return (now - ts).total_seconds()
    except (ValueError, TypeError):
        return float("inf")


def _load_storage_state_for_probe() -> dict[str, Any] | None:
    """Read ``storage_state.json`` into memory as a dict for safe pass to
    ``new_context(storage_state=...)``. Returns ``None`` if absent.

    Single retry on JSONDecodeError covers the narrow race between
    ``MediumBrowserAdapter._refresh_storage_state``'s atomic temp+rename
    and a concurrent probe read. ``os.replace`` is atomic at the FS
    level; transient read failures are unlikely but the retry adds
    defense-in-depth at negligible cost.

    Known limit (PR #83 adversarial review, P1 #2): a different race
    survives this loader — the probe can grab the OLD file's bytes
    *just before* a concurrent ``_refresh_storage_state`` rename, then
    use those cookies in ``_active_probe``. If the new file contains
    rotated session cookies, the probe will hit /m/signin with the
    stale state and call ``mark_expired``, demoting a credential that
    was just refreshed seconds ago. Mitigation when the probe ships:
    re-stat after probe verdict; if mtime advanced, suppress
    ``mark_expired`` for this tick (next Settings load reads the fresh
    file with a fresh cache TTL and gets the correct verdict).
    """
    path = _storage_state_path()
    if not path.exists():
        return None
    for attempt in (1, 2):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            if attempt == 1:
                time.sleep(0.05)
                continue
            log.warn(
                "medium_liveness: storage_state.json unreadable after retry; "
                "treating as needs_recheck"
            )
            return None
        except OSError as exc:
            log.warn(
                f"medium_liveness: storage_state.json read error: "
                f"{type(exc).__name__}: {exc}"
            )
            return None
    return None


def _active_probe(storage_state: dict[str, Any]) -> LivenessResult:
    """Launch a short-lived headless Chromium, load storage_state from
    memory, goto medium.com/me, inspect final URL.

    NOT called from the Flask request thread directly — wrapped in a
    ThreadPoolExecutor with timeout by the public ``medium_liveness_check``.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warn("medium_liveness: Playwright not installed; needs_recheck")
        return LivenessResult.NEEDS_RECHECK

    try:
        with sync_playwright() as pw:
            # Plan 003 Unit 5: launch (not launch_persistent_context) — the
            # probe shares NO disk state with the headed publish; if anti-bot
            # flags this session, the live storage_state.json is untouched.
            browser = pw.chromium.launch(headless=True)
            try:
                # Pass the dict directly; Playwright accepts storage_state
                # as path-or-dict (we use dict here for probe-copy isolation).
                context = browser.new_context(storage_state=storage_state)
                page = context.new_page()
                try:
                    page.goto("https://medium.com/me", timeout=8000)
                except Exception as exc:  # noqa: BLE001
                    log.warn(
                        f"medium_liveness: goto failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    return LivenessResult.NEEDS_RECHECK
                final_url = page.url
            finally:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass

        # Outcome classification — order matters: a Cloudflare challenge URL
        # may CONTAIN "/m/signin" as a redirect param, so check challenge
        # first.
        if (
            "challenges.cloudflare.com" in final_url
            or "__cf_chl_" in final_url
            or "datadome" in final_url.lower()
        ):
            return LivenessResult.NEEDS_RECHECK
        if "/m/signin" in final_url:
            return LivenessResult.EXPIRED
        # Match the Plan 001 recipe's bound-URL semantics — any medium.com
        # URL that isn't /m/signin is treated as logged in (consistent with
        # the bind recipe predicate).
        if "medium.com" in final_url:
            return LivenessResult.LOGGED_IN
        # Unexpected landing (e.g., 503 page on a different host) — don't
        # claim either bound or expired.
        log.warn(
            f"medium_liveness: probe ended on unexpected URL {final_url!r}; "
            f"needs_recheck"
        )
        return LivenessResult.NEEDS_RECHECK
    except Exception as exc:  # noqa: BLE001 — defensive
        log.warn(
            f"medium_liveness: active probe failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return LivenessResult.NEEDS_RECHECK


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
    # NEEDS_RECHECK and any other outcome: do NOT mutate the store —
    # let the cached state ride until the next probe gives a definite
    # answer.

    return result


__all__ = [
    "LivenessResult",
    "MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED",
    "_active_probe",
    "_load_storage_state_for_probe",
    "medium_liveness_check",
]
