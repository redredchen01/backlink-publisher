"""Registry-driven binding surface coverage guard (Plan 2026-05-26-002 Unit 7).

Drift sentinel: every registered platform must have a declared auth_type, and
every auth_type entry must correspond to a registered platform. Adding a new
platform to the adapter registry without a matching auth_type entry (or vice
versa) fails loudly here.

Bespoke-flow channels (blogger/velog/medium/mastodon) have auth_types that
map to custom binding UI; they are NOT flagged as missing — they appear in
_AUTH_TYPE_BY_PLATFORM and are handled by their own partials.
"""

from __future__ import annotations

import pytest

import backlink_publisher.publishing.adapters  # noqa: F401 — side-effect: populates registry
from backlink_publisher.publishing.registry import (
    _AUTH_TYPE_BY_PLATFORM,
    _AUTH_TYPE_VALUES,
    active_platforms,
    auth_type,
    registered_platforms,
)


class TestAuthTypeCoverage:
    """Every registered platform must be classified."""

    def test_all_active_platforms_have_auth_type(self) -> None:
        """No active platform may have auth_type=None.

        Failure here means a new adapter was registered as active without a
        matching entry in registry._AUTH_TYPE_BY_PLATFORM.  Add the entry
        before promoting the platform from experimental to active.

        Experimental platforms (visibility='experimental') are exempt — they
        may be in-progress and not yet fully classified.
        """
        missing = [p for p in active_platforms() if auth_type(p) is None]
        assert not missing, (
            f"Active platforms with no auth_type classification: {missing}. "
            "Add each to registry._AUTH_TYPE_BY_PLATFORM before shipping."
        )

    def test_no_stale_auth_type_entries(self) -> None:
        """Every auth_type entry must point to a registered platform.

        Failure here means a platform was removed from the adapter registry
        without pruning its auth_type entry.  Remove the stale key from
        registry._AUTH_TYPE_BY_PLATFORM.
        """
        registered = frozenset(registered_platforms())
        stale = [k for k in _AUTH_TYPE_BY_PLATFORM if k not in registered]
        assert not stale, (
            f"Stale auth_type entries for unregistered platforms: {stale}. "
            "Remove from registry._AUTH_TYPE_BY_PLATFORM."
        )

    def test_auth_type_values_are_valid(self) -> None:
        """All assigned auth_types must be in the known value set."""
        invalid = {
            platform: atype
            for platform, atype in _AUTH_TYPE_BY_PLATFORM.items()
            if atype not in _AUTH_TYPE_VALUES
        }
        assert not invalid, (
            f"Platforms with unrecognised auth_type values: {invalid}. "
            f"Valid values: {sorted(_AUTH_TYPE_VALUES)}"
        )


class TestAuthTypeDriftSentinel:
    """Demonstrates that unclassified registrations fail the coverage test.

    This test intentionally registers a fake platform without an auth_type
    entry to prove the guard above is not vacuous.
    """

    def test_unclassified_platform_caught_by_coverage_guard(
        self, fake_platform_registered
    ) -> None:
        """'fake' platform has no auth_type — the coverage guard would flag it.

        fake_platform_registered registers with default visibility='active', so
        it appears in active_platforms() and would trigger the coverage guard.
        """
        assert auth_type("fake") is None, (
            "Precondition: fake_platform_registered does not add an auth_type entry."
        )
        unclassified = [p for p in active_platforms() if auth_type(p) is None]
        assert "fake" in unclassified, (
            "Coverage guard should have caught 'fake' as unclassified."
        )


class TestHiddenFromUIDriftGuard:
    """HIDDEN_FROM_UI count must stay consistent with drift-sensitive callers."""

    def test_hidden_from_ui_is_subset_of_registered(self) -> None:
        """Every hidden platform must still be registered.

        If a platform is both hidden and unregistered, something was only
        half-removed.
        """
        from webui_app.binding_status import hidden_from_ui

        registered = frozenset(registered_platforms())
        hidden = hidden_from_ui()
        orphans = hidden - registered
        assert not orphans, (
            f"Platforms in HIDDEN_FROM_UI but not registered: {orphans}. "
            "Either re-register or remove from the hidden list."
        )

    def test_visible_active_platforms_all_have_auth_type(self) -> None:
        """Every visible active platform must have an auth_type.

        Hidden (retired) platforms and experimental platforms may lack
        auth_type; visible active ones must always have one.
        """
        from webui_app.binding_status import hidden_from_ui

        hidden = hidden_from_ui()
        visible_without_auth = [
            p for p in active_platforms()
            if p not in hidden and auth_type(p) is None
        ]
        assert not visible_without_auth, (
            f"Visible active platforms with no auth_type: {visible_without_auth}."
        )
