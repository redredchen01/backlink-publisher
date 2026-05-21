"""Tests for the publish-backend pill — Plan 2026-05-21-001 Unit 5.

Covers ``_publish_backend_for`` classification + ``get_channel_status``
exposing the field + template macro rendering the expected pill class
for each backend kind.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from webui_app.binding_status import (
    _publish_backend_for,
    get_channel_status,
)


@pytest.fixture
def fake_config(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    from backlink_publisher.config import load_config
    return load_config()


@pytest.fixture(autouse=True)
def import_adapters():
    """Adapters import triggers register() side effects."""
    import backlink_publisher.publishing.adapters  # noqa: F401
    yield


class TestPublishBackendClassification:
    @pytest.mark.parametrize(
        "channel,expected",
        [
            ("blogger", "api"),       # single API entry
            ("telegraph", "api"),     # single API entry
            ("writeas", "api"),       # single API entry
            ("ghpages", "api"),       # single API entry
            ("medium", "api"),        # 3 API entries
            ("velog", "api+chrome"),  # API primary + chrome fallback
            ("hashnode", "api+chrome"),  # paywalled API + chrome bypass
            ("devto", "chrome"),      # browser only (re-registered post un-reject)
            ("mastodon", "chrome"),   # browser only
        ],
    )
    def test_classification(self, channel, expected):
        assert _publish_backend_for(channel) == expected

    def test_unknown_channel_returns_unknown(self):
        assert _publish_backend_for("nonexistent") == "unknown"


class TestStatusExposesField:
    def test_get_channel_status_includes_publish_backend(self, fake_config):
        status = get_channel_status("velog", fake_config)
        assert status["publish_backend"] == "api+chrome"

    def test_chrome_only_channel_status(self, fake_config):
        status = get_channel_status("devto", fake_config)
        assert status["publish_backend"] == "chrome"

    def test_api_only_channel_status(self, fake_config):
        status = get_channel_status("blogger", fake_config)
        assert status["publish_backend"] == "api"


class TestTemplateRendersPill:
    """Render the macro via Jinja and assert pill HTML matches backend."""

    @pytest.fixture
    def env(self):
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        template_dir = (
            Path(__file__).resolve().parents[1]
            / "webui_app"
            / "templates"
        )
        return Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(["html"]),
        )

    @pytest.fixture
    def render(self, env):
        def _render(status):
            template_src = (
                "{% from '_channel_card_macro.html' import dashboard_channel_card %}"
                "{{ dashboard_channel_card(status.channel, status) }}"
            )
            return env.from_string(template_src).render(status=status)

        return _render

    def _base_status(self, **overrides):
        base = {
            "channel": "test",
            "bound": False,
            "identity": None,
            "last_verified_at": None,
            "last_verify_result": "never",
            "dofollow": True,
            "publish_backend": "api",
            "blockers": [],
        }
        base.update(overrides)
        return base

    def test_chrome_only_renders_chrome_pill(self, render):
        html = render(self._base_status(publish_backend="chrome"))
        assert 'badge-publish-backend chrome' in html
        assert ">Chrome<" in html

    def test_mixed_renders_api_plus_chrome_pill(self, render):
        html = render(self._base_status(publish_backend="api+chrome"))
        assert 'badge-publish-backend mixed' in html
        assert ">API + Chrome<" in html

    def test_api_only_renders_api_pill(self, render):
        html = render(self._base_status(publish_backend="api"))
        assert 'badge-publish-backend api' in html

    def test_unknown_backend_renders_no_pill(self, render):
        html = render(self._base_status(publish_backend="unknown"))
        assert "badge-publish-backend" not in html

    def test_nofollow_warning_row_for_dofollow_false(self, render):
        html = render(self._base_status(dofollow=False, publish_backend="chrome"))
        assert "dch-nofollow-warning" in html
        assert "不傳遞 PageRank" in html

    def test_no_nofollow_warning_for_dofollow_true(self, render):
        html = render(self._base_status(dofollow=True))
        assert "dch-nofollow-warning" not in html

    def test_no_nofollow_warning_for_uncertain(self, render):
        html = render(self._base_status(dofollow="uncertain"))
        assert "dch-nofollow-warning" not in html
