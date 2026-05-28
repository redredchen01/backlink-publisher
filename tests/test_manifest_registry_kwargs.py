"""Plan 2026-05-25-002 Unit 1 — register() manifest kwargs + helpers.

Test scenarios from the plan, organised by category:

- Happy path: legacy register() (no new kwargs) still works; helpers
  return defaults.
- Happy path: register() with all 4 manifest kwargs round-trips through
  the 6 helpers.
- Edge case: bind=[non-BindDescriptor] raises RegistryError.
- Edge case: visibility="bogus" raises RegistryError.
- Edge case: bind=[BindDescriptor(backend="not-a-backend")] raises.
- Edge case: ui=not-UiMeta / policy=not-Policy raises.
- Integration: active_platforms() excludes hidden + retired; includes
  experimental and active.
- Integration: bound_platforms(cfg, is_bound) composes active + bound
  predicate.
- Integration: re-register clears prior manifest state (matches the
  rationale/referral_value pop-on-None pattern).
- Legacy: legacy_platforms() counts platforms with no manifest kwargs.
"""

from __future__ import annotations

from typing import Any

import pytest

from backlink_publisher._util.errors import RegistryError
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.registry import (
    Publisher,
    _BIND_BY_PLATFORM,
    _POLICY_BY_PLATFORM,
    _REGISTRY,
    _REJECTED_PLATFORMS,
    _UI_META_BY_PLATFORM,
    _VISIBILITY_BY_PLATFORM,
    active_platforms,
    bind_descriptors,
    bound_platforms,
    legacy_platforms,
    policy,
    register,
    ui_meta,
    visibility,
)
from backlink_publisher.publishing._manifest_types import (
    BindDescriptor,
    Policy,
    UiMeta,
)


class _FakeAdapter(Publisher):
    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Any,
    ) -> AdapterResult:  # pragma: no cover - never invoked
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _snapshot_registry():
    """Snapshot+restore registry state so red-path tests don't
    bleed into the rest of the suite.
    
    Note: Manifest fields (ui, bind, policy, visibility) are now stored
    as fields in RegistryEntry within _REGISTRY, so only _REGISTRY and 
    _REJECTED_PLATFORMS need to be snapshotted.
    """
    reg = dict(_REGISTRY)
    rej = dict(_REJECTED_PLATFORMS)
    try:
        yield
    finally:
        for store, snap in (
            (_REGISTRY, reg),
            (_REJECTED_PLATFORMS, rej),
        ):
            store.clear()
            store.update(snap)


class TestLegacyRegistration:
    """register() without manifest kwargs preserves pre-Unit-1 behaviour."""

    def test_register_without_manifest_kwargs_sets_defaults(self) -> None:
        register("legacy_one", _FakeAdapter, dofollow=True)
        assert ui_meta("legacy_one") is None
        assert bind_descriptors("legacy_one") == ()
        assert policy("legacy_one") is None
        assert visibility("legacy_one") == "active"

    def test_helpers_for_unregistered_platform_return_safe_defaults(
        self,
    ) -> None:
        assert ui_meta("never_registered") is None
        assert bind_descriptors("never_registered") == ()
        assert policy("never_registered") is None
        # Unregistered platform default visibility — load-bearing for
        # Unit 2 reverse-lookup wiring (HIDDEN_FROM_UI default).
        assert visibility("never_registered") == "active"


class TestFullManifestRoundTrip:
    """register() with all 4 manifest kwargs round-trips through helpers."""

    def test_all_four_kwargs_round_trip(self) -> None:
        ui = UiMeta(
            display_name="Foo Blog",
            domain="foo.example",
            category="dev-blog",
            icon="bi-globe2",
        )
        bind = [
            BindDescriptor(
                backend="cookie",
                storage_state_path="<config_dir>/foo/storage-state.json",
                login_endpoint="/api/foo/login",
                card_template="_foo_card.html",
                extras={"recipe": "browser_publish.recipes.foo"},
            ),
            BindDescriptor(backend="token-paste", card_template="_foo_token.html"),
        ]
        pol = Policy(
            throttle_band=(30, 120),
            env_keys={"min": "FOO_THROTTLE_MIN", "max": "FOO_THROTTLE_MAX"},
            retry_id="default",
            liveness_probe_sec=900,
            language_whitelist=("ko", "en"),
        )
        register(
            "foo_full",
            _FakeAdapter,
            dofollow=True,
            ui=ui,
            bind=bind,
            policy=pol,
            visibility="experimental",
        )
        assert ui_meta("foo_full") == ui
        descriptors = bind_descriptors("foo_full")
        assert descriptors == tuple(bind)
        assert descriptors[0].backend == "cookie"
        assert descriptors[1].backend == "token-paste"
        assert policy("foo_full") == pol
        assert visibility("foo_full") == "experimental"


class TestValidationErrors:
    """Edge cases that must raise RegistryError, not silently store."""

    def test_visibility_bogus_value_raises(self) -> None:
        with pytest.raises(RegistryError, match="visibility must be one of"):
            register(
                "foo_vis",
                _FakeAdapter,
                dofollow=True,
                visibility="bogus",  # type: ignore[arg-type]
            )

    def test_bind_entry_not_dataclass_raises(self) -> None:
        with pytest.raises(RegistryError, match="expected BindDescriptor"):
            register(
                "foo_bind_dict",
                _FakeAdapter,
                dofollow=True,
                bind=[{"backend": "cookie"}],  # type: ignore[list-item]
            )

    def test_bind_entry_with_bad_backend_raises(self) -> None:
        with pytest.raises(RegistryError, match="backend="):
            register(
                "foo_bind_backend",
                _FakeAdapter,
                dofollow=True,
                bind=[BindDescriptor(backend="ftp")],  # type: ignore[arg-type]
            )

    def test_ui_not_uimeta_raises(self) -> None:
        with pytest.raises(RegistryError, match="expected UiMeta"):
            register(
                "foo_ui",
                _FakeAdapter,
                dofollow=True,
                ui={"display_name": "Foo"},  # type: ignore[arg-type]
            )

    def test_policy_not_policy_raises(self) -> None:
        with pytest.raises(RegistryError, match="expected Policy"):
            register(
                "foo_policy",
                _FakeAdapter,
                dofollow=True,
                policy={"throttle_band": (1, 2)},  # type: ignore[arg-type]
            )


class TestVisibilityFiltering:
    """active_platforms() and visibility() interaction across the 4 states."""

    def test_active_platforms_excludes_hidden_and_retired(self) -> None:
        register("vis_active", _FakeAdapter, dofollow=True)
        register(
            "vis_experimental",
            _FakeAdapter,
            dofollow=True,
            visibility="experimental",
        )
        register("vis_hidden", _FakeAdapter, dofollow=True, visibility="hidden")
        register("vis_retired", _FakeAdapter, dofollow=True, visibility="retired")
        active = active_platforms()
        assert "vis_active" in active
        # Experimental is NOT auto-active per plan — operators opt in
        # via --include-experimental. active_platforms() returns the
        # default user-facing list.
        assert "vis_experimental" not in active
        assert "vis_hidden" not in active
        assert "vis_retired" not in active

    def test_re_register_clears_visibility_back_to_default(self) -> None:
        register("vis_swap", _FakeAdapter, dofollow=True, visibility="hidden")
        assert visibility("vis_swap") == "hidden"
        # Re-register without visibility= must reset to default "active",
        # mirroring the rationale/referral_value pop-on-None pattern.
        register("vis_swap", _FakeAdapter, dofollow=True)
        assert visibility("vis_swap") == "active"
        assert _VISIBILITY_BY_PLATFORM.get("vis_swap") is None

    def test_re_register_clears_manifest_dicts(self) -> None:
        register(
            "manifest_swap",
            _FakeAdapter,
            dofollow=True,
            ui=UiMeta(display_name="X", domain="x.example", category="c"),
            bind=[BindDescriptor(backend="cookie")],
            policy=Policy(throttle_band=(1, 2)),
        )
        assert ui_meta("manifest_swap") is not None
        assert bind_descriptors("manifest_swap") != ()
        assert policy("manifest_swap") is not None
        register("manifest_swap", _FakeAdapter, dofollow=True)
        assert ui_meta("manifest_swap") is None
        assert bind_descriptors("manifest_swap") == ()
        assert policy("manifest_swap") is None


class TestBoundPlatforms:
    """bound_platforms() composes active filter + injected is_bound predicate."""

    def test_bound_platforms_intersects_active_and_bound(self) -> None:
        register("bp_a", _FakeAdapter, dofollow=True)
        register("bp_b", _FakeAdapter, dofollow=True)
        register("bp_c", _FakeAdapter, dofollow=True, visibility="hidden")

        bound_set = {"bp_a", "bp_c"}  # bp_c is hidden -> filtered out

        def is_bound(cfg: Any, name: str) -> bool:
            return name in bound_set

        result = bound_platforms(object(), is_bound)
        assert "bp_a" in result
        assert "bp_b" not in result  # active but not bound
        assert "bp_c" not in result  # bound but hidden

    def test_bound_platforms_empty_when_no_predicate_match(self) -> None:
        register("bp_only", _FakeAdapter, dofollow=True)
        result = bound_platforms(object(), lambda cfg, name: False)
        assert "bp_only" not in result


class TestLegacyPlatformsCount:
    """legacy_platforms() lists platforms with no manifest metadata at all."""

    def test_legacy_when_no_manifest_kwargs(self) -> None:
        register("legacy_x", _FakeAdapter, dofollow=True)
        assert "legacy_x" in legacy_platforms()

    def test_not_legacy_when_any_manifest_kwarg_supplied(self) -> None:
        register(
            "manifest_x",
            _FakeAdapter,
            dofollow=True,
            ui=UiMeta(display_name="X", domain="x.example", category="c"),
        )
        assert "manifest_x" not in legacy_platforms()

    def test_visibility_alone_does_not_make_platform_non_legacy(self) -> None:
        # visibility is excluded from the legacy criterion — pre-manifest
        # platforms already have implicit visibility="active", so checking
        # only ui/bind/policy is what distinguishes "has manifest metadata"
        # from "still legacy".
        register("legacy_v", _FakeAdapter, dofollow=True, visibility="hidden")
        assert "legacy_v" in legacy_platforms()
