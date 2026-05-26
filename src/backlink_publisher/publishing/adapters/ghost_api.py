from __future__ import annotations

import base64
import json
import time
from typing import Any

import requests

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 15


class GhostAPIAdapter(Publisher):
    """Publishes to a self-hosted Ghost blog via Admin REST API.

    Authentication: Ghost Admin API key (``{id}:{secret}`` format) stored
    in a 0600 JSON file (``ghost-token.json``)::

        { "admin_api_key": "<id>:<secret>", "site_url": "https://yourblog.ghost.io" }

    The adapter generates a short-lived JWT from the admin API key and uses
    the ``/ghost/api/admin/posts/`` endpoint to create posts.

    Ghost does not modify outbound links server-side (the API stores HTML
    verbatim), so registered with ``dofollow=True``. Site URL can end with
    or without a trailing slash — the adapter normalises it.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        cred_file = config.config_dir / "ghost-token.json"
        if not cred_file.exists():
            return False
        return True

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="ghost", phase="start", id=article_id)))

        cred_file = config.config_dir / "ghost-token.json"
        if not cred_file.exists():
            raise DependencyError(
                "Ghost Admin API key not configured.\n"
                f"Write {{\"admin_api_key\": \"<id>:<secret>\", \"site_url\": \"...\"}} "
                f"to {cred_file} (chmod 600).\n"
                "Get the key from Ghost Admin → Settings → Advanced → Integrations."
            )

        try:
            creds = json.loads(cred_file.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise DependencyError(f"Cannot read Ghost credentials: {exc}") from None

        admin_api_key = (creds.get("admin_api_key") or "").strip()
        site_url = (creds.get("site_url") or "").rstrip("/")
        if not admin_api_key or not site_url:
            raise DependencyError(
                "Ghost credentials must contain 'admin_api_key' and 'site_url'"
            )

        # Generate Ghost Admin API JWT (standard per Ghost docs)
        key_id, secret = admin_api_key.split(":", 1)
        # The JWT header + payload is standard Ghost Admin API auth
        import hmac
        import hashlib
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "HS256", "typ": "JWT", "kid": key_id}).encode()
        ).rstrip(b"=")
        now = int(time.time())
        payload_jwt = base64.urlsafe_b64encode(
            json.dumps({"iat": now, "exp": now + 300, "aud": "/admin/"}).encode()
        ).rstrip(b"=")
        sig = hmac.new(
            secret.encode(), header + b"." + payload_jwt, hashlib.sha256
        ).digest()
        sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=")
        token = (header + b"." + payload_jwt + b"." + sig_b64).decode()

        title = payload.get("title", "Untitled")
        content = (
            payload.get("content_markdown")
            or extract_publish_html(payload, "ghost")
            or ""
        )
        tags = [{"name": t} for t in payload.get("tags", [])[:5]]

        post_body: dict[str, Any] = {
            "posts": [{
                "title": title,
                "html": content,
                "status": "draft" if mode == "draft" else "published",
                "tags": tags,
            }],
        }

        api_url = f"{site_url}/ghost/api/admin/posts/"
        headers = {
            "Authorization": f"Ghost {token}",
            "Content-Type": "application/json",
        }

        def execute():
            resp = requests.post(
                api_url,
                headers=headers,
                json=post_body,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code in (401, 403):
                raise ExternalServiceError(
                    "Ghost API rejected (HTTP {resp.status_code}) — "
                    "check admin_api_key and site_url"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Ghost API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                resp_body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Ghost returned non-JSON response: {exc}"
                )
            posts_data = resp_body.get("posts", [])
            if not posts_data:
                raise ExternalServiceError("Ghost createPost returned empty posts")
            post_url = (posts_data[0].get("url") or "").strip()
            if not post_url:
                slug = posts_data[0].get("slug", "")
                post_url = f"{site_url}/{slug}/"
            return post_url

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
                adapter="ghost",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Ghost publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="ghost", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="published",
            adapter="ghost",
            platform="ghost",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
