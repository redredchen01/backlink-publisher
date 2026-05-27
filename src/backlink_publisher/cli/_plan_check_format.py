"""Output formatting tier for ``plan-check`` — human/JSON output, RECON lines.

Extracted from ``plan_check.py``.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Literal, Optional

from ._plan_check_git import FetchOutcome
from ._plan_check_schema import SCHEMA_VERSION


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string ending in ``Z``.

    Per the JSON contract (plan §Open Questions Resolved line 138).
    """
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _format_human_drift(
    plan_path: Path,
    paths_missing: list[str],
    shas_unreachable: list[str],
) -> str:
    """Render the human-readable drift report (goes to stderr).

    Header naming both axes followed by an indented list per axis. The shape is
    intentionally similar to ``footprint``'s drift table so CI log readers
    recognise it (plan §R13).
    """
    lines: list[str] = [f"Drift detected in {plan_path}:"]
    if paths_missing:
        lines.append("  paths_missing:")
        for p in paths_missing:
            lines.append(f"    - {p}")
    if shas_unreachable:
        lines.append("  shas_unreachable:")
        for s in shas_unreachable:
            lines.append(f"    - {s}")
    return "\n".join(lines)


def _build_json_payload(
    *,
    plan_path: Path,
    plan_date: Optional[_dt.date],
    status: Literal["pass", "drift", "schema_violation", "missing_claims"],
    exit_code: int,
    fetch_outcome: Optional[FetchOutcome],
    paths_missing: list[str],
    shas_unreachable: list[str],
) -> dict[str, Any]:
    """Assemble the JSON output dict per plan §line 138.

    ``fetch_outcome`` is ``None`` only on early-exit paths (schema violation,
    missing claims) where we never reached the git resolution layer.
    """
    age: Optional[int]
    skip: Optional[str]
    if fetch_outcome is None:
        age = None
        skip = None
    else:
        age = fetch_outcome.fetch_head_age_seconds
        skip = fetch_outcome.skip_reason
    return {
        "plan": str(plan_path),
        "date": plan_date.isoformat() if plan_date is not None else None,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "exit_code": exit_code,
        "fetched_at": _now_iso(),
        "fetch_head_age_seconds": age,
        "fetch_skip_reason": skip,
        "drift": {
            "paths_missing": list(paths_missing),
            "shas_unreachable": list(shas_unreachable),
        },
    }


def _emit_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _emit_recon_line(fetch_outcome: FetchOutcome) -> None:
    """Write the structured RECON line to stderr per plan §D16.

    Two shapes:
      ``RECON warn fetch_skipped reason=<...> fetch_head_age_seconds=<n|null>``
      ``RECON info fetch_head_age_seconds=<n|null>``
    """
    age = fetch_outcome.fetch_head_age_seconds
    age_str = "null" if age is None else str(age)
    if fetch_outcome.skip_reason is not None:
        line = (
            f"RECON warn fetch_skipped reason={fetch_outcome.skip_reason} "
            f"fetch_head_age_seconds={age_str}"
        )
    else:
        line = f"RECON info fetch_head_age_seconds={age_str}"
    print(line, file=sys.stderr, flush=True)
