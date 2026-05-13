"""Tests for report-anchors CLI."""

from __future__ import annotations

import json
import sys
from io import StringIO

import pytest

from backlink_publisher.cli.report_anchors import _build_report, _markdown_table, main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _payload(
    main_domain: str,
    url_mode: str = "A",
    anchors: tuple[str, str] = ("brand-kw", "head-kw"),
) -> dict:
    """Minimal payload dict for testing."""
    return {
        "main_domain": main_domain.rstrip("/") + "/",
        "url_mode": url_mode,
        "links": [
            {"kind": "main_domain", "url": main_domain.rstrip("/"), "anchor": anchors[0]},
            {"kind": "target", "url": main_domain.rstrip("/") + "/page", "anchor": anchors[1]},
            {"kind": "supporting", "url": "https://en.wikipedia.org", "anchor": "Wikipedia"},
        ],
    }


def _run_main(input_data: str, extra_args: list[str] | None = None) -> tuple[str, str, int]:
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    try:
        sys.stdin = StringIO(input_data)
        out = StringIO()
        err = StringIO()
        sys.stdout = out
        sys.stderr = err
        try:
            main(extra_args or [])
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr


# ---------------------------------------------------------------------------
# _build_report unit tests
# ---------------------------------------------------------------------------

def test_build_report_basic_two_domains():
    rows = [
        _payload("https://a.com"),
        _payload("https://b.org"),
        _payload("https://a.com", anchors=("other-kw", "head-kw")),
    ]
    stats = _build_report(rows)
    assert set(stats.keys()) == {"https://a.com", "https://b.org"}
    a = stats["https://a.com"]
    assert a["total_articles"] == 2
    assert a["anchors"]["brand-kw"] == 1
    assert a["anchors"]["head-kw"] == 2  # appears in both articles
    assert a["anchors"]["other-kw"] == 1


def test_build_report_supporting_links_excluded():
    rows = [_payload("https://a.com")]
    stats = _build_report(rows)
    # Wikipedia is a supporting link — must not appear in anchor counts
    assert "Wikipedia" not in stats["https://a.com"]["anchors"]


def test_build_report_fallback_detection():
    rows = [
        _payload("https://a.com", anchors=("a.com", "head-kw")),  # fallback anchor
        _payload("https://a.com", anchors=("brand", "head-kw")),   # not fallback
    ]
    stats = _build_report(rows)
    assert stats["https://a.com"]["fallback_count"] == 1


def test_build_report_all_fallback():
    rows = [
        _payload("https://site.com", anchors=("site.com", "site.com")),
        _payload("https://site.com", anchors=("site.com", "site.com")),
    ]
    stats = _build_report(rows)
    assert stats["https://site.com"]["fallback_count"] == 2
    assert stats["https://site.com"]["total_articles"] == 2


def test_build_report_empty_input():
    assert _build_report([]) == {}


def test_build_report_missing_links_field_skipped():
    rows = [{"main_domain": "https://a.com"}]  # no 'links' key
    stats = _build_report(rows)
    assert stats["https://a.com"]["total_articles"] == 1
    assert len(stats["https://a.com"]["anchors"]) == 0


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------

def test_cli_markdown_output_basic():
    row = json.dumps(_payload("https://a.com"))
    stdout, _, code = _run_main(row)
    assert code == 0
    assert "https://a.com" in stdout
    assert "|" in stdout  # markdown table
    assert "brand-kw" in stdout


def test_cli_json_flag():
    row = json.dumps(_payload("https://a.com"))
    stdout, _, code = _run_main(row, ["--json"])
    assert code == 0
    data = json.loads(stdout)
    assert "https://a.com" in data
    entry = data["https://a.com"]
    assert entry["total_articles"] == 1
    assert "brand-kw" in entry["anchors"]


def test_cli_empty_input():
    stdout, stderr, code = _run_main("")
    assert code == 0
    # No crash; markdown table with just header lines
    assert "target" in stdout


def test_cli_malformed_json_line_warns_and_skips():
    data = "not-json\n" + json.dumps(_payload("https://a.com"))
    stdout, stderr, code = _run_main(data)
    assert code == 0
    assert "WARN" in stderr
    assert "https://a.com" in stdout


def test_cli_top_anchors_limit():
    # 6 distinct anchors for same domain, limit to 2
    rows = "\n".join(
        json.dumps(_payload("https://a.com", anchors=(f"kw{i}", f"kw{i+1}")))
        for i in range(6)
    )
    stdout, _, code = _run_main(rows, ["--top-anchors", "2"])
    assert code == 0
    # At most 2 top-anchor entries per row in the table
    # Just check the row isn't showing more than 2 (kw, count) pairs
    a_row = [line for line in stdout.splitlines() if "https://a.com" in line][0]
    assert a_row.count("('") <= 2 or a_row.count("'kw") <= 2


def test_cli_multiple_url_modes_show_distinct_anchors():
    rows = "\n".join(
        json.dumps(_payload("https://a.com", url_mode=mode, anchors=(f"kw-{mode}", f"kw2-{mode}")))
        for mode in ("A", "B", "C")
    )
    stdout, _, code = _run_main(rows, ["--json"])
    assert code == 0
    data = json.loads(stdout)
    distinct = len(data["https://a.com"]["anchors"])
    assert distinct >= 3
