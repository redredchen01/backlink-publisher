"""Shared types for publisher adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _resolve_article_urls(row: dict[str, Any], draft_url: str, published_url: str) -> list[str]:
    """Return the canonical article URL list for publish outputs."""
    urls = row.get("article_urls")
    if isinstance(urls, list):
        resolved = [str(url).strip() for url in urls if str(url).strip()]
        if resolved:
            return resolved
    return [u for u in (published_url.strip(), draft_url.strip()) if u]


@dataclass
class AdapterResult:
    """Normalised result returned by every adapter."""

    status: str          # "drafted" | "published" | "failed"
    adapter: str         # e.g. "blogger-api", "medium-api", "medium-browser"
    platform: str        # "blogger" | "medium"
    draft_url: str = ""
    published_url: str = ""
    error: str | None = None
    post_publish_delay_seconds: int = 0  # adapter-declared throttle (plan 2026-05-18-009 R9c)
    _dry_run: bool = False
    _command: str = ""
    _provider_meta: dict[str, Any] | None = None  # optional platform-specific metadata

    def to_publish_output(self, row: dict[str, Any], created_at: str) -> dict[str, Any]:
        """Convert to the JSONL output shape expected by publish_backlinks."""
        article_urls = _resolve_article_urls(row, self.draft_url, self.published_url)
        out = {
            "id": row.get("id", ""),
            "platform": self.platform,
            "status": self.status,
            "title": row.get("title", ""),
            "target_url": row.get("target_url", ""),
            "article_urls": article_urls,
            "draft_url": self.draft_url,
            "published_url": self.published_url,
            "created_at": created_at,
            "adapter": self.adapter,
            "error": self.error,
        }
        # Surface the post-publish link-attribute verdict (R4 canary loop) when an
        # adapter attached it. Emit only when present so draft mode and adapters
        # that do not verify keep an unchanged output shape.
        if self._provider_meta and "link_attr_verification" in self._provider_meta:
            out["link_attr_verification"] = self._provider_meta["link_attr_verification"]
        return out
