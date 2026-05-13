"""Tests for backlink_publisher.bulk_input module."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from backlink_publisher.bulk_input import (
    derive_main_domain,
    parse_csv,
    parse_sitemap,
    urls_to_seed_rows,
)


# ── derive_main_domain ─────────────────────────────────────────────────────────

def test_derive_main_domain_https():
    assert derive_main_domain("https://example.com/path/page") == "https://example.com"


def test_derive_main_domain_http():
    assert derive_main_domain("http://example.com/category/foo?x=1") == "http://example.com"


def test_derive_main_domain_with_port():
    assert derive_main_domain("https://example.com:8080/path") == "https://example.com:8080"


def test_derive_main_domain_bare_returns_as_is():
    # If URL has no scheme/netloc, return as-is
    result = derive_main_domain("example.com")
    assert result == "example.com"


# ── parse_csv ──────────────────────────────────────────────────────────────────

def test_parse_csv_simple(tmp_path):
    f = tmp_path / "urls.csv"
    f.write_text("https://example.com/a\nhttps://example.com/b\n", encoding="utf-8")
    assert parse_csv(f) == ["https://example.com/a", "https://example.com/b"]


def test_parse_csv_skips_blank_lines(tmp_path):
    f = tmp_path / "urls.csv"
    f.write_text("\nhttps://a.com\n\nhttps://b.com\n\n", encoding="utf-8")
    assert parse_csv(f) == ["https://a.com", "https://b.com"]


def test_parse_csv_skips_comment_lines(tmp_path):
    f = tmp_path / "urls.csv"
    f.write_text("# header comment\nhttps://a.com\n# another\nhttps://b.com\n", encoding="utf-8")
    assert parse_csv(f) == ["https://a.com", "https://b.com"]


def test_parse_csv_strips_quotes_and_commas(tmp_path):
    f = tmp_path / "urls.csv"
    f.write_text('"https://a.com",\n\'https://b.com\',\n', encoding="utf-8")
    assert parse_csv(f) == ["https://a.com", "https://b.com"]


def test_parse_csv_path_object(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("https://x.com\n", encoding="utf-8")
    assert parse_csv(str(f)) == ["https://x.com"]


def test_parse_csv_stdin():
    fake_stdin = io.StringIO("https://stdin.com/a\nhttps://stdin.com/b\n")
    with patch("sys.stdin", fake_stdin):
        result = parse_csv("-")
    assert result == ["https://stdin.com/a", "https://stdin.com/b"]


def test_parse_csv_empty_file(tmp_path):
    f = tmp_path / "empty.csv"
    f.write_text("", encoding="utf-8")
    assert parse_csv(f) == []


# ── parse_sitemap ──────────────────────────────────────────────────────────────

_SIMPLE_SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/page1</loc></url>
  <url><loc>https://example.com/page2</loc></url>
  <url><loc>https://example.com/page3</loc></url>
</urlset>"""

_SITEMAP_INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap2.xml</loc></sitemap>
</sitemapindex>"""

_SUB_SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/sub/a</loc></url>
  <url><loc>https://example.com/sub/b</loc></url>
</urlset>"""


def _mock_response(content: bytes, status_code: int = 200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.content = content
    mock.raise_for_status = MagicMock()
    return mock


def test_parse_sitemap_simple():
    with patch("requests.get", return_value=_mock_response(_SIMPLE_SITEMAP)):
        urls = parse_sitemap("https://example.com/sitemap.xml")
    assert urls == [
        "https://example.com/page1",
        "https://example.com/page2",
        "https://example.com/page3",
    ]


def test_parse_sitemap_index_fetches_sub_sitemaps():
    def _get(url, **kwargs):
        if "index" in url or url == "https://example.com/sitemap.xml":
            return _mock_response(_SITEMAP_INDEX)
        return _mock_response(_SUB_SITEMAP)

    with patch("requests.get", side_effect=_get):
        urls = parse_sitemap("https://example.com/sitemap.xml")

    assert "https://example.com/sub/a" in urls
    assert "https://example.com/sub/b" in urls


def test_parse_sitemap_deduplicates():
    dup_sitemap = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/page1</loc></url>
  <url><loc>https://example.com/page1</loc></url>
</urlset>"""
    with patch("requests.get", return_value=_mock_response(dup_sitemap)):
        urls = parse_sitemap("https://example.com/sitemap.xml")
    assert urls.count("https://example.com/page1") == 1


def test_parse_sitemap_raises_on_network_error():
    with patch("requests.get", side_effect=ConnectionError("offline")):
        with pytest.raises(RuntimeError, match="Failed to fetch sitemap"):
            parse_sitemap("https://example.com/sitemap.xml")


def test_parse_sitemap_raises_on_invalid_xml():
    with patch("requests.get", return_value=_mock_response(b"not xml at all")):
        with pytest.raises(RuntimeError, match="Failed to parse sitemap XML"):
            parse_sitemap("https://example.com/sitemap.xml")


def test_parse_sitemap_no_namespace():
    no_ns = b"""<?xml version="1.0"?>
<urlset>
  <url><loc>https://example.com/x</loc></url>
</urlset>"""
    with patch("requests.get", return_value=_mock_response(no_ns)):
        urls = parse_sitemap("https://example.com/sitemap.xml")
    assert "https://example.com/x" in urls


# ── urls_to_seed_rows ──────────────────────────────────────────────────────────

def test_urls_to_seed_rows_basic():
    rows = urls_to_seed_rows(
        ["https://example.com/a", "https://example.com/b"],
        platform="blogger",
        language="zh-CN",
        url_mode="A",
        publish_mode="draft",
    )
    assert len(rows) == 2
    assert rows[0]["target_url"] == "https://example.com/a"
    assert rows[0]["main_domain"] == "https://example.com"
    assert rows[0]["platform"] == "blogger"
    assert rows[0]["language"] == "zh-CN"
    assert rows[0]["url_mode"] == "A"
    assert rows[0]["publish_mode"] == "draft"


def test_urls_to_seed_rows_auto_prefix_https():
    rows = urls_to_seed_rows(["example.com/page"])
    assert rows[0]["target_url"] == "https://example.com/page"
    assert rows[0]["main_domain"] == "https://example.com"


def test_urls_to_seed_rows_skips_empty():
    rows = urls_to_seed_rows(["", "  ", "https://a.com"])
    assert len(rows) == 1
    assert rows[0]["target_url"] == "https://a.com"


def test_urls_to_seed_rows_defaults():
    rows = urls_to_seed_rows(["https://example.com"])
    row = rows[0]
    assert row["platform"] == "blogger"
    assert row["language"] == "zh-CN"
    assert row["url_mode"] == "A"
    assert row["publish_mode"] == "draft"


def test_urls_to_seed_rows_custom_platform():
    rows = urls_to_seed_rows(["https://example.com"], platform="medium", language="en")
    assert rows[0]["platform"] == "medium"
    assert rows[0]["language"] == "en"
