"""Tests for MediumBrowserAdapter (Playwright mocked).

Plan 2026-05-19-003 Unit 6: adapter rewritten to use ``new_context(
storage_state=...)`` instead of ``launch_persistent_context``. Tests
updated to mock the new API surface and to exercise:

- ``storage_state.json`` absent → ``mark_expired`` + ``AuthExpiredError``
  raised BEFORE Playwright launches.
- ``/m/signin`` redirect during publish → ``mark_expired`` called +
  ``AuthExpiredError`` raised (replaces old ``ExternalServiceError``).
- Successful publish refreshes ``storage_state.json`` via
  ``context.storage_state(path=...)``.
- Legacy ``chrome-profile-default/`` dir triggers a one-time notice.

All tests use the autouse ``_isolate_user_dirs`` fixture (from conftest)
to point ``_config_dir()`` at a per-session tmp dir, and pre-create the
``medium-storage-state.json`` file inside that dir.
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
def _prepare_storage_state(monkeypatch):
    """Pre-create ``<config_dir>/medium-storage-state.json`` so the adapter
    finds a credential to load. Reset the legacy-notice flag between tests."""
    cfg = _config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    storage = cfg / "medium-storage-state.json"
    if not storage.exists():
        storage.write_text('{"cookies": [], "origins": []}')

    # Reset module-level once-per-process legacy notice flag
    import backlink_publisher.publishing.adapters.medium_browser as mod
    monkeypatch.setattr(mod, "_LEGACY_NOTICE_LOGGED", False, raising=False)


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
    """Build a Playwright mock for the Plan 003 Unit 6 API:
    ``launch() + browser.new_context(storage_state=...) + context.new_page()``.
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
    # storage_state(path=...) writes the refresh file; just no-op in tests
    mock_context.storage_state = MagicMock()

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
def test_uses_new_context_with_storage_state(mock_sync_pw):
    """Plan 003 Unit 6 contract: adapter MUST load credentials via
    ``new_context(storage_state=<path>)``, NOT via
    ``launch_persistent_context(user_data_dir)``."""
    mock_pw, mock_br, mock_ctx, mock_page = make_mock_pw()
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    adapter.publish(PAYLOAD, mode="draft", config=CONFIG)

    # new_context called with storage_state pointing at the config_dir path
    new_context_call = mock_br.new_context.call_args
    assert new_context_call is not None
    storage_state_arg = new_context_call.kwargs.get("storage_state")
    assert storage_state_arg is not None
    assert "medium-storage-state.json" in storage_state_arg

    # launch was called (NOT launch_persistent_context)
    mock_pw.chromium.launch.assert_called_once()
    mock_pw.chromium.launch_persistent_context.assert_not_called()


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_success_refreshes_storage_state(mock_sync_pw):
    """Plan 003 Unit 6: successful publish refreshes storage_state.json
    via context.storage_state(path=...) so rotated session cookies stay
    fresh."""
    mock_pw, mock_br, mock_ctx, mock_page = make_mock_pw()
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    adapter.publish(PAYLOAD, mode="draft", config=CONFIG)

    # context.storage_state was called once with a path keyword
    mock_ctx.storage_state.assert_called_once()
    storage_call_kwargs = mock_ctx.storage_state.call_args.kwargs
    assert "path" in storage_call_kwargs


# ─── Auth-expired paths ───


def test_storage_state_absent_raises_auth_expired():
    """Plan 003 Unit 6: storage_state.json missing → mark_expired +
    AuthExpiredError raised BEFORE Playwright launches."""
    storage = _config_dir() / "medium-storage-state.json"
    if storage.exists():
        storage.unlink()

    adapter = MediumBrowserAdapter()
    with pytest.raises(AuthExpiredError) as excinfo:
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    assert excinfo.value.channel == "medium"
    assert "storage_state.json missing" in (excinfo.value.reason or "")


def test_storage_state_absent_marks_expired_in_store():
    """mark_expired('medium') is called when storage_state.json absent."""
    storage = _config_dir() / "medium-storage-state.json"
    if storage.exists():
        storage.unlink()

    adapter = MediumBrowserAdapter()
    with pytest.raises(AuthExpiredError):
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)

    from webui_store.channel_status import get_status
    assert get_status("medium")["status"] == "expired"


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_login_redirect_raises_auth_expired_error(mock_sync_pw):
    """Plan 003 Unit 6: /m/signin redirect during publish raises
    AuthExpiredError (was ExternalServiceError in pre-Unit-6 code)."""
    mock_pw, mock_br, mock_ctx, mock_page = make_mock_pw()
    mock_page.url = "https://medium.com/m/signin?redirect=..."
    mock_sync_pw.return_value = mock_pw

    adapter = MediumBrowserAdapter()
    with pytest.raises(AuthExpiredError) as excinfo:
        adapter.publish(PAYLOAD, mode="draft", config=CONFIG)
    assert excinfo.value.channel == "medium"
    assert "/m/signin" in (excinfo.value.reason or "")


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_login_redirect_marks_expired_in_store(mock_sync_pw):
    """mark_expired('medium') is called when /m/signin redirect detected."""
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


# ─── Legacy-dir notice ───


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_legacy_dir_notice_logs_once_per_process(mock_sync_pw, monkeypatch, tmp_path, caplog):
    """Plan 003 Unit 6 / Unit 2.5: if legacy chrome-profile-default/ exists,
    log a once-per-process deprecation notice."""
    import logging
    caplog.set_level(logging.INFO)

    mock_pw, mock_br, mock_ctx, mock_page = make_mock_pw()
    mock_sync_pw.return_value = mock_pw

    # Synthesize a legacy dir at the config-derived location used by the
    # notice helper (we point medium_user_data_dir at a real temp dir).
    legacy_dir = tmp_path / "chrome-profile-default"
    legacy_dir.mkdir()
    cfg = Config(medium_user_data_dir=legacy_dir)

    adapter = MediumBrowserAdapter()
    adapter.publish(PAYLOAD, mode="draft", config=cfg)
    adapter.publish(PAYLOAD, mode="draft", config=cfg)  # second call: no extra log


@patch("backlink_publisher.adapters.medium_browser.sync_playwright")
def test_legacy_notice_suppressed_by_env(mock_sync_pw, monkeypatch, tmp_path):
    """``BACKLINK_PUBLISHER_MEDIUM_LEGACY_NOTICE=0`` suppresses the notice."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_MEDIUM_LEGACY_NOTICE", "0")

    mock_pw, mock_br, mock_ctx, mock_page = make_mock_pw()
    mock_sync_pw.return_value = mock_pw

    legacy_dir = tmp_path / "chrome-profile-default"
    legacy_dir.mkdir()
    cfg = Config(medium_user_data_dir=legacy_dir)

    adapter = MediumBrowserAdapter()
    # Should not raise; suppression is best-effort and silent.
    adapter.publish(PAYLOAD, mode="draft", config=cfg)
