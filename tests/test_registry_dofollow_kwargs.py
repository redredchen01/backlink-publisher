"""Unit 2: register() signature extension + rationale validation.

Plan 2026-05-20-009 §Unit 2.
"""

from __future__ import annotations

from typing import Any

import pytest

from backlink_publisher._util.errors import RegistryError
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.registry import (
    Publisher,
    _REGISTRY,
    _REJECTED_PLATFORMS,
    _DOFOLLOW_BY_PLATFORM,
    _RATIONALE_BY_PLATFORM,
    _REFERRAL_VALUE_BY_PLATFORM,
    _UI_META_BY_PLATFORM,
    _BIND_BY_PLATFORM,
    _POLICY_BY_PLATFORM,
    _VISIBILITY_BY_PLATFORM,
    dofollow_rationale,
    dofollow_status,
    referral_value,
    register,
)


class FakeAdapter(Publisher):
    """Minimal Publisher stub for kwarg-validation tests."""

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Any,
    ) -> AdapterResult:  # pragma: no cover - never invoked in U2 tests
        raise NotImplementedError


# ``RATIONALE_PAD`` is exactly 80 chars after strip — minimum legal length
# per R3 / R10. Tests at the boundary use this; tests below the boundary
# use ``"too short"``.
RATIONALE_PAD = "x" * 80


@pytest.fixture(autouse=True)
def _snapshot_registry():
    """Snapshot + restore all three registry dicts around each test.

    The conftest ``fake_platform_registered`` fixture only saves the
    ``"fake"`` key of ``_REGISTRY``. U2 introduces two new dicts; tests
    that exercise validation must restore all three to avoid leaking
    state into the rest of the suite.
    """
    reg_snap = {k: list(v) for k, v in _REGISTRY.items()}
    df_snap = dict(_DOFOLLOW_BY_PLATFORM)
    rat_snap = dict(_RATIONALE_BY_PLATFORM)
    ref_snap = dict(_REFERRAL_VALUE_BY_PLATFORM)
    rej_snap = dict(_REJECTED_PLATFORMS)
    # Plan 2026-05-25-002 Unit 1 — snapshot manifest dicts.
    ui_snap = dict(_UI_META_BY_PLATFORM)
    bind_snap = dict(_BIND_BY_PLATFORM)
    pol_snap = dict(_POLICY_BY_PLATFORM)
    vis_snap = dict(_VISIBILITY_BY_PLATFORM)
    try:
        yield
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(reg_snap)
        _DOFOLLOW_BY_PLATFORM.clear()
        _DOFOLLOW_BY_PLATFORM.update(df_snap)
        _RATIONALE_BY_PLATFORM.clear()
        _RATIONALE_BY_PLATFORM.update(rat_snap)
        _REFERRAL_VALUE_BY_PLATFORM.clear()
        _REFERRAL_VALUE_BY_PLATFORM.update(ref_snap)
        _REJECTED_PLATFORMS.clear()
        _REJECTED_PLATFORMS.update(rej_snap)
        _UI_META_BY_PLATFORM.clear()
        _UI_META_BY_PLATFORM.update(ui_snap)
        _BIND_BY_PLATFORM.clear()
        _BIND_BY_PLATFORM.update(bind_snap)
        _POLICY_BY_PLATFORM.clear()
        _POLICY_BY_PLATFORM.update(pol_snap)
        _VISIBILITY_BY_PLATFORM.clear()
        _VISIBILITY_BY_PLATFORM.update(vis_snap)


class TestDofollowTrue:
    def test_register_with_dofollow_true_stores_status(self) -> None:
        register("foo_true", FakeAdapter, dofollow=True)
        assert dofollow_status("foo_true") is True
        assert dofollow_rationale("foo_true") is None

    def test_dofollow_true_does_not_require_rationale(self) -> None:
        # R4: dofollow=True may pass rationale informationally.
        register("foo_true_with_msg", FakeAdapter, dofollow=True, rationale="ignored short")
        assert dofollow_status("foo_true_with_msg") is True
        # Informational rationale is stored even when not length-validated.
        assert dofollow_rationale("foo_true_with_msg") == "ignored short"


class TestDofollowFalseRequiresRationale:
    def test_register_with_dofollow_false_and_long_rationale_succeeds(self) -> None:
        register(
            "foo_false",
            FakeAdapter,
            dofollow=False,
            rationale=RATIONALE_PAD,
            referral_value="low",
        )
        assert dofollow_status("foo_false") is False
        assert dofollow_rationale("foo_false") == RATIONALE_PAD

    def test_register_with_dofollow_false_and_short_rationale_raises(self) -> None:
        with pytest.raises(RegistryError, match="rationale"):
            register("foo_false_short", FakeAdapter, dofollow=False, rationale="too short")

    def test_register_with_dofollow_false_and_no_rationale_raises(self) -> None:
        with pytest.raises(RegistryError, match="rationale"):
            register("foo_false_none", FakeAdapter, dofollow=False)

    def test_register_with_invalid_referral_value_raises(self) -> None:
        # The _ReferralValue Literal is static-only; a typo must be caught
        # at runtime, not silently stored and mis-bucketed downstream.
        with pytest.raises(RegistryError, match="referral_value must be"):
            register(
                "foo_bad_referral",
                FakeAdapter,
                dofollow=False,
                rationale=RATIONALE_PAD,
                referral_value="HIGH",  # type: ignore[arg-type]
            )

    def test_register_dofollow_true_rejects_invalid_referral_value(self) -> None:
        # Even when referral_value is optional (dofollow=True), a provided
        # out-of-band value is rejected rather than stored.
        with pytest.raises(RegistryError, match="referral_value must be"):
            register(
                "foo_true_bad_referral",
                FakeAdapter,
                dofollow=True,
                referral_value="medium",  # type: ignore[arg-type]
            )


class TestDofollowUncertainRequiresRationale:
    def test_register_with_uncertain_and_long_rationale_succeeds(self) -> None:
        register(
            "foo_unc",
            FakeAdapter,
            dofollow="uncertain",
            rationale=RATIONALE_PAD,
            referral_value="low",
        )
        assert dofollow_status("foo_unc") == "uncertain"

    def test_register_with_uncertain_and_no_rationale_raises(self) -> None:
        with pytest.raises(RegistryError, match="rationale"):
            register("foo_unc_none", FakeAdapter, dofollow="uncertain")


class TestRejectedPlatform:
    # Phase 3: all three previously rejected platforms (devto, mastodon,
    # wordpresscom) have been un-rejected and registered.  ``_REJECTED_PLATFORMS``
    # is empty.  These tests validate the rejection mechanism still works
    # by temporarily inserting a test entry.

    def test_rejected_name_raises_with_temp_entry(self) -> None:
        # Demonstrate that the rejection mechanism still fires
        # by temporarily inserting a test entry.
        _REJECTED_PLATFORMS["_test_reject"] = "rationale: at least 80 chars of padding here xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        with pytest.raises(RegistryError, match="previously rejected"):
            register("_test_reject", FakeAdapter, dofollow=True)
        del _REJECTED_PLATFORMS["_test_reject"]

    def test_error_message_cites_prior_rationale_and_instructs_deletion(self) -> None:
        # R12: failure message must include both prior rationale + the
        # un-rejection-by-deletion instruction. Use a temp entry.
        _REJECTED_PLATFORMS["_temp_reject"] = "rationale: at least 80 chars of padding here xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        with pytest.raises(RegistryError) as exc:
            register("_temp_reject", FakeAdapter, dofollow=True)
        del _REJECTED_PLATFORMS["_temp_reject"]
        message = str(exc.value)
        assert "previously rejected" in message
        assert "delete this entry" in message
        assert "_REJECTED_PLATFORMS" in message

    def test_un_rejection_by_deletion_then_register_succeeds(self) -> None:
        # R12 happy path: insert a temp entry, delete it, then register succeeds.
        _REJECTED_PLATFORMS["_tmp"] = "rationale: at least 80 chars of padding here xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        _REJECTED_PLATFORMS.pop("_tmp")
        register(
            "_tmp",
            FakeAdapter,
            dofollow=False,
            rationale=RATIONALE_PAD,
            referral_value="low",
        )
        assert dofollow_status("_tmp") is False
        assert "_tmp" not in _REJECTED_PLATFORMS

    def test_wordpresscom_now_registers_successfully(self) -> None:
        # Phase 3: wordpresscom is fully un-rejected and registered.
        assert "wordpresscom" in _REGISTRY
        assert "wordpresscom" not in _REJECTED_PLATFORMS


class TestDofollowKwargRequired:
    def test_register_without_dofollow_raises_type_error(self) -> None:
        # R2: gate-active state — missing dofollow= is a TypeError at
        # import time, no silent default. This is the value-validation
        # gate that closes the PR #108 failure mode.
        with pytest.raises(TypeError, match="dofollow"):
            register("foo_no_kwarg", FakeAdapter)  # type: ignore[call-arg]

    def test_register_with_last_call_wins_overwrites_parallel_dicts(self) -> None:
        # "Last call wins" is preserved across all three dicts. A second
        # register() call with a different dofollow value supersedes the
        # first cleanly — no stale residue in the parallel dicts.
        register(
            "foo_recycle",
            FakeAdapter,
            dofollow=False,
            rationale=RATIONALE_PAD,
            referral_value="low",
        )
        assert dofollow_status("foo_recycle") is False
        register("foo_recycle", FakeAdapter, dofollow=True)
        assert dofollow_status("foo_recycle") is True
        # The old False-state rationale must NOT leak into the new
        # True-state registration (R4: True does not validate rationale,
        # so a stale string would be confusing). Same for referral_value
        # (Plan 2026-05-25-001 last-call-wins across all parallel dicts).
        assert dofollow_rationale("foo_recycle") is None
        assert referral_value("foo_recycle") is None


class TestAccessors:
    def test_dofollow_status_returns_none_for_unregistered(self) -> None:
        assert dofollow_status("nonexistent_platform_xyz") is None

    def test_dofollow_rationale_returns_none_for_unregistered(self) -> None:
        assert dofollow_rationale("nonexistent_platform_xyz") is None
