"""Beehiiv adapter tests — P1#7 v2 API contract regression.

The adapter must hit POST /v2/publications/{pub}/posts with a body_content
HTML string and status draft|confirmed, and parse the created id out of
the {"data": {"id": ...}} envelope. The old code used /v1, a blocks array,
status="published", and read a top-level "id".
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters.beehiiv_api import BeehiivAPIAdapter
from backlink_publisher.publishing.adapters.base import AdapterResult

_PUB = "pub_abc123"


@pytest.fixture
def config(tmp_path):
    cfg = MagicMock()
    cfg.config_dir = tmp_path
    return cfg


@pytest.fixture
def config_with_creds(config):
    (config.config_dir / "beehiiv-token.json").write_text(
        json.dumps({"api_key": "key_x", "publication_id": _PUB})
    )
    return config


def _payload():
    return {"id": "a1", "title": "Hi", "content_markdown": "<p>body</p>"}


def _resp(status=201, body=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    if body is None:
        resp.json.side_effect = ValueError("no body")
    else:
        resp.json.return_value = body
    return resp


def test_uses_v2_endpoint_body_content_and_confirmed_status(config_with_creds):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        return _resp(201, {"data": {"id": "post_999"}})

    with patch(
        "backlink_publisher.publishing.adapters.beehiiv_api.requests.post",
        side_effect=fake_post,
    ):
        result = BeehiivAPIAdapter().publish(_payload(), "publish", config_with_creds)

    assert captured["url"] == f"https://api.beehiiv.com/v2/publications/{_PUB}/posts"
    assert captured["body"]["body_content"] == "<p>body</p>"
    assert "content" not in captured["body"]  # no legacy blocks array
    assert captured["body"]["status"] == "confirmed"
    assert isinstance(result, AdapterResult)
    assert result.status == "published"


def test_draft_mode_posts_draft_status(config_with_creds):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["body"] = json
        return _resp(201, {"data": {"id": "post_1"}})

    with patch(
        "backlink_publisher.publishing.adapters.beehiiv_api.requests.post",
        side_effect=fake_post,
    ):
        result = BeehiivAPIAdapter().publish(_payload(), "draft", config_with_creds)
    assert captured["body"]["status"] == "draft"
    assert result.status == "drafted"


def test_parses_id_from_data_envelope(config_with_creds):
    with patch(
        "backlink_publisher.publishing.adapters.beehiiv_api.requests.post",
        return_value=_resp(201, {"data": {"id": "post_xyz", "web_url": "https://x.beehiiv.com/p/y"}}),
    ):
        result = BeehiivAPIAdapter().publish(_payload(), "publish", config_with_creds)
    assert result.published_url == "https://x.beehiiv.com/p/y"


def test_empty_data_envelope_raises(config_with_creds):
    with patch(
        "backlink_publisher.publishing.adapters.beehiiv_api.requests.post",
        return_value=_resp(201, {"data": {}}),
    ):
        with pytest.raises(ExternalServiceError):
            BeehiivAPIAdapter().publish(_payload(), "publish", config_with_creds)


def test_403_reports_enterprise_only(config_with_creds):
    with patch(
        "backlink_publisher.publishing.adapters.beehiiv_api.requests.post",
        return_value=_resp(403, text="forbidden"),
    ):
        with pytest.raises(ExternalServiceError) as exc:
            BeehiivAPIAdapter().publish(_payload(), "publish", config_with_creds)
    assert "403" in str(exc.value)
    assert "Enterprise" in str(exc.value)


def test_missing_credentials_raises_dependency_error(config):
    assert BeehiivAPIAdapter.available(config) is False
    with pytest.raises(DependencyError):
        BeehiivAPIAdapter().publish(_payload(), "publish", config)
