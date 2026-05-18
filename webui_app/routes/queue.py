"""Background Task Submission Route — Plan 2026-05-18-001."""

from __future__ import annotations

import uuid
import json
from flask import Blueprint, request, jsonify
from webui_store import queue_store
from datetime import datetime, timezone

bp = Blueprint("queue", __name__)

@bp.route('/ce:queue-task', methods=['POST'])
def ce_queue_task():
    # 提取表单数据，逻辑与 ce_generate/ce_preview 保持一致
    task_data = {
        'id': str(uuid.uuid4()),
        'status': 'pending',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'config': request.form.to_dict(),
        'urls': json.loads(request.form.get('urls_json', '[]'))
    }
    
    queue_store.update(lambda tasks: tasks + [task_data])
    
    return jsonify({
        'status': 'queued',
        'task_id': task_data['id'],
        'message': '任务已提交到后台队列'
    })
