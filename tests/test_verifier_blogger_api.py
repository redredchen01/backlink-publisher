"""Tests for the Blogger API verification channel.

Unit 3 of the real-publish-verification plan: _verify_blogger_api fetches
the just-published post via service.posts().get(blogId, postId), then
matches title and the verified-link subset against the structured response.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backlink_publisher.adapters.base import AdapterResult
from backlink_publisher.verifier import (
    _extract_hrefs,
    _verified_link_subset,
    verify_published,
)


# ---------- helpers ----------

def _row(title: str = "How to ship safely",
         target_urls: list[str] | None = None,
         main_domain_url: str = "https://target.example/") -> dict:
    """Construct a minimal pipeline row with `links`."""
    links: list[dict] = [{"url": main_domain_url, "anchor": "Home",
                          "kind": "main_domain", "required": True}]
    for url in target_urls or []:
        links.append({"url": url, "anchor": "Read more",
                      "kind": "target", "required": True})
    # Add a non-verifiable supporting link to verify subset filtering
    links.append({"url": "https://wikipedia.org/wiki/X",
                  "anchor": "context", "kind": "supporting", "required": False})
    return {"id": "row-1", "title": title, "links": links}


def _result(post_id: str = "POST_1", blog_id: str = "BLOG_1",
            url: str = "https://example.blogspot.com/2026/05/x.html") -> AdapterResult:
    r = AdapterResult(
        status="published",
        adapter="blogger-api",
        platform="blogger",
        published_url=url,
    )
    r._provider_meta = {"post_id": post_id, "blog_id": blog_id}
    return r


def _mock_service(*, title: str, content_html: str,
                  raise_exc: Exception | None = None) -> MagicMock:
    svc = MagicMock()
    if raise_exc is not None:
        svc.posts.return_value.get.return_value.execute.side_effect = raise_exc
    else:
        svc.posts.return_value.get.return_value.execute.return_value = {
            "title": title,
            "content": content_html,
        }
    return svc


def _http_error(status: int) -> Exception:
    """Build a googleapiclient HttpError with the given status."""
    from googleapiclient.errors import HttpError
    resp = MagicMock()
    resp.status = status
    return HttpError(resp=resp, content=b"")


# ---------- href extraction helpers ----------

def test_extract_hrefs_simple_anchors():
    html = '<p>See <a href="https://a.example/">A</a> and <a href="https://b.example">B</a></p>'
    assert _extract_hrefs(html) == {"https://a.example/", "https://b.example"}


def test_extract_hrefs_ignores_non_anchor_href_attributes():
    """href on <link rel="..."> and similar must not count as verified links."""
    html = '<link href="https://feed.example/atom" rel="alternate"><a href="https://real.example/">R</a>'
    assert _extract_hrefs(html) == {"https://real.example/"}


def test_extract_hrefs_handles_malformed_html_without_crashing():
    """html.parser is lenient; very malformed input returns whatever was salvaged."""
    html = '<a href="https://ok.example/">unclosed <a href="https://also.example/x">'
    out = _extract_hrefs(html)
    assert "https://ok.example/" in out
    assert "https://also.example/x" in out


def test_extract_hrefs_empty_input():
    assert _extract_hrefs("") == set()
    assert _extract_hrefs(None) == set()  # type: ignore[arg-type]


def test_extract_hrefs_case_insensitive_tag():
    html = '<A HREF="https://upper.example/">U</A>'
    assert _extract_hrefs(html) == {"https://upper.example/"}


# ---------- verified link subset ----------

def test_verified_link_subset_filters_to_target_and_main_domain():
    row = _row(target_urls=["https://t1.example/", "https://t2.example/"])
    subset = _verified_link_subset(row)
    assert "https://target.example/" in subset  # main_domain kind
    assert "https://t1.example/" in subset
    assert "https://t2.example/" in subset
    assert "https://wikipedia.org/wiki/X" not in subset  # supporting kind excluded


def test_verified_link_subset_empty_when_no_links():
    assert _verified_link_subset({"links": []}) == []
    assert _verified_link_subset({}) == []


def test_verified_link_subset_tolerates_malformed_link_entries():
    """Robust to garbage in row['links'] — return only the well-formed verifiable ones."""
    row = {"links": [
        {"url": "https://good.example/", "kind": "target"},
        None,
        "not a dict",
        {"kind": "target"},  # missing url
        {"url": "", "kind": "target"},  # empty url
        {"url": "https://also.example/", "kind": "extra"},  # excluded by kind
    ]}
    assert _verified_link_subset(row) == ["https://good.example/"]


# ---------- happy path ----------

def test_happy_path_title_and_links_present_returns_verified_true():
    row = _row(title="How to ship safely",
               target_urls=["https://t1.example/post-a"])
    result = _result()
    svc = _mock_service(
        title="How to Ship Safely: A Practical Guide",  # case-insensitive substring match
        content_html=(
            '<p>Intro</p>'
            '<a href="https://target.example/">target home</a> '
            '<a href="https://t1.example/post-a">link</a>'
        ),
    )

    outcome = verify_published(row, result, service=svc)

    assert outcome.verified is True
    assert outcome.verification_error is None
    assert outcome.verified_at is not None and outcome.verified_at.endswith("+00:00")
    svc.posts.return_value.get.assert_called_once_with(blogId="BLOG_1", postId="POST_1")


def test_empty_link_subset_only_checks_title():
    row = {"id": "row-2", "title": "Solo title", "links": []}
    result = _result()
    svc = _mock_service(title="Solo title", content_html="<p>nothing</p>")

    outcome = verify_published(row, result, service=svc)
    assert outcome.verified is True


# ---------- structured-failure paths ----------

def test_title_missing_marks_verified_false():
    row = _row(title="Critical phrase that must appear")
    result = _result()
    svc = _mock_service(title="A completely different headline",
                        content_html='<a href="https://target.example/">t</a>')

    outcome = verify_published(row, result, service=svc)
    assert outcome.verified is False
    assert outcome.verification_error == "title_missing"


def test_missing_target_link_names_first_missing_href():
    row = _row(title="Headline",
               target_urls=["https://present.example/", "https://absent.example/"])
    result = _result()
    svc = _mock_service(
        title="Headline of the article",
        content_html=(
            '<a href="https://target.example/">m</a>'
            '<a href="https://present.example/">p</a>'
            # https://absent.example/ deliberately omitted
        ),
    )

    outcome = verify_published(row, result, service=svc)
    assert outcome.verified is False
    assert outcome.verification_error == "target_link_missing: https://absent.example/"


def test_missing_main_domain_link_also_caught():
    row = _row(title="Headline", target_urls=["https://t.example/"])
    result = _result()
    svc = _mock_service(
        title="Headline",
        content_html='<a href="https://t.example/">t</a>',  # main_domain target omitted
    )
    outcome = verify_published(row, result, service=svc)
    assert outcome.verified is False
    assert outcome.verification_error.startswith("target_link_missing:")


# ---------- HTTP error mapping ----------

def test_http_404_definitive_marks_verified_false():
    """The founding-incident defense — a fabricated postId returns 404."""
    row = _row()
    result = _result()
    svc = _mock_service(title="", content_html="", raise_exc=_http_error(404))

    outcome = verify_published(row, result, service=svc)
    assert outcome.verified is False
    assert outcome.verification_error == "http_404"


def test_http_410_gone_marks_verified_false():
    svc = _mock_service(title="", content_html="", raise_exc=_http_error(410))
    outcome = verify_published(_row(), _result(), service=svc)
    assert outcome.verified is False
    assert outcome.verification_error == "http_410"


def test_http_403_blocked_marks_verified_false():
    """Any definitive 4xx (not just the canonical 404/410/451) → verified=false."""
    svc = _mock_service(title="", content_html="", raise_exc=_http_error(403))
    outcome = verify_published(_row(), _result(), service=svc)
    assert outcome.verified is False
    assert outcome.verification_error == "http_403"


def test_http_500_transient_marks_verified_null():
    svc = _mock_service(title="", content_html="", raise_exc=_http_error(500))
    outcome = verify_published(_row(), _result(), service=svc)
    assert outcome.verified is None
    assert outcome.verification_error == "http_500"


def test_http_503_transient_marks_verified_null():
    svc = _mock_service(title="", content_html="", raise_exc=_http_error(503))
    outcome = verify_published(_row(), _result(), service=svc)
    assert outcome.verified is None
    assert outcome.verification_error == "http_503"


def test_timeout_transient_marks_verified_null():
    """Transport errors map to verified=null with transient: prefix."""
    svc = _mock_service(title="", content_html="", raise_exc=TimeoutError("read timed out"))
    outcome = verify_published(_row(), _result(), service=svc)
    assert outcome.verified is None
    assert outcome.verification_error.startswith("transient:")
    assert "TimeoutError" in outcome.verification_error


def test_connection_error_transient_marks_verified_null():
    svc = _mock_service(title="", content_html="",
                        raise_exc=ConnectionError("DNS lookup failed"))
    outcome = verify_published(_row(), _result(), service=svc)
    assert outcome.verified is None
    assert "ConnectionError" in outcome.verification_error


# ---------- service / provider-meta protection ----------

def test_missing_service_records_verifier_internal_error():
    """The dispatcher (Unit 6) must always supply a service for Blogger rows."""
    outcome = verify_published(_row(), _result(), service=None)
    assert outcome.verified is None
    assert outcome.verification_error.startswith("verifier_internal_error:")
    assert "service_not_provided" in outcome.verification_error


def test_missing_provider_meta_post_id_marks_null_with_clear_reason():
    row = _row()
    result = AdapterResult(
        status="published", adapter="blogger-api", platform="blogger",
        published_url="https://example.blogspot.com/2026/05/x.html",
    )
    # _provider_meta empty — no blog_id/post_id captured during publish
    svc = _mock_service(title="should not be fetched", content_html="")

    outcome = verify_published(row, result, service=svc)
    assert outcome.verified is None
    assert outcome.verification_error == "missing_provider_meta"
    svc.posts.return_value.get.assert_not_called()


def test_missing_provider_meta_only_blog_id_marks_null():
    row = _row()
    result = AdapterResult(
        status="published", adapter="blogger-api", platform="blogger",
        published_url="https://example.blogspot.com/2026/05/x.html",
    )
    result._provider_meta = {"blog_id": "B1"}  # post_id missing
    svc = _mock_service(title="x", content_html="")
    outcome = verify_published(row, result, service=svc)
    assert outcome.verified is None
    assert outcome.verification_error == "missing_provider_meta"


# ---------- title matching semantics ----------

def test_title_match_is_case_insensitive_substring():
    """Expected title appearing as a case-insensitive substring satisfies R6a."""
    row = _row(title="how to ship")
    result = _result()
    svc = _mock_service(
        title="A Complete Guide: How To Ship Code Safely",
        content_html='<a href="https://target.example/">m</a>',
    )
    outcome = verify_published(row, result, service=svc)
    assert outcome.verified is True


def test_empty_title_in_row_skips_title_check():
    """Row without a title triggers no title-mismatch."""
    row = _row()
    row["title"] = ""
    result = _result()
    svc = _mock_service(title="anything",
                        content_html='<a href="https://target.example/">m</a>')
    outcome = verify_published(row, result, service=svc)
    assert outcome.verified is True


# ---------- single-attempt policy (no retry) ----------

def test_blogger_api_uses_single_attempt_no_retry():
    """R10: API channel is single-attempt (read-after-write consistency)."""
    row = _row()
    result = _result()
    svc = _mock_service(title="", content_html="", raise_exc=_http_error(503))

    verify_published(row, result, service=svc)
    # exactly one execute() call — no retry budget
    assert svc.posts.return_value.get.return_value.execute.call_count == 1
