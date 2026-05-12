"""Shared types for publisher adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdapterResult:
    """Normalised result returned by every adapter."""

    status: str          # "drafted" | "published" | "failed"
    adapter: str         # e.g. "blogger-api", "medium-api", "medium-browser"
    platform: str        # "blogger" | "medium"
    draft_url: str = ""
    published_url: str = ""
    error: str | None = None
    _dry_run: bool = False
    _command: str = ""
    # Per-provider metadata for the verifier (e.g. blogger_id/post_id captured
    # from posts.insert response). Kept internal — not serialised into JSONL.
    _provider_meta: dict[str, str] = field(default_factory=dict)

    def to_publish_output(self, row: dict[str, Any], created_at: str) -> dict[str, Any]:
        """Convert to the JSONL output shape expected by publish_backlinks.

        Includes three additive verification fields with null defaults; the
        publish_backlinks dispatcher overrides these per row after invoking
        the verifier on `status == "published"` rows.
        """
        return {
            "id": row.get("id", ""),
            "platform": self.platform,
            "status": self.status,
            "title": row.get("title", ""),
            "draft_url": self.draft_url,
            "published_url": self.published_url,
            "created_at": created_at,
            "adapter": self.adapter,
            "error": self.error,
            "verified": None,
            "verified_at": None,
            "verification_error": None,
        }
