"""Medium publishing via AppleScript + Brave browser (macOS only).

This adapter controls Brave directly via AppleScript, bypassing all
Cloudflare/CDP detection. It uses the clipboard to paste article content
into Medium's editor, then triggers publish via keyboard shortcuts.

Used as primary fallback when Medium Integration Token API is unavailable.

Tab tracking strategy: the new-story tab's (window_index, tab_index) are
captured atomically at creation time, then threaded through every helper.
This avoids the "find by URL substring" anti-pattern that silently targets
existing medium.com tabs (settings, help, etc.) instead of our editor tab.
"""

from __future__ import annotations

import platform
import subprocess
import time
import json
import uuid
from typing import Any

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher._util.logger import opencli_logger as log
from backlink_publisher.publishing.content_negotiation import extract_publish_html
from backlink_publisher.publishing.registry import Publisher
from .base import AdapterResult
from .link_attr_verifier import verify_link_attributes


def _json_log(**kwargs: Any) -> str:
    return json.dumps(kwargs)


def _check_macos() -> None:
    if platform.system() != "Darwin":
        raise DependencyError(
            "MediumBraveAdapter is macOS-only (requires AppleScript + Brave)"
        )


def _run_applescript(script: str, timeout: int = 60) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise ExternalServiceError(
            f"AppleScript failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _open_new_story_in_brave(wait_secs: int = 12) -> tuple[int, int, str]:
    """Open medium.com/new-story; return (win_idx, tab_idx, settled_url).

    All three values are captured inside a single osascript invocation so
    they're guaranteed consistent — win/tab indices belong to the same tab
    object as the URL. Callers use these indices for every subsequent
    operation, never relying on "find by URL" which would silently target
    existing medium.com tabs (settings, help pages, etc.).
    """
    script = f"""
tell application "Brave Browser"
    activate
    set newWin to front window
    set newWinIdx to index of newWin
    set newTab to make new tab at end of tabs of newWin with properties {{URL:"https://medium.com/new-story"}}
    set newTabIdx to (count tabs of newWin)
    set active tab index of newWin to newTabIdx
    set deadline to (current date) + {wait_secs}
    set settledURL to ""
    repeat while (current date) < deadline
        try
            set settledURL to URL of newTab
        on error
            set settledURL to ""
        end try
        if settledURL is not "" and settledURL is not "about:blank" then
            if settledURL contains "medium.com" then exit repeat
        end if
        delay 0.5
    end repeat
    return (newWinIdx as string) & "|" & (newTabIdx as string) & "|" & settledURL
end tell
"""
    raw = _run_applescript(script, timeout=30 + wait_secs)
    parts = raw.split("|", 2)
    if len(parts) != 3:
        raise ExternalServiceError(
            f"Unexpected response from open-story script: {raw!r}"
        )
    win_idx, tab_idx, url = int(parts[0]), int(parts[1]), parts[2]
    return win_idx, tab_idx, url


def _get_tab_url(win_idx: int, tab_idx: int) -> str:
    """Read URL of the specific tab we opened."""
    script = f"""
tell application "Brave Browser"
    return URL of tab {tab_idx} of window {win_idx}
end tell
"""
    return _run_applescript(script, timeout=10)


def _focus_tab(win_idx: int, tab_idx: int) -> None:
    """Bring Brave forward, put our window first, make our tab active.

    Call before any System Events keystroke so keys land in the editor.
    """
    script = f"""
tell application "Brave Browser"
    activate
    set targetWin to window {win_idx}
    set index of targetWin to 1
    set active tab index of targetWin to {tab_idx}
end tell
delay 0.3
"""
    _run_applescript(script, timeout=10)


def _tab_js(win_idx: int, tab_idx: int, js: str) -> str:
    """Execute JavaScript in our specific tab."""
    escaped = js.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "Brave Browser"
    set result to execute (tab {tab_idx} of window {win_idx}) javascript "{escaped}"
    return result
end tell
'''
    return _run_applescript(script, timeout=30)


def _set_clipboard(text: str) -> None:
    proc = subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=10)
    if proc.returncode != 0:
        raise ExternalServiceError("Failed to copy content to clipboard")


def _wait_for_editor(win_idx: int, tab_idx: int, max_wait: int = 20) -> bool:
    """Poll until Medium's title placeholder is present in our tab."""
    for _ in range(max_wait):
        try:
            url = _get_tab_url(win_idx, tab_idx)
            if "medium.com/m/signin" in url or "medium.com/signin" in url:
                return False
            result = _tab_js(
                win_idx, tab_idx,
                "document.querySelector('[data-testid=\"post-title\"], "
                "[class*=\"graf--title\"], h3[class*=\"title\"]') ? 'ready' : 'wait'"
            )
            if result == "ready":
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _fill_title(win_idx: int, tab_idx: int, title: str) -> None:
    """Click title element via JS, focus the editor tab, type via System Events."""
    _tab_js(
        win_idx, tab_idx,
        "var el = document.querySelector('[data-testid=\"post-title\"], "
        "[class*=\"graf--title\"], h3[class*=\"title\"]'); "
        "if(el){ el.click(); el.focus(); }"
    )
    time.sleep(0.3)
    _focus_tab(win_idx, tab_idx)
    time.sleep(0.2)
    escaped = title.replace('"', '\\"').replace("\\", "\\\\")
    subprocess.run(
        ["osascript", "-e",
         f'tell application "System Events" to tell process "Brave Browser" to keystroke "{escaped}"'],
        timeout=15,
    )
    time.sleep(0.3)
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to tell process "Brave Browser" to key code 36'],
        timeout=5,
    )
    time.sleep(0.5)


def _paste_body(win_idx: int, tab_idx: int, html_content: str) -> None:
    """Put HTML on clipboard, click body, focus tab, Cmd+V."""
    _set_clipboard(html_content)
    time.sleep(0.3)
    _tab_js(
        win_idx, tab_idx,
        "var b = document.querySelector('[data-testid=\"post-body\"], "
        ".section-inner, [class*=\"graf--p\"]'); "
        "if(b){ b.click(); b.focus(); }"
    )
    time.sleep(0.3)
    _focus_tab(win_idx, tab_idx)
    time.sleep(0.2)
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to tell process "Brave Browser"'
         ' to keystroke "v" using command down'],
        timeout=10,
    )
    time.sleep(2)


def _click_publish_menu(win_idx: int, tab_idx: int) -> None:
    clicked = _tab_js(
        win_idx, tab_idx,
        "var btns = Array.from(document.querySelectorAll('button'));"
        "var pub = btns.find(b => b.textContent.trim() === 'Publish');"
        "if(pub){ pub.click(); return 'clicked'; } return 'notfound';"
    )
    if clicked != "clicked":
        raise ExternalServiceError(
            "Could not find Publish button — editor may not have loaded correctly."
        )
    time.sleep(2)


def _click_publish_now(win_idx: int, tab_idx: int) -> None:
    _tab_js(
        win_idx, tab_idx,
        "var btns = Array.from(document.querySelectorAll('button'));"
        "var pub = btns.find(b => "
        "b.textContent.includes('Publish now') || b.textContent.includes('Publish'));"
        "if(pub){ pub.click(); return 'clicked'; } return 'notfound';"
    )
    time.sleep(3)


class MediumBraveAdapter(Publisher):
    """Publish to Medium via AppleScript-controlled Brave browser (macOS only)."""

    @classmethod
    def available(cls, config) -> bool:
        import platform as _p
        return _p.system() == "Darwin"

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        _check_macos()

        article_id = payload.get("id", str(uuid.uuid4())[:8])
        title = payload.get("title", "")
        content_html = extract_publish_html(payload, "medium")

        log.info(_json_log(adapter="medium-brave", phase="start", id=article_id))

        try:
            _run_applescript('tell application "Brave Browser" to return name', timeout=5)
        except Exception:
            raise ExternalServiceError(
                "Brave Browser is not running. Please open Brave and log in to Medium."
            )

        # Open new-story tab; capture its (win, tab) indices atomically.
        log.info(_json_log(adapter="medium-brave", phase="open-new-story", id=article_id))
        win_idx, tab_idx, url = _open_new_story_in_brave(wait_secs=12)
        log.info(_json_log(
            adapter="medium-brave", phase="tab-located",
            id=article_id, win=win_idx, tab=tab_idx, url=url,
        ))

        if not url or "medium.com" not in url:
            raise ExternalServiceError(
                f"New tab did not settle on a medium.com URL within 12s "
                f"(got {url!r}). Brave may be slow or a CAPTCHA intercepted."
            )
        if "signin" in url or "login" in url:
            raise ExternalServiceError(
                "Medium login required. Log in to medium.com in Brave, then retry."
            )
        if "medium.com/new-story" not in url and "medium.com/p/" not in url:
            raise ExternalServiceError(
                f"Unexpected URL after opening new story: {url}. "
                "Medium may have changed its URL structure or is showing a CAPTCHA."
            )

        log.info(_json_log(adapter="medium-brave", phase="wait-editor", id=article_id))
        if not _wait_for_editor(win_idx, tab_idx, max_wait=20):
            time.sleep(5)  # one extra chance if JS check failed

        log.info(_json_log(adapter="medium-brave", phase="fill-title", id=article_id))
        _fill_title(win_idx, tab_idx, title)

        log.info(_json_log(adapter="medium-brave", phase="paste-body", id=article_id))
        _paste_body(win_idx, tab_idx, content_html)

        if mode == "publish":
            log.info(_json_log(adapter="medium-brave", phase="publish", id=article_id))
            try:
                _click_publish_menu(win_idx, tab_idx)
                _click_publish_now(win_idx, tab_idx)
            except ExternalServiceError:
                log.info(_json_log(
                    adapter="medium-brave", phase="publish-fallback",
                    note="Publish button not found; story saved as draft",
                    id=article_id,
                ))
        else:
            log.info(_json_log(adapter="medium-brave", phase="save-draft", id=article_id))
            time.sleep(3)  # let autosave complete

        # Wait up to 20s for Medium to redirect away from /new-story.
        final_url = ""
        for _ in range(20):
            try:
                final_url = _get_tab_url(win_idx, tab_idx)
            except ExternalServiceError:
                break
            if mode == "publish":
                if "/new-story" not in final_url and "medium.com" in final_url:
                    break
            else:
                if "/p/" in final_url or "/edit" in final_url:
                    break
            time.sleep(1)
        log.info(_json_log(
            adapter="medium-brave", phase="done", id=article_id, url=final_url,
        ))

        if mode == "publish" and (
            "/new-story" in final_url or "medium.com" not in final_url
        ):
            raise ExternalServiceError(
                f"Medium did not redirect to a published-story URL "
                f"(still at {final_url!r}). The article may exist as a draft — "
                f"check medium.com/me/stories. Likely causes: 'Allow JavaScript "
                f"from Apple Events' disabled in Brave's View → Developer menu, "
                f"or Medium UI change."
            )

        if mode == "publish":
            meta: dict = {}
            if final_url:
                attr_check = verify_link_attributes(final_url)
                meta["link_attr_verification"] = attr_check
                ratio = attr_check.get("blank_ratio", 1.0)
                total = attr_check.get("total_anchors", 0)
                if attr_check.get("verification") == "ok" and total > 0 and ratio < 0.5:
                    log.warn(_json_log(
                        adapter="medium-brave", phase="attr-warn", id=article_id,
                        msg=(
                            f"Medium stripped target attributes: "
                            f"{attr_check['blank_anchors']}/{total} anchors "
                            "retain target=_blank"
                        ),
                    ))
            return AdapterResult(
                status="published",
                adapter="medium-brave",
                platform="medium",
                published_url=final_url,
                _provider_meta=meta if meta else None,
            )
        return AdapterResult(
            status="drafted",
            adapter="medium-brave",
            platform="medium",
            draft_url=final_url,
        )
