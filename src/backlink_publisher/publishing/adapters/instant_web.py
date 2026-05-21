"""No-login web-form publishers driven by real Chrome/CDP.

``telegra.ph`` and ``write.as/new`` both allow an operator to create a public
post directly from a browser page without a conventional login ceremony.  This
module groups those channels under the same implementation style: launch (or
attach to) a real Chrome DevTools session, fill the public composer, publish,
and return the URL now visible in the browser.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from backlink_publisher.cli._bind import chrome_backend as chrome
from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult


_HTTP_TIMEOUT_S = 3
_PUBLISH_TIMEOUT_S = 45


def _content_markdown(payload: dict[str, Any]) -> str:
    return str(payload.get("content_markdown") or payload.get("content") or "")


def _debug_base_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _cdp_available(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"{_debug_base_url(port)}/json/version",
            timeout=0.5,
        ) as resp:
            return getattr(resp, "status", 200) < 400
    except Exception:
        return False


class _ChromeSession:
    """Small real-Chrome/CDP session for one publish attempt."""

    def __init__(self) -> None:
        raw_port = os.environ.get("BACKLINK_PUBLISHER_REAL_CHROME_PORT")
        self.port = int(raw_port) if raw_port else chrome._find_free_port(chrome.DEFAULT_CHROME_PORT)
        self.proc: subprocess.Popen[Any] | None = None
        self.client: chrome._CdpClient | None = None
        self.page: chrome._CdpPage | None = None

    @classmethod
    def available(cls) -> bool:
        return chrome.discover_chrome_binary() is not None or _cdp_available(
            int(os.environ.get("BACKLINK_PUBLISHER_REAL_CHROME_PORT", "9222"))
        )

    def open(self, url: str) -> chrome._CdpPage:
        attach = os.environ.get("BACKLINK_PUBLISHER_REAL_CHROME_ATTACH") == "1"
        if not _cdp_available(self.port):
            chrome_bin = chrome.discover_chrome_binary()
            if not chrome_bin:
                raise DependencyError(
                    "real Chrome not available. Install Google Chrome or set "
                    "BACKLINK_PUBLISHER_REAL_CHROME_BIN."
                )
            profile = Path(os.environ.get(
                "BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR",
                str(Path.home() / ".config" / "backlink-publisher" / "real-chrome-profile" / f"instant-web-{self.port}"),
            )).expanduser()
            profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.proc = subprocess.Popen(
                [
                    chrome_bin,
                    f"--remote-debugging-port={self.port}",
                    "--remote-allow-origins=*",
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._wait_for_cdp()
        elif not attach:
            # A user-owned debugging Chrome may be running. Avoid hijacking it
            # unless the operator explicitly opted into attach mode.
            raise DependencyError(
                "Chrome DevTools port already in use. Set "
                "BACKLINK_PUBLISHER_REAL_CHROME_ATTACH=1 to attach to it, or "
                "close that Chrome and retry."
            )

        tab = self._open_tab(url)
        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            raise ExternalServiceError("Chrome CDP returned no websocket URL")
        self.client = chrome._CdpClient(str(ws_url))
        self.page = chrome._CdpPage(self.client, timeout_ms=int(_PUBLISH_TIMEOUT_S * 1000))
        self.page.goto(url)
        return self.page

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _wait_for_cdp(self) -> None:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise ExternalServiceError("Chrome exited before CDP became available")
            if _cdp_available(self.port):
                return
            time.sleep(0.1)
        raise ExternalServiceError("Chrome CDP did not become available")

    def _open_tab(self, url: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(url, safe=":/?&=%#")
        req = urllib.request.Request(
            f"{_debug_base_url(self.port)}/json/new?{encoded}",
            method="PUT",
        )
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            # Older Chrome builds used GET for /json/new.
            with urllib.request.urlopen(
                f"{_debug_base_url(self.port)}/json/new?{encoded}",
                timeout=_HTTP_TIMEOUT_S,
            ) as resp:
                return json.loads(resp.read().decode("utf-8"))


def _wait_for_url_change(page: chrome._CdpPage, original: str) -> str:
    deadline = time.monotonic() + _PUBLISH_TIMEOUT_S
    last = original
    while time.monotonic() < deadline:
        current = page.url
        if current and current != original and current != "about:blank":
            return current
        last = current
        time.sleep(0.5)
    raise ExternalServiceError(f"publish did not produce a new URL (last={last!r})")


class TelegraphCdpAdapter(Publisher):
    """Publish to telegra.ph through the public browser composer."""

    @classmethod
    def available(cls, config: Config) -> bool:  # noqa: ARG003
        return _ChromeSession.available()

    def publish(self, payload: dict[str, Any], mode: str, config: Config) -> AdapterResult:  # noqa: ARG002
        if mode == "draft":
            return AdapterResult(
                status="drafted",
                adapter="telegraph-cdp",
                platform="telegraph",
                draft_url="https://telegra.ph/",
            )

        title = str(payload.get("title") or "Untitled")
        body = _content_markdown(payload)
        session = _ChromeSession()
        try:
            page = session.open("https://telegra.ph/")
            page.wait_for_function(
                "() => !!document.querySelector('[contenteditable=\"true\"]')",
                timeout=15000,
            )
            page.evaluate(
                """
                (title, body) => {
                  const fields = [...document.querySelectorAll('[contenteditable="true"]')];
                  const titleEl = fields[0];
                  const bodyEl = fields[1] || fields[0];
                  titleEl.focus();
                  titleEl.textContent = title;
                  titleEl.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: title}));
                  bodyEl.focus();
                  bodyEl.textContent = body;
                  bodyEl.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: body}));
                  const button = [...document.querySelectorAll('button, a')]
                    .find((el) => /publish/i.test(el.textContent || ''));
                  if (!button) throw new Error('Telegraph publish button not found');
                  button.click();
                }
                """,
                title,
                body,
            )
            url = _wait_for_url_change(page, "https://telegra.ph/")
            log.info(json.dumps({"adapter": "telegraph-cdp", "phase": "done", "url": url}))
            return AdapterResult(
                status="published",
                adapter="telegraph-cdp",
                platform="telegraph",
                published_url=url,
            )
        except DependencyError:
            raise
        except Exception as exc:
            raise ExternalServiceError(f"Telegraph CDP publish failed: {exc}") from exc
        finally:
            session.close()


class WriteAsCdpAdapter(Publisher):
    """Publish to Write.as through the public browser composer session."""

    @classmethod
    def available(cls, config: Config) -> bool:  # noqa: ARG003
        return _ChromeSession.available()

    def publish(self, payload: dict[str, Any], mode: str, config: Config) -> AdapterResult:  # noqa: ARG002
        if mode == "draft":
            return AdapterResult(
                status="drafted",
                adapter="writeas-cdp",
                platform="writeas",
                draft_url="https://write.as/new",
            )

        title = str(payload.get("title") or "")
        body = _content_markdown(payload)
        session = _ChromeSession()
        try:
            page = session.open("https://write.as/pad")
            page.wait_for_function(
                "() => !!window.fetch && !!document.querySelector('#writer')",
                timeout=15000,
            )
            full_body = f"# {title}\n\n{body}" if title else body
            result = page.evaluate(
                """
                (async (body) => {
                  const writer = document.querySelector('#writer');
                  writer.value = body;
                  writer.dispatchEvent(new Event('input', {bubbles: true}));
                  writer.dispatchEvent(new Event('change', {bubbles: true}));
                  const response = await fetch('/api/posts', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({body, font: 'norm', lang: 'en'}),
                  });
                  const parsed = await response.json();
                  return {status: response.status, parsed};
                })
                """,
                full_body,
            )
            if not isinstance(result, dict):
                raise ExternalServiceError("Write.as returned no publish result")
            parsed = result.get("parsed") or {}
            data = parsed.get("data") or {}
            if data.get("id") == "contentisblocked":
                raise ExternalServiceError(
                    "Write.as rejected content as blocked by site policy "
                    "(id=contentisblocked); review anchor URLs / payload body "
                    "for terms triggering Write.as anti-spam, or drop the "
                    "writeas target from this seed row"
                )
            url = data.get("url") or data.get("full_post_url")
            if not url:
                raise ExternalServiceError(
                    f"Write.as returned no URL (HTTP {result.get('status')}): {parsed}"
                )
            log.info(json.dumps({"adapter": "writeas-cdp", "phase": "done", "url": url}))
            return AdapterResult(
                status="published",
                adapter="writeas-cdp",
                platform="writeas",
                published_url=url,
            )
        except DependencyError:
            raise
        except ExternalServiceError:
            # Adapter-raised diagnostics (e.g. site-policy rejection, no URL)
            # already carry actionable detail. Don't re-wrap and bury them.
            raise
        except Exception as exc:
            raise ExternalServiceError(f"Write.as CDP publish failed: {exc}") from exc
        finally:
            session.close()


__all__ = ["TelegraphCdpAdapter", "WriteAsCdpAdapter"]
