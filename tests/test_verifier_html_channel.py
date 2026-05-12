"""Tests for the HTML verification channel (Unit 2).

Covers host allowlist (with wildcard + IDNA + label-count), SSRF defense
via post-DNS IP allowlist, redirect cap, scoped article parsing, final-URL
path-shape check, retry/transient mapping, and the four 4xx/5xx/timeout
classes against the founding-incident defense.

Most tests patch `_fetch_html_once` at the boundary — keeps the test
matrix focused on policy decisions (host check, parsing scope, retry
classification) rather than urllib internals. A handful of low-level
helper tests exercise `_check_host_allowed`, `_check_resolved_ip_safe`,
and `_ArticleScopedCollector` directly.
"""

from __future__ import annotations

import socket
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

import pytest

from backlink_publisher.adapters.base import AdapterResult
from backlink_publisher.verifier import (
    _ArticleScopedCollector,
    _BodyTooLarge,
    _RedirectRejected,
    _WallClockExceeded,
    _check_host_allowed,
    _check_path_shape,
    _check_resolved_ip_safe,
    _normalize_host,
    _parse_and_match_html,
    _safe_for_log,
    verify_published,
)


# ---------- _check_host_allowed ----------


def test_host_exact_match():
    assert _check_host_allowed("medium.com", ("medium.com",))


def test_host_exact_match_rejects_substring():
    assert not _check_host_allowed("evilmedium.com", ("medium.com",))


def test_host_wildcard_matches_base():
    assert _check_host_allowed("medium.com", ("*.medium.com",))


def test_host_wildcard_matches_subdomain():
    assert _check_host_allowed("sub.medium.com", ("*.medium.com",))
    assert _check_host_allowed("deep.sub.medium.com", ("*.medium.com",))


def test_host_wildcard_rejects_label_smuggle():
    """attacker.medium.com.evil.com must NOT match *.medium.com."""
    assert not _check_host_allowed("attacker.medium.com.evil.com", ("*.medium.com",))


def test_host_wildcard_rejects_no_separator():
    """evilmedium.com must NOT match *.medium.com (label boundary required)."""
    assert not _check_host_allowed("evilmedium.com", ("*.medium.com",))


def test_host_wildcard_rejects_suffix_smuggle():
    """medium.com.evil.com must NOT match *.medium.com."""
    assert not _check_host_allowed("medium.com.evil.com", ("*.medium.com",))


def test_host_wildcard_does_not_match_unrelated():
    assert not _check_host_allowed("medium.org", ("*.medium.com",))


def test_blogspot_wildcard_matches_subdomain():
    """Blogger uses *.blogspot.com — confirm label-count math works.

    Note: per the plan's wildcard rule, `*.X` matches `X` itself too — the
    path-shape allowlist (e.g. requiring /YYYY/MM/post.html) is the layer
    that rejects bare `blogspot.com` as a non-article URL.
    """
    assert _check_host_allowed("myblog.blogspot.com", ("*.blogspot.com",))
    assert _check_host_allowed("blogspot.com", ("*.blogspot.com",))  # path-shape rejects bare host
    assert not _check_host_allowed("evilblogspot.com", ("*.blogspot.com",))


def test_blogspot_wildcard_with_base_in_allowlist():
    """*.blogspot.com + blogspot.com pair lets both exact and subdomain pass."""
    allowlist = ("*.blogspot.com", "blogspot.com")
    assert _check_host_allowed("blogspot.com", allowlist)
    assert _check_host_allowed("myblog.blogspot.com", allowlist)


# ---------- _normalize_host ----------


def test_normalize_lowercases_and_strips_trailing_dot():
    assert _normalize_host("MEDIUM.COM.") == "medium.com"
    assert _normalize_host("Sub.Medium.Com") == "sub.medium.com"


def test_normalize_returns_none_for_empty():
    assert _normalize_host(None) is None
    assert _normalize_host("") is None
    assert _normalize_host(".") is None


def test_normalize_rejects_invalid_idna():
    # An ASCII NUL or label longer than 63 chars cannot IDNA-encode.
    assert _normalize_host("a" * 64) is None


# ---------- _check_resolved_ip_safe ----------


def _make_addrinfo(ip: str) -> list:
    """Build a getaddrinfo-style tuple list with one address."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 0, "", (ip, 0))]


def test_resolved_ip_safe_for_public_address():
    with patch("backlink_publisher.verifier.socket.getaddrinfo",
               return_value=_make_addrinfo("93.184.216.34")):
        ok, err = _check_resolved_ip_safe("example.com")
    assert ok is True
    assert err is None


def test_resolved_ip_rejects_rfc1918():
    with patch("backlink_publisher.verifier.socket.getaddrinfo",
               return_value=_make_addrinfo("10.0.0.5")):
        ok, err = _check_resolved_ip_safe("medium.com")
    assert ok is False
    assert err == "host_resolved_to_private_ip: 10.0.0.5"


def test_resolved_ip_rejects_loopback():
    with patch("backlink_publisher.verifier.socket.getaddrinfo",
               return_value=_make_addrinfo("127.0.0.1")):
        ok, err = _check_resolved_ip_safe("medium.com")
    assert ok is False
    assert "127.0.0.1" in err


def test_resolved_ip_rejects_cloud_metadata():
    """169.254.169.254 — the AWS/GCP IPv4 metadata endpoint."""
    with patch("backlink_publisher.verifier.socket.getaddrinfo",
               return_value=_make_addrinfo("169.254.169.254")):
        ok, err = _check_resolved_ip_safe("medium.com")
    assert ok is False
    assert "169.254.169.254" in err


def test_resolved_ip_rejects_azure_wire_server():
    """168.63.129.16 — Azure metadata at a routable IP."""
    with patch("backlink_publisher.verifier.socket.getaddrinfo",
               return_value=_make_addrinfo("168.63.129.16")):
        ok, err = _check_resolved_ip_safe("medium.com")
    assert ok is False
    assert "168.63.129.16" in err


def test_resolved_ip_rejects_ipv6_link_local():
    with patch("backlink_publisher.verifier.socket.getaddrinfo",
               return_value=_make_addrinfo("fe80::1")):
        ok, err = _check_resolved_ip_safe("medium.com")
    assert ok is False
    assert "fe80" in err


def test_resolved_ip_rejects_cgnat():
    """Review-fix: RFC 6598 CGNAT (100.64.0.0/10) not flagged by stdlib."""
    with patch("backlink_publisher.verifier.socket.getaddrinfo",
               return_value=_make_addrinfo("100.64.5.10")):
        ok, err = _check_resolved_ip_safe("medium.com")
    assert ok is False
    assert "100.64.5.10" in err


def test_resolved_ip_rejects_6to4_anycast():
    with patch("backlink_publisher.verifier.socket.getaddrinfo",
               return_value=_make_addrinfo("192.88.99.1")):
        ok, err = _check_resolved_ip_safe("medium.com")
    assert ok is False
    assert "192.88.99.1" in err


def test_resolved_ip_rejects_when_any_address_is_private():
    """Multi-A-record: reject if ANY address is unsafe (no skip-and-continue)."""
    infos = _make_addrinfo("93.184.216.34") + _make_addrinfo("10.0.0.5")
    with patch("backlink_publisher.verifier.socket.getaddrinfo", return_value=infos):
        ok, err = _check_resolved_ip_safe("medium.com")
    assert ok is False
    assert "10.0.0.5" in err


def test_resolved_ip_dns_failure_marked_as_transient():
    with patch("backlink_publisher.verifier.socket.getaddrinfo",
               side_effect=socket.gaierror(-2, "Name or service not known")):
        ok, err = _check_resolved_ip_safe("nonexistent.example")
    assert ok is False
    assert err.startswith("dns_failure:")


# ---------- _check_path_shape ----------


def test_path_shape_matches_medium_user_slug():
    patterns = (r"^/@[^/]+/[\w\-]+", r"^/p/[\w]+")
    assert _check_path_shape("/@user/my-post-abc123", patterns)
    assert _check_path_shape("/p/abc123def", patterns)


def test_path_shape_rejects_homepage():
    patterns = (r"^/@[^/]+/[\w\-]+", r"^/p/[\w]+")
    assert not _check_path_shape("/", patterns)


def test_path_shape_rejects_tag_listing():
    patterns = (r"^/@[^/]+/[\w\-]+", r"^/p/[\w]+")
    assert not _check_path_shape("/tag/python", patterns)


def test_path_shape_matches_blogger_year_month_html():
    patterns = (r"^/\d{4}/\d{2}/.+\.html$",)
    assert _check_path_shape("/2026/05/post.html", patterns)
    assert not _check_path_shape("/", patterns)


def test_path_shape_empty_patterns_allows_anything():
    assert _check_path_shape("/anything", ())


# ---------- _safe_for_log ----------


def test_safe_for_log_strips_crlf():
    assert "\r" not in _safe_for_log("evil\r\nLog injection")
    assert "\n" not in _safe_for_log("evil\r\nLog injection")


def test_safe_for_log_caps_length():
    out = _safe_for_log("x" * 1000, max_len=20)
    assert len(out) == 20
    assert out.endswith("...")


def test_safe_for_log_handles_none():
    assert _safe_for_log(None) == ""
    assert _safe_for_log("") == ""


# ---------- _ArticleScopedCollector ----------


def test_collector_extracts_title_and_og_title():
    html = (
        '<html><head><title>The Real Title</title>'
        '<meta property="og:title" content="OG Title Here">'
        '</head><body><article><h1>H1 Title</h1><p>hi</p></article></body></html>'
    )
    c = _ArticleScopedCollector()
    c.feed(html)
    c.close()
    assert c.title_text.strip() == "The Real Title"
    assert c.og_title == "OG Title Here"
    assert c.h1_text.strip() == "H1 Title"


def test_collector_anchors_only_inside_article():
    """Hrefs in <nav> or <aside> outside <article> are ignored."""
    html = (
        '<html><body>'
        '<nav><a href="https://nav.example/">nav link</a></nav>'
        '<article>'
        '<a href="https://target.example/post">target</a>'
        '</article>'
        '<aside><a href="https://aside.example/">sidebar link</a></aside>'
        '</body></html>'
    )
    c = _ArticleScopedCollector()
    c.feed(html)
    c.close()
    assert c.article_hrefs == {"https://target.example/post"}


def test_collector_visible_text_scoped_to_article():
    """Title appearing only in <aside> is NOT in visible_text_chunks."""
    html = (
        '<html><body>'
        '<aside>Critical Phrase only in sidebar</aside>'
        '<article><p>some other content</p></article>'
        '</body></html>'
    )
    c = _ArticleScopedCollector()
    c.feed(html)
    c.close()
    body_text = "".join(c.visible_text_chunks)
    assert "Critical Phrase" not in body_text
    assert "some other content" in body_text


def test_collector_section_data_field_body_fallback():
    """Medium-specific fallback: <section data-field="body">."""
    html = (
        '<html><body>'
        '<section data-field="body">'
        '<a href="https://target.example/">target</a>'
        '</section>'
        '</body></html>'
    )
    c = _ArticleScopedCollector()
    c.feed(html)
    c.close()
    assert "https://target.example/" in c.article_hrefs


def test_collector_skips_script_content():
    """Hrefs inside JSON-in-script blobs do NOT count as article hrefs."""
    html = (
        '<html><body><article>'
        '<script>window.__STATE__ = {"href":"https://target.example/"};</script>'
        '<p>actual body</p>'
        '</article></body></html>'
    )
    c = _ArticleScopedCollector()
    c.feed(html)
    c.close()
    # The <script> content isn't parsed as anchors anyway, but visible text
    # extraction must skip its contents too.
    body_text = "".join(c.visible_text_chunks)
    assert "__STATE__" not in body_text
    assert "actual body" in body_text


def test_collector_first_h1_only():
    """Only the first <h1> contributes to h1_text — later ones don't override."""
    html = (
        '<html><body><article>'
        '<h1>First H1</h1>'
        '<h1>Second H1</h1>'
        '</article></body></html>'
    )
    c = _ArticleScopedCollector()
    c.feed(html)
    c.close()
    assert "First H1" in c.h1_text
    assert "Second H1" not in c.h1_text


# ---------- _parse_and_match_html ----------


def test_parse_match_happy_path_title_in_h1_and_all_hrefs_present():
    html = (
        '<html><head><title>How to Ship</title></head>'
        '<body><article>'
        '<h1>How to Ship Safely</h1>'
        '<p>intro</p>'
        '<a href="https://target.example/home">home</a> '
        '<a href="https://target.example/post-a">post</a>'
        '</article></body></html>'
    )
    out = _parse_and_match_html(
        html, "How to Ship",
        ["https://target.example/home", "https://target.example/post-a"],
    )
    assert out is None


def test_parse_match_title_in_og_title_only():
    """og:title is sufficient even when <h1> doesn't carry the title."""
    html = (
        '<html><head>'
        '<meta property="og:title" content="Critical Phrase X">'
        '<title>Site Tagline</title>'
        '</head><body><article><p>body</p>'
        '<a href="https://t.example/">t</a></article></body></html>'
    )
    out = _parse_and_match_html(html, "Critical Phrase X", ["https://t.example/"])
    assert out is None


def test_parse_match_title_missing_when_only_in_sidebar():
    """Founding-incident defense: title in <aside> doesn't satisfy."""
    html = (
        '<html><head><title>Site Tagline</title></head><body>'
        '<aside><h2>Critical Phrase In Sidebar</h2></aside>'
        '<article><p>unrelated content</p>'
        '<a href="https://t.example/">t</a></article></body></html>'
    )
    out = _parse_and_match_html(html, "Critical Phrase", ["https://t.example/"])
    assert out == "title_missing"


def test_parse_match_target_link_missing_when_only_in_script_blob():
    """Founding-incident defense: href in <script> doesn't satisfy."""
    html = (
        '<html><body><article>'
        '<h1>Headline</h1>'
        '<script>{"href":"https://target.example/post-a"}</script>'
        '<p>body</p></article></body></html>'
    )
    out = _parse_and_match_html(html, "Headline", ["https://target.example/post-a"])
    assert out == "target_link_missing: https://target.example/post-a"


def test_parse_match_names_first_missing_href():
    html = (
        '<html><body><article><h1>Headline</h1>'
        '<a href="https://a.example/">a</a></article></body></html>'
    )
    out = _parse_and_match_html(
        html, "Headline",
        ["https://a.example/", "https://b.example/", "https://c.example/"],
    )
    assert out == "target_link_missing: https://b.example/"


def test_parse_match_empty_expected_hrefs_only_checks_title():
    html = "<html><body><article><h1>Only Title</h1></article></body></html>"
    out = _parse_and_match_html(html, "only title", [])
    assert out is None


def test_parse_match_empty_title_skips_title_check():
    html = '<html><body><article><a href="https://t.example/">t</a></article></body></html>'
    out = _parse_and_match_html(html, "", ["https://t.example/"])
    assert out is None


def test_parse_match_case_insensitive_title():
    html = '<html><body><article><h1>Mixed CASE Title Here</h1><a href="https://t.example/">t</a></article></body></html>'
    out = _parse_and_match_html(html, "case title", ["https://t.example/"])
    assert out is None


# ---------- _verify_html_channel orchestration ----------


def _medium_metadata():
    return {
        "channel": "html",
        "allowed_hosts": ("medium.com", "*.medium.com"),
        "allowed_path_patterns": (r"^/@[^/]+/[\w\-]+", r"^/p/[\w]+"),
        "args": lambda row, result: {"url": result.published_url},
    }


def _row(title: str = "How to Ship",
         target_url: str = "https://target.example/post-a",
         main_domain: str = "https://target.example/") -> dict:
    return {
        "id": "row-1",
        "title": title,
        "links": [
            {"url": main_domain, "kind": "main_domain"},
            {"url": target_url, "kind": "target"},
            {"url": "https://supporting.example/", "kind": "supporting"},
        ],
    }


def _medium_result(url: str = "https://medium.com/@u/my-post-abc") -> AdapterResult:
    return AdapterResult(
        status="published",
        adapter="medium-api",
        platform="medium",
        published_url=url,
    )


@pytest.fixture(autouse=True)
def _no_sleep():
    """Disable retry sleeps so tests don't take 30s each."""
    with patch("backlink_publisher.verifier.time.sleep") as m:
        yield m


@pytest.fixture
def _public_dns():
    """Pretend every host resolves to a public IP — keeps SSRF check out of the way."""
    with patch("backlink_publisher.verifier.socket.getaddrinfo",
               return_value=_make_addrinfo("93.184.216.34")) as m:
        yield m


def _fake_html(title: str = "How to Ship Safely",
               target_url: str = "https://target.example/post-a") -> bytes:
    return (
        f'<html><head><title>{title}</title></head>'
        f'<body><article>'
        f'<h1>{title}</h1>'
        f'<a href="https://target.example/">home</a> '
        f'<a href="{target_url}">post</a>'
        f'</article></body></html>'
    ).encode("utf-8")


def test_html_happy_path_returns_verified_true(_public_dns):
    with patch("backlink_publisher.verifier._fetch_html_once",
               return_value=(200, _fake_html(), "https://medium.com/@u/my-post-abc")):
        out = verify_published(_row(), _medium_result(), service=None)
    assert out.verified is True
    assert out.verification_error is None
    assert out.verified_at is not None


def test_html_host_not_allowed_short_circuits_before_fetch(_public_dns):
    result = _medium_result(url="https://attacker.example/echo")
    with patch("backlink_publisher.verifier._fetch_html_once") as fetch:
        out = verify_published(_row(), result)
    assert out.verified is False
    assert out.verification_error.startswith("host_not_allowed:")
    fetch.assert_not_called()


def test_html_label_smuggle_host_rejected(_public_dns):
    result = _medium_result(url="https://attacker.medium.com.evil.com/x")
    with patch("backlink_publisher.verifier._fetch_html_once") as fetch:
        out = verify_published(_row(), result)
    assert out.verified is False
    assert "host_not_allowed" in out.verification_error
    fetch.assert_not_called()


def test_html_invalid_scheme_rejected(_public_dns):
    result = _medium_result(url="file:///etc/passwd")
    out = verify_published(_row(), result)
    assert out.verified is False
    assert "invalid_scheme" in out.verification_error


def test_html_private_ip_rejected_before_fetch():
    """SSRF defense: medium.com resolving to 127.0.0.1 is rejected pre-flight."""
    with patch("backlink_publisher.verifier.socket.getaddrinfo",
               return_value=_make_addrinfo("127.0.0.1")):
        with patch("backlink_publisher.verifier._fetch_html_once") as fetch:
            out = verify_published(_row(), _medium_result())
    assert out.verified is False
    assert "127.0.0.1" in out.verification_error
    fetch.assert_not_called()


def test_html_cloud_metadata_ip_rejected_before_fetch():
    with patch("backlink_publisher.verifier.socket.getaddrinfo",
               return_value=_make_addrinfo("169.254.169.254")):
        with patch("backlink_publisher.verifier._fetch_html_once") as fetch:
            out = verify_published(_row(), _medium_result())
    assert out.verified is False
    assert "169.254.169.254" in out.verification_error
    fetch.assert_not_called()


def test_html_404_definitive_marks_verified_false(_public_dns):
    """The founding-incident defense: a fabricated medium URL returns 404."""
    err = HTTPError(url="https://medium.com/@u/x", code=404, msg="Not Found",
                    hdrs=None, fp=None)
    with patch("backlink_publisher.verifier._fetch_html_once", side_effect=err):
        out = verify_published(_row(), _medium_result())
    assert out.verified is False
    assert out.verification_error == "http_404"


def test_html_410_definitive_marks_verified_false(_public_dns):
    err = HTTPError(url="x", code=410, msg="Gone", hdrs=None, fp=None)
    with patch("backlink_publisher.verifier._fetch_html_once", side_effect=err):
        out = verify_published(_row(), _medium_result())
    assert out.verified is False
    assert out.verification_error == "http_410"


def test_html_451_definitive_marks_verified_false(_public_dns):
    err = HTTPError(url="x", code=451, msg="Unavailable For Legal Reasons",
                    hdrs=None, fp=None)
    with patch("backlink_publisher.verifier._fetch_html_once", side_effect=err):
        out = verify_published(_row(), _medium_result())
    assert out.verified is False
    assert out.verification_error == "http_451"


def test_html_503_transient_exhausted_marks_verified_null(_public_dns):
    """5xx is transient — retried, then mapped to null on exhaustion."""
    err = HTTPError(url="x", code=503, msg="Service Unavailable", hdrs=None, fp=None)
    with patch("backlink_publisher.verifier._fetch_html_once", side_effect=err) as fetch:
        out = verify_published(_row(), _medium_result())
    assert out.verified is None
    assert out.verification_error == "http_503"
    assert fetch.call_count == len((0, 5, 10, 15))  # 4 attempts


def test_html_timeout_transient_marks_verified_null(_public_dns):
    with patch("backlink_publisher.verifier._fetch_html_once",
               side_effect=TimeoutError("read timed out")) as fetch:
        out = verify_published(_row(), _medium_result())
    assert out.verified is None
    assert out.verification_error.startswith("transient:")
    assert "TimeoutError" in out.verification_error
    assert fetch.call_count == 4


def test_html_url_error_transient(_public_dns):
    err = URLError(reason=ConnectionRefusedError("refused"))
    with patch("backlink_publisher.verifier._fetch_html_once", side_effect=err):
        out = verify_published(_row(), _medium_result())
    assert out.verified is None
    assert out.verification_error.startswith("transient:")


def test_html_empty_body_after_all_retries_marks_null(_public_dns):
    with patch("backlink_publisher.verifier._fetch_html_once",
               return_value=(200, b"", "https://medium.com/@u/my-post-abc")) as fetch:
        out = verify_published(_row(), _medium_result())
    assert out.verified is None
    assert out.verification_error == "empty_body"
    assert fetch.call_count == 4


def test_html_body_too_large_marks_null(_public_dns):
    with patch("backlink_publisher.verifier._fetch_html_once",
               side_effect=_BodyTooLarge()):
        out = verify_published(_row(), _medium_result())
    assert out.verified is None
    assert out.verification_error == "body_too_large"


def test_html_redirect_to_disallowed_host_marks_false(_public_dns):
    rej = _RedirectRejected("host_not_allowed: attacker.example")
    with patch("backlink_publisher.verifier._fetch_html_once", side_effect=rej):
        out = verify_published(_row(), _medium_result())
    assert out.verified is False
    assert "host_not_allowed" in out.verification_error
    assert "attacker.example" in out.verification_error


def test_html_redirect_cap_exceeded_marks_false(_public_dns):
    rej = _RedirectRejected("redirect_cap_exceeded")
    with patch("backlink_publisher.verifier._fetch_html_once", side_effect=rej):
        out = verify_published(_row(), _medium_result())
    assert out.verified is False
    assert out.verification_error == "redirect_cap_exceeded"


def test_html_final_url_off_platform_rejected(_public_dns):
    """Redirect chain to attacker.example: final-URL host check catches it."""
    with patch("backlink_publisher.verifier._fetch_html_once",
               return_value=(200, _fake_html(), "https://attacker.example/echo")):
        out = verify_published(_row(), _medium_result())
    assert out.verified is False
    assert "host_not_allowed" in out.verification_error


def test_html_final_url_homepage_rejected_by_path_shape(_public_dns):
    """Redirect to medium.com/ — host passes, path shape rejects."""
    with patch("backlink_publisher.verifier._fetch_html_once",
               return_value=(200, _fake_html(), "https://medium.com/")):
        out = verify_published(_row(), _medium_result())
    assert out.verified is False
    assert out.verification_error.startswith("non_article_url:")


def test_html_final_url_tag_listing_rejected_by_path_shape(_public_dns):
    """Redirect to medium.com/tag/python — non-article URL."""
    with patch("backlink_publisher.verifier._fetch_html_once",
               return_value=(200, _fake_html(), "https://medium.com/tag/python")):
        out = verify_published(_row(), _medium_result())
    assert out.verified is False
    assert "non_article_url" in out.verification_error
    assert "/tag/python" in out.verification_error


def test_html_target_link_missing_marks_false(_public_dns):
    """Title matches, but one target href is stripped from <a>."""
    body = (
        '<html><head><title>How to Ship Safely</title></head>'
        '<body><article><h1>How to Ship Safely</h1>'
        '<a href="https://target.example/">home</a>'
        '</article></body></html>'
    ).encode("utf-8")
    with patch("backlink_publisher.verifier._fetch_html_once",
               return_value=(200, body, "https://medium.com/@u/my-post-abc")):
        out = verify_published(_row(), _medium_result())
    assert out.verified is False
    assert out.verification_error.startswith("target_link_missing:")


def test_html_title_in_sidebar_rejected(_public_dns):
    """Title only in <aside> — sidebar-false-positive guard fires."""
    body = (
        '<html><head><title>Site Tagline</title></head><body>'
        '<aside><h2>How to Ship Safely</h2></aside>'
        '<article><p>other content</p>'
        '<a href="https://target.example/">home</a>'
        '<a href="https://target.example/post-a">post</a>'
        '</article></body></html>'
    ).encode("utf-8")
    with patch("backlink_publisher.verifier._fetch_html_once",
               return_value=(200, body, "https://medium.com/@u/my-post-abc")):
        out = verify_published(_row(), _medium_result())
    assert out.verified is False
    assert out.verification_error == "title_missing"


def test_html_retry_recovers_on_second_attempt(_public_dns):
    """503 on attempt 1, success on attempt 2 → verified=true."""
    err = HTTPError(url="x", code=503, msg="Service Unavailable", hdrs=None, fp=None)
    ok = (200, _fake_html(), "https://medium.com/@u/my-post-abc")
    with patch("backlink_publisher.verifier._fetch_html_once",
               side_effect=[err, ok]) as fetch:
        out = verify_published(_row(), _medium_result())
    assert out.verified is True
    assert fetch.call_count == 2


def test_html_dns_failure_falls_through_to_retry_and_marks_null(_public_dns):
    """DNS failure at SSRF pre-flight does NOT short-circuit to verified=false.

    The retry loop still runs; if every attempt hits transient errors we
    end up at verified=null (correct: don't punish operators for momentary
    DNS hiccups)."""
    # First override the public_dns fixture: pretend DNS fails.
    err = URLError(reason=socket.gaierror(-2, "Name or service not known"))
    with patch("backlink_publisher.verifier.socket.getaddrinfo",
               side_effect=socket.gaierror(-2, "Name or service not known")):
        with patch("backlink_publisher.verifier._fetch_html_once", side_effect=err):
            out = verify_published(_row(), _medium_result())
    assert out.verified is None
    assert out.verification_error.startswith("transient:") or out.verification_error.startswith("http_")
