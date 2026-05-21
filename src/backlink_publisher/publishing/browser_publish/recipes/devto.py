"""Dev.to chrome publish recipe — Plan 2026-05-21-001 Unit 4b.

Registers a recipe for the ``devto`` channel. Dev.to applies
``rel="nofollow ugc"`` to outbound links since ~2022 (platform policy);
backlinks here carry zero PageRank transfer. Re-registration in this
PR pairs with explicit removal from ``_REJECTED_PLATFORMS`` per the
"un-rejection path is by deletion in the same PR" contract in
``registry.py:_REJECTED_PLATFORMS``.

Operator surface: dofollow=False with 80+ char rationale shown in
binding dashboard (Unit 5 will add UI warning macro). Publishing
continues to work — dofollow declaration does NOT block ``register``,
it only marks the platform as nofollow in dashboard chips.
"""

from __future__ import annotations

from typing import Any

from ..chrome_session import BrowserPublishRecipe
from . import RECIPES
from . import _devto_selectors as sel


def devto_publish_flow(page: Any, payload: dict[str, Any]) -> str:
    """Drive dev.to/new and return final published URL."""
    title = payload.get("title")
    body = payload.get("content_markdown") or payload.get("body")
    if not title or not body:
        raise ValueError(
            "devto publish payload missing title or content_markdown/body"
        )

    page.goto(sel.COMPOSE_URL)

    # Title.
    page.wait_for_selector(sel.TITLE_INPUT, timeout=sel.TITLE_FILL_TIMEOUT_MS)
    page.fill(sel.TITLE_INPUT, title)

    # Body markdown.
    page.wait_for_selector(
        sel.BODY_EDITOR_TEXTAREA, timeout=sel.BODY_FILL_TIMEOUT_MS
    )
    body_handle = page.query_selector(sel.BODY_EDITOR_TEXTAREA)
    if body_handle is None:
        raise RuntimeError("devto body textarea not found")
    body_handle.fill(body)

    # Tags (optional; up to 4 on dev.to).
    tags = payload.get("tags") or []
    if tags:
        tags_value = ", ".join(str(t) for t in tags[:4])
        tags_handle = page.query_selector(sel.TAGS_INPUT)
        if tags_handle is not None:
            tags_handle.fill(tags_value)

    # Publish.
    page.wait_for_selector(
        sel.PUBLISH_BUTTON, timeout=sel.PUBLISH_BUTTON_TIMEOUT_MS
    )
    page.click(sel.PUBLISH_BUTTON)

    # Wait for redirect to post URL.
    page.wait_for_url(
        sel.POST_PUBLISHED_URL_RE,
        timeout=sel.POST_PUBLISH_REDIRECT_TIMEOUT_MS,
    )
    return page.url


RECIPES["devto"] = BrowserPublishRecipe(
    channel="devto",
    compose_url=sel.COMPOSE_URL,
    publish_flow=devto_publish_flow,
)


__all__ = ["devto_publish_flow"]
