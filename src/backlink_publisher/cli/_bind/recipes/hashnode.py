"""Hashnode binding recipe — Plan 2026-05-20-016 Unit 1 SPIKE STUB.

Channel: ``hashnode`` (hashnode.com + ``*.hashnode.dev`` subdomains).

**SPIKE STATUS**: This recipe is intentionally minimal so the operator can
run ``bind-channel --channel hashnode`` during Unit 1 spike to capture
real cookies + bound URL + selectors. Unit 2 will replace this with a
production recipe using the spike outputs (per-channel whitelist /
blacklist cookie sanity gate, identity-mismatch guard, post_persist
cookies-only hardcut).

Spike-time choices (deliberately loose, **not** production-grade):

  - ``login_url = https://hashnode.com/onboard`` — landing page that
    surfaces both Google / GitHub SSO buttons and email signin.
  - ``bound_predicate`` — matches any ``hashnode.com`` (apex, excluding
    /auth, /login, /signup, /onboard) OR any ``*.hashnode.dev``
    subdomain URL. Post-login, Hashnode redirects to either
    ``hashnode.com/<username>`` or ``<username>.hashnode.dev``; both
    must satisfy.
  - ``cookie_host_filter`` — accepts apex ``hashnode.com`` AND any
    ``*.hashnode.dev`` subdomain. The spike collects ALL cookies on
    both surfaces so we can diff logged-in vs logged-out and design the
    Unit 2 sanity gate.
  - No ``post_persist`` — raw ``storage_state.json`` lands in
    ``<config_dir>/hashnode-storage-state.json`` (0600). The operator
    runs ``docs/spike-notes/2026-05-20-hashnode-bind-inspector.py`` on
    that file to dump the cookie table.

Once Unit 1 spike-notes file enumerates real cookies + URL pattern,
Unit 2 hardens this recipe to production grade with cookies-only
hardcut + 3-layer sanity gate + identity guard.
"""

from __future__ import annotations

import re

from . import ChannelRecipe


_LOGIN_URL = "https://hashnode.com/onboard"

# Spike: lax pattern. Accepts apex hashnode.com (not on login family)
# OR any *.hashnode.dev subdomain. Production recipe will tighten this
# once Unit 1 captures the actual post-login landing URL distribution.
_BOUND_URL_PATTERN = re.compile(
    r"^https://(?:hashnode\.com(?:/(?!(?:auth|login|signup|onboard))[^?#]*)?"
    r"|[^./]+\.hashnode\.dev(?:/[^?#]*)?)(?:[?#].*)?$"
)


def _hashnode_bound_predicate(page) -> None:
    """SPIKE PATCH (2026-05-21): no-op — operator already completed login in
    prior chrome-backend bind attempt (session cookies on disk). Telegraph-
    style immediate persist. Production version (Unit 2) will reintroduce
    URL pattern + cookie sanity gate + identity-mismatch guard."""
    return None


def _hashnode_cookie_host_filter(host) -> bool:
    """Spike-lax: accept apex hashnode.com OR any *.hashnode.dev subdomain.

    Production (Unit 2) will narrow based on which cookies the spike
    finds carry auth (likely apex only) vs which carry content/CDN state
    (likely subdomain). For spike we capture everything to enable diff.
    """
    if not host or not isinstance(host, str):
        return False
    h = host.lower().lstrip(".")
    return h == "hashnode.com" or h.endswith(".hashnode.dev")


RECIPE = ChannelRecipe(
    login_url=_LOGIN_URL,
    bound_predicate=_hashnode_bound_predicate,
    cookie_host_filter=_hashnode_cookie_host_filter,
)
