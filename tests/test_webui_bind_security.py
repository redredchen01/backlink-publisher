"""Tests for the Plan 2026-05-19-003 Unit 3 webui security helpers.

Two helpers shipped in ``webui_app/helpers.py`` for Plan 001 Unit 4's
bind routes to consume:

  - ``_check_bind_origin_or_abort()`` — Origin/Referer + DNS-rebinding
    defense. ``Origin: null`` rejected (file://, sandboxed iframes);
    Origin or Referer must match host:port allowlist of loopback +
    ``_FLASK_PORT``. Both absent → 403.
  - ``_refuse_when_allow_network()`` — hard-disables bind endpoints when
    ``BACKLINK_PUBLISHER_ALLOW_NETWORK=1``. Returns JSON 403 with
    ``error="bind_disabled_under_allow_network"``.

These helpers stack with the existing ``_check_localhost`` (network
attacker) and ``_check_csrf_or_abort`` (same-origin XSS) defenses. Each
helper defends a distinct attack class — see Plan 003 Key Technical
Decisions for the threat model.
"""

from __future__ import annotations

import pytest
from flask import Flask, jsonify

from webui_app.helpers.security import (
    _check_bind_origin_or_abort,
    _refuse_when_allow_network,
    _FLASK_PORT,
)


@pytest.fixture
def app():
    """Minimal Flask app that exposes both helpers on test routes."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.route("/test/origin-check", methods=["POST"])
    def origin_check():
        _check_bind_origin_or_abort()
        return jsonify(ok=True)

    @app.route("/test/refuse-network", methods=["POST"])
    def refuse_network():
        _refuse_when_allow_network()
        return jsonify(ok=True)

    return app


@pytest.fixture
def client(app):
    return app.test_client()


# ─── _check_bind_origin_or_abort ───


class TestOriginAllowlist:
    """Loopback + _FLASK_PORT origins pass; everything else 403s."""

    def test_origin_127_0_0_1_passes(self, client):
        resp = client.post(
            "/test/origin-check",
            headers={"Origin": f"http://127.0.0.1:{_FLASK_PORT}"},
        )
        assert resp.status_code == 200

    def test_origin_localhost_passes(self, client):
        resp = client.post(
            "/test/origin-check",
            headers={"Origin": f"http://localhost:{_FLASK_PORT}"},
        )
        assert resp.status_code == 200

    def test_origin_ipv6_loopback_passes(self, client):
        resp = client.post(
            "/test/origin-check",
            headers={"Origin": f"http://[::1]:{_FLASK_PORT}"},
        )
        assert resp.status_code == 200


class TestOriginRejection:
    """Non-allowlisted Origins are 403'd."""

    def test_origin_null_is_403(self, client):
        # file://, sandboxed iframes, data: documents all yield Origin: null
        resp = client.post(
            "/test/origin-check",
            headers={"Origin": "null"},
        )
        assert resp.status_code == 403

    def test_origin_external_host_is_403(self, client):
        resp = client.post(
            "/test/origin-check",
            headers={"Origin": f"http://evil.example.com:{_FLASK_PORT}"},
        )
        assert resp.status_code == 403

    def test_origin_wrong_port_is_403(self, client):
        resp = client.post(
            "/test/origin-check",
            headers={"Origin": f"http://127.0.0.1:{_FLASK_PORT + 1}"},
        )
        assert resp.status_code == 403

    def test_https_origin_rejected(self, client):
        # Our webui is HTTP-only on loopback; an HTTPS origin claiming the
        # same host is suspicious (TLS-stripping attack vector). Reject.
        resp = client.post(
            "/test/origin-check",
            headers={"Origin": f"https://127.0.0.1:{_FLASK_PORT}"},
        )
        assert resp.status_code == 403


class TestOriginReferer:
    """Referer-fallback: when Origin is absent (some browsers strip it),
    a matching Referer carries the equivalent same-origin signal."""

    def test_origin_absent_referer_loopback_passes(self, client):
        resp = client.post(
            "/test/origin-check",
            headers={"Referer": f"http://127.0.0.1:{_FLASK_PORT}/settings"},
        )
        assert resp.status_code == 200

    def test_origin_absent_referer_external_is_403(self, client):
        resp = client.post(
            "/test/origin-check",
            headers={"Referer": "http://evil.example.com/some-page"},
        )
        assert resp.status_code == 403

    def test_origin_present_referer_mismatch_is_403(self, client):
        # When BOTH headers are sent and they disagree (Origin is allowlist
        # but Referer is external), reject — the Referer mismatch hints at
        # something off-origin in the request chain.
        resp = client.post(
            "/test/origin-check",
            headers={
                "Origin": f"http://127.0.0.1:{_FLASK_PORT}",
                "Referer": "http://evil.example.com/redirected",
            },
        )
        assert resp.status_code == 403

    def test_origin_absent_referer_absent_is_403(self, client):
        # State-changing route with neither signal: reject.
        resp = client.post("/test/origin-check")
        assert resp.status_code == 403


# ─── _refuse_when_allow_network ───


class TestRefuseWhenAllowNetwork:
    """When BACKLINK_PUBLISHER_ALLOW_NETWORK=1, bind endpoints hard-403."""

    def test_env_set_to_1_returns_403(self, client, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_ALLOW_NETWORK", "1")
        resp = client.post("/test/refuse-network")
        assert resp.status_code == 403

    def test_env_set_to_0_passes(self, client, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_ALLOW_NETWORK", "0")
        resp = client.post("/test/refuse-network")
        assert resp.status_code == 200

    def test_env_unset_passes(self, client, monkeypatch):
        monkeypatch.delenv("BACKLINK_PUBLISHER_ALLOW_NETWORK", raising=False)
        resp = client.post("/test/refuse-network")
        assert resp.status_code == 200

    def test_env_truthy_but_not_literal_1_passes(self, client, monkeypatch):
        # Mirrors _resolve_bind_host's strict literal "1" check — only the
        # exact value triggers refuse.
        monkeypatch.setenv("BACKLINK_PUBLISHER_ALLOW_NETWORK", "true")
        resp = client.post("/test/refuse-network")
        assert resp.status_code == 200

    def test_403_body_carries_error_code(self, client, monkeypatch):
        """The error code in the body lets the operator/UI distinguish
        this hard-disable from generic 403s."""
        monkeypatch.setenv("BACKLINK_PUBLISHER_ALLOW_NETWORK", "1")
        resp = client.post("/test/refuse-network")
        assert resp.status_code == 403
        # Body may be JSON or HTML depending on Flask version; check
        # for the discriminator string somewhere in the payload.
        body = resp.get_data(as_text=True).lower()
        assert "bind_disabled_under_allow_network" in body
