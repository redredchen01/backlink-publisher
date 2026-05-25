"""Task Dashboard Route — Plan 2026-05-18-001.

Plan 012 Unit 2 made `/ce:dashboard` a 302 redirect (the retry-task POST
endpoint moved to the `history` blueprint; `/ce:retry-task` is unchanged).
Plan 2026-05-25-006 U3 repurposes "dashboard" to mean the publishing **health**
dashboard, so the redirect now targets `/ce:health`. The in-progress task list
is still reachable directly at `/ce:history?section=in-progress`.
"""

from __future__ import annotations

from flask import Blueprint, redirect

bp = Blueprint("dashboard", __name__)


@bp.route('/ce:dashboard', methods=['GET'])
def ce_dashboard():
    return redirect('/ce:health', code=302)
