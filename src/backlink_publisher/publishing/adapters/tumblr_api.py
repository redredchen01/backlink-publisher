from __future__ import annotations

import json
import time
from typing import Any

import requests
from requests_oauthlib import OAuth1

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


TUMBLR_API_BASE = "https://api.tumblr.com/v2"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 15


def _load_credentials(config: Config) -> dict[str, str]:
    from backlink_publisher.config.tokens import _load_token
    data = _load_token(config.tumblr_credentials_path, "tumblr-credentials.json")
    if not data:
        raise DependencyError(
            "Tumblr credentials not configured. "
            f"Write {{\"consumer_key\": ..., \"consumer_secret\": ..., "
            f"\"oauth_token\": ..., \"oauth_token_secret\": ..., "
            f"\"blog_name\": \"your-blog\"}} "
            f"to {config.tumblr_credentials_path} (chmod 600). "
            "Register an app at https://www.tumblr.com/oauth/apps"
        )
    for field in ("consumer_key", "consumer_secret", "oauth_token", "oauth_token_secret", "blog_name"):
        if not (data.get(field) or "").strip():
            raise DependencyError(
                f"Tumblr credentials file missing '{field}' field"
            )
    return {
        "consumer_key": data["consumer_key"],
        "consumer_secret": data["consumer_secret"],
        "oauth_token": data["oauth_token"],
        "oauth_token_secret": data["oauth_token_secret"],
        "blog_name": data["blog_name"],
    }


class TumblrAPIAdapter(Publisher):
    """Publishes to Tumblr via the REST API v2 with OAuth 1.0a.

    OAuth 1.0a requires five credential fields (consumer_key, consumer_secret,
    oauth_token, oauth_token_secret, blog_name) stored in a 0600 JSON file.
    blog_name is the subdomain (e.g. ``myblog`` for ``myblog.tumblr.com``).

    Tumblr wraps all outbound links with their own redirect
    (``t.umblr.com/redirect``) which strips link equity. The adapter is
    registered with dofollow=False — value is referral traffic + topical
    signal only.

    This adapter uses ``requests.post`` directly (not ``backlink_publisher.http``)
    because OAuth 1.0a signing requires ``requests_oauthlib.OAuth1`` auth
    which is per-request, not compatible with the shared-session wrapper.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        from backlink_publisher.config.tokens import _load_token
        data = _load_token(config.tumblr_credentials_path, "tumblr-credentials.json")
        if not data:
            return False
        return all(
            (data.get(f) or "").strip()
            for f in ("consumer_key", "consumer_secret", "oauth_token",
                      "oauth_token_secret", "blog_name")
        )

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="tumblr", phase="start", id=article_id)))

        creds = _load_credentials(config)
        blog_name = creds["blog_name"]

        title = payload.get("title", "Untitled")
        body = extract_publish_html(payload, "tumblr")
        tags = payload.get("tags", [])[:20]

        auth = OAuth1(
            client_key=creds["consumer_key"],
            client_secret=creds["consumer_secret"],
            resource_owner_key=creds["oauth_token"],
            resource_owner_secret=creds["oauth_token_secret"],
            signature_method="HMAC-SHA1",
        )

        post_data: dict[str, Any] = {
            "type": "text",
            "title": title,
            "body": body,
            "tags": ",".join(str(t) for t in tags),
        }

        if mode == "draft":
            post_data["state"] = "draft"

        api_url = f"{TUMBLR_API_BASE}/blog/{blog_name}/post"

        def execute():
            resp = requests.post(
                api_url,
                auth=auth,
                data=post_data,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "Tumblr OAuth rejected (HTTP 401) — re-authorize "
                    "and update tumblr-credentials.json"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Tumblr API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                resp_body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Tumblr returned non-JSON response: {exc}"
                )
            if resp_body.get("meta", {}).get("status") not in (200, 201):
                msg = resp_body.get("meta", {}).get("msg", "unknown")
                raise ExternalServiceError(f"Tumblr API error: {msg}")
            response_data = resp_body.get("response", {})
            post_id = response_data.get("id", "")
            if not post_id:
                raise ExternalServiceError(
                    "Tumblr createPost returned no id"
                )
            published_url = (
                response_data.get("display_url")
                or f"https://{blog_name}.tumblr.com/post/{post_id}"
            )
            return published_url

        try:
            published_url = retry_transient_call(
                execute,
                is_retryable=lambda exc: (
                    isinstance(exc, ExternalServiceError)
                    and any(
                        f"HTTP {code}" in str(exc)
                        for code in RETRYABLE_HTTP_STATUSES
                    )
                ),
                adapter="tumblr",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Tumblr publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="tumblr", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="published",
            adapter="tumblr",
            platform="tumblr",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
