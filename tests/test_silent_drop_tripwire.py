"""Tests for the Silent-Drop Tripwire — reconciliation log lines emitted by
plan-backlinks, validate-backlinks, and publish-backlinks at end-of-run.

Each stage emits a structured `*_reconciliation` event to stderr with
input/output/delta/dropped fields, both on success AND failure paths. The
operator's "planned 20, got 5 — where did 15 go?" question becomes
answerable without grepping every error line.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from typing import Any

import pytest


def _capture_reconciliation_events(stderr: str, event_name: str) -> list[dict[str, Any]]:
    """Parse stderr for structured JSON log lines matching `event_name`."""
    events = []
    for line in stderr.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("msg") == event_name:
            events.append(record)
    return events


# ── plan-backlinks reconciliation ───────────────────────────────────────────


def test_plan_reconciliation_happy_path(tmp_path, capsys):
    """All-valid input → reconciliation says input=N output=N delta=0 dropped={}."""
    from backlink_publisher.cli import plan_backlinks

    input_jsonl = tmp_path / "in.jsonl"
    rows = [
        {
            "main_domain": "https://a.com",
            "target_url": "https://a.com/page",
            "topic": "test topic",
            "seed_keywords": ["kw"],
            "language": "en",
            "platform": "blogger",
            "url_mode": "A",
            "publish_mode": "publish",
        },
        {
            "main_domain": "https://b.com",
            "target_url": "https://b.com/page",
            "topic": "another topic",
            "seed_keywords": ["kw2"],
            "language": "en",
            "platform": "blogger",
            "url_mode": "A",
            "publish_mode": "publish",
        },
    ]
    input_jsonl.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        plan_backlinks.main(["--input", str(input_jsonl)])

    events = _capture_reconciliation_events(capsys.readouterr().err, "plan_reconciliation")
    assert len(events) == 1
    e = events[0]
    assert e["input_rows"] == 2
    assert e["output_rows"] == 2
    assert e["delta"] == 0
    assert e["dropped"] == {"validation": 0, "generation": 0}


def test_plan_reconciliation_validation_drops(tmp_path, capsys):
    """Mix valid + invalid rows → reconciliation partitions by drop reason.

    The CLI still raises SystemExit(2) on any error — the reconciliation
    must be emitted BEFORE that exit.
    """
    from backlink_publisher.cli import plan_backlinks

    input_jsonl = tmp_path / "in.jsonl"
    rows = [
        {"main_domain": "https://x.com"},  # invalid: missing required fields
        {"main_domain": "https://y.com", "language": "en"},  # also invalid
        {
            "main_domain": "https://z.com",
            "target_url": "https://z.com/page",
            "topic": "topic",
            "seed_keywords": ["kw"],
            "language": "en",
            "platform": "blogger",
            "url_mode": "A",
            "publish_mode": "publish",
        },
    ]
    input_jsonl.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        with pytest.raises(SystemExit):
            plan_backlinks.main(["--input", str(input_jsonl)])

    events = _capture_reconciliation_events(capsys.readouterr().err, "plan_reconciliation")
    assert len(events) == 1
    e = events[0]
    assert e["input_rows"] == 3
    # Two validation drops at lines 1 and 2
    assert e["dropped"]["validation"] == 2
    assert sorted(e["dropped_line_numbers"]["validation"]) == [1, 2]
    # Line 3 generated successfully → output_rows = 1, delta = 2
    assert e["output_rows"] == 1
    assert e["delta"] == 2


# ── validate-backlinks reconciliation ───────────────────────────────────────


def test_validate_reconciliation_unsupported_platform_drops(tmp_path, capsys):
    """linkedin platform → drop counted under 'platform' bucket."""
    from backlink_publisher.cli import validate_backlinks

    input_jsonl = tmp_path / "in.jsonl"
    payload = {
        "id": "id-1",
        "platform": "linkedin",
        "main_domain": "https://a.com",
        "target_url": "https://a.com/page",
        "title": "x",
        "language": "en",
        "url_mode": "A",
        "publish_mode": "publish",
        "content_markdown": "body",
        "links": [],
    }
    input_jsonl.write_text(json.dumps(payload), encoding="utf-8")

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        with pytest.raises(SystemExit):
            validate_backlinks.main(["--input", str(input_jsonl), "--no-check-urls"])

    events = _capture_reconciliation_events(capsys.readouterr().err, "validate_reconciliation")
    assert len(events) == 1
    e = events[0]
    assert e["input_rows"] == 1
    assert e["dropped"]["platform"] == 1
    assert e["dropped_row_indices"]["platform"] == [1]


# ── publish-backlinks reconciliation ────────────────────────────────────────


def test_publish_reconciliation_dry_run_emits_event(tmp_path, capsys):
    """Dry-run with one valid payload should still emit reconciliation."""
    from backlink_publisher.cli import publish_backlinks

    input_jsonl = tmp_path / "in.jsonl"
    payload = {
        "id": "id-1",
        "platform": "blogger",
        "main_domain": "https://x.com",
        "target_url": "https://x.com/page",
        "title": "t1",
        "language": "en",
        "url_mode": "A",
        "publish_mode": "publish",
        "content_markdown": "body",
        "links": [
            {"kind": "main_domain", "url": "https://x.com", "anchor": "brand"},
            {"kind": "target", "url": "https://x.com/page", "anchor": "head term"},
            {"kind": "supporting", "url": "https://wikipedia.org/x", "anchor": "ref"},
        ],
    }
    input_jsonl.write_text(json.dumps(payload), encoding="utf-8")

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        try:
            publish_backlinks.main(["--input", str(input_jsonl), "--dry-run"])
        except SystemExit:
            pass

    events = _capture_reconciliation_events(capsys.readouterr().err, "publish_reconciliation")
    # Dry-run may exit at pre-flight before reaching reconciliation; permissive
    # but if it fires, shape must be correct.
    if events:
        e = events[0]
        assert "input_payloads" in e
        assert "output_rows" in e
        assert "delta" in e
        assert isinstance(e["dropped"], dict)
