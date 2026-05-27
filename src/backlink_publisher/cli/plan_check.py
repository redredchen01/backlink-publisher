"""``plan-check`` CLI — validate plan-doc claims against ``origin/main``.

Unit 3 dispatcher. Re-exports from three sub-modules for backward compatibility
(``from backlink_publisher.cli import plan_check as pc`` still resolves all
public and ``_``-prefixed API symbols).
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Re-export from sub-modules (backward compatibility)
# ---------------------------------------------------------------------------
from ._plan_check_schema import (
    SCHEMA_VERSION,
    PlanClaimsFilenameDateMismatch,
    PlanClaimsFrontmatterSchemaError,
    PlanClaimsGlobUnsupported,
    PlanClaimsMissingOnPostCutoff,
    ClaimsBlock,
    _check_filename_date_lock,
    _grandfathered,
    _parse_frontmatter,
    _read_plan_text,
    _validate_claims_schema,
    _validate_sha_format,
)

from ._plan_check_git import (
    FetchOutcome,
    _classify_fetch_stderr,
    _fetch_head_age_seconds,
    _git_env,
    _last_git_error,
    _maybe_fetch_origin_main,
    _path_exists_on_main,
    _sha_reachable_from_main,
)

from ._plan_check_format import (
    _build_json_payload,
    _emit_json,
    _emit_recon_line,
    _format_human_drift,
    _now_iso,
)


# ---------------------------------------------------------------------------
# Unit 3: CLI dispatch — argparse, output formatters, exit-code mapping
# ---------------------------------------------------------------------------
#
# Exit codes (D3):
#   0 — pass (all claims resolved on origin/main, OR grandfathered, OR empty)
#   1 — UsageError (positional missing/not-a-file)
#   2 — schema violation (frontmatter / claims schema / glob / filename-date lock)
#   7 — drift detected (paths missing or shas unreachable)
#   8 — missing claims block on post-cutoff plan-doc
#
# argparse exits 2 on its own errors (missing positional, bad flag); we accept
# that overlap with schema-violation 2 because argparse only fires on usage
# issues, not on our domain validation.
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> None:
    """``plan-check`` CLI entry — validate plan-doc claims against ``origin/main``.

    Exit-code dispatch follows plan §D3: 0/2/7/8 (with 1 reserved for
    :class:`UsageError`). The function never returns a value; success is
    silent ``return`` and failures are :class:`SystemExit` with the code.
    """
    import argparse
    import sys as _sys

    # Local import keeps the module import-cheap (UsageError isn't needed for
    # the schema/git tiers tested in Unit 1/2).
    from backlink_publisher._util.errors import UsageError, handle_error

    parser = argparse.ArgumentParser(
        prog="plan-check",
        description=(
            "Validate a plan-doc's claims block against origin/main. Exits 0 on "
            "pass (or grandfathered/empty claims), 2 on schema violation, 7 on "
            "drift, 8 on missing claims for a post-cutoff plan-doc."
        ),
    )
    parser.add_argument(
        "plan_path",
        help="Path to a plan-doc under docs/plans/*.md",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON to stdout instead of a human table",
    )
    args = parser.parse_args(argv)

    # --- Phase 1: positional must point at a real file ---------------------
    plan_path = Path(args.plan_path)
    try:
        if not plan_path.exists():
            raise UsageError(f"{plan_path}: file not found")
        if not plan_path.is_file():
            raise UsageError(f"{plan_path}: not a regular file")
    except UsageError as exc:
        handle_error(exc)
        return  # pragma: no cover — handle_error always exits

    # --- Phase 2: read + parse frontmatter ---------------------------------
    plan_date: Optional[_dt.date] = None
    try:
        text = _read_plan_text(plan_path)
        fm = _parse_frontmatter(text)
    except PlanClaimsFrontmatterSchemaError as exc:
        print(f"plan-check: schema violation — {exc}", file=_sys.stderr, flush=True)
        if args.json:
            _emit_json(
                _build_json_payload(
                    plan_path=plan_path,
                    plan_date=None,
                    status="schema_violation",
                    exit_code=exc.exit_code,
                    fetch_outcome=None,
                    paths_missing=[],
                    shas_unreachable=[],
                )
            )
        raise SystemExit(exc.exit_code)

    # Pull the typed date for downstream JSON payload before any other branch
    # can throw — we want the date in the output even when later layers fail.
    raw_date = fm.get("date")
    if isinstance(raw_date, _dt.datetime):
        raw_date = raw_date.date()
    if isinstance(raw_date, _dt.date):
        plan_date = raw_date

    # --- Phase 3: filename ↔ date lock (R11b) runs FIRST per Unit 1 docstring
    try:
        _check_filename_date_lock(plan_path, fm)
    except (PlanClaimsFilenameDateMismatch, PlanClaimsFrontmatterSchemaError) as exc:
        print(f"plan-check: schema violation — {exc}", file=_sys.stderr, flush=True)
        if args.json:
            _emit_json(
                _build_json_payload(
                    plan_path=plan_path,
                    plan_date=plan_date,
                    status="schema_violation",
                    exit_code=exc.exit_code,
                    fetch_outcome=None,
                    paths_missing=[],
                    shas_unreachable=[],
                )
            )
        raise SystemExit(exc.exit_code)

    # --- Phase 4: grandfather skip ----------------------------------------
    try:
        is_old = _grandfathered(fm)
    except PlanClaimsFrontmatterSchemaError as exc:
        print(f"plan-check: schema violation — {exc}", file=_sys.stderr, flush=True)
        if args.json:
            _emit_json(
                _build_json_payload(
                    plan_path=plan_path,
                    plan_date=plan_date,
                    status="schema_violation",
                    exit_code=exc.exit_code,
                    fetch_outcome=None,
                    paths_missing=[],
                    shas_unreachable=[],
                )
            )
        raise SystemExit(exc.exit_code)
    if is_old:
        # Pre-cutoff: silent exit 0. No stdout, no stderr, no JSON either —
        # the gate is a no-op for grandfathered plans per plan §step 8.
        return

    # --- Phase 5: claims schema validation --------------------------------
    try:
        claims = _validate_claims_schema(fm)
    except PlanClaimsMissingOnPostCutoff as exc:
        print(
            f"plan-check: missing claims block — {exc}",
            file=_sys.stderr,
            flush=True,
        )
        if args.json:
            _emit_json(
                _build_json_payload(
                    plan_path=plan_path,
                    plan_date=plan_date,
                    status="missing_claims",
                    exit_code=exc.exit_code,
                    fetch_outcome=None,
                    paths_missing=[],
                    shas_unreachable=[],
                )
            )
        raise SystemExit(exc.exit_code)
    except (
        PlanClaimsFrontmatterSchemaError,
        PlanClaimsGlobUnsupported,
    ) as exc:
        print(f"plan-check: schema violation — {exc}", file=_sys.stderr, flush=True)
        if args.json:
            _emit_json(
                _build_json_payload(
                    plan_path=plan_path,
                    plan_date=plan_date,
                    status="schema_violation",
                    exit_code=exc.exit_code,
                    fetch_outcome=None,
                    paths_missing=[],
                    shas_unreachable=[],
                )
            )
        raise SystemExit(exc.exit_code)

    # --- Phase 6: empty claims (escape hatch) → silent exit 0 -------------
    if not claims.paths and not claims.shas:
        return

    # --- Phase 7: freshness + RECON line ----------------------------------
    fetch_outcome = _maybe_fetch_origin_main()
    _emit_recon_line(fetch_outcome)

    # --- Phase 8: resolve paths -------------------------------------------
    paths_missing: list[str] = []
    for p in claims.paths:
        ok, _status = _path_exists_on_main(p)
        if not ok:
            # Both "missing" and "git_error" count as drift in v1 (plan §Unit 3
            # step 12 explicitly accepts this — git_error still means we can't
            # prove the path is on main, so it's drift from the operator's POV).
            paths_missing.append(p)

    # --- Phase 9: resolve shas --------------------------------------------
    shas_unreachable: list[str] = []
    for s in claims.shas:
        ok, _status = _sha_reachable_from_main(s)
        if not ok:
            # D11: normalisation is display-only and the resolution functions
            # accept short SHAs natively. Surface as-given.
            shas_unreachable.append(s)

    # --- Phase 10: aggregate + dispatch -----------------------------------
    if paths_missing or shas_unreachable:
        # Drift — stderr table + stdout one-liner.
        print(
            _format_human_drift(plan_path, paths_missing, shas_unreachable),
            file=_sys.stderr,
            flush=True,
        )
        summary = (
            f"{len(paths_missing)} paths missing, "
            f"{len(shas_unreachable)} shas unreachable on origin/main"
        )
        if args.json:
            _emit_json(
                _build_json_payload(
                    plan_path=plan_path,
                    plan_date=plan_date,
                    status="drift",
                    exit_code=7,
                    fetch_outcome=fetch_outcome,
                    paths_missing=paths_missing,
                    shas_unreachable=shas_unreachable,
                )
            )
        else:
            print(summary, flush=True)
        raise SystemExit(7)

    # Pass — stdout one-liner.
    age = fetch_outcome.fetch_head_age_seconds
    age_str = "null" if age is None else str(age)
    summary = (
        f"plan-check: pass — {len(claims.paths)} paths + {len(claims.shas)} shas "
        f"resolved on origin/main (fetch_head_age_seconds={age_str})"
    )
    if args.json:
        _emit_json(
            _build_json_payload(
                plan_path=plan_path,
                plan_date=plan_date,
                status="pass",
                exit_code=0,
                fetch_outcome=fetch_outcome,
                paths_missing=[],
                shas_unreachable=[],
            )
        )
    else:
        print(summary, flush=True)
    return


if __name__ == "__main__":  # pragma: no cover
    main()
