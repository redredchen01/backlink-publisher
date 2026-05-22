"""Tests for ``VelogGraphQLAdapter.embed_banner``.

Plan: docs/plans/2026-05-20-004-feat-per-adapter-embed-banner-plan.md
Unit 5 — Velog returns ``None`` (explicit opt-in to
source_url fallback) because the plan's ``image_upload_url`` mutation
was not present in velog's GraphQL schema at probe time
(2026-05-20).  Introspection disabled; direct probes of likely
mutation names and REST endpoints all returned validation errors or
HTTP 404.

The pivot rationale lives in detail in the adapter's docstring; this
test file locks the behavioral contract: pure return, no I/O,
dispatcher routes to source_url fallback.
"""

from __future__ import annotations

from pathlib import Path

from backlink_publisher.publishing.adapters.velog_graphql import (
    VelogGraphQLAdapter,
)
from backlink_publisher.publishing import banner_dispatcher


def _make_adapter() -> VelogGraphQLAdapter:
    """Construct without exercising auth — embed_banner is pure (no
    GraphQL call, no cookie load, no daily-cap check)."""
    return VelogGraphQLAdapter()


class _EmitCapture:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind: str, payload: dict) -> None:
        self.events.append((kind, payload))


# ── Pure-return contract ─────────────────────────────────────────────────────


class TestEmbedBannerReturnsNone:
    def test_returns_none_unconditionally(self):
        adapter = _make_adapter()
        result = adapter.embed_banner(Path("/tmp/anything.png"), "alt text")
        assert result is None

    def test_returns_none_regardless_of_path_existence(self):
        adapter = _make_adapter()
        result = adapter.embed_banner(
            Path("/nonexistent/never/created.png"), "Anything"
        )
        assert result is None

    def test_returns_none_with_empty_alt(self):
        adapter = _make_adapter()
        result = adapter.embed_banner(Path("/tmp/x.png"), "")
        assert result is None


# ── Dispatcher integration ───────────────────────────────────────────────────


class TestDispatcherRoutesThroughSourceUrlFallback:
    def test_with_source_url_fallback_branch_fires(self):
        adapter = _make_adapter()
        emit = _EmitCapture()

        body = banner_dispatcher.apply(
            adapter,
            banner={
                "path": "/tmp/banner.png",
                "alt": "Test Alt",
                "mime": "image/png",
                "sha": "deadbeef",
                "source_url": "https://upstream.cdn/banner-1.png",
            },
            body="Original post body.",
            platform="velog",
            strict=False,
            emit=emit,
        )

        assert body.startswith("![Test Alt](https://upstream.cdn/banner-1.png)\n\n")
        assert body.endswith("Original post body.")
        assert emit.events == [
            (
                "banner.source_url_fallback",
                {"platform": "velog", "reason": "adapter_returned_none"},
            )
        ]

    def test_without_source_url_skipped_no_artifact(self):
        adapter = _make_adapter()
        emit = _EmitCapture()

        body = banner_dispatcher.apply(
            adapter,
            banner={
                "path": "/tmp/banner.png",
                "alt": "Test Alt",
                "mime": "image/png",
                "sha": "deadbeef",
            },
            body="Original post body.",
            platform="velog",
            strict=False,
            emit=emit,
        )

        assert body == "Original post body."
        assert emit.events == [
            ("banner.skipped_no_artifact", {"platform": "velog"})
        ]


# ── Distinct-from-Medium / regression guard ──────────────────────────────────


class TestSemanticsDistinctFromMedium:
    def test_velog_reason_is_adapter_returned_none(self):
        adapter = _make_adapter()
        emit = _EmitCapture()

        banner_dispatcher.apply(
            adapter,
            banner={
                "path": "/tmp/x.png",
                "alt": "a",
                "source_url": "https://up/x.png",
            },
            body="b",
            platform="velog",
            strict=False,
            emit=emit,
        )

        # Velog IS opted in (has the method) but returns None.
        assert emit.events[0][1]["reason"] == "adapter_returned_none"

    def test_velog_has_embed_banner_attribute(self):
        # Regression guard: removing the method silently flips to
        # Medium-style branch (adapter_no_method).  Same observable
        # behavior on rows with a source_url but a different event
        # reason that breaks any downstream metric splitting probe-
        # blocked failures from auto-rehost dispatches.
        adapter = _make_adapter()
        assert hasattr(adapter, "embed_banner")
        assert callable(adapter.embed_banner)
