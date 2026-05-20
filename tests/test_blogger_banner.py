"""Tests for ``BloggerAPIAdapter.embed_banner``.

Plan: docs/plans/2026-05-20-004-feat-per-adapter-embed-banner-plan.md
Unit 3 — Blogger inlines the banner as a ``data:<mime>;base64,<...>``
URI in the post HTML.  No upload endpoint, no auth, no retry surface
— pure local base64 computation.

The dispatcher prepends ``![alt](data:...)`` markdown which
``render_to_html`` (markdown-it-py) converts to ``<img src="data:..."
alt="...">`` before it lands in Blogger's ``content_html`` field.
``TestRoundtripDataUriThroughMarkdown`` locks the no-escape invariant.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from backlink_publisher._util.errors import BannerUploadError
from backlink_publisher.publishing.adapters.blogger_api import BloggerAPIAdapter


def _write(tmp_path: Path, name: str, content: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


# ── Happy paths ──────────────────────────────────────────────────────────────


class TestEmbedBannerHappyPath:
    def test_png_returns_exact_data_uri(self, tmp_path):
        payload = b"\x89PNG\r\n\x1a\n"
        path = _write(tmp_path, "banner.png", payload)

        result = BloggerAPIAdapter().embed_banner(path, "Test")

        expected_b64 = base64.b64encode(payload).decode("ascii")
        assert result == f"data:image/png;base64,{expected_b64}"

    def test_webp_returns_data_uri_with_webp_mime(self, tmp_path):
        payload = b"RIFF\x00\x00\x00\x00WEBP"
        path = _write(tmp_path, "banner.webp", payload)

        result = BloggerAPIAdapter().embed_banner(path, "alt")

        assert result is not None
        assert result.startswith("data:image/webp;base64,")
        b64_part = result[len("data:image/webp;base64,") :]
        assert base64.b64decode(b64_part) == payload

    def test_gif_returns_data_uri_with_gif_mime(self, tmp_path):
        # Unexpected mime — Blogger's renderer is responsible; we still emit.
        payload = b"GIF89a"
        path = _write(tmp_path, "banner.gif", payload)

        result = BloggerAPIAdapter().embed_banner(path, "alt")

        assert result is not None
        assert result.startswith("data:image/gif;base64,")

    def test_unknown_extension_defaults_to_image_png_mime(self, tmp_path):
        # Sha-only filename with no extension — mimetypes.guess_type
        # returns (None, None); we default to image/png.
        payload = b"\x89PNG\r\n"
        path = _write(tmp_path, "abc123sha", payload)

        result = BloggerAPIAdapter().embed_banner(path, "alt")

        assert result is not None
        assert result.startswith("data:image/png;base64,")

    def test_large_file_no_size_cap_at_this_layer(self, tmp_path):
        # 5 MiB file — image_gen.storage already caps at upstream; this
        # layer must NOT add a second cap.  Verifies the method completes
        # without error on a realistic worst-case banner.
        payload = b"x" * (5 * 1024 * 1024)
        path = _write(tmp_path, "big.png", payload)

        result = BloggerAPIAdapter().embed_banner(path, "alt")

        assert result is not None
        # Expected length ≈ b64 overhead of payload + header
        # (len(payload) * 4 / 3 rounded up).  Sanity-check it's in the
        # right ballpark without locking exact bytes.
        assert len(result) > len(payload)
        assert len(result) < int(len(payload) * 1.5) + 100


# ── Error paths ──────────────────────────────────────────────────────────────


class TestEmbedBannerErrorPaths:
    def test_unreadable_file_raises_banner_upload_error(self, tmp_path):
        # File does not exist.
        ghost = tmp_path / "never.png"
        with pytest.raises(BannerUploadError, match="banner read failed"):
            BloggerAPIAdapter().embed_banner(ghost, "alt")


# ── Round-trip through markdown-it-py ────────────────────────────────────────


class TestRoundtripDataUriThroughMarkdown:
    """The dispatcher prepends ``![alt](data:...)`` to ``content_markdown``;
    ``extract_publish_html(payload, "blogger")`` then runs ``render_to_html``
    which calls markdown-it-py.  If markdown-it-py escaped ``data:`` →
    ``data%3A`` or stripped the URI entirely, the published Blogger
    post would have a broken image.  This test locks the no-escape
    invariant against a future markdown-it-py upgrade."""

    def test_data_uri_survives_render_to_html(self):
        from backlink_publisher._util.markdown import render_to_html

        data_uri = "data:image/png;base64,iVBORw0KGgo="
        md = f"![Banner Alt]({data_uri})\n\nBody content."

        html = render_to_html(md)

        # The URI must be present verbatim — no ``data%3A``, no stripping.
        assert data_uri in html
        # And as the src attribute of an img tag.
        assert f'src="{data_uri}"' in html or f"src='{data_uri}'" in html
        # And the alt text survives.
        assert 'alt="Banner Alt"' in html or "alt='Banner Alt'" in html

    def test_data_uri_survives_extract_publish_html(self):
        from backlink_publisher.publishing.content_negotiation import (
            extract_publish_html,
        )

        data_uri = "data:image/png;base64,iVBORw0KGgo="
        payload = {
            "content_markdown": f"![Alt]({data_uri})\n\nBody.",
        }

        html = extract_publish_html(payload, "blogger")

        assert data_uri in html


# ── Integration with dispatcher ──────────────────────────────────────────────


class TestEmbedBannerThroughDispatcher:
    def test_dispatcher_prepends_data_uri_into_body(self, tmp_path):
        from backlink_publisher.publishing import banner_dispatcher

        payload = b"\x89PNG\r\n"
        path = _write(tmp_path, "b.png", payload)
        expected_b64 = base64.b64encode(payload).decode("ascii")
        emitted: list[tuple[str, dict]] = []

        body = banner_dispatcher.apply(
            BloggerAPIAdapter(),
            banner={
                "path": str(path),
                "alt": "Banner Alt",
                "mime": "image/png",
                "sha": "deadbeef",
                "source_url": "https://upstream/x.png",
            },
            body="Body content here.",
            platform="blogger",
            strict=False,
            emit=lambda k, p: emitted.append((k, p)),
        )

        assert body.startswith(
            f"![Banner Alt](data:image/png;base64,{expected_b64})\n\n"
        )
        assert body.endswith("Body content here.")
        assert emitted == [("banner.embedded", {"platform": "blogger"})]

    def test_dispatcher_strict_propagates_read_failure(self, tmp_path):
        from backlink_publisher.publishing import banner_dispatcher

        ghost = tmp_path / "ghost.png"
        with pytest.raises(BannerUploadError):
            banner_dispatcher.apply(
                BloggerAPIAdapter(),
                banner={
                    "path": str(ghost),
                    "alt": "Alt",
                    "mime": "image/png",
                    "sha": "x",
                },
                body="b",
                platform="blogger",
                strict=True,
                emit=lambda *_: None,
            )

    def test_dispatcher_non_strict_emits_failed_on_read_error(self, tmp_path):
        from backlink_publisher.publishing import banner_dispatcher

        ghost = tmp_path / "ghost.png"
        emitted: list[tuple[str, dict]] = []

        body = banner_dispatcher.apply(
            BloggerAPIAdapter(),
            banner={
                "path": str(ghost),
                "alt": "Alt",
                "mime": "image/png",
                "sha": "x",
            },
            body="b",
            platform="blogger",
            strict=False,
            emit=lambda k, p: emitted.append((k, p)),
        )

        assert body == "b"
        assert len(emitted) == 1
        assert emitted[0][0] == "banner.failed"
        assert emitted[0][1]["platform"] == "blogger"
