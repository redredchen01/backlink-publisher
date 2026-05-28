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
    """Snapshot + restore registry state around each test.

    U2 stores dofollow/rationale/referral_value as fields in RegistryEntry
    within _REGISTRY, so only _REGISTRY and _REJECTED_PLATFORMS need
    snapshotting to prevent state leakage between tests.
    """
    reg_snap = dict(_REGISTRY)
    rej_snap = dict(_REJECTED_PLATFORMS)
    try:
        yield
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(reg_snap)
        _REJECTED_PLATFORMS.clear()
        _REJECTED_PLATFORMS.update(rej_snap)


class TestDofollowTrue:
    def test_dofollow_true_sets_status(self) -> None:
        register("foo_true", FakeAdapter, dofollow=True)
        assert dofollow_status("foo_true") is True
        assert dofollow_rationale("foo_true") is None

    def test_dofollow_true_allows_empty_rationale(self) -> None:
        # R4: dofollow=True may pass rationale informationally.
        register("foo_true_with_msg", FakeAdapter, dofollow=True, rationale="ignored short")
        assert dofollow_status("foo_true_with_msg") is True
        # Informational rationale is stored even when not length-validated.
        assert dofollow_rationale("foo_true_with_msg") == "ignored short"


class TestDofollowTrueWithOptionalFields:
    def test_dofollow_true_stores_long_rationale(self) -> None:
        register("test", FakeAdapter, dofollow=True, rationale=RATIONALE_PAD)
        assert dofollow_rationale("test") == RATIONALE_PAD

    def test_dofollow_true_accepts_referral_value(self) -> None:
        register("test", FakeAdapter, dofollow=True, referral_value="high")
        assert referral_value("test") == "high"

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


class TestDofollowFalseRequiresRationale:
    @pytest.mark.parametrize("rationale,ref_value,should_pass,description", [
        (RATIONALE_PAD, "low", True, "valid case with long rationale and low referral"),
        ("too short", "low", False, "short rationale should fail"),
        (RATIONALE_PAD, None, False, "missing referral_value should fail"),
        (None, "low", False, "missing rationale should fail"),
        (RATIONALE_PAD, "HIGH", False, "invalid referral_value should fail"),
    ])
    def test_dofollow_false_validation(self, rationale, ref_value, should_pass, description):
        """Parametrized test for dofollow=False validation requirements."""
        if should_pass:
            register("test_platform", FakeAdapter, dofollow=False, 
                    rationale=rationale, referral_value=ref_value)
            assert dofollow_status("test_platform") is False
            if rationale is not None:
                assert dofollow_rationale("test_platform") == rationale
            if ref_value is not None:
                assert referral_value("test_platform") == ref_value
        else:
            with pytest.raises(RegistryError, match="rationale|referral_value"):
                register("test_platform", FakeAdapter, dofollow=False,
                        rationale=rationale, referral_value=ref_value)


class TestDofollowUncertainRequiresRationale:
    def test_dofollow_uncertain_sets_status(self) -> None:
        register(
            "foo_unc",
            FakeAdapter,
            dofollow="uncertain",
            rationale=RATIONALE_PAD,
            referral_value="low",
        )
        assert dofollow_status("foo_unc") == "uncertain"

    def test_dofollow_uncertain_requires_rationale(self) -> None:
        with pytest.raises(RegistryError, match="rationale"):
            register("foo_unc_none", FakeAdapter, dofollow="uncertain")


class TestRejectedPlatform:
    # Phase 3: all three previously rejected platforms (devto, mastodon,
    # wordpresscom) have been un-rejected and registered.  ``_REJECTED_PLATFORMS``
    # is empty.  These tests validate the rejection mechanism still works
    # by temporarily inserting a test entry.

    def test_rejected_name_raises(self) -> None:
        # Demonstrate that the rejection mechanism still fires
        # by temporarily inserting a test entry.
        _REJECTED_PLATFORMS["_test_reject"] = "rationale: at least 80 chars of padding here xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        with pytest.raises(RegistryError, match="previously rejected"):
            register("_test_reject", FakeAdapter, dofollow=True)
        del _REJECTED_PLATFORMS["_test_reject"]

    def test_error_message_includes_prior_rationale_and_deletion_instruction(self) -> None:
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

    def test_wordpresscom_registers_successfully(self) -> None:
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

    def test_register_with_last_call_wins_overwrites_fields(self) -> None:
        # "Last call wins" overwrites all RegistryEntry fields. A second
        # register() call with a different dofollow value replaces the
        # first completely — no stale field values remain.
        register(
            "foo_recycle",
            FakeAdapter,
            dofollow=False,
            rationale=RATIONALE_PAD,
            referral_value="low",
        )
        assert dofollow_status("foo_recycle") is False
        assert dofollow_rationale("foo_recycle") == RATIONALE_PAD
        assert referral_value("foo_recycle") == "low"
        register("foo_recycle", FakeAdapter, dofollow=True)
        assert dofollow_status("foo_recycle") is True
        # The old False-state rationale must NOT leak into the new
        # True-state registration (R4: True does not validate rationale,
        # so a stale string would be confusing). Same for referral_value
        # (last-call-wins across all fields in RegistryEntry).
        assert dofollow_rationale("foo_recycle") is None
        assert referral_value("foo_recycle") is None


class TestAccessors:
    def test_dofollow_status_returns_none_for_unregistered(self) -> None:
        assert dofollow_status("nonexistent_platform_xyz") is None

    def test_dofollow_rationale_returns_none_for_unregistered(self) -> None:
        assert dofollow_rationale("nonexistent_platform_xyz") is None
