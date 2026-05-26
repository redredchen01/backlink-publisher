from __future__ import annotations

import json
import os
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
_POST_PUBLISH_DELAY_S = 60


def _load_cookies(config: Config) -> dict[str, str]:
    cred_file = config.config_dir / "segmentfault-credentials.json"
    if not cred_file.exists():
        raise DependencyError(
            f"SegmentFault credentials not found: {cred_file}\n"
            "Save cookies from a logged-in segmentfault.com session. "
            "Format: {\"cookies\": [{\"name\": \"...\", \"value\": \"...\"}, ...]}"
        )
    mode = os.stat(cred_file).st_mode & 0o777
    if mode != 0o600:
        raise DependencyError(
            f"segmentfault-credentials.json must be 0600 (found {oct(mode)})"
        )
    try:
        raw = json.loads(cred_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise DependencyError(f"Cannot read SegmentFault credentials: {exc}") from None

    cookie_list = raw.get("cookies", [])
    if not isinstance(cookie_list, list):
        raise DependencyError("SegmentFault credentials missing 'cookies' array")
    return {
        c["name"]: c["value"]
        for c in cookie_list
        if isinstance(c, dict) and "name" in c and "value" in c
    }


class SegmentFaultAPIAdapter(Publisher):
    """Publishes to SegmentFault (segmentfault.com) via cookie-authenticated API.

    Authentication: Playwright-exported cookies from a logged-in
    segmentfault.com session, stored in a 0600 JSON file.

    SegmentFault does not modify outbound links in articles so registered
    with ``dofollow=True``. Cookies must be refreshed periodically.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        return (config.config_dir / "segmentfault-credentials.json").exists()

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="segmentfault", phase="start", id=article_id)))

        cookies = _load_cookies(config)

        title = payload.get("title", "Untitled")
        content = extract_publish_html(payload, "segmentfault") or ""
        tags = payload.get("tags", [])[:5]

        body_json: dict[str, Any] = {
            "title": title,
            "content": content,
            "tags": tags,
            "status": "draft" if mode == "draft" else "publish",
        }

        headers = {
            "Content-Type": "application/json",
            "Referer": "https://segmentfault.com/write",
            "X-Requested-With": "XMLHttpRequest",
        }

        api_url = "https://segmentfault.com/api/articles/publish"

        def execute():
            resp = requests.post(
                api_url,
                headers=headers,
                cookies=cookies,
                json=body_json,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code in (401, 403):
                raise ExternalServiceError(
                    "SegmentFault API rejected (HTTP {resp.status_code}) — "
                    "cookies expired. Re-export cookies from segmentfault.com."
                )
            if resp.status_code not in (200,):
                raise ExternalServiceError(
                    f"SegmentFault API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                resp_body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"SegmentFault returned non-JSON response: {exc}"
                )
            code = resp_body.get("code", -1)
            if code != 0:
                msg = resp_body.get("message", resp.text[:200])
                raise ExternalServiceError(f"SegmentFault API error: {msg}")
            data = resp_body.get("data", {})
            article_url = data.get("url", "")
            if not article_url:
                article_id_resp = data.get("id", "")
                if article_id_resp:
                    article_url = f"https://segmentfault.com/a/{article_id_resp}"
            if not article_url:
                raise ExternalServiceError(
                    "SegmentFault createArticle returned no URL"
                )
            return article_url

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
                adapter="segmentfault",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"SegmentFault publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="segmentfault", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="drafted" if mode == "draft" else "published",
            adapter="segmentfault",
            platform="segmentfault",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
