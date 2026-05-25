from __future__ import annotations

import json
import time
from typing import Any

from backlink_publisher.http import post as http_post

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .retry import RETRYABLE_HTTP_STATUSES, retry_transient_call


HASHNODE_GQL_API = "https://gql.hashnode.com/"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 15


def _load_credentials(config: Config) -> dict[str, str]:
    from backlink_publisher.config.tokens import _load_token
    data = _load_token(config.hashnode_token_path, "hashnode-token.json")
    if not data:
        raise DependencyError(
            "Hashnode token not configured. "
            f"Write {{\"personal_access_token\": \"<pat>\", \"publication_id\": \"<pub-id>\"}} "
            f"to {config.hashnode_token_path} (chmod 600). "
            "Get your PAT at https://hashnode.com/settings/developer."
        )
    pat = (data.get("personal_access_token") or "").strip()
    pub_id = (data.get("publication_id") or "").strip()
    if not pat or not pub_id:
        raise DependencyError(
            "Hashnode token file missing 'personal_access_token' or 'publication_id' field"
        )
    return {"personal_access_token": pat, "publication_id": pub_id}


_PUBLISH_MUTATION = """
mutation PublishPost($input: PublishPostInput!) {
  publishPost(input: $input) {
    post {
      url
    }
  }
}
"""


class HashnodeGraphQLAdapter(Publisher):
    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        from backlink_publisher.config.tokens import _load_token
        data = _load_token(config.hashnode_token_path, "hashnode-token.json")
        if not data:
            return False
        return bool(
            (data.get("personal_access_token") or "").strip()
            and (data.get("publication_id") or "").strip()
        )

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="hashnode", phase="start", id=article_id)))

        creds = _load_credentials(config)
        pat = creds["personal_access_token"]
        pub_id = creds["publication_id"]

        title = payload.get("title", "Untitled")
        content_markdown = payload.get("content_markdown") or ""
        body_html = extract_publish_html(payload, "hashnode")
        tags = payload.get("tags", [])[:5]

        variables: dict[str, Any] = {
            "input": {
                "title": title,
                "contentMarkdown": content_markdown or body_html,
                "publicationId": pub_id,
                "tags": [{"slug": t.lower(), "name": t} for t in tags if t],
            }
        }

        if mode == "draft":
            variables["input"]["hideFromHashnodeFeed"] = True

        gql_payload = {"query": _PUBLISH_MUTATION, "variables": variables}
        headers = {
            "Authorization": f"Bearer {pat}",
            "Content-Type": "application/json",
        }

        def execute():
            resp = http_post(
                HASHNODE_GQL_API,
                headers=headers,
                json=gql_payload,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "Hashnode PAT rejected (HTTP 401) — regenerate at "
                    "https://hashnode.com/settings/developer"
                )
            if resp.status_code not in (200,):
                raise ExternalServiceError(
                    f"Hashnode GraphQL returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Hashnode returned non-JSON response: {exc}"
                )
            gql_errors = body.get("errors")
            if gql_errors:
                msg = gql_errors[0].get("message", str(gql_errors))
                raise ExternalServiceError(f"Hashnode GraphQL error: {msg}")
            post_url = (
                (body.get("data") or {})
                .get("publishPost", {})
                .get("post", {})
                .get("url", "")
            )
            if not post_url:
                raise ExternalServiceError(
                    "Hashnode publishPost returned no URL"
                )
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
                adapter="hashnode",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Hashnode publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="hashnode", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="published",
            adapter="hashnode-gql",
            platform="hashnode",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
