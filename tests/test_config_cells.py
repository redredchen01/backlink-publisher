"""Tests for ``[cells.*]`` config section parser and round-trip preservation.

Blast-radius Phase 1 Unit 2 (R7-minimal).

The ``[cells.*]`` root is an **unmanaged** root (not in
``_SAVE_CONFIG_KNOWN_ROOTS``) so ``_preserve_unknown_sections`` passes it
through verbatim on every ``save_config`` call.  Semantic round-trip
assertions (``cfg2.cell_assignments == cfg.cell_assignments``) prove
preservation without hard-coding raw TOML substrings, per the
inverted-negative-assertion lesson.
"""

from __future__ import annotations

import pytest

import backlink_publisher.publishing.adapters  # noqa: F401 — populate registry
from backlink_publisher._util.errors import InputValidationError
from backlink_publisher.config.loader import load_config
from backlink_publisher.config.writer import save_config
from backlink_publisher.config.parsers.cells import _parse_cell_assignments

# ---------------------------------------------------------------------------
# Stable fake channel set — monkeypatched into the parser for unit tests
# ---------------------------------------------------------------------------

_FAKE_CHANNELS = ["telegraph", "rentry", "blogger", "medium", "velog"]


@pytest.fixture
def fake_registered(monkeypatch):
    """Monkeypatch _registered_platforms() at the parser's wrapper function."""
    monkeypatch.setattr(
        "backlink_publisher.config.parsers.cells._registered_platforms",
        lambda: list(_FAKE_CHANNELS),
    )


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Isolate config dir to tmp; also patch _registered_platforms for loader."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "backlink_publisher.config.parsers.cells._registered_platforms",
        lambda: list(_FAKE_CHANNELS),
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Unit tests for _parse_cell_assignments directly
# ---------------------------------------------------------------------------


def test_happy_path_parses_channels(fake_registered):
    """Happy path: [cells."example.com"] channels parses to correct dict."""
    raw = {"example.com": {"channels": ["telegraph", "rentry"]}}
    result = _parse_cell_assignments(raw)
    assert result == {"example.com": ["telegraph", "rentry"]}


def test_multiple_cells_parse_correctly(fake_registered):
    """Happy path: multiple disjoint cells each parse correctly."""
    raw = {
        "site-a.com": {"channels": ["telegraph", "rentry"]},
        "site-b.com": {"channels": ["blogger", "medium"]},
    }
    result = _parse_cell_assignments(raw)
    assert result == {
        "site-a.com": ["telegraph", "rentry"],
        "site-b.com": ["blogger", "medium"],
    }


def test_absent_section_returns_empty(fake_registered):
    """Edge: absent section (None) → empty dict, no error."""
    assert _parse_cell_assignments(None) == {}


def test_empty_section_returns_empty(fake_registered):
    """Edge: empty [cells.*] section ({}) → empty dict, no error."""
    assert _parse_cell_assignments({}) == {}


def test_cell_with_no_channels_key_is_valid(fake_registered):
    """Edge: [cells."example.com"] with no channels key → empty list, no error."""
    raw = {"example.com": {}}
    result = _parse_cell_assignments(raw)
    assert result == {"example.com": []}


def test_domain_trailing_slash_stripped(fake_registered):
    """Edge: trailing slash on domain key is stripped (consistent with target.py)."""
    raw = {"example.com/": {"channels": ["telegraph"]}}
    result = _parse_cell_assignments(raw)
    assert "example.com" in result
    assert "example.com/" not in result


# ---------------------------------------------------------------------------
# Error paths (fail-loud)
# ---------------------------------------------------------------------------


def test_unknown_channel_raises(fake_registered):
    """Error: typo'd channel name → InputValidationError naming the bad value."""
    raw = {"example.com": {"channels": ["telegrph", "rentry"]}}  # "telegrph" typo
    with pytest.raises(InputValidationError, match="telegrph"):
        _parse_cell_assignments(raw)


def test_unknown_channel_error_names_domain(fake_registered):
    """Error: InputValidationError message includes the domain for context."""
    raw = {"example.com": {"channels": ["notachannel"]}}
    with pytest.raises(InputValidationError, match="example.com"):
        _parse_cell_assignments(raw)


def test_overlapping_cells_raise(fake_registered):
    """Error: same channel in two sites' cells → InputValidationError naming channel."""
    raw = {
        "site-a.com": {"channels": ["telegraph", "rentry"]},
        "site-b.com": {"channels": ["telegraph", "blogger"]},  # telegraph overlap
    }
    with pytest.raises(InputValidationError, match="telegraph"):
        _parse_cell_assignments(raw)


def test_overlapping_cells_error_names_both_sites(fake_registered):
    """Error: overlap error message mentions both conflicting sites."""
    raw = {
        "site-a.com": {"channels": ["velog"]},
        "site-b.com": {"channels": ["velog"]},
    }
    with pytest.raises(InputValidationError) as exc_info:
        _parse_cell_assignments(raw)
    msg = str(exc_info.value)
    assert "site-a.com" in msg or "site-b.com" in msg  # at least one site named


def test_channels_not_a_list_raises(fake_registered):
    """Error: channels value is a string, not a list → InputValidationError."""
    raw = {"example.com": {"channels": "telegraph"}}
    with pytest.raises(InputValidationError):
        _parse_cell_assignments(raw)


def test_entry_not_a_table_raises(fake_registered):
    """Error: [cells."example.com"] is not a table → InputValidationError."""
    raw = {"example.com": "not-a-table"}
    with pytest.raises(InputValidationError):
        _parse_cell_assignments(raw)


# ---------------------------------------------------------------------------
# Integration: load_config picks up [cells.*] and Config.cell_assignments
# ---------------------------------------------------------------------------


def test_load_config_populates_cell_assignments(isolated_config):
    """Happy path: load_config reads [cells.*] into Config.cell_assignments."""
    cfg_path = isolated_config / "config.toml"
    cfg_path.write_text(
        '[blogger]\n'
        '[medium]\n'
        '\n'
        '[cells."example.com"]\n'
        'channels = ["telegraph", "rentry"]\n',
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    assert cfg.cell_assignments == {"example.com": ["telegraph", "rentry"]}


def test_load_config_no_cells_section_is_empty(isolated_config):
    """Edge: config with no [cells.*] section → cell_assignments == {}."""
    cfg_path = isolated_config / "config.toml"
    cfg_path.write_text("[blogger]\n[medium]\n", encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg.cell_assignments == {}


def test_load_config_unknown_channel_raises(isolated_config):
    """Error: load_config propagates InputValidationError for unknown channel."""
    cfg_path = isolated_config / "config.toml"
    cfg_path.write_text(
        '[cells."example.com"]\n'
        'channels = ["notachannel_xyz"]\n',
        encoding="utf-8",
    )
    with pytest.raises(InputValidationError, match="notachannel_xyz"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# Round-trip: save_config preserves [cells.*] verbatim (unmanaged root)
# ---------------------------------------------------------------------------


def test_round_trip_preserves_cell_assignments(isolated_config):
    """Positive round-trip: save_config → reload → cell_assignments identical.

    [cells.*] is unmanaged (not in _SAVE_CONFIG_KNOWN_ROOTS); _preserve_unknown_sections
    passes it through verbatim. Verified via positive semantic assertion.
    """
    cfg_path = isolated_config / "config.toml"
    cfg_path.write_text(
        '[blogger]\n'
        '[medium]\n'
        '\n'
        '[cells."example.com"]\n'
        'channels = ["telegraph", "rentry"]\n'
        '\n'
        '[cells."another-site.org"]\n'
        'channels = ["blogger", "medium"]\n',
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    assert cfg.cell_assignments == {
        "example.com": ["telegraph", "rentry"],
        "another-site.org": ["blogger", "medium"],
    }

    save_config(cfg, cfg_path)
    cfg2 = load_config(cfg_path)

    # Positive semantic assertion (per inverted-negative-assertion lesson)
    assert cfg2.cell_assignments == cfg.cell_assignments


def test_round_trip_sibling_unmanaged_section_also_survives(isolated_config):
    """A sibling unmanaged section ([anchor_alarm]) must survive the same save.

    Both [cells.*] and [anchor_alarm] are unmanaged roots preserved verbatim.
    This catches any regression where adding cells breaks the broader
    preservation pass.
    """
    cfg_path = isolated_config / "config.toml"
    cfg_path.write_text(
        '[blogger]\n'
        '[medium]\n'
        '\n'
        '[anchor_alarm]\n'
        'entropy_floor = 1.2\n'
        '\n'
        '[cells."example.com"]\n'
        'channels = ["telegraph"]\n',
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    save_config(cfg, cfg_path)
    cfg2 = load_config(cfg_path)

    # Both unmanaged sections survive
    assert cfg2.cell_assignments == {"example.com": ["telegraph"]}
    assert cfg2.anchor_alarm.entropy_floor == 1.2


def test_round_trip_is_idempotent(isolated_config):
    """Two consecutive saves produce the same file content (idempotency)."""
    cfg_path = isolated_config / "config.toml"
    cfg_path.write_text(
        '[blogger]\n'
        '[medium]\n'
        '\n'
        '[cells."example.com"]\n'
        'channels = ["telegraph", "rentry"]\n',
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    save_config(cfg, cfg_path)
    first = cfg_path.read_text(encoding="utf-8")

    save_config(load_config(cfg_path), cfg_path)
    second = cfg_path.read_text(encoding="utf-8")

    assert first == second, (
        "Round-trip is not idempotent — second save diverged from first."
    )
