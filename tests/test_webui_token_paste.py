"""WebUI token-paste binding route — Plan 006 follow-up."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

# Module-level import to register the route blueprint
from webui_app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _csrf(client):
    """Grab a CSRF token by GET-ing settings; the session middleware
    seeds it into the meta tag."""
    resp = client.get("/settings")
    assert resp.status_code == 200
    # Extract from <meta name="csrf-token" content="...">
    import re
    m = re.search(rb'name="csrf-token" content="([^"]+)"', resp.data)
    assert m, "no csrf token in settings page"
    return m.group(1).decode()


class TestSaveTokenAllowlist:
    def test_unknown_channel_rejected(self, client):
        csrf = _csrf(client)
        resp = client.post("/settings/save-channel-token", data={
            "csrf_token": csrf,
            "channel": "wpcom",  # not in allowlist
            "token": "x",
        })
        assert resp.status_code == 302
        assert b"unknown channel" in resp.data or b'wpcom' in resp.data

    def test_devto_accepted_via_generic_route(self, client, tmp_path):
        # Dev.to is now in _ALLOWED and uses api_key field
        csrf = _csrf(client)
        resp = client.post("/settings/save-channel-token", data={
            "csrf_token": csrf,
            "channel": "devto",
            "token": "devto_test_key_12345",
        })
        assert resp.status_code == 302
        assert b"flash_type=success" in resp.data

    def test_blogger_rejected(self, client):
        # Blogger is OAuth-bound, not token-paste — should not be exposed
        # through this route to avoid confusion.
        csrf = _csrf(client)
        resp = client.post("/settings/save-channel-token", data={
            "csrf_token": csrf,
            "channel": "blogger",
            "token": "x",
        })
        assert resp.status_code == 302
        assert b"flash_type=danger" in resp.data


class TestSaveGhpagesToken:
    def test_save_writes_file_0600(self, client, tmp_path):
        csrf = _csrf(client)
        resp = client.post("/settings/save-channel-token", data={
            "csrf_token": csrf,
            "channel": "ghpages",
            "token": "ghp_testabc123def456",
        })
        assert resp.status_code == 302
        assert b"flash_type=success" in resp.data
        token_file = tmp_path / "ghpages-token.json"
        assert token_file.exists()
        if os.name != "nt":
            assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
        data = json.loads(token_file.read_text())
        assert data == {"token": "ghp_testabc123def456", "token_rev": 1}

    def test_empty_token_does_not_modify(self, client, tmp_path):
        # Seed an existing token first
        token_file = tmp_path / "ghpages-token.json"
        token_file.write_text(json.dumps({"token": "ghp_original"}))
        token_file.chmod(0o600)

        csrf = _csrf(client)
        resp = client.post("/settings/save-channel-token", data={
            "csrf_token": csrf,
            "channel": "ghpages",
            "token": "",  # empty
        })
        assert resp.status_code == 302
        assert b"flash_type=info" in resp.data
        # File unchanged
        assert json.loads(token_file.read_text()) == {"token": "ghp_original"}


class TestClearToken:
    def test_clear_removes_file(self, client, tmp_path):
        token_file = tmp_path / "ghpages-token.json"
        token_file.write_text(json.dumps({"token": "ghp_to_delete"}))
        token_file.chmod(0o600)

        csrf = _csrf(client)
        resp = client.post("/settings/save-channel-token", data={
            "csrf_token": csrf,
            "channel": "ghpages",
            "clear": "1",
        })
        assert resp.status_code == 302
        assert b"flash_type=success" in resp.data
        assert b"token+%E5%B7%B2%E6%B8%85%E9%99%A4" in resp.data or b"token%20%E5%B7%B2%E6%B8%85%E9%99%A4" in resp.data
        assert not token_file.exists()

    def test_clear_nonexistent_is_info(self, client, tmp_path):
        token_file = tmp_path / "ghpages-token.json"
        assert not token_file.exists()
        csrf = _csrf(client)
        resp = client.post("/settings/save-channel-token", data={
            "csrf_token": csrf,
            "channel": "ghpages",
            "clear": "1",
        })
        assert resp.status_code == 302
        assert b"flash_type=info" in resp.data


class TestSettingsRenderWithCards:
    def test_settings_page_includes_ghpages_card(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert b'channel-ghpages' in resp.data
        assert b'GitHub Pages' in resp.data
        assert b'token-paste-ghpages' in resp.data

    def test_settings_page_dofollow_chip_for_confirmed(self, client):
        # ghpages is dofollow=True per _DOFOLLOW_BY_CHANNEL, so the
        # "dofollow" chip should appear at least once.
        resp = client.get("/settings")
        assert b'dofollow' in resp.data

    def test_settings_chrome_publish_channels_exposed_after_unit4c(self, client):
        # Units 4b/4c shipped devto + mastodon as chrome-publish channels —
        # both must appear in the binding dashboard. wpcom permanently rejected;
        # writeas retired to HIDDEN_FROM_UI — both must stay absent.
        resp = client.get("/settings")
        assert b'channel-devto' in resp.data
        assert b'channel-mastodon' in resp.data
        assert b'channel-wpcom' not in resp.data
        assert b'channel-writeas' not in resp.data
        assert b'token-paste-writeas' not in resp.data

    def test_settings_page_includes_notion_card(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert b'channel-notion' in resp.data
        assert b'Notion' in resp.data
        assert b'token-paste-notion' in resp.data

    def test_settings_page_includes_devto_token_paste_card(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert b'Dev.to' in resp.data
        assert b'token-paste-devto' in resp.data


class TestSaveDevtoToken:
    def test_save_writes_api_key_to_file(self, client, tmp_path):
        csrf = _csrf(client)
        resp = client.post("/settings/save-channel-token", data={
            "csrf_token": csrf,
            "channel": "devto",
            "token": "devto_key_abc123",
        })
        assert resp.status_code == 302
        assert b"flash_type=success" in resp.data
        token_file = tmp_path / "devto-token.json"
        assert token_file.exists()
        data = json.loads(token_file.read_text())
        assert data["api_key"] == "devto_key_abc123"

    def test_empty_token_does_not_modify(self, client, tmp_path):
        # Seed existing devto token
        token_file = tmp_path / "devto-token.json"
        token_file.write_text(json.dumps({"api_key": "original_key"}))
        token_file.chmod(0o600)

        csrf = _csrf(client)
        resp = client.post("/settings/save-channel-token", data={
            "csrf_token": csrf,
            "channel": "devto",
            "token": "",
        })
        assert resp.status_code == 302
        assert b"flash_type=info" in resp.data
        assert json.loads(token_file.read_text())["api_key"] == "original_key"

    def test_clear_devto_token(self, client, tmp_path):
        token_file = tmp_path / "devto-token.json"
        token_file.write_text(json.dumps({"api_key": "to_delete"}))
        token_file.chmod(0o600)

        csrf = _csrf(client)
        resp = client.post("/settings/save-channel-token", data={
            "csrf_token": csrf,
            "channel": "devto",
            "clear": "1",
        })
        assert resp.status_code == 302
        assert b"flash_type=success" in resp.data
        assert not token_file.exists()


class TestSaveNotionToken:
    def test_save_notion_token_writes_both_fields(self, client, tmp_path):
        csrf = _csrf(client)
        resp = client.post("/settings/save-notion-token", data={
            "csrf_token": csrf,
            "integration_token": "secret_abc123",
            "database_id": "db_xyz_456",
        })
        assert resp.status_code == 302
        assert b"flash_type=success" in resp.data
        token_file = tmp_path / "notion-token.json"
        assert token_file.exists()
        data = json.loads(token_file.read_text())
        assert data["integration_token"] == "secret_abc123"
        assert data["database_id"] == "db_xyz_456"

    def test_missing_integration_token_rejected(self, client):
        csrf = _csrf(client)
        resp = client.post("/settings/save-notion-token", data={
            "csrf_token": csrf,
            "integration_token": "",
            "database_id": "db_xyz_456",
        })
        assert resp.status_code == 302
        assert b"flash_type=danger" in resp.data

    def test_missing_database_id_rejected(self, client):
        csrf = _csrf(client)
        resp = client.post("/settings/save-notion-token", data={
            "csrf_token": csrf,
            "integration_token": "secret_abc",
            "database_id": "",
        })
        assert resp.status_code == 302
        assert b"flash_type=danger" in resp.data

    def test_empty_form_returns_info(self, client):
        csrf = _csrf(client)
        resp = client.post("/settings/save-notion-token", data={
            "csrf_token": csrf,
            "integration_token": "",
            "database_id": "",
        })
        assert resp.status_code == 302
        assert b"flash_type=info" in resp.data

    def test_clear_notion_token(self, client, tmp_path):
        token_file = tmp_path / "notion-token.json"
        token_file.write_text(json.dumps({
            "integration_token": "secret_old",
            "database_id": "db_old",
        }))
        token_file.chmod(0o600)

        csrf = _csrf(client)
        resp = client.post("/settings/save-notion-token", data={
            "csrf_token": csrf,
            "clear": "1",
        })
        assert resp.status_code == 302
        assert b"flash_type=success" in resp.data
        assert not token_file.exists()

    def test_notion_file_has_0600_permissions(self, client, tmp_path):
        csrf = _csrf(client)
        client.post("/settings/save-notion-token", data={
            "csrf_token": csrf,
            "integration_token": "secret_perm_test",
            "database_id": "db_perm",
        })
        token_file = tmp_path / "notion-token.json"
        if os.name != "nt":
            assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
