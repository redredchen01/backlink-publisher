"""Backlink Equity Ledger WebUI — per-target scorecard table.

Renders the same rows the ``equity-ledger`` CLI emits, from the same in-process
``ledger.build_ledger`` engine (no subprocess, no recomputation divergence).
GET is read-only; the on-demand recheck POST (U6) is the only mutation.
Plan 2026-05-25-004.
"""

from __future__ import annotations

from collections import Counter

from flask import Blueprint, jsonify, request

from backlink_publisher._util.url import canonicalize_url
from backlink_publisher.config import load_config
from backlink_publisher.ledger import build_ledger

from ..helpers.contexts import _render

bp = Blueprint("equity_ledger", __name__)

# _outcome (from recheck_one) → delta-summary bucket.
_OUTCOME_LABELS = {"confirmed": "confirmed", "downgraded": "failed", "skipped": "skipped"}


def _resolve_stale_days() -> int:
    try:
        days = int(request.args.get("stale_days", 30))
    except (TypeError, ValueError):
        return 30
    return days if days > 0 else 30


@bp.route("/ce:equity-ledger", methods=["GET"])
def equity_ledger():
    stale_days = _resolve_stale_days()
    cfg = load_config()
    rows = [row.to_jsonl_dict() for row in build_ledger(stale_days=stale_days)]
    stale_count = sum(1 for r in rows if r["liveness"] in ("stale", "failed"))
    return _render(
        "equity_ledger.html",
        rows=rows,
        stale_days=stale_days,
        stale_count=stale_count,
        exact_match_threshold=cfg.anchor_alarm.exact_ratio_ceiling,
    )


@bp.route("/ce:equity-ledger/recheck", methods=["POST"])
def equity_ledger_recheck():
    """On-demand recheck of one target's links (operator-initiated, U6).

    A canonical target can be backed by several history rows; recheck iterates
    every backing row, calls ``recheck_one`` per row, and writes back via the
    canonical ``update_item`` helper. The target's row is then recomputed and
    returned for in-place refresh. No scheduler, no background job.
    """
    from webui_store import history_store

    from ..services.recheck import recheck_one

    data = request.get_json(silent=True) or {}
    target = data.get("target_url")
    if not target:
        return jsonify({"error": "target_url required"}), 400
    # Honor the same staleness window the table was rendered with, so the
    # refreshed row's stale flag matches the rest of the view.
    try:
        stale_days = int(data.get("stale_days", 30))
    except (TypeError, ValueError):
        stale_days = 30
    if stale_days <= 0:
        stale_days = 30
    canon = canonicalize_url(target)

    row = next((r for r in build_ledger(stale_days=stale_days) if r.target_url == canon), None)
    if row is None:
        return jsonify({"error": "target not found"}), 404

    counts: Counter[str] = Counter()
    for item_id in row.history_item_ids:
        item = history_store.get_item(item_id)
        if not item:
            counts["skipped"] += 1  # deleted between snapshot and recheck
            continue
        try:
            mutation = recheck_one(item)
        except Exception as exc:  # one bad item must not abort the whole batch
            history_store.update_item(item_id, status="failed", verify_error=str(exc))
            counts["failed"] += 1
            continue
        outcome = mutation.pop("_outcome", None)
        history_store.update_item(item_id, **mutation)
        counts[_OUTCOME_LABELS.get(outcome, "skipped")] += 1

    # Recompute the target's row from the freshly mutated history.
    refreshed = next((r for r in build_ledger(stale_days=stale_days) if r.target_url == canon), row)
    summary = (
        f"{counts['confirmed']} confirmed, "
        f"{counts['failed']} failed, {counts['skipped']} skipped"
    )
    return jsonify({"row": refreshed.to_jsonl_dict(), "summary": summary})
