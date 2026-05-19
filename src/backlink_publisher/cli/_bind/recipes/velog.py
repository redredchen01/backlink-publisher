"""Velog binding recipe — Plan 2026-05-19-001 Unit 2.

Channel: ``velog`` (velog.io).

Login flow: operator lands on ``https://velog.io/login`` so the social-OAuth
buttons (Google / GitHub / Facebook) are visible immediately. The bound
predicate waits for the URL to leave the login, signup, and OAuth-callback
routes — that signals the social provider redirected back to a logged-in
session (usually the home feed or ``/@<username>``).

Cookie host filter: exact-apex match against ``velog.io``. Mirrors the
spike's ``_velog_host_allowed`` primitive (plan-012 R16) to guard against
prefix-confusion (``evilvelog.io``) and suffix-confusion
(``velog.io.attacker.tld``). Subdomains are explicitly rejected — the
session cookie lives on the apex.
"""

from __future__ import annotations

import re

from . import ChannelRecipe


_LOGIN_URL = "https://velog.io/login"

# URL pattern that signals "user is logged in" — an apex ``velog.io``
# page that isn't the login, signup, or OAuth-callback route. The driver
# passes this to Playwright's ``wait_for_url``. Two load-bearing pieces:
#
# 1. Excluding ``login`` / ``signup`` / ``auth`` — without these, the
#    very URL the driver navigates to (``_LOGIN_URL = .../login``) or
#    velog's own ``/auth/callback`` intermediate satisfies the predicate
#    before the operator has authenticated.
#
# 2. Apex-only — no ``*.velog.io`` subdomain wildcard. The OAuth dance
#    transits ``v3.velog.io/api/auth/v3/social/redirect/<provider>``
#    *before* the social provider login completes. A subdomain wildcard
#    would treat that intermediate redirect as success and persist a
#    storage state with no apex session cookie. Mirrors the strict
#    exact-apex contract enforced by ``_velog_cookie_host_filter``.
_BOUND_URL_PATTERN = re.compile(
    r"https://velog\.io/(?!(?:auth|login|signup))(?:.*)?$"
)


def _velog_bound_predicate(page) -> None:
    """Wait until the page leaves /login, /signup, and /auth — login completed.

    ``page`` is a Playwright ``Page``; we use the sync API (matches medium_browser
    convention in this repo). Default timeout is governed by the driver's
    ``BIND_TIMEOUT_MS``; a timeout here raises ``PlaywrightTimeoutError`` which
    the driver translates to ``error_code="bound_predicate_timeout"``.
    """
    page.wait_for_url(_BOUND_URL_PATTERN)


def _velog_cookie_host_filter(host) -> bool:
    """Exact-apex match: ``host.lower().lstrip('.') == 'velog.io'``."""
    if not host or not isinstance(host, str):
        return False
    return host.lower().lstrip(".") == "velog.io"


RECIPE = ChannelRecipe(
    login_url=_LOGIN_URL,
    bound_predicate=_velog_bound_predicate,
    cookie_host_filter=_velog_cookie_host_filter,
)
