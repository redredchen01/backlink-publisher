"""Unit 6c — Blogger live verify (Plan 2026-05-19-006).

Replaces the ``unverifiable_live`` stub for blogger in
``adapters._verify_live`` with a real GET to
``https://www.googleapis.com/blogger/v3/users/self`` using the stored
OAuth access_token as a Bearer header.

Tests cover:
  - happy path (200) → identity = displayName + dofollow=True
  - 401 → token_expired (access token rotation needed)
  - timeout → 'timeout'
  - other HTTP status (5xx, 403) → 'never' with blocker
  - no token file → 'never' without HTTP call
  - read-only invariant (verify must NOT trigger an OAuth refresh + save).
    This is the Blogger-specific subset of the lesson from Unit 6a:
    rotation belongs to the publish path. For Blogger the rotation is
    OAuth refresh (writes back ``blogger-token.json``) and is *also*
    publish-path only — verify uses whatever token is currently on disk.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from backlink_publisher.config import Config, load_config
from backlink_publisher.publishing._verify import VerifyResult


def _seed_blogger_token(config_dir: Path, access_token: str = "ya29.fake-access-token"):
    """Write a minimal blogger-token.json that ``load_blogger_token`` accepts."""
    token_file = config_dir / "blogger-token.json"
    token_file.write_text(
        json.dumps(
            {
                "token": access_token,
                "refresh_token": "1//fake-refresh",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "cid.apps.googleusercontent.com",
                "client_secret": "csecret",
                "scopes": ["https://www.googleapis.com/auth/blogger"],
            }
        )
    )
    os.chmod(token_file, 0o600)
    return token_file


def _config_with_blogger_oauth(tmp_path: Path) -> Config:
    """Build a Config whose blogger_oauth is non-None so offline-readiness passes."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[blogger.oauth]\n'
        'client_id = "cid.apps.googleusercontent.com"\n'
        'client_secret = "csecret"\n'
    )
    return load_config(cfg_file)


def _ok_response(display_name: str = "Test Blogger") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "kind": "blogger#user",
        "id": "987654321",
        "displayName": display_name,
        "url": "https://www.blogger.com/profile/987654321",
    }
    return resp


def _http_status_response(status: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body or {"error": {"code": status, "message": "err"}}
    return resp


class TestBloggerLiveVerifyHappyPath:
    """200 → identity surfaced, dofollow=True, last_verified_at set."""

    def test_returns_ok_with_display_name_as_identity(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_blogger_token(tmp_path)
        cfg = _config_with_blogger_oauth(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.get", return_value=_ok_response("My Blog Owner")):
            result = verify_adapter_setup("blogger", cfg, mode="live")

        assert isinstance(result, VerifyResult)
        assert result.ok is True
        assert result.identity == "My Blog Owner"
        assert result.last_verify_result == "ok"
        assert result.dofollow is True
        assert result.last_verified_at is not None
        assert result.last_verified_at.endswith("Z")

    def test_calls_users_self_with_bearer_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_blogger_token(tmp_path, access_token="BEARER_TOKEN_XYZ")
        cfg = _config_with_blogger_oauth(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.get", return_value=_ok_response()) as mock_get:
            verify_adapter_setup("blogger", cfg, mode="live")

        assert mock_get.call_count == 1
        url = mock_get.call_args.args[0]
        assert "googleapis.com/blogger/v3/users/self" in url
        headers = mock_get.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer BEARER_TOKEN_XYZ"


class TestBloggerLiveVerify401:
    """401 from users.self → last_verify_result='token_expired'."""

    def test_401_yields_token_expired(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_blogger_token(tmp_path)
        cfg = _config_with_blogger_oauth(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.get", return_value=_http_status_response(401)):
            result = verify_adapter_setup("blogger", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "token_expired"
        # blockers should hint at re-bind, since Blogger access tokens are 1h
        assert any("rebind" in b.lower() or "re-bind" in b.lower() or "expired" in b.lower()
                   for b in result.blockers)


class TestBloggerLiveVerifyTimeout:
    def test_timeout_yields_timeout_result(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_blogger_token(tmp_path)
        cfg = _config_with_blogger_oauth(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.get", side_effect=requests.Timeout("slow")):
            result = verify_adapter_setup("blogger", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "timeout"

    def test_connection_error_yields_never(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_blogger_token(tmp_path)
        cfg = _config_with_blogger_oauth(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.get", side_effect=requests.ConnectionError("dns")):
            result = verify_adapter_setup("blogger", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "never"


class TestBloggerLiveVerifyOtherStatus:
    """403 / 5xx → never, NOT token_expired (operator action ≠ re-bind)."""

    @pytest.mark.parametrize("status", [403, 500, 502, 503])
    def test_non_401_error_yields_never(self, status, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_blogger_token(tmp_path)
        cfg = _config_with_blogger_oauth(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.get", return_value=_http_status_response(status)):
            result = verify_adapter_setup("blogger", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "never"


class TestBloggerLiveVerifyNoToken:
    """Offline-readiness probe should reject; no HTTP call made."""

    def test_no_token_file_short_circuits_to_never(self, tmp_path, monkeypatch):
        """Empty config dir + valid blogger_oauth config → still never since
        offline readiness only checks blogger_oauth, but live verify must also
        require a token to send the Bearer header. Without token → never +
        zero HTTP calls."""
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cfg = _config_with_blogger_oauth(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.get") as mock_get:
            result = verify_adapter_setup("blogger", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "never"
        assert mock_get.call_count == 0

    def test_no_oauth_config_short_circuits_to_never(self, tmp_path, monkeypatch):
        """No [blogger.oauth] in config.toml → offline-readiness probe rejects
        with DependencyError → never, no HTTP call."""
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        (tmp_path / "config.toml").write_text("")
        _seed_blogger_token(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.get") as mock_get:
            result = verify_adapter_setup("blogger", Config(), mode="live")

        assert result.ok is False
        assert result.last_verify_result == "never"
        assert mock_get.call_count == 0


class TestBloggerLiveVerifyReadOnly:
    """CRITICAL: live verify must NEVER touch the token file.

    For Blogger this means: no OAuth refresh, no save_blogger_token() call.
    Operators re-bind via publish path or /settings; verify is observation
    only. See lesson `feedback_telegraph_rotation_publish_path_only` —
    Blogger's analogue is OAuth refresh.
    """

    def test_happy_verify_does_not_modify_token_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        token_file = _seed_blogger_token(tmp_path, access_token="ORIGINAL_BEARER")
        mtime_before = token_file.stat().st_mtime
        contents_before = token_file.read_text()
        cfg = _config_with_blogger_oauth(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.get", return_value=_ok_response()):
            verify_adapter_setup("blogger", cfg, mode="live")

        assert token_file.stat().st_mtime == mtime_before, (
            "live verify mutated blogger-token.json mtime — OAuth refresh leak"
        )
        assert token_file.read_text() == contents_before, (
            "live verify changed blogger-token.json contents — OAuth refresh leak"
        )

    def test_401_verify_does_not_attempt_refresh(self, tmp_path, monkeypatch):
        """Even on 401, do not call save_blogger_token. Re-bind is operator-driven."""
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        token_file = _seed_blogger_token(tmp_path, access_token="EXPIRED_BEARER")
        contents_before = token_file.read_text()
        cfg = _config_with_blogger_oauth(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with (
            patch("backlink_publisher.http.get", return_value=_http_status_response(401)),
            patch("backlink_publisher.config.save_blogger_token") as mock_save,
        ):
            result = verify_adapter_setup("blogger", cfg, mode="live")

        assert result.last_verify_result == "token_expired"
        mock_save.assert_not_called()
        assert token_file.read_text() == contents_before

    def test_verify_does_not_invoke_build_credentials(self, tmp_path, monkeypatch):
        """``_build_credentials`` triggers OAuth refresh + save when token is
        near expiry. Live verify must bypass it entirely, reading the raw
        token dict instead."""
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _seed_blogger_token(tmp_path)
        cfg = _config_with_blogger_oauth(tmp_path)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with (
            patch("backlink_publisher.http.get", return_value=_ok_response()),
            patch(
                "backlink_publisher.publishing.adapters.blogger_api._build_credentials"
            ) as mock_build,
        ):
            verify_adapter_setup("blogger", cfg, mode="live")

        mock_build.assert_not_called()
