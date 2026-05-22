"""Static CSS file serving — Plan B (index.html template split) Unit 1."""

from __future__ import annotations

import pytest

from webui_app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    app = create_app()
    app.config["TESTING"] = True
    app.config["CSRF_ENABLED"] = False
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_index_css_served(client):
    """GET /static/css/index.css returns 200 with text/css content-type."""
    resp = client.get("/static/css/index.css")
    assert resp.status_code == 200
    assert "text/css" in resp.content_type


def test_index_page_links_to_css(client):
    """GET / includes a <link> pointing to css/index.css."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "css/index.css" in body


def test_index_page_has_no_inline_style(client):
    """GET / response body contains no inline <style> element."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "<style>" not in body
