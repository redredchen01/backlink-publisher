"""P0-1b: deliberate-bad-input harvest for _KNOWN_EXTENSIONS_CODES baseline.

Fires 5 bad-input writePost calls + 1 introspection probe for refresh mutation.
Output: docs/spikes/velog_p0_1b_responses.jsonl (gitignored).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

SPIKE = Path(__file__).resolve().parents[2] / "docs" / "spikes"
COOKIES = (SPIKE / "velog_cookies_flat.txt").read_text().strip()
HEADERS_BASE = json.loads((SPIKE / "velog_required_headers.json").read_text())
OUT = SPIKE / "velog_p0_1b_responses.jsonl"

ENDPOINT = "https://v2.velog.io/graphql"

WRITEPOST_QUERY = (
    "mutation WritePost($title: String, $body: String, $tags: [String], "
    "$is_markdown: Boolean, $is_temp: Boolean, $is_private: Boolean, "
    "$url_slug: String, $thumbnail: String, $meta: JSON, $series_id: ID, "
    "$token: String) { writePost(title: $title, body: $body, tags: $tags, "
    "is_markdown: $is_markdown, is_temp: $is_temp, is_private: $is_private, "
    "url_slug: $url_slug, thumbnail: $thumbnail, meta: $meta, "
    "series_id: $series_id, token: $token) { id user { id username "
    "__typename } url_slug __typename } }"
)


def make_vars(**overrides):
    base = {
        "title": "[P0-1b spike] harvest probe",
        "body": "harvest",
        "tags": [],
        "is_markdown": True,
        "is_temp": True,
        "is_private": True,
        "url_slug": None,
        "thumbnail": None,
        "meta": {},
        "series_id": None,
        "token": None,
    }
    base.update(overrides)
    return base


CASES = [
    {
        "case": "1_empty_title",
        "variables": make_vars(title=""),
        "with_cookie": True,
    },
    {
        "case": "2_oversize_body",
        "variables": make_vars(body="x" * 100_000),
        "with_cookie": True,
    },
    {
        "case": "3_xss_tag",
        "variables": make_vars(tags=["<script>alert(1)</script>"]),
        "with_cookie": True,
    },
    {
        "case": "4_missing_is_markdown",
        "variables": {
            "title": "[P0-1b] missing is_markdown",
            "body": "x",
            "tags": [],
            "is_temp": True,
            "is_private": True,
            "url_slug": None,
            "thumbnail": None,
            "meta": {},
            "series_id": None,
            "token": None,
        },
        "with_cookie": True,
    },
    {
        "case": "5_no_cookie",
        "variables": make_vars(),
        "with_cookie": False,
    },
]


def request(case):
    headers = {k: v for k, v in HEADERS_BASE.items()}
    if case["with_cookie"]:
        headers["cookie"] = COOKIES
    body = {
        "operationName": "WritePost",
        "query": WRITEPOST_QUERY,
        "variables": case["variables"],
    }
    try:
        r = requests.post(ENDPOINT, json=body, headers=headers, timeout=30)
        try:
            resp_json = r.json()
        except ValueError:
            resp_json = {"_non_json_body": r.text[:500]}
        return {
            "case": case["case"],
            "http_status": r.status_code,
            "response": resp_json,
        }
    except requests.RequestException as e:
        return {"case": case["case"], "http_status": None, "error": str(e)}


def introspect_refresh():
    """Probe for refresh-token mutation existence."""
    query = """
    {
      __schema {
        mutationType {
          fields {
            name
            description
            args { name type { name kind ofType { name kind } } }
          }
        }
      }
    }
    """
    r = requests.post(
        ENDPOINT,
        json={"query": query},
        headers={**HEADERS_BASE, "cookie": COOKIES},
        timeout=30,
    )
    try:
        data = r.json()
        fields = data.get("data", {}).get("__schema", {}).get("mutationType", {}).get("fields", []) or []
        # Filter for refresh / token / auth related names
        candidates = [
            f for f in fields
            if re.search(r"refresh|token|login|auth|session", f.get("name", ""), re.I)
        ]
        return {
            "http_status": r.status_code,
            "introspection_supported": bool(fields),
            "total_mutations": len(fields),
            "auth_related_mutations": candidates,
        }
    except Exception as e:
        return {"http_status": r.status_code, "error": str(e), "body_preview": r.text[:500]}


def main():
    lines = []
    print("=== P0-1b: 5 bad-input harvest ===")
    for case in CASES:
        result = request(case)
        lines.append(json.dumps(result, ensure_ascii=False))
        errs = result.get("response", {}).get("errors") or []
        codes = [e.get("extensions", {}).get("code") for e in errs] if errs else []
        msgs = [e.get("message", "")[:80] for e in errs] if errs else []
        print(f"  [{result['case']}] HTTP {result['http_status']}  codes={codes}  msg={msgs}")

    print("\n=== refresh-mutation introspection ===")
    intro = introspect_refresh()
    lines.append(json.dumps({"case": "introspection_auth_mutations", **intro}, ensure_ascii=False))
    if intro.get("auth_related_mutations"):
        for m in intro["auth_related_mutations"]:
            print(f"  → {m['name']}({[a['name'] for a in m['args']]})")
    else:
        print(f"  no auth-related mutations found (status={intro.get('http_status')}, total={intro.get('total_mutations')})")
        if intro.get("body_preview"):
            print(f"  body: {intro['body_preview']}")

    OUT.write_text("\n".join(lines) + "\n")
    print(f"\nWrote {OUT}")

    # Summary
    all_codes = set()
    for line in lines:
        rec = json.loads(line)
        for err in (rec.get("response", {}) or {}).get("errors", []) or []:
            code = err.get("extensions", {}).get("code")
            if code:
                all_codes.add(code)
    print(f"\nDistinct error codes harvested: {sorted(all_codes)}")
    print(f"Count: {len(all_codes)} (P0-1b passes at >= 3)")


if __name__ == "__main__":
    main()
