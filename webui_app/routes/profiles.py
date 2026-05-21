"""/profiles/* — Plan Unit 3."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from webui_store import profiles_store as _profiles_store

from ..helpers import _safe_referrer_redirect

bp = Blueprint("profiles", __name__)


@bp.route('/profiles/save', methods=['POST'])
def profiles_save():
    """Save a campaign profile (AJAX JSON)."""
    name = request.form.get('profile_name', '').strip()
    if not name:
        return jsonify({'ok': False, 'error': '名称不能为空'})

    profile_data = {
        'platform': request.form.get('platform', 'blogger'),
        'language': request.form.get('language', 'zh-CN'),
        'url_mode': request.form.get('url_mode', 'C'),
        'publish_mode': request.form.get('publish_mode', 'publish'),
    }

    def _upsert(profiles):
        for p in profiles:
            if p.get('name') == name:
                p.update(profile_data)
                return profiles
        profiles.append({'name': name, **profile_data})
        return profiles

    _profiles_store.update(_upsert)
    return jsonify({'ok': True})


@bp.route('/profiles/delete', methods=['POST'])
def profiles_delete():
    """Delete a campaign profile by name."""
    name = request.form.get('profile_name', '').strip()
    _profiles_store.update(
        lambda profiles: [p for p in profiles if p.get('name') != name]
    )
    # Plan 2026-05-21-006 Unit 3.3 — same-origin referrer guard. Naive
    # `redirect(request.referrer or '/')` was an open-redirect vector.
    return _safe_referrer_redirect(default='/')
