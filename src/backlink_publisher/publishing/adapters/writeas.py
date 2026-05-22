"""Write.as adapter — REST publish via login-issued token.

Plan 2026-05-19-006 Unit 9. Third platform in the Phase 3 wave after
ghpages (Unit 7) and hashnode (Unit 8). Write.as is the minimalist
markdown blog host — small DA but very low publish friction and
empirically dofollow on collection-bound posts.

Design choices:

  - **REST single endpoint base** — ``https://write.as/api/`` for all
    calls. ``api_base`` is config-overridable to support self-hosted
    WriteFreely instances (same API surface).
  - **Authorization: Token <token>** — Write.as uses the ``Token`` scheme,
    NOT ``Bearer``. Yet another auth header dialect — third in the wave
    (ghpages=Bearer, hashnode=bare-PAT, writeas=Token). The
    ``_required_headers`` helper centralises this so callers never
    assemble it themselves.
  - **Login-issued token, not PAT** — operator obtains the token by
    POST-ing ``/api/auth/login`` once with username + password. The
    token rotates on logout but not on routine use, so verify is a pure
    read (no rotation under verify). Same read-only invariant as the
    blogger/hashnode/velog adapters.
  - **Collection-bound publish** — when ``collection_alias`` is set we
    POST to ``/api/collections/{alias}/posts`` which surfaces the post
    on the operator's named blog (``write.as/{alias}/{slug}``).
    Without it posts go to the user's default feed (less SEO value).
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests
from backlink_publisher.http import post as http_post

from backlink_publisher.config import Config, load_writeas_token
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


DEFAULT_API_BASE = "https://write.as/api"
_HTTP_TIMEOUT_S = 30


def _required_headers(token: str) -> dict[str, str]:
    """Write.as's two mandatory headers.

    Note the ``Token `` prefix (capital T, single space) — NOT ``Bearer``.
    Easy integration mistake; routed through this one helper to enforce.
    """
    return {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
    }


def _load_token(config: Config) -> str:
    """Return the access token, raising DependencyError when not configured."""
    data = load_writeas_token(config.writeas_token_path)
    token = (data or {}).get("token")
    if not token:
        raise DependencyError(
            "Write.as token not configured. "
            f"Write {{\"token\": \"<access_token>\"}} to {config.writeas_token_path} "
            "(chmod 600). Obtain by POSTing /api/auth/login or via writeas-login CLI."
        )
    return token


def _build_post_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Compose the Write.as POST body.

    Write.as accepts ``title`` (optional), ``body`` (markdown), ``font``
    (norm / sans / wrap / serif / mono), and ``lang`` (BCP-47).
    Body source priority: ``content_markdown`` → rendered HTML.

    Mixed canonical (Plan 003 R2): when payload carries a non-empty
    schema-validated ``seo.canonical_url``, prepend ``<link rel=canonical>``
    to the body. Markdown allows inline HTML; the link tag is delivered
    verbatim per the forwarder contract. SEO impact: Write.as renders
    body server-side without exposing post ``<head>`` to the operator,
    so this is best-effort cosmetic — Google's canonical resolver may
    not honor a body-level tag. Adapter does not escape the URL (schema
    gate already rejected control chars / quotes / angle brackets /
    non-http schemes).
    """
    title = payload.get("title", "")
    body = (
        payload.get("content_markdown")
        or extract_publish_html(payload, "writeas")
    )
    canonical = payload.get("seo", {}).get("canonical_url") or None
    if canonical:
        body = f'<link rel="canonical" href="{canonical}">\n\n{body}'
    lang = payload.get("language") or "en"
    out: dict[str, Any] = {"body": body, "font": "norm", "lang": lang}
    if title:
        out["title"] = title
    return out


def _publish_endpoint(api_base: str, collection_alias: str) -> str:
    """Resolve POST URL based on whether a collection is bound.

    - With collection: ``/api/collections/{alias}/posts`` — public on the
      operator's named blog at ``write.as/{alias}/{slug}``
    - Without: ``/api/posts`` — falls back to anonymous-style feed
    """
    base = api_base.rstrip("/")
    if collection_alias:
        return f"{base}/collections/{collection_alias}/posts"
    return f"{base}/posts"


def _published_url(api_base: str, collection_alias: str, slug: str) -> str:
    """Best-effort canonical URL for the published post.

    Write.as serves collection posts at ``{host}/{alias}/{slug}`` —
    derive the host from api_base by stripping ``/api``.
    """
    base = api_base.rstrip("/")
    if base.endswith("/api"):
        host = base[: -len("/api")]
    else:
        host = base
    if collection_alias:
        return f"{host}/{collection_alias}/{slug}"
    return f"{host}/{slug}"


class WriteAsAPIAdapter(Publisher):
    """Publishes Markdown posts to a Write.as collection via REST."""

    @classmethod
    def available(cls, config: Config) -> bool:
        # Config-presence check only; auth verified at publish time.
        # Note: collection_alias is optional — adapter still works for
        # uncategorized posts (lower SEO value but valid).
        return config.writeas is not None

    def embed_banner(self, artifact_path: Any, alt: str) -> str | None:
        """Plan 2026-05-20-004 Unit 1.  Write.as has no media-upload API
        in their public REST surface — posts accept Markdown including
        external ``![](url)`` references but the adapter cannot push
        bytes to Write.as's own CDN.

        Returning ``None`` is the explicit "opted in, but can't"
        signal: the publish-time dispatcher
        (``publishing.banner_dispatcher.apply``) routes to the
        ``source_url`` fallback branch which prepends the upstream
        provider's URL with a warning that the link may rot when the
        upstream CDN's TTL expires.  Distinct from Medium-style
        not-implementing — see AGENTS.md "Adding banner embedding to
        an adapter".
        """
        return None

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(
            json.dumps(dict(adapter="writeas", phase="start", id=article_id))
        )

        wa_cfg = config.writeas
        if wa_cfg is None:
            raise DependencyError(
                "Write.as config missing. Add [writeas] section to config.toml."
            )

        token = _load_token(config)
        api_base = wa_cfg.api_base or DEFAULT_API_BASE
        endpoint = _publish_endpoint(api_base, wa_cfg.collection_alias)
        body = _build_post_body(payload)

        if mode == "draft":
            log.info(
                json.dumps(dict(
                    adapter="writeas", phase="draft-skip", id=article_id,
                ))
            )
            # No predictable URL pre-publish (Write.as assigns the slug).
            return AdapterResult(
                status="drafted",
                adapter="writeas",
                platform="writeas",
                draft_url=f"writeas://collection/{wa_cfg.collection_alias or 'default'}",
            )

        def execute():
            resp = http_post(
                endpoint,
                headers=_required_headers(token),
                json=body,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "Write.as token rejected (HTTP 401) — re-login at "
                    "write.as and re-save to writeas-token.json"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Write.as POST returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                parsed = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Write.as returned non-JSON response: {exc}"
                )
            data = parsed.get("data") or {}
            if data.get("id") == "contentisblocked":
                raise ExternalServiceError(
                    "Write.as rejected content as blocked by site policy "
                    "(id=contentisblocked); review anchor URLs / payload body "
                    "for terms triggering Write.as anti-spam, or drop the "
                    "writeas target from this seed row"
                )
            slug = data.get("slug")
            if not slug:
                raise ExternalServiceError(
                    "Write.as POST returned no slug — check collection_alias "
                    "and ensure token has write permission for the collection"
                )
            # The API returns ``url`` for collection-bound posts but not
            # always for default-feed posts. Compute defensively.
            return data.get("url") or _published_url(
                api_base, wa_cfg.collection_alias, slug
            )

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
                adapter="writeas",
            )
        except DependencyError:
            raise
        except ExternalServiceError:
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Write.as publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(
            json.dumps(dict(
                adapter="writeas", phase="done", id=article_id,
                elapsed_ms=elapsed,
            ))
        )
        return AdapterResult(
            status="published",
            adapter="writeas",
            platform="writeas",
            published_url=published,
        )
