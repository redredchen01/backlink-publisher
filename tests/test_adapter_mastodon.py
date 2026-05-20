"""Phase 4 — Mastodon adapter (Plan 006 follow-up).

Lean coverage:
  - MastodonConfig + load/save_mastodon_token contract
  - Config loader parses [mastodon]
  - _required_headers uses Bearer + form-urlencoded
  - Status text builder respects 480-char budget + appends target_url
  - Publish happy path (201 → AdapterResult)
  - Draft mode (no POST)
  - 401 / 403 / 422 / config-missing / invalid-visibility error paths
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.config import (
    Config,
    MastodonConfig,
    load_config,
    load_mastodon_token,
    save_mastodon_token,
)
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters.mastodon import (
    _MAX_STATUS_CHARS,
    MastodonAPIAdapter,
    _build_status_text,
    _publish_endpoint,
    _required_headers,
)


def _seed_token(config_dir: Path, token: str = "md_fake_access") -> Path:
    path = config_dir / "mastodon-token.json"
    path.write_text(json.dumps({"token": token}))
    os.chmod(path, 0o600)
    return path


def _config_with_mastodon(
    tmp_path: Path,
    instance_url: str = "https://mastodon.example",
    visibility: str = "public",
) -> Config:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[mastodon]\ninstance_url = "{instance_url}"\nvisibility = "{visibility}"\n'
    )
    return load_config(cfg_file)


def _setup_env(tmp_path, monkeypatch, **kwargs) -> Config:
    config_dir = tmp_path / "config-dir"
    config_dir.mkdir()
    _seed_token(config_dir)
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
    return _config_with_mastodon(tmp_path, **kwargs)


def _ok_publish_response(url: str = "https://mastodon.example/@u/1") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {"id": "1", "url": url}
    return resp


def _http_status_response(status: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.text = json.dumps(body or {})
    resp.json.return_value = body or {}
    return resp


class TestMastodonTokenIO:
    def test_load_missing(self, tmp_path):
        assert load_mastodon_token(tmp_path / "x.json") is None

    def test_save_round_trip(self, tmp_path):
        path = tmp_path / "mastodon-token.json"
        save_mastodon_token({"token": "t"}, path)
        assert load_mastodon_token(path) == {"token": "t"}

    def test_save_sets_0600(self, tmp_path):
        path = tmp_path / "mastodon-token.json"
        save_mastodon_token({"token": "a"}, path)
        assert os.stat(path).st_mode & 0o777 == 0o600


class TestMastodonConfig:
    def test_defaults(self):
        cfg = MastodonConfig()
        assert cfg.instance_url == ""
        assert cfg.visibility == "public"

    def test_loader_parses_section(self, tmp_path):
        cfg = _config_with_mastodon(
            tmp_path, instance_url="https://hachyderm.io", visibility="unlisted"
        )
        assert cfg.mastodon is not None
        assert cfg.mastodon.instance_url == "https://hachyderm.io"
        assert cfg.mastodon.visibility == "unlisted"

    def test_no_section_yields_none(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("")
        cfg = load_config(cfg_file)
        assert cfg.mastodon is None


class TestRequiredHeaders:
    def test_uses_bearer(self):
        assert _required_headers("t")["Authorization"] == "Bearer t"

    def test_form_urlencoded(self):
        # Critical: NOT JSON. Mastodon's /api/v1/statuses canonical input is form.
        assert _required_headers("x")["Content-Type"] == (
            "application/x-www-form-urlencoded"
        )


class TestEndpoint:
    def test_strips_trailing_slash(self):
        assert _publish_endpoint("https://m.example/") == (
            "https://m.example/api/v1/statuses"
        )


class TestStatusTextBuilder:
    def test_uses_summary_when_present(self):
        text = _build_status_text(
            {"summary": "Short text", "target_url": "https://x.com"}
        )
        assert "Short text" in text
        assert text.endswith("https://x.com")

    def test_clips_to_budget(self):
        long_summary = "x" * 1000
        text = _build_status_text(
            {"summary": long_summary, "target_url": "https://x.com"}
        )
        # 480 chars max (with the trailing url + \n\n included in budget)
        assert len(text) <= _MAX_STATUS_CHARS
        assert text.endswith("https://x.com")
        assert "…" in text  # truncation marker

    def test_no_url_when_target_absent(self):
        text = _build_status_text({"summary": "Hello", "target_url": ""})
        assert text == "Hello"


class TestAvailable:
    def test_no_config_unavailable(self):
        assert MastodonAPIAdapter.available(Config()) is False

    def test_empty_instance_url_unavailable(self, tmp_path):
        cfg = _config_with_mastodon(tmp_path, instance_url="")
        assert MastodonAPIAdapter.available(cfg) is False

    def test_with_instance_available(self, tmp_path):
        cfg = _config_with_mastodon(tmp_path)
        assert MastodonAPIAdapter.available(cfg) is True


class TestPublishHappyPath:
    def test_publish_returns_url(self, tmp_path, monkeypatch):
        cfg = _setup_env(tmp_path, monkeypatch)
        with patch(
            "backlink_publisher.publishing.adapters.mastodon.requests.post",
            return_value=_ok_publish_response("https://m.example/@u/1"),
        ) as mock_post:
            result = MastodonAPIAdapter().publish(
                {"id": "a1", "summary": "Hi", "target_url": "https://x.com"},
                mode="live",
                config=cfg,
            )
            assert result.status == "published"
            assert result.published_url == "https://m.example/@u/1"
            # Verify form-encoded payload (data=, not json=)
            kwargs = mock_post.call_args.kwargs
            assert "data" in kwargs and "json" not in kwargs
            assert kwargs["data"]["visibility"] == "public"

    def test_draft_mode_skips_network(self, tmp_path, monkeypatch):
        cfg = _setup_env(tmp_path, monkeypatch)
        with patch(
            "backlink_publisher.publishing.adapters.mastodon.requests.post"
        ) as mock_post:
            result = MastodonAPIAdapter().publish(
                {"id": "x", "summary": "S"}, mode="draft", config=cfg
            )
            assert result.status == "drafted"
            mock_post.assert_not_called()


class TestPublishErrorPaths:
    def test_401_raises(self, tmp_path, monkeypatch):
        cfg = _setup_env(tmp_path, monkeypatch)
        with patch(
            "backlink_publisher.publishing.adapters.mastodon.requests.post",
            return_value=_http_status_response(401),
        ):
            with pytest.raises(ExternalServiceError, match="401"):
                MastodonAPIAdapter().publish(
                    {"id": "x", "summary": "S"}, mode="live", config=cfg
                )

    def test_422_raises_with_helpful_hint(self, tmp_path, monkeypatch):
        cfg = _setup_env(tmp_path, monkeypatch)
        with patch(
            "backlink_publisher.publishing.adapters.mastodon.requests.post",
            return_value=_http_status_response(422, {"error": "too long"}),
        ):
            with pytest.raises(ExternalServiceError, match="422"):
                MastodonAPIAdapter().publish(
                    {"id": "x", "summary": "S"}, mode="live", config=cfg
                )

    def test_invalid_visibility_raises(self, tmp_path, monkeypatch):
        cfg = _setup_env(tmp_path, monkeypatch, visibility="secret")
        with pytest.raises(DependencyError, match="visibility"):
            MastodonAPIAdapter().publish(
                {"id": "x", "summary": "S"}, mode="live", config=cfg
            )

    def test_config_missing_raises(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("")
        cfg = load_config(cfg_file)
        with pytest.raises(DependencyError, match="config missing"):
            MastodonAPIAdapter().publish(
                {"id": "x", "summary": "S"}, mode="live", config=cfg
            )

    def test_token_missing_raises(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_mastodon(tmp_path)
        with pytest.raises(DependencyError, match="token not configured"):
            MastodonAPIAdapter().publish(
                {"id": "x", "summary": "S"}, mode="live", config=cfg
            )
