from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from xmlrpc.client import Fault, ProtocolError, SafeTransport, ServerProxy

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.persistence import safe_write
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .http_form_post import attach_link_verification


_CRED_FILENAME = "cnblogs-credentials.json"
_HTTP_TIMEOUT_S = 30


def _credentials_path(config: Config) -> Path:
    return config.config_dir / _CRED_FILENAME


def store_credentials(config: Config, username: str, password: str) -> Path:
    if not username or not password:
        raise DependencyError(
            "CNBlogs credentials require both username and password"
        )
    path = _credentials_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write.atomic_write(
        path,
        json.dumps({"username": username, "password": password}, indent=2),
        mode=0o600,
    )
    mode = os.stat(path).st_mode & 0o777
    if mode != 0o600:
        os.chmod(path, 0o600)
    log.info("cnblogs_credentials_stored username_set=%s", bool(username))
    return path


def _load_credentials(config: Config) -> dict[str, str]:
    path = _credentials_path(config)
    if not path.exists():
        raise DependencyError(
            f"CNBlogs credentials not found: {path}\n"
            "Store them with cnblogs_api.store_credentials(config, username, password)."
        )
    mode = os.stat(path).st_mode & 0o777
    if mode != 0o600:
        raise DependencyError(
            f"{_CRED_FILENAME} must be 0600 (found {oct(mode)})\n"
            f"Run: chmod 600 {path}"
        )
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise DependencyError(f"Cannot parse CNBlogs credentials: {exc}") from None
    if not data.get("username") or not data.get("password"):
        raise DependencyError(
            f"{_CRED_FILENAME} missing 'username' or 'password' field"
        )
    return data


class _TimeoutTransport(SafeTransport):
    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class CNBlogsAPIAdapter(Publisher):
    def _metaweblog_url(self, username: str) -> str:
        return f"https://rpc.cnblogs.com/metaweblog/{username}"

    def _proxy(self, username: str) -> ServerProxy:
        return ServerProxy(
            self._metaweblog_url(username),
            transport=_TimeoutTransport(_HTTP_TIMEOUT_S),
            allow_none=True,
        )

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        article_id = payload.get("id", "")
        title = (payload.get("title") or "").strip() or "Untitled"
        body_html = extract_publish_html(payload, "cnblogs")
        if not body_html.strip():
            raise ExternalServiceError("CNBlogs payload is empty after rendering")

        creds = _load_credentials(config)
        username = creds["username"]
        password = creds["password"]

        proxy = self._proxy(username)
        log.info(json.dumps(dict(adapter="cnblogs", phase="start", id=article_id)))

        post_struct = {
            "title": title,
            "description": body_html,
            "categories": payload.get("tags", []),
        }

        try:
            if mode == "draft":
                result = proxy.metaWeblog.newPost(
                    "", username, password, post_struct, publish=False
                )
                post_id = str(result) if result else ""
                return AdapterResult(
                    status="drafted",
                    adapter="cnblogs-xmlrpc",
                    platform="cnblogs",
                    draft_url=f"https://i.cnblogs.com/posts/edit?postId={post_id}",
                )
            result = proxy.metaWeblog.newPost(
                "", username, password, post_struct, publish=True
            )
            post_id = str(result) if result else ""
            if not post_id:
                raise ExternalServiceError(
                    "CNBlogs metaWeblog.newPost returned no post ID"
                )
            published_url = self._post_url(username, post_id)
        except Fault as fault:
            raise ExternalServiceError(
                f"CNBlogs XML-RPC fault (faultCode={getattr(fault, 'faultCode', '?')})"
            ) from None
        except ProtocolError as exc:
            raise ExternalServiceError(
                f"CNBlogs XML-RPC protocol error (HTTP {exc.errcode})"
            ) from exc
        except (OSError, ConnectionError) as exc:
            raise ExternalServiceError(
                f"CNBlogs XML-RPC transport error ({type(exc).__name__})"
            ) from exc

        log.info(json.dumps(dict(
            adapter="cnblogs", phase="done", id=article_id, url=published_url,
        )))
        meta = attach_link_verification(published_url)
        return AdapterResult(
            status="published",
            adapter="cnblogs-xmlrpc",
            platform="cnblogs",
            published_url=published_url,
            _provider_meta=meta or None,
        )

    @staticmethod
    def _post_url(username: str, post_id: str) -> str:
        return f"https://www.cnblogs.com/{username}/p/{post_id}.html"
