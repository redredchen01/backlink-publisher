#!/usr/bin/env python3
"""Plan 2026-05-20-016 Unit 1b — playwright-stealth runner.

Plain Playwright + `playwright_stealth` plugin (Python port of
puppeteer-extra-plugin-stealth).

Install:
    pip install playwright playwright-stealth
    playwright install chromium

Usage:
    BACKLINK_PUBLISHER_SPIKE_OUT=/tmp/hn-stealth-pwstealth \\
      python3 docs/spike-notes/2026-05-22-hashnode-stealth-runners/02_playwright_stealth_run.py

NOT a production tool — throwaway. Do not import from src/.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError as exc:
    print(f"FAIL_IMPORT: playwright not installed ({exc})", file=sys.stderr)
    print("   Run: pip install playwright && playwright install chromium", file=sys.stderr)
    sys.exit(2)

try:
    # API has shifted across versions; try both shapes.
    try:
        from playwright_stealth import stealth_sync  # older API
    except ImportError:
        from playwright_stealth import Stealth  # newer class-based API
        stealth_sync = None
except ImportError as exc:
    print(f"FAIL_IMPORT: playwright-stealth not installed ({exc})", file=sys.stderr)
    print("   Run: pip install playwright-stealth", file=sys.stderr)
    sys.exit(2)


LOGIN_URL = "https://hashnode.com/onboard"


def main() -> int:
    out_dir = Path(os.environ.get(
        "BACKLINK_PUBLISHER_SPIKE_OUT", "/tmp/hn-stealth-pwstealth"
    ))
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    print(f"# spike out_dir: {out_dir}", file=sys.stderr)
    print(f"# login_url:     {LOGIN_URL}", file=sys.stderr)
    print(f"# stealth API:   {'stealth_sync (legacy)' if stealth_sync else 'Stealth class (new)'}",
          file=sys.stderr)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(out_dir / "profile"),
            headless=False,
        )
        page = context.pages[0] if context.pages else context.new_page()

        # Apply stealth — API depends on version
        if stealth_sync is not None:
            stealth_sync(page)
        else:
            Stealth().apply_stealth_sync(page)

        page.goto(LOGIN_URL, timeout=60_000)

        print("\n=== Operator: complete login in the opened window. ===", file=sys.stderr)
        print("=== Press Enter here when done (or Ctrl+C to abort).  ===\n", file=sys.stderr)
        try:
            input()
        except KeyboardInterrupt:
            print("\nABORTED by operator.", file=sys.stderr)
            context.close()
            return 3

        final_url = page.url
        cookies = context.cookies()
        hashnode_cookies = [
            c for c in cookies
            if c.get("domain", "").lstrip(".").endswith("hashnode.com")
        ]
        has_session = any(
            c.get("name") == "hashnode-session" and c.get("value")
            for c in hashnode_cookies
        )

        verdict = {
            "library": "playwright-stealth",
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

        verdict_path = out_dir / "verdict.json"
        verdict_path.write_text(json.dumps(verdict, indent=2, ensure_ascii=False))
        print(f"\n# verdict written: {verdict_path}", file=sys.stderr)
        print(json.dumps(verdict, indent=2), file=sys.stderr)

        context.close()
        return 0 if has_session else 1


if __name__ == "__main__":
    sys.exit(main())
