"""Unit 6a — Telegraph live verify (Plan 2026-05-19-006).

Replaces the ``unverifiable_live`` stub for telegraph in
``adapters._verify_live`` with a real POST to ``/getAccountInfo``.

Tests cover:
  - happy path (200 + ok:true) → identity = short_name + dofollow=True
  - token_expired (Telegraph error markers)
  - timeout
  - never (token file empty / unreadable)
  - read-only invariant (no token-file writes during verify)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from backlink_publisher.config import Config
from backlink_publisher.publishing._verify import VerifyResult


def _seed_telegraph_token(config_dir: Path, access_token: str = "tg-token-abc",
                           short_name: str = "test-account") -> Path:
    """Write a valid telegraph-token.json that ``_load_token`` accepts."""
    token_file = config_dir / "telegraph-token.json"
    token_file.write_text(
        json.dumps({"access_token": access_token, "short_name": short_name})
    )
    os.chmod(token_file, 0o600)
    return token_file


def _ok_response(short_name: str = "test-account") -> MagicMock:
    resp = MagicMock(status_code=200)
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "ok": True,
        "result": {"short_name": short_name, "author_name": "Test Author", "page_count": 7},
    }
    return resp


def _err_response(error: str) -> MagicMock:
    resp = MagicMock(status_code=200)
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"ok": False, "error": error}
    return resp


class TestTelegraphLiveVerifyHappyPath:
    """200 + ok:true → identity surfaced, dofollow=True, last_verified_at set."""

    def test_returns_ok_with_identity(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_telegraph_token(tmp_path, short_name="my-channel")
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.post", return_value=_ok_response("my-channel")):
            result = verify_adapter_setup("telegraph", Config(), mode="live")

        assert isinstance(result, VerifyResult)
        assert result.ok is True
        assert result.identity == "my-channel"
        assert result.last_verify_result == "ok"
        assert result.dofollow is True
        assert result.last_verified_at is not None
        # iso8601 UTC format
        assert result.last_verified_at.endswith("Z")

    def test_calls_correct_endpoint_with_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_telegraph_token(tmp_path, access_token="SECRET_TOKEN_123")
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.post", return_value=_ok_response()) as mock_post:
            verify_adapter_setup("telegraph", Config(), mode="live")

        assert mock_post.call_count == 1
        url = mock_post.call_args.args[0]
        assert "api.telegra.ph" in url
        assert "getAccountInfo" in url
        assert mock_post.call_args.kwargs["data"]["access_token"] == "SECRET_TOKEN_123"


class TestTelegraphLiveVerifyTokenExpired:
    """Telegraph error markers → last_verify_result='token_expired'."""

    @pytest.mark.parametrize(
        "error_str",
        [
            "ACCESS_TOKEN_INVALID",
            "INVALID_ACCESS_TOKEN",
            "ACCESS_TOKEN_INVALID: something extra here",
        ],
    )
    def test_invalid_token_marker_yields_token_expired(
        self, error_str, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_telegraph_token(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.post", return_value=_err_response(error_str)):
            result = verify_adapter_setup("telegraph", Config(), mode="live")

        assert result.ok is False
        assert result.last_verify_result == "token_expired"
        assert any(error_str in b for b in result.blockers)

    def test_other_error_yields_never_not_token_expired(self, tmp_path, monkeypatch):
        """A non-token Telegraph error (e.g. PAGE_NOT_FOUND) → 'never', not
        'token_expired'. We don't want to falsely flag tokens as expired."""
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_telegraph_token(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.post", return_value=_err_response("RATE_LIMITED")):
            result = verify_adapter_setup("telegraph", Config(), mode="live")

        assert result.ok is False
        assert result.last_verify_result == "never"  # not token_expired
        assert any("RATE_LIMITED" in b for b in result.blockers)


class TestTelegraphLiveVerifyTimeout:
    def test_timeout_yields_timeout_result(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_telegraph_token(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.post", side_effect=requests.Timeout("slow")):
            result = verify_adapter_setup("telegraph", Config(), mode="live")

        assert result.ok is False
        assert result.last_verify_result == "timeout"

    def test_network_error_yields_never(self, tmp_path, monkeypatch):
        """ConnectionError (DNS / refused) → never, not timeout."""
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_telegraph_token(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.post", side_effect=requests.ConnectionError("dns")):
            result = verify_adapter_setup("telegraph", Config(), mode="live")

        assert result.ok is False
        assert result.last_verify_result == "never"


class TestTelegraphLiveVerifyNever:
    """No token file / empty token → 'never' without any HTTP call."""

    def test_no_token_file_returns_never_no_http(self, tmp_path, monkeypatch):
        """Empty config dir: no token file → never + 0 HTTP calls."""
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.post") as mock_post:
            result = verify_adapter_setup("telegraph", Config(), mode="live")

        assert result.ok is False
        assert result.last_verify_result == "never"
        assert mock_post.call_count == 0


class TestTelegraphLiveVerifyReadOnly:
    """CRITICAL: live verify must NEVER write the token file (no rotation)."""

    def test_verify_does_not_modify_token_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        token_file = _seed_telegraph_token(tmp_path, access_token="ORIGINAL_TOKEN")
        mtime_before = token_file.stat().st_mtime
        contents_before = token_file.read_text()
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.post", return_value=_ok_response()):
            verify_adapter_setup("telegraph", Config(), mode="live")

        assert token_file.stat().st_mtime == mtime_before, (
            "live verify mutated telegraph-token.json mtime — rotation leak"
        )
        assert token_file.read_text() == contents_before, (
            "live verify changed telegraph-token.json contents — rotation leak"
        )

    def test_verify_does_not_rotate_on_token_expired(self, tmp_path, monkeypatch):
        """Even when token IS expired, live verify must NOT auto-rotate.
        Rotation is publish-path only — operator must explicitly re-bind."""
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        token_file = _seed_telegraph_token(tmp_path, access_token="EXPIRED_TOKEN")
        contents_before = token_file.read_text()
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("requests.post", return_value=_err_response("ACCESS_TOKEN_INVALID")):
            result = verify_adapter_setup("telegraph", Config(), mode="live")

        assert result.last_verify_result == "token_expired"
        # Token file untouched.
        assert token_file.read_text() == contents_before
