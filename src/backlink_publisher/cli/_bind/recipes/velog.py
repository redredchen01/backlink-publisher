"""Velog binding recipe — Plan 2026-05-19-001 Unit 2.

Channel: ``velog`` (velog.io).

Login flow: operator visits ``https://velog.io/`` (the homepage's "로그인"
button kicks off the social-OAuth dance with Google / GitHub / Facebook).
The bound predicate waits for the URL to settle on any velog.io path that
is **not** ``/auth*`` — that signals the social provider redirected back
to a logged-in session.

Cookie host filter: exact-apex match against ``velog.io``. Mirrors the
spike's ``_velog_host_allowed`` primitive (plan-012 R16) to guard against
prefix-confusion (``evilvelog.io``) and suffix-confusion
(``velog.io.attacker.tld``). Subdomains are explicitly rejected — the
session cookie lives on the apex.
"""

from __future__ import annotations

import re

from . import ChannelRecipe


_LOGIN_URL = "https://velog.io/"

# URL pattern that signals "user is logged in" — any velog.io page that isn't
# the login route. The driver passes this to Playwright's wait_for_url.
_BOUND_URL_PATTERN = re.compile(r"https?://(?:[^/]*\.)?velog\.io/(?!auth)(?:.*)?$")


def _velog_bound_predicate(page) -> None:
    """Wait until the page navigates away from /auth — signals login completed.

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
