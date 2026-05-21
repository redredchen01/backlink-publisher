"""Channel binding routes — Plan 2026-05-19-001 Unit 4 + Plan 003 Unit 4.

POST /settings/channels/<channel>/bind                       — start a bind job
GET  /settings/channels/<channel>/bind/<job_id>              — poll status + events
POST /settings/channels/<channel>/identity-mismatch/keep     — keep old account (Plan 003)
POST /settings/channels/<channel>/identity-mismatch/replace  — replace + force re-bind (Plan 003)

All POST routes:
  - loopback-only (Blueprint-scoped ``before_request``)
  - require a valid CSRF token (via app-level ``_global_csrf_guard``)
  - reject under ``BACKLINK_PUBLISHER_ALLOW_NETWORK=1`` (Plan 003 Unit 3)
  - validate channel against CHANNELS before any state change (defense
    in depth against ``channel=../traversal``)

Identity-mismatch resolution semantics:
  - **keep**: operator says "the previously-bound account is correct;
    discard the mismatch signal". Driver did NOT persist new cookies
    (IdentityMismatch raised BEFORE _persist_storage_state), so the
    old storage_state.json + last_account.txt are intact. We just flip
    status back to ``bound``.
  - **replace**: operator accepts the new account. Driver did NOT
    persist the new account's cookies, so we delete the OLD
    storage_state.json + last_account.txt and flip status to
    ``unbound`` (operator must re-bind to capture the new account
    cleanly). This is conservative — no half-bound state.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, jsonify, redirect, request

from backlink_publisher._util.errors import UsageError
from backlink_publisher.cli._bind.channels import CHANNELS
from backlink_publisher.config.loader import _config_dir

from ..helpers import (
    _check_bind_origin_or_abort,
    _LOOPBACK_HOSTS,
    _refuse_when_allow_network,
)
from ..services.bind_job import registry as _bind_registry


bp = Blueprint("bind", __name__)


@bp.before_request
def _enforce_loopback() -> None:
    if request.remote_addr not in _LOOPBACK_HOSTS:
        abort(403)


@bp.route("/settings/channels/<channel>/bind", methods=["POST"])
def start_bind(channel: str):
    _refuse_when_allow_network()
    _check_bind_origin_or_abort()
    if channel not in CHANNELS:
        abort(400)

    # PR #83 adversarial review: refuse to start a fresh bind while the
    # channel is in ``identity_mismatch``. Letting the bind run in
    # parallel with an open keep/replace decision creates a TOCTOU
    # window where the resolution route can race the new bind's
    # ``mark_bound`` and silently accept the new account under the
    # guise of "keep old".
    from webui_store.channel_status import get_status

    if get_status(channel).get("status") == "identity_mismatch":
        return jsonify({
            "status": "error",
            "error": "identity_mismatch_unresolved",
            "message": (
                "请先在设置页选择保留旧账号或替换为新账号，再发起新的绑定。"
            ),
        }), 409

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


@bp.route(
    "/settings/channels/<channel>/identity-mismatch/keep",
    methods=["POST"],
)
def identity_mismatch_keep(channel: str):
    """Plan 2026-05-19-003 Unit 4. Operator chose to keep the previously
    bound account. Flip status back to bound; storage_state.json and
    last_account.txt are unchanged (the driver never persisted the
    mismatched session, so the old state on disk is still valid)."""
    _refuse_when_allow_network()
    _check_bind_origin_or_abort()
    if channel not in CHANNELS:
        abort(400)

    from webui_store import channel_status_store

    # PR #83 adversarial review: the precondition check + the resolution
    # write must be one atomic step. The previous shape (get_status() →
    # if-check → channel_status_store.update()) had a TOCTOU window where
    # a concurrent bind subprocess could complete between the check and
    # the closure, causing the closure to read a freshly-bound (different
    # account!) record and write status=bound for the WRONG account
    # under the operator's "keep old" click. By moving the check inside
    # the closure we either restore the same identity_mismatch record we
    # observed or no-op cleanly.
    def _restore(current_state: dict) -> dict:
        current_state = dict(current_state)
        existing = current_state.get(channel, {})
        if existing.get("status") != "identity_mismatch":
            # State changed under us (concurrent resolution / bind
            # completion). No-op so we don't silently accept whatever
            # the other writer landed.
            return current_state
        storage_state_path = existing.get("storage_state_path")
        if not storage_state_path or not Path(storage_state_path).exists():
            # Old credential is gone (external wipe or never persisted).
            # Demote to expired — semantically honest: "you used to be
            # bound, the credential is no longer on disk, please rebind".
            # Crucially NOT "replace" (which would also delete sibling
            # last-account files); the UI label says "keep", and silent
            # destructive escalation is the bug the reviewer flagged.
            current_state[channel] = {
                "status": "expired",
                "bound_at": existing.get("bound_at"),
                "storage_state_path": existing.get("storage_state_path"),
                "last_verified_at": None,
            }
            return current_state
        # Happy path: restore bound with the original bound_at (don't
        # pretend it just happened); clear identity_mismatch_* sentinels
        # and last_verified_at (probe must re-confirm).
        current_state[channel] = {
            "status": "bound",
            "bound_at": existing.get("bound_at"),
            "storage_state_path": existing.get("storage_state_path"),
            "last_verified_at": None,
        }
        return current_state

    channel_status_store.update(_restore)
    return redirect("/settings")


@bp.route(
    "/settings/channels/<channel>/identity-mismatch/replace",
    methods=["POST"],
)
def identity_mismatch_replace(channel: str):
    """Plan 2026-05-19-003 Unit 4. Operator chose the new account.
    Wipe storage_state.json + last_account.txt and flip status to
    unbound — operator must re-bind to capture the new account
    cleanly (no half-bound state)."""
    _refuse_when_allow_network()
    _check_bind_origin_or_abort()
    if channel not in CHANNELS:
        abort(400)
    _execute_replace(channel)
    return redirect("/settings")


def _execute_replace(channel: str) -> None:
    """Delete storage_state.json + last-account artifacts; reset to
    unbound. Shared helper between identity-mismatch/replace and the
    defensive fallback in identity-mismatch/keep."""
    from webui_store import channel_status_store

    cfg = _config_dir()
    for fname in (
        f"{channel}-storage-state.json",
        f"{channel}-last-account.txt",
        f"{channel}-last-account.tentative",
    ):
        p = cfg / fname
        if p.exists():
            try:
                p.unlink()
            except OSError:
                # Best-effort delete; if a stale file remains, the next
                # bind overwrites it. Don't fail the route.
                pass

    def _wipe(current_state: dict) -> dict:
        current_state = dict(current_state)
        current_state.pop(channel, None)
        return current_state

    channel_status_store.update(_wipe)
