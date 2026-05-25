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


WRITEAS_API_BASE = "https://write.as/api"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 5


def _load_token(config: Config) -> str:
    from backlink_publisher.config.tokens import _load_token as _load
    data = _load(config.writeas_token_path, "writeas-token.json")
    if not data:
        raise DependencyError(
            "Write.as token not configured. "
            f"Write {{\"token\": \"<api-token>\"}} "
            f"to {config.writeas_token_path} (chmod 600). "
            "Create at https://write.as/settings#term"
        )
    token = (data.get("token") or "").strip()
    if not token:
        raise DependencyError("Write.as token file missing 'token' field")
    return token


class WriteasAPIAdapter(Publisher):
    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        from backlink_publisher.config.tokens import _load_token as _load
        data = _load(config.writeas_token_path, "writeas-token.json")
        if not data:
            return False
        return bool((data.get("token") or "").strip())

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="writeas", phase="start", id=article_id)))

        token = _load_token(config)
        title = payload.get("title", "Untitled")
        body = payload.get("content_markdown") or extract_publish_html(payload, "writeas") or ""

        headers = {
            "Content-Type": "application/json",
        }
        # Write.as uses X-Writeas-Token header (not Bearer)
        if token:
            headers["X-Writeas-Token"] = token

        json_body: dict[str, Any] = {
            "title": title,
            "body": body,
        }

        if mode == "draft":
            json_body["pinned"] = False

        def execute():
            resp = http_post(
                f"{WRITEAS_API_BASE}/posts",
                headers=headers,
                json=json_body,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "Write.as token rejected (HTTP 401) — check your "
                    "api token at https://write.as/settings#term"
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Write.as API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                resp_body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Write.as returned non-JSON response: {exc}"
                )
            post_data = resp_body.get("data") or {}
            slug = post_data.get("slug", "")
            if not slug:
                raise ExternalServiceError(
                    "Write.as createPost returned no slug"
                )
            published_url = f"https://write.as/{slug}"
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
                adapter="writeas",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Write.as publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="writeas", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="published",
            adapter="writeas",
            platform="writeas",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
