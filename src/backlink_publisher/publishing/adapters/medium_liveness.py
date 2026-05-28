"""Medium liveness probe — Playwright-based session health check.

Extracted from webui_app/medium_liveness.py — Wave 1 thin-WebUI refactor.
Contains only probe logic (no WebUI store coupling). The orchestration
function ``medium_liveness_check`` (which calls ``webui_store``) remains
in webui_app.

Design:
  1. **Probe-copy isolation**: when an active probe runs, the live
     ``storage_state.json`` is read into memory and passed as a dict to
     ``new_context(storage_state=...)``. The probe NEVER reads the live
     file via path. This way if Cloudflare/Datadome flags the probe
     request, only the in-memory copy is compromised — the live
     credential that headed publish reads is untouched.
  2. **Conservative default**: ``MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED``
     defaults to ``False`` until anti-bot impact is confirmed.
"""

from __future__ import annotations

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
MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED: bool = False


class LivenessResult(enum.Enum):
    """Outcomes of a liveness probe. UI maps to badge states."""

    NEVER_BOUND = "never_bound"
    EXPIRED = "expired"
    CACHED_BOUND = "cached_bound"
    LOGGED_IN = "logged_in"
    NEEDS_RECHECK = "needs_recheck"


def _storage_state_path() -> Path:
    """Single source of truth for the bound credential.

    Reads ``medium-cookies.json`` (cookies-only), NOT
    ``medium-storage-state.json`` which was unlinked after bind.
    """
    return _config_dir() / "medium-cookies.json"


def _load_storage_state_for_probe() -> dict[str, Any] | None:
    """Read storage state into memory as a dict for probe-copy isolation.

    Returns ``None`` if absent. Single retry on JSONDecodeError covers
    the narrow race between atomic temp+rename and concurrent probe read.
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
    ThreadPoolExecutor with timeout by ``medium_liveness_check``.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warn("medium_liveness: Playwright not installed; needs_recheck")
        return LivenessResult.NEEDS_RECHECK

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
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
        # may CONTAIN "/m/signin" as a redirect param, so check challenge first.
        if (
            "challenges.cloudflare.com" in final_url
            or "__cf_chl_" in final_url
            or "datadome" in final_url.lower()
        ):
            return LivenessResult.NEEDS_RECHECK
        if "/m/signin" in final_url:
            return LivenessResult.EXPIRED
        if "medium.com" in final_url:
            return LivenessResult.LOGGED_IN
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


__all__ = [
    "LivenessResult",
    "MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED",
    "_active_probe",
    "_load_storage_state_for_probe",
]
