"""Shared browser session manager for publishing adapters.

Manages a singleton Playwright browser instance, provides platform-specific
contexts with cookies pre-loaded, and handles credential health checks
(both offline filesystem checks and live API-level verification).

Usage::

    from backlink_publisher.publishing.session_manager import SessionManager

    ctx = SessionManager.get_context("medium", config)
    page = ctx.new_page()
    page.goto("https://medium.com/new-story")
    ...
    SessionManager.close()

Singleton per process — the browser stays alive across publish calls and
retries, avoiding redundant launches.  Call ``close()`` explicitly at the
end of the publish run (registered via ``atexit`` automatically).

Plan 2026-05-21-001 Unit 3.
"""

from __future__ import annotations

import atexit
import json
import time
from pathlib import Path
from typing import Any

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError
from backlink_publisher._util.logger import opencli_logger as log

try:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Playwright,
        sync_playwright,
        TimeoutError as PlaywrightTimeoutError,
    )
except ImportError:
    Browser = None  # type: ignore[assignment,misc]
    BrowserContext = None  # type: ignore[assignment,misc]
    Playwright = None  # type: ignore[assignment,misc]
    sync_playwright = None  # type: ignore[assignment]
    PlaywrightTimeoutError = Exception  # type: ignore[assignment,misc]


_PLAYWRIGHT_AVAILABLE = sync_playwright is not None

# Live health check config per platform.
# Each entry: (auth_url, failure_url_segment).
# ``auth_url`` is the URL to fetch with cookies; if the response's final
# URL contains ``failure_url_segment``, the cookies are considered stale.
_LIVE_CHECK_CONFIG: dict[str, tuple[str, str]] = {
    "medium": ("https://medium.com/me", "/m/signin"),
}


class SessionManagerError(Exception):
    """Session manager operation failed."""


def _cookies_path(platform: str) -> Path:
    """Return the expected cookies path for a browser-based platform."""
    from backlink_publisher.config.loader import _config_dir
    return _config_dir() / f"{platform}-cookies.json"


def _load_cookies(platform: str) -> list[dict[str, Any]]:
    """Load cookies from the platform's cookies file.

    Returns empty list if file is missing or malformed (caller should
    handle via health check).
    """
    path = _cookies_path(platform)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    cookies = payload.get("cookies", [])
    return cookies if isinstance(cookies, list) else []


class SessionManager:
    """Singleton that manages a shared Playwright browser instance.

    Lazy-initialized: the browser launches on the first call to
    ``get_context()`` and stays alive until ``close()``.
    """

    _playwright: Playwright | None = None
    _browser: Browser | None = None
    _refcount: int = 0

    @classmethod
    def available(cls) -> bool:
        """Return True if Playwright is installed."""
        return _PLAYWRIGHT_AVAILABLE

    @classmethod
    def _ensure_playwright(cls) -> None:
        if not _PLAYWRIGHT_AVAILABLE:
            raise SessionManagerError(
                "Playwright is not installed. Run: playwright install chromium"
            )
        if cls._playwright is None:
            cls._playwright = sync_playwright()
        if cls._browser is None:
            log.info("session_manager: launching shared browser")
            cls._browser = cls._playwright.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            atexit.register(cls.close)

    @classmethod
    def get_context(
        cls,
        platform: str,
        config: Config,
        *,
        grant_clipboard: bool = False,
        cookies: list[dict[str, Any]] | None = None,
    ) -> BrowserContext:
        """Get a Playwright browser context for the given platform.

        Creates a new context with platform cookies pre-loaded (from
        ``<config_dir>/<platform>-cookies.json``).  Optional clipboard
        permissions for editors that need them (Medium).

        The context is short-lived per publish attempt — callers should
        close it when done via ``context.close()``.
        """
        cls._ensure_playwright()
        assert cls._browser is not None  # ensured above

        context = cls._browser.new_context()

        if cookies is not None:
            if cookies:
                context.add_cookies(cookies)
        else:
            loaded = _load_cookies(platform)
            if loaded:
                context.add_cookies(loaded)

        if grant_clipboard:
            try:
                context.grant_permissions(
                    ["clipboard-read", "clipboard-write"],
                    origin="https://medium.com",
                )
            except Exception as exc:
                log.warn(f"session_manager: grant_permissions failed: {exc}")

        cls._refcount += 1
        return context

    @classmethod
    def close_context(cls) -> None:
        """Decrement the reference count (call after context.close())."""
        if cls._refcount > 0:
            cls._refcount -= 1

    @classmethod
    def health_check(
        cls, platform: str, config: Config, *, mode: str = "offline",
    ) -> bool:
        """Check whether the platform's cookies are valid.

        Two modes:

        * ``mode='offline'`` (default) — checks that a cookies file exists
          on disk and is parseable.  No network call.
        * ``mode='live'`` — makes a lightweight HTTP request to the
          platform's auth endpoint with the stored cookies and checks for
          a login redirect.  Returns False if the network check fails
          (timeout, HTTP error) since a failed network call is also a
          failed publish.

        Unknown platforms fall back to offline mode silently.
        """
        cookies = _load_cookies(platform)
        if not cookies:
            return False

        if mode != "live":
            return True

        check = _LIVE_CHECK_CONFIG.get(platform)
        if check is None:
            return True

        url, failure_segment = check
        try:
            from urllib.request import Request, urlopen

            cookie_header = "; ".join(
                f"{c['name']}={c['value']}"
                for c in cookies
                if "name" in c and "value" in c
            )
            req = Request(url)
            req.add_header("Cookie", cookie_header)
            req.add_header("User-Agent", "backlink-publisher/1.0")
            resp = urlopen(req, timeout=10)
            final_url = resp.url
            return failure_segment not in final_url
        except Exception as exc:
            log.info(
                f"session_manager: live health check for {platform!r} "
                f"failed: {type(exc).__name__}: {exc}"
            )
            return False

    @classmethod
    def close(cls) -> None:
        """Close browser and Playwright instance. Idempotent."""
        if cls._browser is not None:
            try:
                cls._browser.close()
            except Exception as exc:
                log.warn(f"session_manager: browser.close() failed: {exc}")
            cls._browser = None
        if cls._playwright is not None:
            try:
                cls._playwright.stop()
            except Exception as exc:
                log.warn(f"session_manager: playwright.stop() failed: {exc}")
            cls._playwright = None
        cls._refcount = 0

    @classmethod
    def refresh_cookies_to_disk(
        cls,
        platform: str,
        context: BrowserContext,
        domain: str = "https://medium.com",
    ) -> None:
        """Atomically refresh the platform's cookies file from the current context.

        Best-effort: failure is logged but does not raise — credentials
        are merely slightly stale, not invalid.
        """
        import os
        import tempfile

        target = _cookies_path(platform)
        try:
            live_cookies = context.cookies(domain) or []
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{platform}-cookies.",
                suffix=".tmp",
                dir=str(target.parent),
            )
            os.close(fd)
            tmp_path = Path(tmp_name)
            try:
                tmp_path.write_text(
                    json.dumps({"cookies": live_cookies}, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.chmod(tmp_path, 0o600)
                os.replace(tmp_path, target)
            except Exception:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass
                raise
        except Exception as exc:
            log.warn(
                f"session_manager: failed to refresh {platform}-cookies.json: "
                f"{type(exc).__name__}: {exc}"
            )
