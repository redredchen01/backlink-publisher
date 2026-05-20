"""Unit 2: register() signature extension + rationale validation.

Plan 2026-05-20-009 §Unit 2.
"""

from __future__ import annotations

from typing import Any

import pytest

from backlink_publisher._util.errors import RegistryError
from backlink_publisher.publishing import registry
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.registry import (
    Publisher,
    _REGISTRY,
    _REJECTED_PLATFORMS,
    _DOFOLLOW_BY_PLATFORM,
    _RATIONALE_BY_PLATFORM,
    dofollow_rationale,
    dofollow_status,
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
    rej_snap = dict(_REJECTED_PLATFORMS)
    try:
        yield
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(reg_snap)
        _DOFOLLOW_BY_PLATFORM.clear()
        _DOFOLLOW_BY_PLATFORM.update(df_snap)
        _RATIONALE_BY_PLATFORM.clear()
        _RATIONALE_BY_PLATFORM.update(rat_snap)
        _REJECTED_PLATFORMS.clear()
        _REJECTED_PLATFORMS.update(rej_snap)


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
        register("foo_false", FakeAdapter, dofollow=False, rationale=RATIONALE_PAD)
        assert dofollow_status("foo_false") is False
        assert dofollow_rationale("foo_false") == RATIONALE_PAD

    def test_register_with_dofollow_false_and_short_rationale_raises(self) -> None:
        with pytest.raises(RegistryError, match="rationale"):
            register("foo_false_short", FakeAdapter, dofollow=False, rationale="too short")

    def test_register_with_dofollow_false_and_no_rationale_raises(self) -> None:
        with pytest.raises(RegistryError, match="rationale"):
            register("foo_false_none", FakeAdapter, dofollow=False)


class TestDofollowUncertainRequiresRationale:
    def test_register_with_uncertain_and_long_rationale_succeeds(self) -> None:
        register("foo_unc", FakeAdapter, dofollow="uncertain", rationale=RATIONALE_PAD)
        assert dofollow_status("foo_unc") == "uncertain"

    def test_register_with_uncertain_and_no_rationale_raises(self) -> None:
        with pytest.raises(RegistryError, match="rationale"):
            register("foo_unc_none", FakeAdapter, dofollow="uncertain")


class TestRejectedPlatform:
    def test_register_rejected_name_raises_even_with_valid_dofollow(self) -> None:
        # R12: rejection check fires regardless of dofollow value.
        with pytest.raises(RegistryError, match="previously rejected"):
            register("devto", FakeAdapter, dofollow=False, rationale=RATIONALE_PAD)

    def test_register_rejected_name_with_dofollow_true_still_raises(self) -> None:
        with pytest.raises(RegistryError, match="previously rejected"):
            register("mastodon", FakeAdapter, dofollow=True)

    def test_error_message_cites_prior_rationale_and_instructs_deletion(self) -> None:
        # R12: failure message must include both prior rationale + the
        # un-rejection-by-deletion instruction.
        with pytest.raises(RegistryError) as exc:
            register("wordpresscom", FakeAdapter, dofollow=True)
        message = str(exc.value)
        assert "previously rejected" in message
        assert "delete this entry" in message
        assert "_REJECTED_PLATFORMS" in message

    def test_un_rejection_by_deletion_then_register_succeeds(self) -> None:
        # R12 happy path: delete entry from _REJECTED_PLATFORMS, then
        # register() succeeds with normal R3 validation.
        _REJECTED_PLATFORMS.pop("devto")
        register("devto", FakeAdapter, dofollow=False, rationale=RATIONALE_PAD)
        assert dofollow_status("devto") is False
        assert "devto" not in _REJECTED_PLATFORMS


class TestBackCompatNoneDefault:
    def test_register_without_dofollow_kwarg_still_works(self) -> None:
        # U2 → U5 back-compat: missing kwarg means dofollow=None.
        # U5 will remove the default and make this raise TypeError.
        register("foo_legacy", FakeAdapter)
        assert "foo_legacy" in _REGISTRY
        assert dofollow_status("foo_legacy") is None
        assert dofollow_rationale("foo_legacy") is None

    def test_register_with_explicit_none_clears_stale_parallel_dict(self) -> None:
        # Defensive back-compat: if a name was previously registered
        # with dofollow=True and then re-registered without a kwarg,
        # the parallel dicts must not retain stale truth.
        register("foo_recycle", FakeAdapter, dofollow=True)
        assert dofollow_status("foo_recycle") is True
        register("foo_recycle", FakeAdapter)  # no kwarg → None
        assert dofollow_status("foo_recycle") is None


class TestAccessors:
    def test_dofollow_status_returns_none_for_unregistered(self) -> None:
        assert dofollow_status("nonexistent_platform_xyz") is None

    def test_dofollow_rationale_returns_none_for_unregistered(self) -> None:
        assert dofollow_rationale("nonexistent_platform_xyz") is None
