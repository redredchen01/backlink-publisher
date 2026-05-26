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


JUEJIN_API_BASE = "https://api.juejin.cn"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 30


def _load_cookies(config: Config) -> dict[str, str]:
    cred_file = config.config_dir / "juejin-credentials.json"
    if not cred_file.exists():
        raise DependencyError(
            f"掘金 credentials not found: {cred_file}\n"
            "Save cookies from a logged-in juejin.cn session. "
            "Format: {\"cookies\": [{\"name\": \"...\", \"value\": \"...\"}, ...]}"
        )
    mode = os.stat(cred_file).st_mode & 0o777
    if mode != 0o600:
        raise DependencyError(
            f"juejin-credentials.json must be 0600 (found {oct(mode)})"
        )
    try:
        raw = json.loads(cred_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise DependencyError(f"Cannot read 掘金 credentials: {exc}") from None

    cookie_list = raw.get("cookies", [])
    if not isinstance(cookie_list, list):
        raise DependencyError("掘金 credentials missing 'cookies' array")
    return {
        c["name"]: c["value"]
        for c in cookie_list
        if isinstance(c, dict) and "name" in c and "value" in c
    }


def _extract_csrf_token(cookies: dict[str, str]) -> str:
    return cookies.get("csrf_token", "") or cookies.get("X-CSRF-Token", "")


class JuejinAPIAdapter(Publisher):
    """Publishes to 掘金 (juejin.cn) via cookie-authenticated REST API.

    Authentication: Playwright-exported cookies from a logged-in juejin.cn
    session, stored in a 0600 JSON file (``juejin-credentials.json``).

    The unofficial article creation API (``/content_api/v1/article/create``)
    requires a valid CSRF token from the cookie jar. This adapter reads it
    automatically from the ``csrf_token`` cookie.

    Juejin does not modify outbound links server-side, so registered
    with ``dofollow=True``. Credentials must be refreshed periodically
    (cookie TTL is implementation-defined by 掘金).
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        cred_file = config.config_dir / "juejin-credentials.json"
        return cred_file.exists()

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="juejin", phase="start", id=article_id)))

        cookies = _load_cookies(config)
        csrf_token = _extract_csrf_token(cookies)

        title = payload.get("title", "Untitled")
        body = payload.get("content_markdown") or extract_publish_html(payload, "juejin") or ""
        tags = payload.get("tags", [])

        body_json: dict[str, Any] = {
            "title": title,
            "content": body,
            "tag_ids": tags[:5],
            "brief_content": "",
            "is_english": 0,
            "is_original": 1,
        }

        if mode == "draft":
            body_json["draft"] = 1

        headers = {
            "Content-Type": "application/json",
        }
        if csrf_token:
            headers["x-csrf-token"] = csrf_token

        api_url = f"{JUEJIN_API_BASE}/content_api/v1/article/create"

        def execute():
            resp = requests.post(
                api_url,
                headers=headers,
                cookies=cookies,
                json=body_json,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code == 401:
                raise ExternalServiceError(
                    "掘金 API rejected (HTTP 401) — cookies expired. "
                    "Re-export cookies from a logged-in session."
                )
            if resp.status_code not in (200,):
                raise ExternalServiceError(
                    f"掘金 API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                resp_body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"掘金 returned non-JSON response: {exc}"
                )
            err_no = resp_body.get("err_no", -1)
            err_msg = resp_body.get("err_msg", "")
            if err_no != 0:
                raise ExternalServiceError(
                    f"掘金 API error (err_no={err_no}): {err_msg}"
                )
            data = resp_body.get("data", {})
            article_url = (data.get("article_info") or {}).get("article_url", "")
            if not article_url:
                raise ExternalServiceError(
                    "掘金 createArticle returned no URL"
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
                adapter="juejin",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"掘金 publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="juejin", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="published",
            adapter="juejin",
            platform="juejin",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
