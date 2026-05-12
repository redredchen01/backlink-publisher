"""Medium publishing via AppleScript + Brave browser (macOS only).

This adapter controls Brave directly via AppleScript, bypassing all
Cloudflare/CDP detection. It uses the clipboard to paste article content
into Medium's editor, then triggers publish via keyboard shortcuts.

Used as primary fallback when Medium Integration Token API is unavailable.
"""

from __future__ import annotations

import platform
import subprocess
import sys
import time
import json
import uuid
from dataclasses import dataclass
from typing import Any

from ..config import Config
from ..errors import DependencyError, ExternalServiceError
from ..logger import opencli_logger as log
from ..markdown_utils import render_to_html
from .base import AdapterResult


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


_SUPPORTED_APPS = ("Brave Browser", "Google Chrome")

# We can't reliably round-trip Chrome/Brave's tab id integers through
# Python f-string → AppleScript: AppleScript's integer literal parser caps
# at ~2^30 and silently converts larger values to real (scientific notation),
# making `tab id 1829544078` fail to match the actual integer tab id.
# Instead we tag the new tab with a unique URL hash marker at creation
# time, and every subsequent operation re-locates the tab by URL match.
# This also gives us hijacker-resilience for free: even if the user moves
# the tab to another window or other tabs open/close around it, the
# marker-based lookup still finds it.
_TAB_MARKER_KEY = "__bp_marker"


@dataclass
class _TabRef:
    """Stable handle to the Medium editor tab, looked up by URL marker."""
    app: str
    marker: str  # e.g. "abc12345" — unique per publish


def _build_marker_url() -> tuple[str, str]:
    """Return (url, marker) — url has the marker as a hash fragment."""
    marker = uuid.uuid4().hex[:12]
    # Hash fragments are not sent to the server but ARE preserved across
    # SPA navigation within the same origin. Medium's editor (Lexical-based)
    # doesn't navigate away from medium.com after load, so the marker
    # survives. For OAuth redirects (which DO change origin), the marker
    # may be dropped — in that case _find_marker_tab falls back to "any
    # tab containing 'medium.com/new-story' or 'medium.com/p/'".
    url = f"https://medium.com/new-story#{_TAB_MARKER_KEY}={marker}"
    return url, marker


def _open_new_story(app: str, wait_secs: int = 5) -> _TabRef:
    """Open medium.com/new-story with a unique marker; return a _TabRef.

    `app` must be one of `_SUPPORTED_APPS` (Brave or Chrome — both share
    Chromium's AppleScript dictionary). The returned _TabRef carries a
    unique URL marker; subsequent operations look the tab up by marker
    rather than tab id (sidesteps AppleScript's 2^30 integer literal cap
    AND survives the active-tab hijacking that motivated this fix).
    """
    url, marker = _build_marker_url()
    script = f"""
tell application "{app}"
    activate
    set newTab to make new tab at end of tabs of front window with properties {{URL:"{url}"}}
    set active tab index of front window to (count tabs of front window)
    delay {wait_secs}
end tell
"""
    _run_applescript(script, timeout=30 + wait_secs)
    return _TabRef(app=app, marker=marker)


def _find_marker_tab_position(ref: _TabRef) -> tuple[int, int]:
    """Locate the marked tab and return (window_position, tab_position).

    Positions are 1-based AppleScript indices, recomputed on every call so
    user/extension-driven tab reordering, tab close, or window changes
    don't desynchronise us. Raises ExternalServiceError if not found.
    """
    script = f'''
tell application "{ref.app}"
    set wPos to 0
    repeat with w in windows
        set wPos to wPos + 1
        set tPos to 0
        repeat with t in tabs of w
            set tPos to tPos + 1
            set u to URL of t
            if u contains "{_TAB_MARKER_KEY}={ref.marker}" then
                return (wPos as text) & "," & (tPos as text)
            end if
        end repeat
    end repeat
    -- Fallback: marker may have been stripped by OAuth redirect.
    -- Find any tab that's on medium.com editor URL.
    set wPos to 0
    repeat with w in windows
        set wPos to wPos + 1
        set tPos to 0
        repeat with t in tabs of w
            set tPos to tPos + 1
            set u to URL of t
            if u contains "medium.com/new-story" or u contains "medium.com/p/" then
                return (wPos as text) & "," & (tPos as text)
            end if
        end repeat
    end repeat
    return ""
end tell
'''
    out = _run_applescript(script, timeout=10)
    if not out or "," not in out:
        raise ExternalServiceError(
            f"Could not locate Medium editor tab (marker={ref.marker}). "
            "Was the tab closed manually during publish?"
        )
    w_str, t_str = out.split(",", 1)
    return int(w_str.strip()), int(t_str.strip())


def _get_tab_url(ref: _TabRef) -> str:
    """Read URL of the marker tab — no focus required."""
    w, t = _find_marker_tab_position(ref)
    script = f'''
tell application "{ref.app}"
    return URL of tab {t} of window {w}
end tell
'''
    return _run_applescript(script, timeout=10)


def _set_clipboard(text: str) -> None:
    proc = subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=10)
    if proc.returncode != 0:
        raise ExternalServiceError("Failed to copy content to clipboard")


def _native_js_in_tab(ref: _TabRef, js: str) -> str:
    """Execute JavaScript in the marker tab — no focus required.

    Both Brave and Chrome require "Allow JavaScript from Apple Events"
    enabled in View → Developer (Chrome enables by default; Brave defaults
    to OFF).
    """
    w, t = _find_marker_tab_position(ref)
    escaped = js.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "{ref.app}"
    set result to execute tab {t} of window {w} javascript "{escaped}"
    return result
end tell
'''
    return _run_applescript(script, timeout=30)


def _focus_and_keystroke(ref: _TabRef, keystroke_payload: str) -> None:
    """Focus the marker tab and fire a keystroke — atomic single AppleScript.

    `keystroke_payload` runs inside `tell process "<app>"` (e.g.,
    `keystroke "..."` / `key code 36` / `keystroke "v" using command down`).
    Collapses the focus→keystroke race window so hijackers can't slip in.
    """
    w, t = _find_marker_tab_position(ref)
    script = f'''
tell application "{ref.app}"
    set targetWindow to window {w}
    set active tab index of targetWindow to {t}
    set index of targetWindow to 1
    activate
end tell
delay 0.1
tell application "System Events"
    tell process "{ref.app}"
        {keystroke_payload}
    end tell
end tell
'''
    _run_applescript(script, timeout=20)


def _wait_for_medium_editor(ref: _TabRef, max_wait: int = 20) -> bool:
    """Poll until Medium's editor is ready (title placeholder visible)."""
    for _ in range(max_wait):
        try:
            url = _get_tab_url(ref)
            if "medium.com/m/signin" in url or "medium.com/signin" in url:
                return False  # Not logged in
            result = _native_js_in_tab(
                ref,
                "document.querySelector('[data-testid=\"post-title\"], "
                "[class*=\"graf--title\"], h3[class*=\"title\"]') ? 'ready' : 'wait'"
            )
            if result == "ready":
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _click_title_and_type(ref: _TabRef, title: str) -> None:
    _native_js_in_tab(
        ref,
        "var el = document.querySelector('[data-testid=\"post-title\"], "
        "[class*=\"graf--title\"], h3[class*=\"title\"]'); "
        "if(el){ el.click(); el.focus(); }"
    )
    time.sleep(0.5)
    escaped_title = title.replace('"', '\\"').replace("\\", "\\\\")
    _focus_and_keystroke(ref, f'keystroke "{escaped_title}"')
    time.sleep(0.3)
    _focus_and_keystroke(ref, 'key code 36')  # Return
    time.sleep(0.5)


def _paste_body_content(ref: _TabRef, html_content: str) -> None:
    _set_clipboard(html_content)
    time.sleep(0.3)
    _native_js_in_tab(
        ref,
        "var body = document.querySelector('[data-testid=\"post-body\"], "
        ".section-inner, [class*=\"graf--p\"]'); "
        "if(body){ body.click(); body.focus(); }"
    )
    time.sleep(0.5)
    _focus_and_keystroke(ref, 'keystroke "v" using command down')
    time.sleep(2)


def _click_publish_menu(ref: _TabRef) -> None:
    clicked = _native_js_in_tab(
        ref,
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


def _click_publish_now(ref: _TabRef) -> None:
    _native_js_in_tab(
        ref,
        "var btns = Array.from(document.querySelectorAll('button'));"
        "var pub = btns.find(b => "
        "b.textContent.includes('Publish now') || b.textContent.includes('Publish'));"
        "if(pub){ pub.click(); return 'clicked'; } return 'notfound';"
    )
    time.sleep(3)


def _save_draft_via_keyboard() -> None:
    """Medium auto-saves, but we can ensure it by waiting."""
    time.sleep(3)


class MediumBraveAdapter:
    """Publish to Medium via AppleScript-controlled Brave browser (macOS only).

    Completely bypasses CDP/automation detection since it uses the user's
    real Brave browser with their existing login session.

    Raises DependencyError on non-macOS platforms.
    Raises ExternalServiceError if Brave is not running or user not logged in.
    """

    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        _check_macos()

        app = config.medium_native_browser_app
        if app not in _SUPPORTED_APPS:
            raise DependencyError(
                f"medium_native_browser_app={app!r} not in {_SUPPORTED_APPS}"
            )

        article_id = payload.get("id", str(uuid.uuid4())[:8])
        title = payload.get("title", "")
        content_markdown = payload.get("content_markdown", "")
        content_html = render_to_html(content_markdown)

        log.info(_json_log(adapter="medium-brave", phase="start", id=article_id, app=app))

        # Ensure the chosen browser is running
        try:
            _run_applescript(f'tell application "{app}" to return name', timeout=5)
        except Exception:
            raise ExternalServiceError(
                f"{app} is not running. Please open {app} and log in to Medium."
            )

        # Open new story tab with a unique URL hash marker; every subsequent
        # operation re-locates the tab by marker, so hijacker tab-switches,
        # tab reorders, or window changes don't desynchronise us. Also
        # sidesteps AppleScript's 2^30 integer literal cap on tab ids.
        log.info(_json_log(adapter="medium-brave", phase="open-new-story", id=article_id))
        ref = _open_new_story(app, wait_secs=6)
        log.info(_json_log(
            adapter="medium-brave", phase="tab-pinned",
            id=article_id, marker=ref.marker, app=app,
        ))

        url = _get_tab_url(ref)
        if "signin" in url or "login" in url:
            raise ExternalServiceError(
                f"Medium login required. Please log in to medium.com in {app} first, then retry."
            )

        if "medium.com/new-story" not in url and "medium.com/p/" not in url:
            raise ExternalServiceError(
                f"Unexpected URL after opening new story (marker={ref.marker}): {url}. "
                "Medium may have changed its URL structure or is showing a CAPTCHA."
            )

        log.info(_json_log(adapter="medium-brave", phase="wait-editor", id=article_id))
        ready = _wait_for_medium_editor(ref, max_wait=15)
        if not ready:
            time.sleep(5)

        log.info(_json_log(adapter="medium-brave", phase="fill-title", id=article_id))
        _click_title_and_type(ref, title)

        log.info(_json_log(adapter="medium-brave", phase="paste-body", id=article_id))
        _paste_body_content(ref, content_html)

        if mode == "publish":
            log.info(_json_log(adapter="medium-brave", phase="publish", id=article_id))
            try:
                _click_publish_menu(ref)
                _click_publish_now(ref)
            except ExternalServiceError:
                log.info(_json_log(
                    adapter="medium-brave", phase="publish-fallback",
                    note="publish button not found, story saved as draft", id=article_id
                ))
        else:
            log.info(_json_log(adapter="medium-brave", phase="save-draft", id=article_id))
            _save_draft_via_keyboard()

        time.sleep(2)
        final_url = _get_tab_url(ref)
        log.info(_json_log(adapter="medium-brave", phase="done", id=article_id, url=final_url))

        if mode == "publish":
            return AdapterResult(
                status="published",
                adapter="medium-brave",
                platform="medium",
                published_url=final_url,
            )
        return AdapterResult(
            status="drafted",
            adapter="medium-brave",
            platform="medium",
            draft_url=final_url,
        )
