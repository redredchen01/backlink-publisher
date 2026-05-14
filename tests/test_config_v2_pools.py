"""Tests for Config v2 — typed anchor pools, proportions, LLM provider."""

from __future__ import annotations

import os
import stat
from unittest.mock import patch

import pytest

from backlink_publisher.config import (
    ANCHOR_TYPES,
    LLMProviderConfig,
    get_anchor_keywords,
    get_anchor_pool_v2,
    load_config,
)
from backlink_publisher.errors import InputValidationError


# ── fixtures ────────────────────────────────────────────────────────────────


def _write_toml(tmp_path, body: str):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(body, encoding="utf-8")
    # Make permissions 0600 so api-key-present cases don't trip the warning
    # unless the test explicitly wants that signal.
    try:
        os.chmod(cfg_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return cfg_path


FULL_FIXTURE = """
[blogger]
"https://51acgs.com" = "1111111111"

[sites."https://51acgs.com".url_categories]
home = "https://51acgs.com/"
hot = "https://51acgs.com/comic/hot"
animate = "https://51acgs.com/animate"
category = "https://51acgs.com/category"
topic = "https://51acgs.com/topic/blog"

[sites."https://51acgs.com".anchor_pools.home]
branded = ["51漫画首页", "51漫画"]
partial = ["成人ACG平台"]
exact = ["51"]
lsi = ["线上漫画阅读", "同人漫画推荐"]

[sites."https://51acgs.com".anchor_pools.hot]
branded = []
partial = ["热门漫画推荐", "人气漫画榜单"]
exact = ["热门漫画", "本周热门"]
lsi = ["漫画排行"]

[sites."https://51acgs.com".anchor_pools.animate]
branded = []
partial = ["动漫推荐"]
exact = []
lsi = ["ACG动画", "动漫资源"]

[sites."https://51acgs.com".anchor_pools.category]
branded = []
partial = ["漫画分类"]
exact = []
lsi = ["ACG内容分类"]

[sites."https://51acgs.com".anchor_pools.topic]
branded = []
partial = ["漫画专题"]
exact = []
lsi = ["漫画阅读指南"]

[anchor.proportions]
preset = "safe_seo"

[llm.anchor_provider]
base_url = "https://api.example.com/v1"
api_key = "toml-key-abc"
model = "gpt-4o-mini"
timeout_s = 25
"""


# ── happy paths ─────────────────────────────────────────────────────────────


def test_full_fixture_loads_all_categories_and_types(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    cfg = load_config(_write_toml(tmp_path, FULL_FIXTURE))

    assert set(cfg.site_url_categories["https://51acgs.com"].keys()) == {
        "home", "hot", "animate", "category", "topic",
    }

    # Spot-check the 2D pool
    assert get_anchor_pool_v2(cfg, "https://51acgs.com", "home", "branded") == [
        "51漫画首页", "51漫画",
    ]
    assert get_anchor_pool_v2(cfg, "https://51acgs.com", "hot", "exact") == [
        "热门漫画", "本周热门",
    ]
    assert get_anchor_pool_v2(cfg, "https://51acgs.com", "topic", "lsi") == [
        "漫画阅读指南",
    ]


def test_empty_anchor_type_pool_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    cfg = load_config(_write_toml(tmp_path, FULL_FIXTURE))

    # hot.branded is explicitly empty in the fixture
    assert get_anchor_pool_v2(cfg, "https://51acgs.com", "hot", "branded") == []
    # category.exact is empty (no key in pool) — also returns []
    assert get_anchor_pool_v2(cfg, "https://51acgs.com", "category", "exact") == []


def test_get_pool_unknown_site_returns_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    cfg = load_config(_write_toml(tmp_path, FULL_FIXTURE))

    assert get_anchor_pool_v2(cfg, "https://nope.example", "home", "branded") == []


def test_get_pool_tolerates_trailing_slash(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    cfg = load_config(_write_toml(tmp_path, FULL_FIXTURE))

    # Pool was stored as "https://51acgs.com" — lookup with trailing slash works
    assert get_anchor_pool_v2(cfg, "https://51acgs.com/", "home", "branded") == [
        "51漫画首页", "51漫画",
    ]


def test_default_proportions_when_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    minimal = '[blogger]\n"https://site.com" = "1"\n'
    cfg = load_config(_write_toml(tmp_path, minimal))

    assert cfg.anchor_proportions == {
        "branded": 0.55, "partial": 0.25, "exact": 0.10, "lsi": 0.10,
    }


# ── api_key env var priority ────────────────────────────────────────────────


def test_env_api_key_overrides_toml(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_LLM_API_KEY", "env-key-xyz")
    cfg = load_config(_write_toml(tmp_path, FULL_FIXTURE))

    assert cfg.llm_anchor_provider is not None
    assert cfg.llm_anchor_provider.api_key == "env-key-xyz"


def test_toml_api_key_used_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    cfg = load_config(_write_toml(tmp_path, FULL_FIXTURE))

    assert cfg.llm_anchor_provider is not None
    assert cfg.llm_anchor_provider.api_key == "toml-key-abc"


def test_no_api_key_anywhere_raises_when_section_present(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    body = """
[llm.anchor_provider]
base_url = "https://api.example.com/v1"
model = "gpt-4o-mini"
"""
    with pytest.raises(InputValidationError, match="no api_key"):
        load_config(_write_toml(tmp_path, body))


def test_no_llm_section_yields_no_provider(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    body = '[blogger]\n"https://site.com" = "1"\n'
    cfg = load_config(_write_toml(tmp_path, body))

    assert cfg.llm_anchor_provider is None


# ── base_url https enforcement ──────────────────────────────────────────────


def test_http_base_url_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    body = """
[llm.anchor_provider]
base_url = "http://insecure.example.com/v1"
api_key = "k"
model = "gpt"
"""
    with pytest.raises(InputValidationError, match="https://"):
        load_config(_write_toml(tmp_path, body))


def test_https_base_url_accepted(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    body = """
[llm.anchor_provider]
base_url = "https://api.example.com/v1"
api_key = "k"
model = "gpt"
"""
    cfg = load_config(_write_toml(tmp_path, body))
    assert isinstance(cfg.llm_anchor_provider, LLMProviderConfig)
    assert cfg.llm_anchor_provider.timeout_s == 30.0  # default


# ── permission warning ──────────────────────────────────────────────────────


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_loose_permissions_with_api_key_emits_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    body = """
[llm.anchor_provider]
base_url = "https://api.example.com/v1"
api_key = "secret"
model = "gpt"
"""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(body, encoding="utf-8")
    os.chmod(cfg_path, 0o644)

    import logging
    with caplog.at_level(logging.WARNING, logger="backlink_publisher.config"):
        cfg = load_config(cfg_path)

    assert cfg.llm_anchor_provider is not None  # load still succeeds
    assert any("0600" in record.message for record in caplog.records), (
        f"expected permission warning, got: {[r.message for r in caplog.records]}"
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_strict_0600_permissions_no_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    cfg_path = _write_toml(tmp_path, FULL_FIXTURE)  # _write_toml chmods 0600

    import logging
    with caplog.at_level(logging.WARNING, logger="backlink_publisher.config"):
        load_config(cfg_path)

    assert not any("0600" in record.message for record in caplog.records)


# ── proportion validation ───────────────────────────────────────────────────


def test_proportions_sum_above_one_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    body = """
[anchor.proportions]
branded = 0.55
partial = 0.30
exact = 0.10
lsi = 0.10
"""
    with pytest.raises(InputValidationError, match="sum to 1.0"):
        load_config(_write_toml(tmp_path, body))


def test_proportions_unknown_preset_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    body = """
[anchor.proportions]
preset = "aggressive"
"""
    with pytest.raises(InputValidationError, match="aggressive"):
        load_config(_write_toml(tmp_path, body))


def test_proportions_unknown_anchor_type_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    body = """
[anchor.proportions]
weird_type = 1.0
"""
    with pytest.raises(InputValidationError, match="weird_type"):
        load_config(_write_toml(tmp_path, body))


def test_proportions_partial_override_preserves_safe_seo_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    # Override just exact; partial absorbs the diff so sum stays 1.0.
    body = """
[anchor.proportions]
exact = 0.15
partial = 0.20
"""
    cfg = load_config(_write_toml(tmp_path, body))
    assert cfg.anchor_proportions == {
        "branded": 0.55, "partial": 0.20, "exact": 0.15, "lsi": 0.10,
    }


def test_anchor_types_constant_shape():
    # Guard against accidental reordering — scheduler tie-break depends on order.
    assert ANCHOR_TYPES == ("branded", "partial", "exact", "lsi")


# ── coexistence with legacy fields ──────────────────────────────────────────


def test_legacy_anchor_keywords_and_v2_pools_coexist(tmp_path, monkeypatch):
    """Old [targets...] entries and new [sites....anchor_pools] read independently."""
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    body = """
[targets."https://51acgs.com"]
anchor_keywords = ["legacy_word_1", "legacy_word_2"]

[sites."https://51acgs.com".anchor_pools.home]
branded = ["新的品牌词"]
"""
    cfg = load_config(_write_toml(tmp_path, body))

    # Legacy en/ru path still reads old keywords
    assert get_anchor_keywords(cfg, "https://51acgs.com") == [
        "legacy_word_1", "legacy_word_2",
    ]
    # New zh-CN path reads typed pool
    assert get_anchor_pool_v2(cfg, "https://51acgs.com", "home", "branded") == [
        "新的品牌词",
    ]


def test_unsafe_chars_stripped_from_pool_entries(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    body = """
[sites."https://51acgs.com".anchor_pools.home]
branded = ["clean", "with[brackets]", "<tag>", "quote\\"in"]
"""
    cfg = load_config(_write_toml(tmp_path, body))
    cleaned = get_anchor_pool_v2(cfg, "https://51acgs.com", "home", "branded")
    # Unsafe chars stripped; "tag" survives after < and > are removed.
    assert "clean" in cleaned
    assert "withbrackets" in cleaned
    assert "tag" in cleaned
    assert "quotein" in cleaned


def test_unknown_anchor_type_in_pool_skipped(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    body = """
[sites."https://51acgs.com".anchor_pools.home]
branded = ["ok"]
mystery = ["nope"]
"""
    import logging
    with caplog.at_level(logging.WARNING, logger="backlink_publisher.config"):
        cfg = load_config(_write_toml(tmp_path, body))
    assert get_anchor_pool_v2(cfg, "https://51acgs.com", "home", "branded") == ["ok"]
    # mystery isn't reachable via get_anchor_pool_v2 since it's not a known type
    assert any("mystery" in r.message for r in caplog.records)


def test_malformed_url_in_category_skipped_silently(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    body = """
[sites."https://site.com".url_categories]
home = "https://site.com/"
weird = "not-a-url"
"""
    import logging
    with caplog.at_level(logging.WARNING, logger="backlink_publisher.config"):
        cfg = load_config(_write_toml(tmp_path, body))
    assert cfg.site_url_categories["https://site.com"] == {"home": "https://site.com/"}
    assert any("weird" in r.message for r in caplog.records)


# ── deferred behavior: save_config must not write new fields ────────────────


def test_save_config_preserves_v2_fields_verbatim(tmp_path, monkeypatch):
    """save_config must preserve unknown sections byte-for-byte (Config Safety Net).

    Previously save_config silently dropped any section it didn't know how to
    serialize, which was the documented data-loss bug class behind
    feedback_config-save-overwrite-pattern.md. The new contract: those same
    sections survive a save_config call verbatim (bytes copied from disk).
    """
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    from backlink_publisher.config import save_config

    cfg_path = _write_toml(tmp_path, FULL_FIXTURE)
    cfg = load_config(cfg_path)
    save_config(cfg, path=cfg_path)

    rewritten = cfg_path.read_text(encoding="utf-8")
    # Previously-dropped sections now survive
    assert "[sites." in rewritten
    assert "anchor_pools" in rewritten
    assert "[anchor.proportions]" in rewritten
    assert "[llm.anchor_provider]" in rewritten

    # And the data round-trips through load_config again
    cfg2 = load_config(cfg_path)
    assert cfg2.site_url_categories == cfg.site_url_categories
    assert cfg2.target_anchor_pools_v2 == cfg.target_anchor_pools_v2
    assert cfg2.anchor_proportions == cfg.anchor_proportions
