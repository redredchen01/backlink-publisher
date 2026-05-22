"""Tumblr API v2 adapter (Plan 2026-05-21-001 Unit 5).

Uses OAuth1a for authentication. Consumer key/secret live in config.toml;
user OAuth token/secret live in ``tumblr-token.json`` (0600).

The OAuth1 token pair is obtained once via the Tumblr OAuth console and
written to the token file by the operator.  No browser-based auth flow
is implemented in this adapter.
"""

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


def _load_oauth_token() -> dict[str, str] | None:
    """Load Tumblr OAuth token pair from ``tumblr-token.json``."""
    from backlink_publisher.config.tokens import _load_token as _lt
    data = _lt(None, "tumblr-token.json")
    if data is None:
        return None
    tok = data.get("oauth_token")
    sec = data.get("oauth_token_secret")
    if tok and sec:
        return {"oauth_token": str(tok), "oauth_token_secret": str(sec)}
    return None


class TumblrAPIAdapter(Publisher):
    """Publish to Tumblr via REST API v2 with OAuth1a."""

    @classmethod
    def available(cls, config: Config) -> bool:
        if config.tumblr is None:
            return False
        if not config.tumblr.blog_identifier:
            return False
        return _load_oauth_token() is not None

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        import requests as _requests
        from requests.exceptions import RequestException
        from requests_oauthlib import OAuth1

        platform = "tumblr"
        article_id = payload.get("id", "")
        t0 = time.monotonic()
        log.info(_json_log(adapter="tumblr-api", phase="start", id=article_id))

        if config.tumblr is None:
            raise DependencyError(
                "Tumblr not configured. Add [tumblr] section to config.toml"
            )
        if not config.tumblr.blog_identifier:
            raise DependencyError(
                "Tumblr blog_identifier not set. "
                "Add blog_identifier=\"...\" to [tumblr] in config.toml"
            )

        oauth_data = _load_oauth_token()
        if not oauth_data:
            raise DependencyError(
                "Tumblr OAuth token not found. "
                "Create tumblr-token.json with "
                "{\"oauth_token\": \"...\", \"oauth_token_secret\": \"...\"} "
                "(chmod 600)"
            )

        auth = OAuth1(
            client_key=config.tumblr.consumer_key,
            client_secret=config.tumblr.consumer_secret,
            resource_owner_key=oauth_data["oauth_token"],
            resource_owner_secret=oauth_data["oauth_token_secret"],
        )

        blog = config.tumblr.blog_identifier
        url = f"https://api.tumblr.com/v2/blog/{blog}/post"
        headers = {"User-Agent": "backlink-publisher/1.0"}
        body = {
            "type": "text",
            "title": payload.get("title", ""),
            "body": extract_publish_html(payload, "tumblr"),
            "tags": ",".join(payload.get("tags", [])[:20]),
            "state": "draft" if mode == "draft" else "published",
        }

        def _do_post() -> dict[str, Any]:
            resp = _requests.post(url, auth=auth, headers=headers, data=body, timeout=30)
            if resp.status_code == 401:
                raise AuthExpiredError(
                    channel="tumblr",
                    reason=f"Tumblr API HTTP {resp.status_code}",
                )
            if resp.status_code == 429:
                raise ExternalServiceError(
                    "Tumblr API rate-limited (HTTP 429)"
                )
            if resp.status_code in RETRYABLE_HTTP_STATUSES:
                resp.raise_for_status()
            if resp.status_code >= 400:
                raise ExternalServiceError(
                    f"Tumblr API error (HTTP {resp.status_code}): "
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
                adapter="tumblr-api",
            )
        except (AuthExpiredError, ExternalServiceError, DependencyError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Tumblr publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(
            _json_log(adapter="tumblr-api", phase="done", id=article_id, elapsed_ms=elapsed)
        )

        response = result.get("response", {})
        blog_url = response.get("blog", {}).get("url", "")
        post_id = response.get("id", "")
        published_url = f"{blog_url}post/{post_id}" if blog_url and post_id else ""

        if mode == "draft":
            return AdapterResult(
                status="drafted",
                adapter="tumblr-api",
                platform=platform,
                draft_url=published_url,
            )
        return AdapterResult(
            status="published",
            adapter="tumblr-api",
            platform=platform,
            published_url=published_url,
        )
