"""Tests for BloggerAPIAdapter."""

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from backlink_publisher.adapters.base import AdapterResult
from backlink_publisher.adapters.blogger_api import BloggerAPIAdapter
from backlink_publisher.config import Config, BloggerOAuthConfig
from backlink_publisher.errors import DependencyError, ExternalServiceError

PAYLOAD = {
    "id": "abc123",
    "title": "Test Post",
    "content_markdown": "# Hello\n\nWorld.",
    "tags": ["tag1", "tag2"],
    "main_domain": "https://myblog.com/",
    "publish_mode": "draft",
}

CONFIG = Config(
    blogger_blog_ids={"https://myblog.com": "999"},
    blogger_oauth=BloggerOAuthConfig("cid", "csecret"),
)


def make_mock_service(url="https://myblog.blogspot.com/2026/05/post.html",
                      post_id="12345", blog_id="999"):
    mock_service = MagicMock()
    mock_service.posts.return_value.insert.return_value.execute.return_value = {
        "url": url,
        "id": post_id,
        "blog": {"id": blog_id},
    }
    return mock_service


@patch("backlink_publisher.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_draft_mode_returns_draft_url(mock_build, mock_creds):
    mock_build.return_value = make_mock_service()
    adapter = BloggerAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG)

    assert result.status == "drafted"
    assert result.draft_url == "https://myblog.blogspot.com/2026/05/post.html"
    assert result.published_url == ""
    assert result.adapter == "blogger-api"


@patch("backlink_publisher.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_publish_mode_returns_published_url(mock_build, mock_creds):
    mock_build.return_value = make_mock_service()
    adapter = BloggerAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="publish", config=CONFIG)

    assert result.status == "published"
    assert result.published_url == "https://myblog.blogspot.com/2026/05/post.html"
    assert result.draft_url == ""


def test_missing_blog_id_raises_dependency_error():
    adapter = BloggerAPIAdapter()
    cfg = Config(blogger_blog_ids={})
    with pytest.raises(DependencyError, match="https://myblog.com"):
        adapter.publish(PAYLOAD, mode="draft", config=cfg)


@patch("backlink_publisher.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_http_401_raises_external_service_error(mock_build, mock_creds):
    from googleapiclient.errors import HttpError
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.status = 401
    exc = HttpError(resp=resp, content=b"Unauthorized")

    mock_service = MagicMock()
    mock_service.posts.return_value.insert.return_value.execute.side_effect = exc
    mock_build.return_value = mock_service

    adapter = BloggerAPIAdapter()
    with pytest.raises(ExternalServiceError, match="authentication failed"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)


@patch("backlink_publisher.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_http_429_raises_rate_limited(mock_build, mock_creds):
    from googleapiclient.errors import HttpError
    resp = MagicMock()
    resp.status = 429
    exc = HttpError(resp=resp, content=b"Rate limited")

    mock_service = MagicMock()
    mock_service.posts.return_value.insert.return_value.execute.side_effect = exc
    mock_build.return_value = mock_service

    adapter = BloggerAPIAdapter()
    with pytest.raises(ExternalServiceError, match="rate-limited"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_429_retried_and_recovers(mock_build, mock_creds, mock_sleep):
    """HTTP 429 on first attempt triggers retry; success on second returns result."""
    from googleapiclient.errors import HttpError
    resp_429 = MagicMock()
    resp_429.status = 429

    mock_service = MagicMock()
    execute = mock_service.posts.return_value.insert.return_value.execute
    execute.side_effect = [
        HttpError(resp=resp_429, content=b"rate limited"),
        {"url": "https://myblog.blogspot.com/post", "id": "12345", "blog": {"id": "999"}},
    ]
    mock_build.return_value = mock_service

    adapter = BloggerAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    assert result.status == "drafted"
    mock_sleep.assert_called_once()


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_5xx_not_retried(mock_build, mock_creds, mock_sleep):
    """HTTP 503 is NOT retried (no idempotency guarantee from Blogger API)."""
    from googleapiclient.errors import HttpError
    resp_503 = MagicMock()
    resp_503.status = 503

    mock_service = MagicMock()
    execute = mock_service.posts.return_value.insert.return_value.execute
    execute.side_effect = HttpError(resp=resp_503, content=b"server error")
    mock_build.return_value = mock_service

    adapter = BloggerAPIAdapter()
    with pytest.raises(ExternalServiceError, match="503"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    mock_sleep.assert_not_called()


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_429_exhaustion_raises_external_service_error(mock_build, mock_creds, mock_sleep):
    """Three consecutive 429s exhaust retries and raise ExternalServiceError."""
    from googleapiclient.errors import HttpError
    resp_429 = MagicMock()
    resp_429.status = 429
    exc = HttpError(resp=resp_429, content=b"rate limited")

    mock_service = MagicMock()
    mock_service.posts.return_value.insert.return_value.execute.side_effect = exc
    mock_build.return_value = mock_service

    adapter = BloggerAPIAdapter()
    with pytest.raises(ExternalServiceError, match="rate-limited"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    assert mock_sleep.call_count == 2  # 2 retries → 2 sleeps


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_401_not_retried(mock_build, mock_creds, mock_sleep):
    """HTTP 401 is non-retryable — propagates immediately, no sleep."""
    from googleapiclient.errors import HttpError
    resp_401 = MagicMock()
    resp_401.status = 401
    exc = HttpError(resp=resp_401, content=b"unauthorized")

    mock_service = MagicMock()
    mock_service.posts.return_value.insert.return_value.execute.side_effect = exc
    mock_build.return_value = mock_service

    adapter = BloggerAPIAdapter()
    with pytest.raises(ExternalServiceError, match="authentication failed"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    mock_sleep.assert_not_called()


@patch("backlink_publisher.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_publish_captures_provider_meta_from_insert_response(mock_build, mock_creds):
    """Unit 4: postId and blog.id from the insert response land in _provider_meta."""
    mock_build.return_value = make_mock_service(
        post_id="POST_42", blog_id="BLOG_99",
    )
    adapter = BloggerAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="publish", config=CONFIG)

    assert result._provider_meta == {"post_id": "POST_42", "blog_id": "BLOG_99"}


@patch("backlink_publisher.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_publish_provider_meta_does_not_leak_into_jsonl(mock_build, mock_creds):
    """Internal _provider_meta must not surface in to_publish_output."""
    mock_build.return_value = make_mock_service()
    adapter = BloggerAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="publish", config=CONFIG)
    out = result.to_publish_output(PAYLOAD, "2026-05-12T00:00:00+00:00")

    assert "post_id" not in out
    assert "blog_id" not in out
    assert "_provider_meta" not in out


@patch("backlink_publisher.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_publish_tolerates_missing_blog_in_response(mock_build, mock_creds):
    """Malformed insert response (no blog id) must not block publication.
    The verifier surfaces the gap later as missing_provider_meta."""
    mock_service = MagicMock()
    mock_service.posts.return_value.insert.return_value.execute.return_value = {
        "url": "https://myblog.blogspot.com/2026/05/post.html",
        "id": "12345",
        # no "blog" key at all
    }
    mock_build.return_value = mock_service

    adapter = BloggerAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="publish", config=CONFIG)

    assert result.status == "published"
    assert result._provider_meta == {"post_id": "12345"}  # blog_id absent, no crash


@patch("backlink_publisher.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_publish_tolerates_missing_post_id_in_response(mock_build, mock_creds):
    """Same as above but the postId is missing."""
    mock_service = MagicMock()
    mock_service.posts.return_value.insert.return_value.execute.return_value = {
        "url": "https://myblog.blogspot.com/2026/05/post.html",
        "blog": {"id": "999"},
    }
    mock_build.return_value = mock_service

    adapter = BloggerAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="publish", config=CONFIG)
    assert result.status == "published"
    assert result._provider_meta == {"blog_id": "999"}


@patch("backlink_publisher.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_get_service_centralises_credential_and_service_construction(mock_build, mock_creds):
    """Unit 4: both publisher and verifier go through _get_service.

    Asserting that one call to publish invokes _build_credentials exactly
    once AND build() exactly once — there is no second auth path inside
    the publisher itself."""
    mock_creds.return_value = MagicMock(valid=True)
    mock_build.return_value = make_mock_service()

    adapter = BloggerAPIAdapter()
    adapter.publish(PAYLOAD, mode="draft", config=CONFIG)

    assert mock_creds.call_count == 1
    assert mock_build.call_count == 1


@patch("backlink_publisher.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_tags_truncated_to_20(mock_build, mock_creds):
    many_tags = [f"tag{i}" for i in range(30)]
    payload = {**PAYLOAD, "tags": many_tags}

    mock_service = make_mock_service()
    mock_build.return_value = mock_service

    adapter = BloggerAPIAdapter()
    adapter.publish(payload, mode="draft", config=CONFIG)

    call_kwargs = mock_service.posts.return_value.insert.call_args[1]
    assert len(call_kwargs["body"]["labels"]) == 20
