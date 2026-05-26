"""Unit 3: projector quarantines unmapped checkpoint statuses (the P0 class)
and does NOT quarantine intentional NO_EMIT statuses (the false-positive guard).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from backlink_publisher.events import EventStore, flush_for


@pytest.fixture(autouse=True)
def _isolate_events_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    yield


def _write_checkpoint(path: Path, items: list[dict[str, Any]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "run_id": "20260526T120000-abcd1234",
                "started_at": "2026-05-26T12:00:00+00:00",
                "platform": "blogger",
                "mode": "publish",
                "status": None,
                "items": items,
                "flags": {},
            }
        ),
        encoding="utf-8",
    )
    return path


def _quarantine(store: EventStore) -> list[dict[str, Any]]:
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM quarantine_log ORDER BY id")]


def _event_count(store: EventStore) -> int:
    with store.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def test_unknown_checkpoint_status_is_quarantined_not_dropped(tmp_path):
    # The P0 repro: a status the classifier doesn't know must quarantine, not
    # silently fall through.
    ckpt = _write_checkpoint(
        tmp_path / "20260526T120000-abcd1234.json",
        [{"id": "i1", "status": "done2", "adapter": "blogger",
          "payload": {"target_url": "https://example.com/x"}, "published_url": None}],
    )
    flush_for(ckpt)

    store = EventStore()
    rows = _quarantine(store)
    assert len(rows) == 1
    payload = json.loads(rows[0]["raw_payload_json"])
    assert payload["failure_type"] == "unmapped_status"
    assert payload["source_status"] == "done2"
    assert _event_count(store) == 0  # not emitted as a bogus success


def test_distinct_unmapped_records_produce_distinct_quarantine_rows(tmp_path):
    ckpt = _write_checkpoint(
        tmp_path / "20260526T120000-abcd1234.json",
        [
            {"id": "i1", "status": "bogus", "adapter": "blogger", "payload": {}},
            {"id": "i2", "status": "bogus", "adapter": "blogger", "payload": {}},
        ],
    )
    flush_for(ckpt)
    assert len(_quarantine(EventStore())) == 2


def test_history_drafted_status_is_no_emit_not_quarantined(tmp_path):
    # history does not own "drafted" — intentional NO_EMIT, must not quarantine.
    path = tmp_path / "publish-history.json"
    path.write_text(
        json.dumps(
            [{"id": "h1", "target_url": "https://example.com/x", "platform": "medium",
              "status": "drafted", "created_at": "2026-05-26 12:30"}]
        ),
        encoding="utf-8",
    )
    flush_for(path)
    store = EventStore()
    assert _quarantine(store) == []
    assert _event_count(store) == 0


def test_drafts_failed_status_is_no_emit_not_quarantined(tmp_path):
    # drafts does not own "failed" — history is the system of record.
    path = tmp_path / "draft-queue.json"
    path.write_text(
        json.dumps(
            [{"id": "d1", "target_url": "https://example.com/x", "status": "failed"}]
        ),
        encoding="utf-8",
    )
    flush_for(path)
    store = EventStore()
    assert _quarantine(store) == []
    assert _event_count(store) == 0


def test_reprojecting_unmapped_status_does_not_duplicate_quarantine(tmp_path):
    # Idempotency end-to-end: flushing the same checkpoint twice yields one row.
    ckpt = _write_checkpoint(
        tmp_path / "20260526T120000-abcd1234.json",
        [{"id": "i1", "status": "done2", "adapter": "blogger", "payload": {}}],
    )
    flush_for(ckpt)
    flush_for(ckpt)
    assert len(_quarantine(EventStore())) == 1


def test_mixed_run_emits_known_and_quarantines_unmapped_without_halting(tmp_path):
    # A run with a normal pending item AND an unmapped item: the pending emits
    # its intent event, the unmapped quarantines, and the run completes.
    ckpt = _write_checkpoint(
        tmp_path / "20260526T120000-abcd1234.json",
        [
            {"id": "ok", "status": "pending", "adapter": "blogger",
             "payload": {"target_url": "https://example.com/ok"}, "title": "t"},
            {"id": "bad", "status": "weird", "adapter": "blogger", "payload": {}},
        ],
    )
    result = flush_for(ckpt)
    store = EventStore()
    assert result.events_inserted == 1  # the pending -> publish.intent
    assert _event_count(store) == 1
    assert len(_quarantine(store)) == 1  # only the unmapped one


def test_history_truly_unknown_status_is_no_emit_via_catch_all(tmp_path):
    # A status neither emitted nor explicitly listed -> NO_EMIT default, not quarantine.
    path = tmp_path / "publish-history.json"
    path.write_text(
        json.dumps([{"id": "h1", "target_url": "https://example.com/x",
                     "platform": "medium", "status": "never_seen_before",
                     "created_at": "2026-05-26 12:30"}]),
        encoding="utf-8",
    )
    flush_for(path)
    store = EventStore()
    assert _quarantine(store) == []
    assert _event_count(store) == 0
