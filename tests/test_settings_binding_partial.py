"""_settings_channel_binding.html partial — Plan 2026-05-19-001 Unit 5.

Renders the partial via Flask's render_template with stub contexts and
asserts:
  - status badge text + class match Chinese localization
  - "上次绑定 YYYY-MM-DD" subtext appears only when bound_at is present
  - a11y attributes are present (role=status, aria-live=polite, aria-label)
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path):
    fake_config_dir = tmp_path / "config"
    with patch(
        "backlink_publisher.config._config_dir", return_value=fake_config_dir,
    ):
        yield fake_config_dir


@pytest.fixture
def app():
    from webui_app import create_app
    a = create_app(start_scheduler=False)
    a.config["TESTING"] = True
    return a


def _render(app, channel, channel_statuses):
    from webui_app.services.bind_job import BIND_ERROR_MESSAGES
    with app.app_context():
        from flask import render_template
        return render_template(
            "_settings_channel_binding.html",
            channel=channel,
            channel_statuses=channel_statuses,
            bind_error_messages=BIND_ERROR_MESSAGES,
        )


class TestPartialStates:
    def test_bound_renders_chinese_badge_and_bound_at(self, app):
        html = _render(app, "medium", {
            "medium": {
                "status": "bound",
                "bound_at": "2026-05-19T10:00:00+00:00",
                "storage_state_path": "/tmp/medium-storage-state.json",
            },
        })
        assert "已绑定 ✓" in html
        assert "上次绑定 2026-05-19" in html
        assert 'id="bind-badge-medium"' in html

    def test_expired_renders_warn_badge_and_rebind_button(self, app):
        html = _render(app, "medium", {
            "medium": {"status": "expired", "bound_at": "2026-05-10T08:00:00+00:00"},
        })
        assert "已过期 ⚠" in html
        assert "重新绑定" in html
        assert "重新绑定 medium 渠道" in html  # aria-label

    def test_unbound_renders_error_badge_and_bind_button(self, app):
        html = _render(app, "blogger", {})
        assert "未绑定" in html
        # New users see "绑定" not "重新绑定"
        assert ">绑定<" in html or "绑定 blogger" in html
        assert "重新绑定" not in html or "重新绑定 blogger" not in html

    def test_missing_channel_key_defaults_unbound(self, app):
        # channel_statuses omits 'velog' entirely — must not KeyError
        html = _render(app, "velog", {"medium": {"status": "bound"}})
        assert "未绑定" in html


class TestA11y:
    def test_role_status_and_aria_live_present(self, app):
        html = _render(app, "medium", {"medium": {"status": "bound"}})
        assert 'role="status"' in html
        assert 'aria-live="polite"' in html

    def test_button_has_aria_label_with_channel_name(self, app):
        for ch in ("velog", "medium", "blogger"):
            html = _render(app, ch, {ch: {"status": "unbound"}})
            assert f"绑定 {ch} 渠道" in html, (
                f"missing aria-label for channel {ch!r}"
            )

    def test_button_carries_data_channel_attribute(self, app):
        html = _render(app, "medium", {})
        assert 'data-channel="medium"' in html


class TestSettingsRouteIncludesPartial:
    """End-to-end: GET /settings renders both Blogger and Medium binding sections."""

    def test_settings_html_contains_both_binding_sections(self, app):
        client = app.test_client()
        resp = client.get("/settings")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'id="bind-section-blogger"' in html
        assert 'id="bind-section-medium"' in html
        assert 'src="/static/js/bind_channel.js"' in html
        assert '<meta name="csrf-token"' in html
