"""Phase 4 — Dev.to adapter (Plan 006 follow-up).

Lean coverage:
  - DevtoConfig + load/save_devto_token contract (file I/O, 0600)
  - Config loader parses [devto]
  - _required_headers uses ``api-key:`` not Authorization / Bearer
  - DevToAPIAdapter.publish() happy path (201 → AdapterResult)
  - Draft mode (no POST)
  - 401 / 403 / config-missing error paths
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.config import (
    Config,
    DevtoConfig,
    load_config,
    load_devto_token,
    save_devto_token,
)
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters.devto import (
    DEFAULT_API_BASE,
    DevToAPIAdapter,
    _required_headers,
    _sanitize_tag,
)


def _seed_token(config_dir: Path, token: str = "dt_fake_key") -> Path:
    path = config_dir / "devto-token.json"
    path.write_text(json.dumps({"token": token}))
    os.chmod(path, 0o600)
    return path


def _config_with_devto(tmp_path: Path, api_base: str | None = None) -> Config:
    cfg_file = tmp_path / "config.toml"
    body = "[devto]\n"
    if api_base:
        body += f'api_base = "{api_base}"\n'
    cfg_file.write_text(body)
    return load_config(cfg_file)


def _ok_publish_response(url: str = "https://dev.to/u/post-slug") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 201
    resp.headers = {}
    resp.json.return_value = {"id": 42, "url": url, "slug": "post-slug"}
    return resp


def _http_status_response(status: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.text = json.dumps(body or {})
    resp.json.return_value = body or {}
    return resp


class TestDevtoTokenIO:
    def test_load_returns_none_when_missing(self, tmp_path):
        assert load_devto_token(tmp_path / "missing.json") is None

    def test_save_and_load_round_trip(self, tmp_path):
        path = tmp_path / "devto-token.json"
        save_devto_token({"token": "dt_xyz"}, path)
        assert load_devto_token(path) == {"token": "dt_xyz"}

    def test_save_sets_0600_permissions(self, tmp_path):
        path = tmp_path / "devto-token.json"
        save_devto_token({"token": "abc"}, path)
        assert os.stat(path).st_mode & 0o777 == 0o600


class TestDevtoConfig:
    def test_defaults(self):
        cfg = DevtoConfig()
        assert cfg.api_base == "https://dev.to/api"

    def test_loader_parses_section(self, tmp_path):
        cfg = _config_with_devto(tmp_path)
        assert cfg.devto is not None
        assert cfg.devto.api_base == "https://dev.to/api"

    def test_loader_custom_api_base(self, tmp_path):
        cfg = _config_with_devto(tmp_path, api_base="https://forem.example/api")
        assert cfg.devto.api_base == "https://forem.example/api"

    def test_no_section_yields_none(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("")
        cfg = load_config(cfg_file)
        assert cfg.devto is None


class TestRequiredHeaders:
    def test_uses_api_key_header(self):
        h = _required_headers("abc")
        assert h["api-key"] == "abc"

    def test_no_authorization_header(self):
        # Regression guard: Dev.to uses `api-key:`, NOT `Authorization:`.
        assert "Authorization" not in _required_headers("xyz")

    def test_content_type_json(self):
        assert _required_headers("x")["Content-Type"] == "application/json"


class TestSanitizeTag:
    def test_lowercase_alpha_only(self):
        assert _sanitize_tag("Python-3") == "python3"

    def test_truncate_at_30(self):
        assert len(_sanitize_tag("a" * 50)) == 30


def _setup_devto_env(tmp_path: Path, monkeypatch) -> Config:
    """Helper: seed token + config in an env-isolated dir, return Config."""
    config_dir = tmp_path / "config-dir"
    config_dir.mkdir()
    _seed_token(config_dir)
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
    return _config_with_devto(tmp_path)


class TestPublishHappyPath:
    def test_publish_returns_published_with_url(self, tmp_path, monkeypatch):
        cfg = _setup_devto_env(tmp_path, monkeypatch)
        payload = {
            "id": "a1",
            "title": "Hello",
            "content_markdown": "# Hi",
            "tags": ["python"],
        }
        with patch(
            "backlink_publisher.publishing.adapters.devto.requests.post",
            return_value=_ok_publish_response("https://dev.to/u/hello-x"),
        ) as mock_post:
            result = DevToAPIAdapter().publish(payload, mode="live", config=cfg)
            assert result.status == "published"
            assert result.platform == "devto"
            assert result.published_url == "https://dev.to/u/hello-x"
            # Verify the api-key header was actually sent
            call_headers = mock_post.call_args.kwargs["headers"]
            assert call_headers["api-key"] == "dt_fake_key"

    def test_draft_mode_skips_network(self, tmp_path, monkeypatch):
        cfg = _setup_devto_env(tmp_path, monkeypatch)
        with patch(
            "backlink_publisher.publishing.adapters.devto.requests.post"
        ) as mock_post:
            result = DevToAPIAdapter().publish(
                {"id": "x", "title": "T", "content_markdown": "x"},
                mode="draft",
                config=cfg,
            )
            assert result.status == "drafted"
            mock_post.assert_not_called()


class TestPublishErrorPaths:
    def test_401_raises_external_service_error(self, tmp_path, monkeypatch):
        cfg = _setup_devto_env(tmp_path, monkeypatch)
        with patch(
            "backlink_publisher.publishing.adapters.devto.requests.post",
            return_value=_http_status_response(401, {"error": "unauthorized"}),
        ):
            with pytest.raises(ExternalServiceError, match="401"):
                DevToAPIAdapter().publish(
                    {"id": "x", "title": "T", "content_markdown": "x"},
                    mode="live",
                    config=cfg,
                )

    def test_403_raises_with_scope_hint(self, tmp_path, monkeypatch):
        cfg = _setup_devto_env(tmp_path, monkeypatch)
        with patch(
            "backlink_publisher.publishing.adapters.devto.requests.post",
            return_value=_http_status_response(403, {"error": "forbidden"}),
        ):
            with pytest.raises(ExternalServiceError, match="403"):
                DevToAPIAdapter().publish(
                    {"id": "x", "title": "T", "content_markdown": "x"},
                    mode="live",
                    config=cfg,
                )

    def test_config_missing_raises_dependency_error(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("")  # no [devto]
        cfg = load_config(cfg_file)
        with pytest.raises(DependencyError, match="config missing"):
            DevToAPIAdapter().publish(
                {"id": "x", "title": "T", "content_markdown": "x"},
                mode="live",
                config=cfg,
            )

    def test_token_missing_raises_dependency_error(self, tmp_path, monkeypatch):
        # config present, but no token file
        config_dir = tmp_path / "config-dir"
        config_dir.mkdir()
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
        cfg = _config_with_devto(tmp_path)
        with pytest.raises(DependencyError, match="API key not configured"):
            DevToAPIAdapter().publish(
                {"id": "x", "title": "T", "content_markdown": "x"},
                mode="live",
                config=cfg,
            )
