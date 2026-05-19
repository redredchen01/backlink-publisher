"""``velog-login`` CLI sub-command — headed Playwright login + secure cookie export.

Usage::

    backlink-publisher velog-login
    backlink-publisher velog-login --output /path/to/velog-cookies.json

Opens a headed Chromium window. The operator completes social login
(Google / GitHub / Facebook). Once the home feed is detected the script
exports cookies to a 0600 JSON file and prints a follow-up prompt.

Design constraints (from P0-2 spike + R9 / R16 plan):
- Credentials only — no profile persistence (``new_context()``, not
  ``launch_persistent_context()``).
- R16 host filter: only cookies from ``*.velog.io`` / ``velog.io`` are
  kept. IdP cookies (Google, GitHub) are silently dropped.
- File schema: ``{"cookies": [...]}`` (cookies-only; P0-2 confirmed no
  relevant localStorage / sessionStorage).
- Permissions: ``umask(0o077)`` → write → ``chmod(0o600)``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from backlink_publisher._util.errors import DependencyError
from backlink_publisher._util.logger import opencli_logger as log

try:
    from playwright.sync_api import sync_playwright, TimeoutError as _PWTimeout
except ImportError:  # pragma: no cover
    sync_playwright = None  # type: ignore[assignment]
    _PWTimeout = Exception  # type: ignore[assignment,misc]

# ── Constants ────────────────────────────────────────────────────────────────

_VELOG_LOGIN_URL = "https://velog.io"
_LOGIN_TIMEOUT_MS = 300_000  # 5 minutes


# ── Host filter primitive (R16) ───────────────────────────────────────────────

def _velog_host_allowed(host: str) -> bool:
    """Return True iff *host* belongs to the velog.io domain.

    Accepts ``velog.io``, ``.velog.io``, ``v2.velog.io``, ``v3.velog.io``,
    ``VELOG.IO`` (case-insensitive). Rejects:
    - prefix confusion: ``evilvelog.io``
    - suffix confusion: ``velog.io.attacker.com``
    - IdP domains: ``accounts.google.com``, ``github.com``
    - empty / None
    """
    if not host:
        return False
    normalised = host.lower().lstrip(".")
    return normalised == "velog.io" or normalised.endswith(".velog.io")


def _filter_velog_cookies(raw: list[dict]) -> list[dict]:
    """Keep only cookies whose domain passes ``_velog_host_allowed``."""
    kept = []
    for cookie in raw:
        domain = cookie.get("domain", "")
        if not isinstance(domain, str):
            continue
        if _velog_host_allowed(domain):
            kept.append(cookie)
    return kept


def _filter_velog_storage_state(raw: dict) -> dict:
    """Filter both ``cookies[]`` and ``origins[]`` to velog.io scope."""
    filtered_cookies = _filter_velog_cookies(raw.get("cookies", []))

    filtered_origins = []
    for origin_entry in raw.get("origins", []):
        origin_url = origin_entry.get("origin", "")
        try:
            hostname = urlparse(origin_url).hostname or ""
        except Exception:
            hostname = ""
        if _velog_host_allowed(hostname):
            filtered_origins.append(origin_entry)

    return {"cookies": filtered_cookies, "origins": filtered_origins}


# ── Core logic ────────────────────────────────────────────────────────────────

def _do_login(output_path: Path) -> None:
    """Open headed browser, wait for login, export cookies to *output_path*."""
    if sync_playwright is None:
        raise DependencyError(
            "Playwright is not installed.\n"
            "Run: pip install playwright && playwright install chromium\n"
            "Then retry: backlink-publisher velog-login"
        )

    log.info("velog-login: opening headed browser at %s", _VELOG_LOGIN_URL)
    print(
        "\n[velog-login] A browser window will open.\n"
        "  1. Complete social login (Google / GitHub / Facebook).\n"
        "  2. Wait until your velog home feed loads.\n"
        "  3. The window will close automatically.\n",
        flush=True,
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(_VELOG_LOGIN_URL)

        # Primary: wait for any non-login/signup URL (home feed)
        try:
            import re
            page.wait_for_url(
                re.compile(r"https://velog\.io/(?!(login|signup)($|/))"),
                timeout=_LOGIN_TIMEOUT_MS,
            )
        except _PWTimeout:
            # Fallback: probe for logged-in DOM element
            try:
                page.wait_for_selector(
                    "a[href*='/write'], a[aria-label*='글쓰기'], img[alt*='profile']",
                    timeout=30_000,
                )
            except _PWTimeout:
                browser.close()
                raise DependencyError(
                    "Login timeout after 5 minutes.\n"
                    "Ensure you completed the social login and any 2FA / email confirm.\n"
                    "Run again: backlink-publisher velog-login"
                )

        raw_cookies = context.cookies()
        browser.close()

    filtered = _filter_velog_cookies(raw_cookies)
    if not filtered:
        raise DependencyError(
            "No velog.io cookies found after login.\n"
            "The social login may not have completed. Run again: backlink-publisher velog-login"
        )

    payload = {"cookies": filtered}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o077)
    try:
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    finally:
        os.umask(old_umask)
    os.chmod(output_path, 0o600)

    cookie_names = [c.get("name", "?") for c in filtered]
    print(
        f"\n[velog-login] ✔ Cookies saved to {output_path} (0600)",
        flush=True,
    )
    print(f"  Stored cookies: {cookie_names}", flush=True)
    print(
        "\nNext steps:",
        "  Run: backlink-publisher publish-backlinks --platform velog --dry-run targets.csv",
        "  Or refresh your /settings page in the browser — the velog channel badge will turn green.",
        sep="\n",
        flush=True,
    )


# ── CLI entry point ───────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="backlink-publisher velog-login",
        description=(
            "Open a headed browser for velog.io social login and "
            "export credentials to a 0600 JSON file."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Path to write velog-cookies.json "
            "(default: ~/.config/backlink-publisher/velog-cookies.json)"
        ),
    )
    args = parser.parse_args(argv)

    if args.output is None:
        from backlink_publisher import config as _cfg
        output_path = _cfg._config_dir() / "velog-cookies.json"
    else:
        output_path = args.output.expanduser()

    try:
        _do_login(output_path)
    except DependencyError as exc:
        print(f"\n[velog-login] ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
