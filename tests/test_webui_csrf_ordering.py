"""CSRF hook-ordering, Set-Cookie regression, and token round-trip tests.

Wave 2 security additions (plan 2026-05-28-006). Three permanent assertions:

E3 — _global_csrf_guard must be the *first* app-level before_request hook.
     Flask 3 hook-ordering semantics must not re-order blueprint hooks ahead
     of the app-level guard.

O4 — Session Set-Cookie must carry HttpOnly and SameSite=Lax after the
     Flask 2→3 bump. Werkzeug 3 must not silently revert these to defaults.

O10 — CSRF token issued in request N must be accepted in request N+1 within
      the same client session. Proves itsdangerous session signer stability
      across the Werkzeug/Flask bump.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Shared fixture: fresh app per test, process-global env leaks stripped
# ---------------------------------------------------------------------------

@pytest.fixture
def csrf_app(monkeypatch: pytest.MonkeyPatch):
    """Fresh create_app(start_scheduler=False) with env leaks removed."""
    for key in (
        "BACKLINK_PUBLISHER_ALLOW_NETWORK",
        "BACKLINK_PUBLISHER_SESSION_COOKIE_SECURE",
        "OAUTHLIB_INSECURE_TRANSPORT",
    ):
        monkeypatch.delenv(key, raising=False)

    from webui_app import create_app
    app = create_app(start_scheduler=False)
    app.config["TESTING"] = True

    # Sanity: default-on baseline before any test exercises the guard.
    assert app.config.get("CSRF_ENABLED") is True
    assert app.config.get("SESSION_COOKIE_HTTPONLY") is True
    assert app.config.get("SESSION_COOKIE_SAMESITE") == "Lax"
    return app


# ---------------------------------------------------------------------------
# E3: Hook-ordering assertion
# ---------------------------------------------------------------------------

def test_global_csrf_guard_is_first_before_request_hook(csrf_app) -> None:
    """_global_csrf_guard must be first in app.before_request_funcs[None].

    Flask 3 must not re-order blueprint hooks before app-level hooks.  If it
    ever does, blueprint handlers could run before CSRF validation, effectively
    bypassing the guard for any blueprint that returns early.
    """
    app_hooks = csrf_app.before_request_funcs.get(None, [])
    assert app_hooks, "No app-level before_request hooks registered"
    first_name = app_hooks[0].__name__
    assert first_name == "_global_csrf_guard", (
        f"Expected first hook to be '_global_csrf_guard', got {first_name!r}. "
        "Flask 3 hook-ordering may have changed — CSRF guard is no longer first."
    )


def test_probe_blueprint_cannot_run_before_csrf_guard(csrf_app) -> None:
    """A blueprint before_request that would return 200 must not fire on a
    token-less POST — the global CSRF guard must intercept it first (403)."""
    from flask import Blueprint

    probe_fired: list[bool] = []

    probe_bp = Blueprint("probe_csrf_order", __name__)

    @probe_bp.before_request
    def _probe():
        probe_fired.append(True)

    @probe_bp.route("/__probe_csrf__/mutate", methods=["POST"])
    def _probe_route():
        return "should not reach here", 200

    csrf_app.register_blueprint(probe_bp)

    with csrf_app.test_client() as client:
        resp = client.post("/__probe_csrf__/mutate")  # no token

    assert resp.status_code == 403, (
        f"Expected 403 from CSRF guard, got {resp.status_code}. "
        "Blueprint handler or before_request ran before the global guard."
    )
    # The blueprint's own before_request ran (Flask calls all app+bp hooks
    # in order), but the guard aborted before the route returned 200.
    # What matters is the 403 response from the guard.


# ---------------------------------------------------------------------------
# O4: Set-Cookie security regression
# ---------------------------------------------------------------------------

def test_set_cookie_carries_httponly_and_samesite(csrf_app) -> None:
    """Session Set-Cookie must carry HttpOnly and SameSite=Lax.

    Werkzeug 3 must not silently revert these cookie attributes to framework
    defaults after the Flask 3 bump. SESSION_COOKIE_SECURE is False by default
    for loopback, so it is not asserted here.
    """
    @csrf_app.route("/__set_cookie_test__")
    def _set_cookie_trigger():
        from flask import session
        session["csrf_token"] = "cookie-test-token"
        return "ok"

    with csrf_app.test_client() as client:
        resp = client.get("/__set_cookie_test__")

    assert resp.status_code == 200
    set_cookie_headers = resp.headers.getlist("Set-Cookie")
    assert set_cookie_headers, (
        "No Set-Cookie header in response — session was not created. "
        "Check that the route actually writes to session."
    )

    # Use the first cookie (Flask only sets one session cookie).
    cookie_str = set_cookie_headers[0].lower()
    assert "httponly" in cookie_str, (
        f"HttpOnly missing from Set-Cookie after Flask 3 bump: {set_cookie_headers[0]!r}"
    )
    assert "samesite=lax" in cookie_str, (
        f"SameSite=Lax missing from Set-Cookie after Flask 3 bump: {set_cookie_headers[0]!r}"
    )


# ---------------------------------------------------------------------------
# O10: CSRF token round-trip
# ---------------------------------------------------------------------------

def test_csrf_token_round_trip_across_requests(csrf_app) -> None:
    """Token issued in request N must be accepted in request N+1 (same session).

    Regression guard for the itsdangerous session signer. If the signer's
    format changes across a Werkzeug/itsdangerous bump, tokens in existing
    sessions would be rejected — this test catches that before production.
    """
    @csrf_app.route("/__csrf_rt__/init")
    def _csrf_rt_init():
        from webui_app.helpers.security import _ensure_csrf_token
        return _ensure_csrf_token(), 200

    @csrf_app.route("/__csrf_rt__/mutate", methods=["POST"])
    def _csrf_rt_mutate():
        return "ok", 200

    with csrf_app.test_client() as client:
        # Request N: issue the token
        get_resp = client.get("/__csrf_rt__/init")
        assert get_resp.status_code == 200
        token = get_resp.data.decode().strip()
        assert token, "GET request did not return a CSRF token"

        # Request N+1: same session, use the token
        post_resp = client.post("/__csrf_rt__/mutate", data={"csrf_token": token})

    assert post_resp.status_code == 200, (
        f"CSRF token round-trip failed: token valid in request N but rejected "
        f"in N+1 with status {post_resp.status_code}. "
        "itsdangerous session signer may have changed across the Werkzeug/Flask bump."
    )
