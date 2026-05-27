"""Manifest helpers — Plan 2026-05-25-002 Unit 1.

Extracted from registry.py for monolith SLOC budget. These six helpers are the
reverse-lookup API downstream layers (binding_status.py, webui_app/__init__.py
inject_platforms, webui_app/helpers/contexts.py, config/_toml_utils.py,
templates) call instead of carrying their own hardcoded channel lists.

All helpers are pure-read; thread-safe; cheap (dict.get / list-comp on ≤ 20
platforms). No caching needed at this layer — if downstream call-frequency
demands it (per [[flask-g-cache-pattern]]), the cache belongs at the caller.
"""

from __future__ import annotations

from typing import Callable

from backlink_publisher.config import Config
from backlink_publisher.publishing._manifest_types import (
    BindDescriptor,
    Policy,
    UiMeta,
    Visibility,
)

from .registry import (
    _BIND_BY_PLATFORM,
    _POLICY_BY_PLATFORM,
    _REGISTRY,
    _UI_META_BY_PLATFORM,
    _VISIBILITY_BY_PLATFORM,
)


def ui_meta(name: str) -> UiMeta | None:
    """Return the declared ``UiMeta`` for ``name``, or ``None``.

    ``None`` for platforms registered without ``ui=`` (legacy platforms
    pre-manifest). Callers wanting a fallback should use
    ``ui_meta(name) or UiMeta(display_name=name, domain="", category="")``.
    """
    return _UI_META_BY_PLATFORM.get(name)


def bind_descriptors(name: str) -> tuple[BindDescriptor, ...]:
    """Return the declared bind backends for ``name``, in display order.

    Returns ``()`` for platforms registered without ``bind=`` — the
    legacy state where bind wiring is hardcoded across webui_app /
    helpers / templates rather than declared. Callers iterating to
    auto-build UI cards (Plan Unit 4) treat ``()`` as "fall back to
    legacy per-channel wiring".
    """
    return _BIND_BY_PLATFORM.get(name, ())


def policy(name: str) -> Policy | None:
    """Return the declared ``Policy`` for ``name``, or ``None``.

    ``None`` for legacy registrations. Downstream throttle / retry /
    language machinery keeps its existing defaults when ``policy()`` is
    ``None`` — the manifest is additive metadata, not a behaviour
    rewrite (Plan Scope Boundaries: "不改 publish 業務邏輯").
    """
    return _POLICY_BY_PLATFORM.get(name)


def visibility(name: str) -> Visibility:
    """Return the visibility state for ``name``.

    Always returns a valid ``Visibility`` literal. Unregistered or
    default-active platforms return ``"active"`` — this is the
    load-bearing default that lets Unit 2 swap ``HIDDEN_FROM_UI``
    frozenset to ``visibility(name) in {"hidden","retired"}`` without
    needing a per-platform opt-in.
    """
    return _VISIBILITY_BY_PLATFORM.get(name, "active")


def active_platforms() -> list[str]:
    """Return registered platforms with ``visibility == "active"``.

    Sorted (matches ``registered_platforms`` ordering). Used by Unit 4
    WebUI wiring to populate filter chips and publish-select that
    should *not* show hidden / retired / experimental channels.

    For the variant that includes experimental (e.g. the
    ``--include-experimental`` CLI path or WebUI advanced mode), call
    sites should filter ``registered_platforms()`` themselves with
    ``visibility(name) != "retired"`` — the goal here is the default
    user-facing list, not every possible filter.
    """
    return sorted(
        name for name in _REGISTRY
        if visibility(name) == "active"
    )


def bound_platforms(
    config: Config,
    is_bound: Callable[[Config, str], bool],
) -> list[str]:
    """Return active platforms that are currently bound for ``config``.

    ``is_bound`` is dependency-injected: this module lives in the
    ``publishing`` layer and must not import from ``webui_app``
    (binding-status helpers live up there). Callers in WebUI inject
    ``lambda cfg, name: webui_app.binding_status.get_channel_status(cfg, name).get("bound", False)``
    or the equivalent.

    Filtering is composed: ``active_platforms()`` first (drops hidden /
    retired / experimental), then ``is_bound(config, name)`` for the
    remainder. This guarantees a retired platform never appears in
    publish UI even if its credentials are still on disk.
    """
    return [name for name in active_platforms() if is_bound(config, name)]


def legacy_platforms() -> list[str]:
    """Return registered platforms with NO manifest metadata.

    A platform is "legacy" iff none of ``ui_meta`` / ``bind_descriptors``
    / ``policy`` was supplied at ``register()`` time. ``visibility`` is
    intentionally excluded — leaving it at the default ``"active"`` is
    the expected state for pre-manifest channels.

    Plan Unit 5 surfaces this as a migration progress board (printed to
    contract-test stdout, not failed). Becomes a CI fail gate once all
    8 existing platforms are migrated (deferred to Phase 3 of the plan).
    """
    return sorted(
        name for name in _REGISTRY
        if name not in _UI_META_BY_PLATFORM
        and name not in _BIND_BY_PLATFORM
        and name not in _POLICY_BY_PLATFORM
    )
