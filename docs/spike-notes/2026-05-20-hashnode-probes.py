#!/usr/bin/env python3
"""Hashnode spike — post-bind probes (dofollow + editor selectors + URL pattern).

Loads /tmp/hn-spike-config/hashnode-cookies.json (captured in prior bind),
injects into a fresh Playwright Chromium context, then drives queries.

Outputs:
  - dofollow per-surface table for Step 0 (CF cookie should let us past CF)
  - post-login dashboard URL pattern (operator's actual landing URL)
  - editor URL + title/body/publish selector candidates
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


COOKIES_PATH = Path("/tmp/hn-spike-config/hashnode-cookies.json")


SAMPLE_POSTS = {
    "subdomain": [
        "https://tanujabhatnagar.hashnode.dev/tutorial-how-to-get-recent-blogs-using-hashnode-api-2024-updated",
        "https://aksbad007.hashnode.dev/how-to-include-hashnode-blogs-in-your-portfolio",
        "https://wearedev.hashnode.dev/1-best-practices-nodejs-backend-folder-structure",
    ],
    "publication_subdomain": [
        # Hashnode's official "townhall" publication
        "https://townhall.hashnode.dev/series/hackathons",
    ],
}


JS_EXTRACT_REL = r"""
() => {
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
                if (u.hostname.endsWith('.hashnode.dev') || u.hostname === 'hashnode.com') return null;
                return {
                    href: u.toString().slice(0, 160),
                    hostname: u.hostname,
                    rel: a.getAttribute('rel') || '(no-rel)',
                };
            } catch (e) { return null; }
        })
        .filter(Boolean);
    return {
        page_url: location.href,
        page_title: document.title.slice(0, 100),
        article_root_tag: root.tagName,
        external_count: external.length,
        external: external.slice(0, 20),
        cf_blocked: /cloudflare|just a moment/i.test(document.title),
    };
}
"""


JS_EDITOR_PROBE = r"""
() => {
    // Find form-like inputs that could be title / body / tags / publish button
    return {
        url: location.href,
        title: document.title,
        input_candidates: Array.from(document.querySelectorAll('input, textarea, [contenteditable]'))
            .slice(0, 30)
            .map(el => ({
                tag: el.tagName,
                type: el.getAttribute('type') || '',
                placeholder: el.getAttribute('placeholder') || '',
                name: el.getAttribute('name') || '',
                aria_label: el.getAttribute('aria-label') || '',
                id: el.id || '',
                class_preview: (el.className || '').toString().slice(0, 80),
                contenteditable: el.getAttribute('contenteditable') || '',
            })),
        button_candidates: Array.from(document.querySelectorAll('button'))
            .filter(b => /publish|post|publis|发布|publi|next|continue/i.test(b.textContent || ''))
            .slice(0, 10)
            .map(b => ({
                text: (b.textContent || '').trim().slice(0, 50),
                type: b.getAttribute('type') || '',
                aria_label: b.getAttribute('aria-label') || '',
                class_preview: (b.className || '').toString().slice(0, 80),
            })),
        link_candidates_to_editor: Array.from(document.querySelectorAll('a[href]'))
            .filter(a => /draft|new|write|publish|editor/i.test(a.href + ' ' + (a.textContent || '')))
            .slice(0, 10)
            .map(a => ({
                href: a.href.slice(0, 120),
                text: (a.textContent || '').trim().slice(0, 60),
            })),
    };
}
"""


def main() -> int:
    cookies_data = json.loads(COOKIES_PATH.read_text())
    cookies = cookies_data["cookies"]
    print(f"# Loaded {len(cookies)} cookies", file=sys.stderr)

    report = {"surfaces": {}, "editor_probe": {}}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context()
        # Playwright add_cookies needs sameSite normalized
        normalized = []
        for c in cookies:
            cc = dict(c)
            ss = (cc.get("sameSite") or "None").lower()
            cc["sameSite"] = {"none": "None", "lax": "Lax", "strict": "Strict"}.get(ss, "None")
            if cc.get("expires") in (None, -1, 0):
                cc.pop("expires", None)
            normalized.append(cc)
        try:
            context.add_cookies(normalized)
        except Exception as exc:
            print(f"add_cookies failed: {exc}", file=sys.stderr)
            return 3
        page = context.new_page()

        # === Probe 1: dofollow on sample posts ===
        print("\n## DOFOLLOW PROBE", file=sys.stderr)
        for surface, urls in SAMPLE_POSTS.items():
            print(f"\n### Surface: {surface}", file=sys.stderr)
            report["surfaces"][surface] = []
            for u in urls:
                print(f"  probing: {u}", file=sys.stderr)
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=20_000)
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception as e:
                    print(f"    goto err: {type(e).__name__}: {e}", file=sys.stderr)
                    report["surfaces"][surface].append({"url": u, "error": str(e)})
                    continue
                try:
                    r = page.evaluate(JS_EXTRACT_REL)
                    if r.get("cf_blocked"):
                        print(f"    CF blocked: {r['page_title']}", file=sys.stderr)
                    else:
                        from collections import Counter
                        rels = Counter([(e["rel"]) for e in r.get("external", [])])
                        print(f"    title: {r['page_title'][:80]}", file=sys.stderr)
                        print(f"    external links: {r['external_count']}, rel breakdown: {dict(rels)}", file=sys.stderr)
                    report["surfaces"][surface].append(r)
                except Exception as e:
                    print(f"    evaluate err: {type(e).__name__}: {e}", file=sys.stderr)

        # === Probe 2: editor URL discovery ===
        print("\n## EDITOR PROBE", file=sys.stderr)
        editor_candidates = [
            "https://hashnode.com/",
            "https://hashnode.com/draft",
            "https://hashnode.com/dashboard",
        ]
        for url in editor_candidates:
            print(f"  probing: {url}", file=sys.stderr)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                page.wait_for_load_state("networkidle", timeout=5_000)
                final_url = page.url
                r = page.evaluate(JS_EDITOR_PROBE)
                r["initial_url"] = url
                r["final_url"] = final_url
                print(f"    landed: {final_url}", file=sys.stderr)
                print(f"    title: {r['title'][:80]}", file=sys.stderr)
                print(f"    inputs: {len(r['input_candidates'])}, buttons: {len(r['button_candidates'])}, editor-links: {len(r['link_candidates_to_editor'])}", file=sys.stderr)
                report["editor_probe"][url] = r
            except Exception as e:
                print(f"    err: {type(e).__name__}: {e}", file=sys.stderr)
                report["editor_probe"][url] = {"error": str(e)}

        context.close()
        browser.close()

    out_path = Path(__file__).parent / "2026-05-21-hashnode-probes-raw.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\n# wrote raw report: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
