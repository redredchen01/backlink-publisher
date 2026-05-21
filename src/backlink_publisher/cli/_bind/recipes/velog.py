"""Velog binding recipe — Plan 2026-05-19-001 Unit 2.

Channel: ``velog`` (velog.io).

Login flow: operator lands on ``https://velog.io/setting`` so the login
button is visible immediately. The bound predicate waits until the page is no
longer the login gate and no longer shows the login prompt. That signals the
social provider redirected back to a logged-in session.

Cookie host filter: exact-apex match against ``velog.io``. Mirrors the
spike's ``_velog_host_allowed`` primitive (plan-012 R16) to guard against
prefix-confusion (``evilvelog.io``) and suffix-confusion
(``velog.io.attacker.tld``). Subdomains are explicitly rejected — the
session cookie lives on the apex.
"""

from __future__ import annotations

import re

from . import ChannelRecipe


_LOGIN_URL = "https://velog.io/setting"

# Login gate / state page that appears when the operator is not signed in.
_BOUND_URL_PATTERN = re.compile(r"https://velog\.io/setting(?:[/?#].*)?$")

# Velog's logged-out page renders the string below. We treat its disappearance
# as the bind signal because the site can keep the same URL while swapping the
# page contents after authentication.
_LOGIN_PROMPT_TEXT = "로그인 후 이용해주세요"


def _velog_bound_predicate(page) -> None:
    """Wait until the settings gate is no longer showing the login prompt.

    ``page`` is a Playwright ``Page``; we use the sync API (matches medium_browser
    convention in this repo). Default timeout is governed by the driver's
    ``BIND_TIMEOUT_MS``; a timeout here raises ``PlaywrightTimeoutError`` which
    the driver translates to ``error_code="bound_predicate_timeout"``.
    """
    page.wait_for_url(_BOUND_URL_PATTERN)
    page.wait_for_function(
        """(prompt) => {
            const text = document.body ? document.body.innerText || '' : '';
            return !text.includes(prompt);
        }""",
        arg=_LOGIN_PROMPT_TEXT,
    )


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
