"""CLI: ``footprint`` — offline self-fingerprint audit + baseline regen.

Two operating modes (backwards-compatible: pre-existing pipeline invocation
``cat payloads.jsonl | footprint --json`` is unchanged):

1. **Default (no subcommand)** — read a JSONL stream of payloads from stdin
   or ``--input``, run :func:`analyze_corpus`, print markdown (or
   ``--json``) summary. Identical to the pre-R11 behavior; tied-dimension
   top-value rendering may flip under R11's deterministic lex-tie-break
   (documented in the PR's CHANGELOG; not a regression).

2. **``baseline regenerate``** — regenerate one or all gate baselines for
   the Footprint Regression Gate (Plan Unit 4 + Unit 2 / R7). Refuses to
   run without ``PYTHONHASHSEED=0`` at interpreter startup. Writes atomic
   ``.tmp`` files first then renames all on success.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..footprint import (
    FootprintReport,
    _top_by_count_then_lex,
    analyze_corpus,
    format_report_markdown,
)
from ..footprint_corpus import CORPUS_NAMES, compute_fixture_set_id, make_corpus

# ---------------------------------------------------------------------------
# Re-export from _footprint_baseline (backward compatibility)
# ---------------------------------------------------------------------------
from ._footprint_baseline import (
    _REASON_RUBBER_STAMP_RE,
    _baseline_path,
    _compute_baseline_record,
    _print_diff_summary,
    _read_existing_baseline,
    _require_pythonhashseed_zero,
    _run_regenerate,
    _serialize_baseline,
    _validate_reason,
)


# ---------------------------------------------------------------------------
# Default-audit mode (backwards-compat with `cat payloads.jsonl | footprint`)
# ---------------------------------------------------------------------------


def _payload_html(payload: dict[str, Any]) -> str:
    """Pick the HTML / markdown field this payload uses."""
    for key in ("content_html", "content_markdown", "body_html", "html"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _report_to_json(report: FootprintReport) -> dict[str, Any]:
    return {
        "total_links": report.total_links,
        "total_payloads": report.total_payloads,
        "payloads_without_links": report.payloads_without_links,
        "attr_order_counts": {
            " → ".join(k): v for k, v in report.attr_order_counts.items()
        },
        "rel_value_counts": dict(report.rel_value_counts),
        "target_value_counts": dict(report.target_value_counts),
        "preceding_char_counts": dict(report.preceding_char_counts),
        "concentration_pct": {
            "attr_order": report.concentration_pct("attr_order"),
            "rel_value": report.concentration_pct("rel_value"),
            "target_value": report.concentration_pct("target_value"),
            "preceding_char": report.concentration_pct("preceding_char"),
        },
    }


def _run_default_audit(args: argparse.Namespace) -> None:
    fh = args.input or sys.stdin
    payloads: list[str] = []
    for lineno, line in enumerate(fh, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            print(f"WARN: line {lineno}: malformed JSON — {exc}", file=sys.stderr)
            continue
        payloads.append(_payload_html(payload))

    report = analyze_corpus(payloads)

    if args.json:
        print(json.dumps(_report_to_json(report), ensure_ascii=False, indent=2))
    else:
        print(format_report_markdown(report, alarm_pct=args.alarm_pct))


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="footprint",
        description=(
            "Offline self-fingerprint audit. By default reads a JSONL stream "
            "of plan-backlinks payloads from stdin (or --input) and reports "
            "byte-level patterns that appear in 100% of links. The "
            "`baseline regenerate` subcommand (used by the Footprint "
            "Regression Gate, Plan Unit 4/7) regenerates committed gate "
            "baselines."
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=argparse.FileType("r"),
        default=None,
        help="Payload JSONL file for the default audit (default: stdin)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output the default audit's raw report as JSON instead of Markdown",
    )
    parser.add_argument(
        "--alarm-pct",
        type=float,
        default=95.0,
        help=(
            "Concentration threshold (percent) above which a dimension is "
            "flagged as CLUSTER KEY in the markdown report (default: 95)"
        ),
    )

    # `add_subparsers(dest='command')` WITHOUT required=True keeps the
    # backwards-compat path: `cat … | footprint --json` (no subcommand)
    # falls through to the default audit.
    subparsers = parser.add_subparsers(dest="command")

    baseline = subparsers.add_parser(
        "baseline",
        help="Manage Footprint Regression Gate baselines",
    )
    baseline_sub = baseline.add_subparsers(dest="baseline_command")
    regen = baseline_sub.add_parser(
        "regenerate",
        help="Regenerate one or all baselines",
    )
    regen.add_argument(
        "--path",
        choices=[*CORPUS_NAMES, "all"],
        default="all",
        help="Which corpus to regenerate (default: all)",
    )
    regen.add_argument(
        "--reason",
        required=True,
        help=(
            "Substantive one-line explanation of why this regen is correct. "
            "Rejects generic strings like 'regen' / 'fix' / 'update'."
        ),
    )
    regen.add_argument(
        "--output-dir",
        default="tests/baselines",
        help="Directory holding the baseline JSONs (default: tests/baselines)",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "baseline":
        if args.baseline_command == "regenerate":
            _run_regenerate(args)
            return
        # baseline with no subcommand → show its help
        parser.parse_args(["baseline", "--help"])
        return

    # No subcommand → default audit (backwards-compat)
    _run_default_audit(args)


if __name__ == "__main__":
    main()
