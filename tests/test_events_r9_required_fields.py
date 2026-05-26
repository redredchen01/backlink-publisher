"""R2/R9: append-time required-field floor enforcement.

R2 — every kind declares a required-field floor (the load-bearing payload keys).
R9 — ``EventStore.append`` checks the floor and routes a miss to quarantine
  (``failure_type="missing_field"``) rather than writing a malformed event or
  raising. How it quarantines depends on the caller's transaction, because
  ``quarantine()`` always opens its own private connection:

    * direct caller (``conn is None``)            -> quarantine immediately
    * projector reducer (``pending_quarantines``) -> defer to after commit
    * shared conn with no sink                     -> raise (misuse guard)

The deferred path is the load-bearing one: writing a quarantine from inside a
reducer's held WAL write transaction would deadlock (the same trap the
unmapped-status path hit). The end-to-end test below proves a forced miss inside
the real checkpoint reducer quarantines without deadlocking and the run finishes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from backlink_publisher.checkpoint import checkpoint_path
from backlink_publisher.events import EventStore, flush_for, kinds


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    yield


def _quarantine_rows(store: EventStore) -> list[dict[str, Any]]:
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT source, run_id, reason, raw_payload_json FROM quarantine_log"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(r["raw_payload_json"])
        out.append(d)
    return out


def _event_count(store: EventStore, kind: str) -> int:
    rows = store.query("SELECT COUNT(*) AS n FROM events WHERE kind = ?", (kind,))
    return rows[0]["n"]


# ── R2: floor coverage ────────────────────────────────────────────────


def test_every_registered_kind_has_a_required_field_floor():
    # R2 gate: a new kind cannot ship without declaring its floor.
    assert set(kinds.REQUIRED_FIELDS) == set(kinds.KINDS)
    for kind, floor in kinds.REQUIRED_FIELDS.items():
        assert isinstance(floor, frozenset) and floor, f"{kind} has an empty floor"


def test_missing_required_fields_is_presence_only():
    # A present key with value None satisfies the floor (publish.confirmed's
    # legitimate {"live_url": None} shape). An absent key is flagged.
    assert kinds.missing_required_fields(kinds.PUBLISH_CONFIRMED, {"live_url": None}) == frozenset()
    assert kinds.missing_required_fields(kinds.PUBLISH_CONFIRMED, {}) == frozenset({"live_url"})
    assert kinds.missing_required_fields(kinds.PUBLISH_INTENT, {"target_url": "x"}) == frozenset()


# ── R9: direct-caller (conn is None) path ──────────────────────────────


def test_well_formed_payload_inserts_normally():
    store = EventStore()
    eid = store.append(kinds.PUBLISH_CONFIRMED, {"live_url": "https://x.com/p"})
    assert eid > 0
    assert _event_count(store, kinds.PUBLISH_CONFIRMED) == 1
    assert _quarantine_rows(store) == []


def test_confirmed_with_live_url_none_is_not_quarantined():
    # Edge: live_url=None is a legitimate published-without-URL shape.
    store = EventStore()
    eid = store.append(kinds.PUBLISH_CONFIRMED, {"live_url": None})
    assert eid > 0
    assert _event_count(store, kinds.PUBLISH_CONFIRMED) == 1
    assert _quarantine_rows(store) == []


def test_direct_caller_missing_field_is_quarantined_not_written():
    # Error path: a payload missing its floor is quarantined (failure_type=
    # missing_field), no event row is written, and append returns the -1
    # sentinel rather than raising.
    store = EventStore()
    eid = store.append(kinds.IMAGE_GEN_INVOKED, {})  # floor = {"prompt_sha"}
    assert eid == -1
    assert _event_count(store, kinds.IMAGE_GEN_INVOKED) == 0
    rows = _quarantine_rows(store)
    assert len(rows) == 1
    assert rows[0]["payload"]["failure_type"] == "missing_field"
    assert rows[0]["source"] == kinds.IMAGE_GEN_INVOKED
    assert "prompt_sha" in rows[0]["reason"]


def test_null_identity_misses_dedupe_to_one_row():
    # Edge: a direct caller with no run_id/target_url (image_gen) produces a
    # null-identity quarantine row; NULL-folded dedup collapses repeats to one
    # row rather than flooding on a repeated code-level bug.
    store = EventStore()
    store.append(kinds.IMAGE_GEN_INVOKED, {})
    store.append(kinds.IMAGE_GEN_INVOKED, {})
    store.append(kinds.IMAGE_GEN_INVOKED, {})
    assert len(_quarantine_rows(store)) == 1


# ── R9: shared-conn (projector) paths ──────────────────────────────────


def test_shared_conn_missing_field_with_sink_defers_no_inline_write():
    store = EventStore()
    sink: list[dict[str, Any]] = []
    with store.connect() as conn:
        eid = store.append(
            kinds.IMAGE_GEN_INVOKED, {}, conn=conn, pending_quarantines=sink
        )
    assert eid == -1
    # Deferred: nothing written to quarantine_log yet, only collected.
    assert len(sink) == 1
    assert sink[0]["failure_type"] == "missing_field"
    assert _quarantine_rows(store) == []
    # The reducer flushes after commit via store.quarantine(**record).
    store.quarantine(**sink[0])
    assert len(_quarantine_rows(store)) == 1


def test_shared_conn_missing_field_without_sink_raises_misuse_guard():
    # A shared conn with no deferral sink cannot quarantine safely (private
    # conn deadlocks; sharing the conn loses the row on rollback), so the
    # programmer error is loud rather than silently mishandled.
    store = EventStore()
    with store.connect() as conn:
        with pytest.raises(ValueError, match="pending_quarantines"):
            store.append(kinds.IMAGE_GEN_INVOKED, {}, conn=conn)


# ── R9: end-to-end through the real checkpoint reducer (no deadlock) ────


_RUN_ID = "20260526T120000-abcd1234"


def _write_checkpoint(items: list[dict[str, Any]]) -> Path:
    p = checkpoint_path(_RUN_ID)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "run_id": _RUN_ID,
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
    return p


def test_reducer_floor_miss_quarantines_after_commit_without_deadlock(monkeypatch):
    # Force the confirmed emit to miss its floor by demanding a field the
    # checkpoint reducer never sends. The reducer holds the WAL write lock while
    # emitting, so this exercises the deferred-write path: the miss must
    # quarantine AFTER the transaction commits (no "database is locked"), the
    # malformed event must NOT be written, and flush_for must return normally.
    monkeypatch.setitem(
        kinds.REQUIRED_FIELDS, kinds.PUBLISH_CONFIRMED, frozenset({"__never_sent__"})
    )
    _write_checkpoint(
        [
            {
                "id": "ok1",
                "status": "done",
                "adapter": "blogger",
                "verified": True,
                "published_url": "https://blogger.com/live1",
                "payload": {"target_url": "https://example.com/1"},
            }
        ]
    )
    store = EventStore()
    result = flush_for(checkpoint_path(_RUN_ID), store=store)  # must not raise

    assert _event_count(store, kinds.PUBLISH_CONFIRMED) == 0  # malformed -> not written
    rows = _quarantine_rows(store)
    assert len(rows) == 1
    assert rows[0]["payload"]["failure_type"] == "missing_field"
    assert rows[0]["run_id"] == _RUN_ID
    assert result.quarantined == 1
