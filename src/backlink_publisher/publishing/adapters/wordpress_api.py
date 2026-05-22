"""WordPress.com REST API adapter (Plan 2026-05-21-001 Unit 5)."""

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


def _load_token() -> str | None:
    """Load WordPress.com bearer token from ``wordpress-token.json``."""
    from backlink_publisher.config.loader import _config_dir
    from backlink_publisher.config.tokens import _load_token as _lt
    data = _lt(None, "wordpress-token.json")
    if data is None:
        return None
    token = data.get("token")
    return str(token) if token else None


class WordPressAPIAdapter(Publisher):
    """Publish to WordPress.com via REST API v1.1."""

    @classmethod
    def available(cls, config: Config) -> bool:
        if config.wordpress is None or not config.wordpress.site:
            return False
        return _load_token() is not None

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        import requests as _requests
        from requests.exceptions import RequestException

        platform = "wordpress"
        article_id = payload.get("id", "")
        t0 = time.monotonic()
        log.info(_json_log(adapter="wordpress-api", phase="start", id=article_id))

        if config.wordpress is None or not config.wordpress.site:
            raise DependencyError(
                "WordPress.com site not configured. Add [wordpress] site=\"...\" "
                "to config.toml"
            )
        token = _load_token()
        if not token:
            raise DependencyError(
                "WordPress.com token not found. "
                "Create wordpress-token.json with {\"token\": \"...\"} (chmod 600)"
            )

        site = config.wordpress.site
        url = f"https://public-api.wordpress.com/rest/v1.1/sites/{site}/posts/new"
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "backlink-publisher/1.0",
        }
        body = {
            "title": payload.get("title", ""),
            "content": extract_publish_html(payload, "wordpress"),
            "tags": payload.get("tags", [])[:20],
            "status": "draft" if mode == "draft" else "publish",
        }

        from backlink_publisher.http import post as http_post

        def _do_post() -> dict[str, Any]:
            resp = http_post(url, headers=headers, json=body, timeout=30)
            if resp.status_code == 401:
                raise AuthExpiredError(
                    channel="wordpress",
                    reason=f"WordPress.com HTTP {resp.status_code}",
                )
            if resp.status_code == 403:
                raise AuthExpiredError(
                    channel="wordpress",
                    reason=f"WordPress.com HTTP {resp.status_code}",
                )
            if resp.status_code == 429:
                raise ExternalServiceError(
                    "WordPress.com API rate-limited (HTTP 429)"
                )
            if resp.status_code in RETRYABLE_HTTP_STATUSES:
                resp.raise_for_status()
            if resp.status_code >= 400:
                raise ExternalServiceError(
                    f"WordPress.com API error (HTTP {resp.status_code}): "
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
                adapter="wordpress-api",
            )
        except (AuthExpiredError, ExternalServiceError, DependencyError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"WordPress.com publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(
            _json_log(adapter="wordpress-api", phase="done", id=article_id, elapsed_ms=elapsed)
        )

        published_url = result.get("URL", "")
        if mode == "draft":
            return AdapterResult(
                status="drafted",
                adapter="wordpress-api",
                platform=platform,
                draft_url=published_url,
            )
        return AdapterResult(
            status="published",
            adapter="wordpress-api",
            platform=platform,
            published_url=published_url,
        )
