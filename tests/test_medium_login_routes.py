"""Tests for Plan 013 Phase B — Medium browser-login routes and functions.

Patch target for Playwright: backlink_publisher.publishing.adapters.medium_auth.sync_playwright
(Wave 1 thin-WebUI refactor: logic moved from webui_app/medium_login to adapter).

Config isolation provided by the session-scoped autouse _isolate_user_dirs
fixture in conftest.py.  Per-test isolation done via monkeypatch.setenv.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError


# ── Fixtures shared with test_webui_route_contract.py ────────────────────────
# (inlined; no cross-test import precedent in this codebase)

@pytest.fixture(autouse=True)
def _webui_state_isolated(tmp_path, monkeypatch):
    """Redirect webui_store paths so tests don't touch real files."""
    import webui_store as ws
    state_dir = tmp_path / "webui_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ws.history_store, "path", state_dir / "publish-history.json")
    monkeypatch.setattr(ws.profiles_store, "path", state_dir / "campaign-profiles.json")
    monkeypatch.setattr(ws.drafts_store, "path", state_dir / "draft-queue.json")
    monkeypatch.setattr(ws.schedule_store, "path", state_dir / "schedule-settings.json")


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """Flask test client with per-test config/cache isolation.

    Sets BACKLINK_PUBLISHER_CONFIG_DIR and _CACHE_DIR to per-test tmp paths
    so cooldown files and Chromium profiles don't bleed between tests.
    """
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cache").mkdir()
    import webui
    webui.app.config["TESTING"] = True
    webui.app.config["SESSION_COOKIE_SECURE"] = False
    # Force CSRF enforcement ON regardless of sibling tests that disable it on
    # the shared module-level ``webui.app`` (e.g. test_history_bulk_routes sets
    # WTF_CSRF_ENABLED=False and never restores). monkeypatch.setitem restores
    # the prior value on teardown, keeping the 403 negative tests deterministic
    # under pytest-randomly ordering.
    monkeypatch.setitem(webui.app.config, "CSRF_ENABLED", True)
    monkeypatch.setitem(webui.app.config, "WTF_CSRF_ENABLED", True)
    return webui.app.test_client()


# ── Mock factory (inlined; codebase has zero cross-test import precedent) ─────

def _make_mock_pw(page_url: str = "https://medium.com/@testuser"):
    """Return mocks for Playwright (launch_persistent_context path, used by launch)."""
    page = MagicMock()
    page.url = page_url
    page.goto = MagicMock()
    page.wait_for_url = MagicMock()

    ctx = MagicMock()
    ctx.new_page.return_value = page

    pw_instance = MagicMock()
    pw_instance.chromium.launch_persistent_context.return_value = ctx
    pw_instance.__enter__ = MagicMock(return_value=pw_instance)
    pw_instance.__exit__ = MagicMock(return_value=False)

    mock_spw = MagicMock(return_value=pw_instance)
    return mock_spw, page, ctx, pw_instance


def _make_mock_pw_probe(page_url: str = "https://medium.com/@testuser"):
    """Return mocks for probe (launch + new_context path, used by probe)."""
    page = MagicMock()
    page.url = page_url
    page.goto = MagicMock()

    ctx = MagicMock()
    ctx.new_page.return_value = page

    browser = MagicMock()
    browser.new_context.return_value = ctx
    browser.close = MagicMock()

    pw_instance = MagicMock()
    pw_instance.chromium.launch.return_value = browser
    pw_instance.__enter__ = MagicMock(return_value=pw_instance)
    pw_instance.__exit__ = MagicMock(return_value=False)

    mock_spw = MagicMock(return_value=pw_instance)
    return mock_spw, page, ctx, browser, pw_instance


# ── Origin headers (medium POSTs carry bind-style origin guard) ──────────────

def _origin_headers() -> dict:
    """Allowlisted loopback Origin so ``_check_bind_origin_or_abort`` passes."""
    from webui_app.helpers.security import _FLASK_PORT
    return {"Origin": f"http://127.0.0.1:{_FLASK_PORT}"}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def isolated_cfg(monkeypatch, tmp_path):
    """Per-test config_dir isolation."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    from backlink_publisher.config import Config
    return Config()


@pytest.fixture()
def csrf_client(client):
    """client with a pre-seeded canonical CSRF token in flask session.

    Seeds the canonical ``csrf_token`` key the app-level ``_global_csrf_guard``
    validates (medium's old bespoke ``medium_csrf`` layer was retired).
    """
    with client.session_transaction() as sess:
        sess["csrf_token"] = "test-csrf-abc"
    return client, "test-csrf-abc"


# ══════════════════════════════════════════════════════════════════════════════
# CSRF protection
# ══════════════════════════════════════════════════════════════════════════════

class TestCSRFProtection:
    """The app-level ``_global_csrf_guard`` rejects any POST lacking a valid
    canonical ``csrf_token`` with a 403, before the route runs. medium's old
    bespoke 302-danger CSRF layer was retired, so 403 is the rejection contract.
    (Origin header supplied so the rejection is attributable to CSRF, not the
    origin guard.)
    """

    def test_launch_without_token_forbidden(self, client):
        resp = client.post("/settings/medium/launch-browser-login",
                           data={}, headers=_origin_headers())
        assert resp.status_code == 403

    def test_probe_without_token_forbidden(self, client):
        resp = client.post("/settings/medium/probe-browser-login",
                           data={}, headers=_origin_headers())
        assert resp.status_code == 403

    def test_clear_without_token_forbidden(self, client):
        resp = client.post("/settings/medium/clear-browser-login",
                           data={}, headers=_origin_headers())
        assert resp.status_code == 403


class TestOriginGuard:
    """R5 — medium POSTs carry the same origin guard as the bind routes
    (``_check_bind_origin_or_abort``). A valid CSRF token alone is not enough;
    missing or cross-origin requests are rejected with 403.
    """

    def test_valid_csrf_but_no_origin_forbidden(self, csrf_client):
        client, token = csrf_client
        # Valid CSRF token, but no Origin/Referer → origin guard 403s.
        resp = client.post("/settings/medium/clear-browser-login",
                           data={"csrf_token": token})
        assert resp.status_code == 403

    def test_valid_csrf_cross_origin_forbidden(self, csrf_client):
        client, token = csrf_client
        resp = client.post("/settings/medium/clear-browser-login",
                           data={"csrf_token": token},
                           headers={"Origin": "http://evil.example.com"})
        assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# probe_login_status function unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestProbeLoginStatusFn:
    def test_logged_in_when_not_signin_url(self, isolated_cfg):
        from backlink_publisher.publishing.adapters.medium_auth import probe_login_status
        mock_spw, page, ctx, br, _ = _make_mock_pw_probe("https://medium.com/@alice")
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            result = probe_login_status(isolated_cfg, timeout=5)
        assert result["logged_in"] is True
        assert result["username"] == "alice"

    def test_not_logged_in_when_signin_url(self, isolated_cfg):
        from backlink_publisher.publishing.adapters.medium_auth import probe_login_status
        mock_spw, page, ctx, br, _ = _make_mock_pw_probe("https://medium.com/m/signin?redirect=x")
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            result = probe_login_status(isolated_cfg, timeout=5)
        assert result["logged_in"] is False
        assert result["username"] is None

    def test_dependency_error_when_playwright_none(self, isolated_cfg):
        from backlink_publisher.publishing.adapters.medium_auth import probe_login_status
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", None):
            with pytest.raises(DependencyError):
                probe_login_status(isolated_cfg)

    def test_probe_uses_non_persistent_launch(self, isolated_cfg):
        """Probe must use launch + new_context, NOT launch_persistent_context."""
        from backlink_publisher.publishing.adapters.medium_auth import probe_login_status
        mock_spw, page, ctx, br, pw_instance = _make_mock_pw_probe("https://medium.com/@alice")
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            probe_login_status(isolated_cfg, timeout=5)
        # launch was called (not launch_persistent_context)
        pw_instance.chromium.launch.assert_called_once()
        pw_instance.chromium.launch_persistent_context.assert_not_called()
        # new_context was called
        br.new_context.assert_called_once()

    def test_timeout_raises_external_service_error(self, isolated_cfg):
        from backlink_publisher.publishing.adapters.medium_auth import probe_login_status, _PWTimeout
        mock_spw, page, ctx, br, _ = _make_mock_pw_probe()
        page.goto.side_effect = _PWTimeout("timeout")
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            with pytest.raises(ExternalServiceError, match="超时"):
                probe_login_status(isolated_cfg, timeout=5)


# ══════════════════════════════════════════════════════════════════════════════
# launch_login_window function unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestLaunchLoginWindowFn:
    def test_happy_path_navigates_and_waits(self, isolated_cfg):
        from webui_app.medium_login import launch_login_window
        mock_spw, page, ctx, _ = _make_mock_pw()
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            result = launch_login_window(isolated_cfg)
        assert result["logged_in"] is True
        page.goto.assert_called_once()
        assert "medium.com/m/signin" in page.goto.call_args[0][0]
        page.wait_for_url.assert_called_once()
        ctx.close.assert_called_once()

    def test_dependency_error_when_playwright_none(self, isolated_cfg):
        from webui_app.medium_login import launch_login_window
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", None):
            with pytest.raises(DependencyError):
                launch_login_window(isolated_cfg)

    def test_lock_released_after_exception(self, isolated_cfg):
        from backlink_publisher.publishing.adapters.medium_auth import _lock_path, _PWTimeout
        from webui_app.medium_login import launch_login_window
        mock_spw, page, *_ = _make_mock_pw()
        page.goto.side_effect = _PWTimeout("timeout")
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            with pytest.raises(ExternalServiceError):
                launch_login_window(isolated_cfg)
        assert not _lock_path(isolated_cfg).exists()


# ══════════════════════════════════════════════════════════════════════════════
# clear_browser_profile function unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestClearBrowserProfileFn:
    def test_removes_profile_dir(self, isolated_cfg):
        from webui_app.medium_login import clear_browser_profile
        udd = isolated_cfg.config_dir / "chrome-profile-default"
        (udd / "Default").mkdir(parents=True)
        (udd / "Default" / "Cookies").write_bytes(b"fake")
        clear_browser_profile(isolated_cfg)
        assert not udd.exists()

    def test_noop_when_dir_missing(self, isolated_cfg):
        from webui_app.medium_login import clear_browser_profile
        clear_browser_profile(isolated_cfg)  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# Route integration tests (with valid CSRF token)
# ══════════════════════════════════════════════════════════════════════════════

class TestMediumLoginRoutes:
    def test_probe_logged_in_flashes_info(self, csrf_client):
        client, token = csrf_client
        mock_spw, *_ = _make_mock_pw_probe("https://medium.com/@alice")
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            resp = client.post(
                "/settings/medium/probe-browser-login",
                data={"csrf_token": token},
                headers=_origin_headers(),
            )
        assert resp.status_code == 302
        loc = resp.headers["Location"]
        assert "flash_type=info" in loc
        # Side-effect: logged-in probe persists session state for publish gating.
        with client.session_transaction() as sess:
            assert sess.get("medium_probe_logged_in") is True

    def test_probe_not_logged_in_flashes_info(self, csrf_client):
        client, token = csrf_client
        # Seed a stale logged-in flag so we can assert the route pops it.
        with client.session_transaction() as sess:
            sess["medium_probe_logged_in"] = True
        mock_spw, *_ = _make_mock_pw_probe("https://medium.com/m/signin?x=1")
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            resp = client.post(
                "/settings/medium/probe-browser-login",
                data={"csrf_token": token},
                headers=_origin_headers(),
            )
        assert resp.status_code == 302
        assert "flash_type=info" in resp.headers["Location"]
        # Side-effect: a not-logged-in probe clears any stale logged-in flag.
        with client.session_transaction() as sess:
            assert "medium_probe_logged_in" not in sess

    def test_probe_no_playwright_flashes_warning(self, csrf_client):
        client, token = csrf_client
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", None):
            resp = client.post(
                "/settings/medium/probe-browser-login",
                data={"csrf_token": token},
                headers=_origin_headers(),
            )
        assert resp.status_code == 302
        assert "flash_type=warning" in resp.headers["Location"]

    def test_launch_no_playwright_flashes_warning(self, csrf_client):
        client, token = csrf_client
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", None):
            resp = client.post(
                "/settings/medium/launch-browser-login",
                data={"csrf_token": token},
                headers=_origin_headers(),
            )
        assert resp.status_code == 302
        assert "flash_type=warning" in resp.headers["Location"]

    def test_clear_redirects_success_and_deletes_profile(self, csrf_client):
        client, token = csrf_client
        # R3 side-effect: seed a profile dir, assert clear actually removes it.
        from backlink_publisher.config import load_config
        cfg = load_config()
        profile = cfg.config_dir / "chrome-profile-default"
        (profile / "Default").mkdir(parents=True)
        (profile / "Default" / "Cookies").write_bytes(b"fake")
        resp = client.post(
            "/settings/medium/clear-browser-login",
            data={"csrf_token": token},
            headers=_origin_headers(),
        )
        assert resp.status_code == 302
        loc = resp.headers["Location"]
        assert loc.startswith("/settings?")
        assert "flash_type=success" in loc
        assert not profile.exists()

    def test_settings_render_seeds_usable_csrf_token(self, client):
        """R4 positive proof: after the bespoke medium_csrf_token() jinja global
        was retired, the app-level inject_csrf_token processor must still seed a
        non-empty session csrf_token that the medium forms submit and the global
        guard accepts (no silent empty-token → 403 in production).
        """
        assert client.get("/settings").status_code == 200
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        assert token  # non-empty token was seeded during render
        resp = client.post(
            "/settings/medium/clear-browser-login",
            data={"csrf_token": token},
            headers=_origin_headers(),
        )
        assert resp.status_code != 403  # the rendered token passes the guard


# ══════════════════════════════════════════════════════════════════════════════
# Plan 2026-05-20-010 Unit 4 — Playwright lifecycle + closed-window catch
# Regression tests for the crash fix shipped in webui_app/medium_login.py.
# Without these, removing the `except _PWError` block silently reverts the
# fix to a 500 + Flask debug page on user-closed Chromium windows.
# ══════════════════════════════════════════════════════════════════════════════

import urllib.parse


class TestPlaywrightLifecycle:
    """U1 — pw_cm.__exit__ called on ContextManager, not Playwright instance.

    The original bug was ``pw.__exit__(None, None, None)`` where ``pw`` was
    the ``Playwright`` *instance* (no ``__exit__`` attribute). The fix
    stores the ``PlaywrightContextManager`` separately as ``pw_cm`` and
    calls ``__exit__`` on it. This test asserts the fix by verifying the
    mock's ``__exit__`` is actually invoked during cleanup.
    """

    def test_launch_calls_exit_on_context_manager(self, isolated_cfg):
        from webui_app.medium_login import launch_login_window
        mock_spw, _page, _ctx, pw_instance = _make_mock_pw()
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            launch_login_window(isolated_cfg)
        pw_instance.__exit__.assert_called_once_with(None, None, None)

    def test_probe_calls_exit_on_context_manager(self, isolated_cfg):
        from webui_app.medium_login import probe_login_status
        mock_spw, _page, _ctx, _br, pw_instance = _make_mock_pw_probe()
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            probe_login_status(isolated_cfg, timeout=5)
        pw_instance.__exit__.assert_called_once_with(None, None, None)


class TestPWErrorCatchLaunch:
    """U2 — launch_login_window catches playwright.sync_api.Error.

    Regression net for the "Target page, context or browser has been closed"
    crash. Without these tests, deleting the ``except _PWError`` block
    silently reverts to a 500 error.
    """

    def test_closed_window_raises_friendly_external_error(self, isolated_cfg):
        from backlink_publisher.publishing.adapters.medium_auth import _PWError
        from webui_app.medium_login import launch_login_window
        mock_spw, page, *_ = _make_mock_pw()
        page.wait_for_url.side_effect = _PWError(
            "Target page, context or browser has been closed"
        )
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            with pytest.raises(ExternalServiceError, match="登录窗口已关闭"):
                launch_login_window(isolated_cfg)

    def test_generic_pw_error_falls_through_to_generic_message(self, isolated_cfg):
        from backlink_publisher.publishing.adapters.medium_auth import _PWError
        from webui_app.medium_login import launch_login_window
        mock_spw, page, *_ = _make_mock_pw()
        page.wait_for_url.side_effect = _PWError("Browser launch failed: xyz")
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            with pytest.raises(ExternalServiceError, match="Medium 登录失败"):
                launch_login_window(isolated_cfg)

    def test_lock_released_after_pw_error(self, isolated_cfg):
        from backlink_publisher.publishing.adapters.medium_auth import _lock_path, _PWError
        from webui_app.medium_login import launch_login_window
        mock_spw, page, *_ = _make_mock_pw()
        page.wait_for_url.side_effect = _PWError("Target ... closed")
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            with pytest.raises(ExternalServiceError):
                launch_login_window(isolated_cfg)
        # _FileLock context exit must release even when _PWError bubbles.
        assert not _lock_path(isolated_cfg).exists()


class TestPWErrorCatchProbe:
    """U2 — probe_login_status catches playwright.sync_api.Error too."""

    def test_closed_window_raises_friendly_external_error(self, isolated_cfg):
        from backlink_publisher.publishing.adapters.medium_auth import _PWError
        from webui_app.medium_login import probe_login_status
        mock_spw, page, ctx, br, _ = _make_mock_pw_probe()
        page.goto.side_effect = _PWError(
            "Target page, context or browser has been closed"
        )
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            with pytest.raises(ExternalServiceError, match="浏览器窗口被关闭"):
                probe_login_status(isolated_cfg, timeout=5)

    def test_generic_pw_error_falls_through(self, isolated_cfg):
        from backlink_publisher.publishing.adapters.medium_auth import _PWError
        from webui_app.medium_login import probe_login_status
        mock_spw, page, ctx, br, _ = _make_mock_pw_probe()
        page.goto.side_effect = _PWError("Some other Playwright issue")
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            with pytest.raises(ExternalServiceError, match="Medium probe 失败"):
                probe_login_status(isolated_cfg, timeout=5)


class TestCtxCloseTolerant:
    """U3 — finally's ctx.close() must tolerate already-closed context.

    When the user closes the Chromium window, both ``page.wait_for_url`` and
    the subsequent ``ctx.close()`` can throw the same _PWError. Without
    the try/except in the finally block, the secondary error would mask
    the original (which carried the user-friendly message).
    """

    def test_ctx_close_pw_error_does_not_mask_original(self, isolated_cfg):
        from backlink_publisher.publishing.adapters.medium_auth import _PWError
        from webui_app.medium_login import launch_login_window
        mock_spw, page, ctx, _ = _make_mock_pw()
        page.wait_for_url.side_effect = _PWError("...closed")
        ctx.close.side_effect = _PWError("ctx already closed")
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            with pytest.raises(ExternalServiceError, match="登录窗口已关闭"):
                launch_login_window(isolated_cfg)
        # ctx.close was attempted (and swallowed); __exit__ still ran.
        ctx.close.assert_called_once()


class TestPWErrorRouteIntegration:
    """U2 + route-level — user-closed window surfaces as flash redirect,
    not 500. This is the operator-visible behavior the plan promises.
    """

    def test_launch_closed_window_flashes_danger(self, csrf_client):
        client, token = csrf_client
        from backlink_publisher.publishing.adapters.medium_auth import _PWError
        mock_spw, page, *_ = _make_mock_pw()
        page.wait_for_url.side_effect = _PWError(
            "Target page, context or browser has been closed"
        )
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            resp = client.post(
                "/settings/medium/launch-browser-login",
                data={"csrf_token": token},
                headers=_origin_headers(),
            )
        assert resp.status_code == 302  # NOT 500
        loc = urllib.parse.unquote(resp.headers["Location"])
        assert "flash_type=danger" in loc
        assert "登录窗口已关闭" in loc
        assert "channel-medium" in loc

    def test_probe_closed_window_flashes_warning(self, csrf_client):
        client, token = csrf_client
        from backlink_publisher.publishing.adapters.medium_auth import _PWError
        mock_spw, page, ctx, br, _ = _make_mock_pw_probe()
        page.goto.side_effect = _PWError(
            "Target page, context or browser has been closed"
        )
        with patch("backlink_publisher.publishing.adapters.medium_auth.sync_playwright", mock_spw):
            resp = client.post(
                "/settings/medium/probe-browser-login",
                data={"csrf_token": token},
                headers=_origin_headers(),
            )
        assert resp.status_code == 302  # NOT 500
        loc = urllib.parse.unquote(resp.headers["Location"])
        # route handler maps probe ExternalServiceError -> warning, not danger
        assert "flash_type=warning" in loc
        assert "浏览器窗口被关闭" in loc
