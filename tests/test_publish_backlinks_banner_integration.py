"""End-to-end integration tests for the banner-embed dispatcher
wired into the publish-backlinks per-row loop.

Plan: docs/plans/2026-05-20-004-feat-per-adapter-embed-banner-plan.md
Unit 1 — verifies that ``banner_emit`` reaches the registry and that
``BannerUploadError`` propagation from a strict-mode adapter is
caught at the row level (not the run level).

Unlike ``test_banner_dispatcher.py`` which tests the pure helper in
isolation, this file exercises the full chain:

    publish_backlinks main loop
      → adapters.publish (forwards banner_emit)
        → registry.dispatch (calls banner_dispatcher.apply per chain attempt)
          → adapter.embed_banner
          → adapter.publish (with body containing the prepended image)
"""

from __future__ import annotations

from typing import Any

import pytest

from backlink_publisher._util.errors import BannerUploadError
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.registry import (
    Publisher,
    _REGISTRY,
    dispatch,
    register,
)


# ── stand-in adapters (test-only registration) ─────────────────────────


class _BodyCapturingAdapter(Publisher):
    """Stores the body it saw in ``publish``.  Useful for asserting
    that the dispatcher actually mutated the payload."""

    calls: list[dict[str, Any]] = []

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Any,
    ) -> AdapterResult:
        type(self).calls.append(dict(payload))
        return AdapterResult(
            status="published",
            adapter="bodycap",
            platform=payload.get("platform", ""),
            published_url="https://example.test/x",
        )


class _OptInAdapter(_BodyCapturingAdapter):
    """Adapter that opts into ``embed_banner`` and returns a fixed URL."""

    embed_calls: list[tuple[Any, str]] = []

    def embed_banner(self, artifact_path: Any, alt: str) -> str | None:
        type(self).embed_calls.append((artifact_path, alt))
        return "https://platform.cdn/uploaded.png"


class _RaiseStrictAdapter(_BodyCapturingAdapter):
    """Adapter whose ``embed_banner`` always raises ``BannerUploadError``."""

    def embed_banner(self, artifact_path: Any, alt: str) -> str | None:
        raise BannerUploadError("simulated upload 4xx")


class _NotOptedInAdapter(_BodyCapturingAdapter):
    """Adapter that does NOT define ``embed_banner`` — Medium-style."""

    pass


# ── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def captured_events() -> list[tuple[str, dict]]:
    return []


@pytest.fixture
def banner_emit(captured_events):
    def _emit(kind: str, payload: dict) -> None:
        captured_events.append((kind, payload))
    return _emit


@pytest.fixture(autouse=True)
def _reset_adapter_state():
    """Each test gets a clean adapter call history."""
    _BodyCapturingAdapter.calls.clear()
    _OptInAdapter.embed_calls.clear()
    yield


@pytest.fixture
def _register_test_platform():
    """Temporarily register a test-only platform; restore on teardown.

    Plan 2026-05-20-009 U3: extended to symmetrically snapshot+restore
    the new parallel dofollow/rationale dicts. The pattern is whole-
    registry clear-and-update (asymmetric from conftest's per-key
    `fake_platform_registered` fixture); each dict gets its own
    snapshot tuple and a paired clear()+update() on teardown.
    """
    snapshot = dict(_REGISTRY)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)


@pytest.fixture
def fake_config():
    """Minimal Config-shaped object that satisfies the dispatch path."""

    class _ImageGen:
        def __init__(self, strict: bool) -> None:
            self.strict = strict

    class _Config:
        def __init__(self, strict: bool = False) -> None:
            self.image_gen = _ImageGen(strict=strict)

    return _Config


_BANNER_DICT = {
    "path": "/tmp/banner.png",
    "alt": "Test Banner",
    "mime": "image/png",
    "sha": "abc123",
    "source_url": "https://upstream.cdn/upstream.png",
}


# ── tests ──────────────────────────────────────────────────────────────


class TestDispatcherIntegration:
    def test_opt_in_adapter_body_modified_event_emitted(
        self, _register_test_platform, captured_events, banner_emit, fake_config
    ):
        register("optin_test", _OptInAdapter, dofollow=True)

        result = dispatch(
            {
                "platform": "optin_test",
                "content_markdown": "Original body.",
                "banner": _BANNER_DICT,
            },
            mode="publish",
            config=fake_config(strict=False),
            banner_emit=banner_emit,
        )

        # adapter.publish saw the modified body
        assert _OptInAdapter.calls[0]["content_markdown"].startswith(
            "![Test Banner](https://platform.cdn/uploaded.png)\n\n"
        )
        assert _OptInAdapter.calls[0]["content_markdown"].endswith("Original body.")
        # embed_banner was actually called with the artifact path + alt
        assert len(_OptInAdapter.embed_calls) == 1
        _, alt = _OptInAdapter.embed_calls[0]
        assert alt == "Test Banner"
        # Exactly one banner.embedded event for the optin_test platform
        assert captured_events == [
            ("banner.embedded", {"platform": "optin_test"})
        ]
        assert result.status == "published"

    def test_not_opted_in_adapter_falls_back_to_source_url(
        self, _register_test_platform, captured_events, banner_emit, fake_config
    ):
        register("noembed_test", _NotOptedInAdapter, dofollow=True)

        dispatch(
            {
                "platform": "noembed_test",
                "content_markdown": "Original body.",
                "banner": _BANNER_DICT,
            },
            mode="publish",
            config=fake_config(strict=False),
            banner_emit=banner_emit,
        )

        assert _NotOptedInAdapter.calls[0]["content_markdown"].startswith(
            "![Test Banner](https://upstream.cdn/upstream.png)\n\n"
        )
        assert captured_events == [
            (
                "banner.source_url_fallback",
                {"platform": "noembed_test", "reason": "adapter_no_method"},
            )
        ]

    def test_strict_true_propagates_banner_upload_error(
        self, _register_test_platform, captured_events, banner_emit, fake_config
    ):
        register("strict_test", _RaiseStrictAdapter, dofollow=True)

        with pytest.raises(BannerUploadError, match="simulated upload 4xx"):
            dispatch(
                {
                    "platform": "strict_test",
                    "content_markdown": "Original body.",
                    "banner": _BANNER_DICT,
                },
                mode="publish",
                config=fake_config(strict=True),
                banner_emit=banner_emit,
            )

        # adapter.publish was NOT called because embed_banner raised
        assert _BodyCapturingAdapter.calls == []
        # No event — strict-mode propagation leaves event emission to
        # the publish-loop's failure recording.
        assert captured_events == []

    def test_strict_false_swallows_emits_failed_publishes_with_unchanged_body(
        self, _register_test_platform, captured_events, banner_emit, fake_config
    ):
        register("permissive_test", _RaiseStrictAdapter, dofollow=True)

        result = dispatch(
            {
                "platform": "permissive_test",
                "content_markdown": "Original body.",
                "banner": _BANNER_DICT,
            },
            mode="publish",
            config=fake_config(strict=False),
            banner_emit=banner_emit,
        )

        # adapter.publish WAS called with the ORIGINAL body
        assert _RaiseStrictAdapter.calls[0]["content_markdown"] == "Original body."
        assert result.status == "published"
        # One banner.failed event
        assert len(captured_events) == 1
        kind, payload = captured_events[0]
        assert kind == "banner.failed"
        assert payload["platform"] == "permissive_test"
        assert "simulated upload 4xx" in payload["reason"]

    def test_no_banner_emit_means_no_banner_work(
        self, _register_test_platform, fake_config
    ):
        """Back-compat: when ``banner_emit=None`` (the default for the
        publish() public API), the dispatcher MUST NOT call
        ``embed_banner`` even when the payload has a banner dict.
        Preserves byte-identical behavior for callers that don't
        configure image_gen at all."""
        register("nobanner_test", _OptInAdapter, dofollow=True)

        dispatch(
            {
                "platform": "nobanner_test",
                "content_markdown": "Original body.",
                "banner": _BANNER_DICT,
            },
            mode="publish",
            config=fake_config(strict=False),
            # banner_emit omitted on purpose
        )

        # Body unchanged + adapter.embed_banner NEVER called
        assert _OptInAdapter.calls[0]["content_markdown"] == "Original body."
        assert _OptInAdapter.embed_calls == []

    def test_dry_run_bypasses_banner_path(
        self, _register_test_platform, captured_events, banner_emit, fake_config
    ):
        """Dry-run short-circuits inside dispatch before any chain
        iteration, so banner work is silently skipped even when
        banner_emit IS supplied.  Caller doesn't have to track
        dry-run state."""
        register("dryrun_test", _OptInAdapter, dofollow=True)

        result = dispatch(
            {
                "platform": "dryrun_test",
                "content_markdown": "Original body.",
                "banner": _BANNER_DICT,
            },
            mode="publish",
            config=fake_config(strict=True),  # strict ignored in dry-run
            dry_run=True,
            banner_emit=banner_emit,
        )

        assert result._dry_run is True
        # No banner work happened
        assert _OptInAdapter.calls == []
        assert _OptInAdapter.embed_calls == []
        assert captured_events == []
