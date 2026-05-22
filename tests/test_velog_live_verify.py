"""Unit 6b — Velog live verify (Plan 2026-05-19-006).

Replaces the ``unverifiable_live`` stub for velog in ``adapters._verify_live``
with a POST to ``https://v2.velog.io/graphql`` running a tiny
``{ auth { id username profile { display_name } } }`` query, authenticated with
the cookie jar loaded by the existing ``_load_cookies`` helper.

Tests cover:
  - happy path (200 + auth) → identity = username + dofollow=True
  - auth is null → token_expired (cookies invalidated)
  - timeout, connection error, non-200 → never/timeout split
  - read-only invariant: cookies file untouched after verify (velog refresh
    happens server-side via Set-Cookie which requests.Session captures
    in-memory only — verify must not persist anything back to disk)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from backlink_publisher.config import Config
from backlink_publisher.config.types import VelogConfig
from backlink_publisher.publishing._verify import VerifyResult


def _seed_velog_cookies(
    config_dir: Path,
    access_token: str = "at-abc",
    refresh_token: str = "rt-xyz",
) -> Path:
    """Write a velog-cookies.json that ``_load_cookies`` accepts (0600 + cookies key)."""
    cookies_file = config_dir / "velog-cookies.json"
    cookies_file.write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "access_token", "value": access_token},
                    {"name": "refresh_token", "value": refresh_token},
                ]
            }
        )
    )
    os.chmod(cookies_file, 0o600)
    return cookies_file


def _config_with_velog(cookies_path: Path) -> Config:
    return Config(velog=VelogConfig(cookies_path=cookies_path))


def _ok_response(username: str = "test-velog-user") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": {
            "auth": {
                "id": "uuid-1234",
                "username": username,
                "profile": {"display_name": "Test Velog User"},
            }
        }
    }
    return resp


def _null_user_response() -> MagicMock:
    """200 + data.auth null — velog's silent-drop signal that the
    cookie session is no longer authenticated."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": {"auth": None}}
    return resp


def _http_status_response(status: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {}
    return resp


class TestVelogLiveVerifyHappyPath:
    def test_returns_ok_with_username_identity(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cookies = _seed_velog_cookies(tmp_path)
        cfg = _config_with_velog(cookies)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.post", return_value=_ok_response("graphql-user")):
            result = verify_adapter_setup("velog", cfg, mode="live")

        assert isinstance(result, VerifyResult)
        assert result.ok is True
        assert result.identity == "graphql-user"
        assert result.last_verify_result == "ok"
        assert result.dofollow is True
        assert result.last_verified_at is not None
        assert result.last_verified_at.endswith("Z")

    def test_posts_to_velog_graphql_with_cookies_and_required_headers(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cookies = _seed_velog_cookies(tmp_path, access_token="AT_LIVE", refresh_token="RT_LIVE")
        cfg = _config_with_velog(cookies)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.post", return_value=_ok_response()) as mock_post:
            verify_adapter_setup("velog", cfg, mode="live")

        assert mock_post.call_count == 1
        url = mock_post.call_args.args[0]
        assert url == "https://v2.velog.io/graphql"
        body = mock_post.call_args.kwargs.get("json", {})
        assert "auth" in body.get("query", "")
        assert mock_post.call_args.kwargs.get("cookies") == {
            "access_token": "AT_LIVE",
            "refresh_token": "RT_LIVE",
        }
        headers = mock_post.call_args.kwargs.get("headers", {})
        # Must carry velog's required headers to avoid silent-drop
        assert headers.get("origin") == "https://velog.io"
        assert headers.get("content-type") == "application/json"
        assert "Mozilla" in headers.get("user-agent", "")


class TestVelogLiveVerifyTokenExpired:
    """200 + data.auth=null → cookies no longer authenticated → token_expired."""

    def test_currentUser_null_yields_token_expired(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cookies = _seed_velog_cookies(tmp_path)
        cfg = _config_with_velog(cookies)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.post", return_value=_null_user_response()):
            result = verify_adapter_setup("velog", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "token_expired"
        assert any(
            "velog-login" in b or "re-bind" in b.lower() or "expired" in b.lower()
            for b in result.blockers
        )


class TestVelogLiveVerifyNetworkFailures:
    def test_timeout_yields_timeout_result(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cookies = _seed_velog_cookies(tmp_path)
        cfg = _config_with_velog(cookies)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.post", side_effect=requests.Timeout("slow")):
            result = verify_adapter_setup("velog", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "timeout"

    def test_connection_error_yields_never(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cookies = _seed_velog_cookies(tmp_path)
        cfg = _config_with_velog(cookies)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.post", side_effect=requests.ConnectionError("dns")):
            result = verify_adapter_setup("velog", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "never"

    @pytest.mark.parametrize("status", [403, 500, 502, 503])
    def test_non_200_yields_never(self, status, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cookies = _seed_velog_cookies(tmp_path)
        cfg = _config_with_velog(cookies)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.post", return_value=_http_status_response(status)):
            result = verify_adapter_setup("velog", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "never"

    def test_malformed_json_yields_never(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cookies = _seed_velog_cookies(tmp_path)
        cfg = _config_with_velog(cookies)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")

        with patch("backlink_publisher.http.post", return_value=resp):
            result = verify_adapter_setup("velog", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "never"


class TestVelogLiveVerifyNever:
    """Offline probe rejects (no cookies file) → never + zero HTTP calls."""

    def test_no_cookies_file_short_circuits_to_never(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cookies = tmp_path / "velog-cookies.json"  # not created
        cfg = _config_with_velog(cookies)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.post") as mock_post:
            result = verify_adapter_setup("velog", cfg, mode="live")

        assert result.ok is False
        assert result.last_verify_result == "never"
        assert mock_post.call_count == 0


class TestVelogLiveVerifyReadOnly:
    """CRITICAL: verify must never mutate the cookies file on disk.

    Velog's auth model implicitly refreshes ``access_token`` via Set-Cookie
    on any request — but requests.Session captures that in-memory, NOT
    on-disk. Verify path must NOT call any write-back logic.
    """

    def test_verify_does_not_modify_cookies_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cookies = _seed_velog_cookies(tmp_path, access_token="OG_AT", refresh_token="OG_RT")
        mtime_before = cookies.stat().st_mtime
        contents_before = cookies.read_text()
        cfg = _config_with_velog(cookies)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.post", return_value=_ok_response()):
            verify_adapter_setup("velog", cfg, mode="live")

        assert cookies.stat().st_mtime == mtime_before, (
            "live verify mutated velog-cookies.json mtime — refresh leak"
        )
        assert cookies.read_text() == contents_before

    def test_verify_does_not_modify_cookies_on_token_expired(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cookies = _seed_velog_cookies(tmp_path)
        contents_before = cookies.read_text()
        cfg = _config_with_velog(cookies)
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch("backlink_publisher.http.post", return_value=_null_user_response()):
            result = verify_adapter_setup("velog", cfg, mode="live")

        assert result.last_verify_result == "token_expired"
        assert cookies.read_text() == contents_before
