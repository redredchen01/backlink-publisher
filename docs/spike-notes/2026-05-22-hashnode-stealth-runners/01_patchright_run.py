#!/usr/bin/env python3
"""Plan 2026-05-20-016 Unit 1b — patchright stealth-premise spike runner.

patchright is a rebrowser-playwright fork with stealth patches built in.
Drop-in API replacement for playwright.sync_api.

Install:
    pip install patchright
    patchright install chromium      # downloads its patched chromium build

Usage:
    BACKLINK_PUBLISHER_SPIKE_OUT=/tmp/hn-stealth-patchright \\
      python3 docs/spike-notes/2026-05-22-hashnode-stealth-runners/01_patchright_run.py

Operator manually completes login in the opened window. Script captures
final URL + cookies on `hashnode.com` apex once operator hits Enter in
the launching terminal.

NOT a production tool — throwaway. Do not import from src/.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

try:
    from patchright.sync_api import sync_playwright
except ImportError as exc:
    print(f"FAIL_IMPORT: patchright not installed ({exc})", file=sys.stderr)
    print("   Run: pip install patchright && patchright install chromium", file=sys.stderr)
    sys.exit(2)


LOGIN_URL = "https://hashnode.com/onboard"
TIMEOUT_S = 600  # 10 min absolute cap; operator should finish in < 5


def main() -> int:
    out_dir = Path(os.environ.get(
        "BACKLINK_PUBLISHER_SPIKE_OUT", "/tmp/hn-stealth-patchright"
    ))
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    print(f"# spike out_dir: {out_dir}", file=sys.stderr)
    print(f"# login_url:     {LOGIN_URL}", file=sys.stderr)

    with sync_playwright() as pw:
        # Headed so operator can interact; persistent context so any
        # SSO cookies that survive page navigation stick.
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(out_dir / "profile"),
            headless=False,
            args=[],  # patchright applies stealth internally; no extra args
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, timeout=60_000)

        print("\n=== Operator: complete login in the opened window. ===", file=sys.stderr)
        print("=== Press Enter here when done (or Ctrl+C to abort).  ===\n", file=sys.stderr)
        try:
            input()
        except KeyboardInterrupt:
            print("\nABORTED by operator.", file=sys.stderr)
            context.close()
            return 3

        # Capture state
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
            "library": "patchright",
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
