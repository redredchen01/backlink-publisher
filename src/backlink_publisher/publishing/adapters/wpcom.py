"""WordPress.com adapter — wp/v2 REST + OAuth Bearer (Phase 4 scaffold).

**This adapter is NOT registered yet.** Gated on the Phase 3 dofollow
verification (≥2/3 of ghpages / hashnode / writeas). Once Phase 3 GO,
flip the commented line in ``publishing/adapters/__init__.py``.

Design choices:

  - **wp/v2 REST API** — ``POST {api_base}/wp/v2/sites/{site_id}/posts``.
    WP.com proxies through the same REST shape as self-hosted WordPress,
    so this adapter could be repointed at a Jetpack-bridged site by
    overriding ``api_base``.
  - **OAuth2 Bearer** — back to ``Authorization: Bearer <token>``. WP.com
    issues long-lived OAuth tokens via the developer portal at
    https://developer.wordpress.com/apps/. Token has full posting scope;
    revoke is via the same portal.
  - **HTML body, not Markdown** — wp/v2 ``content`` field accepts raw HTML.
    Markdown would be rendered as escaped text. We pass
    ``extract_publish_html`` (which Phase 3 ghpages/hashnode treat as a
    fallback; here it's the primary source).
  - **site_id required** — WP.com is per-site. ``site_id`` is either the
    numeric site ID (recommended — opaque, never changes) or the domain
    string (``example.wordpress.com``). Both forms work; the adapter does
    not validate which one is supplied.
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from backlink_publisher.config import Config, load_wpcom_token
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


DEFAULT_API_BASE = "https://public-api.wordpress.com"
_HTTP_TIMEOUT_S = 30


def _required_headers(token: str) -> dict[str, str]:
    """WP.com's OAuth Bearer + JSON content type."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _load_token(config: Config) -> str:
    """Return the OAuth token, raising DependencyError when not configured."""
    data = load_wpcom_token(config.wpcom_token_path)
    token = (data or {}).get("token")
    if not token:
        raise DependencyError(
            "WordPress.com OAuth token not configured. "
            f"Write {{\"token\": \"<oauth_bearer>\"}} to {config.wpcom_token_path} "
            "(chmod 600). Obtain via the OAuth flow at "
            "developer.wordpress.com/apps."
        )
    return token


def _build_post_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Compose the wp/v2 POST body.

    ``content`` is raw HTML (wp/v2 doesn't accept Markdown). ``status`` of
    ``publish`` triggers immediate public visibility; ``draft`` keeps it
    invisible (we use it for mode='draft' below).
    """
    title = payload.get("title", "Untitled")
    content_html = extract_publish_html(payload, "wpcom")
    tags = payload.get("tags", [])[:20]
    body: dict[str, Any] = {
        "title": title,
        "content": content_html,
        "status": "publish",
    }
    if tags:
        # wp/v2 accepts tag NAMES via a special endpoint, but the simpler
        # path is to send them as a comma-string in `metadata.tags_input`.
        # For scaffold, we attach to `meta` — operators with strict tag
        # taxonomies should pre-create tags and pass IDs via `tags=[...]`.
        body["meta"] = {"tags_input": ",".join(tags)}
    return body


def _publish_endpoint(api_base: str, site_id: str) -> str:
    base = api_base.rstrip("/")
    return f"{base}/wp/v2/sites/{site_id}/posts"


class WpcomAPIAdapter(Publisher):
    """Publishes HTML posts to WordPress.com via wp/v2 REST.

    **Phase 4 scaffold — NOT registered.** See
    ``publishing/adapters/__init__.py`` for the commented register line.
    """

    @classmethod
    def available(cls, config: Config) -> bool:
        # Both site_id presence AND the WpcomConfig dataclass instance.
        # Empty site_id is meaningless — endpoint would 404.
        return config.wpcom is not None and bool(config.wpcom.site_id)

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(
            json.dumps(dict(adapter="wpcom", phase="start", id=article_id))
        )

        wp_cfg = config.wpcom
        if wp_cfg is None or not wp_cfg.site_id:
            raise DependencyError(
                "WP.com config missing. Add [wpcom] site_id=\"...\" "
                "to config.toml (numeric ID or domain)."
            )

        token = _load_token(config)
        api_base = wp_cfg.api_base or DEFAULT_API_BASE
        endpoint = _publish_endpoint(api_base, wp_cfg.site_id)
        body = _build_post_body(payload)

        if mode == "draft":
            # Flip body status, then short-circuit instead of POSTing — same
            # convention as ghpages/hashnode/writeas: mode='draft' means
            # "build the body, do not network".
            body["status"] = "draft"
            log.info(
                json.dumps(dict(
                    adapter="wpcom", phase="draft-skip", id=article_id,
                ))
            )
            return AdapterResult(
                status="drafted",
                adapter="wpcom",
                platform="wpcom",
                draft_url=f"wpcom://site/{wp_cfg.site_id}",
            )

        def execute():
            resp = requests.post(
                endpoint,
                headers=_required_headers(token),
                json=body,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "WP.com OAuth token rejected (HTTP 401) — re-issue at "
                    "developer.wordpress.com/apps and re-save to wpcom-token.json"
                )
            if resp.status_code == 403:
                raise ExternalServiceError(
                    "WP.com POST forbidden (HTTP 403) — token missing posting "
                    "scope or site lacks editing permission"
                )
            if resp.status_code == 404:
                raise ExternalServiceError(
                    f"WP.com site_id={wp_cfg.site_id} not found (HTTP 404) — "
                    "verify the site ID via wordpress.com/sites"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"WP.com POST returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                parsed = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"WP.com returned non-JSON response: {exc}"
                )
            # wp/v2 returns ``link`` (canonical public URL) — guid is the
            # internal permalink which works but is less friendly.
            url = parsed.get("link") or parsed.get("guid", {}).get("rendered")
            if not url:
                raise ExternalServiceError(
                    "WP.com POST returned no link field — API contract change?"
                )
            return url

        try:
            published = retry_transient_call(
                execute,
                is_retryable=lambda exc: (
                    isinstance(exc, ExternalServiceError)
                    and any(
                        f"HTTP {code}" in str(exc)
                        for code in RETRYABLE_HTTP_STATUSES
                    )
                ),
                adapter="wpcom",
            )
        except DependencyError:
            raise
        except ExternalServiceError:
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"WP.com publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(
            json.dumps(dict(
                adapter="wpcom", phase="done", id=article_id,
                elapsed_ms=elapsed,
            ))
        )
        return AdapterResult(
            status="published",
            adapter="wpcom",
            platform="wpcom",
            published_url=published,
        )
