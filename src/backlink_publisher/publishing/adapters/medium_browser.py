"""Medium browser fallback adapter using Playwright.

Storage convergence (Plan 2026-05-19-003 Unit 6):
The adapter loads its credential from ``<config_dir>/medium-storage-state.json``
(written by ``bind-channel medium``, Plan 2026-05-19-001 Unit 2) via
``new_context(storage_state=...)``. The legacy ``launch_persistent_context``
flow that read from ``~/.config/backlink-publisher/chrome-profile-default/``
is removed; that directory is unused as of Plan 003 and gets a one-time
deprecation notice on adapter import-detection.

On ``/m/signin`` redirect during publish: writes ``mark_expired('medium')``
(channel_status_store, Plan 001 Unit 1) inside a try/except so filesystem
failure doesn't mask the auth error, then raises ``AuthExpiredError(
channel='medium', reason=...)`` (Plan 001 Unit 1 class). The operator
re-binds via the webui Settings page.

On successful publish: refreshes ``medium-storage-state.json`` via
``context.storage_state(path=...)`` with atomic temp+rename so Medium's
rotated session cookies stay fresh.

Always runs headed (Medium detects headless aggressively).
"""

from __future__ import annotations

import os
import platform
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backlink_publisher.config import Config
from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.config.loader import _config_dir
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .link_attr_verifier import verify_link_attributes
from .retry import retry_transient_call
from . import _medium_selectors as sel

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover — tested via DependencyError path
    sync_playwright = None  # type: ignore[assignment]
    PlaywrightTimeoutError = Exception  # type: ignore[assignment,misc]


# Module-level flag for once-per-process legacy-dir notice.
_LEGACY_NOTICE_LOGGED = False


def _json_log(**kwargs: Any) -> str:
    import json
    return json.dumps(kwargs)


def _screenshot_path(config: Config, article_id: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shots_dir = config.screenshot_dir
    shots_dir.mkdir(parents=True, exist_ok=True)
    return shots_dir / f"{article_id}-{ts}.png"


def _paste_key() -> str:
    return "Meta+V" if platform.system() == "Darwin" else "Control+V"


def _storage_state_path() -> Path:
    """Plan 003 Unit 6: ``<config_dir>/medium-storage-state.json``.

    Single source of truth for Medium browser credentials. Written by
    ``bind-channel medium`` (Plan 001 Unit 2); read by this adapter via
    ``new_context(storage_state=...)``; refreshed on every successful
    publish to keep up with Medium's session cookie rotation.
    """
    return _config_dir() / "medium-storage-state.json"


def _safe_mark_expired() -> None:
    """Call ``mark_expired('medium')`` swallowing filesystem errors.

    The caller is about to raise ``AuthExpiredError``; we must not let a
    secondary filesystem failure (disk full, permission denied) mask the
    primary auth-expired signal. Logs a warning on failure so the failure
    isn't completely silent."""
    try:
        from webui_store.channel_status import mark_expired
        mark_expired("medium")
    except Exception as exc:  # noqa: BLE001 — defensive
        log.warn(
            f"medium_browser: mark_expired('medium') failed during auth-expired "
            f"propagation: {type(exc).__name__}: {exc}"
        )


def _refresh_storage_state(context: Any) -> None:
    """Atomically refresh ``medium-storage-state.json`` from the current
    Playwright context's cookies (Medium rotates session cookies during
    publish flows). Best-effort: failure here is logged but does NOT fail
    the publish — the credentials are merely slightly stale, not invalid."""
    target = _storage_state_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".medium-storage-state.",
            suffix=".tmp",
            dir=str(target.parent),
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            context.storage_state(path=str(tmp_path))
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, target)
        except Exception:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise
    except Exception as exc:  # noqa: BLE001 — best-effort refresh
        log.warn(
            f"medium_browser: failed to refresh storage_state.json: "
            f"{type(exc).__name__}: {exc}"
        )


def _emit_legacy_notice(config: Config) -> None:
    """Log once-per-process if the legacy persistent profile dir from
    pre-Unit-6 builds is still on disk.

    The directory at ``~/.config/backlink-publisher/chrome-profile-default/``
    (or wherever ``config.medium_user_data_dir`` pointed) is unused by this
    adapter as of Plan 003 Unit 6. Existing operators are advised to
    re-bind via the webui Settings page and then delete the legacy dir.
    Suppress with ``BACKLINK_PUBLISHER_MEDIUM_LEGACY_NOTICE=0``.
    """
    global _LEGACY_NOTICE_LOGGED
    if _LEGACY_NOTICE_LOGGED:
        return
    if os.environ.get("BACKLINK_PUBLISHER_MEDIUM_LEGACY_NOTICE", "1") == "0":
        _LEGACY_NOTICE_LOGGED = True
        return
    legacy = config.medium_user_data_dir or (config.config_dir / "chrome-profile-default")
    try:
        if legacy.is_dir() and not legacy.is_symlink():
            log.info(
                f"medium_browser: legacy Chromium profile dir at {legacy} is "
                f"unused as of Plan 2026-05-19-003 Unit 6. Re-bind via "
                f"`bind-channel medium` or the webui Settings page; the "
                f"legacy dir is safe to delete after verifying the new "
                f"storage_state.json. Suppress this notice with "
                f"BACKLINK_PUBLISHER_MEDIUM_LEGACY_NOTICE=0."
            )
    except OSError:
        pass  # stat-on-missing is fine; we're only advising
    _LEGACY_NOTICE_LOGGED = True


class MediumBrowserAdapter(Publisher):
    """Fallback: publish to Medium via headed Playwright browser session."""

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        if sync_playwright is None:
            raise DependencyError(
                "Playwright is not installed. Run: playwright install chromium"
            )

        # Plan 2026-05-19-003 Unit 6: log once-per-process if the legacy
        # persistent profile dir is on disk (operator UX nudge to re-bind).
        _emit_legacy_notice(config)

        article_id = payload.get("id", "")
        t0 = time.monotonic()
        log.info(_json_log(adapter="medium-browser", phase="start", id=article_id))

        # Plan 003 Unit 6: storage_state.json is the credential. Absent file
        # means the operator has never run `bind-channel medium` (or
        # storage was wiped). Mark expired + raise AuthExpiredError BEFORE
        # launching Playwright so the operator sees a fast actionable error
        # rather than a 30s page-goto timeout.
        storage_state = _storage_state_path()
        if not storage_state.exists():
            _safe_mark_expired()
            raise AuthExpiredError(
                channel="medium",
                reason=(
                    f"storage_state.json missing at {storage_state}; run "
                    f"`bind-channel medium` or use the webui Settings page "
                    f"to bind first"
                ),
            )

        # Plan 2026-05-18-006 Unit 5 R9: medium is platform-tier (b)
        # (browser-paste WYSIWYG sanitize is lossy) — helper renders MD even
        # when content_html present. Defense in depth: validate-time gate
        # in Unit 6 rejects content_html-only medium rows before publish.
        html_content = extract_publish_html(payload, "medium")
        title = payload.get("title", "")
        tags = payload.get("tags", [])[:5]

        def _run_browser_publish() -> AdapterResult:
            """One full browser publish attempt — opens and closes its own context."""
            with sync_playwright() as pw:
                # Plan 003 Unit 6: non-persistent launch + storage_state from
                # the bind output. Playwright manages an ephemeral profile
                # dir internally and cleans up on browser.close().
                browser = pw.chromium.launch(
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = browser.new_context(storage_state=str(storage_state))
                page = context.new_page()
                try:
                    context.grant_permissions(
                        ["clipboard-read", "clipboard-write"],
                        origin="https://medium.com",
                    )

                    log.info(_json_log(adapter="medium-browser", phase="open", id=article_id))
                    try:
                        page.goto("https://medium.com/new-story", timeout=30_000)
                    except PlaywrightTimeoutError:
                        # CAPTCHA timing race mitigation: if the page partially loaded with a
                        # CAPTCHA present, raise ExternalServiceError (non-retryable) rather than
                        # retrying into the same locked session.
                        try:
                            if page.locator(sel.CAPTCHA_IFRAME_SELECTOR).count() > 0:
                                raise ExternalServiceError(
                                    "Medium CAPTCHA detected after timeout. "
                                    "Solve it manually at medium.com, then retry."
                                )
                        except ExternalServiceError:
                            raise
                        except Exception:
                            pass  # probe failed; let retry handle the timeout
                        raise  # re-raise PlaywrightTimeoutError for retry_transient_call

                    # Plan 003 Unit 6: detect login redirect; mark expired +
                    # raise AuthExpiredError (replaces the old
                    # ExternalServiceError("Medium login expired...") path).
                    if sel.LOGIN_PATH in page.url:
                        _safe_mark_expired()
                        raise AuthExpiredError(
                            channel="medium",
                            reason=(
                                "redirected to /m/signin during publish; "
                                "storage_state cookies are no longer valid — "
                                "re-bind via the webui Settings page"
                            ),
                        )

                    # Detect CAPTCHA
                    if page.locator(sel.CAPTCHA_IFRAME_SELECTOR).count() > 0:
                        raise ExternalServiceError(
                            "Medium CAPTCHA detected. "
                            "Solve it manually at medium.com, then retry."
                        )

                    # Fill title
                    log.info(_json_log(adapter="medium-browser", phase="fill-title", id=article_id))
                    page.locator(sel.TITLE).click()
                    page.keyboard.type(title)

                    # Paste HTML body via clipboard
                    log.info(_json_log(adapter="medium-browser", phase="fill-body", id=article_id))
                    page.locator(sel.BODY).click()
                    page.evaluate(
                        "async (html) => { await navigator.clipboard.writeText(html); }",
                        html_content,
                    )
                    page.keyboard.press(_paste_key())
                    page.wait_for_timeout(1500)

                    # Publish or save draft
                    if mode == "publish":
                        log.info(_json_log(adapter="medium-browser", phase="publish", id=article_id))
                        page.locator(sel.PUBLISH_MENU).click()
                        page.wait_for_timeout(1000)
                        try:
                            tag_input = page.locator(sel.TAGS_INPUT)
                            for tag in tags:
                                tag_input.type(tag)
                                page.keyboard.press("Enter")
                                page.wait_for_timeout(300)
                        except Exception as e:
                            log.debug(f"tag insertion failed (optional): {e}")  # tags are optional
                        page.locator(sel.PUBLISH_BUTTON).click()
                        page.wait_for_timeout(3000)
                    else:
                        try:
                            page.locator(sel.SAVE_DRAFT).click()
                            page.wait_for_timeout(2000)
                        except Exception:
                            page.wait_for_timeout(3000)

                    final_url = page.url
                    elapsed = int((time.monotonic() - t0) * 1000)
                    log.info(
                        _json_log(
                            adapter="medium-browser",
                            phase="done",
                            id=article_id,
                            elapsed_ms=elapsed,
                        )
                    )

                    # Plan 003 Unit 6: refresh storage_state.json from the
                    # current context (Medium rotates session cookies during
                    # publish). Best-effort: failure here is logged but does
                    # NOT fail the publish — cookies are merely slightly stale.
                    _refresh_storage_state(context)

                    context.close()
                    browser.close()

                    if mode == "publish":
                        meta: dict = {}
                        if final_url:
                            attr_check = verify_link_attributes(final_url)
                            meta["link_attr_verification"] = attr_check
                            ratio = attr_check.get("blank_ratio", 1.0)
                            total = attr_check.get("total_anchors", 0)
                            if attr_check.get("verification") == "ok" and total > 0 and ratio < 0.5:
                                log.warn(
                                    f"Medium stripped target attributes: "
                                    f"{attr_check['blank_anchors']}/{total} anchors "
                                    "retain target=_blank"
                                )
                        return AdapterResult(
                            status="published",
                            adapter="medium-browser",
                            platform="medium",
                            published_url=final_url,
                            post_publish_delay_seconds=30,
                            _provider_meta=meta if meta else None,
                        )
                    return AdapterResult(
                        status="drafted",
                        adapter="medium-browser",
                        platform="medium",
                        draft_url=final_url,
                        post_publish_delay_seconds=30,
                    )

                except AuthExpiredError:
                    _save_screenshot(page, config, article_id)
                    context.close()
                    browser.close()
                    raise
                except ExternalServiceError:
                    _save_screenshot(page, config, article_id)
                    context.close()
                    browser.close()
                    raise
                except PlaywrightTimeoutError:
                    # Let PlaywrightTimeoutError propagate to retry_transient_call
                    # without wrapping as ExternalServiceError.
                    _save_screenshot(page, config, article_id)
                    context.close()
                    browser.close()
                    raise
                except Exception as exc:
                    _save_screenshot(page, config, article_id)
                    context.close()
                    browser.close()
                    raise ExternalServiceError(
                        f"Medium browser automation failed: {exc}"
                    ) from exc

        return retry_transient_call(
            _run_browser_publish,
            is_retryable=lambda exc: isinstance(exc, PlaywrightTimeoutError),
            adapter="medium-browser",
        )


def _save_screenshot(page: Any, config: Config, article_id: str) -> None:
    try:
        shot_path = _screenshot_path(config, article_id)
        page.screenshot(path=str(shot_path))
        import sys
        import json
        print(
            json.dumps({"level": "ERROR", "screenshot": str(shot_path)}),
            file=sys.stderr,
        )
    except Exception:
        pass
