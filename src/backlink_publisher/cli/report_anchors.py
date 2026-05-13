"""Report anchor-text distribution across backlink article payloads."""

from __future__ import annotations

import collections
import json
import sys
from typing import Any

from ..anchor_profile import (
    ProfileState,
    load_profile,
    recent_degradation_rate,
    recent_type_counts,
    recent_url_category_counts,
)
from ..config import ANCHOR_TYPES, load_config

# Brainstorm-defined alarm threshold for systemic LLM rejection or pool
# exhaustion. Anything above this in the rolling 100 indicates the
# scheduler is hitting the degrade path too often to trust the
# distribution numbers.
_DEGRADATION_ALARM_PCT: float = 10.0

# Minimum entries below which deviation numbers are statistically meaningless.
# Plan v2 says distribution targets are evaluated after 50 articles; reports
# emit a warning when the profile is thinner than that.
_RELIABLE_SAMPLE_MIN: int = 50

# How many of the most-repeated anchor texts to show in the report.
_TOP_TEXTS_N: int = 20


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


# ─── --from-profile path (zh-CN short-form scheduler observability) ─────────


def _build_profile_report(
    profile: ProfileState,
    target_proportions: dict[str, float],
) -> dict[str, Any]:
    """Compile the report payload from a sliding-window ProfileState.

    Pure function — accepts an in-memory state and target proportions, returns
    a dict the formatter can render either as Markdown or JSON. Splitting the
    aggregation from the formatting keeps both forms in sync without
    duplicating the math.
    """
    total = len(profile.entries)
    type_counts = recent_type_counts(profile)
    deg_rate = recent_degradation_rate(profile)

    # Per-type deviation against the target proportions.
    type_stats: dict[str, dict[str, float]] = {}
    for t in ANCHOR_TYPES:
        count = type_counts.get(t, 0)
        actual = count / total if total > 0 else 0.0
        target = target_proportions.get(t, 0.0)
        type_stats[t] = {
            "count": count,
            "actual_pct": actual * 100,
            "target_pct": target * 100,
            "deviation_pp": (actual - target) * 100,
        }

    # url_category × anchor_type cross-tab. Defaultdict so missing combos
    # render as zero in the formatter without conditional plumbing here.
    cross: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for entry in profile.entries:
        cross[entry.url_category][entry.anchor_type] += 1

    # Top N most-repeated anchor texts — Success Criteria #2 observability.
    text_counter = collections.Counter(e.anchor_text for e in profile.entries)

    return {
        "main_domain": profile.main_domain,
        "total_entries": total,
        "type_stats": type_stats,
        "url_cat_cross": {k: dict(v) for k, v in cross.items()},
        "degradation_rate_pct": deg_rate * 100,
        "top_texts": text_counter.most_common(_TOP_TEXTS_N),
    }


def _format_profile_report_markdown(report: dict[str, Any]) -> str:
    """Render the profile report as a Markdown document."""
    out: list[str] = []
    out.append(f"# Anchor Profile Report: {report['main_domain']}")
    out.append("")
    out.append(f"Total entries (rolling window): **{report['total_entries']}**")

    if report["total_entries"] < _RELIABLE_SAMPLE_MIN:
        out.append("")
        out.append(
            f"⚠️ Sample size ({report['total_entries']}) is below "
            f"{_RELIABLE_SAMPLE_MIN} — deviation values are not yet reliable."
        )

    # Degradation rate — flagged with ⚠️ above the alarm threshold so the
    # operator sees the systemic-rejection signal at a glance.
    deg = report["degradation_rate_pct"]
    deg_marker = " ⚠️" if deg > _DEGRADATION_ALARM_PCT else ""
    out.append("")
    out.append(f"**Degradation Rate (rolling 100): {deg:.1f}%{deg_marker}**")
    if deg > _DEGRADATION_ALARM_PCT:
        out.append(
            f"> Degradation rate exceeds {_DEGRADATION_ALARM_PCT:.0f}% — investigate "
            "LLM provider rejections or typed-pool shortfalls."
        )

    # Anchor type distribution.
    out.append("")
    out.append("## Anchor Type Distribution")
    out.append("")
    out.append("| Type | Count | Actual % | Target % | Deviation (pp) |")
    out.append("|---|---|---|---|---|")
    for t in ANCHOR_TYPES:
        s = report["type_stats"][t]
        out.append(
            f"| {t} | {s['count']} | {s['actual_pct']:.1f}% | "
            f"{s['target_pct']:.1f}% | {s['deviation_pp']:+.1f} |"
        )

    # URL category × anchor type cross-tab.
    out.append("")
    out.append("## URL Category × Anchor Type")
    out.append("")
    cats = sorted(report["url_cat_cross"].keys())
    if cats:
        header = "| Category | " + " | ".join(ANCHOR_TYPES) + " | Total |"
        sep = "|---|" + "---|" * (len(ANCHOR_TYPES) + 1)
        out.append(header)
        out.append(sep)
        for cat in cats:
            cross = report["url_cat_cross"][cat]
            row = f"| {cat} |"
            cat_total = 0
            for t in ANCHOR_TYPES:
                c = cross.get(t, 0)
                cat_total += c
                row += f" {c} |"
            row += f" {cat_total} |"
            out.append(row)
    else:
        out.append("_(no entries)_")

    # Top repeated anchor texts.
    out.append("")
    out.append(f"## Top {_TOP_TEXTS_N} Most-Used Anchor Texts")
    out.append("")
    if report["top_texts"]:
        out.append("| Anchor Text | Count |")
        out.append("|---|---|")
        for text, count in report["top_texts"]:
            out.append(f"| {text} | {count} |")
    else:
        out.append("_(no entries)_")

    return "\n".join(out)


def _format_profile_report_json(report: dict[str, Any]) -> str:
    # Convert top_texts tuples to lists so the JSON is round-trippable.
    serializable = dict(report)
    serializable["top_texts"] = [list(item) for item in report["top_texts"]]
    return json.dumps(serializable, ensure_ascii=False, indent=2)


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
        "--from-profile",
        metavar="MAIN_DOMAIN",
        default=None,
        help=(
            "Read from the anchor profile JSON for the given site instead of "
            "JSONL payloads. Reports type distribution vs. target, URL "
            "category × type cross-tab, degradation rate, and top repeated "
            "anchor texts. Only meaningful for sites using the zh-CN "
            "short-form scheduler."
        ),
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

    if args.from_profile:
        # ── Profile-based report path ────────────────────────────────────
        # Load config to pull the target proportions; missing config is fine
        # (defaults to Safe SEO) — we want to be useful even before the user
        # has wired up the full scheduler config.
        cfg = load_config()
        profile = load_profile(args.from_profile)
        report = _build_profile_report(profile, cfg.anchor_proportions)
        if args.json:
            print(_format_profile_report_json(report))
        else:
            print(_format_profile_report_markdown(report))
        return

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
