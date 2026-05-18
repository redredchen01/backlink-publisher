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
import os
import re
import sys
from pathlib import Path
from typing import Any

from ..footprint import (
    SCHEMA_VERSION,
    FootprintReport,
    _top_by_count_then_lex,
    analyze_corpus,
    format_report_markdown,
)
from ..footprint_corpus import (
    CORPUS_NAMES,
    compute_fixture_set_id,
    make_corpus,
)


# ---------------------------------------------------------------------------
# --reason denylist (Plan Key Decisions G2.3, success criterion #3)
# ---------------------------------------------------------------------------

_REASON_RUBBER_STAMP_RE = re.compile(
    r"^(regen|regenerate|update|fix|bump|wip|.{0,15})$", re.IGNORECASE
)


def _validate_reason(reason: str) -> None:
    """Raise ``argparse``-style error if ``reason`` is too generic.

    Multi-line reasons whose first line is a banned verb (adversarial finding:
    ``$'regen\\nfollow-up...'`` bypassed the regex because ``.`` doesn't span
    newlines) are explicitly rejected — audit-trail belongs in commit messages.
    """
    stripped = reason.strip()
    if "\n" in stripped:
        raise SystemExit(
            f"error: --reason must be a single line (got multi-line value: {reason!r}). "
            "Put detail in the commit message; keep the baseline reason one-line."
        )
    if _REASON_RUBBER_STAMP_RE.match(stripped):
        raise SystemExit(
            f"error: --reason rejected as rubber-stamp ({reason!r}). "
            "Provide a substantive explanation (more than 15 chars, "
            "not just 'regen'/'fix'/'update'/etc.). This guards against "
            "drift-by-attrition. See Plan Unit 2 Key Decisions."
        )


# ---------------------------------------------------------------------------
# PYTHONHASHSEED guard (Plan Key Decisions G2.1)
# ---------------------------------------------------------------------------


def _require_pythonhashseed_zero(invocation_hint: str) -> None:
    """Refuse to run if PYTHONHASHSEED is not '0' at interpreter startup.

    Accepts trailing whitespace / equivalent forms like '00' — anything that
    parses to integer 0 is considered pinned (correctness-reviewer edge case).
    """
    val = os.environ.get("PYTHONHASHSEED")
    try:
        is_zero = val is not None and int(val.strip()) == 0
    except (ValueError, AttributeError):
        is_zero = False
    if not is_zero:
        raise SystemExit(
            f"error: {invocation_hint} requires PYTHONHASHSEED=0 at interpreter "
            f"startup (current value: {val!r}). Run as:\n"
            f"    PYTHONHASHSEED=0 {invocation_hint}\n"
            "Setting the env var inside Python after startup is a no-op — Python "
            "freezes hash randomization at process start."
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
# `baseline regenerate` subcommand
# ---------------------------------------------------------------------------


def _baseline_path(output_dir: Path, corpus_name: str) -> Path:
    return output_dir / f"footprint_concentration_{corpus_name}.json"


def _compute_baseline_record(corpus_name: str, reason: str) -> dict[str, Any]:
    """Generate a corpus, run analyze_corpus, format the baseline JSON record."""
    payloads = make_corpus(corpus_name)
    report = analyze_corpus(payloads)
    if report.total_links == 0:
        raise SystemExit(
            f"error: corpus {corpus_name!r} produced zero links — fixture "
            "or renderer regression suspected. Investigate before regen."
        )
    # Use _top_by_count_then_lex consistently across all 4 dimensions — we
    # want the lex-tie-broken TOP-BY-COUNT bucket, not the lex-smallest
    # arbitrary key (correctness + kieran-python flagged this drift).
    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_set_id": compute_fixture_set_id(corpus_name),
        "reason": reason,
        "concentration_pct": {
            "attr_order": round(report.concentration_pct("attr_order"), 4),
            "rel_value": round(report.concentration_pct("rel_value"), 4),
            "target_value": round(report.concentration_pct("target_value"), 4),
            "preceding_char": round(report.concentration_pct("preceding_char"), 4),
        },
        "top_values": {
            "attr_order": list(_top_by_count_then_lex(report.attr_order_counts, 1)[0][0])
            if report.attr_order_counts
            else [],
            "rel_value": _top_by_count_then_lex(report.rel_value_counts, 1)[0][0]
            if report.rel_value_counts
            else "",
            "target_value": _top_by_count_then_lex(report.target_value_counts, 1)[0][0]
            if report.target_value_counts
            else "",
            "preceding_char": _top_by_count_then_lex(report.preceding_char_counts, 1)[0][0]
            if report.preceding_char_counts
            else "",
        },
    }


def _serialize_baseline(record: dict[str, Any]) -> str:
    """Sorted keys, 2-space indent, trailing newline — minimal git diff."""
    return json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _read_existing_baseline(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _print_diff_summary(
    corpus_name: str, old: dict[str, Any] | None, new: dict[str, Any]
) -> None:
    """Human-readable before/after summary to stderr."""
    print(f"\n=== {corpus_name} ===", file=sys.stderr)
    if old is None:
        print("  (new baseline — no prior file)", file=sys.stderr)
    else:
        if old.get("schema_version") != new["schema_version"]:
            print(f"  schema_version: {old.get('schema_version')} → {new['schema_version']}", file=sys.stderr)
        if old.get("fixture_set_id") != new["fixture_set_id"]:
            print(f"  fixture_set_id: {old.get('fixture_set_id')} → {new['fixture_set_id']}", file=sys.stderr)
    old_conc = (old or {}).get("concentration_pct", {})
    for dim, new_val in new["concentration_pct"].items():
        old_val = old_conc.get(dim)
        if old_val is None:
            print(f"  {dim}: → {new_val:.2f}%", file=sys.stderr)
        elif abs(old_val - new_val) >= 0.01:
            delta = new_val - old_val
            sign = "+" if delta >= 0 else ""
            print(
                f"  {dim}: {old_val:.2f}% → {new_val:.2f}% ({sign}{delta:.2f}pp)",
                file=sys.stderr,
            )
    old_tops = (old or {}).get("top_values", {})
    for dim, new_top in new["top_values"].items():
        old_top = old_tops.get(dim)
        if old_top is not None and old_top != new_top:
            print(f"  top {dim}: {old_top!r} → {new_top!r}", file=sys.stderr)


def _run_regenerate(args: argparse.Namespace) -> None:
    """``footprint baseline regenerate --path … --reason …``."""
    _require_pythonhashseed_zero("footprint baseline regenerate")
    _validate_reason(args.reason)

    output_dir = Path(args.output_dir).resolve()
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)

    targets = (
        list(CORPUS_NAMES) if args.path == "all" else [args.path]
    )

    # Phase 1: compute all records (any failure aborts before any rename).
    new_records: dict[str, dict[str, Any]] = {}
    for corpus_name in targets:
        new_records[corpus_name] = _compute_baseline_record(corpus_name, args.reason)

    # Phase 2: write all .tmp files. If any write fails mid-loop, unlink
    # already-written .tmp files (correctness-reviewer atomicity gap).
    tmp_paths: list[tuple[Path, Path]] = []
    try:
        for corpus_name, record in new_records.items():
            final = _baseline_path(output_dir, corpus_name)
            tmp = final.with_suffix(final.suffix + ".tmp")
            tmp.write_text(_serialize_baseline(record), encoding="utf-8")
            tmp_paths.append((tmp, final))
    except Exception:
        for tmp, _final in tmp_paths:
            tmp.unlink(missing_ok=True)
        raise

    # Phase 3: emit diff summary (operators see deltas BEFORE the rename).
    for corpus_name, record in new_records.items():
        final = _baseline_path(output_dir, corpus_name)
        old = _read_existing_baseline(final)
        _print_diff_summary(corpus_name, old, record)

    # Phase 4: rename all. Per-file rename is atomic via rename(2). Sequenced.
    for tmp, final in tmp_paths:
        tmp.replace(final)

    print(
        f"\n✓ wrote {len(tmp_paths)} baseline(s) to {output_dir}",
        file=sys.stderr,
    )


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
    # falls through to the default audit. This is the project's first
    # subparser pattern; documented in plan Unit 2.
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
