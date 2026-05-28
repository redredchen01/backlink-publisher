"""Tests for ``webui_app/medium_liveness.py`` — Plan 2026-05-19-003 Unit 5.

Locks the contract:
  - Cache: ``last_verified_at`` < 5min ago → ``CACHED_BOUND`` (no probe).
  - State short-circuits: unbound → ``NEVER_BOUND``; expired → ``EXPIRED``.
  - Probe-disabled (default) → ``NEEDS_RECHECK`` after cache miss.
  - Probe outcomes: logged-in URL → ``LOGGED_IN`` + ``mark_verified``;
    ``/m/signin`` → ``EXPIRED`` + ``mark_expired``; Cloudflare/Datadome
    challenge → ``NEEDS_RECHECK`` (no store mutation).
  - Timeout: probe exceeds ``timeout_s`` → ``NEEDS_RECHECK``.
  - Atomic-write race: storage_state.json JSONDecodeError → single retry.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.config.loader import _config_dir
from webui_app import medium_liveness
from webui_app.medium_liveness import (
    LivenessResult,
    _active_probe,
    _load_storage_state_for_probe,
    _storage_state_path,
    medium_liveness_check,
)


@pytest.fixture(autouse=True)
def _reset_channel_status(monkeypatch):
    """Fresh channel-status.json + medium-cookies.json per test."""
    cfg = _config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    for name in ("channel-status.json", "medium-cookies.json"):
        p = cfg / name
        if p.exists():
            p.unlink()
    from webui_store import channel_status_store
    monkeypatch.setattr(
        channel_status_store, "path", cfg / "channel-status.json", raising=False
    )


def _write_storage_state(payload: dict | None = None) -> Path:
    """Post-Plan 005: medium-cookies.json is the canonical credential."""
    target = _config_dir() / "medium-cookies.json"
    target.write_text(json.dumps(payload or {"cookies": []}))
    return target


def _mark_bound_with_last_verified(seconds_ago: float | None) -> None:
    """Helper: stamp a bound record with ``last_verified_at`` set N seconds
    ago (or ``None`` for never-verified)."""
    from webui_store.channel_status import mark_bound
    target = _write_storage_state()
    mark_bound("medium", target)
    if seconds_ago is not None:
        # mark_verified sets to now; we monkey the record to backdate.
        from webui_store import channel_status_store
        ts = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat(
            timespec="seconds"
        )

        def _patch(current):
            current = dict(current)
            rec = dict(current.get("medium", {}))
            rec["last_verified_at"] = ts
            current["medium"] = rec
            return current

        channel_status_store.update(_patch)


# ─── State short-circuits (no probe) ───


class TestNeverBoundShortCircuit:
    def test_storage_state_absent_returns_never_bound(self):
        # No mark_bound, no storage_state.json
        assert medium_liveness_check() == LivenessResult.NEVER_BOUND

    def test_expired_state_returns_expired_without_probe(self):
        from webui_store.channel_status import mark_bound, mark_expired
        target = _write_storage_state()
        mark_bound("medium", target)
        mark_expired("medium")
        # No probe should run — verified by patching _active_probe to raise.
        with patch(
            "webui_app.medium_liveness._active_probe",
            side_effect=AssertionError("probe should not run on expired state"),
        ):
            assert medium_liveness_check() == LivenessResult.EXPIRED


# ─── TTL cache ───


class TestTTLCache:
    def test_recently_verified_returns_cached_bound(self):
        _mark_bound_with_last_verified(seconds_ago=60)  # 1 minute ago
        with patch(
            "webui_app.medium_liveness._active_probe",
            side_effect=AssertionError("probe should not run on cache hit"),
        ):
            assert medium_liveness_check() == LivenessResult.CACHED_BOUND

    def test_stale_last_verified_at_busts_cache(self, monkeypatch):
        _mark_bound_with_last_verified(seconds_ago=600)  # 10 minutes ago
        # Cache miss; with probe disabled (default), result is NEEDS_RECHECK
        monkeypatch.setattr(
            medium_liveness, "MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED", False
        )
        assert medium_liveness_check() == LivenessResult.NEEDS_RECHECK

    def test_no_last_verified_at_busts_cache(self, monkeypatch):
        _mark_bound_with_last_verified(seconds_ago=None)  # never verified
        monkeypatch.setattr(
            medium_liveness, "MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED", False
        )
        assert medium_liveness_check() == LivenessResult.NEEDS_RECHECK


# ─── Probe disabled (default) ───


class TestProbeDisabledDefault:
    def test_probe_disabled_returns_needs_recheck(self, monkeypatch):
        _mark_bound_with_last_verified(seconds_ago=600)
        monkeypatch.setattr(
            medium_liveness, "MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED", False
        )
        # _active_probe should not be invoked
        with patch(
            "webui_app.medium_liveness._active_probe",
            side_effect=AssertionError("probe disabled but called"),
        ):
            assert medium_liveness_check() == LivenessResult.NEEDS_RECHECK


# ─── Active probe outcomes ───


class TestActiveProbeOutcomes:
    """When probe enabled and cache stale, _active_probe drives the verdict."""

    def _enable_probe(self, monkeypatch):
        monkeypatch.setattr(
            medium_liveness, "MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED", True
        )

    def test_logged_in_url_returns_logged_in_and_marks_verified(self, monkeypatch):
        _mark_bound_with_last_verified(seconds_ago=600)
        self._enable_probe(monkeypatch)
        with patch(
            "webui_app.medium_liveness._active_probe",
            return_value=LivenessResult.LOGGED_IN,
        ):
            result = medium_liveness_check()
        assert result == LivenessResult.LOGGED_IN

        from webui_store.channel_status import get_status
        rec = get_status("medium")
        # last_verified_at refreshed; age now near zero
        from webui_app.medium_liveness import _last_verified_age_seconds
        assert _last_verified_age_seconds(rec["last_verified_at"]) < 5

    def test_signin_redirect_returns_expired_and_marks_expired(self, monkeypatch):
        _mark_bound_with_last_verified(seconds_ago=600)
        self._enable_probe(monkeypatch)
        with patch(
            "webui_app.medium_liveness._active_probe",
            return_value=LivenessResult.EXPIRED,
        ):
            result = medium_liveness_check()
        assert result == LivenessResult.EXPIRED

        from webui_store.channel_status import get_status
        assert get_status("medium")["status"] == "expired"

    def test_cloudflare_challenge_returns_needs_recheck_no_mutation(self, monkeypatch):
        _mark_bound_with_last_verified(seconds_ago=600)
        self._enable_probe(monkeypatch)
        with patch(
            "webui_app.medium_liveness._active_probe",
            return_value=LivenessResult.NEEDS_RECHECK,
        ):
            result = medium_liveness_check()
        assert result == LivenessResult.NEEDS_RECHECK

        from webui_store.channel_status import get_status
        # State unchanged (still bound, last_verified_at NOT refreshed)
        rec = get_status("medium")
        assert rec["status"] == "bound"


# ─── Timeout budget ───


class TestProbeTimeout:
    def test_probe_exceeding_timeout_returns_needs_recheck(self, monkeypatch):
        _mark_bound_with_last_verified(seconds_ago=600)
        monkeypatch.setattr(
            medium_liveness, "MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED", True
        )

        def _slow_probe(storage_state):
            time.sleep(2.0)  # exceeds 0.1s budget
            return LivenessResult.LOGGED_IN

        with patch("webui_app.medium_liveness._active_probe", _slow_probe):
            result = medium_liveness_check(timeout_s=0.1)
        assert result == LivenessResult.NEEDS_RECHECK


# ─── Atomic-write race retry ───


class TestStorageStateReadRetry:
    def test_jsondecodeerror_retries_then_returns_none(self, monkeypatch):
        # Write garbage so json.loads fails
        target = _config_dir() / "medium-cookies.json"
        target.write_text("{ corrupt")

        # _load_storage_state_for_probe retries once then gives up → None
        assert _load_storage_state_for_probe() is None

    def test_valid_json_loads_without_retry(self):
        _write_storage_state({"cookies": [{"name": "x"}]})
        result = _load_storage_state_for_probe()
        assert result is not None
        assert result["cookies"][0]["name"] == "x"

    def test_absent_file_returns_none(self):
        assert _load_storage_state_for_probe() is None


# ─── Active probe URL classification (unit, no real Playwright) ───


class TestActiveProbeURLClassification:
    """Test the URL-to-LivenessResult logic in _active_probe by mocking
    sync_playwright with controllable page.url and goto behavior."""

    def _mock_pw(self, final_url, goto_raises=None):
        mock_page = MagicMock()
        mock_page.url = final_url
        if goto_raises is not None:
            mock_page.goto.side_effect = goto_raises
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_pw.__enter__ = MagicMock(return_value=mock_pw)
        mock_pw.__exit__ = MagicMock(return_value=False)
        return mock_pw

    def test_profile_url_classifies_logged_in(self):
        pw = self._mock_pw("https://medium.com/@alice")
        with patch("playwright.sync_api.sync_playwright", return_value=pw):
            assert _active_probe({"cookies": []}) == LivenessResult.LOGGED_IN

    def test_signin_url_classifies_expired(self):
        pw = self._mock_pw("https://medium.com/m/signin?redirect=/me")
        with patch("playwright.sync_api.sync_playwright", return_value=pw):
            assert _active_probe({"cookies": []}) == LivenessResult.EXPIRED

    def test_cloudflare_url_classifies_needs_recheck(self):
        pw = self._mock_pw("https://challenges.cloudflare.com/cdn-cgi/challenge")
        with patch("playwright.sync_api.sync_playwright", return_value=pw):
            assert _active_probe({"cookies": []}) == LivenessResult.NEEDS_RECHECK

    def test_cf_chl_marker_classifies_needs_recheck(self):
        pw = self._mock_pw("https://medium.com/?__cf_chl_token=abc")
        with patch("playwright.sync_api.sync_playwright", return_value=pw):
            assert _active_probe({"cookies": []}) == LivenessResult.NEEDS_RECHECK

    def test_goto_exception_returns_needs_recheck(self):
        pw = self._mock_pw("about:blank", goto_raises=RuntimeError("net::ERR"))
        with patch("playwright.sync_api.sync_playwright", return_value=pw):
            assert _active_probe({"cookies": []}) == LivenessResult.NEEDS_RECHECK
