---
date: 2026-05-26
topic: dual-state-divergence-auditor
---

# Dual-State Divergence Auditor

## Problem Frame

`backlink-publisher` keeps the same publish facts in two places: the operator-facing JSON stores under `webui_store/` (`publish-history.json`, the publish checkpoint, the draft-queue) and the projected `events.db articles` table. The projector flows JSON → events.db, and `articles.live_url` is `UNIQUE`; the read-time reconciler is designed to **never raise** and to degrade to a stale result (`reconcile.py`).

**Trust model (important):** *neither* store is unconditionally ground truth. The JSON side is guarded only by an in-process lock and can be left internally inconsistent by a crashed/partial save; events.db is the rebuildable, lossy side (UNIQUE-swallow, NULL orphans). The only true ground truth for "did this link publish?" is the live web, which is explicitly **out of scope** here. The auditor therefore reports *disagreement between two local mirrors* and, for classes where neither side is authoritative (drift, missing-projection), must label the finding `authority: indeterminate` rather than implying the JSON side is correct.

Some divergence is **already** partially visible today: `reconcile.project_on_read` parks unparseable sources in `quarantine_log` and surfaces `gap`/`degraded` flags on every Health Dashboard load, and PR #237 adds a mass-quarantine alarm. What those mechanisms do **not** surface — and what this auditor targets — is the silently-successful-but-divergent classes: NULL-`live_url` orphans, UNIQUE-swallowed republishes, and count/URL drift. (Note: the projection-skipped class self-heals on the next dashboard load, because `project_on_read` re-flushes — so those findings are point-in-time.)

This auditor gives that residual divergence **visibility**, as a read-only diagnostic, without touching the events.db authority model that PRs #235/#237 are actively hardening.

```
   webui_store/ JSON ("what I published" — guarded only by an in-process lock)
   ├─ publish-history.json ─┐
   ├─ publish checkpoint ────┼──▶  [projector: JSON→db, WRITE path]  ──▶  events.db articles
   └─ draft-queue ──────────┘                                              (live_url UNIQUE; rebuildable, lossy)

   [Divergence Auditor]  reads BOTH sides via SELECT-only / own JSON parser, writes NEITHER
        │  diffs JSON sources  ⟷  articles   (point-in-time snapshot)
        ▼
   divergence report  (JSONL records on stdout + human summary on stderr; exit 0)
```

The auditor is a **new, separate** verb — it is **not** `reconcile.py` (which is itself a write path: it calls `flush_for` and writes `quarantine_log`). It must read articles via the SELECT-only store query path and parse the JSON sources with its own reader; it must **not** import `events.reconcile` or `events.projector.flush_for`.

## Requirements

> **Phasing (decided):** v1 ships the pure-column classes (R1, R3) that have **zero dependency on the projector semantics #235/#237 are rewriting**. R2 and R4 are **gated** behind #235/#237 merging and are specified here so the v1 record/report shape accommodates them without rework.

**v1 — Divergence detection (ships now)**
- R1. Detect **NULL-`live_url` orphans**: articles rows whose `live_url` is NULL (publish crashed before URL capture). Pure articles-side read, no JSON join.
- R3. Detect **count/URL drift**: per publish-host, the `publish-history.json` published count ≠ articles count, or the same publish carries a different `live_url` on each side. Marked `authority: indeterminate`.

**Gated behind #235/#237 (specified now, built after)**
- R2. Detect **UNIQUE-swallowed republishes**: **two or more distinct source records** (different record id / `run_id` / `published_at`, within or across sources) whose **canonical** `live_url`s collide, such that only one is represented in articles. The collision key is `canonicalize_url(live_url)` — the same transform the projector applies (`_article_payload`). **Cursor-idempotency is explicitly NOT a divergence**: the same source record re-seen (same id / unchanged checkpoint status) is filtered by `projection_cursor` before any INSERT and is the healthy, expected case. A republish is a divergence only when a *different* publish event carries a colliding canonical URL.
- R4. Detect **JSON-present / articles-missing**: a source record in a *publishing* state (history `published` with non-empty URLs; drafts `published`; checkpoint `done`/`succeeded`) that has no corresponding articles row. Records in non-publishing states (drafted, scheduled, pending, failed) and `published`-but-urlless records (which the projector intentionally emits with no article row) are **excluded** and must not be flagged. Marked `authority: indeterminate` and point-in-time (may self-heal on next dashboard re-flush).
- R5. Source scope grows with phasing: **v1 reads `publish-history.json` + articles only** (sufficient for R1 + R3). The publish checkpoint and draft-queue are added with R4. The auditor reads sources from their canonical on-disk locations (mirroring `reconcile._collect_sources`: history + drafts under the config dir, checkpoints under the cache dir) using its own read-only parser.

**v1 — Operator triggering & actionability (audience = operator)**
- R11. **Trigger/nudge:** the operator must be pulled to the audit at the moment divergence is most likely fresh. `publish-backlinks` (and/or the Health Dashboard) emits a one-line stderr/UI nudge after a run — e.g. "possible store divergence; run `audit-state`" — reusing surfaces the operator already watches. This is **not** the rejected watchdog; it is a passive pointer.
- R12. **Per-class operator remediation:** the stderr summary (R7) names a concrete manual action per finding class so the report is actionable, not just visible — e.g. R1 orphan → "the prior publish captured no URL; re-run publish for this target or verify it manually"; R3 drift → "verify the live URL on the web, then re-run publish to refresh the record." (The auditor still performs no writes; the operator acts.)

**Reporting**
- R6. Emit one structured divergence record per finding on stdout as JSONL, each carrying at minimum: divergence class (R1–R4), originating source, **`source_tier`** (`high-signal` for history/checkpoint, `informational` for drafts), **`authority`** (`indeterminate` where neither local store is ground truth), the source-side identifying key (canonical `live_url` where applicable), the articles-side `article_id` (when one exists), and the conflicting value(s). Records are self-describing enough that a *future* reconcile step could consume them unchanged ("build the columns, not the machine"); v1 takes no action.
- R7. Emit a **human-readable summary on stderr** (counts per class, per source, with informational-tier findings reported separately so they do not inflate error-class counts). stdout stays pure data per the pipeline contract.
- R8. **Severity is a static `{source_kind → tier}` map** reusing the projector's existing three source kinds (`checkpoint` | `history` | `drafts`): history/checkpoint = high-signal, drafts = informational. **No configurable severity thresholds in v1.**
- R9. **Always exit 0** when the audit runs to completion, regardless of how many divergences are found — a pure diagnostic, not a gate. A reserved non-zero exit for *operational* failure (unreadable store, schema-too-new) is acceptable per existing exit-code conventions.
- R10. Findings are **point-in-time**. The auditor must read all sources against a single consistency point: snapshot each JSON source's mtime/checksum around the articles read and, if any source changed mid-audit, either re-read or down-classify the affected findings as `possibly-transient` (a concurrent in-flight publish is not corruption).

## Success Criteria
- **(v1)** Running the verb against a store seeded with known NULL-`live_url` orphans and a known count/URL drift surfaces every planted divergence with the correct class, tier, authority label, and identifying keys. (R2's distinct-record UNIQUE-collision seed is added when R2 ships post-#235/#237.)
- **(v1)** After a publish run that produces a divergence, the operator sees the R11 nudge on a surface they already watch, and each reported finding carries the R12 remediation hint.
- The run is provably **non-mutating**: verified by recording sha256 of `events.db` (and its `-wal`/`-shm` sidecars) and each JSON source before and after, asserting they are unchanged, **and** asserting (via test double) that no write/projection/reconcile path (`flush_for`, `connect_immediate`, any INSERT/UPDATE) is invoked.
- On a **real (or production-snapshot) store**, the false-positive rate for R3/R4 is hand-verified below a stated threshold — the join-key and canonicalization logic is validated against real data, not only synthetic seeds the planter and detector both agree with.
- A run with only draft-queue differences marks them `informational` and they do not inflate error-class counts.
- A concurrent in-flight publish during the audit does not produce a hard `articles-missing`/`drift` finding (it is down-classified `possibly-transient` per R10).
- The JSONL output is chainable (valid one-record-per-line) and the stderr summary is legible on its own.

## Scope Boundaries
- **No reconcile / repair / write of any kind** — read-only only, deferred until after #235/#237 land and the authority model is settled. Must not call the projector's write path, import `events.reconcile`, or call `flush_for`/`connect_immediate`.
- **CLI only in v1** — no WebUI panel (Health Dashboard surfacing is a possible later extension).
- **Not a continuous monitor/watchdog** — one-shot, on-demand audit.
- **No network / live-URL re-fetch** — it diffs the two *local* stores against each other, not against the live web. (This is why R3/R4 carry `authority: indeterminate`.)

## Key Decisions
- **Surface = CLI verb** (pipeline-native): JSONL on stdout, human summary on stderr; matches the six-stage pipeline, scriptable, and lets a later dashboard just read the report.
- **Pure diagnostic, exit 0**: divergence is currently *expected* to exist, so gating would be noise. Visibility first.
- **Join key pinned**: join `canonicalize_url(json raw url) == articles.live_url`, applying the *same* `canonicalize_url` the projector uses; treat `host` as publish-host (`_host_of(canonical live_url)`) consistently on both sides. Reuse `projector._article_payload` / `canonicalize_url` read-only rather than re-deriving. `published`-but-urlless records have no joinable key and form their own bucket.
- **R2 reframed to source-side distinct-record collision** (above) so it is computable read-only and does not mislabel routine idempotent re-projection as divergence.
- **Trust model is explicit**: report disagreement, label authority, never imply the JSON side is canonical for indeterminate classes.
- **Forward-compatible records** (R6) are reconcile-ready in *shape* only; v1 builds no consumer.
- **Phasing — R1/R3 now, R2/R4 after #235/#237** (decided): the pure-column classes carry zero projector-semantics dependency and deliver the core "visibility now" value; R2/R4 depend on exactly the projector INSERT/dedup + status-vocabulary logic those PRs rewrite, so building them now would guarantee rework and risk two conflicting divergence interpretations. v1 reads history + articles only.
- **Audience = operator** (decided): hence R11 (nudge from an existing surface so the diagnostic is actually run) and R12 (per-class manual remediation so findings are actionable, not anxiety). The auditor stays read-only; the operator performs any fix.

## Dependencies / Assumptions
- Reuses, read-only, the projector's existing helpers (`canonicalize_url`, `_article_payload` collision key, `_host_of`, `_detect_source`, the per-source publishing-status vocabulary) rather than re-deriving them — but must invoke **none** of the projector's cursor-advancing or write functions.
- Opening `events.db` must not trigger a schema migration / WAL checkpoint that mutates the file or sidecars (resolve the SELECT-only read path during planning; see questions).
- #235/#237 are **open** and both modify `events/projector.py` (and #235 `events/schema.py`); the R2 collision-reconstruction and the R4/R8 status-vocabulary classification depend on exactly the projector semantics those PRs are rewriting.

## Outstanding Questions

### Resolve Before Planning
- *(none — both blocking decisions resolved: ship R1/R3 now with R2/R4 gated behind #235/#237; audience = operator, so R11 nudge + R12 remediation are in scope)*

### Deferred to Planning
- [Affects R10][Technical] The exact consistency-snapshot mechanism (mtime/checksum re-read vs abort-and-retry) and the `possibly-transient` window definition.
- [Affects R5][Technical][Needs research] Draft-queue persistence: confirm the draft-queue is file-backed (readable like history/checkpoint) rather than in-memory singleton state.
- [Affects non-mutating criterion][Technical] Whether to open `events.db` via the store's read API or a raw read-only SQLite connection, to guarantee no schema-upgrade/WAL-checkpoint side effects.
- [Affects R2][Technical] Whether retrospective R2 detection is required (no persistent trace of past swallows exists — only forward-looking source-side reconstruction is possible without a projector change to log `unique_collision` into `quarantine_log`).
- [Affects R3][Technical] Timestamp-representation skew between sources (history/drafts normalized local-naive vs checkpoint ISO-offset) when comparing by publish identity.
- [Affects naming][User decision — low stakes] Verb name (`audit-state` / `divergence-report` / `reconcile-check` — avoid the last given the `reconcile.py` write-path name clash).

## Next Steps
→ `/ce:plan` for structured implementation planning (v1 scope: R1, R3, R5-v1, R6–R12; R2/R4 deferred until #235/#237 merge).
