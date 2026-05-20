"""Tests for backlink_publisher.verify_publish module."""

from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pytest

from backlink_publisher.linkcheck.verify import VerificationResult, verify_published


# ── helpers ────────────────────────────────────────────────────────────────────

def _mock_get(status: int, body: str):
    """Patch _get_body to return a fixed status+body."""
    return patch(
        "backlink_publisher.linkcheck.verify._get_body",
        return_value=(status, body),
    )


def _good_body(title="Test Article", link_url="https://example.com"):
    return f"""<html><head><title>{title}</title></head>
<body><h1>{title}</h1><a href="{link_url}">Link</a></body></html>"""


# ── happy paths ────────────────────────────────────────────────────────────────

def test_verify_passes_when_all_conditions_met():
    body = _good_body("My Article", "https://example.com")
    with _mock_get(200, body):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="My Article",
            required_link_urls=["https://example.com"],
        )
    assert result.ok is True
    assert result.reason == ""


def test_verify_passes_with_empty_title():
    body = _good_body()
    with _mock_get(200, body):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="",
            required_link_urls=["https://example.com"],
        )
    assert result.ok is True


def test_verify_passes_with_no_required_links():
    body = _good_body("My Article")
    with _mock_get(200, body):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="My Article",
            required_link_urls=[],
        )
    assert result.ok is True


def test_verify_title_case_insensitive():
    body = "<html><body>MY ARTICLE is great. <a href='https://x.com'>x</a></body></html>"
    with _mock_get(200, body):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="my article",
            required_link_urls=["https://x.com"],
        )
    assert result.ok is True


# ── failure paths ──────────────────────────────────────────────────────────────

def test_verify_fails_on_http_404():
    with _mock_get(404, "<html>Not Found</html>"):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="Title",
            required_link_urls=[],
            max_wait=0,
        )
    assert result.ok is False
    assert "HTTP 404" in result.reason


def test_verify_fails_when_title_missing():
    body = "<html><body>completely different content</body></html>"
    with _mock_get(200, body):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="My Article",
            required_link_urls=[],
            max_wait=0,
        )
    assert result.ok is False
    assert "title not found" in result.reason


def test_verify_fails_when_required_link_missing():
    body = "<html><body>article text without the target link</body></html>"
    with _mock_get(200, body):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="",
            required_link_urls=["https://example.com/must-be-here"],
            max_wait=0,
        )
    assert result.ok is False
    assert "required links not found" in result.reason


def test_verify_fails_on_fetch_error():
    with _mock_get(0, "Connection refused"):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="Title",
            required_link_urls=[],
            max_wait=0,
        )
    assert result.ok is False
    assert "fetch failed" in result.reason


def test_verify_fails_on_empty_url():
    result = verify_published("", title="Title", required_link_urls=[], max_wait=0)
    assert result.ok is False
    assert "no valid URL" in result.reason


def test_verify_fails_on_non_http_url():
    result = verify_published(
        "ftp://example.com/post",
        title="Title",
        required_link_urls=[],
        max_wait=0,
    )
    assert result.ok is False
    assert "no valid URL" in result.reason


# ── retry / polling behaviour ──────────────────────────────────────────────────

def test_verify_retries_until_success():
    """First attempt returns 404, second returns 200 with content."""
    good_body = _good_body("My Article", "https://example.com")
    call_count = 0

    def side_effect(url):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (404, "")
        return (200, good_body)

    with patch("backlink_publisher.linkcheck.verify._get_body", side_effect=side_effect):
        with patch("backlink_publisher.linkcheck.verify.time.sleep"):
            result = verify_published(
                "https://blog.example.com/post/1",
                title="My Article",
                required_link_urls=["https://example.com"],
                max_wait=30,
            )
    assert result.ok is True
    assert call_count == 2


def test_verify_gives_up_after_max_wait():
    """All attempts fail; verify returns False after max_wait."""
    with _mock_get(404, ""):
        with patch("backlink_publisher.linkcheck.verify.time.sleep"):
            with patch("backlink_publisher.linkcheck.verify.time.monotonic") as mock_mono:
                # Simulate: first call is before deadline, second is after
                mock_mono.side_effect = [0, 0, 100]  # start, check 1, check 2
                result = verify_published(
                    "https://blog.example.com/post/1",
                    title="Title",
                    required_link_urls=[],
                    max_wait=30,
                )
    assert result.ok is False
    assert "verification failed" in result.reason


def test_verify_reports_attempt_count_in_reason():
    """Failure reason mentions attempt count."""
    with _mock_get(404, ""):
        with patch("backlink_publisher.linkcheck.verify.time.sleep"):
            with patch("backlink_publisher.linkcheck.verify.time.monotonic") as mock_mono:
                mock_mono.side_effect = [0, 0, 0, 100]
                result = verify_published(
                    "https://x.com/p/1",
                    title="T",
                    required_link_urls=[],
                    max_wait=30,
                )
    assert "attempt" in result.reason
