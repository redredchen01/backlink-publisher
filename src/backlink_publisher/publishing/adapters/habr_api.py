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
    cred_file = config.config_dir / "habr-credentials.json"
    if not cred_file.exists():
        raise DependencyError(
            f"Habr credentials not found: {cred_file}\n"
            "Save cookies from a logged-in habr.com session. "
            "Format: {\"cookies\": [{\"name\": \"...\", \"value\": \"...\"}, ...]}"
        )
    mode = os.stat(cred_file).st_mode & 0o777
    if mode != 0o600:
        raise DependencyError(
            f"habr-credentials.json must be 0600 (found {oct(mode)})"
        )
    try:
        raw = json.loads(cred_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise DependencyError(f"Cannot read Habr credentials: {exc}") from None

    cookie_list = raw.get("cookies", [])
    if not isinstance(cookie_list, list):
        raise DependencyError("Habr credentials missing 'cookies' array")
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


class HabrAPIAdapter(Publisher):
    """Publishes to Habr (habr.com) via cookie-authenticated REST API.

    Authentication: Playwright-exported cookies from a logged-in habr.com
    session, stored in a 0600 JSON file (``habr-credentials.json``).

    Habr uses an internal API (``/kek/v1/...``) for creating posts. The
    adapter posts to the Habr API with title, content (HTML), tags, and
    post type. A browser-like User-Agent is used to bypass basic
    anti-scraping gates.

    Habr does not modify outbound links server-side so registered with
    ``dofollow=True``. Note: Habr's API language is Russian; endpoint
    paths may change. The adapter targets the ``/kek/v1/post/`` route
    which is the current known internal API.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        return (config.config_dir / "habr-credentials.json").exists()

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="habr", phase="start", id=article_id)))

        cookies = _load_cookies(config)

        title = payload.get("title", "Untitled")
        content = extract_publish_html(payload, "habr") or ""
        tags = payload.get("tags", [])[:10]

        body_json: dict[str, Any] = {
            "title": title,
            "text": content,
            "tags": [{"value": t} for t in tags],
            "isDraft": mode == "draft",
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": _UA,
            "Referer": "https://habr.com/ru/post/create/",
            "X-Requested-With": "XMLHttpRequest",
        }

        api_url = "https://habr.com/kek/v1/post/"

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
                    "Habr API rejected (HTTP {resp.status_code}) — "
                    "cookies expired. Re-export cookies from habr.com."
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Habr API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                resp_body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Habr returned non-JSON response: {exc}"
                )
            data = resp_body.get("data") or resp_body
            post_url = data.get("url") or data.get("link", "")
            if not post_url:
                post_id = data.get("id", "")
                if post_id:
                    post_url = f"https://habr.com/ru/post/{post_id}/"
            if not post_url:
                raise ExternalServiceError(
                    "Habr createPost returned no URL"
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
                adapter="habr",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Habr publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="habr", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="published",
            adapter="habr",
            platform="habr",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
