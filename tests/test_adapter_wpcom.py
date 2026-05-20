"""Phase 4 — WordPress.com adapter (Plan 006 follow-up).

Lean coverage:
  - WpcomConfig + load/save_wpcom_token contract
  - Config loader parses [wpcom]
  - _required_headers uses Bearer + JSON
  - Publish happy path (201 → AdapterResult)
  - Draft mode (no POST, body status flipped)
  - 401 / 403 / 404 / config-missing / token-missing error paths
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.config import (
    Config,
    WpcomConfig,
    load_config,
    load_wpcom_token,
    save_wpcom_token,
)
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters.wpcom import (
    DEFAULT_API_BASE,
    WpcomAPIAdapter,
    _publish_endpoint,
    _required_headers,
)


def _seed_token(config_dir: Path, token: str = "wp_oauth_fake") -> Path:
    path = config_dir / "wpcom-token.json"
    path.write_text(json.dumps({"token": token}))
    os.chmod(path, 0o600)
    return path


def _config_with_wpcom(tmp_path: Path, site_id: str = "12345") -> Config:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'[wpcom]\nsite_id = "{site_id}"\n')
    return load_config(cfg_file)


def _setup_env(tmp_path, monkeypatch, site_id="12345") -> Config:
    config_dir = tmp_path / "config-dir"
    config_dir.mkdir()
    _seed_token(config_dir)
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
    return _config_with_wpcom(tmp_path, site_id=site_id)


def _ok_publish_response(link: str = "https://op.wordpress.com/p1") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 201
    resp.headers = {}
    resp.json.return_value = {"id": 1, "link": link}
    return resp


def _http_status_response(status: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.text = json.dumps(body or {})
    resp.json.return_value = body or {}
    return resp


class TestWpcomTokenIO:
    def test_load_returns_none_when_missing(self, tmp_path):
        assert load_wpcom_token(tmp_path / "x.json") is None

    def test_save_and_load_round_trip(self, tmp_path):
        path = tmp_path / "wpcom-token.json"
        save_wpcom_token({"token": "t"}, path)
        assert load_wpcom_token(path) == {"token": "t"}

    def test_save_sets_0600(self, tmp_path):
        path = tmp_path / "wpcom-token.json"
        save_wpcom_token({"token": "a"}, path)
        assert os.stat(path).st_mode & 0o777 == 0o600


class TestWpcomConfig:
    def test_defaults(self):
        cfg = WpcomConfig()
        assert cfg.site_id == ""
        assert cfg.api_base == "https://public-api.wordpress.com"

    def test_loader_parses_section(self, tmp_path):
        cfg = _config_with_wpcom(tmp_path, site_id="42")
        assert cfg.wpcom is not None
        assert cfg.wpcom.site_id == "42"

    def test_no_section_yields_none(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("")
        cfg = load_config(cfg_file)
        assert cfg.wpcom is None


class TestRequiredHeaders:
    def test_uses_bearer(self):
        assert _required_headers("tk")["Authorization"] == "Bearer tk"

    def test_content_type_json(self):
        assert _required_headers("x")["Content-Type"] == "application/json"


class TestEndpoint:
    def test_publish_endpoint_uses_v2(self):
        assert _publish_endpoint(DEFAULT_API_BASE, "5") == (
            "https://public-api.wordpress.com/wp/v2/sites/5/posts"
        )

    def test_publish_endpoint_strips_trailing_slash(self):
        assert _publish_endpoint("https://a/", "1") == "https://a/wp/v2/sites/1/posts"


class TestAvailable:
    def test_no_config_unavailable(self):
        assert WpcomAPIAdapter.available(Config()) is False

    def test_empty_site_id_unavailable(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text('[wpcom]\nsite_id = ""\n')
        cfg = load_config(cfg_file)
        assert WpcomAPIAdapter.available(cfg) is False

    def test_with_site_id_available(self, tmp_path):
        cfg = _config_with_wpcom(tmp_path, site_id="1")
        assert WpcomAPIAdapter.available(cfg) is True


class TestPublishHappyPath:
    def test_publish_returns_url_from_link(self, tmp_path, monkeypatch):
        cfg = _setup_env(tmp_path, monkeypatch)
        with patch(
            "backlink_publisher.publishing.adapters.wpcom.requests.post",
            return_value=_ok_publish_response("https://op.wordpress.com/2026/05/p"),
        ) as mock_post:
            result = WpcomAPIAdapter().publish(
                {"id": "a1", "title": "T", "tags": ["x"]},
                mode="live",
                config=cfg,
            )
            assert result.status == "published"
            assert result.published_url == "https://op.wordpress.com/2026/05/p"
            assert mock_post.call_args.kwargs["headers"]["Authorization"] == (
                "Bearer wp_oauth_fake"
            )

    def test_draft_mode_skips_network(self, tmp_path, monkeypatch):
        cfg = _setup_env(tmp_path, monkeypatch)
        with patch(
            "backlink_publisher.publishing.adapters.wpcom.requests.post"
        ) as mock_post:
            result = WpcomAPIAdapter().publish(
                {"id": "x", "title": "T"}, mode="draft", config=cfg
            )
            assert result.status == "drafted"
            mock_post.assert_not_called()


class TestPublishErrorPaths:
    def test_401_raises(self, tmp_path, monkeypatch):
        cfg = _setup_env(tmp_path, monkeypatch)
        with patch(
            "backlink_publisher.publishing.adapters.wpcom.requests.post",
            return_value=_http_status_response(401),
        ):
            with pytest.raises(ExternalServiceError, match="401"):
                WpcomAPIAdapter().publish(
                    {"id": "x", "title": "T"}, mode="live", config=cfg
                )

    def test_403_raises_with_scope_hint(self, tmp_path, monkeypatch):
        cfg = _setup_env(tmp_path, monkeypatch)
        with patch(
            "backlink_publisher.publishing.adapters.wpcom.requests.post",
            return_value=_http_status_response(403),
        ):
            with pytest.raises(ExternalServiceError, match="403"):
                WpcomAPIAdapter().publish(
                    {"id": "x", "title": "T"}, mode="live", config=cfg
                )

    def test_404_raises_with_site_id_hint(self, tmp_path, monkeypatch):
        cfg = _setup_env(tmp_path, monkeypatch, site_id="999999")
        with patch(
            "backlink_publisher.publishing.adapters.wpcom.requests.post",
            return_value=_http_status_response(404),
        ):
            with pytest.raises(ExternalServiceError, match="404"):
                WpcomAPIAdapter().publish(
                    {"id": "x", "title": "T"}, mode="live", config=cfg
                )

    def test_config_missing_raises(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("")
        cfg = load_config(cfg_file)
        with pytest.raises(DependencyError, match="config missing"):
            WpcomAPIAdapter().publish(
                {"id": "x", "title": "T"}, mode="live", config=cfg
            )

    def test_token_missing_raises(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_wpcom(tmp_path)
        with pytest.raises(DependencyError, match="token not configured"):
            WpcomAPIAdapter().publish(
                {"id": "x", "title": "T"}, mode="live", config=cfg
            )
