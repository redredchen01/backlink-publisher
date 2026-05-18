"""Medium publishing via AppleScript + Brave browser (macOS only).

This adapter controls Brave directly via AppleScript, bypassing all
Cloudflare/CDP detection. It uses the clipboard to paste article content
into Medium's editor, then triggers publish via keyboard shortcuts.

Used as primary fallback when Medium Integration Token API is unavailable.

All AppleScript operations are pinned to the Medium tab specifically
(located each call by URL substring), not "active tab of front window" —
the latter was racy whenever Brave's focus drifted between osascript
invocations and could silently target the wrong tab (e.g., webui).
"""

from __future__ import annotations

import platform
import subprocess
import sys
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


def _open_new_story_in_brave(wait_secs: int = 10) -> str:
    """Open medium.com/new-story in Brave; return URL of the new tab.

    Polls the new tab's own URL inside the same AppleScript invocation so
    the returned value can't be confused by tab/window focus drift.
    """
    script = f"""
tell application "Brave Browser"
    activate
    set newWin to front window
    set newTab to make new tab at end of tabs of newWin with properties {{URL:"https://medium.com/new-story"}}
    set active tab index of newWin to (count tabs of newWin)
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
    return settledURL
end tell
"""
    return _run_applescript(script, timeout=30 + wait_secs)


def _find_medium_tab() -> tuple[int, int]:
    """Locate (window_index, tab_index) of the Medium tab by URL substring."""
    script = """
tell application "Brave Browser"
    set wIdx to 0
    repeat with w in windows
        set wIdx to wIdx + 1
        set tIdx to 0
        repeat with t in tabs of w
            set tIdx to tIdx + 1
            if URL of t contains "medium.com" then
                return (wIdx as string) & "," & (tIdx as string)
            end if
        end repeat
    end repeat
    return ""
end tell
"""
    result = _run_applescript(script, timeout=10)
    if not result or "," not in result:
        raise ExternalServiceError(
            "Medium tab no longer present in Brave (closed or navigated away)."
        )
    parts = result.split(",")
    return int(parts[0]), int(parts[1])


def _focus_medium_tab() -> tuple[int, int]:
    """Bring Brave forward, pin Medium window front, make Medium tab active."""
    win_idx, tab_idx = _find_medium_tab()
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
    return win_idx, tab_idx


def _set_clipboard(text: str) -> None:
    proc = subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=10)
    if proc.returncode != 0:
        raise ExternalServiceError("Failed to copy content to clipboard")


def _brave_js(js: str) -> str:
    """Execute JavaScript pinned to the Medium tab (located by URL each call)."""
    win_idx, tab_idx = _find_medium_tab()
    escaped = js.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "Brave Browser"
    set result to execute (tab {tab_idx} of window {win_idx}) javascript "{escaped}"
    return result
end tell
'''
    return _run_applescript(script, timeout=30)


def _get_medium_tab_url() -> str:
    """Read URL of the Medium tab specifically."""
    win_idx, tab_idx = _find_medium_tab()
    script = f'''
tell application "Brave Browser"
    return URL of tab {tab_idx} of window {win_idx}
end tell
'''
    return _run_applescript(script, timeout=10)


def _wait_for_medium_editor(max_wait: int = 20) -> bool:
    """Poll until Medium's editor is ready (title placeholder visible)."""
    for _ in range(max_wait):
        try:
            url = _get_medium_tab_url()
            if "medium.com/m/signin" in url or "medium.com/signin" in url:
                return False
            result = _brave_js(
                "document.querySelector('[data-testid=\"post-title\"], "
                "[class*=\"graf--title\"], h3[class*=\"title\"]') ? 'ready' : 'wait'"
            )
            if result == "ready":
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _click_title_and_type(title: str) -> None:
    """Click title element via JS, focus Medium tab, type via System Events."""
    _brave_js(
        "var el = document.querySelector('[data-testid=\"post-title\"], "
        "[class*=\"graf--title\"], h3[class*=\"title\"]'); "
        "if(el){ el.click(); el.focus(); }"
    )
    time.sleep(0.3)
    _focus_medium_tab()
    time.sleep(0.2)
    escaped_title = title.replace('"', '\\"').replace("\\", "\\\\")
    osascript_type = f'''
tell application "System Events"
    tell process "Brave Browser"
        keystroke "{escaped_title}"
    end tell
end tell
'''
    subprocess.run(["osascript", "-e", osascript_type], timeout=15)
    time.sleep(0.3)
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to tell process "Brave Browser" to key code 36'],
        timeout=5
    )
    time.sleep(0.5)


def _paste_body_content(html_content: str) -> None:
    """Put HTML on clipboard, click body via JS, focus Medium tab, Cmd+V."""
    _set_clipboard(html_content)
    time.sleep(0.3)
    _brave_js(
        "var body = document.querySelector('[data-testid=\"post-body\"], "
        ".section-inner, [class*=\"graf--p\"]'); "
        "if(body){ body.click(); body.focus(); }"
    )
    time.sleep(0.3)
    _focus_medium_tab()
    time.sleep(0.2)
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to tell process "Brave Browser"'
         ' to keystroke "v" using command down'],
        timeout=10
    )
    time.sleep(2)


def _click_publish_menu() -> None:
    """Click the Publish button in Medium editor."""
    clicked = _brave_js(
        "var btns = Array.from(document.querySelectorAll('button'));"
        "var pub = btns.find(b => b.textContent.trim() === 'Publish');"
        "if(pub){ pub.click(); return 'clicked'; } return 'notfound';"
    )
    if clicked != "clicked":
        raise ExternalServiceError(
            "Could not find Publish button in Medium editor. "
            "The editor may not have loaded correctly."
        )
    time.sleep(2)


def _click_publish_now() -> None:
    """Click 'Publish now' in the publish dialog."""
    _brave_js(
        "var btns = Array.from(document.querySelectorAll('button'));"
        "var pub = btns.find(b => "
        "b.textContent.includes('Publish now') || b.textContent.includes('Publish'));"
        "if(pub){ pub.click(); return 'clicked'; } return 'notfound';"
    )
    time.sleep(3)


def _save_draft_via_keyboard() -> None:
    """Medium auto-saves; wait for the autosave round-trip."""
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

        log.info(_json_log(adapter="medium-brave", phase="open-new-story", id=article_id))
        url = _open_new_story_in_brave(wait_secs=12)

        if not url or "medium.com" not in url:
            # Tab was created but Brave didn't report a settled URL in time;
            # fall back to URL-substring locate. The tab is there; just find it.
            try:
                url = _get_medium_tab_url()
            except ExternalServiceError:
                raise ExternalServiceError(
                    f"Could not locate the new Medium tab in Brave after open "
                    f"(tab.URL was {url!r}). Brave may be slow to load or a "
                    f"CAPTCHA intercepted the navigation."
                )

        if "signin" in url or "login" in url:
            raise ExternalServiceError(
                "Medium login required. Please log in to medium.com in Brave first, then retry."
            )

        if "medium.com/new-story" not in url and "medium.com/p/" not in url:
            raise ExternalServiceError(
                f"Unexpected URL after opening new story: {url}. "
                "Medium may have changed its URL structure or is showing a CAPTCHA."
            )

        log.info(_json_log(adapter="medium-brave", phase="wait-editor", id=article_id))
        ready = _wait_for_medium_editor(max_wait=15)
        if not ready:
            time.sleep(5)

        log.info(_json_log(adapter="medium-brave", phase="fill-title", id=article_id))
        _click_title_and_type(title)

        log.info(_json_log(adapter="medium-brave", phase="paste-body", id=article_id))
        _paste_body_content(content_html)

        if mode == "publish":
            log.info(_json_log(adapter="medium-brave", phase="publish", id=article_id))
            try:
                _click_publish_menu()
                _click_publish_now()
            except ExternalServiceError:
                log.info(_json_log(
                    adapter="medium-brave", phase="publish-fallback",
                    note="publish button not found, story saved as draft", id=article_id
                ))
        else:
            log.info(_json_log(adapter="medium-brave", phase="save-draft", id=article_id))
            _save_draft_via_keyboard()

        # Wait for Medium's editor → published-story redirect. Without this poll
        # we'd capture the /new-story URL and silently report success.
        final_url = ""
        for _ in range(20):
            try:
                final_url = _get_medium_tab_url()
            except ExternalServiceError:
                break
            if mode == "publish":
                if "/new-story" not in final_url and "medium.com" in final_url:
                    break
            else:
                if "/p/" in final_url or "/edit" in final_url:
                    break
            time.sleep(1)
        log.info(_json_log(adapter="medium-brave", phase="done", id=article_id, url=final_url))

        if mode == "publish" and (
            "/new-story" in final_url or "medium.com" not in final_url
        ):
            raise ExternalServiceError(
                f"Medium did not redirect to a published-story URL after publish "
                f"(still at {final_url!r}). The article may exist as a draft — "
                f"check medium.com/me/stories. Likely causes: focus stolen during "
                f"keystrokes, 'Allow JavaScript from Apple Events' disabled in "
                f"Brave's View → Developer menu, or Medium UI change."
            )

        if mode == "publish":
            meta: dict = {}
            if final_url:
                attr_check = verify_link_attributes(final_url)
                meta["link_attr_verification"] = attr_check
                ratio = attr_check.get("blank_ratio", 1.0)
                total = attr_check.get("total_anchors", 0)
                if attr_check.get("verification") == "ok" and total > 0 and ratio < 0.5:
                    log.warn(
                        _json_log(
                            adapter="medium-brave",
                            phase="attr-warn",
                            id=article_id,
                            msg=(
                                f"Medium stripped target attributes: "
                                f"{attr_check['blank_anchors']}/{total} anchors "
                                "retain target=_blank"
                            ),
                        )
                    )
            return AdapterResult(
                status="published",
                adapter="medium-brave",
                platform="medium",
                published_url=final_url,
                post_publish_delay_seconds=30,
                _provider_meta=meta if meta else None,
            )
        return AdapterResult(
            status="drafted",
            adapter="medium-brave",
            platform="medium",
            draft_url=final_url,
            post_publish_delay_seconds=30,
        )
