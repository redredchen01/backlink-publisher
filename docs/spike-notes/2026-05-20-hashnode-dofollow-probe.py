#!/usr/bin/env python3
"""Hashnode dofollow Step 0 — Playwright probe (post-bind, bypasses CF).

Step 0 was supposed to run via Chrome MCP extension before any bind work.
Chrome MCP extension wasn't connected at spike time, and curl / WebFetch
both hit CF 403 on hashnode posts. Pivot: use the persistent profile
established by ``bind-channel --channel hashnode`` to re-launch Playwright
*as if* we were the bound operator — same CF challenge state, same cookies
— and scrape rel attributes from public posts.

This script does NOT require bind to have authenticated successfully;
it only needs the browser-profile directory to have accumulated the
``cf_clearance`` cookie via the human-driven Cloudflare interstitial.

Usage:

    # After (or during) `bind-channel --channel hashnode` — profile must
    # exist at $BACKLINK_PUBLISHER_CONFIG_DIR/browser-profile/.
    python3 docs/spike-notes/2026-05-20-hashnode-dofollow-probe.py \\
        --config-dir /tmp/hn-spike-config

Output: per-surface rel attribute table to stdout + JSON dump to
``docs/spike-notes/<ts>-hashnode-dofollow-raw.json``.

NOT a production tool — discarded after Unit 1.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


# Sample URLs across the 4 hosting surfaces the plan requires.
# - subdomain: *.hashnode.dev personal blog
# - publication: townhall.hashnode.dev (Hashnode's own publication on subdomain)
# - custom_domain: Hashnode blog on custom-domain (find via search if possible)
# - publication_custom: Publication on custom-domain
SAMPLE_POSTS = {
    "subdomain": [
        "https://tanujabhatnagar.hashnode.dev/tutorial-how-to-get-recent-blogs-using-hashnode-api-2024-updated",
        "https://aksbad007.hashnode.dev/how-to-include-hashnode-blogs-in-your-portfolio",
        "https://wearedev.hashnode.dev/1-best-practices-nodejs-backend-folder-structure",
    ],
    "publication": [
        # Hashnode's own publication runs on a *.hashnode.dev subdomain;
        # find a real article slug from the series page.
        "https://townhall.hashnode.dev/series/hackathons",
    ],
    "custom_domain": [
        # TODO: spike operator manually finds a Hashnode blog on custom-domain
        # (e.g., engineering blogs powered by Hashnode that use their own
        # domain). Leave empty if none found — flagged in spike-notes.
    ],
    "publication_custom": [
        # TODO: same — Publication on custom-domain
    ],
}


JS_EXTRACT_REL = r"""
() => {
    // Find article body — Hashnode uses various class names; fall back to <article> or main.
    const candidates = [
        document.querySelector('article'),
        document.querySelector('main'),
        document.querySelector('[role="main"]'),
        document.body,
    ];
    const root = candidates.find(el => el !== null) || document.body;
    const here = location.hostname;

    const links = Array.from(root.querySelectorAll('a[href]'));
    const external = links
        .map(a => {
            try {
                const u = new URL(a.href, location.href);
                if (u.hostname === here) return null;
                if (u.hostname.endsWith('.hashnode.dev') && here.endsWith('.hashnode.dev')) return null;
                if (u.hostname === 'hashnode.com' || u.hostname === here) return null;
                return {
                    href: u.toString(),
                    hostname: u.hostname,
                    rel: a.getAttribute('rel') || '(no-rel)',
                    text_preview: (a.textContent || '').trim().slice(0, 60),
                };
            } catch (e) { return null; }
        })
        .filter(Boolean);

    return {
        page_url: location.href,
        article_root_tag: root.tagName,
        total_links_in_root: links.length,
        external_count: external.length,
        external: external.slice(0, 30),
        cloudflare_detected: document.title.toLowerCase().includes('cloudflare') ||
            document.title.toLowerCase().includes('just a moment') ||
            document.body.textContent.includes('Cloudflare to restrict access'),
    };
}
"""


def probe_url(page, url: str) -> dict:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "error": f"goto failed: {type(exc).__name__}: {exc}"}
    try:
        # Hashnode content sometimes lazy-loads; give it a moment to settle.
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass  # noqa: BLE001 — best effort
    try:
        result = page.evaluate(JS_EXTRACT_REL)
        result["url"] = url
        return result
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "error": f"evaluate failed: {type(exc).__name__}: {exc}"}


def classify_rel(rel: str) -> str:
    rel_lower = rel.lower()
    if "nofollow" in rel_lower:
        return "nofollow"
    if rel == "(no-rel)":
        return "dofollow_implicit"
    return f"other:{rel}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config-dir",
        default=os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR"),
        help="Override config dir (defaults to BACKLINK_PUBLISHER_CONFIG_DIR env)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show browser window (default headless reuses the profile silently)",
    )
    args = parser.parse_args()

    if not args.config_dir:
        print("error: --config-dir or BACKLINK_PUBLISHER_CONFIG_DIR required", file=sys.stderr)
        return 2

    profile_dir = Path(args.config_dir).expanduser().resolve() / "browser-profile"
    if not profile_dir.exists():
        print(f"error: browser-profile not found at {profile_dir}; run bind-channel first", file=sys.stderr)
        return 2

    print(f"# Using browser-profile: {profile_dir}", file=sys.stderr)
    report = {
        "generated_at": datetime.now().isoformat(),
        "profile_dir": str(profile_dir),
        "surfaces": {},
    }

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=not args.headed,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        for surface, urls in SAMPLE_POSTS.items():
            print(f"\n## Surface: {surface}", file=sys.stderr)
            report["surfaces"][surface] = []
            if not urls:
                print(f"  (no sample URLs configured for {surface})", file=sys.stderr)
                continue
            for u in urls:
                print(f"  probing: {u}", file=sys.stderr)
                result = probe_url(page, u)
                if "error" in result:
                    print(f"    ERROR: {result['error']}", file=sys.stderr)
                    report["surfaces"][surface].append(result)
                    continue
                if result.get("cloudflare_detected"):
                    print(f"    BLOCKED: Cloudflare challenge page", file=sys.stderr)
                    report["surfaces"][surface].append({**result, "_status": "cf_blocked"})
                    continue
                ext = result.get("external", [])
                classes = [classify_rel(e["rel"]) for e in ext]
                from collections import Counter
                c = Counter(classes)
                print(f"    {len(ext)} external links | rel breakdown: {dict(c)}", file=sys.stderr)
                report["surfaces"][surface].append({**result, "_classify_counts": dict(c)})

        context.close()

    # Aggregate verdict
    print("\n# AGGREGATE", file=sys.stderr)
    overall = []
    for surface, results in report["surfaces"].items():
        for r in results:
            counts = r.get("_classify_counts", {})
            overall.append((surface, counts))
            print(f"  {surface}: {counts}", file=sys.stderr)
    report["aggregate"] = overall

    out_path = Path(__file__).parent / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-hashnode-dofollow-raw.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\n# Wrote raw report to: {out_path}", file=sys.stderr)

    # stdout = JSON for downstream parsing
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
