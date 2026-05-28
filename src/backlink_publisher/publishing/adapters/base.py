"""Shared types and base functionality for publisher adapters."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

import requests
from backlink_publisher.config import Config
from backlink_publisher.config.types import MEDIUM_API_BASE, MEDIUM_API_TIMEOUT, BLOGGER_LOCK_TIMEOUT_S
from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.adapters.retry import (
    RETRYABLE_HTTP_STATUSES,
    retry_transient_call,
)
from backlink_publisher.http import get as http_get, post as http_post

T = TypeVar("T")


_LINK_ATTR_VERIFICATION_KEY = "link_attr_verification"


def carry_link_attr_verification(
    out: dict[str, Any], source: dict[str, Any] | None
) -> dict[str, Any]:
    """Copy the post-publish link-attribute verdict into ``out`` when present.

    ``source`` is the metadata holder — ``AdapterResult._provider_meta`` on the
    fresh path or a checkpoint item on the resume path. The verdict (R4 canary
    loop) is emitted only when ``source`` carries a non-None value, so draft mode
    and adapters that do not verify keep an unchanged output shape. Shared by both
    publish-output emitters so the two paths stay byte-identical.
    """
    if source:
        verdict = source.get(_LINK_ATTR_VERIFICATION_KEY)
        if verdict is not None:
            out[_LINK_ATTR_VERIFICATION_KEY] = verdict
    return out


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
        # adapter attached it (no-op for draft / non-verifying adapters).
        return carry_link_attr_verification(out, self._provider_meta)


class BaseAdapter:
    """Base adapter class with common HTTP handling and error patterns."""
    
    def _json_log(self, **kwargs: Any) -> str:
        """Create a JSON log line."""
        import json
        return json.dumps(kwargs)
    
    def _make_headers(self, token: str) -> dict[str, str]:
        """Create standard authorization headers."""
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    
    def _handle_http_response(
        self,
        resp: requests.Response,
        adapter_name: str,
        endpoint: str = "",
    ) -> requests.Response:
        """Handle HTTP response with standard error checking."""
        if resp.status_code == 401:
            raise AuthExpiredError(
                channel=adapter_name,
                reason=f"{adapter_name} {endpoint} HTTP 401",
            )
        if not resp.ok:
            raise ExternalServiceError(
                f"{adapter_name} {endpoint} returned HTTP {resp.status_code}"
            )
        return resp
    
    def _retry_http_call(
        self,
        fn: Callable[[], requests.Response],
        adapter_name: str,
        max_attempts: int = 3,
    ) -> requests.Response:
        """Execute an HTTP call with retry logic."""
        try:
            return retry_transient_call(
                fn,
                is_retryable=lambda exc: isinstance(
                    exc, (requests.Timeout, requests.ConnectionError)
                ),
                adapter=adapter_name,
                max_attempts=max_attempts,
            )
        except requests.RequestException as exc:
            raise ExternalServiceError(
                f"{adapter_name} API unreachable: {exc}"
            ) from None
    
    def _handle_rate_limit(
        self,
        resp: requests.Response,
        adapter_name: str,
    ) -> None:
        """Handle rate limiting responses."""
        if resp.status_code == 429:
            raise ExternalServiceError(f"{adapter_name} API rate-limited (429)")


# Backward compatibility - expose the classes that were previously here
__all__ = [
    "AdapterResult",
    "BaseAdapter",
    "carry_link_attr_verification",
    "_resolve_article_urls",
]
