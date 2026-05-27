"""Unit 1 — audit.readers. Each test gets a fresh config/cache dir so the
session sandbox doesn't bleed seeded state. Covers snapshot freshness (the
load-bearing WAL behavior), zero-touch on the real store, store-state
distinction, and tear flagging."""

import hashlib
import json
import sqlite3

import pytest

from backlink_publisher.audit import readers
from backlink_publisher.audit.readers import AuditReadError, read_snapshot
from backlink_publisher.config import _config_dir
from backlink_publisher.events import EventStore


@pytest.fixture(autouse=True)
def fresh_dirs(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cache = tmp_path / "cache"
    cfg.mkdir()
    cache.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(cache))


def _seed_article(live_url, target="https://site.com/p"):
    EventStore().add_article(
        {"target_urls_json": json.dumps([target]), "live_url": live_url}
    )


def _write_history(rows):
    (_config_dir() / "publish-history.json").write_text(json.dumps(rows))


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def test_happy_path_reads_both_stores():
    _seed_article("https://medium.com/post1")
    _write_history(
        [{"id": "h1", "status": "published", "article_urls": ["https://medium.com/post1"]}]
    )
    snap = read_snapshot()
    assert not snap.nothing_to_audit
    assert len(snap.articles) == 1
    assert snap.articles[0].live_url == "https://medium.com/post1"
    assert len(snap.history) == 1


def test_nothing_to_audit_when_neither_store_exists():
    snap = read_snapshot()
    assert snap.nothing_to_audit
    assert snap.articles == [] and snap.history == []


def test_missing_history_file_is_empty_not_error():
    _seed_article("https://medium.com/post1")
    snap = read_snapshot()
    assert snap.history == []
    assert len(snap.articles) == 1


def test_null_live_url_row_is_returned_not_filtered():
    EventStore().add_article(
        {"target_urls_json": json.dumps(["https://site.com/p"]), "live_url": None}
    )
    snap = read_snapshot()
    assert len(snap.articles) == 1
    assert snap.articles[0].live_url is None


def test_unreadable_events_db_raises_audit_read_error(monkeypatch):
    _seed_article("https://medium.com/post1")
    db_path = _config_dir() / "events.db"
    # Force the snapshot copy to fail.
    monkeypatch.setattr(
        readers.shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    with pytest.raises(AuditReadError):
        read_snapshot()


def test_freshness_sees_uncheckpointed_wal_row():
    """Load-bearing: a row committed to -wal but NOT checkpointed (writer
    connection still open) must be seen by the snapshot, because read_snapshot
    copies -wal. Negative control: an immutable=1 open of the live main file
    (which ignores -wal) does NOT see it — proving the -wal copy is essential.
    """
    _seed_article("https://medium.com/post1")  # row1, checkpointed on close
    db_path = _config_dir() / "events.db"

    # Hold a WAL writer open so row2 stays in -wal, uncheckpointed.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            "INSERT INTO articles (live_url, target_urls_json) VALUES (?, '[]')",
            ("https://medium.com/post2",),
        )
        conn.commit()

        # Negative control: immutable=1 reads only the main file → misses row2.
        ro = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        try:
            immutable_urls = {
                r[0] for r in ro.execute("SELECT live_url FROM articles")
            }
        finally:
            ro.close()
        assert "https://medium.com/post2" not in immutable_urls

        # The snapshot (copies -wal) DOES see row2.
        snap = read_snapshot()
        live_urls = {a.live_url for a in snap.articles}
        assert "https://medium.com/post1" in live_urls
        assert "https://medium.com/post2" in live_urls
    finally:
        conn.close()


def test_zero_touch_real_store_byte_identical():
    _seed_article("https://medium.com/post1")
    db_path = _config_dir() / "events.db"
    wal = db_path.with_name("events.db-wal")
    shm = db_path.with_name("events.db-shm")
    before = (_hash(db_path), _hash(wal), _hash(shm))
    sidecars_before = {p.name for p in (wal, shm) if p.exists()}

    read_snapshot()

    after = (_hash(db_path), _hash(wal), _hash(shm))
    sidecars_after = {p.name for p in (wal, shm) if p.exists()}
    assert before == after, "real events.db / sidecars must be byte-identical"
    assert sidecars_before == sidecars_after, "no new sidecars on the real store"


def test_temp_copy_cleaned_up(monkeypatch):
    import tempfile

    _seed_article("https://medium.com/post1")
    created = []
    real_mkdtemp = tempfile.mkdtemp

    def _tracking_mkdtemp(*a, **k):
        d = real_mkdtemp(*a, **k)
        created.append(d)
        return d

    monkeypatch.setattr(readers.tempfile, "mkdtemp", _tracking_mkdtemp)
    read_snapshot()
    assert created, "snapshot should have created a temp dir"
    import os

    for d in created:
        assert not os.path.exists(d), "temp snapshot dir must be cleaned up"


def test_history_change_during_read_flags_transient(monkeypatch):
    _seed_article("https://medium.com/post1")
    _write_history([{"id": "h1", "status": "published", "article_urls": ["x"]}])

    # Mutate the history file while articles are being read (between the pre- and
    # post- history fingerprints), so the two stores are read inconsistently.
    real = readers._read_articles_from_snapshot

    def _read_then_touch_history(db_path):
        result = real(db_path)
        _write_history([{"id": "h2", "status": "published", "article_urls": ["y"]}])
        return result

    monkeypatch.setattr(readers, "_read_articles_from_snapshot", _read_then_touch_history)
    snap = read_snapshot()
    assert snap.transient, "history file changing across the read window must flag transient"


def test_malformed_history_json_raises_audit_read_error():
    _seed_article("https://medium.com/post1")
    (_config_dir() / "publish-history.json").write_text("{not valid json")
    with pytest.raises(AuditReadError):
        read_snapshot()


def test_tear_flagged_when_source_changes_mid_read(monkeypatch):
    _seed_article("https://medium.com/post1")
    db_path = _config_dir() / "events.db"

    # Simulate a concurrent write during the copy by mutating the db between the
    # pre- and post-copy fingerprints (patch copy2 to touch the source after copy).
    real_copy2 = readers.shutil.copy2

    def _copy_then_mutate(src, dst, *a, **k):
        result = real_copy2(src, dst, *a, **k)
        if str(src).endswith("events.db"):
            EventStore().add_article(
                {"target_urls_json": "[]", "live_url": "https://medium.com/post2"}
            )
        return result

    monkeypatch.setattr(readers.shutil, "copy2", _copy_then_mutate)
    snap = read_snapshot()
    assert snap.transient, "a source change during the read window must flag transient"
