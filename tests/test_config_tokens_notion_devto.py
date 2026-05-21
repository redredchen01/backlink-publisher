"""Unit 4: Token storage + config loaders for Notion + Dev.to (Plan 003 Phase 2)."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from backlink_publisher.config import (
    load_devto_token,
    load_notion_token,
    save_devto_token,
    save_notion_token,
)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    return tmp_path


class TestNotionToken:
    def test_save_writes_correct_json(self, config_dir):
        save_notion_token(
            {"integration_token": "secret_abc123", "database_id": "db456"}
        )
        token_file = config_dir / "notion-token.json"
        assert token_file.exists()
        data = json.loads(token_file.read_text())
        assert data["integration_token"] == "secret_abc123"
        assert data["database_id"] == "db456"
        assert data["token_rev"] == 1

    def test_save_sets_0600_permissions(self, config_dir):
        save_notion_token(
            {"integration_token": "secret_xyz", "database_id": "db000"}
        )
        token_file = config_dir / "notion-token.json"
        if os.name != "nt":
            assert stat.S_IMODE(token_file.stat().st_mode) == 0o600

    def test_load_roundtrip(self, config_dir):
        original = {"integration_token": "secret_aaa", "database_id": "dbBBB"}
        save_notion_token(original)
        loaded = load_notion_token()
        assert loaded is not None
        assert loaded["integration_token"] == "secret_aaa"
        assert loaded["database_id"] == "dbBBB"

    def test_load_returns_none_when_missing(self, config_dir):
        assert load_notion_token() is None

    def test_env_var_respected(self, tmp_path, monkeypatch):
        alt_dir = tmp_path / "alt_config"
        alt_dir.mkdir()
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(alt_dir))
        save_notion_token({"integration_token": "secret_env", "database_id": "db_env"})
        token_file = alt_dir / "notion-token.json"
        assert token_file.exists()
        data = json.loads(token_file.read_text())
        assert data["integration_token"] == "secret_env"

    def test_token_rev_increments(self, config_dir):
        save_notion_token({"integration_token": "a", "database_id": "b"})
        save_notion_token({"integration_token": "c", "database_id": "d"})
        loaded = load_notion_token()
        assert loaded["token_rev"] == 2

    def test_load_with_explicit_path(self, config_dir):
        explicit_path = config_dir / "custom-notion.json"
        save_notion_token(
            {"integration_token": "custom_val", "database_id": "custom_db"},
            path=explicit_path,
        )
        assert explicit_path.exists()
        loaded = load_notion_token(path=explicit_path)
        assert loaded["integration_token"] == "custom_val"


class TestDevtoToken:
    def test_save_writes_correct_json(self, config_dir):
        save_devto_token({"api_key": "devto_key_12345"})
        token_file = config_dir / "devto-token.json"
        assert token_file.exists()
        data = json.loads(token_file.read_text())
        assert data["api_key"] == "devto_key_12345"
        assert data["token_rev"] == 1

    def test_save_sets_0600_permissions(self, config_dir):
        save_devto_token({"api_key": "key_abc"})
        token_file = config_dir / "devto-token.json"
        if os.name != "nt":
            assert stat.S_IMODE(token_file.stat().st_mode) == 0o600

    def test_load_roundtrip(self, config_dir):
        save_devto_token({"api_key": "round_trip_key"})
        loaded = load_devto_token()
        assert loaded is not None
        assert loaded["api_key"] == "round_trip_key"

    def test_load_returns_none_when_missing(self, config_dir):
        assert load_devto_token() is None

    def test_env_var_respected(self, tmp_path, monkeypatch):
        alt_dir = tmp_path / "alt_devto"
        alt_dir.mkdir()
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(alt_dir))
        save_devto_token({"api_key": "env_key"})
        token_file = alt_dir / "devto-token.json"
        assert token_file.exists()
        data = json.loads(token_file.read_text())
        assert data["api_key"] == "env_key"

    def test_token_rev_increments(self, config_dir):
        save_devto_token({"api_key": "first"})
        save_devto_token({"api_key": "second"})
        loaded = load_devto_token()
        assert loaded["token_rev"] == 2

    def test_load_with_explicit_path(self, config_dir):
        explicit_path = config_dir / "custom-devto.json"
        save_devto_token({"api_key": "explicit_key"}, path=explicit_path)
        assert explicit_path.exists()
        loaded = load_devto_token(path=explicit_path)
        assert loaded["api_key"] == "explicit_key"
