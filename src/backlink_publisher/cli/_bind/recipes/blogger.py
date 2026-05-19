"""Blogger binding recipe — Plan 2026-05-19-001 Unit 2.

Channel: ``blogger`` (blogger.com via accounts.google.com OAuth).

Login flow: operator visits ``https://www.blogger.com/`` → redirects to
``accounts.google.com`` for OAuth → on success, redirects back to
``blogger.com``. The bound predicate waits for the URL to settle on a
blogger.com path (signals the OAuth round-trip completed).

Cookie host filter accepts both ``blogger.com`` (Blogger session cookies)
and ``google.com`` family (OAuth session cookies). The ``accounts.google.com``
subdomain is explicitly allowed since the OAuth tokens live there.

This recipe does NOT replace the existing OAuth refresh-token flow in
``blogger_api`` — see Plan 2026-05-19-001 Key Technical Decisions.
Binding produces a parallel ``storage_state.json`` sentinel that
``reconcile_on_load`` watches; ``blogger-token.json`` continues to be
the source of truth for ``BloggerAPIAdapter.publish``.
"""

from __future__ import annotations

import re

from . import ChannelRecipe


_LOGIN_URL = "https://www.blogger.com/"

_BOUND_URL_PATTERN = re.compile(r"https?://(?:www\.)?blogger\.com/(?:.*)?$")


def _blogger_bound_predicate(page) -> None:
    page.wait_for_url(_BOUND_URL_PATTERN)


def _blogger_cookie_host_filter(host) -> bool:
    """Accept blogger.com and the google.com OAuth family.

    Allowed shapes:
      - blogger.com / .blogger.com
      - google.com / .google.com
      - accounts.google.com (subdomain of google.com — OAuth flow lives here)

    Rejected:
      - prefix-confusion (``evilgoogle.com``, ``evilblogger.com``)
      - suffix-confusion (``google.com.attacker.tld``, ``blogger.com.attacker.tld``)
      - unrelated hosts (``evil.test``)
    """
    if not host or not isinstance(host, str):
        return False
    normalized = host.lower().lstrip(".")
    # Exact-apex matches
    if normalized in ("blogger.com", "google.com"):
        return True
    # google.com subdomain (e.g. accounts.google.com)
    if normalized.endswith(".google.com"):
        return True
    # blogger.com subdomain (e.g. www.blogger.com)
    if normalized.endswith(".blogger.com"):
        return True
    return False


RECIPE = ChannelRecipe(
    login_url=_LOGIN_URL,
    bound_predicate=_blogger_bound_predicate,
    cookie_host_filter=_blogger_cookie_host_filter,
)
