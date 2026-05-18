"""SQLite schema for events.db (read-side projection).

Schema version 1 — initial. Future migrations append a row to
``schema_version`` and a step to ``maybe_upgrade_schema``.

The FTS5 virtual table is intentionally NOT created in v1 (plan §RBP-2);
``maybe_create_fts5`` is a stub kept here as a schema slot.
"""

from __future__ import annotations

import sqlite3

#: Current schema version. Bump when adding a migration step.
SCHEMA_VERSION: int = 1


class SchemaTooNewError(RuntimeError):
    """Raised when the on-disk schema_version is higher than the binary's.

    Triggered by a v(N+1) binary writing then a v(N) binary attempting to
    open the same file. v(N+1) may have added columns whose constraints
    v(N) cannot honour; refusing to open avoids silently mis-projecting
    rows and corrupting downstream queries.
    """


_DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_raw TEXT NOT NULL,
        ts_utc TEXT NOT NULL,
        run_id TEXT,
        kind TEXT NOT NULL,
        target_url TEXT,
        host TEXT,
        article_id INTEGER,
        payload_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts_utc)",
    "CREATE INDEX IF NOT EXISTS idx_events_host_kind ON events(host, kind)",
    "CREATE INDEX IF NOT EXISTS idx_events_article_kind ON events(article_id, kind)",
    """
    CREATE TABLE IF NOT EXISTS articles (
        article_id INTEGER PRIMARY KEY AUTOINCREMENT,
        body TEXT,
        anchors_json TEXT NOT NULL DEFAULT '[]',
        target_urls_json TEXT NOT NULL DEFAULT '[]',
        lang TEXT,
        host TEXT,
        live_url TEXT UNIQUE,
        published_at_raw TEXT,
        published_at_utc TEXT,
        run_id TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_articles_host_pub ON articles(host, published_at_utc)",
    "CREATE INDEX IF NOT EXISTS idx_articles_run ON articles(run_id)",
    """
    CREATE TABLE IF NOT EXISTS projection_cursor (
        source TEXT PRIMARY KEY,
        last_mtime REAL,
        last_checksum TEXT,
        last_seen_state_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quarantine_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        source TEXT,
        run_id TEXT,
        reason TEXT NOT NULL,
        raw_payload_json TEXT
    )
    """,
)


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Apply the DDL for the current schema version against ``conn``.

    Idempotent — every statement uses ``CREATE TABLE IF NOT EXISTS`` or
    ``CREATE INDEX IF NOT EXISTS``. Caller is responsible for committing.
    """
    cursor = conn.cursor()
    for ddl in _DDL_STATEMENTS:
        cursor.execute(ddl)
    # Insert the current version row only if the table is empty.
    cursor.execute("SELECT COUNT(*) FROM schema_version")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))


def current_schema_version(conn: sqlite3.Connection) -> int:
    """Return the highest schema_version row, or 0 if the table is empty.

    A return of 0 means either: (a) the database is brand new and
    ``initialize_schema`` has not been called yet, or (b) someone provisioned
    the file manually without ever populating ``schema_version``. The caller
    treats both cases the same — run ``initialize_schema``.
    """
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    if row is None or row[0] is None:
        return 0
    return int(row[0])


def maybe_upgrade_schema(conn: sqlite3.Connection) -> None:
    """Bring the database up to ``SCHEMA_VERSION``.

    For v1 this is just ``initialize_schema``; subsequent versions chain
    additional migrations gated on ``current_schema_version``.

    Refuses to open a database whose on-disk version exceeds this
    binary's ``SCHEMA_VERSION`` — that file was written by a newer
    binary and may have columns or constraints the current code cannot
    honour. The caller is expected to upgrade the binary or use the
    bundled ``bp-events-rebuild --force`` flow to start over.
    """
    version = current_schema_version(conn)
    if version > SCHEMA_VERSION:
        raise SchemaTooNewError(
            f"events.db schema is v{version}; this binary supports up "
            f"to v{SCHEMA_VERSION}. Upgrade the binary or rebuild with "
            "`bp-events-rebuild --force`."
        )
    if version < SCHEMA_VERSION:
        initialize_schema(conn)
        # Future: if version < 2: apply v1→v2 migration, INSERT (2), etc.


def maybe_create_fts5(conn: sqlite3.Connection) -> None:  # pragma: no cover - v1 stub
    """FTS5 virtual table slot reserved per plan §RBP-2; not created in v1.

    Kept here as a documentation anchor so the future consumer that needs
    full-text search has an obvious extension point.
    """
    return None
