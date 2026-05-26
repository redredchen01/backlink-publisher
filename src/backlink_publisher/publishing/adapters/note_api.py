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
_POST_PUBLISH_DELAY_S = 30


def _load_cookies(config: Config) -> dict[str, str]:
    cred_file = config.config_dir / "note-credentials.json"
    if not cred_file.exists():
        raise DependencyError(
            f"Note.com credentials not found: {cred_file}\n"
            "Save cookies from a logged-in note.com session. "
            "Format: {\"cookies\": [{\"name\": \"...\", \"value\": \"...\"}, ...]}"
        )
    mode = os.stat(cred_file).st_mode & 0o777
    if mode != 0o600:
        raise DependencyError(
            f"note-credentials.json must be 0600 (found {oct(mode)})"
        )
    try:
        raw = json.loads(cred_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise DependencyError(f"Cannot read Note.com credentials: {exc}") from None

    cookie_list = raw.get("cookies", [])
    if not isinstance(cookie_list, list):
        raise DependencyError("Note.com credentials missing 'cookies' array")
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


class NoteAPIAdapter(Publisher):
    """Publishes to Note.com (note.com) via cookie-authenticated REST API.

    Authentication: Playwright-exported cookies from a logged-in note.com
    session, stored in a 0600 JSON file (``note-credentials.json``).

    Note.com does not modify outbound links in posts so registered with
    ``dofollow=True``. Note.com has a strong presence in the Japanese market.
    The adapter creates a note via the internal API with title, content
    (HTML), and category information.
    """

    post_publish_delay_seconds: int = _POST_PUBLISH_DELAY_S

    @classmethod
    def available(cls, config: Config) -> bool:
        return (config.config_dir / "note-credentials.json").exists()

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        t0 = time.monotonic()
        article_id = payload.get("id", "")
        log.info(json.dumps(dict(adapter="note", phase="start", id=article_id)))

        cookies = _load_cookies(config)

        title = payload.get("title", "Untitled")
        content = (
            payload.get("content_markdown")
            or extract_publish_html(payload, "note")
            or ""
        )
        tags = payload.get("tags", [])[:10]

        body_json: dict[str, Any] = {
            "note": {
                "name": title,
                "body": content,
                "status": "draft" if mode == "draft" else "published",
                "hashtag_names": tags,
            }
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": _UA,
            "Referer": "https://note.com/notes/new",
            "Accept": "application/json",
        }

        # Note.com creates notes via its internal API
        api_url = "https://note.com/api/v1/notes"

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
                    f"Note.com API rejected (HTTP {resp.status_code}) — "
                    "cookies expired. Re-export cookies from note.com."
                )
            if resp.status_code not in (200, 201):
                raise ExternalServiceError(
                    f"Note.com API returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                resp_body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    f"Note.com returned non-JSON response: {exc}"
                )
            data = resp_body.get("data", {})
            note_url = data.get("url", "") or data.get("note_url", "")
            if not note_url:
                note_id = data.get("id") or data.get("note_id", "")
                if note_id:
                    note_url = f"https://note.com/n/{note_id}"
            if not note_url:
                raise ExternalServiceError(
                    "Note.com createNote returned no URL"
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
                adapter="note",
            )
        except (DependencyError, ExternalServiceError):
            raise
        except Exception as exc:
            raise ExternalServiceError(
                f"Note.com publish failed ({type(exc).__name__}): {exc}"
            ) from exc

        elapsed = int((time.monotonic() - t0) * 1000)
        log.info(json.dumps(dict(
            adapter="note", phase="done", id=article_id, elapsed_ms=elapsed,
        )))
        return AdapterResult(
            status="drafted" if mode == "draft" else "published",
            adapter="note",
            platform="note",
            published_url=published_url,
            post_publish_delay_seconds=_POST_PUBLISH_DELAY_S,
        )
