"""Channel binding routes — Plan 2026-05-19-001 Unit 4.

POST /settings/channels/<channel>/bind          — start a bind job
GET  /settings/channels/<channel>/bind/<job_id> — poll status + events

Both routes are loopback-only (Blueprint-scoped ``before_request``) and
the POST route requires a valid CSRF token (existing pattern). Channel
membership is validated against ``CHANNELS`` before any subprocess
spawn — defense in depth against ``channel=../traversal``.
"""

from __future__ import annotations

from flask import Blueprint, abort, jsonify, request

from backlink_publisher._util.errors import UsageError
from backlink_publisher.cli._bind.channels import CHANNELS

from ..helpers import _check_csrf_or_abort, _LOOPBACK_HOSTS
from ..services.bind_job import registry as _bind_registry


bp = Blueprint("bind", __name__)


@bp.before_request
def _enforce_loopback() -> None:
    if request.remote_addr not in _LOOPBACK_HOSTS:
        abort(403)


@bp.route("/settings/channels/<channel>/bind", methods=["POST"])
def start_bind(channel: str):
    _check_csrf_or_abort()
    if channel not in CHANNELS:
        abort(400)
    try:
        job = _bind_registry.start(channel)
    except UsageError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    return jsonify({"job_id": job.id, "channel": channel, "status": "running"})


@bp.route("/settings/channels/<channel>/bind/<job_id>", methods=["GET"])
def poll_bind(channel: str, job_id: str):
    if channel not in CHANNELS:
        abort(400)
    snapshot = _bind_registry.poll(job_id)
    if snapshot is None:
        abort(404)
    if snapshot["channel"] != channel:
        abort(404)
    return jsonify(snapshot)
