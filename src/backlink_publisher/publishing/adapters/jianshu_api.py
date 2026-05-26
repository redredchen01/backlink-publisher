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
    cred_file = config.config_dir / "jianshu-credentials.json"
    if not cred_file.exists():
        raise DependencyError(
            f"简书 credentials not found: {cred_file}\n"
            "Save cookies from a logged-in jianshu.com session. "
            "Format: {\"cookies\": [{\"name\": \"...\", \"value\": \"...\"}, ...]}"
        )
    mode = os.stat(cred_file).st_mode & 0o777
    if mode != 0o600:
        raise DependencyError(
            f"jianshu-credentials.json must be 0600 (found {oct(mode)})"
        )
    try:
        raw = json.loads(cred_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise DependencyError(f"Cannot read 简书 credentials: {exc}") from None

    cookie_list = raw.get("cookies", [])
    if not isinstance(cookie_list, list):
        raise DependencyError("简书 credentials missing 'cookies' array")
    return {
        c["name"]: c["value"]
        for c in cookie_list
        if isinstance(c, dict) and "name" in c and "value" in c
    }


class JianshuAPIAdapter(Publisher):
    """Publishes to 简书 (jianshu.com) via cookie-authenticated REST API.

    Authentication: Playwright-exported cookies from a logged-in
    jianshu.com session, stored in a 0600 JSON file.

    The adapter uses the internal API (``POST /author/notes``) to create
    a note with title, content (HTML), and optional tags.

    简书 does not modify outbound links server-side so registered with
    ``dofollow=True``. Cookies must be refreshed periodically (TTL is
    implementation-defined by 简书).
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        return (config.config_dir / "jianshu-credentials.json").exists()

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="jianshu", phase="start", id=article_id)))

        cookies = _load_cookies(config)

        title = payload.get("title", "Untitled")
        content = extract_publish_html(payload, "jianshu") or ""
        tags = payload.get("tags", [])[:5]

        form_data: list[tuple[str, str]] = [
            ("note[title]", title),
            ("note[content]", content),
            ("note[status]", "draft" if mode == "draft" else "publish"),
        ]
        for tag in tags:
            form_data.append(("note[tag_names][]", tag))

        headers = {
            "Referer": "https://www.jianshu.com/writer",
            "X-Requested-With": "XMLHttpRequest",
        }

        api_url = "https://www.jianshu.com/author/notes"

        def execute():
            resp = requests.post(
                api_url,
                headers=headers,
                cookies=cookies,
                data=form_data,
                timeout=_HTTP_TIMEOUT_S,
            )
            if resp.status_code in (401, 403):
                raise ExternalServiceError(
                    "简书 API rejected (HTTP {resp.status_code}) — "
                    "cookies expired. Re-export cookies from jianshu.com."
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"简书 API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                resp_body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"简书 returned non-JSON response: {exc}"
                )
            note_url = resp_body.get("url", "")
            if not note_url:
                note_id = resp_body.get("id", "")
                if note_id:
                    slug = resp_body.get("slug", note_id)
                    note_url = f"https://www.jianshu.com/p/{slug}"
            if not note_url:
                raise ExternalServiceError(
                    "简书 createNote returned no URL"
                )
            return note_url

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
                adapter="jianshu",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"简书 publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="jianshu", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="published",
            adapter="jianshu",
            platform="jianshu",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
