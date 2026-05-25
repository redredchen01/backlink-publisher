"""Tests for the read-time projection backstop (Plan 2026-05-25-006 / U1).

Exercises ``events.reconcile.project_on_read``: the dormant history-path flush,
crash-stranded checkpoint reconcile, quarantine gap flag (set + clear), the
within-process single-flight (``threading.Barrier`` → no double-append), and the
degrade-never-raise contract.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from backlink_publisher.events import EventStore
from backlink_publisher.events import reconcile


@pytest.fixture(autouse=True)
def _isolate_dirs(tmp_path, monkeypatch):
    # events.db + publish-history.json live in the config dir; checkpoints in
    # the cache dir. Sandbox both so the real operator state is never touched.
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    yield


def _write_history(config_dir: Path, rows: list[dict[str, Any]]) -> Path:
    path = config_dir / "publish-history.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _checkpoint_dir(cache_dir: Path) -> Path:
    d = cache_dir / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_checkpoint(
    cache_dir: Path, run_id: str, items: list[dict[str, Any]]
) -> Path:
    path = _checkpoint_dir(cache_dir) / f"{run_id}.json"
    path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "started_at": "2026-05-18T12:00:00+00:00",
                "items": items,
                "flags": {},
            }
        ),
        encoding="utf-8",
    )
    return path


def _published_row(rid: str, platform: str = "medium") -> dict[str, Any]:
    return {
        "id": rid,
        "target_url": "https://example.com/landing",
        "platform": platform,
        "language": "zh-CN",
        "status": "published",
        "created_at": "2026-05-18 12:30",
        "article_urls": [f"https://{platform}.example/{rid}"],
    }


def _count_kind(store: EventStore, kind: str) -> int:
    rows = store.query("SELECT COUNT(*) AS n FROM events WHERE kind = ?", (kind,))
    return int(rows[0]["n"])


def _quarantine_count(store: EventStore) -> int:
    rows = store.query("SELECT COUNT(*) AS n FROM quarantine_log")
    return int(rows[0]["n"])


# ── Happy path: dormant history path is flushed on read ──────────────────────


def test_history_path_projected_on_first_load(tmp_path):
    _write_history(tmp_path, [_published_row("h1")])

    result = reconcile.project_on_read()

    assert result.events_inserted == 1
    assert result.sources_projected == 1
    assert result.latest_event_utc is not None
    assert result.gap is False
    assert result.degraded is False
    assert _count_kind(EventStore(), "publish.confirmed") == 1


def test_second_load_is_cheap_noop(tmp_path):
    _write_history(tmp_path, [_published_row("h1")])

    first = reconcile.project_on_read()
    second = reconcile.project_on_read()

    assert first.events_inserted == 1
    assert second.events_inserted == 0  # idempotent — cursor already at state
    # No duplicate row from the second pass.
    assert _count_kind(EventStore(), "publish.confirmed") == 1


def test_stranded_checkpoint_reconciled(tmp_path):
    # A checkpoint on disk with NO cursor row == crashed before its inline flush.
    _write_checkpoint(
        tmp_path / "cache",
        "20260518T120000-deadbeef",
        [
            {
                "id": "i1",
                "status": "done",
                "published_url": "https://medium.example/p1",
                "completed_at": "2026-05-18T12:05:00+00:00",
                "verified": True,
                "adapter": "medium",
                "payload": {"target_url": "https://example.com/landing"},
            }
        ],
    )

    result = reconcile.project_on_read()

    assert result.sources_projected == 1
    assert _count_kind(EventStore(), "publish.confirmed") == 1


# ── Zero data ────────────────────────────────────────────────────────────────


def test_zero_data_empty_freshness_no_crash(tmp_path):
    result = reconcile.project_on_read()

    assert result.events_inserted == 0
    assert result.sources_projected == 0
    assert result.latest_event_utc is None
    assert result.gap is False
    assert result.degraded is False


# ── Concurrency: single-flight, no double-append ─────────────────────────────


def test_concurrent_loads_do_not_double_append(tmp_path):
    _write_history(tmp_path, [_published_row("h1")])

    barrier = threading.Barrier(2)
    results: list[Any] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=5)
            results.append(reconcile.project_on_read())
        except BaseException as exc:  # noqa: BLE001 — surface for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, errors
    # The single-flight lock + flush_for idempotency guarantee exactly one row.
    assert _count_kind(EventStore(), "publish.confirmed") == 1
    # Exactly one load INSERTED the event; the other was serialized behind the
    # lock and saw the updated cursor, so it inserted nothing (no double-append).
    # Both still "project" the history source — the second is an idempotent no-op.
    assert sorted(r.events_inserted for r in results) == [0, 1]
    assert [r.sources_projected for r in results] == [1, 1]


# ── Quarantine gap flag: set, then clear ─────────────────────────────────────


def test_corrupt_checkpoint_quarantined_without_aborting_others(tmp_path):
    # Valid history + a stranded checkpoint that is not valid JSON.
    _write_history(tmp_path, [_published_row("h1")])
    bad = _checkpoint_dir(tmp_path / "cache") / "20260518T120000-deadbeef.json"
    bad.write_text("{ this is not json", encoding="utf-8")

    result = reconcile.project_on_read()  # must not raise

    # History still projected despite the corrupt checkpoint.
    assert _count_kind(EventStore(), "publish.confirmed") == 1
    assert result.gap is True
    assert _quarantine_count(EventStore()) == 1


def test_repeated_corrupt_checkpoint_does_not_duplicate_quarantine(tmp_path):
    bad = _checkpoint_dir(tmp_path / "cache") / "20260518T120000-deadbeef.json"
    bad.write_text("{ broken", encoding="utf-8")

    reconcile.project_on_read()
    reconcile.project_on_read()

    assert _quarantine_count(EventStore()) == 1  # de-duped by source


def test_quarantined_checkpoint_cleared_when_it_later_projects(tmp_path):
    run_id = "20260518T120000-deadbeef"
    bad = _checkpoint_dir(tmp_path / "cache") / f"{run_id}.json"
    bad.write_text("{ broken", encoding="utf-8")

    first = reconcile.project_on_read()
    assert first.gap is True
    assert _quarantine_count(EventStore()) == 1

    # Operator/resume fixes the checkpoint; next load projects + clears the gap.
    _write_checkpoint(
        tmp_path / "cache",
        run_id,
        [
            {
                "id": "i1",
                "status": "done",
                "published_url": "https://medium.example/p1",
                "completed_at": "2026-05-18T12:05:00+00:00",
                "verified": True,
                "adapter": "medium",
                "payload": {"target_url": "https://example.com/landing"},
            }
        ],
    )

    second = reconcile.project_on_read()
    assert second.gap is False
    assert _quarantine_count(EventStore()) == 0
    assert _count_kind(EventStore(), "publish.confirmed") == 1


# ── Degrade, never raise ─────────────────────────────────────────────────────


def test_db_operational_error_degrades_not_raises(tmp_path, monkeypatch):
    _write_history(tmp_path, [_published_row("h1")])

    def _boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(reconcile, "flush_for", _boom)

    result = reconcile.project_on_read()  # must not raise

    assert result.degraded is True
    assert result.degraded_reason is not None
    assert "OperationalError" in result.degraded_reason


def test_clear_quarantine_failure_does_not_degrade_successful_projection(tmp_path, monkeypatch):
    # A locked DB during the (best-effort) quarantine-clear must NOT turn an
    # otherwise-successful projection into a degraded result.
    _write_history(tmp_path, [_published_row("h1")])
    store = EventStore()

    def _raise(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    # connect_immediate backs _clear_quarantine (success path); break it.
    monkeypatch.setattr(store, "connect_immediate", _raise)

    result = reconcile.project_on_read(store=store)

    assert result.degraded is False
    assert result.events_inserted == 1
    assert _count_kind(store, "publish.confirmed") == 1


def test_unverified_done_emits_unverified_kind_not_confirmed(tmp_path):
    # D5: a done item with verified=False must project as publish.unverified.
    _write_checkpoint(
        tmp_path / "cache",
        "20260518T120000-deadbeef",
        [
            {
                "id": "i1",
                "status": "done",
                "published_url": "https://medium.example/p1",
                "completed_at": "2026-05-18T12:05:00+00:00",
                "verified": False,
                "adapter": "medium",
                "payload": {"target_url": "https://example.com/landing"},
            }
        ],
    )

    reconcile.project_on_read()

    store = EventStore()
    assert _count_kind(store, "publish.unverified") == 1
    assert _count_kind(store, "publish.confirmed") == 0
