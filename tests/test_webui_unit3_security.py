"""Unit 3 security hardening tests — Plan 2026-05-21-006.

Covers:
  3.1 — SSRF gate + LLM host allowlist + response size/content-type caps
  3.2 — OAUTHLIB_INSECURE_TRANSPORT context manager + loopback assertion
  3.3 — Same-origin referrer redirect
  3.4 — _safe_flash_redirect: CR/LF stripping + length cap + URL-quote
  3.5 — SESSION_COOKIE_SECURE env-driven + ALLOW_NETWORK warning
"""

from __future__ import annotations

import os
import sys
import warnings
from unittest.mock import patch

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("BACKLINK_PUBLISHER_ALLOW_NETWORK", raising=False)
    monkeypatch.delenv("BACKLINK_PUBLISHER_SESSION_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST", raising=False)
    monkeypatch.delenv("BACKLINK_PUBLISHER_LLM_ALLOW_LOOPBACK", raising=False)
    from webui_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    # CSRF off for these tests — they exercise business-logic guards in
    # llm.py / oauth.py / profiles.py, not the CSRF layer itself.
    app.config["CSRF_ENABLED"] = False
    app.config["WTF_CSRF_ENABLED"] = False
    return app


@pytest.fixture
def client(app):
    return app.test_client()


# ── 3.4 _safe_flash_redirect ────────────────────────────────────────────────

class TestSafeFlashRedirect:
    def test_strips_crlf(self, app):
        from webui_app.helpers.security import _safe_flash_redirect
        with app.test_request_context():
            resp = _safe_flash_redirect(
                "/x", flash_type="danger",
                msg="bad\r\nSet-Cookie: evil=1\rmore")
        loc = resp.headers["Location"]
        assert "\r" not in loc
        assert "\n" not in loc
        assert "Set-Cookie" in loc  # text preserved, but no raw CRLF

    def test_caps_length(self, app):
        from webui_app.helpers.security import _safe_flash_redirect, _FLASH_MSG_MAX_LEN
        with app.test_request_context():
            resp = _safe_flash_redirect(
                "/x", flash_type="warning", msg="A" * 500)
        loc = resp.headers["Location"]
        # quote()-encoded 'A' is just 'A', so msg portion ≤ MAX_LEN.
        # Locate the flash_msg= portion and count.
        msg_part = loc.split("flash_msg=", 1)[1]
        msg_decoded = msg_part.split("&")[0].split("#")[0]
        assert len(msg_decoded) <= _FLASH_MSG_MAX_LEN

    def test_quotes_special_chars(self, app):
        from webui_app.helpers.security import _safe_flash_redirect
        with app.test_request_context():
            resp = _safe_flash_redirect(
                "/x", flash_type="info",
                msg="a & b=c?#frag")
        loc = resp.headers["Location"]
        assert "%26" in loc  # &
        assert "%3D" in loc  # =
        # The hash separator # in the msg must be quoted, not used as fragment.
        assert "%23" in loc

    def test_fragment_appended(self, app):
        from webui_app.helpers.security import _safe_flash_redirect
        with app.test_request_context():
            resp = _safe_flash_redirect(
                "/x", flash_type="success", msg="ok",
                fragment="sect-ai")
        assert resp.headers["Location"].endswith("#sect-ai")

    def test_empty_msg_omits_param(self, app):
        from webui_app.helpers.security import _safe_flash_redirect
        with app.test_request_context():
            resp = _safe_flash_redirect("/x", flash_type="info", msg="")
        assert "flash_msg=" not in resp.headers["Location"]


# ── 3.3 _safe_referrer_redirect ─────────────────────────────────────────────

class TestSafeReferrerRedirect:
    def test_same_origin_referrer_followed(self, app):
        from webui_app.helpers.security import _safe_referrer_redirect
        with app.test_request_context(
                "/profiles/delete", method="POST",
                headers={"Referer": "http://localhost/some-page"}):
            resp = _safe_referrer_redirect(default="/")
        assert "/some-page" in resp.headers["Location"]

    def test_cross_origin_referrer_blocked(self, app):
        from webui_app.helpers.security import _safe_referrer_redirect
        with app.test_request_context(
                "/profiles/delete", method="POST",
                headers={"Referer": "https://evil.com/phish"}):
            resp = _safe_referrer_redirect(default="/")
        assert resp.headers["Location"] == "/"

    def test_no_referrer_uses_default(self, app):
        from webui_app.helpers.security import _safe_referrer_redirect
        with app.test_request_context("/profiles/delete", method="POST"):
            resp = _safe_referrer_redirect(default="/")
        assert resp.headers["Location"] == "/"


# ── 3.2 OAUTHLIB context manager ────────────────────────────────────────────

class TestOauthlibInsecureTransportContext:
    def test_sets_then_restores_unset_env(self, monkeypatch):
        monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)
        from webui_app.routes.oauth import _oauthlib_insecure_transport
        with _oauthlib_insecure_transport("http://localhost:8888/cb"):
            assert os.environ["OAUTHLIB_INSECURE_TRANSPORT"] == "1"
        assert "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ

    def test_sets_then_restores_prior_value(self, monkeypatch):
        monkeypatch.setenv("OAUTHLIB_INSECURE_TRANSPORT", "0")
        from webui_app.routes.oauth import _oauthlib_insecure_transport
        with _oauthlib_insecure_transport("http://localhost:8888/cb"):
            assert os.environ["OAUTHLIB_INSECURE_TRANSPORT"] == "1"
        assert os.environ["OAUTHLIB_INSECURE_TRANSPORT"] == "0"

    def test_restores_env_on_exception(self, monkeypatch):
        monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)
        from webui_app.routes.oauth import _oauthlib_insecure_transport
        try:
            with _oauthlib_insecure_transport("http://localhost:8888/cb"):
                raise ValueError("boom")
        except ValueError:
            pass
        assert "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ

    def test_refuses_non_loopback_uri(self):
        from webui_app.routes.oauth import _oauthlib_insecure_transport
        with pytest.raises(RuntimeError, match="not loopback"):
            with _oauthlib_insecure_transport("https://prod.example.com/cb"):
                pass

    def test_accepts_loopback_variants(self, monkeypatch):
        monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)
        from webui_app.routes.oauth import _oauthlib_insecure_transport
        # IPv6 literal in URL needs brackets per RFC 3986; otherwise
        # urlparse can't extract the hostname.
        for uri in (
                "http://localhost:8888/cb",
                "http://127.0.0.1:8888/cb",
                "http://[::1]:8888/cb",
        ):
            with _oauthlib_insecure_transport(uri):
                pass


# ── 3.1 LLM allowlist + SSRF guard ──────────────────────────────────────────

class TestLlmAllowlist:
    def test_canonical_host_is_allowlisted(self):
        from backlink_publisher._util.llm_allowlist import is_allowlisted
        assert is_allowlisted("https://api.openai.com/v1")
        assert is_allowlisted("https://api.anthropic.com/v1/messages")
        assert is_allowlisted("https://api.deepseek.com/v1")

    def test_unknown_host_rejected(self, monkeypatch):
        monkeypatch.delenv("BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST", raising=False)
        from backlink_publisher._util.llm_allowlist import is_allowlisted
        assert not is_allowlisted("https://evil.example.com/v1")

    def test_opt_out_allows_any_host(self, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST", "1")
        from backlink_publisher._util.llm_allowlist import is_allowlisted
        assert is_allowlisted("https://evil.example.com/v1")

    def test_subdomain_not_matched(self):
        from backlink_publisher._util.llm_allowlist import is_allowlisted
        assert not is_allowlisted("https://evil.api.openai.com/v1")


class TestLlmEndpointGuard:
    def test_rejects_scheme(self, client, monkeypatch):
        # ALLOW_ANY_HOST=1 lets us bypass the allowlist so the request
        # reaches the scheme check (ce:review maint-006 tightening).
        monkeypatch.setenv("BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST", "1")
        resp = client.post(
            "/settings/test-llm-connection",
            data={"endpoint": "file:///etc/passwd", "api_key": "sk-x"},
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["reason"] == "scheme_rejected"

    def test_rejects_unallowlisted_host(self, client, monkeypatch):
        # Need both api_key and endpoint to bypass the "missing fields" 200 gate.
        monkeypatch.delenv("BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST", raising=False)
        resp = client.post(
            "/settings/test-llm-connection",
            data={"endpoint": "https://evil.example.com",
                  "api_key": "sk-attacker"},
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["reason"] == "host_not_allowlisted"

    def test_rejects_metadata_ip(self, client, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST", "1")
        resp = client.post(
            "/settings/test-llm-connection",
            data={"endpoint": "http://169.254.169.254",
                  "api_key": "sk-attacker"},
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["reason"] == "url_rejected"
        assert "169.254" in body["message"] or "blocked_ip" in body["message"]

    def test_rejects_rfc1918(self, client, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST", "1")
        resp = client.post(
            "/settings/test-llm-connection",
            data={"endpoint": "http://10.0.0.1",
                  "api_key": "sk-attacker"},
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["reason"] == "url_rejected"

    def test_rejects_loopback_without_opt_in(self, client, monkeypatch):
        # Env is read inline in _guard_llm_endpoint (post ce:review maint-001),
        # so monkeypatch is sufficient — no importlib.reload needed.
        monkeypatch.setenv("BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST", "1")
        monkeypatch.delenv("BACKLINK_PUBLISHER_LLM_ALLOW_LOOPBACK", raising=False)
        resp = client.post(
            "/settings/test-llm-connection",
            data={"endpoint": "http://127.0.0.1:11434",
                  "api_key": "sk-attacker"},
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["reason"] == "url_rejected"

    def test_accepts_loopback_with_opt_in(self, client, monkeypatch):
        # Operator with Ollama on localhost sets both env vars; SSRF gate
        # gets out of the way. The endpoint won't actually respond (no
        # Ollama in test env), but the gate must NOT 400.
        monkeypatch.setenv("BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST", "1")
        monkeypatch.setenv("BACKLINK_PUBLISHER_LLM_ALLOW_LOOPBACK", "1")
        resp = client.post(
            "/settings/test-llm-connection",
            data={"endpoint": "http://127.0.0.1:11434",
                  "api_key": "sk-x"},
        )
        # Gate passed; downstream connection error gets caught and returned
        # as 200 with status=error/exception. The key invariant: NOT 400.
        if resp.status_code == 400:
            body = resp.get_json()
            assert body.get("reason") != "url_rejected", (
                f"loopback opt-in didn't take effect: {body}"
            )


class TestLlmSsrfRedirectGuard:
    """Critical: requests.get(..., allow_redirects=False) prevents an
    allowlisted upstream from redirecting the request to an internal IP
    with the operator's Bearer token in tow (ce:review C1 / sec-001).
    """

    def test_safe_get_json_rejects_redirect(self, monkeypatch):
        from webui_app.routes.llm import _safe_get_json

        class _FakeResp:
            def __init__(self):
                self.status_code = 302
                self.headers = {"Location": "http://169.254.169.254/iam/",
                                "Content-Type": "text/html"}

            def iter_content(self, chunk_size=8192):
                yield b""

        calls = []

        def fake_get(url, headers=None, timeout=None, stream=None,
                     allow_redirects=None):
            calls.append({"url": url, "allow_redirects": allow_redirects})
            return _FakeResp()

        monkeypatch.setattr("webui_app.routes.llm.requests.get", fake_get)
        with pytest.raises(ValueError, match="redirect_not_allowed"):
            _safe_get_json("https://api.openai.com/models",
                           {"Authorization": "Bearer sk-x"})
        # The fix: requests.get MUST be called with allow_redirects=False.
        assert calls[0]["allow_redirects"] is False

    def test_safe_post_json_rejects_redirect(self, monkeypatch):
        from webui_app.routes.llm import _safe_post_json

        class _FakeResp:
            def __init__(self):
                self.status_code = 307
                self.headers = {"Location": "http://10.0.0.1/admin",
                                "Content-Type": "text/html"}

            def iter_content(self, chunk_size=8192):
                yield b""

        calls = []

        def fake_post(url, headers=None, json=None, timeout=None, stream=None,
                      allow_redirects=None):
            calls.append({"url": url, "allow_redirects": allow_redirects})
            return _FakeResp()

        monkeypatch.setattr("webui_app.routes.llm.requests.post", fake_post)
        with pytest.raises(ValueError, match="redirect_not_allowed"):
            _safe_post_json("https://api.openai.com/chat/completions",
                            {"Authorization": "Bearer sk-x"}, {})
        assert calls[0]["allow_redirects"] is False


# ── 3.5 SESSION_COOKIE_SECURE env-driven + ALLOW_NETWORK warning ────────────

class TestSessionCookieSecure:
    def test_defaults_to_false(self, monkeypatch):
        monkeypatch.delenv("BACKLINK_PUBLISHER_SESSION_COOKIE_SECURE", raising=False)
        from webui_app import create_app
        app = create_app()
        assert app.config["SESSION_COOKIE_SECURE"] is False

    def test_env_true_sets_true(self, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_SESSION_COOKIE_SECURE", "1")
        from webui_app import create_app
        app = create_app()
        assert app.config["SESSION_COOKIE_SECURE"] is True


class TestAllowNetworkWarning:
    def test_warns_when_allow_network_set(self, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_ALLOW_NETWORK", "1")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            from webui_app import create_app
            create_app()
        msgs = [str(w.message) for w in caught
                if issubclass(w.category, RuntimeWarning)]
        assert any("ALLOW_NETWORK=1" in m for m in msgs), \
            f"Expected ALLOW_NETWORK warning; got: {msgs}"

    def test_silent_when_loopback(self, monkeypatch):
        monkeypatch.delenv("BACKLINK_PUBLISHER_ALLOW_NETWORK", raising=False)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            from webui_app import create_app
            create_app()
        # No ALLOW_NETWORK warning when env is unset.
        msgs = [str(w.message) for w in caught
                if issubclass(w.category, RuntimeWarning)
                and "ALLOW_NETWORK" in str(w.message)]
        assert not msgs
