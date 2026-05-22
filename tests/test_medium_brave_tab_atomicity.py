"""Regression tests for medium_brave tab helpers.

Before this change, `_get_tab_url`, `_focus_tab`, `_tab_js` first
resolved (win_id, tab_id) to positional (win_idx, tab_idx) in one
AppleScript call, then issued a *second* AppleScript using those
positions. Any tab opening/closing between the two calls shifted indices
and raised errAEIllegalIndex (-1719). The current implementation
resolves and acts inside a single `tell` block, so Brave evaluates
against one consistent snapshot.

These tests pin the single-call contract and the tab-gone sentinel
translation so the race-prone two-call pattern can't regress in.
"""

from __future__ import annotations

import platform
from unittest.mock import patch

import pytest

from backlink_publisher._util.errors import ExternalServiceError
from backlink_publisher.publishing.adapters import medium_brave as mb


pytestmark = pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="medium_brave is macOS-only (AppleScript)",
)


def _capture():
    """Return (calls, fake_run) where calls records every script passed in."""
    calls: list[tuple[str, int]] = []

    def fake_run(script: str, timeout: int = 60) -> str:
        calls.append((script, timeout))
        return fake_run.return_value

    fake_run.return_value = ""
    return calls, fake_run


def test_get_tab_url_is_single_atomic_call():
    calls, fake = _capture()
    fake.return_value = "https://medium.com/new-story"
    with patch.object(mb, "_run_applescript", side_effect=fake):
        url = mb._get_tab_url("WIN", "TAB")
    assert url == "https://medium.com/new-story"
    assert len(calls) == 1, "must resolve+read in one AppleScript call (no TOCTOU)"
    script = calls[0][0]
    assert 'id of w as string) is "WIN"' in script
    assert 'id of t as string) is "TAB"' in script
    assert "URL of t" in script
    assert "of tab " not in script and "of window " not in script, (
        "must not use positional index — that's the bug being fixed"
    )


def test_focus_tab_is_single_atomic_call():
    calls, fake = _capture()
    fake.return_value = "OK"
    with patch.object(mb, "_run_applescript", side_effect=fake):
        mb._focus_tab("WIN", "TAB")
    assert len(calls) == 1
    script = calls[0][0]
    assert 'id of w as string) is "WIN"' in script
    assert 'id of t as string) is "TAB"' in script
    assert "set active tab index of foundWin to foundIdx" in script
    assert "tab " + str(2) + " of window" not in script


def test_tab_js_is_single_atomic_call_and_escapes():
    calls, fake = _capture()
    fake.return_value = "ready"
    js = 'document.querySelector("h1")?.click(); var x = "a\\nb";'
    with patch.object(mb, "_run_applescript", side_effect=fake):
        result = mb._tab_js("WIN", "TAB", js)
    assert result == "ready"
    assert len(calls) == 1
    script = calls[0][0]
    assert 'id of t as string) is "TAB"' in script
    assert "execute t javascript" in script
    # quotes/backslashes must be escaped before embedding
    assert '\\"a\\\\nb\\"' in script


@pytest.mark.parametrize(
    "fn,args",
    [
        (mb._get_tab_url, ("WIN", "TAB")),
        (mb._focus_tab, ("WIN", "TAB")),
        (mb._tab_js, ("WIN", "TAB", "noop()")),
    ],
)
def test_tab_gone_sentinel_maps_to_external_service_error(fn, args):
    calls, fake = _capture()
    fake.return_value = mb._TAB_GONE_SENTINEL
    with patch.object(mb, "_run_applescript", side_effect=fake):
        with pytest.raises(ExternalServiceError) as excinfo:
            fn(*args)
    msg = str(excinfo.value)
    assert "win_id=WIN" in msg and "tab_id=TAB" in msg
    assert "no longer exists" in msg
