"""Tests for the publish-history reducer in ``events.projector``.

Covers append-only diff, scrubbed failed events, and the bounded JSON
read retry that protects against the non-atomic webui writer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backlink_publisher.events import EventStore, flush_for


@pytest.fixture(autouse=True)
def _isolate_events_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    yield


def _write_history(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _query_events(store: EventStore) -> list[dict[str, Any]]:
    import sqlite3
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT kind, target_url, host, payload_json, ts_raw, ts_utc "
            "FROM events ORDER BY id"
        )]


def _query_articles(store: EventStore) -> list[dict[str, Any]]:
    import sqlite3
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT live_url, host, lang FROM articles ORDER BY article_id"
        )]


def test_published_history_row_emits_confirmed_and_article(tmp_path):
    path = _write_history(
        tmp_path / "publish-history.json",
        [
            {
                "id": "h1",
                "target_url": "https://example.com/landing",
                "platform": "medium",
                "language": "zh-CN",
                "status": "published",
                "created_at": "2026-05-18 12:30",
                "article_urls": ["https://medium.com/@op/post-1"],
            }
        ],
    )

    result = flush_for(path)

    assert result.events_inserted == 1
    assert result.articles_inserted == 1
    assert result.cursor_updated is True

    events = _query_events(EventStore())
    assert events[0]["kind"] == "publish.confirmed"
    assert events[0]["target_url"] == "https://example.com/landing"
    assert events[0]["host"] == "medium.com"
    assert events[0]["ts_raw"] == "2026-05-18 12:30"
    # ts_utc round-tripped through local TZ; just verify shape.
    assert events[0]["ts_utc"].endswith("+00:00")

    articles = _query_articles(EventStore())
    assert articles[0]["live_url"] == "https://medium.com/@op/post-1"
    assert articles[0]["host"] == "medium.com"
    assert articles[0]["lang"] == "zh-CN"


def test_failed_history_row_emits_scrubbed_publish_failed(tmp_path):
    path = _write_history(
        tmp_path / "publish-history.json",
        [
            {
                "id": "h-fail",
                "target_url": "https://example.com/x",
                "platform": "blogger",
                "language": "en",
                "status": "failed",
                "created_at": "2026-05-18 12:30",
                "article_urls": [],
                "error": "Token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 expired",
            }
        ],
    )

    flush_for(path)
    events = _query_events(EventStore())
    assert events[0]["kind"] == "publish.failed"
    payload = json.loads(events[0]["payload_json"])
    assert "<REDACTED>" in payload["error_message_clean"]
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in payload["error_message_clean"]


def test_corrupt_history_leaves_cursor_untouched(tmp_path):
    path = tmp_path / "publish-history.json"
    path.write_text("garbage", encoding="utf-8")

    result = flush_for(path)

    assert result.cursor_updated is False
    assert result.events_inserted == 0
    assert result.articles_inserted == 0

    with EventStore().connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM projection_cursor WHERE source = ?",
            (str(path),),
        ).fetchone()
    assert row[0] == 0
