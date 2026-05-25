"""Plan 2026-05-25-002 Unit 5 — Channel Manifest contract test.

Asserts the invariants every register() call must satisfy AND prints
the manifest migration progress board to stdout (Phase-2 visibility).
The progress count is *not* a fail gate yet — it becomes one in Phase 3
of the plan when all 8 production channels have migrated.

Contract rules:

  R1. visibility(name) must be one of the 4-state Literal
      ('active' / 'experimental' / 'hidden' / 'retired').

  R2. Every entry in bind_descriptors(name) must be a BindDescriptor
      instance with backend in the allowed Literal set.

  R3. Every declared card_template path must exist on disk (under
      webui_app/templates/) OR the manifest must omit the field. Bad
      paths surface as Jinja TemplateNotFound at first request — better
      to fail in CI.

  R4. The registered adapter class (or instance) must implement publish
      and available — the R9 Publisher ABC enforces this at instantiation
      time, but importing the registered class without instantiating is
      still a sufficient signal.

Cross-checks the production registry only — synthetic test platforms
created by other fixtures are excluded.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import backlink_publisher.publishing.adapters as _production  # noqa: F401
from backlink_publisher.publishing._manifest_types import (
    _BIND_BACKEND_VALUES,
    _VISIBILITY_VALUES,
    BindDescriptor,
)
from backlink_publisher.publishing.registry import (
    Publisher,
    _REGISTRY,
    bind_descriptors,
    legacy_platforms,
    registered_platforms,
    visibility,
)

# Resolve the templates directory once. Used by R3 card_template
# existence check.
_TEMPLATES_DIR = (
    Path(__file__).resolve().parents[1] / "webui_app" / "templates"
)


@pytest.mark.parametrize("platform", registered_platforms())
class TestPerPlatformContract:
    """Each invariant fires once per production platform."""

    def test_visibility_is_valid_literal(self, platform: str) -> None:
        assert visibility(platform) in _VISIBILITY_VALUES, (
            f"{platform!r}: visibility={visibility(platform)!r} is not in "
            f"the allowed set {sorted(_VISIBILITY_VALUES)}."
        )

    def test_bind_entries_are_bind_descriptor_instances(
        self, platform: str
    ) -> None:
        for idx, descriptor in enumerate(bind_descriptors(platform)):
            assert isinstance(descriptor, BindDescriptor), (
                f"{platform!r}: bind[{idx}] is "
                f"{type(descriptor).__name__}, expected BindDescriptor. "
                f"This shouldn't happen at runtime — register() validates "
                f"this — but the contract test catches dict-injection "
                f"shortcuts."
            )

    def test_bind_backends_are_valid_literal(self, platform: str) -> None:
        for idx, descriptor in enumerate(bind_descriptors(platform)):
            assert descriptor.backend in _BIND_BACKEND_VALUES, (
                f"{platform!r}: bind[{idx}].backend={descriptor.backend!r} "
                f"is not in {sorted(_BIND_BACKEND_VALUES)}."
            )

    def test_card_template_exists_if_declared(self, platform: str) -> None:
        for idx, descriptor in enumerate(bind_descriptors(platform)):
            if descriptor.card_template is None:
                continue
            template_path = _TEMPLATES_DIR / descriptor.card_template
            assert template_path.is_file(), (
                f"{platform!r}: bind[{idx}].card_template="
                f"{descriptor.card_template!r} does not exist at "
                f"{template_path}. Either fix the path or set the field "
                f"to None to fall back to _channel_card_macro.html."
            )

    def test_adapter_is_publisher_subclass_or_instance(
        self, platform: str
    ) -> None:
        chain = _REGISTRY[platform]
        assert chain, f"{platform!r} has empty chain"
        for idx, entry in enumerate(chain):
            if inspect.isclass(entry):
                assert issubclass(entry, Publisher), (
                    f"{platform!r}: chain[{idx}] class {entry.__name__} "
                    f"is not a Publisher subclass."
                )
            else:
                assert isinstance(entry, Publisher), (
                    f"{platform!r}: chain[{idx}] instance "
                    f"{type(entry).__name__} is not a Publisher instance."
                )


class TestMigrationProgressBoard:
    """Phase 3 gate (Plan 2026-05-25-002): no production channel may be
    legacy. After all 10 channels migrated in Phase 2 (PR #215), this
    test flipped from stdout-print to hard CI fail gate. Any new
    register() call without a manifest immediately fires.
    """

    def test_no_legacy_platforms_remain(self) -> None:
        legacy = legacy_platforms()
        assert legacy == [], (
            f"Phase 3 gate breach: {legacy!r} declared register() "
            f"without ui / bind / policy kwargs. Add a "
            f"<SLUG>_MANIFEST dict to publishing/_manifests.py and "
            f"splat it into the register() call. See the velog or "
            f"telegraph manifest for reference, or AGENTS.md "
            f"'Declare manifest metadata' section."
        )

    def test_print_progress(self, capsys) -> None:
        # Keep the progress board printed to stdout so CI logs surface
        # the current count alongside the gate above. After Phase 3
        # this always reads N/N + ∅; if it ever doesn't, the gate
        # above will have already failed.
        total = len(registered_platforms())
        legacy = legacy_platforms()
        migrated = total - len(legacy)
        print(
            f"\n=== Manifest migration progress ===\n"
            f"{migrated}/{total} channels migrated to full manifest.\n"
            f"Legacy (no ui/bind/policy): {legacy or '∅'}\n"
            f"==================================="
        )

    def test_velog_is_not_legacy(self) -> None:
        # Belt-and-suspenders with test_manifest_pilot_velog — captured
        # here so the contract test alone proves the pilot survived.
        assert "velog" not in legacy_platforms()


class TestVisibilityCoverage:
    """Ensure visibility() returns a valid literal even for synthetic
    cases — the Literal type is static-only, so this validates the
    runtime guard in register().
    """

    def test_no_production_platform_has_invalid_visibility(self) -> None:
        for platform in registered_platforms():
            assert visibility(platform) in _VISIBILITY_VALUES
