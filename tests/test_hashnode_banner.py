"""Tests for ``HashnodeAPIAdapter.embed_banner``.

Plan: docs/plans/2026-05-20-004-feat-per-adapter-embed-banner-plan.md
Unit 4 — Hashnode returns ``None`` (writeas-style explicit opt-in to
source_url fallback) because the free GraphQL API was retired on
2026-05-13 (see hashnode.com/changelog/2026-05-13-graphql-api-paid-access).

The plan originally proposed an ``uploadMedia`` GraphQL mutation; this
test file documents the pivot via assertions:

* embed_banner returns None unconditionally — no I/O, no API call.
* Dispatcher routes through source_url fallback (reason=adapter_returned_none).
* Without source_url, banner is silently omitted with skipped_no_artifact.

Distinct from Medium's not-implementing semantics (Medium auto-rehosts
externals; Hashnode rejects the entire media-upload story without a
Pro subscription).
"""

from __future__ import annotations

from pathlib import Path

from backlink_publisher.publishing.adapters.hashnode import HashnodeAPIAdapter
from backlink_publisher.publishing import banner_dispatcher


def _make_adapter() -> HashnodeAPIAdapter:
    """Construct without exercising auth — embed_banner is pure (no
    GraphQL call, no token load)."""
    return HashnodeAPIAdapter()


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
        # embed_banner is pure signal-by-return — must NOT raise on a
        # non-existent path (distinct from blogger/telegraph which read bytes).
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
    """Hashnode + dispatcher → source_url fallback branch."""

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
            platform="hashnode",
            strict=False,
            emit=emit,
        )

        # Source URL prepended via the adapter_returned_none branch
        # (distinct from Medium's adapter_no_method which fires for
        # adapters that don't implement embed_banner at all).
        assert body.startswith("![Test Alt](https://upstream.cdn/banner-1.png)\n\n")
        assert body.endswith("Original post body.")
        assert emit.events == [
            (
                "banner.source_url_fallback",
                {"platform": "hashnode", "reason": "adapter_returned_none"},
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
                # source_url omitted — b64-only provider OR pre-R12 row.
            },
            body="Original post body.",
            platform="hashnode",
            strict=False,
            emit=emit,
        )

        assert body == "Original post body."
        assert emit.events == [
            ("banner.skipped_no_artifact", {"platform": "hashnode"})
        ]

    def test_strict_mode_does_not_alter_pure_return_path(self):
        # Strict gating governs only BannerUploadError; a None return
        # is not an error, so strict=True is a no-op here.  Lock this
        # invariant against a future regression that confuses
        # "returned None" with "raised BannerUploadError".
        adapter = _make_adapter()
        emit = _EmitCapture()

        body = banner_dispatcher.apply(
            adapter,
            banner={
                "path": "/tmp/banner.png",
                "alt": "Alt",
                "mime": "image/png",
                "sha": "x",
                "source_url": "https://upstream/x.png",
            },
            body="b",
            platform="hashnode",
            strict=True,
            emit=emit,
        )

        assert body.startswith("![Alt](https://upstream/x.png)\n\n")
        assert emit.events == [
            (
                "banner.source_url_fallback",
                {"platform": "hashnode", "reason": "adapter_returned_none"},
            )
        ]


# ── Distinct-from-Medium semantics ───────────────────────────────────────────


class TestSemanticsDistinctFromMedium:
    """Hashnode opting-in-with-None and Medium not-implementing produce
    different ``reason`` values in the dispatcher's emit payload.  This
    distinction matters because (a) Medium's not-implementing is its
    own intentional design (auto-rehost), and (b) Hashnode's None is a
    paywall-driven runtime defect we may want to alert on separately."""

    def test_hashnode_reason_is_adapter_returned_none(self):
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
            platform="hashnode",
            strict=False,
            emit=emit,
        )

        # Hashnode IS opted in (has the method) but returns None.
        assert emit.events[0][1]["reason"] == "adapter_returned_none"

    def test_hashnode_has_embed_banner_attribute(self):
        # Regression guard: if someone later "simplifies" by removing
        # the method, semantics flip to Medium-style (adapter_no_method)
        # — same observable behavior on most rows but a different
        # event reason that breaks any downstream metric splitting
        # paywall-driven failures from auto-rehost dispatches.
        adapter = _make_adapter()
        assert hasattr(adapter, "embed_banner")
        assert callable(adapter.embed_banner)
