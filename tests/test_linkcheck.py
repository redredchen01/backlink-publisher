"""Tests for linkcheck.check_url (Unit 4 of plan 2026-05-14-001).

Focused on the additive public wrapper. The existing
``_check_url_with_retry`` and ``check_urls_strict`` paths are not
re-tested here — they're exercised via test_validate_backlinks.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backlink_publisher import linkcheck


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock time.sleep at the module reference so retry delays don't slow tests."""
    monkeypatch.setattr("backlink_publisher.linkcheck.time", _FakeTime())


class _FakeTime:
    def sleep(self, _seconds: float) -> None:
        return None


def test_check_url_reachable_returns_true_none() -> None:
    with patch(
        "backlink_publisher.linkcheck._check_url_once",
        return_value=(True, None),
    ):
        ok, err = linkcheck.check_url("https://example.com")
    assert ok is True
    assert err is None


def test_check_url_unreachable_after_retries_returns_false_with_error() -> None:
    with patch(
        "backlink_publisher.linkcheck._check_url_once",
        return_value=(False, "HTTP 404"),
    ) as mocked:
        ok, err = linkcheck.check_url("https://example.com/dead")
    assert ok is False
    assert err == "HTTP 404"
    # 3 attempts total: initial + MAX_RETRIES=2 retries.
    assert mocked.call_count == 3


def test_check_url_succeeds_on_second_attempt() -> None:
    side_effects = iter([(False, "Timeout"), (True, None)])

    def fake_once(_url: str) -> tuple[bool, str | None]:
        return next(side_effects)

    with patch("backlink_publisher.linkcheck._check_url_once", side_effect=fake_once):
        ok, err = linkcheck.check_url("https://example.com/slow")
    assert ok is True
    assert err is None
