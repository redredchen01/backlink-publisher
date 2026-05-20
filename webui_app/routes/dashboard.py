"""Task Dashboard Route — Plan 2026-05-18-001.

Plan 012 Unit 2: `/ce:dashboard` is now a 302 redirect to
`/ce:history?section=in-progress`. The retry-task POST endpoint moved
to the `history` blueprint (URL `/ce:retry-task` is unchanged).
"""

from __future__ import annotations

from flask import Blueprint, redirect

bp = Blueprint("dashboard", __name__)


@bp.route('/ce:dashboard', methods=['GET'])
def ce_dashboard():
    return redirect('/ce:history?section=in-progress', code=302)
