"""Medium binding recipe — Plan 2026-05-19-001 Unit 2 + Plan 003 Unit 1.

Channel: ``medium`` (medium.com).

Login flow: operator visits ``https://medium.com/m/signin``; once authed,
medium.com redirects off ``/m/signin`` (typically to ``/@<username>``).

Bound predicate (Plan 003 Unit 1 hardening):
  1. Idle-detection timeout (90s no nav) + 20-min absolute wall, replacing
     Plan 001 driver's 5-min default for Medium only.
  2. URL pattern match (existing negative-match: not ``/m/signin``).
  3. HttpOnly cookie sanity (whitelist + structural fallback that rejects
     known anonymous-tracking names like ``uid``, ``_ga``, ``_dd_s``).
     Prevents the "any HttpOnly cookie = bound" false positive on
     logged-out page loads.
  4. Username scrape (DOM data-testid → og:url meta → URL parse).
  5. Identity-mismatch check against ``<config_dir>/medium-last-account.txt``
     (Plan 003 R6): raises ``IdentityMismatch`` if stored last_account
     differs from current scraped username.
  6. Atomic write of ``<config_dir>/medium-last-account.tentative``;
     driver promotes to ``.txt`` after ``_persist_storage_state`` succeeds.

Cookie host filter: exact-apex match against ``medium.com``. Subdomains
not accepted (the auth cookie lives on the apex).

This recipe does NOT replace the existing Medium integration-token /
OAuth flow in ``medium_api`` — see Plan 2026-05-19-001 Key Technical
Decisions. Binding produces a parallel ``storage_state.json`` sentinel
that ``reconcile_on_load`` watches; ``medium-token.json`` continues to
be the source of truth for ``MediumAPIAdapter.publish``.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from backlink_publisher.cli._bind.driver import IdentityMismatch
from backlink_publisher.config.loader import _config_dir

from . import ChannelRecipe


_LOGIN_URL = "https://medium.com/m/signin"

_BOUND_URL_PATTERN = re.compile(r"https?://(?:[^/]*\.)?medium\.com/(?!m/signin)(?:.*)?$")

# Plan 003 Unit 0 / R5. Whitelisted HttpOnly auth cookie names on
# medium.com apex. Populated from Spike 3a (2026-05-19): a logged-in
# medium.com profile sets ``sid`` (session id) and ``rid`` (refresh id)
# both HttpOnly with ~1.5-year expiry. These are the canonical Medium
# auth pair — presence proves an authenticated session beyond doubt.
# Structural fallback below still applies for cookies whose names rotate.
MEDIUM_AUTH_COOKIE_WHITELIST: frozenset[str] = frozenset({"sid", "rid"})

# Cookies Medium sets on logged-out + logged-in sessions that must NOT
# count as proof-of-auth. Three families:
#   1. Analytics / tracking (uid, _ga, _dd_s, …) — set for anonymous users
#   2. Cloudflare anti-bot (cf_clearance, _cfuvid) — set on ANY visitor
#      that passes Cloudflare's challenge, including logged-out
#   3. Function tokens (xsrf) — CSRF token, ambient on logged-in sessions
#      but emitted independently of auth and could appear without it
# Without these in the blacklist, the structural fallback (httpOnly +
# expires>7d + name not in this set) would accept them as auth and
# false-positive a logged-out browser.
MEDIUM_ANONYMOUS_TRACKING_NAMES: frozenset[str] = frozenset({
    "uid",                # legacy anonymous user identifier
    "_ga",                # Google Analytics
    "_gid",               # Google Analytics
    "_gat",               # Google Analytics throttle
    "_dd_s",              # Datadog RUM session
    "g_state",            # Google One Tap state
    "nonce",              # request-scoped nonce
    "pr",                 # preferences
    "sz",                 # screen size
    "tz",                 # timezone
    "optimizelyEndUserId",
    "lightstep_guid",
    "lightstep_guid/medium-web",
    "cf_clearance",       # Cloudflare anti-bot clearance (Spike 3a)
    "_cfuvid",            # Cloudflare visitor id (Spike 3a)
    "xsrf",               # CSRF token, not auth (Spike 3a)
})

# Idle-detection timeouts (Plan 003 R7). Replace Plan 001 driver's
# BIND_TIMEOUT_MS = 5 * 60 * 1000 default for Medium only by polling
# wait_for_url in short windows + tracking nav-event idleness.
#
# Spike 7 verdict (2026-05-19, two attempts against Google SSO + 2FA):
# in BOTH runs the script's ``page.on("framenavigated")`` listener and
# ``page.url`` polling failed to observe the SSO completion — the
# operator successfully authenticated (cookies fully persisted to disk,
# sid + rid + cf_clearance all present, restart goto /me lands at
# /@username) but the listener-bearing Page object was apparently
# orphaned by a popup / SPA cross-origin transition during the SSO
# flow. Conclusion: ``framenavigated`` is NOT a reliable bound signal
# when the login chain crosses origins (Google → Medium). Therefore:
#
#   - The 20-min ``_ABSOLUTE_TIMEOUT_SECONDS`` wall-clock IS the
#     load-bearing safety floor. Operators get full SSO+2FA budget
#     even when nav events go missing entirely.
#   - The 90s ``_IDLE_TIMEOUT_SECONDS`` is a *secondary fast-path* —
#     it shortens the happy case but is never the only ceiling.
#   - URL match against ``_BOUND_URL_PATTERN`` is the *positive*
#     signal that actually fires success; the timers are only there
#     to bound the wait.
#
# Do not raise ``_IDLE_TIMEOUT_SECONDS`` to compensate for missing
# nav events — that just enlarges the silent-failure window. If a
# future driver change makes framenavigated reliable end-to-end
# (e.g. enumerate ctx.pages on each tick), document the new evidence
# here before lowering the absolute timeout.
_IDLE_TIMEOUT_SECONDS = 90.0
_ABSOLUTE_TIMEOUT_SECONDS = 1200.0
_INNER_WAIT_TIMEOUT_MS = 1000  # per-iteration wait_for_url timeout


def _medium_cookie_host_filter(host) -> bool:
    if not host or not isinstance(host, str):
        return False
    return host.lower().lstrip(".") == "medium.com"


def _cookie_sanity_passes(cookies: list[dict[str, Any]]) -> bool:
    """True if any cookie in the list looks like a Medium auth cookie.

    Two acceptance criteria (either passes):
      1. Name in ``MEDIUM_AUTH_COOKIE_WHITELIST`` (Spike 3a output).
      2. Structural fallback: ``httpOnly=True`` AND
         ``expires - now > 7 days`` AND name NOT in
         ``MEDIUM_ANONYMOUS_TRACKING_NAMES``.

    Reject criteria (override): empty cookie list, only short-lived or
    non-HttpOnly cookies, only anonymous-tracking names.
    """
    if not cookies:
        return False
    cutoff = time.time() + 7 * 86400
    for c in cookies:
        if not isinstance(c, dict):
            continue
        name = c.get("name", "")
        if name in MEDIUM_AUTH_COOKIE_WHITELIST:
            return True
        if not c.get("httpOnly"):
            continue
        expires = c.get("expires", -1) or -1
        if expires < cutoff:
            continue
        if name in MEDIUM_ANONYMOUS_TRACKING_NAMES:
            continue
        return True
    return False


_USERNAME_URL_RE = re.compile(r"medium\.com/@([^/?#]+)")


def _scrape_username(page) -> str | None:
    """Three-tier fallback to extract the operator's Medium handle.

    1. DOM ``[data-testid="headerUserIcon"]`` parent's ``href`` attribute
       — most reliable, present on every authenticated page.
    2. ``<meta property="og:url">`` content — Medium emits this on
       profile pages with the canonical ``/@username`` URL.
    3. ``page.url`` regex parse — works when the operator lands directly
       on ``/@username``.

    Returns the lowercase handle (no leading ``@``) or ``None`` if all
    three fail.
    """
    # Tier 1: DOM data-testid
    try:
        el = page.query_selector('[data-testid="headerUserIcon"]')
        if el is not None:
            href = el.get_attribute("href")
            if href:
                m = _USERNAME_URL_RE.search(href)
                if m:
                    return m.group(1).lower()
    except Exception:
        pass
    # Tier 2: og:url meta
    try:
        el = page.query_selector('meta[property="og:url"]')
        if el is not None:
            content = el.get_attribute("content")
            if content:
                m = _USERNAME_URL_RE.search(content)
                if m:
                    return m.group(1).lower()
    except Exception:
        pass
    # Tier 3: URL parse
    try:
        m = _USERNAME_URL_RE.search(page.url or "")
        if m:
            return m.group(1).lower()
    except Exception:
        pass
    return None


def _last_account_path() -> Path:
    """``<config_dir>/medium-last-account.txt`` — the promoted file."""
    return _config_dir() / "medium-last-account.txt"


def _last_account_tentative_path() -> Path:
    """``<config_dir>/medium-last-account.tentative`` — written by the
    predicate before returning success; driver promotes to ``.txt`` after
    ``_persist_storage_state`` succeeds."""
    return _config_dir() / "medium-last-account.tentative"


def _read_last_account() -> str | None:
    """Return the stored last_account handle (lowercase) or ``None``."""
    path = _last_account_path()
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip().lower() or None
    except OSError:
        return None


def _write_last_account_tentative(account: str) -> None:
    """Atomically write the tentative last_account file with mode 0600.

    Uses ``tempfile.mkstemp`` for a random suffix to avoid collisions if
    multiple bind attempts race in the same config dir.
    """
    target = _last_account_tentative_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".medium-last-account.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(account + "\n", encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def _medium_bound_predicate(page) -> None:
    """Plan 003 Unit 1 predicate. See module docstring for the full flow.

    Raises:
        IdentityMismatch: scraped @username differs from stored
            last_account. Driver maps to BindResult(error_code=
            'identity_mismatch', extras=...).
        BoundPredicateTimeout: idle (90s no nav since *first* observed nav)
            OR absolute (1200s wall) timeout exceeded.
    """
    # Lazy-import Playwright's TimeoutError so the recipe module is
    # importable in non-Playwright contexts (e.g., webui process that
    # only needs ChannelRecipe metadata).
    from playwright.sync_api import TimeoutError as PWTimeoutError

    from backlink_publisher.cli._bind.driver import BoundPredicateTimeout

    started_at = time.monotonic()
    # last_nav_at is None until the first ``framenavigated`` event lands.
    # Idle-timeout enforcement is gated on this — see Spike 7 verdict
    # in the module docstring: in cross-origin SSO scenarios the listener
    # may never observe a single nav event despite a successful login.
    # If we treated "no nav events" as 90s of idleness, the predicate
    # would raise long before the absolute floor the docstring promises.
    last_nav_at: list[float | None] = [None]

    def _on_nav(_frame) -> None:
        last_nav_at[0] = time.monotonic()

    page.on("framenavigated", _on_nav)

    while True:
        now = time.monotonic()
        if (
            last_nav_at[0] is not None
            and now - last_nav_at[0] > _IDLE_TIMEOUT_SECONDS
        ):
            raise BoundPredicateTimeout()
        if now - started_at > _ABSOLUTE_TIMEOUT_SECONDS:
            raise BoundPredicateTimeout()
        try:
            page.wait_for_url(
                _BOUND_URL_PATTERN, timeout=_INNER_WAIT_TIMEOUT_MS
            )
        except PWTimeoutError:
            # URL didn't transition within the inner window; loop and
            # re-check idle / absolute timers.
            continue

        # URL transitioned off /m/signin. Verify cookies look like a real
        # authenticated session before declaring success.
        try:
            cookies = page.context.cookies("https://medium.com")
        except Exception:
            cookies = []
        if not _cookie_sanity_passes(cookies):
            # URL fluke (e.g., user clicked Medium logo while not logged
            # in and landed on /policy/terms) — keep waiting.
            continue

        # Cookies look authentic. Scrape @username.
        username = _scrape_username(page)
        if username is None:
            # Can't determine identity — refuse to commit. This is a
            # rare case; the driver maps it to a timeout-class failure
            # on the next iteration when idle elapses.
            continue

        # Identity-mismatch guard (Plan 003 R6).
        last = _read_last_account()
        if last is not None and last != username:
            raise IdentityMismatch(old_account=last, new_account=username)

        # First-bind or same-account rebind: record the tentative file;
        # driver promotes to the final path after persisting storage_state.
        _write_last_account_tentative(username)
        return  # predicate success


RECIPE = ChannelRecipe(
    login_url=_LOGIN_URL,
    bound_predicate=_medium_bound_predicate,
    cookie_host_filter=_medium_cookie_host_filter,
)
