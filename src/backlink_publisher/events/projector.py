"""Projection from JSON state files into the read-side event store.

The projector reads checkpoint / publish-history / draft-queue JSON files,
diffs them against the per-source cursor in ``projection_cursor``, and
emits ``events`` + ``articles`` rows for the new state. Each reducer runs
inside a single ``EventStore.connect()`` transaction so a partial flush
can't half-update the database.

Idempotency is layered (plan §U4):

1. In-transaction ``seen_in_tx`` set keyed per kind catches duplicate
   logical changes emitted within one flush.
2. ``projection_cursor.last_seen_state_json`` diff keeps a no-op flush
   from inserting any rows.
3. ``articles.live_url`` UNIQUE rejects cross-source duplicates at the
   DB layer — caller catches ``IntegrityError`` and skips the matching
   ``publish.confirmed``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from ._project_helpers import ProjectionError, cursor_load, cursor_save, detect_source
from ._project_reducers import (
    ProjectionResult,
    _project_checkpoint,
    _project_drafts,
    _project_history,
)
from .store import EventStore

_log = logging.getLogger(__name__)


def flush_for(
    path: Path,
    *,
    store: EventStore | None = None,
) -> ProjectionResult:
    """Project the JSON source at ``path`` into the event store.

    Dispatches by detecting whether ``path`` is a checkpoint, history,
    or drafts file. Each reducer runs in one transaction; on any
    unexpected exception the transaction rolls back and the cursor is
    left unchanged.
    """
    store = store or EventStore()
    source_kind = detect_source(path)
    if source_kind == "checkpoint":
        return _project_checkpoint(path, store)
    if source_kind == "history":
        return _project_history(path, store)
    if source_kind == "drafts":
        return _project_drafts(path, store)
    raise ProjectionError(f"unknown source for path: {path}")


# Reserved projection_cursor key for the projection-health marker (Plan 005 /
# U4). Reuses the existing table — no schema migration — so the dashboard and
# an operator can see whether the projection is fresh or silently failing.
_HEALTH_SOURCE = "__projection_health__"

# R10 mass-quarantine alarm: a run whose quarantine ratio reaches this fraction
# of considered records records a `degraded` health signal — so a flood (an
# upstream status vocabulary drifting wholesale) can't pass as a clean run even
# though quarantine-and-continue lets the run finish. Relative (not absolute) so
# it catches a small all-quarantined run, not just large ones.
_QUARANTINE_DEGRADED_RATIO = 0.25


def record_projection_health(
    store: EventStore,
    *,
    ok: bool,
    error: str | None = None,
    quarantine_ratio: float | None = None,
) -> None:
    """Persist the last projection outcome so swallowed failures are visible.

    Fail-safe in its own right: never raises (the DB may be the thing that is
    locked/broken). Stored under a reserved ``projection_cursor`` row. When
    ``quarantine_ratio`` is supplied, sets a ``degraded`` flag (R10) once it
    reaches ``_QUARANTINE_DEGRADED_RATIO`` — a healthy ``ok=True`` run can still
    be degraded if a flood of records quarantined.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        with store.connect() as conn:
            state = dict(cursor_load(conn, _HEALTH_SOURCE))
            if ok:
                state["last_ok_at"] = now
                state["last_error"] = None
            else:
                state["last_error"] = error
                state["last_error_at"] = now
            if quarantine_ratio is not None:
                state["last_quarantine_ratio"] = quarantine_ratio
                state["degraded"] = quarantine_ratio >= _QUARANTINE_DEGRADED_RATIO
            cursor_save(conn, _HEALTH_SOURCE, state, mtime=None)
    except Exception as exc:  # noqa: BLE001 — health recording is best-effort
        _log.warning("projector: could not record projection health: %s", exc)


def project_run_safe(
    run_id: str, *, store: EventStore | None = None
) -> ProjectionResult | None:
    """Project a finished run's checkpoint into ``events.db`` — fail-safe.

    Called inline at the end of publish/resume (Plan 005 / R2). It MUST NOT
    raise: a projection failure (a locked DB ``sqlite3.OperationalError`` from a
    concurrent writer, a ``ProjectionError``, a missing checkpoint) is logged
    and swallowed so the publish result is unaffected. ``flush_for`` is
    idempotent, so the dashboard's project-on-read remains a safe backstop.
    """
    store = store or EventStore()
    try:
        from ..checkpoint import checkpoint_path

        result = flush_for(checkpoint_path(run_id), store=store)
        ratio = (
            result.quarantined / result.records_considered
            if result.records_considered
            else 0.0
        )
        record_projection_health(store, ok=True, quarantine_ratio=ratio)
        return result
    except Exception as exc:  # noqa: BLE001 — projection must never fail publish
        _log.warning(
            "projector: projection after run %s failed (non-fatal): %s",
            run_id, exc,
        )
        record_projection_health(
            store, ok=False, error=f"{type(exc).__name__}: {exc}"
        )
        return None
