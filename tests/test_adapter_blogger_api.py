"""Tests for BloggerAPIAdapter."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch, PropertyMock

import pytest

from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.adapters.blogger_api import BloggerAPIAdapter, _near_expiry
from backlink_publisher.config import Config, BloggerOAuthConfig
from backlink_publisher._util.errors import AuthExpiredError, DependencyError, ExternalServiceError

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


def make_mock_service(url="https://myblog.blogspot.com/2026/05/post.html"):
    mock_service = MagicMock()
    mock_service.posts.return_value.insert.return_value.execute.return_value = {
        "url": url,
        "id": "12345",
    }
    return mock_service


@patch("backlink_publisher.publishing.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_draft_mode_returns_draft_url(mock_build, mock_creds):
    mock_build.return_value = make_mock_service()
    adapter = BloggerAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG)

    assert result.status == "drafted"
    assert result.draft_url == "https://myblog.blogspot.com/2026/05/post.html"
    assert result.published_url == ""
    assert result.adapter == "blogger-api"


@patch("backlink_publisher.publishing.adapters.blogger_api._build_credentials")
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


@patch("backlink_publisher.publishing.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_http_401_raises_auth_expired_error(mock_build, mock_creds):
    """Plan 2026-05-19-001 Unit 6: HTTP 401 → AuthExpiredError."""
    from googleapiclient.errors import HttpError
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.status = 401
    exc = HttpError(resp=resp, content=b"Unauthorized")

    mock_service = MagicMock()
    mock_service.posts.return_value.insert.return_value.execute.side_effect = exc
    mock_build.return_value = mock_service

    adapter = BloggerAPIAdapter()
    with pytest.raises(AuthExpiredError) as exc_info:
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    assert exc_info.value.channel == "blogger"
    assert "Blogger HTTP 401" in (exc_info.value.reason or "")
    assert isinstance(exc_info.value, DependencyError)


@patch("backlink_publisher.publishing.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_http_403_raises_auth_expired_error(mock_build, mock_creds):
    """Plan 2026-05-19-001 Unit 6: HTTP 403 (token revoked) → AuthExpiredError."""
    from googleapiclient.errors import HttpError
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.status = 403
    exc = HttpError(resp=resp, content=b"Forbidden")

    mock_service = MagicMock()
    mock_service.posts.return_value.insert.return_value.execute.side_effect = exc
    mock_build.return_value = mock_service

    adapter = BloggerAPIAdapter()
    with pytest.raises(AuthExpiredError) as exc_info:
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    assert exc_info.value.channel == "blogger"
    assert "Blogger HTTP 403" in (exc_info.value.reason or "")


@patch("backlink_publisher.publishing.adapters.blogger_api._build_credentials")
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


@patch("backlink_publisher.publishing.adapters.retry.time.sleep")
@patch("backlink_publisher.publishing.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_429_retried_and_recovers(mock_build, mock_creds, mock_sleep):
    """HTTP 429 on first attempt triggers retry; success on second returns result."""
    from googleapiclient.errors import HttpError
    resp_429 = MagicMock()
    resp_429.status = 429

    mock_service = MagicMock()
    execute = mock_service.posts.return_value.insert.return_value.execute
    execute.side_effect = [HttpError(resp=resp_429, content=b"rate limited"), {"url": "https://myblog.blogspot.com/post"}]
    mock_build.return_value = mock_service

    adapter = BloggerAPIAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    assert result.status == "drafted"
    mock_sleep.assert_called_once()


@patch("backlink_publisher.publishing.adapters.retry.time.sleep")
@patch("backlink_publisher.publishing.adapters.blogger_api._build_credentials")
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


@patch("backlink_publisher.publishing.adapters.retry.time.sleep")
@patch("backlink_publisher.publishing.adapters.blogger_api._build_credentials")
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


@patch("backlink_publisher.publishing.adapters.retry.time.sleep")
@patch("backlink_publisher.publishing.adapters.blogger_api._build_credentials")
@patch("googleapiclient.discovery.build")
def test_401_not_retried(mock_build, mock_creds, mock_sleep):
    """Plan 2026-05-19-001 Unit 6: HTTP 401 is non-retryable —
    AuthExpiredError immediately, no sleep."""
    from googleapiclient.errors import HttpError
    resp_401 = MagicMock()
    resp_401.status = 401
    exc = HttpError(resp=resp_401, content=b"unauthorized")

    mock_service = MagicMock()
    mock_service.posts.return_value.insert.return_value.execute.side_effect = exc
    mock_build.return_value = mock_service

    adapter = BloggerAPIAdapter()
    with pytest.raises(AuthExpiredError):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    mock_sleep.assert_not_called()


@patch("backlink_publisher.publishing.adapters.blogger_api._build_credentials")
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


# ---------------------------------------------------------------------------
# _near_expiry unit tests
# ---------------------------------------------------------------------------

def _make_creds(expiry=None, expired=False, refresh_token="tok"):
    creds = MagicMock()
    creds.expired = expired
    creds.expiry = expiry
    creds.refresh_token = refresh_token
    return creds


def _now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_near_expiry_false_when_600s_future():
    creds = _make_creds(expiry=_now_naive() + timedelta(seconds=600))
    assert _near_expiry(creds, 300) is False


def test_near_expiry_true_when_200s_future():
    creds = _make_creds(expiry=_now_naive() + timedelta(seconds=200))
    assert _near_expiry(creds, 300) is True


def test_near_expiry_true_at_exactly_300s_boundary():
    # 300s remaining is inclusive — should trigger refresh
    creds = _make_creds(expiry=_now_naive() + timedelta(seconds=300))
    assert _near_expiry(creds, 300) is True


def test_near_expiry_false_at_301s():
    creds = _make_creds(expiry=_now_naive() + timedelta(seconds=301))
    assert _near_expiry(creds, 300) is False


def test_near_expiry_true_via_arithmetic_when_expiry_in_past():
    # creds.expired is False but expiry datetime is in the past → arithmetic path fires
    creds = _make_creds(expiry=_now_naive() - timedelta(seconds=30), expired=False)
    assert _near_expiry(creds, 300) is True


def test_near_expiry_true_via_expired_property_shortcircuit():
    # creds.expired=True triggers immediately regardless of expiry datetime
    creds = _make_creds(expired=True)
    assert _near_expiry(creds, 300) is True


def test_near_expiry_false_when_expiry_is_none():
    creds = _make_creds(expiry=None, expired=False)
    assert _near_expiry(creds, 300) is False


# ---------------------------------------------------------------------------
# _build_credentials pre-flight refresh tests
# ---------------------------------------------------------------------------

@patch("backlink_publisher.publishing.adapters.blogger_api.save_blogger_token")
@patch("backlink_publisher.publishing.adapters.blogger_api.load_blogger_token")
def test_build_credentials_refreshes_near_expiry_token(mock_load, mock_save):
    """Token expiring in 200s triggers pre-flight refresh; token persisted to disk."""
    from backlink_publisher.publishing.adapters.blogger_api import _build_credentials

    mock_creds = _make_creds(
        expiry=_now_naive() + timedelta(seconds=200),
        expired=False,
        refresh_token="refresh-tok",
    )
    mock_creds.token = "new-token"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.client_id = "cid"
    mock_creds.client_secret = "csecret"
    mock_creds.scopes = ["https://www.googleapis.com/auth/blogger"]
    mock_load.return_value = {"token": "old"}

    with patch("google.oauth2.credentials.Credentials.from_authorized_user_info", return_value=mock_creds):
        with patch("google.auth.transport.requests.Request"):
            result = _build_credentials(CONFIG)

    mock_creds.refresh.assert_called_once()
    mock_save.assert_called_once()
    assert result is mock_creds


@patch("backlink_publisher.publishing.adapters.blogger_api.save_blogger_token")
@patch("backlink_publisher.publishing.adapters.blogger_api.load_blogger_token")
def test_build_credentials_does_not_refresh_healthy_token(mock_load, mock_save):
    """Token with 600s remaining is returned as-is; no refresh call made."""
    from backlink_publisher.publishing.adapters.blogger_api import _build_credentials

    mock_creds = _make_creds(
        expiry=_now_naive() + timedelta(seconds=600),
        expired=False,
        refresh_token="refresh-tok",
    )
    mock_creds.valid = True
    mock_load.return_value = {"token": "healthy"}

    with patch("google.oauth2.credentials.Credentials.from_authorized_user_info", return_value=mock_creds):
        result = _build_credentials(CONFIG)

    mock_creds.refresh.assert_not_called()
    mock_save.assert_not_called()
    assert result is mock_creds


@patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_config")
@patch("backlink_publisher.publishing.adapters.blogger_api.save_blogger_token")
@patch("backlink_publisher.publishing.adapters.blogger_api.load_blogger_token")
def test_build_credentials_falls_to_reauth_when_preflight_refresh_fails(
    mock_load, mock_save, mock_flow_factory
):
    """refresh() failure sets creds=None and triggers full re-auth (R3)."""
    from backlink_publisher.publishing.adapters.blogger_api import _build_credentials

    mock_creds = _make_creds(
        expiry=_now_naive() + timedelta(seconds=100),
        expired=False,
        refresh_token="refresh-tok",
    )
    mock_creds.refresh.side_effect = Exception("network error")
    mock_load.return_value = {"token": "old"}

    new_creds = MagicMock()
    new_creds.token = "new-token"
    new_creds.refresh_token = "new-refresh"
    new_creds.token_uri = "https://oauth2.googleapis.com/token"
    new_creds.client_id = "cid"
    new_creds.client_secret = "csecret"
    new_creds.scopes = ["https://www.googleapis.com/auth/blogger"]
    mock_flow = MagicMock()
    mock_flow.run_local_server.return_value = new_creds
    mock_flow_factory.return_value = mock_flow

    with patch("google.oauth2.credentials.Credentials.from_authorized_user_info", return_value=mock_creds):
        with patch("google.auth.transport.requests.Request"):
            result = _build_credentials(CONFIG)

    # Re-auth flow was triggered (not stale creds returned)
    mock_flow.run_local_server.assert_called_once()
    assert result is new_creds
