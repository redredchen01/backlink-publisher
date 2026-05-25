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
    cred_file = config.config_dir / "pikabu-credentials.json"
    if not cred_file.exists():
        raise DependencyError(
            f"Pikabu credentials not found: {cred_file}\n"
            "Save cookies from a logged-in pikabu.ru session. "
            "Format: {\"cookies\": [{\"name\": \"...\", \"value\": \"...\"}, ...]}"
        )
    mode = os.stat(cred_file).st_mode & 0o777
    if mode != 0o600:
        raise DependencyError(
            f"pikabu-credentials.json must be 0600 (found {oct(mode)})"
        )
    try:
        raw = json.loads(cred_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise DependencyError(f"Cannot read Pikabu credentials: {exc}") from None

    cookie_list = raw.get("cookies", [])
    if not isinstance(cookie_list, list):
        raise DependencyError("Pikabu credentials missing 'cookies' array")
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


class PikabuAPIAdapter(Publisher):
    """Publishes to Pikabu (pikabu.ru) via cookie-authenticated REST API.

    Authentication: Playwright-exported cookies from a logged-in pikabu.ru
    session, stored in a 0600 JSON file (``pikabu-credentials.json``).

    Pikabu uses its internal API for story/community post creation. The
    adapter posts with title, HTML content, tags, and community information.
    A browser-like User-Agent and Referer are used.

    Pikabu does not apply ``rel=nofollow`` to outbound links server-side so
    registered with ``dofollow=True``. Note: Pikabu is a Russian platform;
    all content should be in Russian.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        return (config.config_dir / "pikabu-credentials.json").exists()

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="pikabu", phase="start", id=article_id)))

        cookies = _load_cookies(config)

        title = payload.get("title", "Untitled")
        content = extract_publish_html(payload, "pikabu") or ""
        tags = payload.get("tags", [])[:10]
        community = payload.get("meta", {}).get("community", "community")

        body_json: dict[str, Any] = {
            "title": title,
            "text": content,
            "tags": [{"name": t} for t in tags],
            "community": community,
            "isDraft": mode == "draft",
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": _UA,
            "Referer": "https://pikabu.ru/new",
            "X-Requested-With": "XMLHttpRequest",
        }

        api_url = "https://pikabu.ru/api/story/create"

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
                    "Pikabu API rejected (HTTP {resp.status_code}) — "
                    "cookies expired. Re-export cookies from pikabu.ru."
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Pikabu API returned HTTP {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
            try:
                resp_body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Pikabu returned non-JSON response: {exc}"
                )
            data = resp_body.get("data") or resp_body
            story_url = data.get("url", "") or data.get("link", "")
            if not story_url:
                story_id = data.get("id", "") or data.get("story_id", "")
                if story_id:
                    story_url = f"https://pikabu.ru/story/{story_id}"
            if not story_url:
                raise ExternalServiceError(
                    "Pikabu createStory returned no URL"
                )
            return story_url

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
                adapter="pikabu",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Pikabu publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="pikabu", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="published",
            adapter="pikabu",
            platform="pikabu",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
