"""Regression tests for ``_medium_bound_predicate`` timeout semantics.

Lock the invariant the module docstring promises: when the ``framenavigated``
listener never fires (Spike 7 verdict — cross-origin SSO can orphan the
listener silently), the absolute wall-clock timeout — *not* the 90s idle
timeout — is the only floor. Operators must keep their full SSO+2FA
budget even when nav events go missing entirely.

Prior to the fix, ``last_nav_at`` was initialized to ``started_at`` and
the idle check fired ``_IDLE_TIMEOUT_SECONDS`` after the predicate began,
long before the 20-minute absolute floor was reached. The fix gates idle
enforcement on ``last_nav_at is not None`` (i.e., at least one observed
nav event).
"""

from __future__ import annotations

import os
import sys
import time as time_mod

import pytest

# Ensure repo root is importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_page_that_never_transitions():
    """Playwright ``Page`` stub: never satisfies ``wait_for_url`` and never
    fires ``framenavigated``. Mirrors the Spike 7 silent-failure scenario."""
    from playwright.sync_api import TimeoutError as PWTimeoutError

    class _FakePage:
        def __init__(self) -> None:
            self._on_nav = None

        def on(self, event: str, cb) -> None:
            if event == "framenavigated":
                self._on_nav = cb

        def wait_for_url(self, pattern, timeout=None):
            # Honour the timeout so the predicate's loop advances through
            # wall-clock time at the same rate it would in production.
            if timeout is not None:
                time_mod.sleep(timeout / 1000.0)
            raise PWTimeoutError("test: URL never transitions")

    return _FakePage()


class TestNavNeverFiresHitsAbsoluteNotIdle:
    """The regression. With the bug, predicate raised at ``_IDLE_TIMEOUT``
    elapsed; with the fix it must survive until ``_ABSOLUTE_TIMEOUT``."""

    def test_predicate_raises_only_at_absolute_floor(self, monkeypatch):
        from backlink_publisher.cli._bind.driver import BoundPredicateTimeout
        from backlink_publisher.cli._bind.recipes import medium

        monkeypatch.setattr(medium, "_IDLE_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(medium, "_ABSOLUTE_TIMEOUT_SECONDS", 0.8)
        monkeypatch.setattr(medium, "_INNER_WAIT_TIMEOUT_MS", 50)

        page = _make_page_that_never_transitions()

        started = time_mod.monotonic()
        with pytest.raises(BoundPredicateTimeout):
            medium._medium_bound_predicate(page)
        elapsed = time_mod.monotonic() - started

        # Must clear the idle ceiling (0.3s) by a comfortable margin.
        # Hitting the absolute floor (0.8s) confirms idle did NOT fire
        # prematurely.
        assert elapsed >= 0.7, (
            f"predicate raised at {elapsed:.2f}s — pre-fix behavior would "
            f"raise at ~{medium._IDLE_TIMEOUT_SECONDS}s (idle, with the "
            "bug). Absolute floor is 0.8s; idle was supposed to be gated "
            "on first-nav-observed."
        )


class TestIdleStillFiresAfterFirstNav:
    """Sanity: gating idle on ``last_nav_at is not None`` must not break
    the normal post-first-nav idle behavior. Once *any* nav event lands,
    subsequent silence for ``_IDLE_TIMEOUT_SECONDS`` should still raise."""

    def test_idle_enforced_after_first_nav_event(self, monkeypatch):
        from backlink_publisher.cli._bind.driver import BoundPredicateTimeout
        from backlink_publisher.cli._bind.recipes import medium

        monkeypatch.setattr(medium, "_IDLE_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(medium, "_ABSOLUTE_TIMEOUT_SECONDS", 5.0)
        monkeypatch.setattr(medium, "_INNER_WAIT_TIMEOUT_MS", 50)

        page = _make_page_that_never_transitions()

        # Trigger one nav event on the first wait_for_url call, then
        # stay silent. The predicate should raise via idle ~0.3s after,
        # well before the 5s absolute floor.
        original_wait = page.wait_for_url
        nav_fired = [False]

        def _wait_with_first_nav(pattern, timeout=None):
            if not nav_fired[0]:
                page._on_nav(None)  # simulate one framenavigated event
                nav_fired[0] = True
            return original_wait(pattern, timeout=timeout)

        page.wait_for_url = _wait_with_first_nav  # type: ignore[method-assign]

        started = time_mod.monotonic()
        with pytest.raises(BoundPredicateTimeout):
            medium._medium_bound_predicate(page)
        elapsed = time_mod.monotonic() - started

        # Must raise via idle (≈0.3s after first nav), nowhere near
        # the 5.0s absolute ceiling.
        assert elapsed < 2.0, (
            f"predicate raised at {elapsed:.2f}s — once a nav has been "
            "observed, idle should fire near _IDLE_TIMEOUT_SECONDS, well "
            "under the 5s absolute floor. Gate may have over-reached."
        )
