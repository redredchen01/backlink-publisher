"""Backlink Equity Ledger — per-target read-only scorecard (JSONL on stdout).

Emits one JSON object per target page, composing already-recorded data from
events.db, the WebUI history store, and the anchor-profile store via the shared
``ledger.build_ledger`` engine. Pure read-only: no publishing, no fetching.

stdout = data (JSONL), stderr = the config-echo banner. Exit 0 on success.
Plan 2026-05-25-004.
"""

from __future__ import annotations

import sys

import backlink_publisher.publishing.adapters  # noqa: F401  populate registry before config load
from .. import config_echo
from backlink_publisher._util.jsonl import write_jsonl
from backlink_publisher.config import load_config
from backlink_publisher.ledger import build_ledger


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="equity-ledger",
        description=(
            "Per-target backlink scorecard: for each target page, the live vs "
            "total links, dofollow breakdown, live-dofollow count, exact-match "
            "anchor share, platform spread, and liveness freshness. Read-only "
            "aggregation over events.db + publish history + anchor profiles. "
            "Emits one JSON object per target on stdout."
        ),
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=30,
        metavar="N",
        help=(
            "Liveness verified longer ago than N days is flagged 'stale' "
            "(display only; default: 30)."
        ),
    )
    args = parser.parse_args(argv)

    # Closed-set/range validation post-parse (repo convention: UsageError-style
    # exit 1, not argparse's exit 2). See [[argparse-choices-vs-usage-error]].
    if args.stale_days <= 0:
        raise SystemExit("equity-ledger: --stale-days must be a positive integer")

    # Config Echo Chamber: banner to stderr so the operator sees which config /
    # env / SHA was resolved. Missing config is fine (read-only, Safe defaults).
    cfg = load_config()
    config_echo.emit_banner(cfg, "equity-ledger")

    rows = build_ledger(stale_days=args.stale_days)
    write_jsonl((row.to_jsonl_dict() for row in rows), sys.stdout)


if __name__ == "__main__":
    main()
