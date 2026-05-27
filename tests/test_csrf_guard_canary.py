"""Positive CSRF-guard canary (Plan 2026-05-27-003 Unit 4).

Isolation-independent proof that the global CSRF guard is *live under default
config* — a failure mode that the containment net and the AST gate (both
leak-detectors) cannot catch. If a refactor ever turned ``_global_csrf_guard``
or ``_check_csrf_or_abort`` into a no-op, every other test could stay green
while this one goes red.

Independence: builds a fresh ``create_app(start_scheduler=False)``, ``delenv``s
the process-global toggles so a sibling test's env leak cannot make the result
hollow, asserts the fresh app is genuinely CSRF-enabled before exercising it,
and registers its own minimal routes so the 403 proves the *CSRF* check fired —
not a route-specific origin guard. It does not depend on the net and is not on
the gate's grandfather allowlist (it performs no raw security-config subscript
mutation; ``TESTING`` is ungated).
"""
from __future__ import annotations

import pytest

_TOKEN = "canary-csrf-token"
_MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")


@pytest.fixture
def canary_client(monkeypatch):
    # Strip process-global toggles a sibling test may have leaked, so the fresh
    # app's cookie/network posture is genuinely default.
    for key in (
        "BACKLINK_PUBLISHER_ALLOW_NETWORK",
        "OAUTHLIB_INSECURE_TRANSPORT",
        "BACKLINK_PUBLISHER_SESSION_COOKIE_SECURE",
    ):
        monkeypatch.delenv(key, raising=False)

    from webui_app import create_app

    app = create_app(start_scheduler=False)
    app.config["TESTING"] = True  # ungated standard setup

    # Prove the baseline is genuinely default-on before exercising the guard.
    assert app.config.get("CSRF_ENABLED") is True
    assert app.config.get("WTF_CSRF_ENABLED", True) is not False
    # env unset -> False, so the HTTP test-client cookie round-trips.
    assert app.config.get("SESSION_COOKIE_SECURE") is False

    @app.route("/__canary__/mutate", methods=["GET", *_MUTATING_METHODS])
    def _canary_mutate():  # CSRF-guarded, no origin guard
        return "ok"

    @app.route("/__canary__/blogger/oauth-callback", methods=["POST"])
    def _canary_blogger_oauth_callback():  # endpoint ends 'oauth_callback' -> exempt
        return "ok"

    @app.route("/__canary__/not-a-callback", methods=["POST"])
    def _canary_oauth_callback_admin():  # near-miss: does NOT end 'oauth_callback'
        return "ok"

    return app.test_client()


def _seed_token(client) -> None:
    with client.session_transaction() as sess:
        sess["csrf_token"] = _TOKEN


@pytest.mark.parametrize("method", _MUTATING_METHODS)
def test_token_less_mutating_request_is_rejected(canary_client, method) -> None:
    _seed_token(canary_client)
    resp = canary_client.open("/__canary__/mutate", method=method)
    assert resp.status_code == 403


def test_valid_token_is_accepted(canary_client) -> None:
    _seed_token(canary_client)
    resp = canary_client.post("/__canary__/mutate", data={"csrf_token": _TOKEN})
    assert resp.status_code != 403
    assert resp.status_code == 200


def test_mismatched_token_is_rejected(canary_client) -> None:
    _seed_token(canary_client)
    resp = canary_client.post("/__canary__/mutate", data={"csrf_token": "wrong"})
    assert resp.status_code == 403


def test_empty_token_is_rejected(canary_client) -> None:
    _seed_token(canary_client)
    resp = canary_client.post("/__canary__/mutate", data={"csrf_token": ""})
    assert resp.status_code == 403


def test_header_token_is_accepted(canary_client) -> None:
    # JS fetch path: token arrives via X-CSRFToken header.
    _seed_token(canary_client)
    resp = canary_client.post("/__canary__/mutate", headers={"X-CSRFToken": _TOKEN})
    assert resp.status_code != 403


def test_get_is_not_guarded(canary_client) -> None:
    _seed_token(canary_client)
    resp = canary_client.get("/__canary__/mutate")
    assert resp.status_code == 200


def test_oauth_callback_endpoint_is_exempt(canary_client) -> None:
    _seed_token(canary_client)
    resp = canary_client.post("/__canary__/blogger/oauth-callback")  # no token
    assert resp.status_code != 403


def test_oauth_callback_exemption_is_narrow(canary_client) -> None:
    # A near-miss endpoint name must NOT inherit the exemption (endswith, not
    # substring) — token-less POST is still rejected.
    _seed_token(canary_client)
    resp = canary_client.post("/__canary__/not-a-callback")  # no token
    assert resp.status_code == 403
