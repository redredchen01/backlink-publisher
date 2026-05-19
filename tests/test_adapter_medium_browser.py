"""Tests for MediumBrowserAdapter (Playwright mocked).

Plan 2026-05-19-005 Unit 1: adapter hard-cut from
``new_context(storage_state=<medium-storage-state.json>)`` to
``new_context() + context.add_cookies(<medium-cookies.json>)``. Tests
updated to mock the new API surface and to exercise:

- ``medium-cookies.json`` absent → ``DependencyError`` raised BEFORE
  Playwright launches; message points operator at ``medium-login``.
- ``medium-cookies.json`` mode != 0o600 → ``DependencyError``.
- ``medium-cookies.json`` malformed JSON → ``DependencyError``.
- ``/m/signin`` redirect during publish → ``mark_expired`` called +
  ``AuthExpiredError`` raised (Plan 003 behavior preserved).
- Successful publish refreshes ``medium-cookies.json`` via
  ``context.cookies('https://medium.com')``.

All tests use the autouse ``_isolate_user_dirs`` fixture (from conftest)
to point ``_config_dir()`` at a per-session tmp dir, and pre-create the
``medium-cookies.json`` file inside that dir.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.adapters.medium_browser import MediumBrowserAdapter
from backlink_publisher.config import Config
from backlink_publisher.config.loader import _config_dir
from backlink_publisher.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
)

PAYLOAD = {
    "id": "abc123",
    "title": "Test Post",
    "content_markdown": "# Hello\n\nWorld.",
    "tags": ["tag1", "tag2"],
    "seo": {"canonical_url": "https://example.com/article"},
}

CONFIG = Config(medium_user_data_dir=Path("/tmp/test-chrome-profile"))


@pytest.fixture(autouse=True)
def _prepare_cookies(monkeypatch):
    """Pre-create ``<config_dir>/medium-cookies.json`` (0600) so the adapter
    finds a credential to load. Each test starts with one apex-cookie."""
    import os
    cfg = _config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    cookies_path = cfg / "medium-cookies.json"
    cookies_path.write_text(
        '{"cookies": [{"name": "sid", "value": "test-sid", '
        '"domain": "medium.com", "path": "/", "httpOnly": true, '
        '"secure": true, "sameSite": "Lax"}]}'
    )
    os.chmod(cookies_path, 0o600)


@pytest.fixture(autouse=True)
def _reset_channel_status(monkeypatch):
    """Each test gets a fresh channel-status.json so mark_expired calls are
    observable without bleed across tests."""
    fresh = _config_dir() / "channel-status.json"
    if fresh.exists():
        fresh.unlink()
    from webui_store import channel_status_store
    monkeypatch.setattr(channel_status_store, "path", fresh, raising=False)


def make_mock_pw(page_url="https://medium.com/@user/test-draft-abc123"):
    """Build a Playwright mock for the Plan 005 Unit 1 API:
    ``launch() + browser.new_context() + context.add_cookies(...) +
    context.new_page()``.
    """
    mock_page = MagicMock()
    mock_page.url = page_url
    mock_page.locator.return_value = MagicMock()
    mock_page.locator.return_value.count.return_value = 0
    mock_page.evaluate = MagicMock()
    mock_page.keyboard = MagicMock()

    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page
    mock_context.__enter__ = MagicMock(return_value=mock_context)
    mock_context.__exit__ = MagicMock(return_value=False)
    # context.cookies(...) returns fresh cookies for refresh path
    mock_context.cookies.return_value = [
        {"name": "sid", "value": "refreshed-sid", "domain": "medium.com",
         "path": "/", "httpOnly": True, "secure": True, "sameSite": "Lax"}
    ]
    mock_context.add_cookies = MagicMock()

    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_context

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser
    mock_pw.__enter__ = MagicMock(return_value=mock_pw)
    mock_pw.__exit__ = MagicMock(return_value=False)

    return mock_pw, mock_browser, mock_context, mock_page


# ─── Happy paths ───


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_draft_mode_returns_draft_url(mock_sync_pw):
    mock_pw, mock_br, mock_ctx, mock_page = make_mock_pw()
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG)

    assert result.status == "drafted"
    assert result.draft_url == "https://medium.com/@user/test-draft-abc123"
    assert result.adapter == "medium-browser"


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_publish_mode_clicks_publish_button(mock_sync_pw):
    mock_pw, mock_br, mock_ctx, mock_page = make_mock_pw(
        "https://medium.com/@user/live-post-abc123"
    )
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    result = adapter.publish(PAYLOAD, mode="publish", config=CONFIG)

    assert result.status == "published"
    assert result.published_url == "https://medium.com/@user/live-post-abc123"


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_uses_add_cookies_not_storage_state(mock_sync_pw):
    """Plan 005 Unit 1 contract: adapter MUST load credentials via
    ``new_context() + add_cookies([...])``, NOT via
    ``new_context(storage_state=...)`` (Plan 003 contract is gone)."""
    mock_pw, mock_br, mock_ctx, mock_page = make_mock_pw()
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    adapter.publish(PAYLOAD, mode="draft", config=CONFIG)

    # new_context was called WITHOUT storage_state kwarg
    new_context_call = mock_br.new_context.call_args
    assert new_context_call is not None
    assert "storage_state" not in new_context_call.kwargs

    # add_cookies was called with the apex-cookie from the fixture
    assert mock_ctx.add_cookies.call_count == 1
    cookies_arg = mock_ctx.add_cookies.call_args.args[0]
    assert isinstance(cookies_arg, list)
    assert any(c.get("name") == "sid" and c.get("domain") == "medium.com"
               for c in cookies_arg)

    # launch was called (NOT launch_persistent_context)
    mock_pw.chromium.launch.assert_called_once()
    mock_pw.chromium.launch_persistent_context.assert_not_called()


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_success_refreshes_cookies_json(mock_sync_pw):
    """Plan 005 Unit 1: successful publish refreshes medium-cookies.json
    via context.cookies('https://medium.com') so rotated session cookies
    stay fresh."""
    import json
    mock_pw, mock_br, mock_ctx, mock_page = make_mock_pw()
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    adapter.publish(PAYLOAD, mode="draft", config=CONFIG)

    # context.cookies was called with the apex URL
    mock_ctx.cookies.assert_called_with("https://medium.com")

    # File on disk reflects the refresh
    cookies_path = _config_dir() / "medium-cookies.json"
    payload = json.loads(cookies_path.read_text())
    assert payload["cookies"][0]["value"] == "refreshed-sid"


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_refreshed_cookies_file_is_0600(mock_sync_pw):
    """Plan 005 Unit 1 R3: the refresh must preserve 0o600 mode."""
    mock_pw, mock_br, mock_ctx, mock_page = make_mock_pw()
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    adapter.publish(PAYLOAD, mode="draft", config=CONFIG)

    cookies_path = _config_dir() / "medium-cookies.json"
    assert (cookies_path.stat().st_mode & 0o777) == 0o600


# ─── Credential-missing / bad-mode / malformed paths ───


def test_cookies_absent_raises_dependency_error():
    """Plan 005 Unit 1: medium-cookies.json missing → DependencyError with
    actionable message pointing at medium-login. NOT AuthExpiredError —
    missing creds is a setup state, not an auth-expired state."""
    cookies_path = _config_dir() / "medium-cookies.json"
    if cookies_path.exists():
        cookies_path.unlink()

    adapter = MediumBrowserAdapter()
    with pytest.raises(DependencyError) as excinfo:
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    assert "medium-cookies.json not found" in str(excinfo.value)
    assert "medium-login" in str(excinfo.value)


def test_cookies_wrong_mode_raises_dependency_error():
    """Plan 005 Unit 1 R3: medium-cookies.json with mode != 0o600 →
    fail-loud DependencyError (don't trust leaky creds)."""
    import os
    cookies_path = _config_dir() / "medium-cookies.json"
    os.chmod(cookies_path, 0o644)

    adapter = MediumBrowserAdapter()
    with pytest.raises(DependencyError) as excinfo:
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    assert "0o600" in str(excinfo.value)
    assert "0o644" in str(excinfo.value)


def test_cookies_malformed_json_raises_dependency_error():
    """Plan 005 Unit 1: corrupt JSON → DependencyError pointing at
    medium-login for re-binding."""
    import os
    cookies_path = _config_dir() / "medium-cookies.json"
    cookies_path.write_text("{not valid json")
    os.chmod(cookies_path, 0o600)

    adapter = MediumBrowserAdapter()
    with pytest.raises(DependencyError) as excinfo:
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    assert "invalid" in str(excinfo.value).lower()
    assert "medium-login" in str(excinfo.value)


def test_cookies_wrong_shape_raises_dependency_error():
    """``cookies`` field must be a list. Anything else → DependencyError."""
    import os
    cookies_path = _config_dir() / "medium-cookies.json"
    cookies_path.write_text('{"cookies": "not a list"}')
    os.chmod(cookies_path, 0o600)

    adapter = MediumBrowserAdapter()
    with pytest.raises(DependencyError) as excinfo:
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    assert "malformed" in str(excinfo.value).lower()


# ─── Auth-expired during publish (unchanged from Plan 003) ───


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_login_redirect_raises_auth_expired_error(mock_sync_pw):
    """``/m/signin`` redirect during publish raises AuthExpiredError — this
    is genuinely auth-expired (cookies are stale, not missing)."""
    mock_pw, mock_br, mock_ctx, mock_page = make_mock_pw()
    mock_page.url = "https://medium.com/m/signin?redirect=..."
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    with pytest.raises(AuthExpiredError) as excinfo:
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    assert excinfo.value.channel == "medium"
    assert "/m/signin" in (excinfo.value.reason or "")
    assert "medium-login" in (excinfo.value.reason or "")


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_login_redirect_marks_expired_in_store(mock_sync_pw):
    """``mark_expired('medium')`` is called when /m/signin redirect detected."""
    mock_pw, mock_br, mock_ctx, mock_page = make_mock_pw()
    mock_page.url = "https://medium.com/m/signin?redirect=..."
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    with pytest.raises(AuthExpiredError):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)

    from webui_store.channel_status import get_status
    assert get_status("medium")["status"] == "expired"


# ─── Existing error paths preserved ───


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_captcha_raises_external_service_error(mock_sync_pw):
    """CAPTCHA still raises ExternalServiceError (NOT AuthExpiredError) —
    CAPTCHA isn't an auth-expiration; operator solves it manually."""
    mock_pw, mock_br, mock_ctx, mock_page = make_mock_pw()
    mock_page.url = "https://medium.com/new-story"
    captcha_locator = MagicMock()
    captcha_locator.count.return_value = 1

    def locator_side_effect(sel_str):
        if "captcha" in sel_str:
            return captcha_locator
        return MagicMock()

    mock_page.locator.side_effect = locator_side_effect
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    with pytest.raises(ExternalServiceError, match="CAPTCHA"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)


def test_playwright_not_installed_raises_dependency_error():
    """When sync_playwright is None (import failed at module load), raise DependencyError."""
    import backlink_publisher.adapters.medium_browser as mod
    original = mod.sync_playwright
    try:
        mod.sync_playwright = None
        adapter = MediumBrowserAdapter()
        with pytest.raises(DependencyError, match="Playwright is not installed"):
            adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    finally:
        mod.sync_playwright = original


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_playwright_timeout_retried_and_recovers(mock_sync_pw, mock_sleep):
    """PlaywrightTimeoutError on first attempt triggers retry; second succeeds."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    success_pw, success_br, success_ctx, success_page = make_mock_pw()

    call_count = [0]

    def sync_pw_factory():
        call_count[0] += 1
        if call_count[0] == 1:
            # First attempt: page.goto raises TimeoutError, no CAPTCHA
            fail_page = MagicMock()
            fail_page.url = "https://medium.com/new-story"
            fail_page.locator.return_value.count.return_value = 0
            fail_page.goto.side_effect = PlaywrightTimeout("timeout")

            fail_ctx = MagicMock()
            fail_ctx.new_page.return_value = fail_page
            fail_ctx.__enter__ = MagicMock(return_value=fail_ctx)
            fail_ctx.__exit__ = MagicMock(return_value=False)
            fail_ctx.add_cookies = MagicMock()

            fail_br = MagicMock()
            fail_br.new_context.return_value = fail_ctx

            fail_pw = MagicMock()
            fail_pw.chromium.launch.return_value = fail_br
            fail_pw.__enter__ = MagicMock(return_value=fail_pw)
            fail_pw.__exit__ = MagicMock(return_value=False)
            return fail_pw
        return success_pw

    mock_sync_pw.side_effect = sync_pw_factory

    adapter = MediumBrowserAdapter()
    result = adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    assert result.status == "drafted"
    mock_sleep.assert_called_once()
    assert call_count[0] == 2  # two browser contexts opened


@patch("backlink_publisher.adapters.retry.time.sleep")
@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_captcha_after_timeout_not_retried(mock_sync_pw, mock_sleep):
    """TimeoutError with CAPTCHA in DOM → non-retryable ExternalServiceError, no sleep."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    mock_page = MagicMock()
    mock_page.url = "https://medium.com/new-story"

    # CAPTCHA present
    captcha_locator = MagicMock()
    captcha_locator.count.return_value = 1
    mock_page.locator.return_value = captcha_locator
    mock_page.goto.side_effect = PlaywrightTimeout("slow load")

    mock_ctx = MagicMock()
    mock_ctx.new_page.return_value = mock_page
    mock_ctx.add_cookies = MagicMock()

    mock_br = MagicMock()
    mock_br.new_context.return_value = mock_ctx

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_br
    mock_pw.__enter__ = MagicMock(return_value=mock_pw)
    mock_pw.__exit__ = MagicMock(return_value=False)

    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    with pytest.raises(ExternalServiceError, match="CAPTCHA"):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    mock_sleep.assert_not_called()


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_html_clipboard_content_matches_render(mock_sync_pw):
    """Clipboard write must contain rendered HTML, not raw markdown."""
    from backlink_publisher.markdown_utils import render_to_html
    expected_html = render_to_html(PAYLOAD["content_markdown"])

    mock_pw, mock_br, mock_ctx, mock_page = make_mock_pw()
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    adapter.publish(PAYLOAD, mode="draft", config=CONFIG)

    # page.evaluate was called with the rendered HTML as the second argument
    evaluate_calls = mock_page.evaluate.call_args_list
    html_args = [c.args[1] for c in evaluate_calls if len(c.args) > 1]
    assert any(expected_html in arg for arg in html_args), (
        f"Expected rendered HTML in clipboard evaluate args. Got: {html_args}"
    )
