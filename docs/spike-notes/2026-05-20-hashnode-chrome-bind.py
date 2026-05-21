#!/usr/bin/env python3
"""Hashnode spike — chrome-backend bind via RealChromeBrowserRunner directly.

Skips the bind-channel CLI (which doesn't have --backend flag on origin/main
yet — landed only on the in-flight feat/bind-medium-pipeline-repair branch).
Uses the already-merged chrome_backend.py library directly.

Launches REAL Chrome (not Playwright Chromium) with CDP debugging port so
Cloudflare's bot-detection sees a real-browser fingerprint and lets the
operator through human verification.

Usage:

    BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/hn-spike-config \\
      python3 docs/spike-notes/2026-05-20-hashnode-chrome-bind.py

A real Chrome window opens at https://hashnode.com/. Operator logs in
(SSO or email). When URL pattern matches the bound predicate (anywhere
on hashnode.com except /login /signup /onboard /auth), cookies are
extracted to /tmp/hn-spike-config/hashnode-storage-state.json.

NOT a production tool — discarded after Unit 1.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure our local src is on the path
HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE / "src"))

from backlink_publisher.cli._bind.recipes.hashnode import RECIPE
from backlink_publisher.cli._bind.chrome_backend import RealChromeBrowserRunner


def main() -> int:
    config_dir = Path(os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR", "/tmp/hn-spike-config"))
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    storage_state_path = config_dir / "hashnode-storage-state.json"

    print(f"# config_dir: {config_dir}", file=sys.stderr)
    print(f"# storage_state will land at: {storage_state_path}", file=sys.stderr)
    print(f"# recipe.login_url: {RECIPE.login_url}", file=sys.stderr)
    print(f"# chrome backend available: {RealChromeBrowserRunner.available()}", file=sys.stderr)

    runner = RealChromeBrowserRunner()
    if not runner.available():
        print("ERROR: chrome backend not available (no Chrome binary discovered, no CDP port).", file=sys.stderr)
        return 3

    print("# Launching real Chrome (operator window will open on desktop)...", file=sys.stderr)
    print("# Complete Hashnode login when the window appears.", file=sys.stderr)
    print("# Bound predicate waits for URL away from /onboard /login /signup /auth", file=sys.stderr)

    def on_browser_ready():
        print("[event] browser_ready", file=sys.stderr)

    def on_login_detected():
        print("[event] login_detected — extracting cookies...", file=sys.stderr)

    try:
        storage_state_provider = runner.launch_and_wait(
            recipe=RECIPE,
            on_browser_ready=on_browser_ready,
            on_login_detected=on_login_detected,
        )
    except Exception as exc:
        print(f"ERROR: launch_and_wait failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    print(f"# Writing storage_state to {storage_state_path}", file=sys.stderr)
    try:
        storage_state_provider(path=str(storage_state_path))
        os.chmod(storage_state_path, 0o600)
    except Exception as exc:
        print(f"ERROR: storage_state write failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    state = json.loads(storage_state_path.read_text())
    cookies = state.get("cookies", [])
    print(f"# Captured {len(cookies)} cookies", file=sys.stderr)
    for c in cookies:
        print(f"#   {c.get('domain')}/{c.get('name')} httpOnly={c.get('httpOnly')}", file=sys.stderr)

    print(f"\nOK: bound — storage_state at {storage_state_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
