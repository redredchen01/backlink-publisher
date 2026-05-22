"""Cross-adapter ``seo.canonical_url`` forwarding contract.

Plan 2026-05-21-003 Unit 3. Locks the invariant that every adapter wired
in Unit 2 emits the canonical URL **verbatim** into its outbound payload
when one is provided, and emits **nothing canonical-shaped** when one is
not provided (defense against default-on regressions that would silently
flip rows out of pure-backlink mode into syndication mode).

Adapters that are structurally incapable of carrying canonical
(``telegraph``, ``velog``) are not tested here — their docstrings note
the platform limitation. They will never be in any positive assertion
here even when new adapters land later, so the contract test does not
need to skip them dynamically.

Forwarder contract: this test deliberately asserts that adapters pass the
URL through *verbatim* (no escaping, no normalization). Defense lives at
the schema layer (``tests/test_schema_seo_canonical_contract.py``); this
suite would catch a future PR that adds adapter-side mangling.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.publishing.adapters.ghpages import _build_markdown_body
from backlink_publisher.publishing.adapters.hashnode import _build_publish_input


_CANONICAL = "https://example.com/article-original"


# --------------------------------------------------------------------------- #
# Shared payload fixtures                                                     #
# --------------------------------------------------------------------------- #


def _payload_with_canonical(canonical: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "row-001",
        "title": "Sample post",
        "content_markdown": "Body with [link](https://example.com).",
        "tags": ["tag1", "tag2"],
        "language": "en",
        "slug": "sample-post",
    }
    if canonical is not None:
        payload["seo"] = {
            "title": "SEO title",
            "description": "SEO description.",
            "canonical_url": canonical,
        }
    return payload


# --------------------------------------------------------------------------- #
# Hashnode — GraphQL ``input.originalArticleURL`` variable                    #
# --------------------------------------------------------------------------- #


class TestHashnodeCanonical:
    def test_with_canonical_emits_original_article_url(self):
        payload = _payload_with_canonical(_CANONICAL)
        variables = _build_publish_input(payload, "pub-id-xyz")
        assert variables.get("originalArticleURL") == _CANONICAL

    def test_without_seo_omits_field(self):
        payload = _payload_with_canonical(None)
        variables = _build_publish_input(payload, "pub-id-xyz")
        assert "originalArticleURL" not in variables

    def test_empty_canonical_omits_field(self):
        payload = _payload_with_canonical("")
        variables = _build_publish_input(payload, "pub-id-xyz")
        assert "originalArticleURL" not in variables


# --------------------------------------------------------------------------- #
# GHPages — Jekyll front-matter ``canonical_url:`` line                       #
# --------------------------------------------------------------------------- #


class TestGhpagesCanonical:
    def test_with_canonical_emits_front_matter_line(self):
        payload = _payload_with_canonical(_CANONICAL)
        rendered = _build_markdown_body(payload)
        assert f'canonical_url: "{_CANONICAL}"' in rendered

    def test_without_seo_omits_line(self):
        payload = _payload_with_canonical(None)
        rendered = _build_markdown_body(payload)
        assert "canonical_url:" not in rendered

    def test_empty_canonical_omits_line(self):
        payload = _payload_with_canonical("")
        rendered = _build_markdown_body(payload)
        assert "canonical_url:" not in rendered


# --------------------------------------------------------------------------- #
# Blogger — HTML body prepended ``<link rel=canonical>``                      #
# --------------------------------------------------------------------------- #


def _capture_blogger_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Capture the body passed to ``service.posts().insert()``.

    Mirrors the mock pattern in
    ``tests/test_adapter_blogger_api_xss_contract.py``.
    """
    from backlink_publisher.config import BloggerOAuthConfig, Config
    from backlink_publisher.publishing.adapters.blogger_api import BloggerAPIAdapter

    config = Config(
        blogger_blog_ids={"https://example.com": "fake-blog-id"},
        blogger_oauth=BloggerOAuthConfig("cid", "csecret"),
    )

    blogger_payload = dict(payload)
    blogger_payload.update(
        {
            "main_domain": "https://example.com",
            "publish_mode": "draft",
            # blogger uses content_html as tier-(a) source; supply both so
            # extract_publish_html prefers content_html.
            "content_html": "<p>Body content</p>",
        }
    )

    captured: dict[str, Any] = {}

    def fake_insert(*, blogId, isDraft, body):
        captured["body"] = body
        insert_call = MagicMock()
        insert_call.execute.return_value = {
            "url": "https://test.blogspot.com/2026/05/post.html",
            "id": "post-001",
        }
        return insert_call

    with patch(
        "backlink_publisher.publishing.adapters.blogger_api._build_credentials"
    ), patch("googleapiclient.discovery.build") as mock_build:
        mock_service = MagicMock()
        mock_service.posts.return_value.insert.side_effect = fake_insert
        mock_build.return_value = mock_service

        adapter = BloggerAPIAdapter()
        adapter.publish(blogger_payload, "draft", config)

    return captured["body"]


class TestBloggerCanonical:
    def test_with_canonical_prepends_link_tag_in_content(self):
        payload = _payload_with_canonical(_CANONICAL)
        body = _capture_blogger_body(payload)
        assert (
            f'<link rel="canonical" href="{_CANONICAL}">' in body["content"]
        )
        # Original content survives.
        assert "Body content" in body["content"]

    def test_without_seo_omits_link_tag(self):
        payload = _payload_with_canonical(None)
        body = _capture_blogger_body(payload)
        assert "canonical" not in body["content"].lower()

    def test_empty_canonical_omits_link_tag(self):
        payload = _payload_with_canonical("")
        body = _capture_blogger_body(payload)
        assert "canonical" not in body["content"].lower()


# --------------------------------------------------------------------------- #
# Forwarder verbatim — defense-in-depth assertion that future adapter         #
# refactors don't add silent escaping (would mask Unit 1 schema gate         #
# failures and create double-escaping bugs).                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url_with_query",
    [
        "https://example.com/post?utm_source=x&utm_medium=y",
        "https://example.com/post#section-2",
        "https://sub.example.com:8443/path/to/post",
    ],
)
class TestForwarderVerbatim:
    """Schema-validated URLs flow through every adapter unchanged."""

    def test_hashnode_forwards_verbatim(self, url_with_query: str):
        variables = _build_publish_input(
            _payload_with_canonical(url_with_query), "pub-id-xyz"
        )
        assert variables["originalArticleURL"] == url_with_query

    def test_ghpages_forwards_verbatim(self, url_with_query: str):
        rendered = _build_markdown_body(_payload_with_canonical(url_with_query))
        # json.dumps wraps in double-quotes; URL itself unchanged.
        assert f'"{url_with_query}"' in rendered


