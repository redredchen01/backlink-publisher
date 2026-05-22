"""DEV Community API adapter (Plan 2026-05-21-001 Unit 5)."""

from __future__ import annotations

import json
import time
from typing import Any

from backlink_publisher.config import Config
from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


def _json_log(**kwargs: Any) -> str:
    return json.dumps(kwargs)


def _load_api_key() -> str | None:
    """Load DEV API key from ``dev-token.json``."""
    from backlink_publisher.config.tokens import _load_token as _lt
    data = _lt(None, "dev-token.json")
    if data is None:
        return None
    key = data.get("api_key")
    return str(key) if key else None


class DevAPIAdapter(Publisher):
    """Publish to DEV Community via the Forem API v1."""

    @classmethod
    def available(cls, config: Config) -> bool:
        return _load_api_key() is not None

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        import requests as _requests
        from requests.exceptions import RequestException

        platform = "dev"
        article_id = payload.get("id", "")
        t0 = time.monotonic()
        log.info(_json_log(adapter="dev-api", phase="start", id=article_id))

        api_key = _load_api_key()
        if not api_key:
            raise DependencyError(
                "DEV API key not found. "
                "Create dev-token.json with {\"api_key\": \"...\"} (chmod 600)"
            )

        url = "https://dev.to/api/articles"
        headers = {
            "api-key": api_key,
            "User-Agent": "backlink-publisher/1.0",
            "Content-Type": "application/json",
        }
        body_markdown = extract_publish_html(payload, "dev")

        article = {
            "title": payload.get("title", ""),
            "body_markdown": body_markdown,
            "tags": payload.get("tags", [])[:8],
            "published": mode != "draft",
        }
        request_body = {"article": article}

        def _do_post() -> dict[str, Any]:
            resp = _requests.post(url, headers=headers, json=request_body, timeout=30)
            if resp.status_code == 401:
                raise AuthExpiredError(
                    channel="dev",
                    reason=f"DEV API HTTP {resp.status_code}",
                )
            if resp.status_code == 429:
                raise ExternalServiceError(
                    "DEV API rate-limited (HTTP 429)"
                )
            if resp.status_code in RETRYABLE_HTTP_STATUSES:
                resp.raise_for_status()
            if resp.status_code >= 400:
                raise ExternalServiceError(
                    f"DEV API error (HTTP {resp.status_code}): "
                    f"{resp.text[:500]}"
                )
            return resp.json()

        try:
            result = retry_transient_call(
                _do_post,
                is_retryable=lambda exc: (
                    isinstance(exc, RequestException)
                    or (isinstance(exc, ExternalServiceError)
                        and "HTTP 5" in str(exc))
                ),
                adapter="dev-api",
            )
        except (AuthExpiredError, ExternalServiceError, DependencyError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"DEV publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(
            _json_log(adapter="dev-api", phase="done", id=article_id, elapsed_ms=elapsed)
        )

        published_url = result.get("url", "")
        if mode == "draft":
            return AdapterResult(
                status="drafted",
                adapter="dev-api",
                platform=platform,
                draft_url=published_url,
            )
        return AdapterResult(
            status="published",
            adapter="dev-api",
            platform=platform,
            published_url=published_url,
        )
