"""Dedup recording + (later) gate hooks for the publish path.

Unit 2 (this module, observe phase): record dedup state on every dispatch across
**both** the fresh (``publish_backlinks``) and resume (``_resume``) seams, WITHOUT
gating — publish behavior is unchanged. Unit 7 adds the enforce gate that consults
these records.

All recording is **observe-safe**: a dedup-store failure is logged and swallowed so
it can never break a publish run. The intent write runs before dispatch (so a crash
leaves ``attempting``); the terminal write runs on the dispatch outcome.

Failure → state mapping (R8, conservative): only ``http_5xx`` (the may-have-committed
class) maps to ``uncertain``; every other error class is ``failed`` (re-publishable).
``classify_exception`` is message-based and cannot truly see send-state, so this is a
conservative hold, not a precise one.

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U2).
"""

from __future__ import annotations

import os
from typing import Any

from ..idempotency import DedupKey, DedupStore
from .._util.logger import get_logger

#: Single account per channel today; carried in the key so a future second
#: account on the same platform is a distinct key (see plan Key Decisions).
_ACCOUNT_DEFAULT = "default"

_log = get_logger("dedup")


def _key_for_row(row: dict[str, Any], platform: str) -> DedupKey | None:
    target = (row or {}).get("target_url")
    if not target or not platform:
        return None
    try:
        return DedupKey(platform=platform, target_url=str(target), account=_ACCOUNT_DEFAULT)
    except Exception:  # canonicalization on a malformed URL must not break publish
        return None


def terminal_for_error_class(error_class: str | None) -> str:
    """``http_5xx`` may have committed server-side → hold (``uncertain``);
    everything else is confirmed-not-landed → ``failed`` (re-publishable)."""
    return "uncertain" if error_class == "http_5xx" else "failed"


def record_intent(row: dict[str, Any], platform: str, *, run_id: str | None) -> None:
    """Observe-safe ``absent -> attempting`` before dispatch. A lost race (key
    already present) is a no-op here; the terminal recorder handles existing rows."""
    key = _key_for_row(row, platform)
    if key is None:
        return
    try:
        DedupStore().intent_write(
            key, run_id=run_id, owner_pid=os.getpid(), owner_run_id=run_id
        )
    except Exception as exc:  # observe-only: never break the run
        _log.debug(f"dedup intent_write skipped: {exc}")


def record_done(
    row: dict[str, Any],
    platform: str,
    *,
    live_url: str | None,
    verify_ok: bool,
    run_id: str | None,
) -> None:
    """Observe-safe terminal ``done`` write on a successful dispatch."""
    _record_terminal(row, platform, "done", live_url=live_url, verify_ok=verify_ok, run_id=run_id)


def record_failure(
    row: dict[str, Any],
    platform: str,
    *,
    error_class: str | None,
    run_id: str | None,
) -> None:
    """Observe-safe terminal ``failed``/``uncertain`` write on a failed dispatch."""
    _record_terminal(
        row, platform, terminal_for_error_class(error_class), run_id=run_id
    )


def _record_terminal(
    row: dict[str, Any],
    platform: str,
    state: str,
    *,
    live_url: str | None = None,
    verify_ok: bool | None = None,
    run_id: str | None = None,
) -> None:
    key = _key_for_row(row, platform)
    if key is None:
        return
    try:
        store = DedupStore()
        rec = store.get(key)
        if rec is None:
            # No intent row (intent write lost/failed) — observe-only, skip rather
            # than fabricate a row out of band.
            return
        if rec.state in ("done", "failed"):
            # Already terminal (a prior run, or an observe re-dispatch of an
            # already-done key). Do not re-transition; leave the existing record.
            # Backfilling a second observe-mode post's live_url is a documented
            # refinement deferred past observe (see plan U2 Approach).
            return
        store.transition(
            key, state, live_url=live_url, verify_ok=verify_ok, run_id=run_id
        )
    except Exception as exc:  # observe-only: never break the run
        _log.debug(f"dedup terminal write skipped ({state}): {exc}")
