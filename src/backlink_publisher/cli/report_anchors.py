"""Report anchor-text distribution across backlink article payloads."""

from __future__ import annotations

import collections
import json
import sys
from typing import Any


def _domain_label(main_domain: str) -> str:
    """Return bare domain for fallback detection (strips scheme + trailing slash)."""
    return main_domain.rstrip("/").removeprefix("https://").removeprefix("http://")


def _build_report(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate anchor stats per main_domain from payload JSONL rows."""
    stats: dict[str, dict[str, Any]] = {}

    for row in rows:
        main_domain = row.get("main_domain", "").rstrip("/")
        if not main_domain:
            continue
        links = row.get("links", [])
        if not isinstance(links, list):
            continue

        if main_domain not in stats:
            stats[main_domain] = {
                "total_articles": 0,
                "anchors": collections.Counter(),
                "fallback_count": 0,
            }

        entry = stats[main_domain]
        entry["total_articles"] += 1
        fallback_label = _domain_label(main_domain)
        article_has_fallback = False

        for link in links:
            if not isinstance(link, dict):
                continue
            if link.get("kind") not in ("main_domain", "target"):
                continue
            anchor = link.get("anchor", "")
            if not anchor:
                continue
            entry["anchors"][anchor] += 1
            if anchor == fallback_label:
                article_has_fallback = True

        if article_has_fallback:
            entry["fallback_count"] += 1

    return stats


def _markdown_table(
    stats: dict[str, dict[str, Any]],
    top_n: int,
) -> str:
    header = "| target | articles | distinct anchors | fallback % | top anchors |"
    sep = "|---|---|---|---|---|"
    rows = [header, sep]

    for domain in sorted(stats):
        s = stats[domain]
        total = s["total_articles"]
        counter: collections.Counter = s["anchors"]
        distinct = len(counter)
        fallback_pct = (
            f"{100 * s['fallback_count'] / total:.0f}%" if total else "—"
        )
        top = ", ".join(
            f"{kw!r} ({cnt})" for kw, cnt in counter.most_common(top_n)
        )
        rows.append(f"| {domain} | {total} | {distinct} | {fallback_pct} | {top} |")

    return "\n".join(rows)


def _json_output(stats: dict[str, dict[str, Any]]) -> str:
    out = {
        domain: {
            "total_articles": s["total_articles"],
            "anchors": dict(s["anchors"]),
            "fallback_count": s["fallback_count"],
        }
        for domain, s in sorted(stats.items())
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="report-anchors",
        description=(
            "Analyse anchor-text distribution across backlink article payloads. "
            "Reads payload JSONL (plan-backlinks output) from --input or stdin."
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=argparse.FileType("r"),
        default=None,
        help="Payload JSONL file (default: stdin)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of a Markdown table",
    )
    parser.add_argument(
        "--top-anchors",
        type=int,
        default=5,
        metavar="N",
        help="Number of top anchor keywords to show per target (default: 5)",
    )
    args = parser.parse_args(argv)

    fh = args.input or sys.stdin
    rows: list[dict[str, Any]] = []
    for lineno, raw in enumerate(fh, start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            print(f"WARN: line {lineno}: malformed JSON — {exc}", file=sys.stderr)

    stats = _build_report(rows)

    if args.json:
        print(_json_output(stats))
    else:
        print(_markdown_table(stats, top_n=args.top_anchors))
