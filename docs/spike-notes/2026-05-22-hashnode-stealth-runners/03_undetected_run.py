#!/usr/bin/env python3
"""Plan 2026-05-20-016 Unit 1b — undetected-playwright-python runner.

This library's API has shifted across versions and may be stale; the
runner tries multiple import shapes and falls back through them.

Install (verify last release date first — if older than 6 months,
consider skipping in favor of patchright):
    pip install undetected-playwright
    playwright install chromium

Usage:
    BACKLINK_PUBLISHER_SPIKE_OUT=/tmp/hn-stealth-undetected \\
      python3 docs/spike-notes/2026-05-22-hashnode-stealth-runners/03_undetected_run.py

NOT a production tool — throwaway. Do not import from src/.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Try the known API shapes for undetected-playwright; record which one we used.
_API = None
try:
    from undetected_playwright import Tarnished  # one historical entry point

    _API = "Tarnished"
except ImportError:
    try:
        from undetected_playwright.sync_api import sync_playwright as _undetected_sync

        _API = "sync_api"
    except ImportError:
        try:
            from undetected_playwright import sync_playwright as _undetected_sync

            _API = "module_root"
        except ImportError as exc:
            print(f"FAIL_IMPORT: undetected-playwright not installed or API changed ({exc})",
                  file=sys.stderr)
            print("   Run: pip install undetected-playwright", file=sys.stderr)
            print("   If still failing, library may be unmaintained — record as ABORT for this row.",
                  file=sys.stderr)
            sys.exit(2)


LOGIN_URL = "https://hashnode.com/onboard"


def _launch_and_capture(out_dir: Path) -> dict:
    """Drives the browser through whichever API shape resolved at import."""
    if _API == "Tarnished":
        # Tarnished wraps a standard playwright instance
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(out_dir / "profile"),
                headless=False,
            )
            Tarnished.apply_stealth(ctx)  # type: ignore[name-defined]
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(LOGIN_URL, timeout=60_000)
            return _await_operator_and_capture(ctx, page)

    # sync_api / module_root variants: same call shape as standard playwright
    with _undetected_sync() as pw:  # type: ignore[name-defined]
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(out_dir / "profile"),
            headless=False,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(LOGIN_URL, timeout=60_000)
        return _await_operator_and_capture(ctx, page)


def _await_operator_and_capture(ctx, page) -> dict:
    print("\n=== Operator: complete login in the opened window. ===", file=sys.stderr)
    print("=== Press Enter here when done (or Ctrl+C to abort).  ===\n", file=sys.stderr)
    try:
        input()
    except KeyboardInterrupt:
        print("\nABORTED by operator.", file=sys.stderr)
        ctx.close()
        return {"aborted": True}

    final_url = page.url
    cookies = ctx.cookies()
    hashnode_cookies = [
        c for c in cookies
        if c.get("domain", "").lstrip(".").endswith("hashnode.com")
    ]
    has_session = any(
        c.get("name") == "hashnode-session" and c.get("value")
        for c in hashnode_cookies
    )

    result = {
        "library": "undetected-playwright",
        "api_used": _API,
        "final_url": final_url,
        "hashnode_session_captured": has_session,
        "hashnode_cookie_count": len(hashnode_cookies),
        "all_cookie_count": len(cookies),
        "cookies_on_hashnode_apex": [
            {
                "name": c.get("name"),
                "http_only": c.get("httpOnly"),
                "secure": c.get("secure"),
                "value_len": len(c.get("value", "")),
            }
            for c in hashnode_cookies
        ],
    }
    ctx.close()
    return result


def main() -> int:
    out_dir = Path(os.environ.get(
        "BACKLINK_PUBLISHER_SPIKE_OUT", "/tmp/hn-stealth-undetected"
    ))
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    print(f"# spike out_dir: {out_dir}", file=sys.stderr)
    print(f"# login_url:     {LOGIN_URL}", file=sys.stderr)
    print(f"# api_used:      {_API}", file=sys.stderr)

    verdict = _launch_and_capture(out_dir)
    verdict_path = out_dir / "verdict.json"
    verdict_path.write_text(json.dumps(verdict, indent=2, ensure_ascii=False))
    print(f"\n# verdict written: {verdict_path}", file=sys.stderr)
    print(json.dumps(verdict, indent=2), file=sys.stderr)

    if verdict.get("aborted"):
        return 3
    return 0 if verdict.get("hashnode_session_captured") else 1


if __name__ == "__main__":
    sys.exit(main())
