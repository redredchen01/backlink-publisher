"""Tests for backlink_publisher.anchor_metrics — pure deterministic distribution math."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from backlink_publisher.anchor.metrics import (
    TargetThresholds,
    WindowMetrics,
    compute_window_metrics,
    detect_breaches,
    exact_match_ratio,
    filter_window,
    group_by_target_url,
    normalize,
    shannon_entropy,
    top_n_concentration,
)
from backlink_publisher.anchor.profile import ProfileEntry, ProfileState


# ── normalize ───────────────────────────────────────────────────────────────


def test_normalize_lowercase_strip_collapse_whitespace():
    assert normalize("iPhone Repair") == "iphone repair"
    assert normalize("  iPhone   Repair  ") == "iphone repair"
    assert normalize("IPHONE\tREPAIR") == "iphone repair"


def test_normalize_does_not_strip_punctuation():
    """Brand-variant preservation. 'Lyft, Inc.' and 'Lyft Inc' MUST remain distinct."""
    assert normalize("Lyft, Inc.") == "lyft, inc."
    assert normalize("Lyft Inc.") == "lyft inc."
    assert normalize("Lyft Inc.") != normalize("Lyft, Inc.")
    # Apostrophes too
    assert normalize("O'Reilly") == "o'reilly"
    # Bang preservation
    assert normalize("Yahoo!") == "yahoo!"
    # Brand suffix differentiation
    assert normalize(".NET") != normalize("net")


def test_normalize_does_not_fold_diacritics():
    """'café' and 'cafe' are intentional variants, not the same anchor."""
    assert normalize("Café") != normalize("Cafe")


# ── shannon_entropy ─────────────────────────────────────────────────────────


def _entry(text: str, *, ty: str = "branded") -> ProfileEntry:
    return ProfileEntry(
        ts="2026-05-14T00:00:00+00:00",
        link_role="main",
        url_category="home",
        anchor_type=ty,
        anchor_text=text,
    )


def test_entropy_uniform_distribution():
    """4 distinct equally-frequent anchors → entropy = log2(4) = 2.0."""
    entries = [_entry("a"), _entry("b"), _entry("c"), _entry("d")]
    assert shannon_entropy(entries) == 2.0


def test_entropy_all_same_anchor_is_zero():
    """Maximum concentration → entropy = 0."""
    entries = [_entry("same")] * 10
    assert shannon_entropy(entries) == 0.0


def test_entropy_empty_list_is_zero():
    assert shannon_entropy([]) == 0.0


def test_entropy_normalizes_anchor_text():
    """Case + whitespace variants collapse to one bucket → low entropy."""
    entries = [
        _entry("iPhone Repair"),
        _entry("iphone repair"),
        _entry("IPHONE  REPAIR"),
        _entry("iPhone Repair  "),
    ]
    # After normalization all 4 are the same → entropy 0
    assert shannon_entropy(entries) == 0.0


def test_entropy_rounded_to_4_decimals():
    """Float drift mitigation — result has at most 4 decimal places."""
    # 7 distinct anchors → entropy ≈ 2.8074 (slightly irrational)
    entries = [_entry(f"text_{i}") for i in range(7)]
    result = shannon_entropy(entries)
    # Verify rounded representation
    assert result == round(result, 4)


# ── exact_match_ratio ────────────────────────────────────────────────────────


def test_exact_ratio_all_exact_is_one():
    entries = [_entry(f"t{i}", ty="exact") for i in range(5)]
    assert exact_match_ratio(entries) == 1.0


def test_exact_ratio_mixed():
    entries = [
        _entry("a", ty="exact"),
        _entry("b", ty="exact"),
        _entry("c", ty="exact"),
        _entry("d", ty="branded"),
        _entry("e", ty="partial"),
        _entry("f", ty="branded"),
        _entry("g", ty="lsi"),
        _entry("h", ty="branded"),
        _entry("i", ty="partial"),
        _entry("j", ty="lsi"),
    ]
    # 3 exact out of 10 → 0.3
    assert exact_match_ratio(entries) == 0.3


def test_exact_ratio_empty_list_is_zero():
    assert exact_match_ratio([]) == 0.0


def test_exact_ratio_ignores_normalization():
    """Uses anchor_type field, not text — case-mangled anchors still count."""
    entries = [_entry("X y z", ty="exact"), _entry("a B c", ty="branded")]
    assert exact_match_ratio(entries) == 0.5


# ── top_n_concentration ─────────────────────────────────────────────────────


def test_top_n_excludes_branded_by_default():
    """Branded anchors are filtered before computing top-N."""
    # 5 non-branded entries, top-3 are 'a' (3x), 'b' (1x), 'c' (1x)
    entries = [
        _entry("a", ty="exact"),
        _entry("a", ty="exact"),
        _entry("a", ty="exact"),
        _entry("b", ty="partial"),
        _entry("c", ty="lsi"),
        # Branded — excluded
        _entry("brand", ty="branded"),
        _entry("brand", ty="branded"),
        _entry("brand", ty="branded"),
    ]
    result = top_n_concentration(entries)
    assert result is not None
    # All 5 non-branded counted in top-3 → 5/5 = 1.0
    assert result == 1.0


def test_top_n_degraded_signal_under_5_non_branded():
    """< 5 non-branded entries → None (caller skips top-N breach)."""
    entries = [
        _entry("a", ty="partial"),
        _entry("b", ty="partial"),
        _entry("brand1", ty="branded"),
        _entry("brand2", ty="branded"),
        _entry("brand3", ty="branded"),
    ]
    assert top_n_concentration(entries) is None


def test_top_n_normalizes_text():
    """Variants of the same anchor (after casefold + whitespace) collapse."""
    entries = [
        _entry("iPhone Repair", ty="exact"),
        _entry("iphone repair", ty="exact"),
        _entry("IPHONE  REPAIR", ty="exact"),
        _entry("other one", ty="partial"),
        _entry("other two", ty="partial"),
    ]
    result = top_n_concentration(entries, n=1)
    assert result is not None
    # Top-1 normalized: 'iphone repair' appears 3 times out of 5 non-branded
    assert result == 0.6


# ── filter_window ───────────────────────────────────────────────────────────


def _entry_at(ts: datetime, *, ty: str = "branded", text: str = "x") -> ProfileEntry:
    return ProfileEntry(
        ts=ts.isoformat(),
        link_role="main",
        url_category="home",
        anchor_type=ty,
        anchor_text=text,
    )


def test_filter_window_includes_only_recent():
    now = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
    entries = [
        _entry_at(now - timedelta(days=100), text="old"),
        _entry_at(now - timedelta(days=89), text="just_in"),
        _entry_at(now - timedelta(days=29), text="recent"),
        _entry_at(now - timedelta(hours=1), text="now"),
    ]
    in_30d = filter_window(entries, days=30, now=now)
    in_90d = filter_window(entries, days=90, now=now)
    assert {e.anchor_text for e in in_30d} == {"recent", "now"}
    assert {e.anchor_text for e in in_90d} == {"just_in", "recent", "now"}


def test_filter_window_drops_malformed_ts():
    now = datetime(2026, 5, 14, tzinfo=timezone.utc)
    good = _entry_at(now, text="good")
    bad = ProfileEntry(
        ts="not-an-iso-date",
        link_role="main",
        url_category="home",
        anchor_type="branded",
        anchor_text="bad",
    )
    naive = ProfileEntry(
        ts="2026-05-14T00:00:00",  # no tzinfo
        link_role="main",
        url_category="home",
        anchor_type="branded",
        anchor_text="naive",
    )
    result = filter_window([good, bad, naive], days=90, now=now)
    assert {e.anchor_text for e in result} == {"good"}


# ── group_by_target_url ─────────────────────────────────────────────────────


def test_group_by_target_url_partitions_correctly():
    state = ProfileState(
        entries=[
            ProfileEntry(ts="2026-05-14T00:00:00+00:00", link_role="main",
                         url_category="home", anchor_type="branded",
                         anchor_text="a", target_url="https://x.com/a"),
            ProfileEntry(ts="2026-05-14T00:00:00+00:00", link_role="main",
                         url_category="home", anchor_type="branded",
                         anchor_text="b", target_url="https://x.com/b"),
            ProfileEntry(ts="2026-05-14T00:00:00+00:00", link_role="main",
                         url_category="home", anchor_type="branded",
                         anchor_text="c", target_url=""),  # pre-bump
        ]
    )
    grouped = group_by_target_url(state)
    assert set(grouped.keys()) == {"https://x.com/a", "https://x.com/b", ""}
    assert len(grouped[""]) == 1


# ── compute_window_metrics ──────────────────────────────────────────────────


def test_compute_window_metrics_bundles_all_three():
    entries = [_entry(f"t{i}", ty="exact") for i in range(10)]
    m = compute_window_metrics(entries)
    assert isinstance(m, WindowMetrics)
    assert m.sample_size == 10
    assert m.exact_ratio == 1.0
    assert m.entropy > 0  # 10 distinct anchors


# ── detect_breaches: NEGATIVE-SHAPE TESTS ───────────────────────────────────
# These are load-bearing per feedback_test-locks-in-bug.md — a future
# tautological-gate regression would be caught here when a synthesized
# breach fixture stops producing a breach.


def test_breach_detection_fires_on_exact_ratio_breach():
    """Synthesize 25 entries with 60% exact-match (well above 10% ceiling).
    Defaults must produce a non-empty breach list — this is the 'gate is
    not tautological' assertion."""
    entries = [_entry(f"e{i}", ty="exact") for i in range(15)] + [
        _entry(f"b{i}", ty="branded") for i in range(10)
    ]
    m = compute_window_metrics(entries)
    assert m.sample_size == 25
    breaches = detect_breaches(m, TargetThresholds())
    assert "exact_ratio_ceiling" in breaches


def test_breach_detection_fires_on_entropy_floor():
    """All-same-anchor → entropy 0 → breach the 1.5 floor."""
    entries = [_entry("same anchor", ty="partial") for _ in range(20)]
    m = compute_window_metrics(entries)
    breaches = detect_breaches(m, TargetThresholds())
    assert "entropy_floor" in breaches


def test_breach_detection_fires_on_top3_concentration():
    """20 non-branded entries, top-3 dominating → top-3 > 25%."""
    entries = (
        [_entry("phrase_a", ty="partial") for _ in range(8)]
        + [_entry("phrase_b", ty="partial") for _ in range(5)]
        + [_entry("phrase_c", ty="lsi") for _ in range(3)]
        + [_entry(f"long_tail_{i}", ty="lsi") for i in range(4)]
    )
    m = compute_window_metrics(entries)
    # top-3 = (8+5+3)/20 = 0.8 → breaches ceiling 0.25
    breaches = detect_breaches(m, TargetThresholds())
    assert "top3_concentration_ceiling" in breaches


def test_breach_suppressed_below_sample_floor():
    """Even with severe over-optimization, sample < 20 → no breach."""
    entries = [_entry(f"e{i}", ty="exact") for i in range(10)]
    m = compute_window_metrics(entries)
    assert m.sample_size == 10
    assert detect_breaches(m, TargetThresholds()) == []


def test_breach_suppressed_on_healthy_distribution():
    """Realistic Safe-SEO distribution → empty breach list.

    Distinct non-branded anchors with low concentration: 8 partial + 8 lsi =
    16 unique non-branded texts. Top-3 over 16 = 3/16 ≈ 0.19, below 0.25
    ceiling. Entropy is high because anchors are all distinct. Exact-ratio
    is 0 (no exact-match anchors). Sample = 40, well above 20 floor.
    """
    entries = (
        [_entry(f"branded_{i}", ty="branded") for i in range(24)]
        + [_entry(f"partial_{i}", ty="partial") for i in range(8)]
        + [_entry(f"lsi_{i}", ty="lsi") for i in range(8)]
    )
    m = compute_window_metrics(entries)
    breaches = detect_breaches(m, TargetThresholds())
    assert breaches == []


def test_breach_detection_respects_per_target_thresholds():
    """Custom thresholds override defaults."""
    entries = [_entry(f"e{i}", ty="exact") for i in range(15)] + [
        _entry(f"b{i}", ty="branded") for i in range(10)
    ]
    m = compute_window_metrics(entries)
    # Default ceiling 0.10 → breach (60% exact). Loose threshold 0.80 → no breach.
    strict = TargetThresholds()
    loose = TargetThresholds(exact_ratio_ceiling=0.80)
    assert "exact_ratio_ceiling" in detect_breaches(m, strict)
    assert "exact_ratio_ceiling" not in detect_breaches(m, loose)


def test_breach_detection_skips_top3_when_degraded():
    """Even with skewed distribution, < 5 non-branded → top-3 skip."""
    entries = (
        [_entry("a", ty="partial") for _ in range(2)]
        + [_entry("b", ty="lsi") for _ in range(2)]
        + [_entry(f"brand{i}", ty="branded") for i in range(20)]
    )
    m = compute_window_metrics(entries)
    # 4 non-branded → top_n returns None
    assert m.top_n_non_branded is None
    breaches = detect_breaches(m, TargetThresholds())
    assert "top3_concentration_ceiling" not in breaches


# ── float-drift edge near threshold ─────────────────────────────────────────


def test_entropy_round_does_not_create_phantom_breaches():
    """A value of 1.50001 rounds to 1.5 and is NOT below the floor of 1.5."""
    # Synthesize a distribution producing entropy near 1.5 — 3 distinct
    # anchors with very skewed counts. We just check the math machinery,
    # not the exact frequency that produces 1.5.
    entries = [_entry("a", ty="partial")] * 10 + [_entry("b", ty="partial")] * 3 + [_entry("c", ty="lsi")] * 1
    m = compute_window_metrics(entries)
    # entropy with (10, 3, 1) = -((10/14)*log2(10/14) + (3/14)*log2(3/14) + (1/14)*log2(1/14)) ≈ 1.149
    # Below 1.5 → would breach. This test just confirms round() doesn't accidentally pass.
    expected = -(
        (10 / 14) * math.log2(10 / 14)
        + (3 / 14) * math.log2(3 / 14)
        + (1 / 14) * math.log2(1 / 14)
    )
    assert m.entropy == round(expected, 4)
