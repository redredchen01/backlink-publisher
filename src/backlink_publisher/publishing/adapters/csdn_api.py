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


CSDN_CREATE_API = "https://mp.csdn.net/mp_blog/creation/submit"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 60


def _load_cookies(config: Config) -> dict[str, str]:
    cred_file = config.config_dir / "csdn-credentials.json"
    if not cred_file.exists():
        raise DependencyError(
            f"CSDN credentials not found: {cred_file}\n"
            "Save cookies from a logged-in mp.csdn.net session. "
            "Format: {\"cookies\": [{\"name\": \"...\", \"value\": \"...\"}, ...]}"
        )
    mode = os.stat(cred_file).st_mode & 0o777
    if mode != 0o600:
        raise DependencyError(
            f"csdn-credentials.json must be 0600 (found {oct(mode)})"
        )
    try:
        raw = json.loads(cred_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise DependencyError(f"Cannot read CSDN credentials: {exc}") from None

    cookie_list = raw.get("cookies", [])
    if not isinstance(cookie_list, list):
        raise DependencyError("CSDN credentials missing 'cookies' array")
    return {
        c["name"]: c["value"]
        for c in cookie_list
        if isinstance(c, dict) and "name" in c and "value" in c
    }


class CSDNAPIAdapter(Publisher):
    """Publishes to CSDN via cookie-authenticated REST API.

    Authentication: Playwright-exported cookies from a logged-in
    ``mp.csdn.net`` session, stored in a 0600 JSON file.

    CSDN does not modify outbound links (observed server-side behaviour:
    HTML is stored and served verbatim). Registered with ``dofollow=True``.
    CSDN has high DA and strong indexing for Chinese-language content.

    The article submit endpoint accepts HTML content with a title and
    category. Tags are optional. A 60-second post-publish delay is
    recommended to avoid triggering rate limits.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        return (config.config_dir / "csdn-credentials.json").exists()

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="csdn", phase="start", id=article_id)))

        cookies = _load_cookies(config)

        title = payload.get("title", "Untitled")
        content = extract_publish_html(payload, "csdn")
        if not content.strip():
            raise ExternalServiceError("CSDN payload is empty after rendering")

        form_data: dict[str, Any] = {
            "title": title,
            "content": content,
            "channel": "",
            "description": "",
            "tags": "",
            "type": "original",
            "status": "publish" if mode == "publish" else "draft",
        }

        tags = payload.get("tags", [])
        if tags:
            form_data["tags"] = ",".join(str(t) for t in tags[:5])

        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": "https://mp.csdn.net/mp_blog/creation/editor",
        }

        def execute():
            resp = requests.post(
                CSDN_CREATE_API,
                headers=headers,
                cookies=cookies,
                data=form_data,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code in (401, 403):
                raise ExternalServiceError(
                    f"CSDN API rejected (HTTP {resp.status_code}) — cookies expired. "
                    "Re-export cookies from a logged-in mp.csdn.net session."
                )
            if resp.status_code not in (200,):
                raise ExternalServiceError(
                    f"CSDN API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                resp_body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"CSDN returned non-JSON response: {exc}"
                )
            if resp_body.get("status") is not True:
                msg = resp_body.get("message", resp.text[:200])
                raise ExternalServiceError(f"CSDN API error: {msg}")
            data = resp_body.get("data") or {}
            article_url = data.get("url", "")
            if not article_url:
                article_id_resp = data.get("id", "")
                if article_id_resp:
                    article_url = f"https://blog.csdn.net/{article_id_resp}"
            if not article_url:
                raise ExternalServiceError(
                    "CSDN submitArticle returned no URL"
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
                adapter="csdn",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"CSDN publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="csdn", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="drafted" if mode == "draft" else "published",
            adapter="csdn",
            platform="csdn",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
