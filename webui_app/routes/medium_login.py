"""Medium browser-login POST routes: launch / probe / clear.

CSRF is enforced globally by ``_global_csrf_guard`` in ``create_app()`` (it
aborts 403 on any POST without a valid canonical ``csrf_token`` before this
blueprint runs), so these routes carry no bespoke CSRF layer. Because they
spawn OS-level browser processes and delete the persistent login profile, each
POST additionally guards origin like the ``bind`` routes do:
``_refuse_when_allow_network()`` + ``_check_bind_origin_or_abort()``.
"""

from __future__ import annotations

from flask import Blueprint, redirect, session

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.config import load_config

from ..helpers.security import (
    _check_bind_origin_or_abort,
    _refuse_when_allow_network,
)
from ..medium_login import (
    clear_browser_profile,
    launch_login_window,
    probe_login_status,
)

bp = Blueprint("medium_login", __name__)


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/settings/medium/launch-browser-login", methods=["POST"])
def medium_launch_browser_login():
    """Open a headed Chromium for the user to log in to Medium."""
    _refuse_when_allow_network()
    _check_bind_origin_or_abort()
    cfg = load_config()
    try:
        result = launch_login_window(cfg)
        session["medium_probe_logged_in"] = result.get("logged_in", False)
        return redirect(
            "/settings?flash_type=success"
            "&flash_msg=Medium 浏览器登录完成！#channel-medium"
        )
    except DependencyError as e:
        return redirect(
            f"/settings?flash_type=warning&flash_msg={e}#channel-medium"
        )
    except ExternalServiceError as e:
        return redirect(
            f"/settings?flash_type=danger&flash_msg={e}#channel-medium"
        )


@bp.route("/settings/medium/probe-browser-login", methods=["POST"])
def medium_probe_browser_login():
    """Probe Medium login state via a short Playwright navigation."""
    _refuse_when_allow_network()
    _check_bind_origin_or_abort()
    cfg = load_config()
    try:
        result = probe_login_status(cfg)
        if result["logged_in"]:
            session["medium_probe_logged_in"] = True
            name = f" (@{result['username']})" if result.get("username") else ""
            msg = f"Medium 登录有效{name}，发布通道就绪"
        else:
            session.pop("medium_probe_logged_in", None)
            msg = "Medium 未登录，请点击「打开浏览器登录」完成登录"
        return redirect(
            f"/settings?flash_type=info&flash_msg={msg}#channel-medium"
        )
    except DependencyError as e:
        return redirect(
            f"/settings?flash_type=warning&flash_msg={e}#channel-medium"
        )
    except ExternalServiceError as e:
        return redirect(
            f"/settings?flash_type=warning&flash_msg={e}#channel-medium"
        )


@bp.route("/settings/medium/clear-browser-login", methods=["POST"])
def medium_clear_browser_login():
    """Delete the persistent Chromium profile (clears stored login cookies)."""
    _refuse_when_allow_network()
    _check_bind_origin_or_abort()
    cfg = load_config()
    try:
        clear_browser_profile(cfg)
        session.pop("medium_probe_logged_in", None)
        return redirect(
            "/settings?flash_type=success"
            "&flash_msg=浏览器登录已清除；下次发布前请重新登录#channel-medium"
        )
    except Exception as e:
        return redirect(
            f"/settings?flash_type=danger&flash_msg=清除失败: {e}#channel-medium"
        )
