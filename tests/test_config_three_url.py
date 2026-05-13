"""Tests for the three-URL ``[targets."x"]`` schema and the extended
``save_config`` — Plan 2026-05-13-004 Unit 3.

Covers:
- ``_parse_target_three_url`` schema parsing (happy + every error path).
- ``ThreeUrlConfig`` defaults (``DEFAULT_WORK_TEMPLATES`` + ``insecure_tls``).
- ``get_three_url_config`` scheme/trailing-slash tolerance.
- Maintenance-mode INFO log when ``[sites.x]`` and ``[targets.x]`` coexist.
- ``save_config(target_three_url=...)`` three-state semantics + round-trip.
- ``save_config`` preserves ``[blogger.oauth]`` (credential-retention regression).
- ``save_config`` preserves ``[sites.x]`` verbatim (P0 data-loss fix).
- Atomic write: a mid-write failure leaves the original file intact.
"""

from __future__ import annotations

import logging
import os
import stat
from unittest.mock import patch

import pytest

from backlink_publisher.config import (
    DEFAULT_WORK_TEMPLATES,
    ThreeUrlConfig,
    get_three_url_config,
    load_config,
    save_config,
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _write_toml(tmp_path, body: str):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return path


def _basic_three_url(
    *,
    main_url: str = "https://site.com/",
    list_url: str = "https://site.com/list",
    work_urls: list[str] | None = None,
    branded: list[str] | None = None,
    partial: list[str] | None = None,
    exact: list[str] | None = None,
    work_anchor_templates: list[str] | None = None,
    list_path_blocklist: list[str] | None = None,
    insecure_tls: bool = False,
) -> ThreeUrlConfig:
    return ThreeUrlConfig(
        main_url=main_url,
        list_url=list_url,
        work_urls=work_urls or [],
        branded_pool=branded or ["Site", "Site Hub"],
        partial_pool=partial or ["site hub partial"],
        exact_pool=exact or ["site"],
        work_anchor_templates=(
            work_anchor_templates
            if work_anchor_templates is not None
            else list(DEFAULT_WORK_TEMPLATES)
        ),
        list_path_blocklist=list_path_blocklist,
        insecure_tls=insecure_tls,
    )


# ═════════════════════════════════════════════════════════════════════════════
# _parse_target_three_url — schema happy paths
# ═════════════════════════════════════════════════════════════════════════════


class TestParseThreeUrlHappy:
    def test_full_schema_loads_all_fields(self, tmp_path):
        body = """
[targets."https://site.com/"]
main_url = "https://site.com/"
list_url = "https://site.com/list"
work_urls = ["https://site.com/work/1", "https://site.com/work/2"]
branded_pool = ["Brand A", "Brand B"]
partial_pool = ["brand partial"]
exact_pool = ["brand"]
work_anchor_templates = ["{title}", "{title} 详情"]
list_path_blocklist = ["/tag/", "/banned/"]
insecure_tls = true
"""
        cfg = load_config(_write_toml(tmp_path, body))
        assert "https://site.com" in cfg.target_three_url
        entry = cfg.target_three_url["https://site.com"]
        assert entry.main_url == "https://site.com/"
        assert entry.list_url == "https://site.com/list"
        assert entry.work_urls == [
            "https://site.com/work/1",
            "https://site.com/work/2",
        ]
        assert entry.branded_pool == ["Brand A", "Brand B"]
        assert entry.partial_pool == ["brand partial"]
        assert entry.exact_pool == ["brand"]
        assert entry.work_anchor_templates == ["{title}", "{title} 详情"]
        assert entry.list_path_blocklist == ["/tag/", "/banned/"]
        assert entry.insecure_tls is True

    def test_only_required_fields_applies_defaults(self, tmp_path):
        body = """
[targets."https://site.com/"]
main_url = "https://site.com/"
list_url = "https://site.com/list"
branded_pool = ["Brand"]
partial_pool = ["brand partial"]
exact_pool = ["brand"]
"""
        cfg = load_config(_write_toml(tmp_path, body))
        entry = cfg.target_three_url["https://site.com"]
        assert entry.work_urls == []
        assert entry.work_anchor_templates == list(DEFAULT_WORK_TEMPLATES)
        assert entry.list_path_blocklist is None
        assert entry.insecure_tls is False

    def test_default_work_templates_have_title_placeholder(self):
        # Documenting the contract — Unit 4 relies on `{title}` substitution.
        assert all("{title}" in t for t in DEFAULT_WORK_TEMPLATES)
        assert len(DEFAULT_WORK_TEMPLATES) >= 3

    def test_trailing_slash_in_key_is_normalized(self, tmp_path):
        body = """
[targets."https://site.com"]
main_url = "https://site.com/"
list_url = "https://site.com/list"
branded_pool = ["B"]
partial_pool = ["p"]
exact_pool = ["e"]
"""
        cfg = load_config(_write_toml(tmp_path, body))
        # Stored key has no trailing slash; lookup tolerates both forms.
        assert get_three_url_config(cfg, "https://site.com") is not None
        assert get_three_url_config(cfg, "https://site.com/") is not None

    def test_get_three_url_config_returns_none_for_unknown(self, tmp_path):
        cfg = load_config(_write_toml(tmp_path, ""))
        assert get_three_url_config(cfg, "https://nope.com") is None


# ═════════════════════════════════════════════════════════════════════════════
# _parse_target_three_url — error paths
# ═════════════════════════════════════════════════════════════════════════════


class TestParseThreeUrlErrors:
    def test_non_https_main_url_skips_with_warning(self, tmp_path, caplog):
        body = """
[targets."http://site.com/"]
main_url = "http://site.com/"
list_url = "https://site.com/list"
branded_pool = ["B"]
partial_pool = ["p"]
exact_pool = ["e"]
"""
        with caplog.at_level(logging.WARNING, logger="backlink_publisher.config"):
            cfg = load_config(_write_toml(tmp_path, body))
        assert cfg.target_three_url == {}
        assert any("main_url" in r.message for r in caplog.records)

    def test_missing_list_url_skips_with_warning(self, tmp_path, caplog):
        body = """
[targets."https://site.com/"]
main_url = "https://site.com/"
branded_pool = ["B"]
partial_pool = ["p"]
exact_pool = ["e"]
"""
        with caplog.at_level(logging.WARNING, logger="backlink_publisher.config"):
            cfg = load_config(_write_toml(tmp_path, body))
        assert cfg.target_three_url == {}
        assert any("list_url" in r.message for r in caplog.records)

    def test_empty_branded_pool_skips_with_warning(self, tmp_path, caplog):
        body = """
[targets."https://site.com/"]
main_url = "https://site.com/"
list_url = "https://site.com/list"
branded_pool = []
partial_pool = ["p"]
exact_pool = ["e"]
"""
        with caplog.at_level(logging.WARNING, logger="backlink_publisher.config"):
            cfg = load_config(_write_toml(tmp_path, body))
        assert cfg.target_three_url == {}
        assert any("branded_pool" in r.message for r in caplog.records)

    def test_partial_or_exact_pool_missing_skips(self, tmp_path, caplog):
        body = """
[targets."https://site.com/"]
main_url = "https://site.com/"
list_url = "https://site.com/list"
branded_pool = ["B"]
exact_pool = ["e"]
"""
        with caplog.at_level(logging.WARNING, logger="backlink_publisher.config"):
            cfg = load_config(_write_toml(tmp_path, body))
        assert cfg.target_three_url == {}

    def test_non_https_work_url_is_filtered_out(self, tmp_path, caplog):
        body = """
[targets."https://site.com/"]
main_url = "https://site.com/"
list_url = "https://site.com/list"
work_urls = ["https://site.com/work/1", "http://site.com/insecure"]
branded_pool = ["B"]
partial_pool = ["p"]
exact_pool = ["e"]
"""
        with caplog.at_level(logging.WARNING, logger="backlink_publisher.config"):
            cfg = load_config(_write_toml(tmp_path, body))
        entry = cfg.target_three_url["https://site.com"]
        assert entry.work_urls == ["https://site.com/work/1"]

    def test_anchor_keywords_only_entry_does_not_create_three_url(self, tmp_path):
        # Backward-compat: a legacy [targets."x"] with only anchor_keywords must
        # still parse cleanly into target_anchor_keywords (NOT target_three_url).
        body = """
[targets."https://legacy.com/"]
anchor_keywords = ["legacy"]
"""
        cfg = load_config(_write_toml(tmp_path, body))
        assert cfg.target_three_url == {}
        assert cfg.target_anchor_keywords["https://legacy.com"] == ["legacy"]


# ═════════════════════════════════════════════════════════════════════════════
# Maintenance-mode INFO log when [sites.x] + [targets.x] coexist
# ═════════════════════════════════════════════════════════════════════════════


class TestMaintenanceModeLog:
    def test_coexistence_emits_info_not_warn(self, tmp_path, caplog):
        body = """
[sites."https://site.com".url_categories]
home = "https://site.com/"

[targets."https://site.com/"]
main_url = "https://site.com/"
list_url = "https://site.com/list"
branded_pool = ["B"]
partial_pool = ["p"]
exact_pool = ["e"]
"""
        with caplog.at_level(logging.INFO, logger="backlink_publisher.config"):
            cfg = load_config(_write_toml(tmp_path, body))

        # New schema parses fine — both paths coexist
        assert "https://site.com" in cfg.target_three_url
        assert "https://site.com" in cfg.site_url_categories

        # An INFO (not WARN) log mentions maintenance mode
        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("maintenance" in r.message.lower() for r in info_records)

        # Critically: no WARN about maintenance/deprecated (avoid old-user alarm)
        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("maintenance" in r.message.lower() for r in warn_records)
        assert not any("deprecated" in r.message.lower() for r in warn_records)


# ═════════════════════════════════════════════════════════════════════════════
# save_config — three-state target_three_url + round-trip
# ═════════════════════════════════════════════════════════════════════════════


class TestSaveConfigThreeUrl:
    def test_round_trip_writes_all_fields(self, tmp_path):
        path = tmp_path / "config.toml"
        cfg = load_config(path)  # empty config
        three_url = {"https://site.com": _basic_three_url(
            work_urls=["https://site.com/work/1"],
            branded=["Brand"],
            partial=["brand partial"],
            exact=["brand"],
            list_path_blocklist=["/banned/"],
            insecure_tls=True,
        )}
        save_config(cfg, path=path, target_three_url=three_url)

        # Round-trip cycle 1
        reloaded = load_config(path)
        entry = reloaded.target_three_url["https://site.com"]
        assert entry.main_url == "https://site.com/"
        assert entry.list_url == "https://site.com/list"
        assert entry.work_urls == ["https://site.com/work/1"]
        assert entry.branded_pool == ["Brand"]
        assert entry.partial_pool == ["brand partial"]
        assert entry.exact_pool == ["brand"]
        assert entry.list_path_blocklist == ["/banned/"]
        assert entry.insecure_tls is True

        # Round-trip cycle 2 — save again with no args → preserves
        save_config(reloaded, path=path)
        reloaded2 = load_config(path)
        entry2 = reloaded2.target_three_url["https://site.com"]
        assert entry2 == entry  # exact equality across save+load+save+load

    def test_none_preserves_existing_three_url(self, tmp_path):
        path = tmp_path / "config.toml"
        cfg = load_config(path)
        save_config(
            cfg,
            path=path,
            target_three_url={"https://site.com": _basic_three_url()},
        )
        reloaded = load_config(path)
        # call save_config with target_three_url=None — should preserve
        save_config(reloaded, path=path)
        again = load_config(path)
        assert "https://site.com" in again.target_three_url

    def test_empty_dict_clears(self, tmp_path):
        path = tmp_path / "config.toml"
        save_config(
            load_config(path),
            path=path,
            target_three_url={"https://site.com": _basic_three_url()},
        )
        # Now clear
        save_config(load_config(path), path=path, target_three_url={})
        reloaded = load_config(path)
        assert reloaded.target_three_url == {}

    def test_overwrites_with_new_dict(self, tmp_path):
        path = tmp_path / "config.toml"
        save_config(
            load_config(path),
            path=path,
            target_three_url={"https://old.com": _basic_three_url(
                main_url="https://old.com/", list_url="https://old.com/list",
            )},
        )
        save_config(
            load_config(path),
            path=path,
            target_three_url={"https://new.com": _basic_three_url(
                main_url="https://new.com/", list_url="https://new.com/list",
            )},
        )
        reloaded = load_config(path)
        assert "https://old.com" not in reloaded.target_three_url
        assert "https://new.com" in reloaded.target_three_url


# ═════════════════════════════════════════════════════════════════════════════
# CRITICAL: save_config must preserve [blogger.oauth] + [sites.x]
# (P0 data-loss regression guard)
# ═════════════════════════════════════════════════════════════════════════════


class TestSaveConfigPreservesCriticalSections:
    def test_preserves_blogger_oauth(self, tmp_path):
        body = """
[blogger]
"https://site.com" = "blog-id-123"

[blogger.oauth]
client_id     = "id.apps.googleusercontent.com"
client_secret = "secret-value"
"""
        path = _write_toml(tmp_path, body)
        cfg = load_config(path)

        # Save with new three-url payload — must NOT erase OAuth credentials
        save_config(
            cfg,
            path=path,
            target_three_url={"https://site.com": _basic_three_url()},
        )
        reloaded = load_config(path)
        assert reloaded.blogger_oauth is not None
        assert reloaded.blogger_oauth.client_id == "id.apps.googleusercontent.com"
        assert reloaded.blogger_oauth.client_secret == "secret-value"

    def test_preserves_sites_section_verbatim(self, tmp_path):
        # [sites."x"] is the load-bearing read-only schema for the legacy
        # zh-CN path. save_config historically nuked it (P0 data loss).
        body = """
[blogger]
"https://51acgs.com" = "1234567890"

[sites."https://51acgs.com".url_categories]
home = "https://51acgs.com/"
hot = "https://51acgs.com/comic/hot"

[sites."https://51acgs.com".anchor_pools.home]
branded = ["51漫画"]
partial = ["成人漫画站"]
exact = ["漫画"]
lsi = ["二次元资源"]
"""
        path = _write_toml(tmp_path, body)
        cfg = load_config(path)
        assert cfg.site_url_categories  # sanity: loaded once

        save_config(
            cfg,
            path=path,
            target_three_url={"https://51acgs.com": _basic_three_url(
                main_url="https://51acgs.com/",
                list_url="https://51acgs.com/list",
            )},
        )

        reloaded = load_config(path)
        # [sites.x].url_categories survived round-trip
        assert reloaded.site_url_categories["https://51acgs.com"]["home"] \
            == "https://51acgs.com/"
        assert reloaded.site_url_categories["https://51acgs.com"]["hot"] \
            == "https://51acgs.com/comic/hot"
        # [sites.x].anchor_pools.home survived too
        from backlink_publisher.config import get_anchor_pool_v2
        assert get_anchor_pool_v2(
            reloaded, "https://51acgs.com", "home", "branded"
        ) == ["51漫画"]

    def test_preserves_anchor_proportions_and_llm_section(self, tmp_path):
        body = """
[blogger]
"https://site.com" = "1"

[anchor.proportions]
preset = "safe_seo"

[llm.anchor_provider]
base_url = "https://api.openai.com/v1"
api_key = "k"
model = "gpt-4o-mini"
"""
        path = _write_toml(tmp_path, body)
        # NB: api_key is in toml; chmod 0600 already applied in _write_toml
        cfg = load_config(path)
        save_config(cfg, path=path, target_three_url={
            "https://site.com": _basic_three_url(),
        })
        rewritten = path.read_text(encoding="utf-8")
        assert "[anchor.proportions]" in rewritten
        assert "[llm.anchor_provider]" in rewritten

    def test_atomic_write_failure_leaves_original_intact(self, tmp_path):
        body = """
[blogger]
"https://site.com" = "blog-id-original"
"""
        path = _write_toml(tmp_path, body)
        original = path.read_text(encoding="utf-8")

        # Force the inner write step to raise — by patching os.replace
        # (the final rename step). The temp file may exist briefly; the
        # invariant is the ORIGINAL path is untouched.
        with patch(
            "backlink_publisher.config.os.replace",
            side_effect=OSError("simulated rename failure"),
        ):
            with pytest.raises(OSError):
                save_config(
                    load_config(path),
                    path=path,
                    target_three_url={
                        "https://site.com": _basic_three_url(),
                    },
                )

        assert path.read_text(encoding="utf-8") == original


# ═════════════════════════════════════════════════════════════════════════════
# Coexistence with legacy [targets."x"].anchor_keywords
# ═════════════════════════════════════════════════════════════════════════════


class TestCoexistenceWithLegacyAnchorKeywords:
    def test_anchor_keywords_and_three_url_in_same_domain_block(self, tmp_path):
        path = tmp_path / "config.toml"
        cfg = load_config(path)
        save_config(
            cfg,
            path=path,
            target_anchor_keywords={"https://site.com": ["site", "site hub"]},
            target_three_url={"https://site.com": _basic_three_url()},
        )
        reloaded = load_config(path)
        assert reloaded.target_anchor_keywords["https://site.com"] == [
            "site", "site hub",
        ]
        assert "https://site.com" in reloaded.target_three_url
