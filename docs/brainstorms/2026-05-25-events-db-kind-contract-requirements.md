---
date: 2026-05-25
topic: events-db-kind-contract
---

# events.db Kind & Classification Contract

## Problem Frame

`events.db` is becoming the state-of-truth that the equity ledger, a health
dashboard, footprint, and several Round-8 ideas all read. Today the event vocabulary is
**uncontracted**: kind strings are scattered literals at every writer, the reader side
(`ledger/sources.py`) keeps its **own duplicated** `("publish.intent","publish.confirmed",
"publish.failed")` tuple plus raw SQL, and the projector classifies upstream source records
through hand-maintained status tuples like `_SUCCESS_STATUSES=("succeeded","done")`.

The just-fixed P0 (events projector silently dropped every CLI success) came from exactly this
gap — production wrote checkpoint status `"done"`, the classifier expected `"succeeded"`, the
record was misclassified, and the success was **never written**. Crucially, that failure lived
at the *input-classification* seam, not the output seam. A contract that only validates the
kind passed to `append()` would not have caught it.

This work hardens both seams so the next vocabulary drift — at either the input or output
edge — is caught loudly or quarantined visibly instead of silently dropping data.

**Terminology (used consistently below):** *event kind* = the value written to the `events.db`
`kind` column (e.g. `publish.intent`); *source-record type* = the upstream record type the
projector reads (`checkpoint` / `history` / `drafts`); *source status* = the `status` field
inside an upstream record (e.g. `done`, `published`).

### The two seams

| Seam | Governs | Who | Drift example | P0 here? |
|---|---|---|---|---|
| **A — Output** | the `kind` + required payload fields written to `events.db`, and the kinds readers query | writers (projector, banner_dispatcher, image_gen/caps) → readers (ledger, dashboard) | `ledger/sources.py` duplicated kind tuple goes stale; a typo'd kind; a reader queries a kind no writer emits | No |
| **B — Input classification** | the source-record status vocabularies the projector *reads* (`checkpoint`, `history`, `drafts`) and maps to output kinds | `events/projector.py` reducers | `_SUCCESS_STATUSES` missing `"done"` → success dropped | **Yes** |

## Requirements

Priority tags reflect the review: the **anti-P0 core is Seam B + quarantine** (R4–R7, R8c,
R10). Seam A output-contract work (R1–R3, R8a/b) is real but guards a different, lower-severity
drift; **R2 and R9 are P2 stretch** — plan may split them into a follow-up without weakening
the anti-P0 mechanism.

**Kind vocabulary registry (Seam A)**
- R1. [P1] Introduce `events/kinds.py` as the single source of truth declaring every event
  `kind` currently written to `events.db`. Initial vocabulary — **14 kinds** (verified against
  source, do **not** rename — historical rows depend on the exact strings):
  `publish.intent`, `publish.confirmed`, `publish.unverified`, `publish.failed`,
  `draft.created`, `draft.scheduled`,
  `banner.source_url_fallback`, `banner.skipped_no_method`, `banner.failed`, `banner.embedded`,
  `banner.skipped_no_artifact`, `image_gen_invoked`, `image_gen_capped`, `image_gen_disabled_auto`.
- R1a. [P1] To make R8(a) enforceable, **every writer references its kind via the registry
  symbol**, not a bare literal — because `publish.unverified` is a computed variable (`_kind` at
  `projector.py`), the five `banner.*` kinds are literals in `banner_dispatcher.py`'s `emit(...)`
  (reaching `append()` only as a variable via `_publish_helpers.py`), so a literal-only AST scan
  is blind to exactly the most fragile writers. Tradeoff to weigh in planning: `banner_dispatcher.py`
  is deliberately a pure no-I/O module; importing the registry symbol there erodes that boundary.
  Acceptable alternative is a registry symbol re-exported so the dispatcher imports a constant,
  not `EventStore`. **Honest limit:** a symbol-reference/import check proves the writer *imports*
  the registry, not that the value reaching `append()` *is* that symbol — a writer could import
  the constant and still pass a bare/typo'd literal. To close that gap, pair R8(a) with a lint
  rule **banning bare string literals as the `kind` argument at `append()`/`emit()` call sites**
  (the literal must be a registry symbol). Full dataflow proof is out of scope; lint + symbol
  reference is the pragmatic floor.
- R2. [P2] Each registered kind declares its **required payload fields** (the fields readers
  depend on). Optional/extra fields remain allowed — the contract is a floor, not a closed shape.
  The floor for a kind emitted by multiple reducers (e.g. `publish.confirmed`) must be the
  **intersection** of their payloads, so legitimately-absent fields (`live_url=None`) are not
  wrongly flagged.
- R3. [P1] The reader side stops duplicating vocabulary: `ledger/sources.py` (and any future
  reader/dashboard query) references the registry's kind names instead of its own inline
  `ATTEMPTED_KINDS` tuple.

**Source-status classification mapping (Seam B — anti-P0 core)**
- R4. [P0] Promote each projector source-record status vocabulary into an explicit,
  registry-declared mapping from `(source-record-type, source-status) → event-kind`. Cover all
  three source shapes the projector reads: `checkpoint` (e.g. `pending`/`done`/`succeeded`/
  `failed`), `history` (e.g. `published`/`failed`), and `drafts`. The projector classifies
  *through* this table rather than through inline status tuples like `_SUCCESS_STATUSES`.
- R5. [P1] Existing scattered reducer status logic is **migrated into** the mapping table (a
  refactor, not just a home for new statuses); after migration the table is genuinely the only
  place a status→kind decision is encoded. Adding/changing a recognized status is then a one-line
  table edit.

**Unknown-input handling (the anti-P0 mechanism)**
- R6. [P0] When the projector encounters a `(source-record-type, source-status)` **not** present
  in the R4 mapping, it must **quarantine and continue**: write a `projection.unmapped` record to
  the `quarantine_log` table (capturing the run, source-record type, the unrecognized status
  value, and enough identity to re-process) and proceed with the remaining records. It must
  **not** silently drop the row, and must **not** halt the whole projection run.
  *Reality check (verified):* the `quarantine_log` table exists in `schema.py` but has **zero
  writers today**, `EventStore` exposes **no quarantine method**, and the table has **no `kind`
  column** (`id/ts_utc/source/run_id/reason/raw_payload_json`) — so `projection.unmapped` lives
  in `reason` with structured detail in `raw_payload_json`. **R6 owns the `failure_type`
  field** and ships it with the single value `unmapped_status`; R9 (if/when built) only *widens*
  the value set with `missing_field`. So R6 (P0) carries no dependency on R9 (P2) — R6 ships a
  self-contained one-value discriminator that R9 later extends. (Note: `failure_type` lives
  inside `raw_payload_json` TEXT, so R7/R10 filters JSON-extract it — no dedicated column/index.) Building the `EventStore.quarantine()` write path (interface,
  `conn`/transaction semantics) **is in scope**; "reuse" means the table, not an existing path.
  The write must be **idempotent** — the projector re-runs on every dashboard read, so
  re-projecting a run must not duplicate quarantine rows. Idempotency needs a **dedupe key**
  (`quarantine_log` has none today): either (a) an additive `UNIQUE` index over a deterministic
  natural key — roughly `(run_id, source, source-status, source-record identity)` — used with
  `INSERT OR IGNORE` (a permitted additive migration, see Scope Boundaries), or (b) record
  quarantined identities in the existing `projection_cursor` state so re-projection skips them
  (no schema change, but diverges from the events-table cursor model which keys off *successful*
  projection). Planning picks one; a read-before-write `SELECT` is **not** acceptable (races
  under per-dashboard-read re-projection).
- R7. [P1] `projection.unmapped` quarantine entries are surfaced as a queryable signal —
  satisfied by a direct `SELECT` against `quarantine_log` through the existing SELECT-only
  `store.query()` (no dependency on the deferred `bp-events-query` CLI). They must be visible to
  the operator/dashboard and to tests, so an unmapped status is a noticed event, not a buried log
  line.
- R10. [P1] **Mass-quarantine alarm.** Quarantine-and-continue can recreate silent failure one
  level up: an upstream format flip (the exact P0 class at scale) could quarantine *every* record
  while the run still reports success and the dashboard shows healthy. The run must therefore
  emit a **loud signal when quarantine volume crosses a threshold** — a non-zero/degraded health
  indicator, not merely a queryable row. The threshold is **relative (% of a run's records)** as
  the primary trigger — an absolute row count misses the small-run all-quarantined case and
  false-alarms on large healthy runs — with an optional absolute floor for tiny runs. The exact
  percentage is a planning decision; that the trigger is *relative* and that an alarm exists are
  not. Reuse the existing Plan 005 `record_projection_health()` / `__projection_health__`
  channel rather than inventing a new one. Success criteria must include a **partial-flood
  scenario** (e.g. one source-record type fully unmapped while others are healthy), not only a
  single injected status.

**Enforcement**
- R8. [P1] Primary enforcement is a **CI/test gate**, not a runtime exception. The gate asserts:
  (a) every kind any writer emits is registered in R1 — checked via the **registry-symbol
  reference** required by R1a (an import-graph/symbol check, *not* a literal-only AST scan, which
  would miss the variable-kind writers); (b) **bidirectional reader check** — readers query only
  registered kinds **and** are flagged if they silently *omit* a registered kind, with an
  explicit allowlist for intentional omissions (today `ledger`'s `ATTEMPTED_KINDS` omits
  `publish.unverified`, added by PR #222 — planning must decide: consume it or allowlist it);
  (c) the R4 mapping covers every source-status exercised by the `test_events_projector_*`
  fixtures and reducer code paths — a **fixture/reducer-derived coverage check**. R8(c) does
  **not** and cannot prove the mapping is complete against *future/upstream* statuses — that is
  not statically knowable, and **R6 quarantine is the real defense** against the unknown-status
  class. R8(c) only guards against *known* orphan statuses regressing.
- R9. [P2] `EventStore.append()` performs a **lightweight required-field check** (R2). This is a
  **distinct mechanism from R6**, sharing only the `quarantine_log` table: R6 quarantines an
  unmapped *input status* (projector context: real `run_id` + source row); R9 quarantines a
  malformed *output write* (Seam A). Entries reuse R6's `failure_type` field, *widening* it with
  the value `missing_field` (R6 owns the field and ships `unmapped_status`; R9 only adds this
  value) so operators can triage. On a missing field, `append()` routes to quarantine
  **rather than raising** — because its call sites swallow exceptions (`try/except` at
  `_publish_helpers.py`, `project_run_safe`), so a raise would be swallowed → silent again.
  Open planning question: banner/image_gen callers reach `append()` with **no `run_id`/source**,
  so an R9 quarantine row from them carries null identity and is not re-processable the way R6
  rows are — decide whether such writers skip-and-log instead, or write tolerated null-identity
  rows.

## Success Criteria
- A simulated repeat of the P0 (introduce an unrecognized checkpoint status in a test) results
  in a `projection.unmapped` quarantine entry and a preserved/visible record — **not** a
  silently dropped success.
- A **partial-flood scenario** (one source-record type's status fully unmapped while others are
  healthy) trips the R10 relative-threshold alarm and surfaces a degraded health signal via
  `record_projection_health()` — the run does not report clean.
- Adding a new event kind without registering it (via the R1a registry symbol), or a reader
  querying an unregistered kind — **or a reader silently omitting a registered, non-allowlisted
  kind** — fails CI.
- `ledger/sources.py` no longer carries its own kind tuple; there is exactly one kind
  vocabulary and one status→kind mapping in the tree.
- No existing `events.db` row is invalidated and no kind string is renamed.

## Scope Boundaries
- **`bp-events-query` read CLI is out of scope** for this unit — deferred. (The library-level
  SELECT-only `store.query()` already exists; exposing it as a named, version-pinned CLI is a
  separate follow-up that can reuse this registry once it lands.)
- **`bp-events-rebuild` is out of scope** — it is referenced in docstrings/error messages but
  is not actually shipped as a console script; wiring it is a separate task. This brainstorm
  only must avoid contradicting its eventual existence.
- **No kind renaming / namespace normalization.** The `image_gen_*` underscored, un-namespaced
  names are inconsistent with the dotted `publish.*`/`banner.*` convention, but renaming would
  break historical rows and rebuild reproducibility. The registry documents the inconsistency;
  it does not fix it here.
- No schema migration is required **except** one narrowly-scoped, additive change permitted for
  R6's dedupe key: a `UNIQUE` index (or a derived natural-key column) on the **empty**
  `quarantine_log` table. This is additive, touches no existing rows, and is the only schema
  change in scope; the registry + mapping are otherwise code-level. (Option (b) in R6 avoids even
  this.)
- A health dashboard's read/aggregation logic is **not** rewritten here; this work only provides
  the registry it can adopt.

## Key Decisions
- **Cover both seams, but prioritize Seam B.** The P0 lived at the input-classification seam, so
  an output-only `kinds`-on-`append` contract would not have prevented it. Seam A is retained but
  R2/R9 are P2 — splittable to a follow-up without weakening the anti-P0 mechanism.
- **R8(c) cannot prove completeness against future statuses; R6 quarantine is the real defense.**
  "Every status the projector can produce is mapped" is not statically knowable (the P0 *was* an
  unknown future status). R8(c) is reframed as a fixture/reducer-derived coverage check that
  guards against *known* orphan regressions only.
- **Unknown input status → quarantine + continue, reusing the `quarantine_log` table** (table
  only — the write path is built here). Chosen over fail-loud (one bad record shouldn't halt the
  run, and a raise under `project_run_safe` would be swallowed → silent again) and over warn+skip
  (still drops data). Plus an **R10 mass-quarantine alarm** so quarantine-and-continue can't
  silently swallow a flood.
- **R6 and R9 are distinct mechanisms sharing one table**, discriminated by `failure_type`
  (`unmapped_status` vs `missing_field`) — not one "R6-style" path, because their available
  context differs (projector has run/source identity; banner/image_gen `append()` callers do not).
- **Enforcement: CI/test gate primary; `append()` only lightly validates and quarantines, never
  raises** — a runtime raise would be swallowed by the call sites' `try/except` + `project_run_safe`.
  The gate works only because **R1a forces writers to reference the registry symbol** (a literal
  AST scan would miss the variable-kind writers).
- **Additive where possible, but R3/R4/R5 do edit `ledger/sources.py` + `projector.py` reducers**
  — these are rewrites, not pure additions. "Additive" applies to the *new* `events/kinds.py` and
  to not rewriting the dashboard's reads; the in-repo reducer/reader edits must be sequenced
  against whatever concurrent work actually exists at plan time (re-verify — see Dependencies).

## Dependencies / Assumptions
- Recently-merged Plan 005 projector-correctness work (PR #222) is the current `projector.py`
  baseline; this work edits the same file, so it must rebase on merged `origin/main` and run
  from a **fresh isolated worktree**.
- A health-dashboard effort (worktree `bp-health-dashboard`) reads events.db. **Re-verify its
  state at plan time** — `git worktree list` shows no such worktree registered in the canonical
  repo as of this brainstorm, so the collision premise may be stale; the plan must re-check the
  dashboard's actual events.db read sites before assuming a conflict. If it has added its own
  kind references, R3 extends to it as a follow-up rather than a same-PR rewrite.
- Assumes the 14 kinds enumerated in R1 are the complete live set as of 2026-05-25; the R8(a)
  registry-symbol gate (R1a) is itself the guard that this assumption stays true.

## Outstanding Questions

### Deferred to Planning
- [Affects R1a][Technical] How `banner_dispatcher.py` (a deliberately pure no-I/O module)
  references the registry symbol without importing `EventStore` — e.g. a constants-only re-export
  it can import. Decided in principle (symbol reference over literal AST scan); the *form* is open.
- [Affects R4][Technical] Exact enumeration of every status value each source-record type
  (`checkpoint`/`history`/`drafts`) carries today — derive from `projector.py` reducers + the
  `test_events_projector_{checkpoints,drafts,history}.py` fixtures. Audit for already-unmapped
  live statuses (e.g. any `complete`/terminal status the checkpoint reducer doesn't handle).
- [Affects R2][Technical] Per-kind required-field lists — derive from the **intersection** of
  payloads at each emit site (esp. multi-reducer kinds like `publish.confirmed`).
- [Affects R9][Technical] Quarantine-row identity contract for non-projector callers
  (banner/image_gen) that have no `run_id`/source: skip-and-log vs. tolerated null-identity row.
- [Affects R10][Technical] Concrete relative-threshold percentage (and optional absolute floor)
  for the mass-quarantine alarm; channel is decided (reuse `record_projection_health()`).
- [Affects R6][Technical] Dedupe-key choice: additive `UNIQUE` index on `quarantine_log` vs.
  `projection_cursor`-state tracking (R6 options a/b).
- [Affects R8b][User decision] Should `ledger` consume `publish.unverified`, or is its omission
  intentional and allowlisted?

## Next Steps
→ `/ce:plan` for structured implementation planning.
