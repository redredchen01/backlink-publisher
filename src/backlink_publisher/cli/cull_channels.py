"""cull-channels — read-only channel-quality cull advisory.

Enumerates every registered publishing platform and classifies it by SEO
footprint value, reusing the registry's existing dofollow/referral grading:

  - **cull-candidate**: ``dofollow_status is False`` AND ``referral_value ==
    "low"`` — confirmed nofollow + low value: adds footprint/detectability but
    little equity, a candidate to retire to shrink the blast-radius exposure
    surface.
  - **unverifiable**: ``dofollow_status == "uncertain"`` (the registry's
    "tried but couldn't confirm" signal — e.g. anti-bot blocked). Never
    auto-culled; listed for awareness only, per the dofollow-map lesson that a
    value you couldn't verify is not a value you act on.
  - **keep**: everything else — dofollow channels (carry equity regardless of
    referral grade) and confirmed-nofollow-but-high-referral channels.

Note: every *registered* platform has ``dofollow_status`` in
``{True, False, "uncertain"}`` (``register()`` requires it; ``None`` is only
returned for unregistered names), so ``"uncertain"`` — not ``None`` — is the
unverifiable signal here.

stdout = a human markdown table (default) or machine JSONL (``--format json``);
stderr = a RECON summary; **always exit 0** — this is a diagnostic, not a gate,
and a cull *recommendation is not a cull*. Acting on it (removing a channel from
``register()`` + manifest + the auth-type map + sample tests) is out of scope.
Blast-radius Phase 1 (R9).
"""

from __future__ import annotations

from typing import Any

import backlink_publisher.publishing.adapters  # noqa: F401  populate registry before enumeration
from backlink_publisher._util.errors import PipelineError, UsageError, handle_error
from backlink_publisher._util.jsonl import write_jsonl
from backlink_publisher._util.logger import PipelineLogger, set_log_level
from backlink_publisher.publishing.registry import (
    dofollow_rationale,
    dofollow_status,
    referral_value,
    registered_platforms,
)

cull_logger = PipelineLogger("cull-channels")

_LOG_LEVELS = {"DEBUG", "INFO", "WARN", "ERROR"}
_FORMATS = {"markdown", "json"}

#: All classifications the verb can assign (explicit for the summary + tests).
CLASSES = ("cull-candidate", "unverifiable", "keep")


def _classify(name: str) -> str:
    """Map a platform's registry grading to a cull classification.

    "uncertain" (and the defensive None for an unregistered name) take
    precedence — never auto-cull a channel whose dofollow value could not be
    empirically confirmed.
    """
    status = dofollow_status(name)
    if status == "uncertain" or status is None:
        return "unverifiable"
    if status is False and referral_value(name) == "low":
        return "cull-candidate"
    return "keep"


def _build_row(name: str) -> dict[str, Any]:
    """The single canonical serializer — one row per registered platform."""
    return {
        "platform": name,
        "classification": _classify(name),
        "dofollow_status": dofollow_status(name),
        "referral_value": referral_value(name),
        "rationale": dofollow_rationale(name),
    }


def _render_markdown(rows: list[dict[str, Any]]) -> str:
    """Human-readable table, cull-candidates first, then unverifiable, then keep."""
    order = {cls: i for i, cls in enumerate(CLASSES)}
    rows = sorted(rows, key=lambda r: (order[r["classification"]], r["platform"]))
    lines = [
        "# Channel cull advisory",
        "",
        "| platform | classification | dofollow | referral | rationale |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        rationale = (r["rationale"] or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {r['platform']} | {r['classification']} | "
            f"{r['dofollow_status']} | {r['referral_value']} | {rationale} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="cull-channels",
        description=(
            "Read-only channel-quality cull advisory: classify every registered "
            "platform as cull-candidate / unverifiable / keep from its registry "
            "dofollow+referral grading. Markdown table (default) or JSONL "
            "(--format json) on stdout; RECON summary on stderr; always exit 0. "
            "A recommendation is not a cull."
        ),
    )
    parser.add_argument(
        "--format",
        default="markdown",
        metavar="FORMAT",
        help="Output format: markdown|json (default: markdown)",
    )
    parser.add_argument(
        "--log-level",
        default="WARN",
        metavar="LEVEL",
        help="Log verbosity: DEBUG|INFO|WARN|ERROR (default: WARN)",
    )
    args = parser.parse_args(argv)

    try:
        # Closed-set validation post-parse (repo convention: UsageError exit 1,
        # not argparse's exit 2). See [[argparse-choices-vs-usage-error]].
        if args.format not in _FORMATS:
            raise UsageError(
                f"cull-channels: --format must be one of {sorted(_FORMATS)}; "
                f"got {args.format!r}"
            )
        if args.log_level not in _LOG_LEVELS:
            raise UsageError(
                f"cull-channels: --log-level must be one of {sorted(_LOG_LEVELS)}; "
                f"got {args.log_level!r}"
            )
        set_log_level(args.log_level)

        rows = [_build_row(name) for name in registered_platforms()]
        counts = {cls: 0 for cls in CLASSES}
        for r in rows:
            counts[r["classification"]] += 1

        if args.format == "json":
            write_jsonl(rows)
        else:
            print(_render_markdown(rows))

        # Always-on RECON summary (bypasses --log-level; stripped by tests'
        # _stderr_without_warnings filter, so it doesn't break existing assertions).
        cull_logger.recon(
            "cull_summary",
            total=len(rows),
            classifications=counts,
        )
        # No SystemExit: classifications are advisory data, not process failures.
    except PipelineError as exc:
        handle_error(exc)


if __name__ == "__main__":
    main()
