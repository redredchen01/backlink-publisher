"""Unit 7: Dev.to adapter tests (Plan 003 Phase 2)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters.devto_api import (
    DevtoAPIAdapter,
    _build_article_payload,
    _load_api_key,
    _required_headers,
)
from backlink_publisher.publishing.adapters.base import AdapterResult


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    cfg = MagicMock()
    cfg.devto_token_path = tmp_path / "devto-token.json"
    return cfg


@pytest.fixture
def config_with_token(config, tmp_path):
    token_data = {"api_key": "devto_test_key_abc123", "token_rev": 1}
    config.devto_token_path.write_text(json.dumps(token_data))
    return config


def _mock_devto_success_response(article_id=42, slug="test-article", username="testuser"):
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {
        "id": article_id,
        "slug": slug,
        "url": f"https://dev.to/{username}/{slug}",
        "user": {"username": username},
    }
    resp.text = ""
    return resp


class TestRequiredHeaders:
    def test_uses_api_key_header_not_authorization(self):
        headers = _required_headers("my_key")
        assert "api-key" in headers
        assert headers["api-key"] == "my_key"
        assert "Authorization" not in headers

    def test_includes_content_type(self):
        headers = _required_headers("key")
        assert headers["Content-Type"] == "application/json"


class TestLoadApiKey:
    def test_raises_dependency_error_when_no_file(self, config):
        with pytest.raises(DependencyError, match="API key"):
            _load_api_key(config)

    def test_raises_dependency_error_when_api_key_empty(self, config):
        config.devto_token_path.write_text(json.dumps({"api_key": ""}))
        with pytest.raises(DependencyError, match="API key"):
            _load_api_key(config)

    def test_returns_key_when_present(self, config_with_token):
        key = _load_api_key(config_with_token)
        assert key == "devto_test_key_abc123"


class TestBuildArticlePayload:
    def test_includes_title(self):
        payload = {"title": "My Dev.to Article"}
        result = _build_article_payload(payload)
        assert result["article"]["title"] == "My Dev.to Article"

    def test_canonical_url_included_when_present(self):
        payload = {
            "title": "Test",
            "seo": {"canonical_url": "https://example.com/post"},
        }
        result = _build_article_payload(payload)
        assert result["article"]["canonical_url"] == "https://example.com/post"

    def test_canonical_url_omitted_when_empty(self):
        payload = {"title": "Test", "seo": {"canonical_url": ""}}
        result = _build_article_payload(payload)
        assert "canonical_url" not in result["article"]

    def test_canonical_url_omitted_when_no_seo(self):
        payload = {"title": "Test"}
        result = _build_article_payload(payload)
        assert "canonical_url" not in result["article"]

    def test_published_is_true_by_default(self):
        payload = {"title": "Test"}
        result = _build_article_payload(payload)
        assert result["article"]["published"] is True

    def test_tags_truncated_to_4(self):
        payload = {"title": "Test", "tags": ["a", "b", "c", "d", "e", "f"]}
        result = _build_article_payload(payload)
        assert len(result["article"]["tags"]) == 4

    def test_tags_empty_list_accepted(self):
        payload = {"title": "Test", "tags": []}
        result = _build_article_payload(payload)
        assert result["article"]["tags"] == []

    def test_tags_converted_to_lowercase(self):
        payload = {"title": "Test", "tags": ["Python", "WebDev"]}
        result = _build_article_payload(payload)
        assert result["article"]["tags"] == ["python", "webdev"]

    def test_body_markdown_used_when_present(self):
        payload = {"title": "Test", "content_markdown": "## Hello\nWorld"}
        result = _build_article_payload(payload)
        assert "## Hello" in result["article"]["body_markdown"]


class TestDevtoAPIAdapterAvailable:
    def test_false_when_no_token_file(self, config):
        assert DevtoAPIAdapter.available(config) is False

    def test_false_when_api_key_empty(self, config):
        config.devto_token_path.write_text(json.dumps({"api_key": ""}))
        assert DevtoAPIAdapter.available(config) is False

    def test_true_when_api_key_present(self, config_with_token):
        assert DevtoAPIAdapter.available(config_with_token) is True


class TestDevtoAPIAdapterPublish:
    def test_happy_path_returns_published_result(self, config_with_token):
        adapter = DevtoAPIAdapter()
        mock_resp = _mock_devto_success_response()
        with patch("requests.post", return_value=mock_resp):
            result = adapter.publish(
                {"title": "Test Article", "content": "Body text"},
                mode="live",
                config=config_with_token,
            )
        assert result.status == "published"
        assert result.platform == "devto"
        assert "dev.to" in result.published_url

    def test_canonical_url_in_request_body(self, config_with_token):
        adapter = DevtoAPIAdapter()
        mock_resp = _mock_devto_success_response()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            adapter.publish(
                {
                    "title": "Test",
                    "seo": {"canonical_url": "https://example.com/canonical"},
                },
                mode="live",
                config=config_with_token,
            )
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"]["article"]["canonical_url"] == "https://example.com/canonical"

    def test_no_seo_no_canonical_in_request(self, config_with_token):
        adapter = DevtoAPIAdapter()
        mock_resp = _mock_devto_success_response()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            adapter.publish(
                {"title": "Pure backlink article"},
                mode="live",
                config=config_with_token,
            )
        call_kwargs = mock_post.call_args.kwargs
        assert "canonical_url" not in call_kwargs["json"]["article"]

    def test_draft_mode_no_api_call(self, config_with_token):
        adapter = DevtoAPIAdapter()
        with patch("requests.post") as mock_post:
            result = adapter.publish(
                {"title": "Draft"},
                mode="draft",
                config=config_with_token,
            )
        assert result.status == "drafted"
        assert mock_post.call_count == 0

    def test_401_raises_external_service_error(self, config_with_token):
        adapter = DevtoAPIAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(ExternalServiceError, match="401"):
                adapter.publish(
                    {"title": "Test"},
                    mode="live",
                    config=config_with_token,
                )

    def test_422_raises_external_service_error(self, config_with_token):
        adapter = DevtoAPIAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.json.return_value = {"error": "Title too short"}
        mock_resp.text = ""
        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(ExternalServiceError, match="422"):
                adapter.publish(
                    {"title": "x"},
                    mode="live",
                    config=config_with_token,
                )

    def test_missing_api_key_raises_dependency_error(self, config):
        adapter = DevtoAPIAdapter()
        with pytest.raises(DependencyError):
            adapter.publish(
                {"title": "Test"},
                mode="live",
                config=config,
            )

    def test_fallback_url_construction_from_slug_and_username(self, config_with_token):
        adapter = DevtoAPIAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "id": 99,
            "slug": "my-test-post",
            "url": "",  # empty URL forces fallback
            "user": {"username": "myuser"},
        }
        mock_resp.text = ""
        with patch("requests.post", return_value=mock_resp):
            result = adapter.publish(
                {"title": "Test"},
                mode="live",
                config=config_with_token,
            )
        assert result.published_url == "https://dev.to/myuser/my-test-post"

    def test_api_key_header_used_not_authorization(self, config_with_token):
        adapter = DevtoAPIAdapter()
        mock_resp = _mock_devto_success_response()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            adapter.publish(
                {"title": "Test"},
                mode="live",
                config=config_with_token,
            )
        headers = mock_post.call_args.kwargs["headers"]
        assert "api-key" in headers
        assert "Authorization" not in headers


class TestDevtoAdapterDocstring:
    def test_docstring_mentions_nofollow(self):
        """Verify the adapter docstring explicitly calls out nofollow status."""
        assert "nofollow" in DevtoAPIAdapter.__doc__


class TestR9ExtensionReadiness:
    def test_devto_in_registered_platforms(self):
        from backlink_publisher.publishing.registry import registered_platforms
        assert "devto" in registered_platforms()

    def test_devto_dofollow_false(self):
        from backlink_publisher.publishing.registry import dofollow_status
        assert dofollow_status("devto") is False
