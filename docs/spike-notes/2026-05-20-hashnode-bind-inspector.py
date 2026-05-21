#!/usr/bin/env python3
"""Hashnode bind spike — post-bind cookie/storage-state inspector.

Reads the storage_state.json + browser-profile/Default/Cookies SQLite
produced by `bind-channel --channel hashnode` and dumps a redacted
cookie + URL summary so we can:

  - Build the Unit 2 cookie whitelist (positive auth indicators)
  - Build the Unit 2 cookie blacklist (CF / anonymous-tracking cookies)
  - Capture the actual post-login URL the operator landed on

Usage:

    BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/hn-spike-config \\
        bind-channel --channel hashnode

    # Operator completes login in opened Chrome window.

    python3 docs/spike-notes/2026-05-20-hashnode-bind-inspector.py \\
        --config-dir /tmp/hn-spike-config

Output goes to stdout + docs/spike-notes/<timestamp>-hashnode-bind-raw.json.
Cookie values are redacted by default (length + first 4 chars) — pass
--unsafe-show-values ONLY when manually inspecting.

NOT a production tool — discarded after Unit 1.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _redact(value: str, *, show: bool) -> dict:
    """Cookie value summary: length + prefix unless --unsafe-show-values."""
    if not value:
        return {"len": 0, "preview": ""}
    if show:
        return {"len": len(value), "value": value}
    return {"len": len(value), "preview": value[:4] + "…" if len(value) > 4 else value}


def _classify(cookie: dict) -> str:
    """Heuristic classification: 'auth_likely' | 'cf_baseline' | 'tracker' | 'unknown'.

    Used to build the Unit 2 sanity gate. Adjusted after spike.
    """
    name = cookie["name"].lower()
    # CF baseline blacklist starters
    if name in {"cf_clearance", "_cfuvid", "__cf_bm", "xsrf-token", "xsrf"}:
        return "cf_baseline"
    # Common tracker patterns
    if name.startswith(("_ga", "_gid", "_fb", "_hj", "amplitude_", "intercom-")):
        return "tracker"
    # Auth-likely heuristics: HttpOnly + long expiry + non-tracking name
    if cookie.get("http_only") and (cookie.get("expires_days") or 0) > 7:
        return "auth_likely"
    return "unknown"


def _read_storage_state(path: Path, *, show_values: bool) -> dict:
    """Load Playwright storage_state.json and classify cookies."""
    if not path.exists():
        return {"error": f"storage_state file missing: {path}"}
    try:
        data = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        return {"error": f"parse failed: {exc}"}
    cookies = data.get("cookies", [])
    now = datetime.now(timezone.utc).timestamp()
    summary = []
    for c in cookies:
        expires = c.get("expires", -1)
        expires_days = (expires - now) / 86400 if expires and expires > 0 else None
        item = {
            "name": c.get("name", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "http_only": c.get("httpOnly", False),
            "secure": c.get("secure", False),
            "same_site": c.get("sameSite", ""),
            "expires_days": round(expires_days, 1) if expires_days else None,
            "value_summary": _redact(c.get("value", ""), show=show_values),
        }
        item["classify"] = _classify(item)
        summary.append(item)
    summary.sort(key=lambda x: (x["classify"], x["domain"], x["name"]))
    return {
        "file": str(path),
        "cookie_count": len(cookies),
        "origins": data.get("origins", []),
        "cookies": summary,
    }


def _read_chromium_cookies(profile_dir: Path, *, show_values: bool) -> dict:
    """Read the Chromium SQLite Cookies DB if present (live profile)."""
    db = profile_dir / "Default" / "Cookies"
    if not db.exists():
        return {"error": f"Cookies SQLite missing: {db}"}
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT host_key, name, path, is_httponly, is_secure, "
            "expires_utc, samesite, length(value) "
            "FROM cookies "
            "WHERE host_key LIKE '%hashnode.com' OR host_key LIKE '%hashnode.dev'"
        ).fetchall()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"sqlite read failed: {exc}"}
    # Chrome epoch: microseconds since 1601-01-01
    chrome_epoch_offset = 11644473600
    now = datetime.now(timezone.utc).timestamp()
    out = []
    for host, name, path, http_only, secure, expires_utc, same_site, vlen in rows:
        expires_unix = (expires_utc / 1_000_000) - chrome_epoch_offset if expires_utc else 0
        expires_days = (expires_unix - now) / 86400 if expires_unix > 0 else None
        item = {
            "domain": host,
            "name": name,
            "path": path,
            "http_only": bool(http_only),
            "secure": bool(secure),
            "expires_days": round(expires_days, 1) if expires_days else None,
            "same_site": ["unspecified", "none", "lax", "strict"][same_site] if 0 <= same_site < 4 else str(same_site),
            "value_len": vlen,
        }
        item["classify"] = _classify(item)
        out.append(item)
    out.sort(key=lambda x: (x["classify"], x["domain"], x["name"]))
    return {"db": str(db), "cookie_count": len(out), "cookies": out}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config-dir",
        default=os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR"),
        help="Override config dir (defaults to BACKLINK_PUBLISHER_CONFIG_DIR env)",
    )
    parser.add_argument(
        "--unsafe-show-values",
        action="store_true",
        help="DANGER: print raw cookie values. Only for ad-hoc spike inspection.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Where to write the raw JSON dump (default: docs/spike-notes/<ts>-hashnode-bind-raw.json)",
    )
    args = parser.parse_args()

    if not args.config_dir:
        print("error: --config-dir or BACKLINK_PUBLISHER_CONFIG_DIR required", file=sys.stderr)
        return 2

    config_dir = Path(args.config_dir).expanduser().resolve()
    print(f"# Inspecting config_dir: {config_dir}", file=sys.stderr)

    storage_state = config_dir / "hashnode-storage-state.json"
    profile_dir = config_dir / "browser-profile"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_dir": str(config_dir),
        "storage_state": _read_storage_state(storage_state, show_values=args.unsafe_show_values),
        "live_chromium_profile": _read_chromium_cookies(profile_dir, show_values=args.unsafe_show_values),
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))

    out_path = args.out or Path(__file__).parent / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-hashnode-bind-raw.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"# Wrote raw report to: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
