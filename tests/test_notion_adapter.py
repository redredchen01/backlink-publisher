"""Unit 6: Notion adapter tests (Plan 003 Phase 2)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters.notion_api import (
    NotionAPIAdapter,
    _build_page_payload,
    _load_credentials,
    _required_headers,
)
from backlink_publisher.publishing.adapters.base import AdapterResult


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    cfg = MagicMock()
    cfg.notion_token_path = tmp_path / "notion-token.json"
    return cfg


@pytest.fixture
def config_with_token(config, tmp_path):
    token_data = {
        "integration_token": "secret_test_token",
        "database_id": "test_db_12345",
        "token_rev": 1,
    }
    config.notion_token_path.write_text(json.dumps(token_data))
    return config


def _mock_notion_success_response(page_id="abc123", url=None):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "object": "page",
        "id": page_id,
        "url": url or f"https://www.notion.so/{page_id}",
    }
    resp.text = ""
    return resp


class TestRequiredHeaders:
    def test_includes_bearer_prefix(self):
        headers = _required_headers("secret_abc")
        assert headers["Authorization"] == "Bearer secret_abc"

    def test_includes_notion_version(self):
        headers = _required_headers("token")
        assert "Notion-Version" in headers

    def test_includes_content_type(self):
        headers = _required_headers("token")
        assert headers["Content-Type"] == "application/json"


class TestLoadCredentials:
    def test_raises_dependency_error_when_no_file(self, config):
        with pytest.raises(DependencyError, match="integration token"):
            _load_credentials(config)

    def test_raises_dependency_error_when_token_empty(self, tmp_path, config):
        config.notion_token_path.write_text(
            json.dumps({"integration_token": "", "database_id": "db1"})
        )
        with pytest.raises(DependencyError, match="integration token"):
            _load_credentials(config)

    def test_raises_dependency_error_when_database_id_missing(self, config):
        config.notion_token_path.write_text(
            json.dumps({"integration_token": "secret_x", "database_id": ""})
        )
        with pytest.raises(DependencyError, match="database_id"):
            _load_credentials(config)

    def test_returns_token_and_db_when_valid(self, config_with_token):
        token, db_id = _load_credentials(config_with_token)
        assert token == "secret_test_token"
        assert db_id == "test_db_12345"


class TestBuildPagePayload:
    def test_includes_title_in_properties(self):
        payload = {"title": "My Test Article", "content": "Body text"}
        result = _build_page_payload(payload, "db_123")
        title_block = result["properties"]["Name"]["title"][0]
        assert title_block["text"]["content"] == "My Test Article"

    def test_parent_uses_database_id(self):
        payload = {"title": "Test"}
        result = _build_page_payload(payload, "my_database_id")
        assert result["parent"]["database_id"] == "my_database_id"

    def test_canonical_url_appended_when_present(self):
        payload = {
            "title": "Test",
            "seo": {"canonical_url": "https://example.com/post"},
        }
        result = _build_page_payload(payload, "db1")
        children = result["children"]
        # Last child should be the canonical paragraph
        last = children[-1]
        assert last["type"] == "paragraph"
        rich_text = last["paragraph"]["rich_text"]
        # Should contain a text block with the URL and a link
        link_parts = [r for r in rich_text if r.get("text", {}).get("link")]
        assert len(link_parts) == 1
        assert link_parts[0]["text"]["link"]["url"] == "https://example.com/post"
        assert link_parts[0]["text"]["content"] == "https://example.com/post"

    def test_canonical_url_omitted_when_empty(self):
        payload = {
            "title": "Test",
            "seo": {"canonical_url": ""},
        }
        result = _build_page_payload(payload, "db1")
        children_text = json.dumps(result["children"])
        assert "canonical" not in children_text.lower()

    def test_canonical_url_omitted_when_no_seo(self):
        payload = {"title": "Test", "body": "Content"}
        result = _build_page_payload(payload, "db1")
        children_text = json.dumps(result["children"])
        assert "canonical" not in children_text.lower()

    def test_body_paragraphs_created_from_markdown(self):
        payload = {
            "title": "Test",
            "content_markdown": "Line one\nLine two\nLine three",
        }
        result = _build_page_payload(payload, "db1")
        # Should have 3 paragraph blocks from the body
        body_blocks = [
            b for b in result["children"]
            if b["type"] == "paragraph"
            and any("Line" in rt["text"]["content"] for rt in b["paragraph"]["rich_text"])
        ]
        assert len(body_blocks) == 3


class TestNotionAPIAdapterAvailable:
    def test_false_when_no_token_file(self, config):
        assert NotionAPIAdapter.available(config) is False

    def test_false_when_token_empty(self, config):
        config.notion_token_path.write_text(
            json.dumps({"integration_token": "", "database_id": "db1"})
        )
        assert NotionAPIAdapter.available(config) is False

    def test_false_when_db_id_empty(self, config):
        config.notion_token_path.write_text(
            json.dumps({"integration_token": "secret_x", "database_id": ""})
        )
        assert NotionAPIAdapter.available(config) is False

    def test_true_when_both_fields_present(self, config_with_token):
        assert NotionAPIAdapter.available(config_with_token) is True


class TestNotionAPIAdapterPublish:
    def test_happy_path_returns_published_result(self, config_with_token):
        adapter = NotionAPIAdapter()
        mock_resp = _mock_notion_success_response(
            page_id="page_abc", url="https://www.notion.so/pageabc"
        )
        with patch("requests.post", return_value=mock_resp):
            result = adapter.publish(
                {"title": "Test Article", "content": "Body"},
                mode="live",
                config=config_with_token,
            )
        assert result.status == "published"
        assert result.platform == "notion"
        assert "notion.so" in result.published_url

    def test_draft_mode_returns_drafted_without_api_call(self, config_with_token):
        adapter = NotionAPIAdapter()
        with patch("requests.post") as mock_post:
            result = adapter.publish(
                {"title": "Draft Article"},
                mode="draft",
                config=config_with_token,
            )
        assert result.status == "drafted"
        assert mock_post.call_count == 0

    def test_canonical_url_in_request_body(self, config_with_token):
        adapter = NotionAPIAdapter()
        mock_resp = _mock_notion_success_response()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            adapter.publish(
                {
                    "title": "Test",
                    "seo": {"canonical_url": "https://example.com/test"},
                },
                mode="live",
                config=config_with_token,
            )
        call_kwargs = mock_post.call_args.kwargs
        children = call_kwargs["json"]["children"]
        canonical_block = children[-1]
        rich_text = canonical_block["paragraph"]["rich_text"]
        link_item = [r for r in rich_text if r.get("text", {}).get("link")]
        assert link_item[0]["text"]["link"]["url"] == "https://example.com/test"

    def test_no_seo_no_canonical_in_body(self, config_with_token):
        adapter = NotionAPIAdapter()
        mock_resp = _mock_notion_success_response()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            adapter.publish(
                {"title": "Pure backlink"},
                mode="live",
                config=config_with_token,
            )
        call_kwargs = mock_post.call_args.kwargs
        children_json = json.dumps(call_kwargs["json"]["children"])
        assert "canonical" not in children_json.lower()

    def test_401_raises_external_service_error(self, config_with_token):
        adapter = NotionAPIAdapter()
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

    def test_missing_token_raises_dependency_error(self, config):
        adapter = NotionAPIAdapter()
        with pytest.raises(DependencyError):
            adapter.publish(
                {"title": "Test"},
                mode="live",
                config=config,
            )

    def test_fallback_url_construction_when_no_url_in_response(self, config_with_token):
        adapter = NotionAPIAdapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "object": "page",
            "id": "abc-def-123",
            "url": "",
        }
        mock_resp.text = ""
        with patch("requests.post", return_value=mock_resp):
            result = adapter.publish(
                {"title": "Test"},
                mode="live",
                config=config_with_token,
            )
        assert "abcdef123" in result.published_url


class TestR9ExtensionReadiness:
    def test_notion_in_registered_platforms(self):
        from backlink_publisher.publishing.registry import registered_platforms
        assert "notion" in registered_platforms()

    def test_notion_dofollow_false(self):
        from backlink_publisher.publishing.registry import dofollow_status
        assert dofollow_status("notion") is False
