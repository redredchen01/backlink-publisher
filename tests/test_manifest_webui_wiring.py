"""Plan 2026-05-25-002 Unit 4a — WebUI reverse-lookup wiring.

Verifies that the WebUI context injectors (``inject_platforms`` in
``webui_app/__init__.py`` and ``_settings_context`` in
``webui_app/helpers/contexts.py``) read from the registry's
manifest-aware helpers instead of carrying their own filter logic.

Scope guard: this file only covers the ``inject_platforms`` +
``dashboard_channels`` swap. Template-level token-paste 5-site wire-up
is Unit 4b (separate PR — touches templates + JS).
"""

from __future__ import annotations

from typing import Any

import pytest

from backlink_publisher.publishing import adapters as _production  # noqa: F401
from backlink_publisher.publishing.registry import (
    Publisher,
    _BIND_BY_PLATFORM,
    _POLICY_BY_PLATFORM,
    _REGISTRY,
    _UI_META_BY_PLATFORM,
    _VISIBILITY_BY_PLATFORM,
    register,
)
from backlink_publisher.publishing.adapters.base import AdapterResult


class _Fake(Publisher):
    def publish(
        self, payload: dict[str, Any], mode: str, config: Any
    ) -> AdapterResult:  # pragma: no cover
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _snapshot():
    snaps = [
        (_REGISTRY, dict(_REGISTRY)),
        (_UI_META_BY_PLATFORM, dict(_UI_META_BY_PLATFORM)),
        (_BIND_BY_PLATFORM, dict(_BIND_BY_PLATFORM)),
        (_POLICY_BY_PLATFORM, dict(_POLICY_BY_PLATFORM)),
        (_VISIBILITY_BY_PLATFORM, dict(_VISIBILITY_BY_PLATFORM)),
    ]
    try:
        yield
    finally:
        for store, snap in snaps:
            store.clear()
            store.update(snap)


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Minimal Flask app with the production webui_app blueprint stack."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    from webui_app import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    return flask_app


class TestInjectPlatformsKeysPreserved:
    """Plan U4a explicitly preserves the two-key shape — history filter
    chips need ``platforms`` (all registered), publish select needs
    ``bound_platforms`` (active + bound). Merging would break per
    ``feedback_platforms_vs_bound_platforms_split``."""

    def test_both_context_keys_present(self, app) -> None:
        with app.test_request_context("/"):
            from flask import current_app

            ctx = {}
            for processor in current_app.template_context_processors[None]:
                ctx.update(processor())
            assert "platforms" in ctx
            assert "bound_platforms" in ctx

    def test_platforms_is_full_registered_list(self, app) -> None:
        from backlink_publisher.publishing.registry import registered_platforms

        with app.test_request_context("/"):
            from flask import current_app

            ctx = {}
            for processor in current_app.template_context_processors[None]:
                ctx.update(processor())
            slugs = {p["slug"] for p in ctx["platforms"]}
            assert slugs == set(registered_platforms())


class TestUiMetaDisplayName:
    """When a channel declares ``ui=UiMeta(...)``, its display_name comes
    from the manifest. Otherwise legacy ``slug.title()`` derivation."""

    def test_velog_uses_manifest_display_name(self, app) -> None:
        # Velog declared UiMeta(display_name="Velog", ...) in Unit 3.
        # Happens to match s.title() so this asserts the lookup path
        # *executes*, not just the output value.
        with app.test_request_context("/"):
            from flask import current_app

            ctx = {}
            for processor in current_app.template_context_processors[None]:
                ctx.update(processor())
            velog_entry = next(
                (p for p in ctx["platforms"] if p["slug"] == "velog"), None
            )
            assert velog_entry is not None
            assert velog_entry["display_name"] == "Velog"

    def test_every_platform_has_manifest_display_name(self, app) -> None:
        # Plan 2026-05-25-002 Phase 2 finish — every production channel
        # now declares a UiMeta. The legacy slug.title() fallback path
        # is unreachable in production but still wired (the helper keeps
        # it for forward-compat with newly-registered platforms that
        # have not yet declared a manifest).
        from backlink_publisher.publishing.registry import ui_meta

        with app.test_request_context("/"):
            from flask import current_app

            ctx = {}
            for processor in current_app.template_context_processors[None]:
                ctx.update(processor())
            for entry in ctx["platforms"]:
                meta = ui_meta(entry["slug"])
                assert meta is not None, (
                    f"{entry['slug']!r}: no UiMeta — Phase 2 missed this "
                    f"channel. Add a <SLUG>_MANIFEST to _manifests.py."
                )
                assert entry["display_name"] == meta.display_name


class TestHiddenPlatformFiltered:
    """A hidden manifest platform must not appear in bound_platforms or
    in dashboard_channels (via active_platforms)."""

    def test_register_hidden_platform_excluded_from_bound(self, app) -> None:
        register("ui_hidden", _Fake, dofollow=True, visibility="hidden")

        with app.test_request_context("/"):
            from flask import current_app

            ctx = {}
            for processor in current_app.template_context_processors[None]:
                ctx.update(processor())
            bound_slugs = {p["slug"] for p in ctx["bound_platforms"]}
            assert "ui_hidden" not in bound_slugs
            # But it still appears in the FULL list (history chips).
            all_slugs = {p["slug"] for p in ctx["platforms"]}
            assert "ui_hidden" in all_slugs

    def test_register_retired_platform_excluded_from_bound(self, app) -> None:
        register("ui_retired", _Fake, dofollow=True, visibility="retired")

        with app.test_request_context("/"):
            from flask import current_app

            ctx = {}
            for processor in current_app.template_context_processors[None]:
                ctx.update(processor())
            bound_slugs = {p["slug"] for p in ctx["bound_platforms"]}
            assert "ui_retired" not in bound_slugs


class TestFallbackOnFailure:
    """If config load fails, bound_platforms should fall back to the
    full platforms list so the form never breaks mid-render."""

    def test_fallback_when_config_load_raises(self, app, monkeypatch) -> None:
        def _raise(*_a, **_kw):
            raise RuntimeError("simulated config load failure")

        monkeypatch.setattr(
            "backlink_publisher.config.load_config", _raise
        )

        with app.test_request_context("/"):
            from flask import current_app

            ctx = {}
            for processor in current_app.template_context_processors[None]:
                ctx.update(processor())
            # Fallback: bound_platforms degrades to full list.
            assert len(ctx["bound_platforms"]) == len(ctx["platforms"])
