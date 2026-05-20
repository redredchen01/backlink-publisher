"""Mastodon adapter — instance-scoped REST status post (Phase 4 scaffold).

**This adapter is NOT registered yet.** Gated on the Phase 3 dofollow
verification (≥2/3 of ghpages / hashnode / writeas). Once Phase 3 GO,
flip the commented line in ``publishing/adapters/__init__.py``.

Design choices:

  - **Per-instance endpoint** — Mastodon is federated. There's no single
    API base. ``instance_url`` (e.g. ``https://mastodon.social``) is
    required in config; the adapter posts to
    ``{instance_url}/api/v1/statuses``. Tokens are instance-scoped —
    useless on any other server.
  - **Bearer auth** — ``Authorization: Bearer <token>``. Token issued
    via ``{instance_url}/settings/applications`` with the ``write:statuses``
    scope.
  - **500-char limit on status text** — Mastodon's default cap. The
    adapter clips at 480 chars to leave room for the trailing
    backlink anchor (`` … <link>``). Operators wanting longer posts
    should use an instance with extended status length config.
  - **No content negotiation** — Mastodon renders status text with its
    own minimal Markdown subset (just URL auto-linking). We pass plain
    text; HTML payloads would render as raw text. Body source priority:
    ``summary`` → first 480 chars of stripped HTML content.
  - **Public visibility default** — for SEO surface. ``unlisted`` is
    the next sensible step down (no public timeline appearance but still
    crawlable). ``private``/``direct`` would defeat the purpose.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from backlink_publisher.config import Config, load_mastodon_token
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


_HTTP_TIMEOUT_S = 30
_MAX_STATUS_CHARS = 480  # Leave 20 chars of safety margin under the 500 cap
_VALID_VISIBILITY = {"public", "unlisted", "private", "direct"}


def _required_headers(token: str) -> dict[str, str]:
    """Mastodon's Bearer + form-urlencoded content type.

    Note: form-urlencoded, NOT JSON. The /api/v1/statuses endpoint accepts
    both but form is the documented stable contract; JSON has surfaced
    instance-version-specific parsing quirks historically.
    """
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _load_token(config: Config) -> str:
    """Return the access token, raising DependencyError when not configured."""
    data = load_mastodon_token(config.mastodon_token_path)
    token = (data or {}).get("token")
    if not token:
        raise DependencyError(
            "Mastodon access token not configured. "
            f"Write {{\"token\": \"<access_token>\"}} to {config.mastodon_token_path} "
            "(chmod 600). Generate at {instance}/settings/applications with "
            "scope `write:statuses`."
        )
    return token


def _strip_html(html: str) -> str:
    """Minimal HTML tag strip — Mastodon status is plain text only."""
    return re.sub(r"<[^>]+>", " ", html or "").strip()


def _build_status_text(payload: dict[str, Any]) -> str:
    """Compose the status body within Mastodon's 500-char ceiling.

    Body source priority: explicit ``summary`` → first chunk of stripped
    HTML. Always ends with the backlink URL on a fresh line so Mastodon's
    URL auto-linker turns it into a clickable (and crawlable) link.
    """
    target_url = payload.get("target_url", "")
    summary = payload.get("summary")
    if summary:
        text = summary.strip()
    else:
        html = extract_publish_html(payload, "mastodon")
        text = _strip_html(html)

    # Reserve room for trailing URL ("\n\n" + url).
    url_budget = len(target_url) + 2 if target_url else 0
    body_budget = _MAX_STATUS_CHARS - url_budget
    if len(text) > body_budget:
        text = text[: max(body_budget - 1, 0)].rstrip() + "…"

    if target_url:
        return f"{text}\n\n{target_url}"
    return text


def _publish_endpoint(instance_url: str) -> str:
    return f"{instance_url.rstrip('/')}/api/v1/statuses"


class MastodonAPIAdapter(Publisher):
    """Publishes short statuses to a Mastodon instance via REST.

    **Phase 4 scaffold — NOT registered.** See
    ``publishing/adapters/__init__.py`` for the commented register line.
    """

    @classmethod
    def available(cls, config: Config) -> bool:
        # Both instance_url presence AND the MastodonConfig dataclass.
        # Empty instance_url has no canonical fallback (no single Mastodon
        # endpoint exists — federation).
        return (
            config.mastodon is not None
            and bool(config.mastodon.instance_url)
        )

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(
            json.dumps(dict(adapter="mastodon", phase="start", id=article_id))
        )

        md_cfg = config.mastodon
        if md_cfg is None or not md_cfg.instance_url:
            raise DependencyError(
                "Mastodon config missing. Add [mastodon] instance_url=\"...\" "
                "to config.toml (e.g. \"https://mastodon.social\")."
            )

        visibility = md_cfg.visibility or "public"
        if visibility not in _VALID_VISIBILITY:
            raise DependencyError(
                f"Mastodon [mastodon] visibility=\"{visibility}\" invalid; "
                f"must be one of {sorted(_VALID_VISIBILITY)}"
            )

        token = _load_token(config)
        endpoint = _publish_endpoint(md_cfg.instance_url)
        status_text = _build_status_text(payload)
        form_body = {"status": status_text, "visibility": visibility}

        if mode == "draft":
            # Mastodon has no native draft state — operators with a
            # scheduling need use the ``scheduled_at`` field. For scaffold
            # parity with the other adapters, draft mode just short-circuits.
            log.info(
                json.dumps(dict(
                    adapter="mastodon", phase="draft-skip", id=article_id,
                ))
            )
            return AdapterResult(
                status="drafted",
                adapter="mastodon",
                platform="mastodon",
                draft_url=f"mastodon://{md_cfg.instance_url}/draft",
            )

        def execute():
            resp = requests.post(
                endpoint,
                headers=_required_headers(token),
                data=form_body,  # form-urlencoded per design note above
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    f"Mastodon access token rejected by {md_cfg.instance_url} "
                    "(HTTP 401) — re-issue at "
                    f"{md_cfg.instance_url}/settings/applications and "
                    "re-save to mastodon-token.json"
                )
            if resp.status_code == 403:
                raise ExternalServiceError(
                    "Mastodon POST forbidden (HTTP 403) — token missing "
                    "`write:statuses` scope or account silenced"
                )
            if resp.status_code == 422:
                raise ExternalServiceError(
                    f"Mastodon rejected status (HTTP 422): {resp.text[:200]} "
                    "— typically status text too long or unsupported visibility"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Mastodon POST returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                parsed = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Mastodon returned non-JSON response: {exc}"
                )
            url = parsed.get("url")
            if not url:
                raise ExternalServiceError(
                    "Mastodon POST returned no url field — API contract change?"
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
                adapter="mastodon",
            )
        except DependencyError:
            raise
        except ExternalServiceError:
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Mastodon publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(
            json.dumps(dict(
                adapter="mastodon", phase="done", id=article_id,
                elapsed_ms=elapsed,
            ))
        )
        return AdapterResult(
            status="published",
            adapter="mastodon",
            platform="mastodon",
            published_url=published,
        )
