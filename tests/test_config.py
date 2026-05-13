"""Tests for config loader."""

import json
import os
import stat
from pathlib import Path

import pytest

from backlink_publisher.config import (
    Config,
    get_anchor_keywords,
    load_config,
    load_blogger_token,
    resolve_blog_id,
    save_blogger_token,
)
from backlink_publisher.errors import DependencyError


SAMPLE_TOML = b"""
[blogger]
"https://my-site.com" = "1234567890"
"https://other-site.org/" = "9876543210"

[blogger.oauth]
client_id = "my-client-id"
client_secret = "my-client-secret"

[medium]
integration_token = "abc-token"
"""


def test_load_config_missing_file(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg.blogger_blog_ids == {}
    assert cfg.blogger_oauth is None
    assert cfg.medium_integration_token is None


def test_load_config_parses_blogger_and_medium(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(SAMPLE_TOML)
    cfg = load_config(cfg_path)

    assert cfg.blogger_blog_ids["https://my-site.com"] == "1234567890"
    assert cfg.blogger_oauth is not None
    assert cfg.blogger_oauth.client_id == "my-client-id"
    assert cfg.medium_integration_token == "abc-token"


def test_load_config_corrupt_toml(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(b"[[not valid toml")
    with pytest.raises(DependencyError, match="Failed to parse"):
        load_config(cfg_path)


def test_resolve_blog_id_found(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(SAMPLE_TOML)
    cfg = load_config(cfg_path)

    assert resolve_blog_id(cfg, "https://my-site.com") == "1234567890"
    # trailing slash normalisation
    assert resolve_blog_id(cfg, "https://my-site.com/") == "1234567890"


def test_resolve_blog_id_with_trailing_slash_in_config(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(SAMPLE_TOML)
    cfg = load_config(cfg_path)
    # Configured as "https://other-site.org/" (with slash)
    assert resolve_blog_id(cfg, "https://other-site.org") == "9876543210"
    assert resolve_blog_id(cfg, "https://other-site.org/") == "9876543210"


def test_resolve_blog_id_missing(tmp_path):
    cfg = Config()
    with pytest.raises(DependencyError, match="https://unknown.com"):
        resolve_blog_id(cfg, "https://unknown.com")


def test_save_and_load_blogger_token(tmp_path):
    token_path = tmp_path / "blogger-token.json"
    data = {"token": "tok123", "refresh_token": "ref456"}
    save_blogger_token(data, token_path)

    loaded = load_blogger_token(token_path)
    assert loaded == data


def test_save_blogger_token_mode_0600(tmp_path):
    token_path = tmp_path / "blogger-token.json"
    save_blogger_token({"token": "x"}, token_path)

    mode = oct(os.stat(token_path).st_mode)[-3:]
    # 600 on Unix; skip check on Windows
    if os.name != "nt":
        assert mode == "600", f"Expected 600, got {mode}"


def test_load_blogger_token_missing(tmp_path):
    result = load_blogger_token(tmp_path / "nonexistent.json")
    assert result is None


# ---------------------------------------------------------------------------
# target_anchor_keywords parsing
# ---------------------------------------------------------------------------

ANCHOR_TOML = b"""
[targets."https://my-site.com"]
anchor_keywords = ["brand", "head term", "long tail phrase"]

[targets."https://other-site.org/"]
anchor_keywords = ["other-brand", "industry word"]
"""


def test_target_anchor_keywords_parsed(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(ANCHOR_TOML)
    cfg = load_config(cfg_path)

    assert cfg.target_anchor_keywords["https://my-site.com"] == [
        "brand", "head term", "long tail phrase",
    ]
    # Trailing slash stripped at parse time
    assert cfg.target_anchor_keywords["https://other-site.org"] == [
        "other-brand", "industry word",
    ]


def test_get_anchor_keywords_normalises_trailing_slash(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(ANCHOR_TOML)
    cfg = load_config(cfg_path)

    # Lookup tolerates either form
    assert get_anchor_keywords(cfg, "https://my-site.com") == [
        "brand", "head term", "long tail phrase",
    ]
    assert get_anchor_keywords(cfg, "https://my-site.com/") == [
        "brand", "head term", "long tail phrase",
    ]


def test_get_anchor_keywords_missing_returns_empty(tmp_path):
    cfg = Config()
    assert get_anchor_keywords(cfg, "https://unknown.com") == []


def test_target_anchor_keywords_section_absent(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(SAMPLE_TOML)  # no [targets] section
    cfg = load_config(cfg_path)
    assert cfg.target_anchor_keywords == {}


def test_target_anchor_keywords_invalid_type_skipped(tmp_path, caplog):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(b"""
[targets."https://bad.com"]
anchor_keywords = "should-be-a-list"

[targets."https://good.com"]
anchor_keywords = ["foo"]
""")
    with caplog.at_level("WARNING"):
        cfg = load_config(cfg_path)

    # Bad entry skipped, good entry kept
    assert "https://bad.com" not in cfg.target_anchor_keywords
    assert cfg.target_anchor_keywords["https://good.com"] == ["foo"]
    # Warning emitted for the malformed entry
    assert any("anchor_keywords" in rec.message for rec in caplog.records)


def test_target_anchor_keywords_empty_list_kept(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(b"""
[targets."https://empty.com"]
anchor_keywords = []
""")
    cfg = load_config(cfg_path)
    # Empty list is preserved verbatim — caller (selector) treats as "no pool"
    assert cfg.target_anchor_keywords["https://empty.com"] == []
    assert get_anchor_keywords(cfg, "https://empty.com") == []
