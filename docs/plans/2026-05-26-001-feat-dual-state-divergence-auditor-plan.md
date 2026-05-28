---
title: "feat: Dual-State Divergence Auditor (audit-state) — v1"
type: feat
status: shipped
date: 2026-05-26
deepened: 2026-05-26
origin: docs/brainstorms/2026-05-26-dual-state-divergence-auditor-requirements.md
claims: {}
---

# feat: Dual-State Divergence Auditor (`audit-state`) — v1

## Overview

A new **read-only** diagnostic CLI verb, `audit-state`, that diffs the operator-facing `publish-history.json` against the projected `events.db articles` table and reports divergences the operator cannot otherwise see. v1 ships two classes — **R1 NULL-`live_url` orphans** (articles-side scan) and **R3 store drift** (a canonical-URL join between history and articles) — both of which read only stable `articles` columns and have no dependency on the projector reducer logic PRs #235/#237 are rewriting. It emits JSONL on stdout + a human summary on stderr, always exits 0, and performs **no data mutation** (it reads a throwaway snapshot copy of events.db, never the live store). A *conditional* nudge from `publish-backlinks` points the operator at the verb only when a run produces divergence-precursor rows.

## Problem Frame

`backlink-publisher` records publish facts in two stores — `webui_store/publish-history.json` and `events.db articles` — and `reconcile.py` is designed to never raise / degrade to stale, so divergences accumulate silently. Today nothing surfaces a NULL-`live_url` orphan (a publish whose projected article row never captured a URL) or drift between the two stores (a history-published URL with no matching article row, or vice versa). The operator trusts "did this link publish?" answers built on stores that can quietly disagree. This auditor gives that residual divergence visibility without touching the authority model #235/#237 are hardening (see origin: `docs/brainstorms/2026-05-26-dual-state-divergence-auditor-requirements.md`).

## Requirements Trace

- **R1** — Detect NULL-`live_url` orphans: `articles` rows with `live_url IS NULL` (a publish whose article row was projected without a URL). Articles-side scan; no join.
- **R3** — Detect store drift via a **canonical-URL join** (no host grouping): for each history `published` record, canonicalize each published URL in `article_urls` and match against `canonicalize_url(articles.live_url)`. Report (a) a history-published URL with **no matching article row** (history-side orphan), (b) an article row matching **no** history-published URL (article-side orphan). Fan-out aware (a multi-URL history record legitimately maps to multiple article rows). Marked `authority: indeterminate`.
- **R5 (v1)** — JSON sources diffed: `publish-history.json` only (checkpoint/draft-queue deferred with R4). Note R1 reads the already-projected `articles` table regardless of which source produced the rows.
- **R6** — One JSONL record per finding on stdout, carrying: `class`, `source`, `source_tier`, `authority`, the canonical URL key (where applicable), `article_id` (when present), and a `details` object with the conflicting value(s). Reconcile-ready in shape; v1 takes no action. The `class` enum expands when R2/R4 land without breaking `to_jsonl_dict()`.
- **R7** — Human-readable summary on stderr, reading `source_tier` from each record: high-signal counts and informational counts printed separately so informational findings don't inflate error counts.
- **R8** — Static `{source_kind → tier}` map (`history` = high-signal). No configurable thresholds in v1.
- **R9** — Always exit 0 on completion (with or without findings). Distinguish store states: **absent** stores (fresh operator, no events.db / no history yet) → exit 0 with a "nothing to audit" stderr note; **present-but-unopenable** (permission denied, corruption, schema-too-new) → `DependencyError` (exit 3) per the repo's documented exit-code contract.
- **R10** — Findings are point-in-time. Hash + mtime the source files immediately before the first copy byte and immediately after the copy completes; on any change, re-copy once, then `PRAGMA quick_check` the copy and mark affected findings `authority: possibly-transient` if a tear is suspected.
- **R11** — `publish-backlinks` emits a one-line stderr nudge **only when the just-finished run produced divergence-precursor rows** (`*_unverified` / `failed_partial` / `published`-with-empty-`article_urls`), not on every run. Passive pointer, fail-safe, never a watchdog.
- **R12** — The stderr summary names a concrete manual remediation per finding class (auditor performs no writes; operator acts).

## Scope Boundaries

- **R2 (UNIQUE-swallowed republishes) and R4 (JSON-present/articles-missing from checkpoint/drafts) are OUT** — gated behind #235/#237 (they depend on the projector INSERT/dedup + status-vocabulary logic those PRs rewrite). **R3 must not re-detect R2:** a UNIQUE-collision-dropped article (`skipped_due_to_dedup`) would naively read as a history-orphan — these are explicitly labeled/suppressed as deferred-R2, not reported as R3 drift.
- **No data mutation, no schema migration, no WAL checkpoint** on the live `events.db`. Reads go against a throwaway snapshot copy. Must not import `events.reconcile` or `events.projector.flush_for`, must not call `EventStore.connect`/`connect_immediate`.
- **CLI + `python -m` only** — `audit-state` is intentionally **not** wired into the WebUI `cli_runner._CLI_MODULES` dispatch in v1. Health Dashboard surfacing is an optional follow-up (Unit 4b).
- **Not a watchdog**, **no network / live-URL re-fetch** (hence `authority: indeterminate` for R3).

## Context & Research

### Relevant Code and Patterns

- **`cli/equity_ledger.py`** (61 SLOC) — the read-only-aggregation sibling to mirror: thin CLI shell delegating to the `ledger/` package; `def main(argv=None)`, lazy `import argparse` inside `main`, config-echo banner to stderr, `write_jsonl(...)` to stdout, `if __name__ == "__main__": main()`. **Note:** its post-parse validation uses bare `raise SystemExit("equity-ledger: ...")` (exit 1), *not* `UsageError` — see Key Decisions for which to follow.
- **`ledger/sources.py`** — `build_target_buckets()` reads `articles` via `store.query("SELECT target_urls_json, live_url FROM articles")`, loads history via a **lazy** `_load_history`, and wraps `canonicalize_url` in a `None`-tolerant `_canon()`. **This is the canonical join pattern** — it joins history↔articles on canonical URL, *not* on host. The auditor copies the `_canon` wrapper and the canonical-URL join, but reads the two sources **separately and diffs** (it does not reuse `build_target_buckets`, which *reconciles* and would hide the drift we detect).
- **`_util/url.py`** — `canonicalize_url` (line 124, pure/idempotent: lowercases scheme+host, strips default port, trailing slash, `utm_*`, fragment). **Use it for the join key on both sides.** `is_same_host`/`_normalize_host_for_compare` (line 70) re-parse full URLs and return False for a bare netloc — **do not feed `articles.host` (a bare netloc) into `is_same_host`**; the canonical-URL join needs no host key. Do **not** import `_host_of` (it lives in the write-side `projector.py`).
- **`events/store.py`** — `query()` and all connects run `maybe_upgrade_schema`+commit and `_tighten_wal_sidecars` chmod (mutating). The store is **WAL mode** (`PRAGMA journal_mode = WAL`, line 211) with no explicit checkpoint anywhere; SQLite's default **checkpoint-on-close** drains the WAL when each short-lived connection closes (e.g. after `project_run_safe`). Path resolution can be mirrored without instantiating `EventStore`.
- **`events/schema.py:51-62`** — `articles` columns: `article_id, body, anchors_json, target_urls_json, lang, host, live_url (UNIQUE), published_at_raw, published_at_utc, run_id`. `live_url` is `canonicalize_url(...)` or `None` when empty.
- **`events/projector.py`** — the history reducer fans out `article_urls` into one `add_article` per URL; an empty-`article_urls` published row emits a `publish.confirmed` *event* with `live_url=None` and **no** article row; the **checkpoint reducer** (run inline by `project_run_safe` on every publish) is what produces article rows including `live_url=None` for crashed/urlless publishes. So R1's NULL rows are real and present in `articles` under normal operation; R1's *read* is projector-independent even though the *producer* is the checkpoint path.
- **`webui_app/helpers/history.py:83-97`** — `publish-history.json` record: `id, run_id, target_url, platform, language, status, created_at, article_urls (LIST), title, adapter`, and `error` **only when present** (use `.get("error")`). `article_urls = [u for u in (published_url, draft_url) if u]` — **it can include a draft URL** that has no matching article row; R3 must not flag the draft URL as a mismatch. `status` values: `published / failed / failed_partial / *_unverified / expired / bound`; empty-`article_urls` published rows are coerced to `failed` upstream.
- **`cli/publish_backlinks.py:402-446`** — end-of-run seam after `project_run_safe(run_id)`; the R11 conditional nudge goes here (fail-safe `print(..., file=sys.stderr)`). File ceiling 440 SLOC, ~401 now.
- **`webui_app/helpers/cli_runner.py:79-86`** — `_CLI_MODULES` WebUI dispatch map (hand-maintained, mirrored by `tests/test_cli_python_m_entrypoints.py`). v1 deliberately does **not** add `audit-state` here.
- **`_util/errors.py`** — `UsageError(exit_code=1)`, `DependencyError(exit_code=3)`, and `handle_error()` which maps a raised `PipelineError` to its exit code. Used by `medium_login`/`frw_login`.
- **`tests/test_cli_equity_ledger.py`** — test template: per-test `BACKLINK_PUBLISHER_CONFIG_DIR`/`_CACHE_DIR` monkeypatch, `_run(argv)` harness (captures stdout/stderr/code), `_seed` via `EventStore().add_article({...})` + `history_store.save([...])`.

### Institutional Learnings

- **`publish-history-helper-invariant-2026-05-20`** — `status="published" ⟹ non-empty url`; a published row with empty `article_urls` is a known phantom-claim defect (and is coerced to `failed` upstream). R3 treats a genuine history-published-URL-with-no-article as drift; the auditor never synthesizes rows.
- **`tests-coupled-to-operator-config-state-2026-05-18`** + memory `webui_store-config-dir-frozen` — resolve `events.db` and `publish-history.json` via the env-honoring `BACKLINK_PUBLISHER_CONFIG_DIR` resolver, lazily. Read the history JSON file directly; do **not** import the import-frozen `webui_store` singletons.
- **`argparse-choices-vs-usage-error-exit-clash-2026-05-20`** — no `argparse choices=`; validate post-parse.
- **`python-m-needs-main-module-after-package-split-2026-05-19`** — keep `audit_state` a single file with the `__main__` guard; add to `tests/test_cli_python_m_entrypoints.py`.

### External References

- SQLite WAL semantics (verified during deepening): reading a live WAL DB freshly requires the `-shm` wal-index (a filesystem touch); `immutable=1` skips `-wal` → stale reads; a transactionally consistent copy needs `VACUUM INTO` / the backup API. See Key Decisions for how this shapes the snapshot approach.

## Key Technical Decisions

- **Read events.db via a snapshot-copy opened read-only.** Copy `events.db` + `events.db-wal` (NOT `-shm` — let SQLite rebuild it in the copy) into a **writable** temp dir, then open the copy with `sqlite3.connect("file:<tmp>/events.db?mode=ro", uri=True)` and SELECT stable columns; delete the temp dir in `finally`. **Rationale:** `immutable=1` ignores `-wal` → would miss any uncheckpointed publish (stale → false findings); plain `mode=ro` on the *live* db must create the `-shm` wal-index (touches the real store). Snapshot-copy gives fresh data while leaving the real store byte-identical, and decouples the auditor from the `EventStore`/`projector` connect path #235/#237 are churning. *(Refined from the deepening: checkpoint-on-close means the WAL is usually drained after each run, so uncheckpointed rows persist mainly mid-publish/post-crash — the `-wal` copy captures exactly those; the rationale for copying `-wal` is robustness to that window, not "the WAL is never drained.")*
- **Bound the torn-snapshot risk** (sequential file copy of a live WAL DB is not atomic). Hash+mtime `events.db` and `-wal` immediately before the first copy byte and after the copy completes; if either changed, re-copy once; run `PRAGMA quick_check` on the copy and, if the source changed during the window or the check is not clean, mark affected findings `authority: possibly-transient` (R10). *(Deferred alternative: if tear-freedom proves necessary, switch to `VACUUM INTO`/backup API from a `mode=ro` source connection — transactionally consistent but touches the source `-shm`. Documented as the fallback, not v1 default.)*
- **R3 joins on canonical URL, never on host.** `articles.host` is a bare netloc that `is_same_host` rejects, and `host` mixes publish-host vs target-host across row types — so host grouping is unsafe. Join `canonicalize_url(history.article_urls[*])` against `canonicalize_url(articles.live_url)` (the `ledger/sources.py` pattern). This is fan-out-aware and sidesteps the count-bucketing bug. Filter draft-only URLs out of the published-URL set. A UNIQUE-collision-dropped article is labeled deferred-R2, not R3.
- **Read `publish-history.json` directly** from the env-resolved config dir (not the `webui_store` singleton). Use `.get("error")` (conditional key).
- **Reuse `canonicalize_url` + the `_canon` wrapper** from `_util/url.py` / `ledger/sources.py`. Do not import `projector`/`reconcile`.
- **Operational failures map to the documented exit-code contract:** present-but-unopenable store → `DependencyError` (exit 3); absent store → exit 0 with a "nothing to audit" note. Bad flags → post-parse `UsageError` via `handle_error` (exit 1).
- **Static tier map** `{ "history": "high-signal" }` as a module constant (R8) — no config surface.

## Open Questions

### Resolved During Planning

- *Open events.db without mutating it AND read fresh data?* — Not via `EventStore` (migrates + chmods sidecars), not `immutable=1` (stale), not bare `mode=ro` on the live db (touches `-shm`). Resolved: snapshot-copy `events.db`+`-wal`, open the copy `mode=ro`, let `-shm` rebuild in the (writable) temp dir.
- *R3 join key?* — `canonicalize_url(history article_url) == canonicalize_url(articles.live_url)`. Host grouping rejected (`articles.host` is a bare netloc `is_same_host` can't compare; host semantics mix publish/target).
- *Where do NULL-`live_url` article rows come from under v1 scope?* — The checkpoint reducer (run by `project_run_safe` on every publish) produces them for urlless/crashed publishes; they sit in `articles` and R1's SELECT reads them projector-independently.
- *Operational vs benign store states?* — absent → exit 0; unopenable → exit 3 (`DependencyError`).
- *Bad flags?* — post-parse `UsageError` → `handle_error` (exit 1), not `argparse choices=`.

### Deferred to Implementation

- Exact tear-detection thresholds (mtime-only vs mtime+sha; quick_check vs integrity_check) — settle against the real file at implementation time.
- Whether the freshness window is narrow enough to keep snapshot-copy, or whether `VACUUM INTO`/backup API is warranted (the documented fallback).
- Final verb help text and the exact conditional-nudge wording.
- Whether the Unit 4b Health Dashboard hint ships in this PR or a follow-up.

## Implementation Units

- [x] **Unit 1: `audit/` package — read-only readers (snapshot events.db + history)**

**Goal:** A reader layer that loads `articles` (from a snapshot copy, fresh + zero-touch on the real store) and `publish-history.json` into separate in-memory views, with env-honoring path resolution and tear detection.

**Requirements:** R5 (v1 sources), R9 (store-state distinction), R10 (snapshot + tear detection); foundation for R1/R3.

**Dependencies:** None.

**Files:**
- Create: `src/backlink_publisher/audit/__init__.py`
- Create: `src/backlink_publisher/audit/readers.py`
- Test: `tests/test_audit_readers.py`

**Approach:**
- Resolve the events.db + history paths via the env-honoring config/cache resolver (mirror `store.py`'s path resolution; do **not** instantiate `EventStore`).
- **Store state check:** if neither events.db nor `publish-history.json` exists → return a sentinel "nothing to audit" so the CLI exits 0. If events.db is absent but history exists → readable (empty articles view). If events.db is present but `mode=ro` open fails → raise a typed operational error the CLI maps to `DependencyError` (exit 3).
- **Snapshot read:** hash+mtime `events.db` and `events.db-wal`; copy `events.db` + `events.db-wal` (only those present) into a `tempfile.mkdtemp()` (writable) dir; open the copy `sqlite3.connect("file:<tmp>/events.db?mode=ro", uri=True)`, `row_factory = sqlite3.Row`, `SELECT article_id, host, live_url, target_urls_json, published_at_utc, run_id FROM articles`; let SQLite rebuild `-shm` in the temp dir; re-hash the real sources; run `PRAGMA quick_check` on the copy. Delete the temp dir in `finally`.
- `read_history()` reads `<config_dir>/publish-history.json` directly (env-resolved, lazy); tolerate a missing file (empty list); use `.get("error")`.
- Return lightweight typed records + a `transient` flag (set when a source changed during the copy window or quick_check was not clean). `_canon(url)` None-tolerant wrapper around `canonicalize_url`.

**Patterns to follow:** `ledger/sources.py` (lazy history load, `_canon`); `events/store.py` path resolution (NOT its connect path); `_util/url.py`.

**Test scenarios:**
- Happy path: seeded articles + history → readers return the expected rows/dicts from the snapshot.
- **Freshness (load-bearing):** open a WAL writer connection, `INSERT`+`commit` an article **without closing the connection** (so the row stays in `-wal`, uncheckpointed), then run the snapshot read → the WAL-only row **is** seen. Negative control: assert an `immutable=1` open of the same snapshot does **not** see it (proves the `-wal` copy is load-bearing).
- Edge: missing `publish-history.json` → empty list, no raise.
- Edge: `live_url IS NULL` row returned (not filtered) for Unit 2.
- R9: neither store exists → "nothing to audit" sentinel (CLI exits 0). events.db present but unopenable (e.g. chmod 000 / truncated) → typed operational error.
- Integration (zero-touch proof): sha256 the **real** `events.db` + `-wal` + `-shm` before and after a full read; assert all unchanged and no new sidecars created against the real store.
- Integration (temp cleanup): assert the temp dir is removed after the call (and after an exception via `finally`).
- Integration (R10 tear): change the real `-wal` content/mtime between the pre- and post-copy hash → the run is flagged `transient`.

**Verification:** Readers return fresh data (incl. uncheckpointed WAL rows); the **real** store + sidecars are byte-identical; temp copy cleaned up; tear flagged.

- [x] **Unit 2: Divergence detection — R1 orphans, R3 canonical-URL drift**

**Goal:** Pure diff logic producing divergence records for R1 + R3, honoring the `transient` flag.

**Requirements:** R1, R3, R6 (record shape), R8 (tier), R10 (consume `transient`).

**Dependencies:** Unit 1.

**Files:**
- Create: `src/backlink_publisher/audit/diff.py`
- Test: `tests/test_audit_diff.py`

**Approach:**
- `DivergenceRecord` dataclass with `to_jsonl_dict()`. Concrete shapes:
  - `null_url_orphan`: `{class, source:"articles", source_tier:"high-signal", authority:"indeterminate", article_id, details:{reason:"live_url IS NULL"}}`
  - `history_orphan` (history-published URL with no article): `{class, source:"history", source_tier, authority:"indeterminate", canonical_url, details:{history_id, raw_url}}`
  - `article_orphan` (article with no history-published URL): `{class, source:"articles", source_tier:"high-signal", authority:"indeterminate", article_id, canonical_url}`
- **R1:** every article with `live_url IS NULL` → `null_url_orphan`.
- **R3:** build the set of canonical published URLs from history (`published` rows, iterate `article_urls`, **exclude the draft URL**, `_canon` each); build the set of `_canon(articles.live_url)`; diff the two sets → `history_orphan` (in history, not articles) and `article_orphan` (in articles, not history). Fan-out is automatic (set membership, not counts). **Suppress/label deferred-R2:** if a history URL is absent from articles AND another article shares its canonical key (UNIQUE-collision signature), tag it `deferred_r2` instead of `history_orphan`.
- **R10:** if Unit 1's `transient` flag is set, stamp affected records `authority: possibly-transient`.
- Static tier map `{ "history": "high-signal" }`.

**Technical design:** *(directional, not implementation spec)*
```
R1: articles where live_url IS NULL                        -> null_url_orphan
R3: H = { _canon(u) for published rows, u in article_urls except draft_url }
    A = { _canon(row.live_url) for articles if live_url }
    H - A -> history_orphan   (unless UNIQUE-collision signature -> deferred_r2)
    A - H -> article_orphan
    if transient: authority = possibly-transient
```

**Patterns to follow:** `ledger/sources.py` canonical-URL join + `_canon`; `ledger/aggregate.py` record shaping.

**Test scenarios:**
- Happy: seeded NULL-`live_url` article → one `null_url_orphan` with correct `article_id`.
- Happy: history-published URL with no article → `history_orphan`; article with no history match → `article_orphan`.
- Edge (the host bug guard): two URLs on the same host that are genuinely different pages do not collapse; bare-host values never used as a join key.
- Edge (fan-out): one history record with 2 distinct published URLs → 2 matched articles, **no** spurious finding.
- Edge (draft URL): history `article_urls = [published, draft]` where only `published` has an article → **no** `history_orphan` for the draft.
- Edge (canonicalization): `utm_*`/trailing-slash-only difference between sides → **no** finding.
- Edge (deferred-R2 guard): a duplicate canonical URL where one article was UNIQUE-dropped → tagged `deferred_r2`, not `history_orphan` R3.
- Edge: clean store → empty record list.
- Integration (R10): `transient` flag set → affected records `authority: possibly-transient`.

**Verification:** Each planted R1/R3 case yields the correct class/authority/keys; canonicalization-equivalent and draft/fan-out cases produce no false finding; deferred-R2 not mislabeled.

- [x] **Unit 3: `audit-state` CLI verb — reporting (R6/R7/R8/R9/R12)**

**Goal:** Thin CLI shell: parse args, run readers + diff, emit JSONL stdout + stderr summary with remediation, exit per R9.

**Requirements:** R6, R7, R8, R9, R12.

**Dependencies:** Units 1–2.

**Files:**
- Create: `src/backlink_publisher/cli/audit_state.py`
- Modify: `pyproject.toml` (`[project.scripts]`: `audit-state = "backlink_publisher.cli.audit_state:main"`)
- Modify: `tests/test_cli_python_m_entrypoints.py` (add `audit-state`)
- Test: `tests/test_cli_audit_state.py`

**Approach:**
- `def main(argv=None)`, lazy `import argparse`, `prog="audit-state"`. Closed-set flags validated post-parse → `from backlink_publisher._util.errors import UsageError, DependencyError, handle_error`; wrap the body so a raised `PipelineError` routes through `handle_error` (UsageError→1, DependencyError→3). Never `argparse choices=`.
- `config_echo.emit_banner(load_config(), "audit-state")` to stderr (missing config tolerated).
- Run Unit-1 readers; on "nothing to audit" sentinel → stderr note, exit 0. On operational error → `DependencyError` (exit 3). Else run Unit-2 diff; `write_jsonl((r.to_jsonl_dict() for r in records), sys.stdout)`.
- **R7 stderr summary:** read `source_tier` per record; print high-signal class counts and informational class counts in separate blocks.
- **R12:** per-class remediation lines — `null_url_orphan → "article row has no URL; re-run publish for this target or verify manually"`; `history_orphan → "published URL not found in events.db; re-run publish to refresh, or verify the link is live"`; `article_orphan → "events.db has a link absent from history; verify the live URL on the web."`
- **R9:** normal completion returns (exit 0) regardless of findings; absent store → exit 0 note; unopenable → exit 3.
- `if __name__ == "__main__": main()`.

**Patterns to follow:** `cli/equity_ledger.py` (shell, banner, `write_jsonl`); `cli/report_anchors.py` (stderr summary idiom); `_util/errors.handle_error`.

**Test scenarios:** *(template: `tests/test_cli_equity_ledger.py` `_run` harness + per-test fresh dirs)*
- Happy: seeded orphan + drift → stdout lines each `json.loads` to a valid record; exit 0.
- Happy: clean store → empty stdout, stderr "no divergence"; exit 0.
- R9: fresh sandbox, no events.db and no history → stderr "nothing to audit"; exit 0 (NOT an error).
- R9: events.db present but unopenable → exit 3, clear stderr message.
- R7: stderr shows correct high-signal vs informational counts separately.
- R12: each reported class's remediation hint appears in stderr.
- Edge: bad `--format` value → exit 1 (UsageError), not 2.
- Integration: `python -m backlink_publisher.cli.audit_state` runs (main-guard smoke).

**Verification:** `audit-state` prints valid JSONL + legible summary; exit 0 with findings or empty store; bad flag → 1; unopenable store → 3.

- [ ] **Unit 4a: Conditional R11 nudge from `publish-backlinks`** *(DEFERRED — publish_backlinks.py has a large in-flight concurrent refactor; build after it lands to avoid merge collision)*

**Goal:** A fail-safe, **conditional** one-line stderr nudge pointing the operator at `audit-state` only when the run produced divergence-precursor rows.

**Requirements:** R11.

**Dependencies:** Unit 3.

**Files:**
- Modify: `src/backlink_publisher/cli/publish_backlinks.py` (after `project_run_safe`, ~line 408)
- Test: `tests/test_publish_backlinks_nudge.py`

**Approach:**
- After `project_run_safe(run_id)` and before the success/failure `SystemExit` branches, check the just-finished run's row statuses; **only if** any are `*_unverified` / `failed_partial` / `published`-with-empty-`article_urls`, emit one stderr line (e.g. `"note: this run had unverified/partial results; run \`audit-state\` to check store consistency."`). Fail-safe (wrapped, never raises, never changes stdout/exit code) — mirror the `project_run_safe` "never affects exit code" contract. Stay within the 440 SLOC ceiling (~401 now).

**Execution note:** Add a test asserting publish-backlinks' stdout (JSONL) and exit code are unchanged with vs without the nudge.

**Patterns to follow:** existing `print(..., file=sys.stderr)` diagnostics; the fail-safe wrapping around `project_run_safe`.

**Test scenarios:**
- Happy: a run with an `*_unverified`/`failed_partial`/empty-URL row → nudge appears on stderr.
- Edge (no alarm fatigue): a fully-clean run → **no** nudge line.
- Integration: stdout (JSONL) and exit code identical with vs without the nudge.
- Error path: if nudge construction raises, it is swallowed; run exit code unaffected.

**Verification:** Nudge appears only after runs with precursor rows; clean runs are silent; publish-backlinks stdout/exit codes unchanged.

- [ ] **Unit 4b (optional, may defer to follow-up): Health Dashboard hint**

**Goal:** Surface a divergence hint in the Health Dashboard (only if it fits without risking the route's "never 500" fallback).

**Requirements:** R11 (secondary surface).

**Dependencies:** Unit 3.

**Files:**
- Modify: `webui_app/routes/health.py` (thread an `audit_hint` through `_build()`/`_render` inside the existing `try`)
- Modify: `webui_app/templates/health.html`
- Test: extend `tests/` health route coverage

**Approach:** Compute the hint inside the existing `try` (degrade to no-hint on any error; never raise). **This unit is explicitly optional** — ship only if it lands cleanly; otherwise defer to a follow-up PR. Not required for v1.

**Test scenarios:** *Test expectation: route renders with and without the hint; the "never 500" fallback still holds when hint computation fails.*

**Verification:** Dashboard renders the hint when present and never 500s when hint computation fails.

## System-Wide Impact

- **Interaction graph:** New isolated `audit/` package + `cli/audit_state.py`. Reads use a **throwaway snapshot copy** of `events.db`+`-wal` (opened `mode=ro`), never the live store — the core safety invariant letting the auditor run during concurrent publishes (R10). The only edits to existing hot paths are the conditional fail-safe stderr nudge in `publish_backlinks.py` and (optionally) the health route.
- **Error propagation:** absent stores → exit 0; unopenable → `DependencyError` (exit 3); bad flags → `UsageError` (exit 1); findings never change exit code (R9). The nudge is fail-safe and cannot affect publish exit codes.
- **State lifecycle risks:** no writes, no schema migration, no WAL checkpoint, no sidecar creation/chmod on the **real** `events.db`; the copy carries `-wal` for freshness. Torn-snapshot bounded by hash-bracketing + `quick_check` + `possibly-transient` labeling.
- **API surface parity:** adds a `[project.scripts]` entry + a `python -m` entrypoint (registered together). Intentionally **not** added to `cli_runner._CLI_MODULES` (CLI/python-m only in v1).
- **Unchanged invariants:** events.db schema, projector, reconcile, and `EventStore` are untouched; the auditor reads only stable `articles` columns and never writes. R2/R4 deliberately absent pending #235/#237; R3 actively avoids re-detecting the deferred R2 UNIQUE-collision class.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `EventStore` connect path mutates the file (schema-upgrade-commit + sidecar chmod) | Bypass `EventStore`; snapshot-copy + open the copy `mode=ro`; assert the **real** store byte-identical via sha256 |
| `immutable=1` reads stale main-file data (misses uncheckpointed `-wal` rows) | Do not use `immutable=1`; copy `-wal` so the copy carries fresh committed rows; freshness test holds a writer conn open + `immutable=1` negative control |
| Torn snapshot (sequential copy of a live WAL DB) → confident false finding | Hash-bracket the copy (pre-first-byte / post-copy) + `PRAGMA quick_check` + re-copy once; label `possibly-transient`; documented fallback = `VACUUM INTO`/backup API |
| `is_same_host` rejects bare `articles.host` netloc → broken R3 grouping | Join on canonical URL, never on host (the `ledger/sources.py` pattern) |
| `article_urls` list fan-out / draft URL → false count/mismatch | Set-membership canonical-URL diff (fan-out free); exclude the draft URL from the published set |
| R3 re-detecting the deferred R2 UNIQUE-collision class | Tag duplicate-canonical UNIQUE-drop signatures as `deferred_r2`, not R3 |
| Missing events.db on a fresh operator misreported as failure | Distinguish absent (exit 0 "nothing to audit") from unopenable (exit 3) |
| Unconditional nudge → alarm fatigue | Nudge fires only on runs with `*_unverified`/`failed_partial`/empty-URL precursor rows |
| `mode=ro` on a copy lacking `-shm` needs a writable dir | Use `tempfile.mkdtemp()` (writable); copy `db`+`-wal`, let SQLite rebuild `-shm` |
| #235/#237 bump schema → `mode=ro` open of newer schema | Read only stable columns; unopenable/too-new → exit 3, never migrate |
| Operational exit code drifts from the 0–6 contract | Map to `DependencyError` (exit 3); pin the exact code in the Unit 3 error-path test |

## Documentation / Operational Notes

- Add `audit-state` to the CLI/AGENTS.md entrypoint table and pipeline doc once it lands (note R2/R4 pending #235/#237; CLI/python-m only).
- Operator runbook: "run `audit-state` when the post-publish nudge appears (or any time) to check store consistency." Findings are point-in-time and `authority: indeterminate` (the live web is the only ground truth).
- Snapshot copies of `events.db` land in a temp dir — ensure cleanup on crash; the copy carries publish payloads, so the temp dir should be 0700 (mirror the store's 0600 sidecar posture).

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-26-dual-state-divergence-auditor-requirements.md](docs/brainstorms/2026-05-26-dual-state-divergence-auditor-requirements.md)
- Related code: `cli/equity_ledger.py`, `cli/report_anchors.py`, `ledger/sources.py` (canonical-URL join + `_canon`), `_util/url.py` (`canonicalize_url`), `_util/jsonl.py`, `_util/errors.py` (`UsageError`/`DependencyError`/`handle_error`), `events/schema.py` (articles), `events/store.py` (WAL/connect), `events/projector.py` (reducer fan-out / NULL-url rows), `webui_app/helpers/history.py`, `webui_app/helpers/cli_runner.py` (`_CLI_MODULES`), `cli/publish_backlinks.py:402-446`, `tests/test_cli_equity_ledger.py`
- Related PRs/issues: #235, #237 (events kind-contract — gate R2/R4)
- Institutional learnings: `publish-history-helper-invariant-2026-05-20`, `tests-coupled-to-operator-config-state-2026-05-18`, `argparse-choices-vs-usage-error-exit-clash-2026-05-20`, `python-m-needs-main-module-after-package-split-2026-05-19`
