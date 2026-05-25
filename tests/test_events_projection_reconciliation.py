"""Plan 005 / U5: reconciliation acceptance gate.

The origin's v1 success criterion: a known completed run's outcome counts
reconcile exactly against events.db totals — no silent undercount. Uses the
real checkpoint module (not synthetic `succeeded` fixtures) and reconciles
against the checkpoint source only (single-source per the failed-event dedup
decision). This test must fail on pre-fix `main` (successes dropped) and pass
after U1–U4.

Realism note: `skipped_unreachable` is written to the run's *output* rows, not
to the checkpoint (publish_backlinks.py), so it is intentionally not seeded as
a checkpoint item — the projector never sees it from the checkpoint, and the
dashboard must source it elsewhere.
"""

from __future__ import annotations

import sqlite3

import pytest

from backlink_publisher.checkpoint import create_checkpoint, update_item
from backlink_publisher.events import EventStore, project_run_safe


@pytest.fixture(autouse=True)
def _isolate_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    yield


def _pending_row(item_id: str, target_url: str) -> dict:
    return {
        "id": item_id, "platform": "blogger", "language": "en",
        "publish_mode": "publish", "target_url": target_url,
        "main_domain": "https://example.com", "title": f"T-{item_id}",
        "slug": "t", "content_markdown": "x", "links": [],
    }


def _kind_counts() -> dict[str, int]:
    with EventStore().connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT kind, COUNT(*) AS n FROM events GROUP BY kind"
        ).fetchall()
    return {r["kind"]: r["n"] for r in rows}


def test_reconciliation_known_run_matches_events_totals():
    # 7 verified done (distinct URLs) + 2 failed. (skipped_unreachable is not a
    # checkpoint item, so it is not part of this fixture.)
    rows = [_pending_row(f"d{i}", f"https://example.com/t{i}") for i in range(7)]
    rows += [_pending_row(f"f{i}", f"https://example.com/f{i}") for i in range(2)]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="publish")

    for i in range(7):
        update_item(
            run_id, f"d{i}", "done",
            published_url=f"https://blog.example.org/p{i}",
            adapter="blogger", verified=True,
            completed_at="2026-05-25T12:00:00+00:00",
        )
    for i in range(2):
        update_item(
            run_id, f"f{i}", "failed",
            error="boom", error_class="X",
            completed_at="2026-05-25T12:00:00+00:00",
        )

    project_run_safe(run_id)

    counts = _kind_counts()
    assert counts.get("publish.confirmed", 0) == 7  # no dropped success
    assert counts.get("publish.failed", 0) == 2
    assert "publish.unverified" not in counts


def test_reconciliation_excludes_unverified_from_confirmed():
    rows = [
        _pending_row("v", "https://example.com/v"),
        _pending_row("u", "https://example.com/u"),
    ]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="publish")
    update_item(run_id, "v", "done", published_url="https://b.org/v",
                adapter="blogger", verified=True,
                completed_at="2026-05-25T12:00:00+00:00")
    update_item(run_id, "u", "done", published_url="https://b.org/u",
                adapter="blogger", verified=False,
                completed_at="2026-05-25T12:00:00+00:00")

    project_run_safe(run_id)

    counts = _kind_counts()
    assert counts.get("publish.confirmed", 0) == 1   # only the verified one
    assert counts.get("publish.unverified", 0) == 1


def test_reconciliation_duplicate_url_collapses_to_distinct():
    rows = [
        _pending_row("a", "https://example.com/a"),
        _pending_row("b", "https://example.com/b"),
    ]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="publish")
    # Both land the SAME published_url → article live_url UNIQUE collapses the
    # second; reconciliation counts distinct URLs (1 confirmed event), by design.
    for item_id in ("a", "b"):
        update_item(run_id, item_id, "done",
                    published_url="https://b.org/same",
                    adapter="blogger", verified=True,
                    completed_at="2026-05-25T12:00:00+00:00")

    project_run_safe(run_id)

    assert _kind_counts().get("publish.confirmed", 0) == 1


def test_reconciliation_per_platform_attribution_is_faithful():
    rows = [
        _pending_row("m", "https://example.com/m"),
        _pending_row("v", "https://example.com/v"),
    ]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="publish")
    update_item(run_id, "m", "done", published_url="https://b.org/m",
                adapter="medium", verified=True,
                completed_at="2026-05-25T12:00:00+00:00")
    update_item(run_id, "v", "done", published_url="https://b.org/v",
                adapter="velog", verified=True,
                completed_at="2026-05-25T12:00:00+00:00")

    project_run_safe(run_id)

    with EventStore().connect() as conn:
        conn.row_factory = sqlite3.Row
        by_platform = {
            r["platform"]: r["n"] for r in conn.execute(
                "SELECT json_extract(payload_json, '$.platform') AS platform, "
                "COUNT(*) AS n FROM events WHERE kind = 'publish.confirmed' "
                "GROUP BY platform"
            )
        }
    assert by_platform == {"medium": 1, "velog": 1}
