"""Hashnode adapter — GraphQL publishPost via Personal Access Token.

Plan 2026-05-19-006 Unit 8. Second platform in the Phase 3 wave after
ghpages (Unit 7). Hashnode is the highest-DA blog-aggregator in the
dofollow shortlist — confirmed dofollow on the canonical post URL when
the author is verified.

Design choices:

  - **GraphQL single endpoint** — all reads/writes go through
    ``POST https://gql.hashnode.com/``. No REST path multiplexing, no
    versioned base URL drift to track.
  - **Authorization: <pat>** — NO ``Bearer `` prefix. This deviates from
    ghpages/blogger and matches Hashnode's documented contract. Easy
    to get wrong; the adapter centralises the header construction so
    callers never assemble it themselves.
  - **Publication-scoped** — every post belongs to a publication, not a
    user. Operators must supply ``publication_id`` in ``[hashnode]``;
    they look it up via the dashboard URL or ``query { publications }``.
  - **Markdown body** — Hashnode renders Markdown server-side. We pass
    ``contentMarkdown`` (preferred) or fall back to the rendered HTML
    via ``extract_publish_html`` for callers that only have HTML.
  - **No draft API in v1** — Hashnode does support drafts via
    ``createDraft`` but the publishPost mutation is the SEO-relevant
    path. ``mode='draft'`` returns a sentinel ``drafted`` result
    without calling the API, mirroring ghpages's same-name convention.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import requests

from pathlib import Path
from backlink_publisher.http import post as http_post

from backlink_publisher.config import Config, load_hashnode_token
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


HASHNODE_API = "https://gql.hashnode.com/"
_HTTP_TIMEOUT_S = 30
_PAYWALL_PROBE_TIMEOUT_S = 10
_PAYWALL_CACHE_TTL_S = 300  # 5-minute TTL per plan Unit 8 spec

# Module-level paywall probe cache: {token_hash: (result_str_or_None, timestamp)}
_paywall_cache: dict[str, tuple[str | None, float]] = {}

# GraphQL fragments kept module-level so tests can assert exact query shape.
ME_QUERY = "query { me { id username name } }"
_PUBLICATION_PROBE_QUERY = "{ me { publication { id name } } }"

PUBLISH_POST_MUTATION = """
mutation PublishPost($input: PublishPostInput!) {
  publishPost(input: $input) {
    post { id slug url }
  }
}
""".strip()


def _required_headers(token: str) -> dict[str, str]:
    """Hashnode's two mandatory headers.

    Note: ``Authorization`` carries the bare PAT — NO ``Bearer `` prefix.
    This is the most common integration mistake; we route every adapter
    call through this helper to keep the contract enforced in one place.
    """
    return {
        "Authorization": token,
        "Content-Type": "application/json",
    }


def _load_token(config: Config) -> str:
    """Return the PAT, raising DependencyError when not configured.

    Mirrors the ghpages pattern — fail loud at adapter entry rather than
    pushing failure deeper into the publish path.
    """
    data = load_hashnode_token(config.hashnode_token_path)
    token = (data or {}).get("token")
    if not token:
        raise DependencyError(
            "Hashnode PAT not configured. "
            f"Write {{\"token\": \"<pat>\"}} to {config.hashnode_token_path} "
            "(chmod 600). Generate at hashnode.com/settings/developer."
        )
    return token


def _build_publish_input(payload: dict[str, Any], publication_id: str) -> dict[str, Any]:
    """Build the ``PublishPostInput`` GraphQL variable.

    Hashnode's PublishPostInput accepts ``title``, ``contentMarkdown``,
    ``tags`` (array of ``{slug, name}`` — we send name only and let
    Hashnode resolve), ``publicationId`` (required), and
    ``originalArticleURL`` (optional canonical, Plan 2026-05-21-003
    Unit 2 — per-row Mixed canonical strategy). Body source priority:
    ``content_markdown`` (passthrough) → rendered HTML.
    """
    title = payload.get("title", "Untitled")
    body = (
        payload.get("content_markdown")
        or extract_publish_html(payload, "hashnode")
    )
    raw_tags = payload.get("tags", [])[:5]  # Hashnode caps at 5
    tags = [{"name": t, "slug": _tag_slug(t)} for t in raw_tags if t]

    publish_input: dict[str, Any] = {
        "title": title,
        "contentMarkdown": body,
        "publicationId": publication_id,
        "tags": tags,
    }

    # Mixed canonical (Plan 003 R2): pass-through schema-validated URL via
    # GraphQL variable. Empty string ``""`` and missing field both fall
    # through ``or None`` → omit from input → pure-backlink mode.
    canonical = payload.get("seo", {}).get("canonical_url") or None
    if canonical:
        publish_input["originalArticleURL"] = canonical

    return publish_input


def _tag_slug(name: str) -> str:
    """Lowercase alnum-dash slug for tag references.

    Hashnode treats unknown slugs as new tags. We do the minimum cleanup
    here — alphanumerics kept, everything else becomes a single dash.
    """
    cleaned = []
    last_dash = False
    for ch in name.lower():
        if ch.isalnum():
            cleaned.append(ch)
            last_dash = False
        elif not last_dash:
            cleaned.append("-")
            last_dash = True
    slug = "".join(cleaned).strip("-")
    return slug or "tag"


def _graphql_post(
    token: str,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    timeout: int = _HTTP_TIMEOUT_S,
) -> dict[str, Any]:
    """Single chokepoint for every Hashnode API call.

    Returns the parsed JSON body on HTTP 200 + non-empty ``data``.
    Status-code semantics:

      - 200 + ``data`` present → return body (caller checks ``errors``)
      - 200 + ``errors`` only  → ExternalServiceError, surfaces server msg
      - 401 → ExternalServiceError("token rejected ...") — auth-fixable
      - 429 → ExternalServiceError("rate-limited ...") — retryable via
              ``retry_transient_call`` (status appears in RETRYABLE set)
      - other non-200 → ExternalServiceError with status + first 200 chars
    """
    resp = http_post(
        HASHNODE_API,
        headers=_required_headers(token),
        json={"query": query, "variables": variables or {}},
        timeout=timeout,
    )
    if resp.status_code == 401:
        raise ExternalServiceError(
            "Hashnode PAT rejected (HTTP 401) — regenerate at "
            "hashnode.com/settings/developer and re-save to hashnode-token.json"
        )
    if resp.status_code != 200:
        raise ExternalServiceError(
            f"Hashnode GraphQL returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    try:
        body = resp.json()
    except ValueError as exc:
        raise ExternalServiceError(
            f"Hashnode returned non-JSON response: {exc}"
        )
    if body.get("errors") and not body.get("data"):
        # GraphQL-level error with no data — surface the first error message.
        msg = body["errors"][0].get("message", "unknown")
        raise ExternalServiceError(f"Hashnode GraphQL error: {msg}")
    return body


def _probe_hashnode_paywall(token: str) -> str | None:
    """Probe whether the Hashnode account is on the free-tier paywall.

    Plan 2026-05-21-003 Phase 2 Unit 8. Called at ``publish()`` entry point
    (NOT in ``available()``), preserving Publisher ABC contract.

    Strategy:
      - POST ``{ me { publication { id name } } }`` to gql.hashnode.com.
      - Pro tier: ``data.me.publication`` is a non-null object with an ``id``.
      - Free tier (paywalled): ``data.me.publication`` is null.
      - Network errors / 4xx / 5xx: return None — let the publishPost
        mutation decide (avoids false-positive paywall blocks).

    Cache: results are cached for 5 minutes keyed by SHA-256(token) so
    repeated ``publish()`` calls within the same process don't hammer the
    introspection endpoint. WebUI status endpoint shares this cache.

    Returns:
        None — Pro tier or probe inconclusive (network error / auth ambiguity).
        str  — Error message suitable for ExternalServiceError; indicates
               free-tier paywall detected.
    """
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = time.monotonic()
    cached = _paywall_cache.get(token_hash)
    if cached is not None:
        result, ts = cached
        if now - ts < _PAYWALL_CACHE_TTL_S:
            return result

    try:
        resp = http_post(
            HASHNODE_API,
            headers=_required_headers(token),
            json={"query": _PUBLICATION_PROBE_QUERY},
            timeout=_PAYWALL_PROBE_TIMEOUT_S,
        )
    except Exception:
        # Network error / timeout — don't block publish, let mutation decide.
        return None

    if resp.status_code != 200:
        # 4xx/5xx are network or auth issues — return None, don't misclassify.
        return None

    try:
        body = resp.json()
    except Exception:
        return None

    me = ((body or {}).get("data") or {}).get("me")
    if me is None:
        # GraphQL returned errors or missing data — inconclusive, don't block.
        return None

    publication = me.get("publication")
    if publication is None:
        # Free-tier: publication is explicitly null.
        error_msg = (
            "Hashnode GraphQL API requires Pro plan (2026-05-13). "
            "The publishPost mutation is unavailable for free-tier accounts. "
            "See https://hashnode.com/changelog/2026-05-13-graphql-api-paid-access"
        )
        _paywall_cache[token_hash] = (error_msg, now)
        return error_msg

    # Pro tier — publication object present.
    _paywall_cache[token_hash] = (None, now)
    return None


class HashnodeAPIAdapter(Publisher):
    """Publishes Markdown to a Hashnode publication via GraphQL."""

    def embed_banner(self, artifact_path: Path, alt: str) -> str | None:
        """Return ``None`` — route to dispatcher's source_url fallback.

        Plan 2026-05-20-004 Unit 4.  The plan originally proposed an
        ``uploadMedia`` GraphQL mutation, but probe at implementation
        time (2026-05-20) found Hashnode's free GraphQL API was retired
        on **2026-05-13** — see Hashnode's own changelog at
        ``hashnode.com/changelog/2026-05-13-graphql-api-paid-access``.
        The ``gql.hashnode.com`` endpoint now 301s to the paywall
        announcement; both publish and any media-upload mutation now
        require a Pro plan.

        Without a Pro subscription on the project's bench accounts, no
        ``uploadMedia`` (or alternative) mutation can be schema-
        introspected or contract-tested.  Shipping an unverified
        mutation name would land dead code that emits
        ``BannerUploadError`` on every row in non-strict mode — strictly
        worse than the explicit ``None`` opt-in to the source_url
        fallback the dispatcher already wires for writeas.

        Returning ``None`` is the writeas-style "considered but can't"
        signal (distinct from Medium's not-implementing).  Dispatcher
        prepends ``![alt](source_url)`` from ``banner.source_url`` and
        emits ``banner.source_url_fallback`` with ``reason=
        adapter_returned_none``.  The banner still appears in the
        published post — hosted on the upstream image-gen provider's
        CDN — at the cost of link rot when that CDN expires.

        When/if Hashnode documents a stable upload mutation (paid or
        otherwise) AND the project's bench accounts can verify it via
        introspection, swap this to a real upload implementation.
        That is a separate plan; do NOT re-litigate here.
        """
        del alt
        return None

    @classmethod
    def available(cls, config: Config) -> bool:
        # Config-presence check only; auth verified at publish time.
        return (
            config.hashnode is not None
            and bool(config.hashnode.publication_id)
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
            json.dumps(dict(adapter="hashnode", phase="start", id=article_id))
        )

        hn_cfg = config.hashnode
        if hn_cfg is None or not hn_cfg.publication_id:
            raise DependencyError(
                "Hashnode config missing. Add [hashnode] publication_id=\"<id>\" "
                "to config.toml."
            )

        token = _load_token(config)
        publish_input = _build_publish_input(payload, hn_cfg.publication_id)

        if mode == "draft":
            log.info(
                json.dumps(dict(
                    adapter="hashnode", phase="draft-skip", id=article_id,
                ))
            )
            # No predictable URL pre-publish (Hashnode assigns the slug at
            # publish time). Surface a placeholder marking the platform so
            # the WebUI / history records still carry routing info.
            return AdapterResult(
                status="drafted",
                adapter="hashnode",
                platform="hashnode",
                draft_url=f"hashnode://publication/{hn_cfg.publication_id}",
            )

        # Unit 8 paywall probe: check BEFORE attempting the publishPost mutation.
        # Probe is in publish() not available() to preserve Publisher ABC contract.
        # Draft mode is handled above — probe only runs for live publish.
        # On free-tier accounts, raise ExternalServiceError with a rich message
        # so publish-history failure_reason carries the full context.
        paywall_msg = _probe_hashnode_paywall(token)
        if paywall_msg is not None:
            raise ExternalServiceError(paywall_msg)

        def execute():
            body = _graphql_post(
                token, PUBLISH_POST_MUTATION, {"input": publish_input}
            )
            data = (body.get("data") or {}).get("publishPost") or {}
            post = data.get("post") or {}
            url = post.get("url")
            if not url:
                raise ExternalServiceError(
                    "Hashnode publishPost returned no URL — check "
                    "publication_id and tag slugs"
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
                adapter="hashnode",
            )
        except DependencyError:
            raise
        except ExternalServiceError:
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Hashnode publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(
            json.dumps(dict(
                adapter="hashnode", phase="done", id=article_id,
                elapsed_ms=elapsed,
            ))
        )
        return AdapterResult(
            status="published",
            adapter="hashnode",
            platform="hashnode",
            published_url=published,
        )
