"""Read-time projection backstop for the publishing health dashboard.

Plan 2026-05-25-006 / U1.

005-fix wires ``project_run_safe`` (→ ``flush_for`` on the *checkpoint* path)
inline at the end of every CLI publish/resume. Two gaps remain that only a
read-time pass can close:

1. ``publish-history.json`` is written by the WebUI publish flow and **nothing
   flushes it in production** — its reducer path stays dormant.
2. A publish that crashes before the inline flush strands a checkpoint that
   never projects.

``project_on_read`` is the dashboard's load-time backstop for both: it flushes
the history source and any un-projected (crash-stranded) checkpoints, records
corrupt sources in ``quarantine_log`` (a gap flag, cleared once they later
project), and NEVER raises — a locked/broken DB degrades to a stale result
instead of a 500.

Single-flight
-------------
A module-level ``threading.Lock`` serializes concurrent ``/health`` loads within
the Flask process — the real deployment, and what the ``threading.Barrier`` test
exercises. ``flush_for`` is itself idempotent (``projection_cursor`` diff) and
``articles.live_url`` is UNIQUE (cross-source confirmed dedup), so a re-flush is
a cheap no-op and cross-source duplicates of the same publish are rejected at the
DB layer. Full cross-*process* serialization of ``flush_for``'s cursor RMW would
require threading a shared connection through 005-fix's reducers, which Plan 006
scopes out (those reducers belong to 005-fix); the writes this module *owns*
(``quarantine_log``) use ``EventStore.connect_immediate`` for that guarantee.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config import _config_dir
from .projector import _HISTORY_FILENAME, ProjectionError, flush_for
from .store import EventStore

_log = logging.getLogger(__name__)

# Within-process single-flight: only one /health load projects at a time.
_PROJECTION_LOCK = threading.Lock()

# Terminal publish kinds whose latest ts_utc is the dashboard's freshness "as of".
_PUBLISH_KINDS = ("publish.confirmed", "publish.unverified", "publish.failed")


@dataclass(frozen=True)
class ReadProjectionResult:
    """Outcome of one read-time projection pass.

    ``degraded`` means the DB itself could not be read/written (locked, I/O) —
    the dashboard should render whatever it can with a "data may be incomplete"
    notice. ``gap`` means at least one source file could not be projected
    (corrupt/unparseable) and is parked in ``quarantine_log``.
    """

    events_inserted: int = 0
    sources_projected: int = 0
    latest_event_utc: str | None = None
    gap: bool = False
    gap_reason: str | None = None
    degraded: bool = False
    degraded_reason: str | None = None


def project_on_read(*, store: EventStore | None = None) -> ReadProjectionResult:
    """Flush the dormant history path + crash-stranded checkpoints; never raise.

    Single-flight within the process via ``_PROJECTION_LOCK``. Any failure
    degrades to a result object — the dashboard route must be able to render
    without a 500 even when the projection cannot make progress.
    """
    store = store or EventStore()
    try:
        with _PROJECTION_LOCK:
            return _project_all(store)
    except Exception as exc:  # noqa: BLE001 — backstop must never raise to the route
        _log.warning("health: project-on-read failed (non-fatal): %s", exc)
        return ReadProjectionResult(
            degraded=True, degraded_reason=f"{type(exc).__name__}: {exc}"
        )


def _project_all(store: EventStore) -> ReadProjectionResult:
    events_inserted = 0
    sources_projected = 0
    degraded = False
    degraded_reason: str | None = None

    for src in _collect_sources(store):
        try:
            result = flush_for(src, store=store)
        except FileNotFoundError:
            # Source vanished between collection and flush — nothing to do.
            continue
        except (ProjectionError, json.JSONDecodeError, ValueError) as exc:
            # Corrupt / unparseable source: park it, keep projecting the rest.
            _log.warning("health: quarantining unprojectable source %s: %s", src, exc)
            _quarantine(store, str(src), f"{type(exc).__name__}: {exc}")
            continue
        except sqlite3.OperationalError as exc:
            # DB-level failure affects every source — stop and degrade.
            degraded = True
            degraded_reason = f"{type(exc).__name__}: {exc}"
            _log.warning("health: project-on-read degraded mid-flush: %s", exc)
            break
        else:
            events_inserted += result.events_inserted
            sources_projected += 1
            # Clean projection clears any stale quarantine entry for this source.
            _clear_quarantine(store, str(src))

    latest = _latest_event_utc(store)
    gap_count = _open_quarantine_count(store)
    return ReadProjectionResult(
        events_inserted=events_inserted,
        sources_projected=sources_projected,
        latest_event_utc=latest,
        gap=gap_count > 0,
        gap_reason=(
            f"{gap_count} source(s) could not be projected" if gap_count else None
        ),
        degraded=degraded,
        degraded_reason=degraded_reason,
    )


def _collect_sources(store: EventStore) -> list[Path]:
    """History (always — the dormant path) + crash-stranded checkpoints.

    A checkpoint with a ``projection_cursor`` row was already projected by the
    inline ``project_run_safe`` at publish/resume end; only those with *no*
    cursor row are stranded (the publish crashed before its inline flush) and
    need re-projecting here. ``flush_for`` is idempotent regardless, so this
    gate is a cost bound, not a correctness requirement.
    """
    sources: list[Path] = []

    history = _config_dir() / _HISTORY_FILENAME
    if history.exists():
        sources.append(history)

    from ..checkpoint import _checkpoint_dir

    cp_dir = _checkpoint_dir()
    if cp_dir.exists():
        known = _known_cursor_sources(store)
        for cp in sorted(cp_dir.glob("*.json")):
            if str(cp) not in known:
                sources.append(cp)
    return sources


def _known_cursor_sources(store: EventStore) -> set[str]:
    rows = store.query("SELECT source FROM projection_cursor")
    return {r["source"] for r in rows}


def _latest_event_utc(store: EventStore) -> str | None:
    placeholders = ",".join("?" for _ in _PUBLISH_KINDS)
    rows = store.query(
        f"SELECT MAX(ts_utc) AS m FROM events WHERE kind IN ({placeholders})",
        _PUBLISH_KINDS,
    )
    if rows and rows[0]["m"] is not None:
        return str(rows[0]["m"])
    return None


def _open_quarantine_count(store: EventStore) -> int:
    rows = store.query("SELECT COUNT(*) AS n FROM quarantine_log")
    return int(rows[0]["n"]) if rows else 0


def _quarantine(
    store: EventStore, source: str, reason: str, raw: str | None = None
) -> None:
    """Park an unprojectable source. De-duped by source so retries don't pile up."""
    now = datetime.now(timezone.utc).isoformat()
    with store.connect_immediate() as conn:
        existing = conn.execute(
            "SELECT 1 FROM quarantine_log WHERE source = ? LIMIT 1", (source,)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO quarantine_log "
                "(ts_utc, source, run_id, reason, raw_payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, source, None, reason, raw),
            )


def _clear_quarantine(store: EventStore, source: str) -> None:
    with store.connect_immediate() as conn:
        conn.execute("DELETE FROM quarantine_log WHERE source = ?", (source,))
