"""Backfill-reconciliation precondition for the enforce flip (Unit 7b, R19b).

Before the enforce gate is allowed to skip/hold anything, the dedup store must
**cover the back-catalogue** — otherwise an already-live post the store doesn't
recognize would be treated as ``NEW`` and re-published (the exact double-post
this feature prevents).

The check is **dedup-store-centric** and deliberately distinct from
``audit-state``'s ``articles``↔``history`` divergence (which says nothing about
dedup-store completeness): every distinct ``(mapped-platform, account, canon
target_url)`` over publish-success events must be present in the dedup store in a
**covered** state (``done`` or ``uncertain`` — both prevent a re-post; ``failed``/
``attempting``/absent do not and so count as *missing*). The U6 unmappable
(quarantine) tail must be zero **or operator-acknowledged**.

**This proves coverage, NOT correctness** — a mis-seeded ``done`` (a post that
never actually landed) satisfies the count gate yet makes enforce permanently
skip a needed backlink. Mitigations live elsewhere: U6's conservative seeding
(only pristine confirmed → ``done``) and U4's ``suspect_done`` finding.

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U7).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .backfill import _ADAPTER_STRING_TO_PLATFORM, _events_db_path, _read_publish_events
from .store import DedupKey, DedupStore

#: Operator acknowledgement of the unmappable (quarantine) tail — lets enforce
#: proceed with retired/unknown adapter strings that cannot be auto-seeded.
ACK_QUARANTINE_ENV = "BACKLINK_PUBLISHER_DEDUP_ENFORCE_ACK_QUARANTINE"

#: Dedup states that cover a back-catalogue key (prevent a re-post under enforce).
_COVERED = frozenset({"done", "uncertain"})


@dataclass
class ReconResult:
    event_key_count: int = 0
    covered_count: int = 0
    missing_count: int = 0
    quarantine_count: int = 0
    quarantine_acknowledged: bool = False
    #: Sample of missing key digests (HMAC, never raw URLs) for the stderr report.
    missing_digests: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        if self.missing_count > 0:
            return False
        if self.quarantine_count > 0 and not self.quarantine_acknowledged:
            return False
        return True


def check_enforce_readiness() -> ReconResult:
    """Compare publish-event coverage against the dedup store. Read-only."""
    result = ReconResult(
        quarantine_acknowledged=os.environ.get(ACK_QUARANTINE_ENV) == "1"
    )
    events = _read_publish_events(_events_db_path())
    if not events:
        return result  # nothing published yet → trivially ready

    store = DedupStore()
    seen: set[tuple[str, str, str]] = set()
    for _kind, col_target_url, payload_json in events:
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except ValueError:
            payload = {}
        adapter_string = payload.get("platform")
        target_url = col_target_url or payload.get("target_url")
        platform = _ADAPTER_STRING_TO_PLATFORM.get(adapter_string or "")
        if platform is None or not target_url:
            result.quarantine_count += 1
            continue
        key = DedupKey(platform=platform, target_url=str(target_url))
        tup = key.as_tuple()
        if tup in seen:
            continue  # one distinct logical key, counted once
        seen.add(tup)
        result.event_key_count += 1
        rec = store.get(key)
        if rec is not None and rec.state in _COVERED:
            result.covered_count += 1
        else:
            result.missing_count += 1
            if len(result.missing_digests) < 10:
                result.missing_digests.append(store.key_digest(key))
    return result
