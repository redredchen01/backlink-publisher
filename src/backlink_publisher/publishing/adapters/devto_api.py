"""Dev.to adapter — publishes articles via the Forem/Dev.to REST API.

Plan 2026-05-21-003 Phase 2 Unit 7. Implements the R9 extension recipe.

IMPORTANT — NOFOLLOW NOTICE:

    Dev.to (and all Forem-based platforms) apply ``rel="nofollow ugc"`` to
    **every** outbound link in article bodies, regardless of account tier,
    post format, or publication status. This is enforced server-side and
    cannot be overridden by authors.

    This adapter is retained because Dev.to has measurable value for:
      - **Entity signal**: a published article on a high-DA domain (dev.to
        DA ~90+) is seen and crawled by Googlebot even without link equity.
      - **Referral traffic**: well-indexed posts drive real click-through.
      - **Syndication speed**: Dev.to Forem RSS is ingested by several
        news aggregators and newsletters that increase content reach.

    It is NOT appropriate for PageRank transfer. Operators building a
    dofollow backlink strategy should use Telegraph, GitHub Pages, or
    Blogger instead. Dev.to is registered with ``dofollow=False`` in the
    adapter table and is absent from the dofollow shortlist in
    ``docs/solutions/dofollow-platform-shortlist.md``.

Design choices:

  - **api-key header** — Dev.to uses a custom ``api-key: <key>`` header,
    NOT ``Authorization: Bearer``. Easy to get wrong; centralised in
    ``_required_headers``.
  - **Tags capped at 4** — Dev.to rejects articles with >4 tags (HTTP 422).
    The adapter silently truncates to 4 and logs the trim.
  - **canonical_url** — Dev.to natively supports ``article.canonical_url``.
    When ``payload.seo.canonical_url`` is present it is passed directly.
    Empty string → omit field (pure-backlink mode, no syndication marker).
  - **published=True** — drafts go to ``published=False`` path, but the
    adapter uses ``published=True`` by default so the article is immediately
    indexable. ``mode='draft'`` returns a sentinel without API call.
  - **No retries for 5xx** — Forem has no idempotency tokens; a timeout on
    POST /articles might mean the article was created. Only 429 is retried.
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from backlink_publisher.config import Config, load_devto_token
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


DEVTO_ARTICLES_API = "https://dev.to/api/articles"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 30
_MAX_TAGS = 4


def _required_headers(api_key: str) -> dict[str, str]:
    """Dev.to's required headers.

    IMPORTANT: Dev.to uses the custom ``api-key`` header, NOT the standard
    ``Authorization: Bearer`` pattern used by GitHub Pages, Blogger, etc.
    Always route through this helper to avoid mixing up the auth scheme.
    """
    return {
        "api-key": api_key,
        "Content-Type": "application/json",
    }


def _load_api_key(config: Config) -> str:
    """Return the API key, raising DependencyError when not configured."""
    token_path = config.devto_token_path
    data = load_devto_token(token_path)
    api_key = (data or {}).get("api_key", "").strip()
    if not api_key:
        raise DependencyError(
            "Dev.to API key not configured. "
            f"Write {{\"api_key\": \"<key>\"}} to {token_path} (chmod 600). "
            "Generate at https://dev.to/settings/extensions → DEV Community API Keys."
        )
    return api_key


def _build_article_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the Dev.to articles API request body.

    canonical_url (per-row opt-in): if ``seo.canonical_url`` is present and
    non-empty, it is passed as-is. Dev.to natively supports this field.
    Empty string → omit → pure backlink mode.

    Tags: capped at 4 (Dev.to platform limit). Tags are converted to lowercase
    and alphanumeric (Dev.to rejects tags with spaces or punctuation).
    """
    title = payload.get("title", "Untitled")
    body_markdown = (
        payload.get("content_markdown")
        or extract_publish_html(payload, "devto")
        or ""
    )

    raw_tags = payload.get("tags", [])
    # Dev.to tag format: lowercase, alphanumeric + hyphens only, max 4.
    tags = []
    for t in raw_tags:
        cleaned = "".join(
            ch if (ch.isalnum() or ch == "-") else ""
            for ch in str(t).lower()
        ).strip("-")
        if cleaned:
            tags.append(cleaned)
    tags = tags[:_MAX_TAGS]

    article: dict[str, Any] = {
        "title": title,
        "body_markdown": body_markdown,
        "published": True,
        "tags": tags,
    }

    # Mixed canonical (Plan 003 R6): pass-through schema-validated URL directly.
    # Dev.to natively supports canonical_url — no special escaping needed;
    # the field is a structured JSON string, not injected into HTML.
    canonical = payload.get("seo", {}).get("canonical_url") or None
    if canonical:
        article["canonical_url"] = canonical

    return {"article": article}


class DevtoAPIAdapter(Publisher):
    """Publishes Markdown articles to Dev.to via the Forem REST API.

    NOFOLLOW NOTICE: Dev.to applies ``rel=nofollow ugc`` to all outbound
    links server-side. This adapter's value is entity signal, referral
    traffic, and content syndication reach — not PageRank transfer. See
    ``docs/solutions/dofollow-platform-shortlist.md`` for the authoritative
    dofollow shortlist.

    link_attr_verifier results showing nofollow on Dev.to published_url
    are EXPECTED and should not be treated as anomalies.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        """Return True when devto-token.json exists with a non-empty api_key."""
        token_path = config.devto_token_path
        data = load_devto_token(token_path)
        if not data:
            return False
        return bool((data.get("api_key") or "").strip())

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="devto", phase="start", id=article_id)))

        api_key = _load_api_key(config)
        article_payload = _build_article_payload(payload)

        if mode == "draft":
            log.info(json.dumps(dict(
                adapter="devto", phase="draft-skip", id=article_id,
            )))
            return AdapterResult(
                status="drafted",
                adapter="devto",
                platform="devto",
                draft_url="https://dev.to/dashboard",
            )

        def execute():
            resp = requests.post(
                DEVTO_ARTICLES_API,
                headers=_required_headers(api_key),
                json=article_payload,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "Dev.to API key rejected (HTTP 401) — regenerate at "
                    "https://dev.to/settings/extensions and re-save to devto-token.json"
                )
            if resp.status_code == 422:
                try:
                    err_body = resp.json()
                    msg = (
                        err_body.get("error")
                        or str(err_body.get("errors", ""))
                        or resp.text[:200]
                    )
                except Exception:
                    msg = resp.text[:200]
                raise ExternalServiceError(
                    f"Dev.to rejected article (HTTP 422 — validation error): {msg}"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Dev.to API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Dev.to returned non-JSON response: {exc}"
                )
            published_url = body.get("url", "")
            if not published_url:
                # Fallback: construct from slug + username
                slug = body.get("slug", "")
                username = (body.get("user") or {}).get("username", "")
                if slug and username:
                    published_url = f"https://dev.to/{username}/{slug}"
            if not published_url:
                raise ExternalServiceError(
                    "Dev.to createArticle returned no URL — check API response shape"
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
                adapter="devto",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Dev.to publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="devto", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="published",
            adapter="devto",
            platform="devto",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
