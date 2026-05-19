"""Plan 2026-05-19-003 Unit 0 spikes — Medium browser-bind hardening.

Run before implementing Units 1 / 5. Three sub-spikes:

  3a (cookie name)  — capture Medium HttpOnly auth cookie name(s)
                       → feeds MEDIUM_AUTH_COOKIE_WHITELIST in
                       cli/_bind/recipes/medium.py
  2  (anti-bot)     — does headless goto('/me') trigger Cloudflare?
                       → feeds MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED
                       default in webui_app/medium_liveness.py
  7  (framenavigated) — does framenavigated fire reliably during
                       Google SSO 2FA SPA transitions?
                       → feeds Unit 1's idle-detection vs wall-clock
                       fallback decision

Throwaway. Outputs go into Unit 1's commit message and the recipe's
module-level constants. Delete or archive to docs/solutions/research/
after run.

Usage:
    pip install playwright       # if not already
    playwright install chromium  # if not already

    # Spike 3a (HEADED — operator must be logged into Medium in this
    # context's profile, e.g., reuse the persistent profile or do a
    # manual login):
    python scripts/medium_bind_spike.py 3a --profile-dir ~/.cache/medium-spike

    # Spike 2 (HEADLESS — uses the persistent profile from 3a):
    python scripts/medium_bind_spike.py 2 --profile-dir ~/.cache/medium-spike

    # Spike 7 (interactive — operator walks through Google 2FA):
    python scripts/medium_bind_spike.py 7 --profile-dir ~/.cache/medium-spike
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


ANONYMOUS_TRACKING = {"uid", "_ga", "_dd_s", "g_state", "nonce", "pr", "sz", "tz", "_gid", "_gat", "optimizely"}


def spike_3a(profile_dir: Path) -> int:
    """Capture HttpOnly auth cookies on medium.com."""
    print("=" * 60)
    print("Spike 3a — HttpOnly auth cookie name capture")
    print("=" * 60)
    print(f"profile_dir: {profile_dir}")
    print("If this profile is fresh, you'll need to log in to Medium")
    print("interactively in the headed window that opens.")
    print()

    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.new_page()
        page.goto("https://medium.com/me", wait_until="load")
        print(f"Landed at: {page.url}")
        if "/m/signin" in page.url:
            print("Profile not logged in. Log in manually in the headed window,")
            print("then press Enter here.")
            input(">>> ready? ")
            page.goto("https://medium.com/me", wait_until="load")
            print(f"After login, landed at: {page.url}")

        cookies = ctx.cookies(["https://medium.com"])
        print(f"\nTotal cookies on medium.com apex: {len(cookies)}")
        print()
        print("HttpOnly cookies (sorted by expires, far-future first):")
        print("-" * 60)
        http_only = [
            c
            for c in cookies
            if c.get("httpOnly")
            and c.get("domain", "").lstrip(".") == "medium.com"
        ]
        http_only.sort(key=lambda c: c.get("expires", 0), reverse=True)
        candidates = []
        for c in http_only:
            name = c.get("name", "?")
            expires = c.get("expires", -1)
            expires_human = "session" if expires < 0 else f"epoch={expires:.0f}"
            tracker = " [LIKELY-TRACKER]" if name.lower() in ANONYMOUS_TRACKING else ""
            print(f"  {name:30s} httpOnly=True  expires={expires_human}{tracker}")
            if expires > 0 and (time.time() + 7 * 86400) < expires and name.lower() not in ANONYMOUS_TRACKING:
                candidates.append(name)

        print()
        print(f"Candidate auth cookies (HttpOnly + expires>7d + not in tracker list):")
        for name in candidates:
            print(f"  -> {name}")
        if not candidates:
            print("  (none — investigate further; auth cookie may be session-scoped or"
                  " have shorter expiry)")
        ctx.close()
    return 0


def spike_2(profile_dir: Path) -> int:
    """Headless goto(/me) × 10 with 5-min interval; record final URL."""
    print("=" * 60)
    print("Spike 2 — Headless anti-bot probe (10 iterations)")
    print("=" * 60)
    print("WARNING: takes ~50 minutes due to 5-min sleeps between probes.")
    print("Press Ctrl-C to abort early.")
    print()

    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        for i in range(10):
            ctx = pw.chromium.launch_persistent_context(
                str(profile_dir), headless=True
            )
            page = ctx.new_page()
            try:
                page.goto("https://medium.com/me", wait_until="load", timeout=15000)
                final_url = page.url
                challenged = (
                    "challenges.cloudflare.com" in final_url
                    or "__cf_chl_" in final_url
                    or "datadome" in final_url.lower()
                )
                status_class = "CHALLENGE" if challenged else (
                    "SIGNIN" if "/m/signin" in final_url else "OK"
                )
                print(f"[{i + 1:2d}/10] {status_class:10s} -> {final_url}")
            except Exception as exc:
                print(f"[{i + 1:2d}/10] ERROR      -> {type(exc).__name__}: {exc}")
            finally:
                ctx.close()
            if i < 9:
                print(f"        ... sleeping 5 min ...")
                time.sleep(300)
    return 0


def spike_7(profile_dir: Path) -> int:
    """Manual Google SSO 2FA walkthrough; report framenavigated frequency."""
    print("=" * 60)
    print("Spike 7 — framenavigated reliability during SPA-2FA")
    print("=" * 60)
    print("Open the headed window. Manually click 'Sign in with Google'")
    print("and walk through 2FA. The script attaches a framenavigated")
    print("listener and counts events. Press Enter when done.")
    print()

    profile_dir.mkdir(parents=True, exist_ok=True)
    nav_events: list[str] = []

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.new_page()
        page.on("framenavigated", lambda frame: nav_events.append(frame.url))
        page.goto("https://medium.com/m/signin", wait_until="load")
        print("Walk through Google SSO + 2FA now. When you reach a logged-in")
        print("page, press Enter here.")
        input(">>> done? ")

        print()
        print(f"Captured {len(nav_events)} framenavigated event(s):")
        for url in nav_events[-30:]:
            print(f"  -> {url}")
        print()
        print("Heuristic for Unit 1:")
        print(f"  - if events > 5 between signin and /me: framenavigated reliable;")
        print(f"    idle-detection (90s no nav) is viable")
        print(f"  - if events < 3: framenavigated unreliable on SPA challenges;")
        print(f"    fall back to wall-clock 20-min timeout in Unit 1")
        ctx.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spike", choices=("3a", "2", "7"), help="Which spike to run")
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path.home() / ".cache" / "medium-spike",
        help="Persistent Chromium profile dir for the spike",
    )
    args = parser.parse_args()

    if args.spike == "3a":
        return spike_3a(args.profile_dir)
    if args.spike == "2":
        return spike_2(args.profile_dir)
    if args.spike == "7":
        return spike_7(args.profile_dir)
    return 1


if __name__ == "__main__":
    sys.exit(main())
