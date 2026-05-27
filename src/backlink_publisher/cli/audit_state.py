"""audit-state — Dual-State Divergence Auditor (read-only diagnostic).

Diffs ``publish-history.json`` against the ``events.db`` ``articles`` table and
reports divergences the operator cannot otherwise see: NULL-``live_url`` orphans
(R1) and store drift (R3). stdout = JSONL findings, stderr = config banner +
human summary with per-class remediation. Always exits 0 on completion (a pure
diagnostic, not a gate); an *unreadable* store exits 3 (DependencyError) while an
*absent* store is a benign exit-0 "nothing to audit". No writes of any kind.
Plan 2026-05-26-001 Unit 3.
"""

from __future__ import annotations

import sys

import backlink_publisher.publishing.adapters  # noqa: F401  populate registry before config load
from .. import config_echo
from backlink_publisher._util.errors import (
    DependencyError,
    PipelineError,
    UsageError,
    handle_error,
)
from backlink_publisher._util.jsonl import write_jsonl
from backlink_publisher.audit import (
    AuditReadError,
    DivergenceRecord,
    find_divergences,
    read_snapshot,
)
from backlink_publisher.config import load_config

_FORMATS = {"jsonl"}

#: R12 — concrete manual remediation per finding class (auditor never writes).
_REMEDIATION: dict[str, str] = {
    "null_url_orphan": (
        "article row has no URL; re-run publish for this target or verify the "
        "link manually"
    ),
    "history_orphan": (
        "published URL not found in events.db; re-run publish to refresh, or "
        "confirm the link is live"
    ),
    "article_orphan": (
        "events.db has a link absent from history; verify the live URL on the web"
    ),
    "duplicate_key": (
        "two dedup keys resolve to one live URL; inspect both and `--forget` the "
        "stale key if one is a mistaken/re-published duplicate"
    ),
    "aged_uncertain": (
        "an uncertain hold has not been adjudicated; resolve it with "
        "`--adjudicate-uncertain ... --to (succeeded|failed)`"
    ),
    "aged_attempting": (
        "an attempting row outlived the publish-lease TTL (crashed run); `--forget` "
        "it to allow re-publish, or confirm the post then adjudicate"
    ),
    "suspect_done": (
        "a done row has no live_url (likely mis-seeded backfill); verify the post "
        "actually landed, else `--forget` so enforce will not skip a needed backlink"
    ),
}


def _emit_summary(records: list[DivergenceRecord], *, transient: bool) -> None:
    """R7 — human summary on stderr, high-signal vs informational separated."""
    if not records:
        print("audit-state: no divergence found.", file=sys.stderr)
        return

    by_tier: dict[str, dict[str, int]] = {}
    for rec in records:
        by_tier.setdefault(rec.source_tier, {})
        by_tier[rec.source_tier][rec.divergence_class] = (
            by_tier[rec.source_tier].get(rec.divergence_class, 0) + 1
        )

    for tier in ("high-signal", "informational"):
        counts = by_tier.get(tier)
        if not counts:
            continue
        total = sum(counts.values())
        breakdown = ", ".join(f"{cls}: {n}" for cls, n in sorted(counts.items()))
        print(
            f"audit-state: {total} divergence(s) [{tier}] — {breakdown}",
            file=sys.stderr,
        )

    seen_classes = {rec.divergence_class for rec in records}
    for cls in sorted(seen_classes):
        if cls in _REMEDIATION:
            print(f"  {cls}: {_REMEDIATION[cls]}", file=sys.stderr)

    if transient:
        print(
            "audit-state: a store changed during the read; findings marked "
            "'possibly-transient' may clear on re-run.",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="audit-state",
        description=(
            "Read-only auditor: diff publish-history.json against events.db "
            "articles and report NULL-live_url orphans (R1) and store drift "
            "(R3). JSONL findings on stdout; summary on stderr; always exit 0."
        ),
    )
    parser.add_argument(
        "--format",
        default="jsonl",
        metavar="FMT",
        help="findings output format (jsonl; default: jsonl)",
    )
    args = parser.parse_args(argv)

    try:
        # Closed-set validation post-parse (repo convention: UsageError exit 1,
        # not argparse's exit 2). See [[argparse-choices-vs-usage-error]].
        if args.format not in _FORMATS:
            raise UsageError(
                f"audit-state: --format must be one of {sorted(_FORMATS)}; "
                f"got {args.format!r}"
            )

        cfg = load_config()
        config_echo.emit_banner(cfg, "audit-state")

        try:
            snapshot = read_snapshot()
        except AuditReadError as exc:
            raise DependencyError(str(exc)) from exc

        if snapshot.nothing_to_audit:
            print(
                "audit-state: no stores to audit yet "
                "(no events.db or publish-history.json).",
                file=sys.stderr,
            )
            return

        records = find_divergences(snapshot)
        write_jsonl((rec.to_jsonl_dict() for rec in records), sys.stdout)
        _emit_summary(records, transient=snapshot.transient)
    except PipelineError as exc:
        handle_error(exc)


if __name__ == "__main__":
    main()
