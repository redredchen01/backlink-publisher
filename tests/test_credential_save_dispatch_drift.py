"""Drift guard for the credential-save dispatch cluster (Plan 2026-05-27-008, U3).

Six per-platform maps decide how the WebUI persists a channel's credentials,
none previously guarded against the #253 registration-drift class (a removed /
renamed platform leaving a stale key, or a key whose ``auth_type`` silently
drifted away from what its handler assumes):

  - ``token_paste._ALLOWED``                     (single-token-paste route)
  - ``channel_bind_save._TOKEN_DISPATCH``        (auth_type "token")
  - ``channel_bind_save._TOKEN_FIELDS_DISPATCH`` (auth_type "token_fields")
  - ``channel_bind_save._PASTE_BLOB_CHANNELS``   (auth_type "paste_blob")
  - ``channel_bind_save._USERPASS_MODULES``      (auth_type "userpass")
  - ``channel_bind_save._SKIP_CHANNELS``         (handled by dedicated routes)

Authority is SUBSET, not equality. A bucket member may legitimately be absent
from these maps — config-file-only channels (``hashnode``), dedicated routes
(``ghpages``/``devto``/``notion``), pending UI (``tumblr``), or non-token auth
(anon / oauth / live_browser). So the guard asserts every *key present* is a
registered, active platform whose ``auth_type`` matches the handler that
consumes it. It does NOT require every bucket member to be wired (that is a UI
completeness concern, not registration drift).

Test-time only, registry populated by importing adapters — never an import-time
assert (``invert-drift-check-when-invariant-becomes-dynamic``).
"""

import backlink_publisher.publishing.adapters  # noqa: F401 — trigger registration

import pytest

from backlink_publisher.publishing.registry import (
    active_platforms,
    platforms_by_auth_type,
)
from webui_app.routes.channel_bind_save import (
    _PASTE_BLOB_CHANNELS,
    _SKIP_CHANNELS,
    _TOKEN_DISPATCH,
    _TOKEN_FIELDS_DISPATCH,
    _USERPASS_MODULES,
)
from webui_app.routes.token_paste import _ALLOWED


def _allowed_for(*auth_types: str) -> frozenset[str]:
    """Union of the active-platform buckets for the given auth_types."""
    out: set[str] = set()
    for t in auth_types:
        out |= platforms_by_auth_type(t)
    return frozenset(out)


# (map name, its keys, the auth_type bucket(s) every key must belong to).
# ``_ALLOWED`` spans two buckets because the single-token-paste route serves
# both a "token" channel (devto) and a "token_fields" channel (ghpages).
_DISPATCH_CASES = [
    ("token_paste._ALLOWED", set(_ALLOWED), ("token", "token_fields")),
    ("_TOKEN_DISPATCH", set(_TOKEN_DISPATCH), ("token",)),
    ("_TOKEN_FIELDS_DISPATCH", set(_TOKEN_FIELDS_DISPATCH), ("token_fields",)),
    ("_PASTE_BLOB_CHANNELS", set(_PASTE_BLOB_CHANNELS), ("paste_blob",)),
    ("_USERPASS_MODULES", set(_USERPASS_MODULES), ("userpass",)),
]


@pytest.mark.parametrize("name,keys,buckets", _DISPATCH_CASES, ids=lambda v: v if isinstance(v, str) else "")
def test_dispatch_keys_are_registered_active_and_correct_auth_type(name, keys, buckets):
    """Every key in each typed save-dispatch map is a registered, active
    platform whose auth_type matches the handler's bucket."""
    allowed = _allowed_for(*buckets)
    stale = keys - allowed
    assert not stale, (
        f"{name} has keys {sorted(stale)} that are not active platforms of "
        f"auth_type {buckets} — a removed/renamed platform left a stale entry, "
        f"or its auth_type drifted. Allowed: {sorted(allowed)}"
    )


def test_skip_channels_are_all_registered_active():
    """_SKIP_CHANNELS names channels routed to dedicated save endpoints; each
    must still be a registered, active platform (else the skip is stale)."""
    stale = set(_SKIP_CHANNELS) - set(active_platforms())
    assert not stale, (
        f"_SKIP_CHANNELS has stale entries {sorted(stale)} no longer registered/active"
    )


def test_subset_check_flags_a_planted_unregistered_key():
    """R7 red->green honesty: the subset check the guard relies on must FAIL on
    a planted unregistered/wrong-bucket key, so it is not tautological."""
    paste_blob = platforms_by_auth_type("paste_blob")
    planted = set(_PASTE_BLOB_CHANNELS) | {"not_a_real_platform"}
    assert planted - paste_blob == {"not_a_real_platform"}
    # And a wrong-bucket key (a real platform of the wrong auth_type) is caught:
    assert ({"livejournal"} - paste_blob) == {"livejournal"}
