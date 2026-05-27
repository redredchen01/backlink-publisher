"""Operator dedup escape verbs for publish-backlinks (U5a, extended in U5b).

Early-exit verbs (``--forget``, ``--list-uncertain``, and later
``--adjudicate-uncertain``) that read/mutate the authoritative dedup store and
its append-only audit log. Mutual exclusion with the publish/checkpoint verbs is
enforced in :func:`_publish_helpers._handle_checkpoint_ops`; this module performs
the action and exits 0.

Extracted from ``_publish_helpers.py`` to keep that file under the monolith
budget and to give the dedup verbs (which grow with U5b) their own home.

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U5a).
"""

from __future__ import annotations

import sys
from typing import Any


def add_dedup_arguments(parser: Any) -> None:
    """Register the dedup escape-verb flags on the publish-backlinks parser.
    Kept here (not in ``_build_parser``) so all dedup-verb concerns live together
    and ``_publish_helpers.py`` stays under the monolith budget."""
    parser.add_argument(
        "--forget",
        nargs=2,
        default=None,
        metavar=("PLATFORM", "TARGET_URL"),
        help=(
            "Clear one dedup key (single key only — no globs/bulk) so it becomes "
            "re-publishable, and exit 0. Requires --reason. Records an audit entry."
        ),
    )
    parser.add_argument(
        "--list-uncertain",
        action="store_true",
        default=False,
        help=(
            "List held (uncertain) dedup rows awaiting adjudication and exit 0. "
            "Optional --platform filter."
        ),
    )
    parser.add_argument(
        "--adjudicate-uncertain",
        nargs=2,
        default=None,
        metavar=("PLATFORM", "TARGET_URL"),
        help=(
            "Terminally resolve ONE held (uncertain) dedup key. Requires "
            "--to (succeeded|failed) and --reason. Records an audit entry. Exit 0."
        ),
    )
    parser.add_argument(
        "--adjudicate-bulk",
        action="store_true",
        default=False,
        help=(
            "Bulk-resolve held (uncertain) rows matched by --platform/--older-than. "
            "Requires --to, --reason, and either --list-affected (preview) or "
            "--confirm N matching the affected count (blast-radius guard)."
        ),
    )
    parser.add_argument(
        "--to",
        default=None,
        metavar="OUTCOME",
        help="Adjudication outcome: succeeded (-> done) or failed (-> re-publishable).",
    )
    parser.add_argument(
        "--older-than",
        default=None,
        metavar="DURATION",
        help="Bulk-adjudicate filter: only rows older than e.g. 7d / 24h / 3600s.",
    )
    parser.add_argument(
        "--confirm",
        type=int,
        default=None,
        metavar="N",
        help="Bulk-adjudicate guard: must equal the affected-row count to proceed.",
    )
    parser.add_argument(
        "--list-affected",
        action="store_true",
        default=False,
        help="Bulk-adjudicate preview: print the rows that would be resolved; do not mutate.",
    )
    parser.add_argument(
        "--reason",
        default=None,
        metavar="TEXT",
        help="Operator reason recorded in the dedup audit log (--forget/--adjudicate).",
    )


#: ``--to`` outcome → dedup terminal state. Closed set validated post-parse with
#: ``UsageError`` (exit 1), never argparse ``choices=`` (which exits 2) — see
#: [[argparse-choices-vs-usage-error]].
_TO_STATE = {"succeeded": "done", "failed": "failed"}


def _handle_dedup_ops(args: Any) -> None:
    """Dispatch the dedup escape verbs. Each raises ``SystemExit(0)`` on success
    (or exits 1 via ``emit_error`` on a usage error). No stdin needed."""
    if getattr(args, "forget", None):
        _do_forget(args)
        raise SystemExit(0)

    if getattr(args, "list_uncertain", False):
        _do_list_uncertain(args)
        raise SystemExit(0)

    if getattr(args, "adjudicate_uncertain", None):
        _do_adjudicate_single(args)
        raise SystemExit(0)

    if getattr(args, "adjudicate_bulk", False):
        _do_adjudicate_bulk(args)
        raise SystemExit(0)


def _resolve_to_state(args: Any) -> str:
    """Validate ``--to`` post-parse and map to a dedup terminal state."""
    from backlink_publisher._util.errors import emit_error

    raw = getattr(args, "to", None)
    if raw not in _TO_STATE:
        emit_error(
            f"error: --to must be one of {sorted(_TO_STATE)}; got {raw!r}",
            exit_code=1,
        )
    if not args.reason:
        emit_error("error: --adjudicate requires --reason <text>", exit_code=1)
    return _TO_STATE[raw]


def _parse_older_than(spec: str | None) -> float | None:
    """Parse ``7d`` / ``24h`` / ``90m`` / ``3600s`` to seconds. ``None`` → no
    age filter. Raises ``UsageError`` (exit 1) on a malformed spec."""
    from backlink_publisher._util.errors import emit_error

    if not spec:
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = spec[-1]
    if unit not in units or not spec[:-1].isdigit():
        emit_error(
            f"error: --older-than must be <int><s|m|h|d> (e.g. 7d); got {spec!r}",
            exit_code=1,
        )
    return int(spec[:-1]) * units[unit]


def _adjudicate_one(store: Any, key: Any, to_state: str, reason: str, *, run_id) -> None:
    """Append the audit entry then transition uncertain → terminal (append-first
    so a crash still leaves a trail)."""
    from backlink_publisher.idempotency import audit_log

    audit_log.append_entry(
        action="adjudicate",
        platform=key.platform,
        target_url=key.target_url,
        account=key.account,
        from_state="uncertain",
        to_state=to_state,
        reason=reason,
        run_id=run_id,
    )
    store.transition(key, to_state, run_id=run_id)


def _do_adjudicate_single(args: Any) -> None:
    """Resolve ONE held key (``uncertain`` → ``done``/``failed``). Refuses a key
    that is absent or not currently held."""
    import sys as _sys

    from backlink_publisher._util.errors import emit_error
    from backlink_publisher.idempotency import DedupKey, DedupStore

    to_state = _resolve_to_state(args)
    platform, target_url = args.adjudicate_uncertain
    key = DedupKey(platform=platform, target_url=target_url)
    store = DedupStore()
    record = store.get(key)
    if record is None:
        emit_error(f"error: no dedup row for {platform} key (nothing to adjudicate)", exit_code=1)
    if record.state != "uncertain":
        emit_error(
            f"error: key is {record.state}, not uncertain; "
            "only held rows can be adjudicated (use --forget for a terminal row)",
            exit_code=1,
        )
    _adjudicate_one(store, key, to_state, args.reason, run_id=record.run_id)
    print(
        f"adjudicate: {platform} key uncertain -> {to_state}.",
        file=_sys.stderr,
    )


def _do_adjudicate_bulk(args: Any) -> None:
    """Resolve every held row matched by ``--platform``/``--older-than``. Guarded:
    without ``--list-affected`` it refuses unless ``--confirm N`` equals the
    affected count, so a wrong selector cannot silently mass-retire backlinks."""
    import sys as _sys
    import time as _time

    from backlink_publisher._util.errors import emit_error
    from backlink_publisher.idempotency import DedupKey, DedupStore

    to_state = _resolve_to_state(args)
    older_than_s = _parse_older_than(getattr(args, "older_than", None))
    store = DedupStore()
    rows = store.list_by_state("uncertain", platform=getattr(args, "platform", None))
    if older_than_s is not None:
        cutoff = _time.time() - older_than_s
        rows = [r for r in rows if r.updated_at < cutoff]

    if getattr(args, "list_affected", False):
        print(f"adjudicate-bulk: {len(rows)} row(s) would be set to {to_state}:")
        for r in rows:
            print(f"  {r.platform:<14}  {r.target_url}")
        return

    confirm = getattr(args, "confirm", None)
    if confirm != len(rows):
        emit_error(
            f"error: --adjudicate-bulk would affect {len(rows)} row(s); re-run with "
            f"--confirm {len(rows)} to proceed (or --list-affected to preview)",
            exit_code=1,
        )

    for r in rows:
        key = DedupKey(platform=r.platform, target_url=r.target_url, account=r.account)
        _adjudicate_one(store, key, to_state, args.reason, run_id=r.run_id)
    print(
        f"adjudicate-bulk: resolved {len(rows)} row(s) uncertain -> {to_state}.",
        file=_sys.stderr,
    )


def _do_forget(args: Any) -> None:
    """Clear one dedup key → ``absent`` and append an audit entry. Single key
    only: a glob/wildcard in either field is rejected (exit 1) so a wrong pattern
    cannot silently mass-retire backlinks."""
    from backlink_publisher._util.errors import emit_error
    from backlink_publisher.idempotency import DedupKey, DedupStore
    from backlink_publisher.idempotency import audit_log

    platform, target_url = args.forget
    if not args.reason:
        emit_error("error: --forget requires --reason <text>", exit_code=1)
    if any("*" in v or "?" in v for v in (platform, target_url)):
        emit_error(
            "error: --forget takes a single concrete key; globs/wildcards are "
            "rejected (forget one key at a time)",
            exit_code=1,
        )

    key = DedupKey(platform=platform, target_url=target_url)
    store = DedupStore()
    record = store.get(key)
    from_state = record.state if record is not None else None

    # Append the audit entry BEFORE deleting so a crash mid-forget still leaves a
    # trail (the row simply remains; the operator re-runs). Canonical target_url
    # (key.target_url) is logged so U6's touched-key check matches the store key.
    audit_log.append_entry(
        action="forget",
        platform=key.platform,
        target_url=key.target_url,
        account=key.account,
        from_state=from_state,
        to_state="absent",
        reason=args.reason,
        run_id=getattr(args, "resume", None),
    )
    store.forget(key)
    if from_state is None:
        print(
            f"forget: key was already absent (platform={key.platform}); "
            "audit entry recorded.",
            file=sys.stderr,
        )
    else:
        print(
            f"forget: cleared {key.platform} key (was {from_state}); now "
            "re-publishable.",
            file=sys.stderr,
        )


def _do_list_uncertain(args: Any) -> None:
    """Print held (``uncertain``) dedup rows on stdout (the operator needs the
    target_url to adjudicate). Optional ``--platform`` filter."""
    from backlink_publisher.idempotency import DedupStore

    platform_filter = getattr(args, "platform", None)
    rows = DedupStore().list_by_state("uncertain", platform=platform_filter)
    if not rows:
        print("No uncertain (held) dedup rows.")
        return
    print(f"{'PLATFORM':<14}  {'STATE':<10}  {'RUN_ID':<28}  TARGET_URL")
    print("-" * 90)
    for r in rows:
        print(
            f"{r.platform:<14}  {r.state:<10}  {(r.run_id or ''):<28}  {r.target_url}"
        )
