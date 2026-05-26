"""Read-only readers for the dual-state divergence auditor.

Loads the ``events.db`` ``articles`` table and ``publish-history.json`` into
separate in-memory views. The real ``events.db`` is **never** touched: articles
are read from a throwaway *snapshot copy* (``events.db`` + ``events.db-wal``
copied to a temp dir, opened ``mode=ro``). This is required because:

- ``EventStore``'s connect path runs ``maybe_upgrade_schema`` + commit and
  ``_tighten_wal_sidecars`` chmod — i.e. it mutates the file/sidecars.
- ``immutable=1`` would ignore ``-wal`` and read stale main-file data, missing
  any uncheckpointed publish (the store is WAL-mode and is not explicitly
  checkpointed, so committed rows can sit in ``-wal``).
- plain ``mode=ro`` on the *live* db must create the ``-shm`` wal-index, which
  touches the real store.

Copying ``events.db`` + ``-wal`` and opening the *copy* ``mode=ro`` (letting
SQLite rebuild ``-shm`` in the writable temp dir) gives fresh data with the
real store left byte-identical. Paths resolve through ``config._config_dir`` so
``BACKLINK_PUBLISHER_CONFIG_DIR`` is honored. Plan 2026-05-26-001 Unit 1.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backlink_publisher._util.url import canonicalize_url
from backlink_publisher.config import _config_dir

_DB_FILENAME = "events.db"
_WAL_SUFFIX = "-wal"
_HISTORY_FILENAME = "publish-history.json"

#: Stable ``articles`` columns the auditor depends on (R6/Dependencies). These
#: predate and are unaffected by the in-flight #235/#237 projector rewrite.
_ARTICLE_COLUMNS = (
    "article_id, host, live_url, target_urls_json, published_at_utc, run_id"
)


class AuditReadError(Exception):
    """``events.db`` is present but cannot be read (corruption, permission
    denial, schema-too-new). The CLI maps this to ``DependencyError`` (exit 3).

    Distinct from an *absent* store (a fresh operator), which is benign and
    surfaces as ``StoreSnapshot.nothing_to_audit`` → exit 0.
    """


def _canon(url: str | None) -> str:
    """Canonicalize, tolerating ``None``/blank (returns "")."""
    if not url:
        return ""
    return canonicalize_url(url)


def _events_db_path() -> Path:
    return _config_dir() / _DB_FILENAME


def _history_path() -> Path:
    return _config_dir() / _HISTORY_FILENAME


@dataclass(frozen=True)
class ArticleRow:
    """One ``articles`` row, stable columns only."""

    article_id: int
    host: str | None
    live_url: str | None
    target_urls_json: str
    published_at_utc: str | None
    run_id: str | None


@dataclass
class StoreSnapshot:
    """Point-in-time view of both stores.

    ``transient`` is set when a source file changed during the copy window or
    the copied db failed ``PRAGMA quick_check`` — callers down-classify any
    finding drawn from it as ``possibly-transient`` (R10). ``nothing_to_audit``
    is set when neither store exists yet (fresh operator → exit 0).
    """

    articles: list[ArticleRow]
    history: list[dict[str, Any]]
    transient: bool = False
    nothing_to_audit: bool = False


def _fingerprint(path: Path) -> tuple[float, str] | None:
    """``(mtime, sha256)`` for ``path``, or ``None`` if absent."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_mtime, hashlib.sha256(path.read_bytes()).hexdigest())


def _read_articles_from_snapshot(db_path: Path) -> tuple[list[ArticleRow], bool]:
    """Snapshot-copy ``db_path`` (+ its ``-wal``) and read ``articles`` from the
    copy. Returns ``(rows, transient)``. Raises ``AuditReadError`` if the
    present db cannot be copied/opened.
    """
    wal_path = db_path.with_name(db_path.name + _WAL_SUFFIX)
    before = (_fingerprint(db_path), _fingerprint(wal_path))

    tmp_dir = Path(tempfile.mkdtemp(prefix="bp-audit-"))
    try:
        copy_db = tmp_dir / _DB_FILENAME
        try:
            shutil.copy2(db_path, copy_db)
            if wal_path.exists():
                shutil.copy2(wal_path, tmp_dir / (_DB_FILENAME + _WAL_SUFFIX))
            # Deliberately NOT copying -shm: SQLite rebuilds the wal-index in the
            # (writable) temp dir from the copied -wal.
            conn = sqlite3.connect(f"file:{copy_db}?mode=ro", uri=True)
            try:
                conn.row_factory = sqlite3.Row
                quick = conn.execute("PRAGMA quick_check").fetchone()
                rows = [
                    ArticleRow(
                        article_id=r["article_id"],
                        host=r["host"],
                        live_url=r["live_url"],
                        target_urls_json=r["target_urls_json"],
                        published_at_utc=r["published_at_utc"],
                        run_id=r["run_id"],
                    )
                    for r in conn.execute(
                        f"SELECT {_ARTICLE_COLUMNS} FROM articles"
                    )
                ]
            finally:
                conn.close()
        except (OSError, sqlite3.Error) as exc:
            raise AuditReadError(f"cannot read events.db: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    after = (_fingerprint(db_path), _fingerprint(wal_path))
    quick_ok = quick is not None and quick[0] == "ok"
    transient = (before != after) or not quick_ok
    return rows, transient


def _read_history(history_path: Path) -> list[dict[str, Any]]:
    """Read ``publish-history.json`` directly (NOT via the import-frozen
    ``webui_store`` singleton). Tolerates an absent or malformed file.
    """
    if not history_path.exists():
        return []
    try:
        data = json.loads(history_path.read_text())
    except (OSError, ValueError) as exc:
        raise AuditReadError(f"cannot read publish-history.json: {exc}") from exc
    return data if isinstance(data, list) else []


def read_snapshot() -> StoreSnapshot:
    """Load both stores point-in-time. See ``StoreSnapshot``."""
    db_path = _events_db_path()
    history_path = _history_path()

    if not db_path.exists() and not history_path.exists():
        return StoreSnapshot(articles=[], history=[], nothing_to_audit=True)

    history = _read_history(history_path)
    if db_path.exists():
        articles, transient = _read_articles_from_snapshot(db_path)
    else:
        articles, transient = [], False

    return StoreSnapshot(articles=articles, history=history, transient=transient)
