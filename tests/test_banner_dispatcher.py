"""Tests for ``backlink_publisher.publishing.banner_dispatcher``.

Plan: docs/plans/2026-05-20-004-feat-per-adapter-embed-banner-plan.md
Unit 1 — duck-typed ``embed_banner`` dispatcher with 5 happy-path
branches, 3 error-path branches, and 2 "no-op" branches.

Pure helper: no I/O, no EventStore, no Config. Caller supplies
``emit`` callback (signature mirrors ``EventStore.append``).
"""

from __future__ import annotations

from typing import Any

import pytest

from backlink_publisher._util.errors import BannerUploadError
from backlink_publisher.publishing.banner_dispatcher import apply


# ── helpers ────────────────────────────────────────────────────────────


class _AdapterWithEmbed:
    """Stand-in for a real adapter that implements ``embed_banner``."""

    def __init__(self, behavior: Any) -> None:
        # ``behavior`` is either a string (returned URL), None (returned
        # None), or an Exception instance (raised).
        self._behavior = behavior
        self.calls: list[tuple[Any, str]] = []

    def embed_banner(self, artifact_path: Any, alt: str) -> str | None:
        self.calls.append((artifact_path, alt))
        if isinstance(self._behavior, Exception):
            raise self._behavior
        return self._behavior


class _AdapterWithoutEmbed:
    """Stand-in for a Medium-style adapter that does NOT opt in."""

    pass


class _EmitCapture:
    """Captures emit(kind, payload) calls for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.append((kind, payload))

    @property
    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]


def _banner(
    *,
    path: str | None = "/tmp/banner.png",
    alt: str = "Test Banner",
    mime: str = "image/png",
    sha: str = "abc123",
    source_url: str | None = None,
) -> dict[str, Any]:
    """Build a banner dict matching the post-R12 JSONL shape."""
    d: dict[str, Any] = {"path": path, "alt": alt, "mime": mime, "sha": sha}
    if source_url is not None:
        d["source_url"] = source_url
    return d


_ORIGINAL_BODY = "This is the article body.\n\nSecond paragraph."


# ── happy-path branches ────────────────────────────────────────────────


class TestEmbedSuccess:
    def test_returns_url_prepends_markdown_image_and_emits_embedded(self):
        adapter = _AdapterWithEmbed("https://platform.cdn/foo.png")
        emit = _EmitCapture()

        result = apply(
            adapter,
            banner=_banner(),
            body=_ORIGINAL_BODY,
            platform="telegraph",
            strict=False,
            emit=emit,
        )

        assert result.startswith("![Test Banner](https://platform.cdn/foo.png)\n\n")
        assert result.endswith(_ORIGINAL_BODY)
        assert len(emit.events) == 1
        kind, payload = emit.events[0]
        assert kind == "banner.embedded"
        assert payload["platform"] == "telegraph"

    def test_alt_text_sourced_from_banner_dict_not_recomputed(self):
        adapter = _AdapterWithEmbed("https://platform.cdn/x.png")
        emit = _EmitCapture()

        apply(
            adapter,
            banner=_banner(alt="Custom Alt Override"),
            body=_ORIGINAL_BODY,
            platform="devto",
            strict=False,
            emit=emit,
        )

        # adapter received the alt as-is from banner["alt"]
        assert adapter.calls[0][1] == "Custom Alt Override"


# ── opt-in but can't (returns None) ────────────────────────────────────


class TestEmbedReturnsNone:
    def test_with_source_url_falls_back_emits_returned_none(self):
        adapter = _AdapterWithEmbed(None)
        emit = _EmitCapture()

        result = apply(
            adapter,
            banner=_banner(source_url="https://upstream.cdn/img.png"),
            body=_ORIGINAL_BODY,
            platform="telegraph",
            strict=False,
            emit=emit,
        )

        assert result.startswith("![Test Banner](https://upstream.cdn/img.png)\n\n")
        assert result.endswith(_ORIGINAL_BODY)
        assert emit.events == [
            (
                "banner.source_url_fallback",
                {"platform": "telegraph", "reason": "adapter_returned_none"},
            )
        ]

    def test_without_source_url_skipped_no_artifact(self):
        adapter = _AdapterWithEmbed(None)
        emit = _EmitCapture()

        result = apply(
            adapter,
            banner=_banner(),  # no source_url key at all
            body=_ORIGINAL_BODY,
            platform="telegraph",
            strict=False,
            emit=emit,
        )

        assert result == _ORIGINAL_BODY
        assert emit.kinds == ["banner.skipped_no_artifact"]

    def test_explicit_source_url_none_falls_through_to_skipped(self):
        # source_url key present but value is None (b64-only provider).
        adapter = _AdapterWithEmbed(None)
        emit = _EmitCapture()

        result = apply(
            adapter,
            banner=_banner(source_url=None),
            body=_ORIGINAL_BODY,
            platform="telegraph",
            strict=False,
            emit=emit,
        )

        # source_url is the literal None — same as the missing-key case
        # (the helper treats both as "no source_url available").
        assert result == _ORIGINAL_BODY
        assert emit.kinds == ["banner.skipped_no_artifact"]


# ── no-opt-in (Medium-style) ───────────────────────────────────────────


class TestAdapterWithoutMethod:
    def test_with_source_url_falls_back_emits_no_method(self):
        adapter = _AdapterWithoutEmbed()
        emit = _EmitCapture()

        result = apply(
            adapter,
            banner=_banner(source_url="https://upstream.cdn/img.png"),
            body=_ORIGINAL_BODY,
            platform="medium",
            strict=False,
            emit=emit,
        )

        assert result.startswith("![Test Banner](https://upstream.cdn/img.png)\n\n")
        assert emit.events == [
            (
                "banner.source_url_fallback",
                {"platform": "medium", "reason": "adapter_no_method"},
            )
        ]

    def test_without_source_url_skipped_no_method(self):
        adapter = _AdapterWithoutEmbed()
        emit = _EmitCapture()

        result = apply(
            adapter,
            banner=_banner(),
            body=_ORIGINAL_BODY,
            platform="medium",
            strict=False,
            emit=emit,
        )

        assert result == _ORIGINAL_BODY
        assert emit.kinds == ["banner.skipped_no_method"]


# ── no-banner-attempted (silent) ───────────────────────────────────────


class TestNoBanner:
    def test_banner_dict_is_none_silent(self):
        adapter = _AdapterWithEmbed("https://platform.cdn/should-not-be-called.png")
        emit = _EmitCapture()

        result = apply(
            adapter,
            banner=None,
            body=_ORIGINAL_BODY,
            platform="telegraph",
            strict=False,
            emit=emit,
        )

        assert result == _ORIGINAL_BODY
        assert emit.events == []
        # adapter.embed_banner must NOT be called
        assert adapter.calls == []

    def test_banner_path_is_none_silent_plan_time_degraded(self):
        # plan-backlinks emits ``{"path": None, "status": "..."}`` for
        # every degraded path (capped / gen_failed / storage_failed /
        # auth_failed / auto_disabled).  publish-time dispatcher must
        # treat these as "no banner attempted", silently.
        adapter = _AdapterWithEmbed("https://platform.cdn/should-not-be-called.png")
        emit = _EmitCapture()

        result = apply(
            adapter,
            banner={"path": None, "status": "capped:daily_cap"},
            body=_ORIGINAL_BODY,
            platform="telegraph",
            strict=False,
            emit=emit,
        )

        assert result == _ORIGINAL_BODY
        assert emit.events == []
        assert adapter.calls == []


# ── error paths ────────────────────────────────────────────────────────


class TestBannerUploadError:
    def test_strict_false_swallows_emits_failed_body_unchanged(self):
        adapter = _AdapterWithEmbed(BannerUploadError("devto 4xx"))
        emit = _EmitCapture()

        result = apply(
            adapter,
            banner=_banner(),
            body=_ORIGINAL_BODY,
            platform="devto",
            strict=False,
            emit=emit,
        )

        assert result == _ORIGINAL_BODY
        assert len(emit.events) == 1
        kind, payload = emit.events[0]
        assert kind == "banner.failed"
        assert payload["platform"] == "devto"
        assert "devto 4xx" in payload["reason"]

    def test_strict_true_propagates(self):
        adapter = _AdapterWithEmbed(BannerUploadError("devto 4xx"))
        emit = _EmitCapture()

        with pytest.raises(BannerUploadError, match="devto 4xx"):
            apply(
                adapter,
                banner=_banner(),
                body=_ORIGINAL_BODY,
                platform="devto",
                strict=True,
                emit=emit,
            )

        # No event emitted when strict propagates — the publish-loop
        # caller is the one that records the failure event after
        # writing the checkpoint row.
        assert emit.events == []


class TestNonBannerUploadException:
    def test_adapter_bug_propagates_unconditionally_strict_false(self):
        # Strict gate only governs BannerUploadError.  A buggy adapter
        # raising KeyError / TypeError / etc. should propagate even
        # when strict=False, so the bug is visible and not silently
        # swallowed by the banner pipeline.
        adapter = _AdapterWithEmbed(KeyError("config_blob_unset"))
        emit = _EmitCapture()

        with pytest.raises(KeyError, match="config_blob_unset"):
            apply(
                adapter,
                banner=_banner(),
                body=_ORIGINAL_BODY,
                platform="ghpages",
                strict=False,
                emit=emit,
            )

        assert emit.events == []

    def test_adapter_bug_propagates_unconditionally_strict_true(self):
        adapter = _AdapterWithEmbed(RuntimeError("oops"))
        emit = _EmitCapture()

        with pytest.raises(RuntimeError, match="oops"):
            apply(
                adapter,
                banner=_banner(),
                body=_ORIGINAL_BODY,
                platform="ghpages",
                strict=True,
                emit=emit,
            )

        assert emit.events == []


# ── body shape invariants ──────────────────────────────────────────────


class TestBodyShape:
    def test_exactly_two_newlines_separate_image_from_body(self):
        adapter = _AdapterWithEmbed("https://x/y.png")
        emit = _EmitCapture()

        result = apply(
            adapter,
            banner=_banner(alt="Alt"),
            body="paragraph",
            platform="telegraph",
            strict=False,
            emit=emit,
        )

        # Exactly the contract: `![alt](url)\n\nbody`
        assert result == "![Alt](https://x/y.png)\n\nparagraph"

    def test_body_with_existing_leading_newline_not_double_separated(self):
        # Implementer-driven invariant: dispatcher does NOT strip
        # leading whitespace from body; the prepend is mechanical
        # concatenation.  Reviewers comparing diff to AGENTS.md
        # should rely on this exact assertion.
        adapter = _AdapterWithEmbed("https://x/y.png")
        emit = _EmitCapture()

        result = apply(
            adapter,
            banner=_banner(alt="Alt"),
            body="\nLeading newline body",
            platform="telegraph",
            strict=False,
            emit=emit,
        )

        assert result == "![Alt](https://x/y.png)\n\n\nLeading newline body"


# ── interaction with embed_banner signature ────────────────────────────


class TestEmbedBannerSignature:
    def test_adapter_receives_path_object_not_string(self):
        # Per plan §Key Technical Decisions: dispatcher passes a
        # ``Path``, not a string, so ghpages can use it directly
        # without re-parsing.
        from pathlib import Path

        adapter = _AdapterWithEmbed("https://x/y.png")
        emit = _EmitCapture()

        apply(
            adapter,
            banner=_banner(path="/tmp/foo.png"),
            body=_ORIGINAL_BODY,
            platform="ghpages",
            strict=False,
            emit=emit,
        )

        artifact_path, _ = adapter.calls[0]
        assert isinstance(artifact_path, Path)
        assert str(artifact_path) == "/tmp/foo.png"
