"""HTML head-window reading and title extraction helpers.

Extracted from ``fetch.py`` in Plan 2026-05-26-002 Phase 3 to free
monolith-budget headroom.
"""

from __future__ import annotations

from typing import Optional

from bs4 import BeautifulSoup


def read_html_head_window(resp, max_bytes: int) -> bytes:
    """Stream ``resp`` and return the accumulated body up to whichever comes
    first: closing ``</head>`` tag, end of stream, or ``max_bytes``.

    Title extraction lives entirely in ``<head>``; reading the full body is
    wasteful. Stopping at ``</head>`` keeps memory use bounded by the head's
    actual size — typically tens of KB.
    """
    buf = bytearray()
    chunk_size = 16_384
    sentinel = b"</head>"
    probe_window = 32_768
    while len(buf) < max_bytes:
        remaining = max_bytes - len(buf)
        chunk = resp.read(min(chunk_size, remaining))
        if not chunk:
            break
        buf.extend(chunk)
        tail_start = max(0, len(buf) - probe_window)
        if sentinel in bytes(buf[tail_start:]).lower():
            break
    return bytes(buf)


def extract_title(body: bytes) -> Optional[str]:
    """Parse ``body`` as HTML and return the first non-empty title element.

    Looks for ``<meta property="og:title">`` first, then falls back to
    ``<title>``. Returns ``None`` if neither element is present or both
    are empty after strip.
    """
    try:
        soup = BeautifulSoup(body, "html.parser")
    except Exception:  # noqa: BLE001
        return None

    og = soup.find("meta", attrs={"property": "og:title"})
    if og is not None:
        content = og.get("content", "")
        if content and content.strip():
            return content.strip()

    title_tag = soup.find("title")
    if title_tag is not None and title_tag.text:
        stripped = title_tag.text.strip()
        if stripped:
            return stripped

    return None
