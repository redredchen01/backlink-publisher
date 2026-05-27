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


# ── Plan 2026-05-21-005 ────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, code: int) -> None:
        self._code = code
    def getcode(self) -> int:
        return self._code


def test_check_url_once_normalizes_cjk_url_before_request() -> None:
    """HEAD branch: CJK URL is percent-encoded before urlopen sees it."""
    captured: list[str] = []

    def fake_urlopen(req, **kw):
        captured.append(req.full_url)
        return _FakeResp(200)

    with patch("backlink_publisher.linkcheck.http.urlopen", side_effect=fake_urlopen):
        ok, err = linkcheck._check_url_once("https://velog.io/@한글/슬러그")

    assert ok is True
    assert err is None
    assert len(captured) == 1
    captured[0].encode("ascii")  # would raise if non-ASCII slipped through
    assert "%" in captured[0]


def test_check_url_once_get_fallback_also_normalizes() -> None:
    """When HEAD fails, GET fallback must use the same normalized URL."""
    captured: list[str] = []

    def fake_urlopen(req, **kw):
        captured.append(req.full_url)
        if req.get_method() == "HEAD":
            raise OSError("simulated head failure")
        return _FakeResp(200)

    with patch("backlink_publisher.linkcheck.http.urlopen", side_effect=fake_urlopen):
        ok, err = linkcheck._check_url_once("https://velog.io/@한글/슬러그")

    assert ok is True
    assert len(captured) == 2
    for u in captured:
        u.encode("ascii")  # both HEAD and GET URLs must be ASCII-clean


def test_check_url_once_ascii_url_passthrough() -> None:
    """ASCII URLs are not silently rewritten."""
    captured: list[str] = []

    def fake_urlopen(req, **kw):
        captured.append(req.full_url)
        return _FakeResp(200)

    with patch("backlink_publisher.linkcheck.http.urlopen", side_effect=fake_urlopen):
        linkcheck._check_url_once("https://example.com/api/v1?q=1")

    assert captured == ["https://example.com/api/v1?q=1"]


@pytest.mark.parametrize("bad", ["http://[invalid", "http://[::1", "http://["])
def test_check_url_once_malformed_ipv6_returns_invalid_not_raises(bad) -> None:
    """A malformed-IPv6 URL must yield the (False, 'invalid URL') verdict, not a
    bare ValueError — _check_url_once parses before the scheme/netloc check, so
    the raise would otherwise escape its never-raises contract (Plan 006 R2)."""
    ok, err = linkcheck._check_url_once(bad)
    assert ok is False
    assert err is not None and "invalid URL" in err
