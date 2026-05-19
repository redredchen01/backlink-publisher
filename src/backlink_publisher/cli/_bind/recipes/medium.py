"""Medium binding recipe — Plan 2026-05-19-001 Unit 2.

Channel: ``medium`` (medium.com).

Login flow: operator visits ``https://medium.com/m/signin``; once authed,
medium.com redirects to ``/`` (or a path that isn't ``/m/signin*``). The
bound predicate waits for that transition.

Cookie host filter: exact-apex match against ``medium.com``. Subdomains
not accepted (the auth cookie lives on the apex).

This recipe does NOT replace the existing Medium integration-token /
OAuth flow in ``medium_api`` — see Plan 2026-05-19-001 Key Technical
Decisions. Binding produces a parallel ``storage_state.json`` sentinel
that ``reconcile_on_load`` watches; ``medium-token.json`` continues to
be the source of truth for ``MediumAPIAdapter.publish``.
"""

from __future__ import annotations

import re

from . import ChannelRecipe


_LOGIN_URL = "https://medium.com/m/signin"

_BOUND_URL_PATTERN = re.compile(r"https?://(?:[^/]*\.)?medium\.com/(?!m/signin)(?:.*)?$")


def _medium_bound_predicate(page) -> None:
    page.wait_for_url(_BOUND_URL_PATTERN)


def _medium_cookie_host_filter(host) -> bool:
    if not host or not isinstance(host, str):
        return False
    return host.lower().lstrip(".") == "medium.com"


RECIPE = ChannelRecipe(
    login_url=_LOGIN_URL,
    bound_predicate=_medium_bound_predicate,
    cookie_host_filter=_medium_cookie_host_filter,
)
