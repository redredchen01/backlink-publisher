#!/usr/bin/env python3
"""Gate for spike capture commits: refuse if the file still carries
header/cookie values, Set-Cookie lines, or token-shaped strings.

Spec: docs/plans/2026-05-20-003-feat-portfolio-roundtrip-spike-quality-plan.md
(Phase B credential-scrub procedure B.1a).

Usage:
    python scripts/scrub-spike-capture.py --check <path>

Exit codes:
    0  no findings; safe to commit
    2  findings present; refuse commit (stdout lists offending lines)
    3  usage error (missing path, file unreadable)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ALLOWLIST = {
    "sid", "uid", "xsrf", "lightstep-access-token", "x-xsrf-token",
    "sessionid", "csrf", "authorization",
}

HEADER_VALUE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_-]+)\s*[:=]\s*(?P<value>\S.*)$"
)
SET_COOKIE = re.compile(r"set-cookie\s*[:=]", re.IGNORECASE)
TOKEN_SHAPE = re.compile(
    r"\b(?=[A-Za-z0-9_-]*[A-Z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{20,}\b"
)

REDACTED_MARKERS = {"REDACTED", "<redacted>", "<scrubbed>", "***"}


def looks_redacted(value: str) -> bool:
    stripped = value.strip().strip("\"'`")
    if not stripped:
        return True
    if stripped in REDACTED_MARKERS:
        return True
    if stripped.lower() in {"redacted", "scrubbed", "n/a", "none"}:
        return True
    return False


def scan(path: Path) -> list[str]:
    findings: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if SET_COOKIE.search(raw):
            if not looks_redacted(raw.split(":", 1)[-1] if ":" in raw else raw):
                findings.append(f"{path}:{lineno}: Set-Cookie value not scrubbed")
            continue

        match = HEADER_VALUE.match(raw)
        if match and match.group("name").lower() in ALLOWLIST:
            if not looks_redacted(match.group("value")):
                findings.append(
                    f"{path}:{lineno}: header '{match.group('name')}' value not scrubbed"
                )
            continue

        for token in TOKEN_SHAPE.findall(raw):
            if token.lower() in ALLOWLIST:
                continue
            if looks_redacted(token):
                continue
            findings.append(f"{path}:{lineno}: token-shaped string '{token[:8]}...' (len {len(token)})")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", required=True, type=Path, help="file to scan")
    args = parser.parse_args()

    if not args.check.exists() or not args.check.is_file():
        print(f"error: {args.check} not a readable file", file=sys.stderr)
        return 3

    findings = scan(args.check)
    if not findings:
        print(f"OK: {args.check} clean (0 findings)")
        return 0

    print(f"FAIL: {args.check} ({len(findings)} findings)")
    for line in findings:
        print(f"  {line}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
