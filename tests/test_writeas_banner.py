"""Tests for ``WriteAsAPIAdapter.embed_banner``.

Plan: docs/plans/2026-05-20-004-feat-per-adapter-embed-banner-plan.md
Unit 1 — Write.as opts in with a ``None``-returning ``embed_banner``
to route the dispatcher to the source_url fallback branch.

Distinct from Medium (which does NOT implement ``embed_banner``):
returning ``None`` carries the "I considered this but can't"
semantic; not-implementing carries the "this adapter does not
participate in banners at all" semantic.
"""

from __future__ import annotations

from pathlib import Path

from backlink_publisher.publishing.adapters.writeas import WriteAsAPIAdapter
from backlink_publisher.publishing import banner_dispatcher


def _make_adapter() -> WriteAsAPIAdapter:
    """Construct an adapter without exercising any auth — embed_banner
    is intentionally pure (no Write.as API call), so we don't need to
    configure tokens or sessions."""
    return WriteAsAPIAdapter()


class _EmitCapture:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind: str, payload: dict) -> None:
        self.events.append((kind, payload))


class TestEmbedBannerReturnsNone:
    def test_returns_none_unconditionally(self):
        adapter = _make_adapter()
        result = adapter.embed_banner(Path("/tmp/anything.png"), "alt text")
        assert result is None

    def test_returns_none_regardless_of_path_existence(self):
        # The method MUST not raise on a non-existent path —
        # it's a pure signal-by-return, no I/O on the filesystem.
        adapter = _make_adapter()
        result = adapter.embed_banner(
            Path("/nonexistent/never/created.png"), "Anything"
        )
        assert result is None


class TestDispatcherRoutesThroughSourceUrlFallback:
    """Integration: writeas + dispatcher → source_url fallback."""

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
            platform="writeas",
            strict=False,
            emit=emit,
        )

        # Source URL prepended via the dispatcher's
        # ``adapter_returned_none`` branch.
        assert body.startswith("![Test Alt](https://upstream.cdn/banner-1.png)\n\n")
        assert body.endswith("Original post body.")
        assert emit.events == [
            (
                "banner.source_url_fallback",
                {"platform": "writeas", "reason": "adapter_returned_none"},
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
                # source_url omitted — b64-only provider OR pre-R12 row
            },
            body="Original post body.",
            platform="writeas",
            strict=False,
            emit=emit,
        )

        assert body == "Original post body."
        assert emit.events == [
            ("banner.skipped_no_artifact", {"platform": "writeas"})
        ]
