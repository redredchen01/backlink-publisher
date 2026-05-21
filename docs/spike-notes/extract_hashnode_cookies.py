#!/usr/bin/env python3
"""Extract cookies from a Chromium-profile SQLite into Playwright cookies JSON.

Use when bind succeeded (auth cookies present) but the recipe's bound_predicate
URL regex didn't match — converts the live profile state into the same
hashnode-cookies.json schema the production adapter will read.

Chromium encrypts cookie values on macOS via Keychain; this script uses the
`chrome` library if available, else falls back to attempting decryption via
keyring. For SPIKE purposes we accept partial value extraction.

Usage:
    python3 docs/spike-notes/extract_hashnode_cookies.py \\
        --config-dir /tmp/hn-spike-config \\
        --out /tmp/hn-spike-config/hashnode-cookies.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _chrome_epoch_to_unix(chrome_epoch_us: int) -> float:
    if not chrome_epoch_us:
        return -1
    return (chrome_epoch_us / 1_000_000) - 11644473600


def _decrypt_value_macos(encrypted: bytes) -> str:
    """Best-effort decrypt of Chrome cookie value on macOS.

    Chrome on macOS encrypts with AES-128-CBC, key derived from a Keychain
    password "Chrome Safe Storage". This requires `pycryptodome` + Keychain
    access. For spike, we return a hex preview if decryption is unavailable —
    the cookie set works for Playwright's `add_cookies` ONLY if we have real
    plaintext values. If encryption blocks us, fall back to CDP-driven
    extraction (re-launch Chrome with same profile, ask CDP for cookies which
    are returned plaintext).
    """
    if not encrypted:
        return ""
    if not encrypted.startswith(b"v10") and not encrypted.startswith(b"v11"):
        # Old-format value or unencrypted
        try:
            return encrypted.decode("utf-8")
        except UnicodeDecodeError:
            return f"<binary-{len(encrypted)}b>"
    # Encrypted (v10/v11) — needs Keychain password
    try:
        from Cryptodome.Cipher import AES
        from Cryptodome.Protocol.KDF import PBKDF2
    except ImportError:
        try:
            from Crypto.Cipher import AES
            from Crypto.Protocol.KDF import PBKDF2
        except ImportError:
            return f"<encrypted-{len(encrypted)}b-need-pycryptodome>"
    try:
        password = subprocess.check_output(
            ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage"],
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return f"<encrypted-{len(encrypted)}b-keychain-blocked>"
    key = PBKDF2(password, b"saltysalt", dkLen=16, count=1003)
    iv = b" " * 16
    enc_value = encrypted[3:]
    try:
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(enc_value)
        # PKCS#7 unpad
        pad_len = decrypted[-1]
        if pad_len < 16:
            decrypted = decrypted[:-pad_len]
        return decrypted.decode("utf-8")
    except Exception as exc:
        return f"<decrypt-failed-{type(exc).__name__}>"


def extract(profile_dir: Path, host_filter: callable) -> list[dict]:
    db = profile_dir / "Default" / "Cookies"
    if not db.exists():
        print(f"error: Cookies SQLite missing: {db}", file=sys.stderr)
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT host_key, name, value, encrypted_value, path, is_secure, "
        "is_httponly, expires_utc, samesite "
        "FROM cookies"
    ).fetchall()
    conn.close()
    same_site_names = {0: "None", 1: "Lax", 2: "Strict", -1: "None"}
    out = []
    for host, name, plain, enc, path, secure, httponly, expires_utc, ss in rows:
        if not host_filter(host):
            continue
        value = plain or _decrypt_value_macos(enc) if enc else plain
        out.append({
            "name": name,
            "value": value,
            "domain": host,
            "path": path,
            "secure": bool(secure),
            "httpOnly": bool(httponly),
            "sameSite": same_site_names.get(ss, "None"),
            "expires": _chrome_epoch_to_unix(expires_utc),
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    profile_dir = Path(args.config_dir).expanduser().resolve() / "real-chrome-profile"
    out_path = Path(args.out).expanduser().resolve()

    def host_filter(h: str) -> bool:
        h = (h or "").lower().lstrip(".")
        return h == "hashnode.com" or h.endswith(".hashnode.dev")

    cookies = extract(profile_dir, host_filter)
    print(f"# Extracted {len(cookies)} cookies for hashnode.com / *.hashnode.dev", file=sys.stderr)
    for c in cookies:
        print(f"#   {c['domain']}/{c['name']} httpOnly={c['httpOnly']} value_decoded={c['value'][:60]!r}", file=sys.stderr)

    payload = {"cookies": cookies, "_source": "spike-extract", "_extracted_at": datetime.now(timezone.utc).isoformat()}
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    out_path.chmod(0o600)
    print(f"\nOK: wrote {len(cookies)} cookies to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
