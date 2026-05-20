---
title: "WebUI publish-history writes must route through `_push_history_per_row` — enforces `status=\"published\" ⟹ url`"
date: 2026-05-20
category: docs/solutions/best-practices
module: webui_app + webui_store
problem_type: best_practice
component: rails_controller
severity: high
applies_when:
  - "Adding a new WebUI route under `webui_app/` that writes publish-history rows"
  - "Reviewing a PR that calls `_history_store.update` or `_history_store.append` directly"
  - "Designing a bulk operation that emits multiple publish-history rows in a loop"
related_components:
  - service_object
tags:
  - publish-history
  - invariant-helper
  - webui-store
  - status-published
  - url-required
  - bulk-routes
---

# WebUI publish-history writes must route through `_push_history_per_row`

## Context

The publish-history store (`webui_store/publish-history.json`) carries a load-bearing invariant: **every row with `status="published"` must have a non-empty `url`**. Downstream consumers (the history page, the anchor reporter, the footprint validator, operator monitoring) treat the URL as the proof of publication. A row with `status="published"` and `url=""` is a phantom claim that breaks every downstream contract.

The invariant is enforced by `webui_app/helpers.py:_push_history_per_row` (around line 439). The helper:

1. Validates the input row has `url` present and non-empty when `status="published"`.
2. Raises `ValueError` at write time if the invariant is violated, before persistence.
3. Handles the per-row dedup, ID assignment, and timestamp normalization.

Bypassing the helper — calling `_history_store.update(...)` or `_history_store.append(...)` directly with a hand-built dict — silently breaks the invariant. The store accepts the write, the row persists, and the breakage surfaces hours later in a downstream view.

PR #87 (bulk draft-queue + history management, squash `539f5727`) and PR #97 (publish-history no-URL invariant, squash `3e458cb`) both hit this during review and landed only after switching to the helper.

## Guidance

### Always use the helper

```python
# webui_app/<your_route>.py
from .helpers import _push_history_per_row

def new_route():
    # ... gather data ...
    for row in rows_to_publish:
        try:
            _push_history_per_row(
                row_id=row["id"],
                status="published",
                url=row["published_url"],   # required when status="published"
                # ... other fields ...
            )
        except ValueError as exc:
            # invariant violation → surface to operator, do not persist
            current_app.logger.error("history invariant violation: %s", exc)
            return jsonify(error=str(exc)), 400
```

### Never write the store directly

```python
# WRONG — bypasses invariant check
_history_store.update({
    "rows": existing_rows + [{
        "id": row_id,
        "status": "published",
        "url": "",                    # <- silent invariant violation
    }]
})
```

The helper accepts the same data the direct call would, plus validation. There is no scenario where the direct call is faster or simpler enough to justify skipping the invariant.

### Bulk routes: helper-per-row, not bulk-write

A common temptation in bulk routes (rerun history, bulk draft promotion, channel rebind) is to build a list of N rows and write them in one `_history_store.update`. Don't. Loop over `_push_history_per_row` once per row. The per-row cost is dominated by JSON serialization which is already paid; the invariant check is free relative to that.

If the bulk operation is performance-critical (>1000 rows), the right answer is a **bulk helper** in `helpers.py` that performs the same per-row validation in a tight loop, not a direct store write. Add the helper before the route.

## Why This Matters

The invariant is downstream-facing:

- **History page** renders `<a href="{url}">` — empty `url` produces broken links the operator sees in production.
- **Anchor reporter** (`cli/report_anchors.py`) aggregates by URL; a phantom row with empty URL crashes the reporter or skews metrics depending on the version.
- **Footprint validator** (`cli/footprint.py`) treats each `status="published"` row as a published artifact; missing URLs make footprint counts disagree with channel reality.
- **Operator monitoring** alerts on "row published but no link" — a phantom row generates a false-positive alert.

The cost of the helper is one import + one function call. The cost of skipping it is debugging a downstream report that disagrees with channel reality 6 hours later, often during an unrelated session, with no obvious tie back to the write that violated the invariant.

This is one of the bugs that the existing test suite **can** catch but only if the test is well-aimed. The 2026-05-19 incident around PR #87 verification ([[pr87-verification-complete]] in memory) involved a parallel `bp-cbu5-ui/` worktree's pytest writing phantom rows into the canonical `webui_store/`. The phantom rows survived for hours because the pytest didn't go through the helper — once the helper invariant was tightened in PR #97, the same class of incident became impossible.

## When to Apply

- Any new route or service-object method that creates publish-history rows.
- Reviewing a PR diff that contains `_history_store.update(` or `_history_store.append(` — push back, route through the helper.
- Test code that synthesizes publish-history rows for fixtures: use the helper there too; otherwise tests can lock in the invariant violation as expected behavior.

Skip when:

- The write is a read-only enrichment (e.g., adding a tag to an existing row) — these go through `_history_store.update_row(id, partial)` which preserves the invariant.
- Migration code that needs to bulk-fix old rows — those have an explicit migration path with the invariant temporarily relaxed and re-validated post-migration.

## Examples

**Right (post-PR #97):**

```python
# webui_app/bulk_rerun.py
def bulk_rerun_publish():
    results = run_publish_subset(...)
    for row_id, outcome in results.items():
        if outcome.success:
            _push_history_per_row(
                row_id=row_id,
                status="published",
                url=outcome.url,           # always set on success path
                channel=outcome.channel,
                published_at=outcome.ts,
            )
        else:
            _push_history_per_row(
                row_id=row_id,
                status="failed",
                url=None,                  # allowed when status != "published"
                error=outcome.error,
            )
```

**Wrong (pre-PR #97, what PR #87 originally tried):**

```python
def bulk_rerun_publish():
    new_rows = []
    for row_id, outcome in run_publish_subset(...).items():
        new_rows.append({
            "id": row_id,
            "status": "published" if outcome.success else "failed",
            "url": getattr(outcome, "url", ""),   # empty string on failure
                                                   # or on bug paths
        })
    _history_store.bulk_append(new_rows)           # bypasses invariant
```

The wrong version persists `status="published", url=""` rows whenever the bug path is hit (e.g., outcome reports success but URL retrieval failed). PR #97 added regression tests against this exact shape.

## Related

- PR #97 (`3e458cb`) — invariant landed.
- PR #87 (`539f5727`) — bulk routes that originally bypassed the helper; rebased onto post-#97 main during land.
- `webui_app/helpers.py:_push_history_per_row` — canonical helper.
- `webui_store/publish-history.json` — file the invariant guards.
- `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` — adjacent: tests that lock in the wrong invariant in a different config-write area.
