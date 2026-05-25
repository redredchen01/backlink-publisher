"""Backlink Equity Ledger WebUI — per-target scorecard table.

Renders the same rows the ``equity-ledger`` CLI emits, from the same in-process
``ledger.build_ledger`` engine (no subprocess, no recomputation divergence).
GET is read-only; the on-demand recheck POST (U6) is the only mutation.
Plan 2026-05-25-004.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backlink_publisher.config import load_config
from backlink_publisher.ledger import build_ledger

from ..helpers.contexts import _render

bp = Blueprint("equity_ledger", __name__)


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
