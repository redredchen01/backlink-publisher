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


ZHIHU_API_BASE = "https://zhuanlan.zhihu.com/api"
_HTTP_TIMEOUT_S = 30
_POST_PUBLISH_DELAY_S = 60


def _load_cookies(config: Config) -> dict[str, str]:
    cred_file = config.config_dir / "zhihu-credentials.json"
    if not cred_file.exists():
        raise DependencyError(
            f"知乎 credentials not found: {cred_file}\n"
            "Save cookies from a logged-in zhihu.com session. "
            "Format: {\"cookies\": [{\"name\": \"...\", \"value\": \"...\"}, ...]}"
        )
    mode = os.stat(cred_file).st_mode & 0o777
    if mode != 0o600:
        raise DependencyError(
            f"zhihu-credentials.json must be 0600 (found {oct(mode)})"
        )
    try:
        raw = json.loads(cred_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise DependencyError(f"Cannot read 知乎 credentials: {exc}") from None

    cookie_list = raw.get("cookies", [])
    if not isinstance(cookie_list, list):
        raise DependencyError("知乎 credentials missing 'cookies' array")
    return {
        c["name"]: c["value"]
        for c in cookie_list
        if isinstance(c, dict) and "name" in c and "value" in c
    }


_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


class ZhihuAPIAdapter(Publisher):
    """Publishes to 知乎专栏 (zhuanlan.zhihu.com) via cookie-authenticated API.

    Authentication: Playwright-exported cookies from a logged-in zhihu.com
    session, stored in a 0600 JSON file (``zhihu-credentials.json``).

    The zhuanlan API (``POST /api/articles``) creates a column article.
    The request body is JSON with title, content (markdown or HTML), and
    tags. The adapter uses a browser-like User-Agent and Origin header to
    bypass basic anti-scraping gates.

    知乎 modifies outbound links with ``zbp`` tracking param but does NOT
    apply ``rel=nofollow`` server-side. Registered with ``dofollow=True``.
    Credentials must be refreshed periodically (cookie TTL ~7 days for
    zhihu.com sessions).
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        return (config.config_dir / "zhihu-credentials.json").exists()

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="zhihu", phase="start", id=article_id)))

        cookies = _load_cookies(config)

        title = payload.get("title", "Untitled")
        content = extract_publish_html(payload, "zhihu")
        tags = payload.get("tags", [])[:5]

        body_json: dict[str, Any] = {
            "title": title,
            "content": content,
            "tags": [{"name": t} for t in tags],
        }

        if mode == "draft":
            body_json["status"] = "draft"

        headers = {
            "Content-Type": "application/json",
            "User-Agent": _UA,
            "Origin": "https://zhuanlan.zhihu.com",
            "Referer": "https://zhuanlan.zhihu.com/write",
            "x-requested-with": "XMLHttpRequest",
        }

        api_url = f"{ZHIHU_API_BASE}/articles"

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
                    "知乎 API rejected (HTTP 401) — cookies expired. "
                    "Re-export cookies from a logged-in zhihu.com session."
                )
            if resp.status_code == 403:
                raise ExternalServiceError(
                    "知乎 API blocked (HTTP 403) — anti-scraping gate, "
                    "try updating User-Agent or re-exporting cookies."
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"知乎 API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                resp_body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"知乎 returned non-JSON response: {exc}"
                )
            article_url = resp_body.get("url", "")
            if not article_url:
                slug = resp_body.get("slug", "")
                if slug:
                    article_url = f"https://zhuanlan.zhihu.com/p/{slug}"
            if not article_url:
                raise ExternalServiceError(
                    "知乎 createArticle returned no URL"
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
                adapter="zhihu",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"知乎 publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="zhihu", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="published",
            adapter="zhihu",
            platform="zhihu",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
