---
date: 2026-05-25
topic: publishing-health-dashboard
---

# Publishing Health Dashboard ("最近整體跑得怎麼樣?")

## Problem Frame

The operator runs batches of backlink publishes, then comes back later and
**cannot answer "how is the whole thing doing lately?"** They cannot see overall
success rate, which platform is silently failing, what the failures are made of,
or which channels are currently broken without clicking through the history list
row by row. The `webui_app/routes/dashboard.py` route is currently a 302 redirect
to the in-progress history list — the natural home for a real health view is
unoccupied.

## Key Pre-Existing Facts (grounded — corrected after document review)

A durable, append-only SQLite event store (`events.db`, at
`~/.config/backlink-publisher/`, never pruned) exists and is a derived projection
of the canonical JSON files (checkpoint / history / drafts), rebuildable via
`events/projector.py:flush_for()`. **But document review verified the substrate is
not dashboard-ready as-is — three defects, confirmed against source, must be fixed
before any view is trustworthy:**

- **D1 — successes are silently dropped (P0, verified).** Production writes
  checkpoint `status="done"` on success (`cli/publish_backlinks.py:265`,
  `cli/_resume.py:254`), but the checkpoint reducer only branches on
  `status=="succeeded"` (`events/projector.py:274`) — a literal production never
  writes. So **no `publish.confirmed` event is emitted for real successful
  publishes today**; the success-rate hero would read ~0%. Existing projector
  tests pass only because they fabricate `status="succeeded"`.
- **D2 — no platform attribution in events (P0, verified).** The `events` table
  has no adapter/platform column (only `id/ts/run_id/kind/target_url/host/article_id/payload_json`,
  indexed by `host`). `host` is the *destination/SEO target* domain, NOT the
  publishing platform. The checkpoint item carries `adapter` (`publish_backlinks.py:267`)
  but the projector drops it. A per-platform view (R7) cannot be built without
  threading platform through the projector.
- **D3 — failed-event shape is inconsistent (P1, verified).** The checkpoint
  reducer writes `error_class` on `publish.failed` (`projector.py:336`); the
  history reducer writes only `error_message_clean`, no `error_class`
  (`projector.py:516-531`). Any failure projected from history is un-bucketable.

**Implication for scope.** This is NOT a pure "read + aggregate + render" layer as
originally framed. v1 requires a **bounded, correctness-only projector fix**
(D1–D3) before the views. It still does NOT require a new persistence system. The
load-bearing risk is no longer "querying is hard" — it is "the projection is
quietly wrong, so a dashboard built on it would lie."

## Requirements

**Projection Correctness — verified prerequisites (must precede the views)**
- R1. The checkpoint projector MUST emit `publish.confirmed` for the production
  success status `"done"` (fix D1). A reconciliation of a real completed run's
  outcome counts against events.db totals MUST pass (no silent undercount).
- R2. Publish outcomes MUST reliably reach events.db after a run and after resume.
  `flush_for()` is not auto-invoked today; a never-projected run (e.g., crash
  mid-batch) MUST be either reconciled before display or visibly flagged as an
  unprojected gap (ties to R5).
- R3. The publish event payload MUST carry publishing-platform attribution so
  outcomes can be grouped by platform (fix D2). Failures occurring before adapter
  dispatch (e.g., validation) MUST be representable as "unattributed".
- R4. `publish.failed` events MUST carry a consistent `error_class` across both
  checkpoint and history sources (fix D3); failures lacking a class surface in an
  explicit "unclassified" bucket, never silently folded into a real class.
- R5. The dashboard MUST surface its own data freshness — the window and an
  "as of" timestamp — and MUST degrade honestly when data is incomplete: show
  what is known and signal the gap rather than implying a complete picture.

**Aggregate Views**
- R6. **Overall success rate over a window** (hero). "Over the last N days:
  X targets, Y% confirmed." Default window 30 days. **Denominator = distinct
  targets by latest outcome** (decided): a target retried-then-succeeded counts as
  success; only its most recent outcome in the window matters. `skipped_unreachable`
  is excluded from the denominator (it is neither a confirmed nor a failed publish).
- R7. **Per-adapter health table** (depends on R3). One row per platform:
  confirmed / failed counts and success rate. Default sort worst-first to satisfy
  "name the weakest platform" at a glance. Small-sample rows (e.g., 1/1=100%) MUST
  be flagged so they don't outrank high-volume healthy platforms. Includes an
  "Unattributed" row for pre-dispatch failures.
- R8. **Error distribution** (depends on R4). Failures bucketed by `error_class`
  (auth-expired / content-rejected / anti-bot / 5xx / unclassified). Finer
  content-rejection breakdown is a fast-follow gated on a machine-readable
  `reason_class` not existing today.
- R9. **Currently-broken channels banner.** Channels that are `expired` /
  `identity_mismatch` right now, sourced from `channel_status`. **Scope-honest
  caveat:** `channel_status` only models the three browser-bind channels
  (velog / medium / blogger) and is reactive (flips only after a failed attempt).
  The banner MUST be labelled so an empty banner reads as "no *known* problems",
  not "all platforms healthy", and MUST link each broken channel to the existing
  bind/re-bind flow.

**Empty / Loading / Placement**
- R10. Every view MUST distinguish "no data yet" from "0% success". Zero publishes
  in window → "No publishes in the last N days" (not "0% confirmed"); zero failures
  → positive empty state; empty banner → single "No known channel problems" line.
- R11. If projection runs on dashboard load and is not instant, the dashboard MUST
  show a labelled loading state and MUST NOT render a number that silently changes
  after load (render after projection completes, or stamp last-known aggregates
  with their older "as of" time).
- R12. Placement hierarchy top-to-bottom: overall success rate (R6) →
  broken-channels banner (R9) → per-adapter table (R7) → error distribution (R8).
  The R5 freshness "as of" signal MUST sit adjacent to the hero so the window and
  staleness are read together with the headline number. Visual weight tracks
  importance — the act-now banner and the passive 30-day hero must not read as
  equal-weight cards.

## Success Criteria

- After running a batch and returning later, the operator answers "how did it go
  lately?" from a single screen in under ~10 seconds, without opening row history.
- A reconciliation of a known completed run's outcomes against the dashboard
  totals matches exactly (no silent undercount) — run as a v1 acceptance gate
  against **real checkpoint data**, not synthetic events.
- The operator can name the current weakest platform and the dominant failure type
  from the dashboard alone.
- Channels that have already failed since their last successful publish are visible
  before starting a new batch. (Honest limit: a channel that expired but has not
  been hit since cannot be caught without proactive verification, which is out of
  scope — the banner reflects last-known status.)
- The operator changes behavior on what they see: after a channel shows broken,
  they re-bind or skip it before the next batch rather than publishing into it.

## Scope Boundaries

- **Permitted write-path change is the bounded projector correctness fix (R1–R4)
  plus ensuring the projection runs (R2)** — nothing more. NOT a new persistence
  system, NOT a generic event/hook framework (there is exactly one consumer).
- **Not** proactive pre-flight channel verification, auth-expiry alerting, or
  staleness countdowns — v1 only *displays* current `channel_status`.
- **Not** the "did it actually land / false-success guard / universal post-publish
  liveness" direction — v1 trusts the (corrected) outcome events.
- **Not** auto-retry, backoff, or dead-letter queue.
- **Not** historical analytics beyond the active dashboard windows. Per-day
  time-bucketing needed to compute a window/trend IS in scope; long-term export,
  data warehousing, and SEO indexability tracking are not.
- **Week-over-week trend is a nice-to-have, not a v1 must-have** — no success
  criterion requires direction-of-change. Include only if near-zero cost.

## Key Decisions

- **Fix the projection, don't replace it.** The durable indexed log exists;
  duplicating it is waste. v1's core deliverable is *correctness + freshness*
  (R1–R5), because a dashboard built on the current quietly-wrong projection would
  lie, which is worse than no dashboard.
- **Group outcomes by platform via a projector change (R3/R7), not by `host`.**
  `host` is the operator's own target domain, not the publishing platform, so it
  cannot answer "which platform is failing". Threading `adapter` through is the
  minimum change; widening the scope boundary to allow it is deliberate.
- **Display-only, scope-honest channel health (R9).** Surfacing existing
  `channel_status` is near-zero cost; proactive verification is a separate, larger
  direction. The banner is explicitly labelled to avoid over-trust.
- **30-day default window; trend demoted to nice-to-have.** The stated goals are
  all current-state; direction-of-change is not goal-anchored.
- **Success-rate denominator = distinct targets by latest outcome (decided).**
  Rationale: a per-attempt ratio gets *worse* the more diligently the operator
  retries, punishing good behavior; per-target-latest reflects "how many backlinks
  eventually landed", which is the operator-meaningful signal. Requires per-target
  dedup-to-latest in the aggregation query.

## Dependencies / Assumptions

- Assumes the projector correctness fixes (R1–R4) are bounded — confirm during
  planning that no other reducer branch or consumer depends on the current
  (broken) `succeeded`/`done` split.
- Adjacent in-flight brainstorm `2026-05-25-backlink-equity-ledger-requirements.md`
  also reads `events.db`. Its open question Q1 (whether to extend events.db with
  platform/liveness fields) **directly overlaps R3** (this doc's platform-attribution
  change). The two MUST be coordinated so they converge on one event schema rather
  than diverging — see Resolve Before Planning.

## Outstanding Questions

### Resolve Before Planning
- (none — the success-rate denominator is decided; all remaining questions are
  technical and belong in planning)

### Deferred to Planning
- [Affects R3][Technical] Coordinate with the equity-ledger brainstorm before
  changing the event schema/payload: will platform attribution be a new column, a
  `payload_json` field, or a shared schema both features agree on? Decide the shared
  shape once to avoid a migration collision. (Needs reading equity-ledger's planning
  state — answerable in planning, not now.)
- [Affects R2][Technical] Where to trigger projection — inline at end of
  publish/resume, or lazy project-on-read at dashboard load — and how to handle a
  crashed-mid-batch run whose checkpoint never projected (reconcile-on-load safety
  net). Constraint: reuse existing invocation points; do not build a hook framework.
- [Affects R1][Technical] Whether the D1 `done`/`succeeded` fix should normalize on
  `"done"` in the projector or rename the production status — and whether a
  `bp-events-rebuild` is needed to backfill historical successes already dropped.
- [Affects R6/R7/R8][Technical] Aggregation queries, and whether to cache rollups
  (e.g., `flask.g` per-request memo) vs query `events.db` live. Live query is the
  simpler v1; caching needs a measured cost justification.
- [Affects R8][Needs research] Whether `ContentRejectedError` carries a
  machine-readable reason (memory says it lacks a `reason_class`) — sets bucket
  granularity.
- [Affects R5][Technical] Cheapest freshness signal: max `ts_utc` in events.db vs
  projection-cursor position vs last-run timestamp.

## Next Steps
→ `/ce:plan` for structured implementation planning. Blocking product decisions are
resolved; the plan should sequence the projection correctness fix (R1–R4) as a
verified-against-real-data prerequisite before building the views (R6–R9).
