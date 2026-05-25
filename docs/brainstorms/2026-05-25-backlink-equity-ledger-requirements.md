---
date: 2026-05-25
topic: backlink-equity-ledger
---

# Backlink Equity Ledger

## Problem Frame

The operator can see *what was published* (a flat publish history), but not *what each target page actually has working for it right now*. The data to answer this already exists but is scattered: `events.db` holds the article→target inventory, the registry now carries `dofollow_status` + `referral_value` per platform (shipped with dofollow-tiering), anchor diversity is computable via `exact_match_ratio`, and on-demand `verify_published()` records a `verified_at` liveness signal on history rows. Nothing composes these into a per-target view.

Today's `report-anchors` aggregates only by **main domain**, not by individual target URL — so "target page X has 12 live dofollow links across 4 platforms, anchor profile healthy, 3 verifies are stale" is a question the tool cannot answer.

This is a **diagnostic scorecard**: it reflects the current state per target page and lets the operator draw their own conclusions. It is explicitly **not** a recommender and **not** a background monitor.

## Requirements

**Scope unit & data sourcing**
- R1. The ledger aggregates per **target URL** (the page the backlink points at), not per main domain. Rows key on a **normalized canonical URL** (scheme/trailing-slash/case-folded, query-stripped) so trailing-slash/utm/case variants of one page collapse into a single row rather than fragmenting its counts.
- R1a. **Row universe = all attempted targets.** The ledger lists every target that appears in the data, including ones whose publishes all failed (rendered `0/0`), not only targets with ≥1 live link — because a target with nothing working is the most diagnostic case and must not silently vanish. This requires reading the `events` table (attempted/planned kinds), not only `articles` rows that have a `live_url`.
- R2. The ledger's **core aggregation is pure read-side** over already-recorded data. It performs no publishing and no re-execution. The **only** mutation in the whole feature is the WebUI recheck action (R9), which is explicitly outside the aggregation's read-only scope — the CLI engine (R10) never mutates or fetches.
- R3. Each published backlink is attributed to its platform, and the platform's `dofollow_status` (True / uncertain / False) and `referral_value` (high / low) are read from the registry to classify the link. **Caveat (temporal drift):** registry values are *current* and applied retroactively to historical links; a link published when its platform was nofollow but since flipped will be shown by today's value, which may not match the link's actually-rendered `rel`. Accepted caveat — see Key Decisions Q3.
- R3a. **Unknown/retired platform path:** `dofollow_status()` and `referral_value()` return `None` for unregistered/retired platforms and `referral_value()` is `None` for dofollow platforms. The scorecard renders an explicit `unknown` bucket for these rather than dropping or miscounting them.

**Decomposed dimensions (no composite score)**
- R4. The scorecard shows **decomposed dimensions only** — there is no single "equity index" number. Each target row exposes its raw components so nothing is opaque or weighted.
- R5. Per target row, show at minimum: total published links vs confirmed-live links; a dofollow breakdown (dofollow / uncertain / nofollow / unknown counts) plus a single **high-tier live-dofollow** headline number; exact-match anchor-text percentage; count of distinct platforms; and liveness freshness (see R7–R8). The full dofollow×referral-tier cross-tab is an **expandable detail**, not always-visible inline cells — the at-a-glance row stays scannable and every visible column remains a sortable scalar (R6).
- R5a. **Information hierarchy & empty states (UI):** the always-visible columns are target URL + live/total + high-tier live-dofollow + exact-match% + liveness status; the dofollow×tier matrix and per-platform breakdown are behind an expand. Three zero-states are first-class, not edge cases: (a) no published targets ("publish backlinks to populate the ledger"); (b) targets but no live links; (c) the **day-one all-`unverified` case** — confirmed-live renders as a neutral `— / N (not yet verified)`, never an alarming red `0`. Long target URLs truncate with full value on hover.
- R6. Rows are sortable/scannable by each dimension (e.g. by live-dofollow count). No derived weighted sort key is invented.
- R6a. **Triage without a recommender:** the table has a sensible **default sort** over a raw dimension (e.g. confirmed-live-dofollow ascending, so weak targets surface first) and **threshold highlighting** on raw dimensions (e.g. 0 live dofollow, any `failed` liveness, exact-match% over the existing `anchor_alarm` threshold). It offers a lightweight **filter** by liveness status and a free-text target-URL filter. These are sorts/flags over raw dimensions — not a composite index or opportunity ranking — so they stay within R4/R6 while answering "which target needs attention" rather than forcing manual re-triage across 50+ rows.
- R6b. **Passive staleness nudge:** an aggregate "N targets have stale/failed liveness" badge is shown at the ledger entry point, computed at load time from existing `verified_at` (no fetching). This keeps the operator-triggered recheck (R9) from decaying into a uniformly-stale, uninformative column.

**Liveness (read-only, operator-triggered recheck)**
- R7. Liveness is **read-only by default**: the ledger reads the existing `verified_at` / `verify_error` signal and renders status as one of `live-as-of <date>` (last verify succeeded — the verify date is **always shown inline**, not only when stale, so a fresh-looking label never launders an old check into present-tense confidence), `unverified` (never verified), `stale` (verified longer ago than the threshold), or `failed` (last verify failed). It never fetches anything on its own.
- R7a. **Status precedence + multi-link rows:** when conditions overlap (e.g. a failed verify that is also 40 days old), precedence is `failed` > `stale` > `live-as-of` > `unverified`. Because `verified_at` is recorded per **history row** (which may bundle several `article_urls`) and a target receives links from many rows over time, the row's liveness maps to its links by **worst-status-wins**, and the displayed status carries a qualifier when it is row-level rather than link-level evidence. See Key Decisions Q2.
- R8. The staleness threshold defaults to **30 days** and is overridable via a **CLI flag** (e.g. `--stale-days`, following the existing `report-anchors`/`footprint` threshold-flag pattern) and the equivalent WebUI query param — **not** a persisted config section (the repo's `save_config` does not round-trip several sections, so a new persisted key inherits that gap). It affects only the `stale` display flag.
- R9. The WebUI provides an **on-demand "recheck this target" action** that calls the existing `recheck_one()` path and refreshes the affected rows. This is operator-initiated only — no scheduler, no background job, no recheck-on-load.
- R9a. **Recheck interaction states (UI):** `recheck_one` verifies each link with `max_wait_per_url≈10s` **sequentially**, so a target with many links can block for tens of seconds (a 12-link target ≈ up to ~120s). The button has explicit states: idle → in-progress (spinner + disabled + "Rechecking N links…" progress, since URLs are serial) → success (row refresh + a short delta summary, e.g. "9 confirmed, 2 failed, 1 skipped") → error (inline `verify_error`, button re-enabled). Only one recheck runs at a time. Because a recheck can legitimately **downgrade** a previously-live link to `failed`, the delta summary frames a drop as a real finding (no undo — the mutation persists).

**Surfaces**
- R10. A **CLI verb** emits per-target JSONL on stdout (consistent with `report-anchors` / `footprint`), is pure read-only, scriptable, and never rechecks/mutates/fetches. **It is the single engine and reads all three sources directly** (decided): `events.db` (target structure, host, anchor text, published/attempted counts), the `history_store` JSON (platform + `verified_at`/`verify_error` liveness), and the anchor-profile store (`anchor_type` for exact-match%). It joins them per normalized target URL. The CLI is therefore the full source of truth; the WebUI does no independent recomputation.
- R11. A **WebUI page** renders the same per-target rows as a scannable table. Note: the existing `seo_viz` route is a single-domain JSON chart API with **no reusable table** — the table is net-new under that route area, not inherited. The WebUI obtains its data from the same aggregation the CLI exposes (so counts match by construction) and hosts the R9 recheck action.

## Success Criteria
- The operator can answer "what does target page X have working for it right now?" in one glance — high-tier live-dofollow count, dofollow mix, anchor health, and how stale the liveness data is (with verify date shown) — without cross-referencing publish history, registry, and anchor reports manually.
- The CLI and WebUI present the **same per-target rows and counts** for the dimensions both can source; any dimension that only the WebUI can compute (pending Q1) is clearly the same value, not a divergent recomputation.
- Adding it introduces **zero** background fetching or scheduled work; the only network calls are the operator pressing recheck.
- It does not become "a dashboard nobody uses": the default sort + threshold highlighting + stale-count badge (R6a/R6b) make the target that needs attention obvious without manual re-triage across rows.

## Scope Boundaries
- **Not a recommender.** No "where to add links next" ranking, no opportunity scoring. (Possible future round; explicitly deferred.)
- **No composite equity index / magic number.** Decomposed dimensions only (R4).
- **No background health monitor / scheduler / recheck-on-load.** Liveness is read-only + operator-triggered (R7, R9) — this is the hard boundary that keeps the excluded "post-publish health monitor" out.
- **No new persistent liveness store.** Reads the existing `verified_at` signal on history rows; does not introduce a separate liveness table.
- **No referral-traffic / GA4 correlation, no indexation status.** Those were separate (rejected/other) ideas; the ledger composes only data the tool already records.
- **CLI does not mutate or fetch.** Recheck lives only in the WebUI (R9, R10).

## Key Decisions
- **Diagnostic over prescriptive**: chosen for lower risk and faster ship; operator draws conclusions. Recommender deferred.
- **Decomposed dimensions, no composite score**: a weighted "equity index" would be arbitrary and become a vanity metric, undercutting trust in a diagnostic tool.
- **Per-target URL granularity**: this is the actual gap; existing reporting stops at main-domain.
- **Liveness = read existing signal + staleness flag; recheck on-demand in WebUI only**: gets "is it live?" actionability while structurally preventing the excluded background-monitor pattern. CLI stays a pure read-only JSONL engine.
- **Name note**: "Equity Ledger" is retained as the topic name, but there is no computed equity *index* — it is a decomposed per-target backlink scorecard.
- **Q1 (data source) — RESOLVED:** the CLI is the single engine and reads all three stores directly (`events.db` + `history_store` JSON + anchor-profile store), joining per normalized target URL (R10). Rejected: WebUI-as-primary (splits the source of truth) and publish-time snapshotting (breaks read-only; deferred as a possible future correctness upgrade).
- **Q2 (liveness attribution) — RESOLVED:** worst-status-wins across a target's links, with a row-level qualifier when evidence is row-level not link-level (R7a). True per-link liveness is out of scope (data can't supply it without new capture).
- **Q3 (temporal drift) — RESOLVED:** accept the documented caveat (R3) — classification uses current registry values applied retroactively, with the verify date shown inline (R7). Point-in-time snapshotting deferred with Q1.
- **Q4 (row identity & universe) — RESOLVED:** rows key on a normalized canonical URL (R1); the universe is **all attempted targets**, never-linked ones shown as `0/0` (R1a).

## Dependencies / Assumptions
- **Two-store topology (corrected by review):** the data lives in two physically separate stores with no join key designed for per-target rollup. `events.db` `articles` provides `target_urls_json`, `anchors_json` (anchor **text + kind only — no `anchor_type`**), `host`, `live_url`, `published_at_utc` — but **no platform column**. The WebUI `history_store` (`publish-history.json`) provides `platform`, `target_url`, `article_urls`, and the `verified_at`/`verify_error` liveness signal — but **no anchors**. The ledger must join these; the join key (likely `live_url` ↔ `article_urls`, and/or `target_url`) and its reliability must be confirmed (Q1).
- Anchor diversity needs `anchor_type` (`anchor.metrics.exact_match_ratio` counts `anchor_type=='exact'`), which is **not** in `articles.anchors_json`; it lives in the `main_domain`-keyed anchor-profile store. `anchor/metrics.py:153 group_by_target_url()` already buckets `ProfileEntry` per target — so per-target rollup is partly solved **if** the profile store (not `articles.anchors_json`) is the anchor source. R1's "net-new" applies to the join/scorecard, less so to the per-target grouping primitive.
- Assumes registry `dofollow_status()` / `referral_value()` cover every *currently-registered* platform; retired/unknown platforms surface via the R3a `unknown` bucket.
- Liveness has **no persistent store**: `verified_at`/`verify_error` sit on `history_store` rows only, written by on-demand `recheck_one()`.

## Outstanding Questions

### Resolve Before Planning
- (all four load-bearing questions resolved during brainstorm — see Key Decisions Q1–Q4)

### Deferred to Planning
- [Affects R5][Technical] determine the exact per-target `ProfileEntry` construction — confirm whether `anchors_json` keys anchors to specific targets or is a flat per-article blob (multi-target articles risk double-counting), and whether `group_by_target_url()` over the profile store is the cleaner source.
- [Affects R2][Technical] decide the read path: reuse `events/projector.py` reducers vs. a direct read query for the per-target rollup.
- [Affects R9][Technical] confirm `recheck_one()` can be scoped to one target's links given the row-bundling, and whether post-recheck the WebUI refreshes in place or re-invokes the aggregation.
- [Affects R11][Technical] how the WebUI obtains per-target data — CLI subprocess vs. shared aggregation function.
- [Affects R5a/R6][Design] sortable-header keyboard/ARIA treatment for the internal table (low priority, note in planning).

## Next Steps
→ `/ce:plan` for structured implementation planning (all blocking questions resolved).
