"""/ce:history* — Plan Unit 3 + Plan 2026-05-19-006 bulk operations.

Plan 012 Unit 2: owns the `/ce:retry-task` POST endpoint (moved from
the `dashboard` blueprint along with `/ce:dashboard` becoming a 302
redirect to `/ce:history?section=in-progress`).

Phase A refactoring: delegates to ``HistoryAPI`` for all operations.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, redirect, request, session

from ..api import HistoryAPI
from ..helpers.contexts import _draft_tab_extra, _render

bp = Blueprint("history", __name__)
_history = HistoryAPI()


@bp.route('/ce:history', methods=['GET', 'POST'])
def ce_history():
    config = session.get('config', {})
    return _render('index.html',
        history=_history.list(),
        history_active=True,
        config=config,
        **_draft_tab_extra())


@bp.route('/ce:history/delete', methods=['POST'])
def ce_history_delete():
    item_id = request.form.get('id', '')
    result = _history.delete(item_id)
    return _render('index.html',
        history=result.get("history", _history.list()),
        history_active=True,
        config=session.get('config', {}))


@bp.route('/ce:history/update-status', methods=['POST'])
def ce_history_update_status():
    item_id = request.form.get('id', '')
    new_status = request.form.get('status', '')
    result = _history.update_status(item_id, new_status)
    return _render('index.html',
        history=result.get("history", _history.list()),
        history_active=True,
        config=session.get('config', {}))


@bp.route('/ce:history/reuse', methods=['POST'])
def ce_history_reuse():
    target_url = request.form.get('target_url', '')
    session.pop('plans', None)
    session.pop('validated', None)
    return _render('index.html', target_url=target_url,
                   config=session.get('config', {}))


# ── Bulk operations —───────────────────────────────────────────────────────


@bp.route('/ce:history/bulk-delete', methods=['POST'])
def ce_history_bulk_delete():
    """Delete multiple history entries by id."""
    ids = request.form.getlist('ids')
    result = _history.bulk_delete(ids)
    flash_type = "success" if result["ok"] else "warning"
    return redirect(f'/ce:history?flash_type={flash_type}&flash_msg={result["flash_msg"]}')


@bp.route('/ce:history/purge-failed', methods=['POST'])
def ce_history_purge_failed():
    """One-shot delete every history entry whose status is exactly 'failed'."""
    result = _history.purge_failed()
    flash_type = "success" if result["ok"] else "info"
    return redirect(f'/ce:history?flash_type={flash_type}&flash_msg={result["flash_msg"]}')


@bp.route('/ce:history/recheck', methods=['POST'])
def ce_history_recheck():
    """Re-verify a single history item by id."""
    item_id = request.form.get('id', '')
    result = _history.recheck(item_id)
    flash_type = "success" if result["ok"] else "danger"
    return redirect(f'/ce:history?flash_type={flash_type}&flash_msg={result["flash_msg"]}')


@bp.route('/ce:retry-task', methods=['POST'])
def ce_retry_task():
    """Plan 012 Unit 2: moved from dashboard blueprint. URL is unchanged."""
    task_id = request.form.get('task_id', '')
    result = _history.retry_task(task_id)
    if not result["ok"]:
        return jsonify({'status': 'error', 'message': result.get("message", "Missing task_id")})
    return jsonify({'status': 'success', 'message': result["message"]})


@bp.route('/ce:history/bulk-recheck', methods=['POST'])
def ce_history_bulk_recheck():
    """Re-verify multiple history entries; updates store in one pass."""
    ids = request.form.getlist('ids')
    result = _history.bulk_recheck(ids)
    flash_type = "success" if result["ok"] else "warning"
    return redirect(f'/ce:history?flash_type={flash_type}&flash_msg={result["flash_msg"]}')
