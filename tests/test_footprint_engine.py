"""Engine-determinism tests for ``footprint.py`` (Plan Unit 1, R11).

These tests pin down the post-R11 contract: lex-smallest tie-break across every
``Counter.most_common(1)`` call site, plus ``SCHEMA_VERSION`` import.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections import Counter

from hypothesis import given, strategies as st

from backlink_publisher.footprint import (
    DEFAULT_THRESHOLD_ALARM_PCT,
    DEFAULT_THRESHOLD_DRIFT_PP,
    FootprintReport,
    SCHEMA_VERSION,
    THRESHOLD_OVERRIDES,
    _top_by_count_then_lex,
    analyze_corpus,
    format_report_markdown,
)


def test_schema_version_is_importable_and_int():
    """R11: SCHEMA_VERSION is a module-level constant the gate reads at import time."""
    assert isinstance(SCHEMA_VERSION, int)
    assert SCHEMA_VERSION >= 1


def test_default_thresholds_are_importable():
    """Plan Unit 4: thresholds live in footprint.py, not the test module."""
    assert isinstance(DEFAULT_THRESHOLD_DRIFT_PP, float)
    assert isinstance(DEFAULT_THRESHOLD_ALARM_PCT, float)
    assert isinstance(THRESHOLD_OVERRIDES, dict)
    assert 0 < DEFAULT_THRESHOLD_DRIFT_PP < 100
    assert 0 < DEFAULT_THRESHOLD_ALARM_PCT < 100


def test_top_by_count_then_lex_unique_counts():
    c = Counter({"b": 5, "a": 3, "c": 1})
    assert _top_by_count_then_lex(c, 1) == [("b", 5)]
    assert _top_by_count_then_lex(c, 3) == [("b", 5), ("a", 3), ("c", 1)]


def test_top_by_count_then_lex_tied_strings_picks_lex_smallest():
    c1 = Counter()
    c1["z"] = 2
    c1["a"] = 2
    c2 = Counter()
    c2["a"] = 2
    c2["z"] = 2
    assert _top_by_count_then_lex(c1, 1) == [("a", 2)]
    assert _top_by_count_then_lex(c2, 1) == [("a", 2)]


def test_top_by_count_then_lex_tied_tuples_uses_native_tuple_compare():
    c = Counter()
    c[("z", "href")] = 3
    c[("a", "href")] = 3
    c[("a", "rel")] = 3
    top = _top_by_count_then_lex(c, 3)
    assert top == [
        (("a", "href"), 3),
        (("a", "rel"), 3),
        (("z", "href"), 3),
    ]


def test_top_by_count_then_lex_empty_counter():
    assert _top_by_count_then_lex(Counter(), 1) == []
    assert _top_by_count_then_lex(Counter(), 3) == []


def test_top_rel_values_deterministic_on_ties():
    report_a = FootprintReport(total_links=4, total_payloads=2)
    report_a.rel_value_counts["z"] = 2
    report_a.rel_value_counts["a"] = 2
    report_b = FootprintReport(total_links=4, total_payloads=2)
    report_b.rel_value_counts["a"] = 2
    report_b.rel_value_counts["z"] = 2
    assert report_a.top_rel_values(1) == report_b.top_rel_values(1) == [("a", 2)]


def test_top_attr_order_deterministic_on_ties():
    report = FootprintReport(total_links=6, total_payloads=2)
    report.attr_order_counts[("z", "href", "rel")] = 3
    report.attr_order_counts[("a", "href", "rel")] = 3
    assert report.top_attr_order(1) == [(("a", "href", "rel"), 3)]


def test_concentration_pct_independent_of_insertion_order():
    report_a = FootprintReport(total_links=4, total_payloads=2)
    report_a.rel_value_counts["z"] = 2
    report_a.rel_value_counts["a"] = 2
    report_b = FootprintReport(total_links=4, total_payloads=2)
    report_b.rel_value_counts["a"] = 2
    report_b.rel_value_counts["z"] = 2
    assert report_a.concentration_pct("rel_value") == report_b.concentration_pct("rel_value") == 50.0


def test_concentration_pct_empty_dimension():
    report = FootprintReport(total_links=0, total_payloads=1, payloads_without_links=1)
    assert report.concentration_pct("rel_value") == 0.0
    assert report.concentration_pct("attr_order") == 0.0


def test_format_report_markdown_deterministic_on_tied_content():
    def _build(insert_z_first: bool) -> FootprintReport:
        rpt = FootprintReport(total_links=4, total_payloads=2)
        if insert_z_first:
            rpt.rel_value_counts["z"] = 2
            rpt.rel_value_counts["a"] = 2
            rpt.target_value_counts["_blank"] = 4
            rpt.preceding_char_counts[" "] = 2
            rpt.preceding_char_counts["."] = 2
            rpt.attr_order_counts[("z", "href")] = 2
            rpt.attr_order_counts[("a", "href")] = 2
        else:
            rpt.rel_value_counts["a"] = 2
            rpt.rel_value_counts["z"] = 2
            rpt.target_value_counts["_blank"] = 4
            rpt.preceding_char_counts["."] = 2
            rpt.preceding_char_counts[" "] = 2
            rpt.attr_order_counts[("a", "href")] = 2
            rpt.attr_order_counts[("z", "href")] = 2
        return rpt

    md_a = format_report_markdown(_build(insert_z_first=True))
    md_b = format_report_markdown(_build(insert_z_first=False))
    assert md_a == md_b


def test_analyze_corpus_unchanged_on_singleton_input():
    html = '<p>Read <a href="https://example.com" target="_blank" rel="noopener">more</a>.</p>'
    report = analyze_corpus([html])
    assert report.total_links == 1
    assert report.total_payloads == 1
    assert report.payloads_without_links == 0
    assert report.attr_order_counts[("href", "target", "rel")] == 1
    assert report.rel_value_counts["noopener"] == 1
    assert report.target_value_counts["_blank"] == 1
    assert report.preceding_char_counts[" "] == 1


def test_analyze_corpus_handles_no_links():
    report = analyze_corpus(["<p>No links here.</p>", ""])
    assert report.total_links == 0
    assert report.total_payloads == 2
    assert report.payloads_without_links == 2


@given(
    pairs=st.lists(
        st.tuples(st.text(min_size=1, max_size=8), st.integers(min_value=1, max_value=10)),
        min_size=1,
        max_size=12,
    )
)
def test_top_by_count_then_lex_is_permutation_invariant(pairs):
    """Property: shuffling insertion order of (key, count) pairs does not change top-N."""
    import random
    base = {k: v for k, v in pairs}
    keys = list(base.keys())
    c1 = Counter()
    for k in keys:
        c1[k] = base[k]
    shuffled = keys[:]
    random.Random(42).shuffle(shuffled)
    c2 = Counter()
    for k in shuffled:
        c2[k] = base[k]
    assert _top_by_count_then_lex(c1, len(base)) == _top_by_count_then_lex(c2, len(base))


def test_top_helper_stable_across_pythonhashseed_values(tmp_path):
    """Subprocess proof: helper output is byte-identical across hash seeds."""
    script = tmp_path / "probe.py"
    script.write_text(
        "from collections import Counter\n"
        "from backlink_publisher.footprint import _top_by_count_then_lex\n"
        "c = Counter()\n"
        "for k in ['z', 'a', 'm', 'b']:\n"
        "    c[k] = 2\n"
        "print(_top_by_count_then_lex(c, 4))\n",
        encoding="utf-8",
    )
    env_a = {**os.environ, "PYTHONHASHSEED": "0"}
    env_b = {**os.environ, "PYTHONHASHSEED": "12345"}
    r_a = subprocess.run([sys.executable, str(script)], env=env_a, text=True, capture_output=True, check=True)
    r_b = subprocess.run([sys.executable, str(script)], env=env_b, text=True, capture_output=True, check=True)
    assert r_a.stdout.strip() == r_b.stdout.strip()
    assert r_a.stdout.strip().startswith("[('a', 2), ('b', 2), ('m', 2), ('z', 2)]")
