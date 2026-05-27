"""Preview manifest (Unit 3): a read-only, pre-dispatch verdict pass.

For each planned row, compute its dedup key, read the dedup store, and emit a
verdict:

* ``NEW``            — no record (or ``failed``, re-publishable) → would dispatch.
* ``SKIP-DUPLICATE`` — ``done`` → already published (carries the recorded live_url).
* ``HOLD-UNCERTAIN`` — ``uncertain``/``attempting`` → held; needs adjudication.

**Leak boundary:** stdout carries JSONL data including the canonical ``target_url``
and recorded ``live_url`` (campaign URLs — data goes to stdout by contract). The
human summary on **stderr** carries only counts plus a per-row **keyed HMAC digest**
(:meth:`DedupStore.key_digest`) — never a raw URL. A bare hash over the operator's
small, enumerable URL space would be reversible, so the digest is HMAC-keyed by a
per-store secret.

The manifest is a **carrier**, not an authority: U7's enforce gate honors the
``force`` slot but re-checks live store state at dispatch (the store can change
between preview and run). No network call — store-only reads.

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U3).
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ..idempotency import DedupStore
from ._dedup_gate import _key_for_row

#: Stable verdict strings (also the stderr summary count buckets).
VERDICT_NEW = "NEW"
VERDICT_SKIP = "SKIP-DUPLICATE"
VERDICT_HOLD = "HOLD-UNCERTAIN"

_VERDICT_BY_STATE = {
    "done": VERDICT_SKIP,
    "uncertain": VERDICT_HOLD,
    "attempting": VERDICT_HOLD,
    "failed": VERDICT_NEW,  # confirmed-not-landed → re-publishable
}


def _verdict_for_state(state: str | None) -> str:
    """absent (None) → NEW; otherwise map the stored state. Unknown states fall
    to HOLD (conservative: never silently treat an unrecognized row as NEW)."""
    if state is None:
        return VERDICT_NEW
    return _VERDICT_BY_STATE.get(state, VERDICT_HOLD)


def emit_manifest(rows: list[dict[str, Any]], platform_override: str | None = None) -> None:
    """Emit the preview manifest: JSONL on stdout, human summary on stderr.

    Read-only — never writes the dedup store (``key_digest`` may lazily create the
    HMAC secret file, but no dedup row is touched). Caller exits 0 afterward."""
    store = DedupStore()
    store_token = store.store_token()  # binds the manifest to this store (U7c force)
    counts = {VERDICT_NEW: 0, VERDICT_SKIP: 0, VERDICT_HOLD: 0}
    summary_lines: list[str] = []

    for row in rows:
        platform = platform_override or row.get("platform", "")
        key = _key_for_row(row, platform)
        if key is None:
            # No usable key (missing platform/target_url) — cannot dedup; treat as
            # NEW with a null digest so the row still appears in the manifest.
            entry = {
                "id": row.get("id", ""),
                "platform": platform,
                "account": None,
                "target_url": row.get("target_url"),
                "key_digest": None,
                "state": None,
                "verdict": VERDICT_NEW,
                "live_url": None,
                "run_id": None,
                "force": False,
                "store_token": store_token,
            }
            counts[VERDICT_NEW] += 1
            print(json.dumps(entry), flush=True)
            summary_lines.append(f"  {'-' * 16}  {VERDICT_NEW}")
            continue

        rec = store.get(key)
        state = rec.state if rec is not None else None
        verdict = _verdict_for_state(state)
        digest = store.key_digest(key)
        entry = {
            "id": row.get("id", ""),
            "platform": key.platform,
            "account": key.account,
            "target_url": key.target_url,
            "key_digest": digest,
            "state": state,
            "verdict": verdict,
            "live_url": rec.live_url if rec is not None else None,
            "run_id": rec.run_id if rec is not None else None,
            "force": False,
            "store_token": store_token,
        }
        counts[verdict] += 1
        print(json.dumps(entry), flush=True)
        summary_lines.append(f"  {digest}  {verdict}")

    total = sum(counts.values())
    print(
        f"preview-manifest: {total} row(s) — "
        f"{VERDICT_NEW}={counts[VERDICT_NEW]} "
        f"{VERDICT_SKIP}={counts[VERDICT_SKIP]} "
        f"{VERDICT_HOLD}={counts[VERDICT_HOLD]}",
        file=sys.stderr,
    )
    for line in summary_lines:
        print(line, file=sys.stderr)
