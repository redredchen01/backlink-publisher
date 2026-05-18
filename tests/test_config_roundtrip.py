"""Characterization tests for config load/save round-trip — Plan 2026-05-18 Unit 5.

Locks current behavior of ``load_config`` / ``save_config`` so the Unit 5
split of ``config.py`` into a ``config/`` subpackage can be verified to
not drift. Per Plan D5 + the institutional history:

  docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md

``save_config`` once silently dropped ``[sites.*]`` and ``[anchor.proportions]``
sections for weeks while all unit tests stayed green. This file exercises
the contract end-to-end on real TOML fixtures so any future refactor that
breaks the round-trip is caught immediately, not by a user.

Strategy
--------
- Use the shipped ``config.example.toml`` as the full-spectrum fixture
  (~267 lines, every documented section).
- Each test runs against the *public* API only (``Config`` / ``load_config``
  / ``save_config``). The split must preserve this contract verbatim — the
  ``backlink_publisher.config`` import path is part of the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backlink_publisher.config import Config, load_config, save_config


EXAMPLE_TOML_PATH = (
    Path(__file__).resolve().parent.parent / "config.example.toml"
)


@pytest.fixture
def example_toml(tmp_path) -> Path:
    """Copy the shipped example to a tmp dir so save_config writes are isolated."""
    target = tmp_path / "config.toml"
    target.write_text(
        EXAMPLE_TOML_PATH.read_text(encoding="utf-8"), encoding="utf-8",
    )
    return target


# ─── Characterization: example.toml hydrates a real Config ──────────────────


def test_example_toml_loads_into_config(example_toml):
    """The shipped example.toml parses without raising and produces a Config."""
    cfg = load_config(example_toml)
    assert isinstance(cfg, Config)
    # Blogger map present (example shows two entries)
    assert cfg.blogger_blog_ids, (
        "config.example.toml should populate blogger_blog_ids from [blogger]"
    )
    # Blogger OAuth section present
    assert cfg.blogger_oauth is not None
    assert cfg.blogger_oauth.client_id


# ─── Round-trip: in-place save_config preserves every section ──────────────


def test_save_config_inplace_preserves_all_sections(example_toml):
    """load → save_config(no kwargs) → load yields an equal Config.

    This is the canonical sneaky-bug guard: save_config with no overrides
    must not drop any section the loader can see. If a section is silently
    stripped during write (the 2026-05-14 regression), the second load
    produces a different Config and this test fails.
    """
    cfg_before = load_config(example_toml)
    save_config(cfg_before, path=example_toml)
    cfg_after = load_config(example_toml)
    assert cfg_after == cfg_before


def test_save_config_inplace_preserves_sections_with_keyvalue_data(example_toml):
    """Top-level sections that carry at least one key=value pair must
    survive save_config in the rewritten file.

    Pure-placeholder sections (header + comments only, no live data) are
    documented as not preserved — that is _preserve_unknown_sections'
    intentional behavior, not a regression. We only assert preservation
    for sections the loader could actually extract data from.
    """
    def _sections_with_data(text: str) -> set[str]:
        sections: dict[str, bool] = {}
        current: str | None = None
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("[") and line.endswith("]") and not line.startswith("[["):
                current = line
                sections.setdefault(current, False)
            elif current and "=" in line and not line.startswith("#"):
                sections[current] = True
        return {s for s, has_data in sections.items() if has_data}

    original_data_sections = _sections_with_data(
        example_toml.read_text(encoding="utf-8"),
    )
    cfg = load_config(example_toml)
    save_config(cfg, path=example_toml)
    rewritten_data_sections = _sections_with_data(
        example_toml.read_text(encoding="utf-8"),
    )

    missing = original_data_sections - rewritten_data_sections
    assert not missing, (
        f"save_config dropped sections that carried key=value data: "
        f"{sorted(missing)}. See docs/solutions/test-failures/"
        f"inverted-negative-assertion-enshrined-config-save-data-loss-"
        f"2026-05-14.md"
    )


# ─── Round-trip: unknown future section survives ───────────────────────────


def test_save_config_preserves_unknown_top_level_section(tmp_path):
    """A TOML with an unknown [future_feature] table must survive save_config.

    Exercises the _preserve_unknown_sections path end-to-end via the public
    API (not as an internal-function test like tests/test_config_safety_net.py
    already does).
    """
    src = tmp_path / "config.toml"
    src.write_text(
        '[blogger]\n'
        '"https://example.com" = "111"\n'
        '\n'
        '[future_feature]\n'
        'will_become_supported = true\n'
        'note = "do not silently strip"\n',
        encoding="utf-8",
    )
    cfg = load_config(src)
    save_config(cfg, path=src)
    text = src.read_text(encoding="utf-8")

    assert "[future_feature]" in text, (
        "save_config silently dropped unknown top-level section"
    )
    assert "will_become_supported" in text
    assert "do not silently strip" in text


# ─── Public-API surface lock ────────────────────────────────────────────────


def test_public_api_imports_remain_stable():
    """The Unit 5 split must keep the existing import path working verbatim.

    16 test files + the CLI entry points reach into
    ``backlink_publisher.config`` for these names. The refactor must keep
    them importable from the same module path (R6).
    """
    from backlink_publisher.config import (  # noqa: F401
        AnchorAlarmConfig,
        AnchorAlarmOverride,
        BloggerOAuthConfig,
        Config,
        LLMProviderConfig,
        MediumOAuthConfig,
        ThreeUrlConfig,
        get_anchor_keywords,
        get_anchor_pool_v2,
        get_three_url_config,
        load_blogger_token,
        load_config,
        load_medium_token,
        merge_site_url_categories,
        resolve_blog_id,
        save_blogger_token,
        save_config,
        save_medium_token,
        upgrade_target_to_threeurl,
    )
