"""Task Dashboard Route — Plan 2026-05-18-001."""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from webui_store import queue_store

from ..helpers import _render

bp = Blueprint("dashboard", __name__)


@bp.route('/ce:dashboard', methods=['GET'])
def ce_dashboard():
    tasks = queue_store.load()
    sorted_tasks = sorted(tasks, key=lambda x: x.get('created_at', ''), reverse=True)
    return _render('dashboard.html', tasks=sorted_tasks)


@bp.route('/ce:retry-task', methods=['POST'])
def ce_retry_task():
    task_id = request.form.get('task_id')
    if not task_id:
        return jsonify({'status': 'error', 'message': 'Missing task_id'})

    queue_store.update_task(task_id, {'status': 'pending', 'error': None})
    return jsonify({'status': 'success', 'message': '任务已重置为待发布状态'})
