"""LinkedIn adapter tests — P1#6 post-URN-from-header regression.

POST /v2/posts returns 201 with an EMPTY body; the created post URN is in
the ``x-restli-id`` response header. The adapter previously read
resp.json()["id"] and raised on every success.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters.linkedin_api import LinkedInAPIAdapter
from backlink_publisher.publishing.adapters.base import AdapterResult


@pytest.fixture
def config(tmp_path):
    cfg = MagicMock()
    cfg.linkedin_token_path = tmp_path / "linkedin-token.json"
    cfg.config_dir = tmp_path
    return cfg


@pytest.fixture
def config_with_token(config):
    config.linkedin_token_path.write_text(
        json.dumps({"token": "tok123", "person_id": "urn:li:person:abc"})
    )
    os.chmod(config.linkedin_token_path, 0o600)
    return config


def _payload():
    return {"id": "a1", "title": "Hi", "content_markdown": "body", "tags": []}


def _resp(status=201, headers=None, body=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.text = ""
    if body is None:
        resp.json.side_effect = ValueError("empty body")
    else:
        resp.json.return_value = body
    return resp


def test_post_url_built_from_x_restli_id_header(config_with_token):
    urn = "urn:li:share:7890123"
    resp = _resp(status=201, headers={"x-restli-id": urn}, body=None)
    with patch(
        "backlink_publisher.publishing.adapters.linkedin_api.requests.post",
        return_value=resp,
    ):
        result = LinkedInAPIAdapter().publish(_payload(), "publish", config_with_token)
    assert isinstance(result, AdapterResult)
    assert result.published_url == f"https://www.linkedin.com/feed/update/{urn}"


def test_missing_urn_header_and_body_raises(config_with_token):
    resp = _resp(status=201, headers={}, body=None)
    with patch(
        "backlink_publisher.publishing.adapters.linkedin_api.requests.post",
        return_value=resp,
    ):
        with pytest.raises(ExternalServiceError):
            LinkedInAPIAdapter().publish(_payload(), "publish", config_with_token)


def test_missing_token_raises_dependency_error(config):
    with pytest.raises(DependencyError):
        LinkedInAPIAdapter().publish(_payload(), "publish", config)
