"""Unit 2: EventStore.quarantine() write path + dedupe + schema migration.

Covers the idempotent quarantine write, the NULL-run_id dedupe trap (banner/
image_gen), distinct-record separation, queryability (R7), and the
pre-existing-v2-DB migration that back-fills the dedup_key column + index on
the connect path (the data-integrity finding).
"""

from __future__ import annotations

import sqlite3


from backlink_publisher.events.store import EventStore


def _quarantine_rows(store: EventStore) -> list[sqlite3.Row]:
    return store.query("SELECT * FROM quarantine_log ORDER BY id")


def test_quarantine_writes_queryable_row_with_failure_type(tmp_path):
    store = EventStore(path=tmp_path / "events.db")
    wrote = store.quarantine(
        reason="unmapped_status: checkpoint/done2",
        failure_type="unmapped_status",
        source="checkpoint",
        run_id="20260526T120000-abcd1234",
        source_status="done2",
        record_identity="item-1",
    )
    assert wrote is True
    rows = _quarantine_rows(store)
    assert len(rows) == 1
    import json

    payload = json.loads(rows[0]["raw_payload_json"])
    assert payload["failure_type"] == "unmapped_status"
    assert payload["source_status"] == "done2"


def test_quarantine_is_idempotent_for_same_record(tmp_path):
    store = EventStore(path=tmp_path / "events.db")
    kw = dict(
        reason="unmapped_status: checkpoint/done2",
        failure_type="unmapped_status",
        source="checkpoint",
        run_id="run-1",
        source_status="done2",
        record_identity="item-1",
    )
    assert store.quarantine(**kw) is True
    assert store.quarantine(**kw) is False  # dedup no-op
    assert len(_quarantine_rows(store)) == 1


def test_null_run_id_records_still_dedupe(tmp_path):
    # The trap: SQLite treats NULLs as distinct, so a multi-column key would
    # never dedupe banner/image_gen rows (no run_id). The single folded-NULL
    # dedup_key must still collapse them.
    store = EventStore(path=tmp_path / "events.db")
    kw = dict(
        reason="missing_field: image_gen_invoked",
        failure_type="missing_field",
        source=None,
        run_id=None,
        source_status=None,
        record_identity="prompt-sha-abc",
    )
    assert store.quarantine(**kw) is True
    assert store.quarantine(**kw) is False
    assert len(_quarantine_rows(store)) == 1


def test_distinct_records_produce_distinct_rows(tmp_path):
    # Guards against an over-coarse dedup_key collapsing two different unmapped
    # records (which would silently drop the second — recreating the drop).
    store = EventStore(path=tmp_path / "events.db")
    base = dict(
        reason="unmapped_status",
        failure_type="unmapped_status",
        source="checkpoint",
        run_id="run-1",
        source_status="bogus",
    )
    assert store.quarantine(record_identity="item-1", **base) is True
    assert store.quarantine(record_identity="item-2", **base) is True
    assert len(_quarantine_rows(store)) == 2


def test_preexisting_v2_db_gains_dedup_key_column_and_index_on_connect(tmp_path):
    # Build a v2 events.db whose quarantine_log predates dedup_key, then open
    # it through EventStore (which runs maybe_upgrade_schema in _connect_raw)
    # and assert the column + index now exist. Must go through the real
    # connect() path, not initialize_schema directly.
    db_path = tmp_path / "events.db"
    raw = sqlite3.connect(str(db_path))
    raw.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
    raw.execute("INSERT INTO schema_version (version) VALUES (2)")
    raw.execute(
        "CREATE TABLE quarantine_log ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL,"
        " source TEXT, run_id TEXT, reason TEXT NOT NULL, raw_payload_json TEXT)"
    )
    raw.commit()
    raw.close()

    store = EventStore(path=db_path)
    # Trigger a connect via a harmless write.
    store.quarantine(
        reason="unmapped_status",
        failure_type="unmapped_status",
        source="checkpoint",
        run_id="run-1",
        source_status="x",
        record_identity="item-1",
    )

    with store.connect() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(quarantine_log)")}
        idx = {row[1] for row in conn.execute("PRAGMA index_list(quarantine_log)")}
    assert "dedup_key" in cols
    assert "idx_quarantine_dedup" in idx
    # And the back-filled DB actually dedupes.
    assert len(_quarantine_rows(store)) == 1
