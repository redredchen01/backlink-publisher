"""Ghost adapter tests — focus on the P0#1 JWT signing regression.

The Ghost Admin API key is ``<id>:<secret>`` where ``secret`` is HEX-encoded.
The JWT HMAC must sign with ``bytes.fromhex(secret)``; signing with
``secret.encode()`` (the ASCII bytes of the hex string) produces a wrong
signature and Ghost rejects every request with HTTP 401.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters.ghost_api import GhostAPIAdapter
from backlink_publisher.publishing.adapters.base import AdapterResult

# 32-byte (64 hex char) secret, the Ghost Admin API key format.
_KEY_ID = "6512a1b2c3d4e5f600000001"
_SECRET_HEX = "0123456789abcdef" * 4
_ADMIN_KEY = f"{_KEY_ID}:{_SECRET_HEX}"
_SITE_URL = "https://blog.example.com"


@pytest.fixture
def config(tmp_path):
    cfg = MagicMock()
    cfg.config_dir = tmp_path
    return cfg


@pytest.fixture
def config_with_creds(config):
    (config.config_dir / "ghost-token.json").write_text(
        json.dumps({"admin_api_key": _ADMIN_KEY, "site_url": _SITE_URL})
    )
    return config


def _ok_response(url=f"{_SITE_URL}/my-post/"):
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"posts": [{"url": url, "slug": "my-post"}]}
    resp.text = ""
    return resp


def _payload():
    return {"id": "a1", "title": "Hello", "content_markdown": "<p>hi</p>", "tags": []}


def _extract_jwt(authorization_header: str) -> tuple[bytes, bytes, bytes]:
    """Return (signing_input, actual_sig_bytes, ) from a 'Ghost <jwt>' header."""
    assert authorization_header.startswith("Ghost ")
    token = authorization_header[len("Ghost "):]
    header_b64, payload_b64, sig_b64 = token.split(".")
    signing_input = (header_b64 + "." + payload_b64).encode()
    pad = "=" * (-len(sig_b64) % 4)
    actual_sig = base64.urlsafe_b64decode(sig_b64 + pad)
    return signing_input, actual_sig


class TestJwtSigning:
    def test_jwt_signed_with_hex_decoded_secret(self, config_with_creds):
        """P0#1 regression: HMAC key must be bytes.fromhex(secret)."""
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["headers"] = headers
            return _ok_response()

        with patch(
            "backlink_publisher.publishing.adapters.ghost_api.requests.post",
            side_effect=fake_post,
        ):
            GhostAPIAdapter().publish(_payload(), "publish", config_with_creds)

        signing_input, actual_sig = _extract_jwt(captured["headers"]["Authorization"])

        expected_correct = hmac.new(
            bytes.fromhex(_SECRET_HEX), signing_input, hashlib.sha256
        ).digest()
        expected_buggy = hmac.new(
            _SECRET_HEX.encode(), signing_input, hashlib.sha256
        ).digest()

        assert actual_sig == expected_correct
        # Lock the bug: the old secret.encode() path must NOT match.
        assert actual_sig != expected_buggy

    def test_jwt_header_carries_key_id_as_kid(self, config_with_creds):
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["headers"] = headers
            return _ok_response()

        with patch(
            "backlink_publisher.publishing.adapters.ghost_api.requests.post",
            side_effect=fake_post,
        ):
            GhostAPIAdapter().publish(_payload(), "publish", config_with_creds)

        token = captured["headers"]["Authorization"][len("Ghost "):]
        header_b64 = token.split(".")[0]
        pad = "=" * (-len(header_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(header_b64 + pad))
        assert header["kid"] == _KEY_ID
        assert header["alg"] == "HS256"


class TestHappyPath:
    def test_publish_returns_adapter_result(self, config_with_creds):
        with patch(
            "backlink_publisher.publishing.adapters.ghost_api.requests.post",
            return_value=_ok_response(),
        ):
            result = GhostAPIAdapter().publish(_payload(), "publish", config_with_creds)
        assert isinstance(result, AdapterResult)
        assert result.status == "published"
        assert result.platform == "ghost"
        assert result.published_url == f"{_SITE_URL}/my-post/"


class TestDependencyErrors:
    def test_missing_cred_file_unavailable_and_raises(self, config):
        assert GhostAPIAdapter.available(config) is False
        with pytest.raises(DependencyError):
            GhostAPIAdapter().publish(_payload(), "publish", config)

    def test_key_without_colon_raises_dependency_error(self, config):
        (config.config_dir / "ghost-token.json").write_text(
            json.dumps({"admin_api_key": "no-colon-here", "site_url": _SITE_URL})
        )
        with pytest.raises(DependencyError):
            GhostAPIAdapter().publish(_payload(), "publish", config)

    def test_non_hex_secret_raises_dependency_error(self, config):
        (config.config_dir / "ghost-token.json").write_text(
            json.dumps({"admin_api_key": f"{_KEY_ID}:not-hex-zzzz", "site_url": _SITE_URL})
        )
        with pytest.raises(DependencyError):
            GhostAPIAdapter().publish(_payload(), "publish", config)


class TestExternalServiceErrors:
    def test_401_raises_external_service_error(self, config_with_creds):
        resp = MagicMock()
        resp.status_code = 401
        resp.text = "Unauthorized"
        with patch(
            "backlink_publisher.publishing.adapters.ghost_api.requests.post",
            return_value=resp,
        ):
            with pytest.raises(ExternalServiceError) as exc:
                GhostAPIAdapter().publish(_payload(), "publish", config_with_creds)
        # f-prefix regression: the status code must be interpolated, not literal.
        assert "401" in str(exc.value)
        assert "{resp.status_code}" not in str(exc.value)
