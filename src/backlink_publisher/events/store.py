"""SQLite-backed event store for the read-side projection (events.db).

EventStore is a sibling of JsonStore (not a subclass): JsonStore's
contract is dict/list-shaped JSON files; events.db is schema-bound. The
projector (U4) translates JsonStore writes into EventStore appends.

Connection options (plan §U1 Approach):
- ``journal_mode = WAL`` — concurrent readers + single writer; the file
  layout adds ``events.db-wal`` and ``events.db-shm`` side files.
- ``synchronous = NORMAL`` — durability tradeoff acceptable for a
  rebuildable read-side projection (events.db is recoverable from JSON
  via ``bp-events-rebuild``).
- ``busy_timeout = 5000`` — 5s wait for a write lock before raising; the
  retry layer below adds another bounded backoff on transient errors.
- ``foreign_keys = ON`` — defensive even though v1 has no FK constraints.

File mode 0600, parent dir 0700; macOS Time Machine exclusion via xattr
attempted on first create (failure WARNs, never raises — U10 expands
coverage to ``.db-wal``/``.db-shm``/``persona.salt`` etc).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from ..config import _config_dir
from . import schema as _schema

#: Default filename inside ``_config_dir()``.
_DB_FILENAME: str = "events.db"

#: Max retry attempts on ``sqlite3.OperationalError`` (typically "disk I/O
#: error" or "database is locked" if it slips past busy_timeout). Three is
#: a deliberate small number; persistent errors should bubble to the
#: caller rather than mask a real problem.
_MAX_RETRIES: int = 3

#: Base backoff between retries (seconds). Multiplied by attempt number
#: for crude linear backoff. Injectable so tests can use a no-op sleep.
_BASE_BACKOFF_S: float = 0.05

#: macOS xattr key for Time Machine / iCloud backup exclusion.
_XATTR_BACKUP_EXCLUDE: str = "com.apple.metadata:com_apple_backup_excludeItem"


def _default_db_path() -> Path:
    return _config_dir() / _DB_FILENAME


def _is_select_statement(sql: str) -> bool:
    """Return True iff ``sql`` is a single SELECT or WITH-prefixed SELECT.

    Trims leading whitespace and SQL block comments. Rejects multi-
    statement input (anything past the first ``;`` that isn't trailing
    whitespace) — caller in the SELECT-only contract must not chain.
    """
    stripped = sql.lstrip()
    # Strip leading ``/* ... */`` block comments (one level is enough; we
    # do not need to support nested comments here).
    while stripped.startswith("/*"):
        end = stripped.find("*/")
        if end == -1:
            return False
        stripped = stripped[end + 2 :].lstrip()
    head = stripped[:10].upper()
    if not (head.startswith("SELECT ") or head.startswith("SELECT\n")
            or head.startswith("WITH ") or head.startswith("WITH\n")):
        return False
    # Multi-statement guard: a trailing ``;`` is fine, but anything
    # non-whitespace after it is rejected.
    tail = stripped.rstrip()
    if ";" in tail[:-1]:
        return False
    return True


def _tighten_wal_sidecars(db_path: Path) -> None:
    """Chmod ``db_path``-wal / ``-shm`` to 0o600 if present.

    SQLite creates WAL/SHM lazily on first write using the process umask,
    which is typically 0o022 → 0o644 — wide enough to leak uncheckpointed
    event payloads. Best-effort: missing files and chmod failures are
    silent (Windows POSIX modes are not meaningful; macOS sandbox or
    file-system flags can also reject chmod).
    """
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            try:
                os.chmod(side, 0o600)
            except OSError:
                pass


def _set_backup_exclude_xattr(path: Path) -> None:
    """Best-effort backup-exclusion mark on macOS; no-op elsewhere.

    Plan §U10 extends this to ``persona.salt``, ``token/``, and the WAL
    side files. U1 only handles ``events.db`` itself at first create.
    Failures are silent (subprocess missing, kernel rejects) — the file
    is still created and usable; backup exclusion is defense-in-depth.
    """
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["xattr", "-w", _XATTR_BACKUP_EXCLUDE, "1", str(path)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        # xattr binary missing or subprocess died. Don't crash event-store
        # init over a backup-hygiene improvement.
        pass


def _is_transient_sqlite_error(exc: sqlite3.OperationalError) -> bool:
    """Whether ``exc`` is worth retrying.

    Restricted to the two messages we expect to recover from automatically
    — disk-I/O glitches and lock-contention misses. Anything else (table
    missing, syntax error, type mismatch) is a programming error and
    should surface immediately.
    """
    msg = str(exc).lower()
    return "disk i/o error" in msg or "database is locked" in msg


def _retry_sqlite(
    op: Callable[[], Any],
    *,
    max_retries: int = _MAX_RETRIES,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Any:
    """Run ``op`` with bounded retry on transient ``OperationalError``.

    Tests inject ``sleep_fn`` to skip real sleeping. The same error class
    is re-raised after ``max_retries`` exhausted so callers see the
    underlying failure rather than a synthetic wrapper.
    """
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return op()
        except sqlite3.OperationalError as exc:
            if not _is_transient_sqlite_error(exc):
                raise
            last_exc = exc
            if attempt < max_retries:
                sleep_fn(_BASE_BACKOFF_S * attempt)
    assert last_exc is not None
    raise last_exc


class EventStore:
    """Append-mostly SQLite store for projected events + articles.

    Construction does not open the file; the first ``connect()`` call
    creates the database (and applies the schema) if absent. Pass
    ``path=`` to override the default location for tests.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path: Path = path if path is not None else _default_db_path()
        self._sleep_fn = sleep_fn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection, apply PRAGMAs + schema, yield to caller.

        Acts as a transaction boundary: pending work is committed on
        normal exit, rolled back on exception, and the connection is
        always closed. Idempotent: schema upgrade runs every connect,
        but is itself a no-op when the version is current.

        File hygiene: parent directory is tightened to 0o700 on first
        create regardless of any pre-existing mode (events.db sits next
        to ``persona.salt`` / ``token/``, and a wider parent leaks
        sensitive sibling filenames). The WAL and SHM side files are
        chmodded to 0o600 after their first appearance — SQLite creates
        them with the process umask (typically 0o644) which would
        otherwise expose uncheckpointed event payloads.
        """
        first_create = not self.path.exists()
        if first_create:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # mkdir(exist_ok=True) does NOT apply mode= to a pre-existing
            # directory. Tighten unconditionally on first create so the
            # parent does not leak siblings via 0o755.
            try:
                os.chmod(self.path.parent, 0o700)
            except OSError:
                pass

        conn = sqlite3.connect(str(self.path), timeout=5.0)
        try:
            # Apply PRAGMAs before touching tables — WAL mode in particular
            # must be set on a fresh connection.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA foreign_keys = ON")
            _schema.maybe_upgrade_schema(conn)
            conn.commit()

            if first_create:
                # Mode + xattr only on the first creation; subsequent
                # ``connect`` calls do not re-chmod (operator may have
                # widened the mode intentionally).
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
                _set_backup_exclude_xattr(self.path)

            # WAL/SHM appear lazily on first write and inherit umask
            # rather than the .db file's mode. Tighten every connect so
            # post-checkpoint recreations stay locked down.
            _tighten_wal_sidecars(self.path)

            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        finally:
            conn.close()

    def append(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        run_id: str | None = None,
        target_url: str | None = None,
        host: str | None = None,
        article_id: int | None = None,
        ts_raw: str | None = None,
        ts_utc: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """INSERT one row into ``events`` and return its id.

        ``payload`` is JSON-serialised with ``sort_keys=True`` so a given
        logical event always produces the same byte string regardless of
        dict construction order — eases round-trip tests and diff
        tooling. Whitelist-based field pruning lands in U2.

        ``ts_raw`` defaults to ``ts_utc``; both default to "now" UTC. The
        projector (U4) supplies the source's original timestamp.

        ``conn`` lets the caller share a transaction across multiple
        appends / ``add_article`` calls — useful for projector reducers
        that must atomically emit an event plus its article row. When
        ``conn`` is ``None`` a private connection is opened, used, and
        committed; callers are then *not* protected against partial
        writes across multiple operations.
        """
        if ts_utc is None:
            ts_utc = _now_iso_utc()
        if ts_raw is None:
            ts_raw = ts_utc
        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        params = (
            ts_raw, ts_utc, run_id, kind, target_url, host,
            article_id, payload_json,
        )
        sql = (
            "INSERT INTO events "
            "(ts_raw, ts_utc, run_id, kind, target_url, host, "
            " article_id, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )

        if conn is not None:
            cursor = conn.execute(sql, params)
            row_id = cursor.lastrowid
            assert row_id is not None
            return int(row_id)

        def _op() -> int:
            with self.connect() as own_conn:
                cursor = own_conn.execute(sql, params)
                own_conn.commit()
                row_id = cursor.lastrowid
                assert row_id is not None
                return int(row_id)

        return _retry_sqlite(_op, sleep_fn=self._sleep_fn)

    def add_article(
        self,
        article: dict[str, Any],
        *,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """INSERT one row into ``articles`` and return its article_id.

        Accepted keys: ``body``, ``anchors_json``, ``target_urls_json``,
        ``lang``, ``host``, ``live_url``, ``published_at_raw``,
        ``published_at_utc``, ``run_id``. Unknown keys raise
        ``KeyError`` so a typo doesn't silently drop data — the
        whitelist-vs-shape distinction matters more for the events
        table (U2 enforces it there).

        Raises ``sqlite3.IntegrityError`` if ``live_url`` already exists.
        Callers (projector reducers, U4) catch and route to dedup.

        ``conn`` shares a transaction with the caller (see ``append``).
        """
        allowed = {
            "body", "anchors_json", "target_urls_json", "lang", "host",
            "live_url", "published_at_raw", "published_at_utc", "run_id",
        }
        unknown = set(article) - allowed
        if unknown:
            raise KeyError(f"unknown article columns: {sorted(unknown)}")

        # SQLite default for missing TEXT columns is NULL; the schema
        # provides DEFAULT '[]' for anchors_json / target_urls_json.
        cols = list(article.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        sql = f"INSERT INTO articles ({col_list}) VALUES ({placeholders})"
        params = tuple(article[c] for c in cols)

        if conn is not None:
            cursor = conn.execute(sql, params)
            row_id = cursor.lastrowid
            assert row_id is not None
            return int(row_id)

        def _op() -> int:
            with self.connect() as own_conn:
                cursor = own_conn.execute(sql, params)
                own_conn.commit()
                row_id = cursor.lastrowid
                assert row_id is not None
                return int(row_id)

        return _retry_sqlite(_op, sleep_fn=self._sleep_fn)

    def query(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[sqlite3.Row]:
        """Thin SELECT wrapper for CLI consumers. Returns all rows eagerly.

        Enforces a SELECT-only contract at runtime — refuses any
        statement that doesn't start with ``SELECT`` so a downstream CLI
        (U7/U8) accidentally f-stringing user input into a DML statement
        cannot DROP / ATTACH / INSERT through this entry point. For
        admin-style mutations, use ``connect()`` directly and own the
        risk explicitly.
        """
        if not _is_select_statement(sql):
            raise ValueError(
                "EventStore.query() is SELECT-only; use connect() for DML."
            )

        def _op() -> list[sqlite3.Row]:
            with self.connect() as conn:
                conn.row_factory = sqlite3.Row
                return list(conn.execute(sql, params))

        return _retry_sqlite(_op, sleep_fn=self._sleep_fn)


def _now_iso_utc() -> str:
    """ISO-8601 UTC timestamp, e.g. ``2026-05-18T12:00:00+00:00``."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
