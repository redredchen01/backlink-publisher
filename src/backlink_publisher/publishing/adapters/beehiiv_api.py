from __future__ import annotations

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


BEEHIIV_API_BASE = "https://api.beehiiv.com/v2"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 30


class BeehiivAPIAdapter(Publisher):
    """Publishes to Beehiiv via REST API v2 (Bearer Token).

    Authentication: Beehiiv API key stored in a 0600 JSON file
    (``beehiiv-token.json``)::

        { "api_key": "<your-api-key>", "publication_id": "pub_<id>" }

    Uses ``POST /v2/publications/{publication_id}/posts`` with a
    ``body_content`` raw-HTML string (Beehiiv wraps it in an htmlSnippet
    block server-side). The created post is returned in a ``data``
    envelope: ``{"data": {"id": "post_..."}}``. ``status`` must be
    ``"draft"`` or ``"confirmed"`` ("confirmed" = go live) — "published"
    is not a valid Beehiiv status.

    NOTE: the Create Post endpoint is beta and Enterprise-only; non-
    Enterprise accounts get HTTP 403.

    Registered ``dofollow=False`` (2026-05-26 audit): Beehiiv routes
    outbound links through tracking redirects (bhclick.com /
    link.mail.beehiiv.com), so links do not transfer PageRank.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        cred_file = config.config_dir / "beehiiv-token.json"
        return cred_file.exists()

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="beehiiv", phase="start", id=article_id)))

        cred_file = config.config_dir / "beehiiv-token.json"
        if not cred_file.exists():
            raise DependencyError(
                "Beehiiv API key not configured.\n"
                f"Write {{\"api_key\": \"...\", \"publication_id\": \"...\"}} "
                f"to {cred_file} (chmod 600).\n"
                "Get the key from Beehiiv Dashboard → Settings → API."
            )

        try:
            creds = json.loads(cred_file.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise DependencyError(f"Cannot read Beehiiv credentials: {exc}") from None

        api_key = (creds.get("api_key") or "").strip()
        pub_id = (creds.get("publication_id") or "").strip()
        if not api_key or not pub_id:
            raise DependencyError(
                "Beehiiv credentials must contain 'api_key' and 'publication_id'"
            )

        title = payload.get("title", "Untitled")
        content = (
            payload.get("content_markdown")
            or extract_publish_html(payload, "beehiiv")
            or ""
        )

        # body_content is a raw-HTML string Beehiiv wraps in an htmlSnippet
        # block server-side (simpler + correct vs the old ad-hoc "content"
        # blocks array, which v2 rejects). status must be "draft" or
        # "confirmed" ("confirmed" = go live); the old "published" value is
        # not a valid Beehiiv status and was silently rejected.
        body: dict[str, Any] = {
            "title": title,
            "body_content": content,
            "status": "draft" if mode == "draft" else "confirmed",
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        api_url = f"{BEEHIIV_API_BASE}/publications/{pub_id}/posts"

        def execute():
            resp = requests.post(
                api_url,
                headers=headers,
                json=body,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "Beehiiv API rejected (HTTP 401) — check api_key"
                )
            if resp.status_code == 403:
                raise ExternalServiceError(
                    "Beehiiv API forbidden (HTTP 403) — the Create Post "
                    "endpoint is beta and Enterprise-only; this account "
                    "likely lacks access"
                )
            if resp.status_code == 404:
                raise ExternalServiceError(
                    "Beehiiv API returned 404 — check publication_id"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Beehiiv API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                resp_body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Beehiiv returned non-JSON response: {exc}"
                )
            # v2 wraps the created post in a "data" envelope:
            # {"data": {"id": "post_..."}}. Reading top-level "id" (the old
            # code) always missed it.
            data = resp_body.get("data") or {}
            post_id = (data.get("id") or "").strip()
            if not post_id:
                raise ExternalServiceError(
                    "Beehiiv createPost returned no post id in the data envelope"
                )
            return (
                data.get("web_url")
                or data.get("url")
                or f"https://app.beehiiv.com/posts/{post_id}"
            )

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
                adapter="beehiiv",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Beehiiv publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="beehiiv", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            # draft mode posts status="draft"; publish mode posts
            # status="confirmed" (Beehiiv's go-live value).
            status="drafted" if mode == "draft" else "published",
            adapter="beehiiv",
            platform="beehiiv",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
