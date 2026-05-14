"""Tests for save_config's unknown-section preservation, atomic write, and snapshot history.

These tests are the load-bearing defense against the documented data-loss
bug class (feedback_config-save-overwrite-pattern.md). Each test follows
the negative-shape pattern: write a config WITH the section, call
save_config, then assert the section SURVIVED — not "config without it
doesn't grow one".
"""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from backlink_publisher.config import (
    _CONFIG_HISTORY_MAX,
    _preserve_unknown_sections,
    _SAVE_CONFIG_KNOWN_ROOTS,
    _snapshot_config,
    _toml_heading_root,
    load_config,
    save_config,
)


# ── _toml_heading_root: lexer-lite ───────────────────────────────────────────


def test_heading_root_bare():
    assert _toml_heading_root("[blogger]") == "blogger"
    assert _toml_heading_root("[anchor.proportions]") == "anchor"
    assert _toml_heading_root("[[anchor_alarm.override]]") == "anchor_alarm"


def test_heading_root_quoted():
    assert _toml_heading_root('[targets."https://example.com"]') == "targets"
    assert _toml_heading_root('[sites."51acgs.com".url_categories]') == "sites"


def test_heading_root_with_leading_whitespace():
    """Operators sometimes indent — should still match."""
    assert _toml_heading_root("  [anchor_alarm]") == "anchor_alarm"


def test_heading_root_non_heading_returns_none():
    assert _toml_heading_root('key = "value"') is None
    assert _toml_heading_root("# this is a comment") is None
    assert _toml_heading_root("") is None


# ── _preserve_unknown_sections: byte-exact preservation ──────────────────────


def test_preserve_empty_input():
    assert _preserve_unknown_sections("", _SAVE_CONFIG_KNOWN_ROOTS) == ""


def test_preserve_only_known_sections():
    raw = '[blogger]\n"x.com" = "1"\n[medium]\nintegration_token = "tok"\n'
    assert _preserve_unknown_sections(raw, _SAVE_CONFIG_KNOWN_ROOTS) == ""


def test_preserve_anchor_alarm_section_verbatim():
    raw = """[blogger]
"x.com" = "1"

[anchor_alarm]
entropy_floor = 1.8
exact_ratio_ceiling = 0.05

[medium]
integration_token = "tok"
"""
    out = _preserve_unknown_sections(raw, _SAVE_CONFIG_KNOWN_ROOTS)
    assert "[anchor_alarm]" in out
    assert "entropy_floor = 1.8" in out
    assert "exact_ratio_ceiling = 0.05" in out
    # Known sections are NOT in the preserved chunk
    assert "[blogger]" not in out
    assert "[medium]" not in out


def test_preserve_array_of_tables():
    """[[anchor_alarm.override]] array-of-tables must be preserved as a block."""
    raw = """[anchor_alarm]
entropy_floor = 1.5

[[anchor_alarm.override]]
match = "money.example.com"
scope = "domain"
exact_ratio_ceiling = 0.05

[[anchor_alarm.override]]
match = "https://other.com/page"
scope = "url"
top3_concentration_ceiling = 0.20
"""
    out = _preserve_unknown_sections(raw, _SAVE_CONFIG_KNOWN_ROOTS)
    assert out.count("[[anchor_alarm.override]]") == 2
    assert 'match = "money.example.com"' in out
    assert 'match = "https://other.com/page"' in out
    assert 'top3_concentration_ceiling = 0.20' in out


def test_preserve_comments_inside_unknown_section():
    raw = """[anchor_alarm]
# Operator note: tuned for the 51acgs.com profile after 2026-Q2 audit
entropy_floor = 1.8
# Following ceiling is intentionally tight
exact_ratio_ceiling = 0.05
"""
    out = _preserve_unknown_sections(raw, _SAVE_CONFIG_KNOWN_ROOTS)
    assert "# Operator note: tuned for the 51acgs.com profile" in out
    assert "# Following ceiling is intentionally tight" in out


def test_preserve_deep_dotted_sections():
    raw = """[sites."51acgs.com".url_categories]
home = "https://51acgs.com/"
hot = "https://51acgs.com/hot"

[sites."51acgs.com".anchor_pools.home.branded]
items = ["51漫画", "51acgs"]
"""
    out = _preserve_unknown_sections(raw, _SAVE_CONFIG_KNOWN_ROOTS)
    assert '[sites."51acgs.com".url_categories]' in out
    assert '[sites."51acgs.com".anchor_pools.home.branded]' in out
    assert 'items = ["51漫画", "51acgs"]' in out


def test_preserve_drops_file_preamble():
    """Lines before the first heading are dropped (save_config rewrites the preamble)."""
    raw = """# This is a top-level comment
# Should not survive save_config (it's part of the file preamble)

[anchor_alarm]
entropy_floor = 1.5
"""
    out = _preserve_unknown_sections(raw, _SAVE_CONFIG_KNOWN_ROOTS)
    assert "This is a top-level comment" not in out
    assert "[anchor_alarm]" in out


# ── save_config: full integration (negative-shape on documented incident class) ───


def _write_config(path: Path, contents: str) -> None:
    path.write_text(contents.lstrip("\n"), encoding="utf-8")


def test_save_config_preserves_anchor_proportions(tmp_path):
    """[anchor.proportions] is the documented data-loss footgun. Must survive."""
    cfg_path = tmp_path / "config.toml"
    _write_config(cfg_path, """
[blogger]
"https://site.com" = "1"

[anchor.proportions]
branded = 0.65
partial = 0.20
exact = 0.05
lsi = 0.10
""")
    cfg = load_config(cfg_path)
    save_config(cfg, path=cfg_path, medium_token="tok")
    cfg2 = load_config(cfg_path)
    assert cfg2.anchor_proportions == {
        "branded": 0.65, "partial": 0.20, "exact": 0.05, "lsi": 0.10,
    }


def test_save_config_preserves_anchor_alarm_globals(tmp_path):
    """[anchor_alarm] (the section this PR just added) must survive save_config."""
    cfg_path = tmp_path / "config.toml"
    _write_config(cfg_path, """
[blogger]
"https://site.com" = "1"

[anchor_alarm]
entropy_floor = 1.8
exact_ratio_ceiling = 0.05
top3_concentration_ceiling = 0.20
""")
    cfg = load_config(cfg_path)
    save_config(cfg, path=cfg_path, medium_token="tok")
    cfg2 = load_config(cfg_path)
    assert cfg2.anchor_alarm.entropy_floor == 1.8
    assert cfg2.anchor_alarm.exact_ratio_ceiling == 0.05
    assert cfg2.anchor_alarm.top3_concentration_ceiling == 0.20


def test_save_config_preserves_anchor_alarm_overrides(tmp_path):
    cfg_path = tmp_path / "config.toml"
    _write_config(cfg_path, """
[blogger]
"https://site.com" = "1"

[anchor_alarm]
entropy_floor = 1.8

[[anchor_alarm.override]]
match = "high-risk.example.com"
scope = "domain"
exact_ratio_ceiling = 0.03

[[anchor_alarm.override]]
match = "https://other.com/page"
scope = "url"
top3_concentration_ceiling = 0.20
""")
    cfg = load_config(cfg_path)
    save_config(cfg, path=cfg_path, target_anchor_keywords={"x.com": ["kw"]})
    cfg2 = load_config(cfg_path)
    assert len(cfg2.anchor_alarm.overrides) == 2
    assert cfg2.anchor_alarm.overrides[0].match == "high-risk.example.com"
    assert cfg2.anchor_alarm.overrides[0].exact_ratio_ceiling == 0.03
    assert cfg2.anchor_alarm.overrides[1].match == "https://other.com/page"
    assert cfg2.anchor_alarm.overrides[1].top3_concentration_ceiling == 0.20


def test_save_config_preserves_llm_anchor_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_LLM_API_KEY", "sk-test-llm-key")
    cfg_path = tmp_path / "config.toml"
    _write_config(cfg_path, """
[blogger]
"https://site.com" = "1"

[llm.anchor_provider]
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
timeout_s = 30
""")
    save_config(load_config(cfg_path), path=cfg_path, medium_token="x")
    cfg2 = load_config(cfg_path)
    assert cfg2.llm_anchor_provider is not None
    assert cfg2.llm_anchor_provider.base_url == "https://api.openai.com/v1"
    assert cfg2.llm_anchor_provider.model == "gpt-4o-mini"


def test_save_config_preserves_sites_deep_tables(tmp_path):
    cfg_path = tmp_path / "config.toml"
    _write_config(cfg_path, """
[blogger]
"https://51acgs.com" = "12345"

[sites."51acgs.com".url_categories]
home = "https://51acgs.com/"
hot = "https://51acgs.com/hot"

[sites."51acgs.com".anchor_pools.home]
branded = ["51漫画", "51acgs"]
""")
    save_config(load_config(cfg_path), path=cfg_path)
    cfg2 = load_config(cfg_path)
    # site_url_categories preserved (config parser normalizes the key)
    assert "51acgs.com" in cfg2.site_url_categories
    assert cfg2.site_url_categories["51acgs.com"]["home"] == "https://51acgs.com/"
    # anchor_pools_v2 preserved
    assert "51acgs.com" in cfg2.target_anchor_pools_v2
    assert cfg2.target_anchor_pools_v2["51acgs.com"]["home"]["branded"] == [
        "51漫画", "51acgs",
    ]


def test_save_config_preserves_comments_in_unknown_sections(tmp_path):
    cfg_path = tmp_path / "config.toml"
    _write_config(cfg_path, """
[blogger]
"x.com" = "1"

[anchor_alarm]
# Operator note: tuned 2026-Q2 after anchor audit
entropy_floor = 1.8
# This ceiling intentionally tight
exact_ratio_ceiling = 0.05
""")
    save_config(load_config(cfg_path), path=cfg_path)
    raw_after = cfg_path.read_text(encoding="utf-8")
    assert "# Operator note: tuned 2026-Q2 after anchor audit" in raw_after
    assert "# This ceiling intentionally tight" in raw_after


# ── snapshot history ────────────────────────────────────────────────────────


def test_save_config_creates_snapshot_on_pre_existing_file(tmp_path):
    cfg_path = tmp_path / "config.toml"
    original = '[blogger]\n"x.com" = "before"\n'
    cfg_path.write_text(original, encoding="utf-8")

    save_config(load_config(cfg_path), path=cfg_path, medium_token="newtok")

    snapshot_dir = tmp_path / ".config-history"
    assert snapshot_dir.is_dir()
    snaps = list(snapshot_dir.glob("*.toml"))
    assert len(snaps) == 1
    # Snapshot contents are pre-save bytes
    assert snaps[0].read_text(encoding="utf-8") == original


def test_save_config_no_snapshot_when_file_does_not_exist(tmp_path):
    cfg_path = tmp_path / "config.toml"
    # File does NOT exist yet
    cfg = load_config(cfg_path)  # returns empty Config
    save_config(cfg, path=cfg_path, medium_token="firsttok")

    snapshot_dir = tmp_path / ".config-history"
    # Either no dir created or empty dir
    assert (not snapshot_dir.exists()) or list(snapshot_dir.glob("*.toml")) == []


def test_save_config_rotates_snapshots_at_cap(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[blogger]\n"x" = "0"\n', encoding="utf-8")

    # Save N+5 times where N = _CONFIG_HISTORY_MAX = 20
    n_saves = _CONFIG_HISTORY_MAX + 5
    for i in range(n_saves):
        # Force unique mtimes by writing a fresh original each loop, then save
        cfg_path.write_text(
            f'[blogger]\n"x" = "{i}"\n', encoding="utf-8",
        )
        save_config(load_config(cfg_path), path=cfg_path)

    snaps = list((tmp_path / ".config-history").glob("*.toml"))
    assert len(snaps) == _CONFIG_HISTORY_MAX


def test_snapshot_failure_does_not_block_save(tmp_path, capsys):
    """If snapshot fails (e.g. dir unwritable), main save still succeeds."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[blogger]\n"x.com" = "1"\n', encoding="utf-8")

    # Patch _snapshot_config indirectly via patching write_bytes on the snapshot path
    with patch.object(Path, "write_bytes", side_effect=OSError("disk full")) as wb:
        # We need to avoid the patch affecting the atomic write of config.toml itself
        # — atomic write uses write_text, not write_bytes. So only the snapshot path hits this.
        save_config(load_config(cfg_path), path=cfg_path, medium_token="newtok")

    # Main save still landed
    cfg2 = load_config(cfg_path)
    assert cfg2.medium_integration_token == "newtok"


# ── atomic write ────────────────────────────────────────────────────────────


def test_atomic_write_creates_new_file(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg = load_config(cfg_path)  # empty Config (file absent)
    save_config(cfg, path=cfg_path, medium_token="fresh")
    assert cfg_path.exists()
    # No .new orphan left behind
    assert not (tmp_path / "config.toml.new").exists()


def test_atomic_write_replaces_atomically(tmp_path):
    """Multiple saves never leave a torn .new alongside config.toml."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[blogger]\n"a.com" = "1"\n', encoding="utf-8")
    cfg = load_config(cfg_path)
    for tok in ("t1", "t2", "t3", "t4"):
        save_config(cfg, path=cfg_path, medium_token=tok)
        assert not (tmp_path / "config.toml.new").exists()
    cfg_final = load_config(cfg_path)
    assert cfg_final.medium_integration_token == "t4"


def test_rename_failure_leaves_original_untouched(tmp_path):
    cfg_path = tmp_path / "config.toml"
    original = '[blogger]\n"x.com" = "preserve-me"\n'
    cfg_path.write_text(original, encoding="utf-8")

    # Make Path.replace raise — original must survive
    with patch.object(Path, "replace", side_effect=OSError("simulated rename fail")):
        with pytest.raises(OSError):
            save_config(load_config(cfg_path), path=cfg_path, medium_token="newtok")

    # Original config bytes untouched
    assert cfg_path.read_text(encoding="utf-8") == original


# ── combined: the full "operator workflow" canary ───────────────────────────


def test_full_workflow_anchor_alarm_survives_oauth_token_refresh(tmp_path):
    """The exact scenario that motivated this PR: operator tunes [anchor_alarm],
    OAuth refresh writes the Blogger token, [anchor_alarm] must survive.
    """
    cfg_path = tmp_path / "config.toml"
    _write_config(cfg_path, """
[blogger]
"https://51acgs.com" = "blog-id-1"

[blogger.oauth]
client_id     = "id"
client_secret = "secret"

[medium]
integration_token = "old-medium-token"

[anchor_alarm]
entropy_floor = 1.9
exact_ratio_ceiling = 0.03

[[anchor_alarm.override]]
match = "money.51acgs.com"
scope = "domain"
top3_concentration_ceiling = 0.15
""")
    cfg = load_config(cfg_path)
    # Simulate OAuth refresh writing a new Blogger client_secret
    save_config(
        cfg,
        path=cfg_path,
        blogger_client_id="new-id",
        blogger_client_secret="new-secret",
    )

    cfg2 = load_config(cfg_path)
    # OAuth refresh landed
    assert cfg2.blogger_oauth is not None
    assert cfg2.blogger_oauth.client_id == "new-id"
    assert cfg2.blogger_oauth.client_secret == "new-secret"
    # AND the alarm config survived intact
    assert cfg2.anchor_alarm.entropy_floor == 1.9
    assert cfg2.anchor_alarm.exact_ratio_ceiling == 0.03
    assert len(cfg2.anchor_alarm.overrides) == 1
    assert cfg2.anchor_alarm.overrides[0].match == "money.51acgs.com"
    assert cfg2.anchor_alarm.overrides[0].top3_concentration_ceiling == 0.15
