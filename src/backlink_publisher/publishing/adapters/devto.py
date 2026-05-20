"""Dev.to adapter — REST publish via api-key header (Phase 4 scaffold).

**This adapter is NOT registered yet.** It exists to be flipped on once the
Phase 3 dofollow gate (≥2/3 of ghpages / hashnode / writeas) passes. Until
then, leaving it unregistered keeps the publish pipeline blind to it.

Design choices (mirroring the Phase 3 wave):

  - **REST single endpoint** — ``POST {api_base}/articles`` for all publishes.
    ``api_base`` is config-overridable for forge testing.
  - **api-key header** — Dev.to uses ``api-key: <key>``, NOT ``Bearer``.
    Fourth auth dialect after ghpages/hashnode/writeas — keep the
    ``_required_headers`` helper as the single point of truth.
  - **Article body is Markdown** — Dev.to renders Markdown server-side via
    its own pipeline. We pass ``body_markdown``; HTML payloads are not
    accepted by the API (would render as escaped text).
  - **No collection / publication routing** — articles publish under the
    authenticated user's account. No per-publish config needed beyond the
    config-presence signal.
  - **403 vs 401 split** — Dev.to returns 401 for invalid key, 403 for
    rate-limited or missing scope. Surface both distinctly so live verify
    (gated, to be added) never falsely flags an expired token.
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


DEFAULT_API_BASE = "https://dev.to/api"
_HTTP_TIMEOUT_S = 30


def _required_headers(api_key: str) -> dict[str, str]:
    """Dev.to's two mandatory headers.

    Note ``api-key`` (lowercase, hyphen) — NOT ``Authorization``. Easy
    integration mistake; routed through this one helper to enforce.
    """
    return {
        "api-key": api_key,
        "Content-Type": "application/json",
    }


def _load_token(config: Config) -> str:
    """Return the API key, raising DependencyError when not configured."""
    data = load_devto_token(config.devto_token_path)
    token = (data or {}).get("token")
    if not token:
        raise DependencyError(
            "Dev.to API key not configured. "
            f"Write {{\"token\": \"<api-key>\"}} to {config.devto_token_path} "
            "(chmod 600). Generate at dev.to → settings → extensions → API keys."
        )
    return token


def _build_article_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Compose the Dev.to ``POST /articles`` body.

    The article wrapper is required (``{"article": {...}}``). Dev.to
    enforces a Markdown body — we prefer ``content_markdown`` and fall
    back to ``extract_publish_html`` only as a last resort (Dev.to will
    escape HTML, defeating the purpose, but better than a 400).
    """
    title = payload.get("title", "Untitled")
    body_md = (
        payload.get("content_markdown")
        or extract_publish_html(payload, "devto")
    )
    tags = payload.get("tags", [])[:4]  # Dev.to caps at 4 tags
    article: dict[str, Any] = {
        "title": title,
        "body_markdown": body_md,
        "published": True,
    }
    if tags:
        # Dev.to expects lowercase, alpha-only tag names. Sanitize defensively.
        article["tags"] = [_sanitize_tag(t) for t in tags if t.strip()]
    return {"article": article}


def _sanitize_tag(name: str) -> str:
    """Lowercase ASCII letters/digits — Dev.to rejects anything else."""
    return "".join(ch for ch in name.lower() if ch.isalnum())[:30]


class DevToAPIAdapter(Publisher):
    """Publishes Markdown articles to Dev.to via REST.

    **Phase 4 scaffold — NOT registered.** See
    ``publishing/adapters/__init__.py`` for the commented register line.
    """

    @classmethod
    def available(cls, config: Config) -> bool:
        # Config-presence check only; auth verified at publish time.
        return config.devto is not None

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(
            json.dumps(dict(adapter="devto", phase="start", id=article_id))
        )

        dt_cfg = config.devto
        if dt_cfg is None:
            raise DependencyError(
                "Dev.to config missing. Add [devto] section to config.toml."
            )

        token = _load_token(config)
        api_base = dt_cfg.api_base or DEFAULT_API_BASE
        endpoint = f"{api_base.rstrip('/')}/articles"
        body = _build_article_body(payload)

        if mode == "draft":
            # Dev.to has a true draft state (published=False), but for
            # mode='draft' we skip the network entirely — same convention as
            # the Phase 3 adapters.
            log.info(
                json.dumps(dict(
                    adapter="devto", phase="draft-skip", id=article_id,
                ))
            )
            return AdapterResult(
                status="drafted",
                adapter="devto",
                platform="devto",
                draft_url="devto://user/draft",
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
                    "Dev.to API key rejected (HTTP 401) — regenerate at "
                    "dev.to/settings/extensions and re-save to devto-token.json"
                )
            if resp.status_code == 403:
                raise ExternalServiceError(
                    "Dev.to POST forbidden (HTTP 403) — likely rate-limited "
                    "or key missing write scope"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Dev.to POST returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                parsed = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Dev.to returned non-JSON response: {exc}"
                )
            url = parsed.get("url")
            if not url:
                raise ExternalServiceError(
                    "Dev.to POST returned no url field — API contract change?"
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
                adapter="devto",
            )
        except DependencyError:
            raise
        except ExternalServiceError:
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Dev.to publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(
            json.dumps(dict(
                adapter="devto", phase="done", id=article_id,
                elapsed_ms=elapsed,
            ))
        )
        return AdapterResult(
            status="published",
            adapter="devto",
            platform="devto",
            published_url=published,
        )
