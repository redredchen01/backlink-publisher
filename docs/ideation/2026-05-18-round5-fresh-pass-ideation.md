---
date: 2026-05-18
topic: round5-fresh-pass
focus: open-ended (fifth pass; explicit exclusion of R1+R2+R3+R4 survivors and ~84 documented rejections)
---

# Ideation: Backlink-Publisher — Round 5 Fresh Pass (2026-05-18)

Fifth round of open-ended ideation. Builds on Rounds 1–4 (`2026-05-12`, two `2026-05-14` docs, `2026-05-18-round4`). This round explicitly **excludes the 26 prior survivors and ~84 prior rejections (~110 explored ideas total)** and pushes for ideas grounded in the substrate that landed between R4 (this morning) and now — specifically: the event store U1/U2 (`events/store.py` + `events/kinds.py` + `events/schemas.py`), the webui_app/ split, and the visible mass-concentration in `cli/plan_backlinks.py` (1573 LOC), `config.py` (1567 LOC), `cli/publish_backlinks.py` (840 LOC), `content_fetch.py` (621 LOC after 5 features stacked in 7 days).

## Codebase Context

### Project shape
- Python 3.11+ local-first CLI pipeline: `plan-backlinks → validate-backlinks → publish-backlinks → footprint → report-anchors`, JSONL-pipeable, cron-safe.
- Two consumer surfaces: CLI entry points + `webui_app/` package (Flask, 9 route modules, JsonStore, APScheduler). Legacy `webui.py` is being replaced; no retrofit allowed.
- Domain core: anchor system, `content_fetch`, `linkcheck`, `language_check`, `footprint`, `checkpoint`, `verify_publish`, `markdown_utils`, `pipeline_logger`. Three Medium adapters (`medium_api`, `medium_brave`, `medium_browser`).
- TOML config + `.config-history/` snapshots; canonical SHA stamped into every artifact's `metadata.config_sha`.
- **New today:** event substrate U1+U2 landed on main — `events/store.py` (SQLite + WAL EventStore, 282 LOC) and `events/kinds.py` (Literal `EventKind` enum + per-kind whitelist + CI gate). v1 projects only `publish.*` and `draft.*`; SIX reserved-but-empty namespaces sit unused: `verify.*`, `plan.*`, `validate.*`, `throttle.*`, `retry.*`, `oauth.*`. No projector / replay / aggregator CLI yet.

### Frame & guardrails for this round
- **Hard constraints baked into every ideation prompt**: no runtime LLM, no `webui.py` retrofit (sibling pattern only — webui_app/ canonical), no `config.py` decomposition (P1 queued — narrow zero-internal-caller extractions allowed), no ToS violation, no footprint emission, solo-operator scale, cron-safe / non-interactive.
- **26 prior survivors + ~84 rejections explicitly excluded.** Every ideation sub-agent received the full exclusion list and was told a regenerated R1–R4 idea is immediately disqualified.
- **Four R5-specific divergent frames**, deliberately distinct from R4's A/B/C/D set:
  - **E: Event Substrate Productization** — make the just-landed event store earn its keep. Six reserved namespaces empty; no projector / replay / aggregator.
  - **F: Mass-Concentration Surgery** — what's hidden inside the 4 biggest files (plan_backlinks 1573, config 1567, publish_backlinks 840, content_fetch 621) without doing the rejected "decompose the monolith" work.
  - **G: CLI ↔ webui Seam** — primitives at the surface boundary that neither owner would propose alone (race conditions, reader/writer contracts, shared timeline).
  - **H: Plan/PR Velocity Defense** — defenses against the bug class introduced by the project's own velocity (30+ plans, 40+ PRs in 14 days; multiple memory-recorded near-misses about HEAD drift, parallel-PR supersession, worktree concurrent switching).
- **Synthesis combos** by orchestrator after dedup: **S3 Plan-doc Discipline Stack** (H3 + H1).
  - S1 Throttle Substrate Bundle (F5 + E2 + G4) and S2 Event Substrate First-Consumers Bundle (F6 + G2 + G6) were both rejected as bundles by the critic — accept components individually where they stand, kill the bundle ceremony.
- **Adversarial filter**: independent skeptic agent attacked the 32-idea merged list on subsumption, rejected-idea-recreation, hard-constraint violation, shell-script-in-disguise, speculative defense, premature abstraction, operator UX foot-gun, and cost-vs-value. Orchestrator re-scored independently.

### Past learnings consulted
- `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` — Maps to R3-#5 + R4-#3.
- `docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md` — Maps to R3-#5 + R4-#3.
- `docs/solutions/ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md` — unmined: "dead-tool reference linter" CI variant (broader than R4-C8 pre-commit); synthetic-page-load latency budget. Not picked up in R5 — speculative without webui-latency incident.
- `docs/solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md` — unmined: OS-matrix CI lane / `macos_sensitive` pytest mark; unmocked wall-clock detector. Not picked up — speculative without a second platform-conditional incident.
- `docs/solutions/best-practices/document-review-catches-runtime-errors-at-plan-time-2026-05-14.md` — drives R5-#7 (plan-doc frontmatter + merge-time HEAD-drift gate). R4-C6 per-doc pre-commit was rejected; **merge-time deterministic gate is in-bounds and drives this round's top discipline play**.
- MEMORY: `feedback_verify_repo_state_before_planning.md`, `feedback_scan_parallel_prs_before_blocker.md`, `feedback_worktree_concurrent_switching.md`, `feedback_force_push_refspec_when_hook_blocks.md`, `feedback_verify_external_commits_before_push.md` — drive the H-frame; only H1+H3 (the merge-time/frontmatter pair) cleared the critic.

## Ranked Ideas

### 1. Single ErrorClass Enum Oracle (`adapters/retry.py`)

**Description:** Collapse three parallel transient-error classifiers — `content_fetch._is_transient` (content_fetch.py:298), `adapters/retry.RETRYABLE_HTTP_STATUSES`, `publish_backlinks._error_class` (publish_backlinks.py:98-103) — into one typed `ErrorClass` enum + classifier function in `adapters/retry.py`. Three call sites import the same oracle.

**Rationale:** Most replicated invariant in the codebase — "what counts as transient" — leaks into retry budgets, soft-404 detection, and publish-stage error tags. A single oracle means tuning retry policy in one place affects content-fetch + adapter retries + publish error reporting consistently. Three callers clears the project's ≥3 abstraction bar without ambiguity. Highest groundedness on the survivor list.

**Downsides:** Behavior parity across 3 sites needs paranoid testing — `_error_class` returns a string `"transient"`, `_is_transient` returns bool, `RETRYABLE_HTTP_STATUSES` is a set. Semantic equivalence under one enum needs care (a regression here is a retry-budget bug that's hard to spot in production).

**Confidence:** 90%
**Complexity:** Low-Medium (~80 LOC + enum + 3 call-sites updated + parity tests)
**Status:** Unexplored

### 2. Persistence `safe_write` Carve from `config.py`

**Description:** Lift `_atomic_write_text` (config.py:1154) and `_snapshot_config` (config.py:1170) into a new module `src/backlink_publisher/persistence/safe_write.py` (~80 LOC). Both are zero-internal-callers within config.py (only `save_config`-family uses them at config.py:1383, 1386, 1513, 1514). Rewire `webui_store/JsonStore` and `events/store.py` (and future Telegraph artifact persistence) to consume the shared helper.

**Rationale:** Carve-out explicitly permitted by the P1 constraint ("zero-internal-callers extraction allowed; no broad refactor"). Two real downstream consumers already waiting — JsonStore in webui_app/ and the just-landed events/store both want atomic write + history snapshot semantics. Smallest possible decomposition without touching config.py's overall shape. The safest-write pattern earned by `feedback_config-save-overwrite-pattern.md` becomes available to every state-writing module.

**Downsides:** Cross-module import adds coupling at a previously-private seam — if the helper's signature changes, three callers move in lockstep. Snapshot rotation policy (rolling N=20) must remain caller-owned, not absorbed into the helper (else config's history policy bleeds into JsonStore).

**Confidence:** 88%
**Complexity:** Low (~80 LOC move + 2 import sites + 1 wiring test)
**Status:** Unexplored

### 3. `ThrottleClock` Class Extraction

**Description:** Replace the inlined Medium-throttle pattern (read `MEDIUM_THROTTLE_MIN/MAX` env, `random.uniform`, `time.sleep`, log line) — currently duplicated in `publish_backlinks.py:249-283` (resume path) and `:591-601` (main path) — with an injectable `ThrottleClock(min_s, max_s).wait_between("Medium")` class. Preserve env-var contract as the default source for backward compatibility.

**Rationale:** Three inlined copies clears the abstraction bar. Makes throttle deterministically testable (inject a fake clock instead of monkey-patching `time.sleep` + `random.uniform` everywhere). Pre-condition for Telegraph Phase 0 / Blogger to reuse the same time-of-day-aware throttling without copy-paste. Also: the ThrottleClock is the natural seam for any future "writable throttle ceiling" surface (rejected G4 as standalone, but the class makes it cheap if demand surfaces).

**Downsides:** Env-var override is the only thing operators currently know — adding a class without preserving the env-var contract breaks documented runbooks. Must keep `MEDIUM_THROTTLE_MIN/MAX` as the default source until a `[throttle]` config section is explicitly added (out of scope here). Risk of "ThrottleClock everywhere" creep — must scope to Medium first, defer Blogger/Telegraph until demand exists.

**Confidence:** 85%
**Complexity:** Low (~60 LOC + 3 call-sites + injection test)
**Status:** Unexplored

### 4. Monolith LOC Ceiling Structural Test

**Description:** New `tests/test_no_monolith_regrowth.py` enforces hard LOC ceilings per top monolith — `plan_backlinks ≤1600`, `config ≤1600`, `publish_backlinks ≤900`, `content_fetch ≤650`, `markdown_utils ≤500`. Companion `.monolith_budget.toml` at repo root documents intentional ceilings + per-file rationale + bumped-by date. Bumping a ceiling requires explicit PR touching the budget file with rationale.

**Rationale:** Mass-concentration is the recurring failure mode in this repo — `content_fetch.py` grew 5 features in 7 days via PRs #20/#25/#26/#27/#28. Without a structural ratchet, every "narrow extraction" idea (including this round's #1/#2/#3) silently gets undone within 2 sprints. This test is the *enforcement* layer that makes durable any decomposition discipline the project ever commits to. Zero runtime cost, solo-operator-friendly (one config file to bump intentionally), cron-safe (test-only).

**Downsides:** Bumping a ceiling on legitimate work adds a one-line PR ritual; over-tight ceilings produce muscle-memory bumping that defeats the point. The 5 chosen ceilings are guesses (current LOC + ~5% headroom) — tuning will lag reality for the first 2-3 PRs. Risk of becoming a "lint that nobody reads the rationale of" — the `.monolith_budget.toml` per-entry rationale field needs to be enforced (non-empty string) or it decays.

**Confidence:** 85%
**Complexity:** Low (~40 LOC test + budget file + one-time backfill of current LOCs)
**Status:** Explored — brainstorm started 2026-05-18

### 5. Single-Writer Publish Lease via `events.db`

**Description:** Both `publish-backlinks` (cron-side) and `webui_app/scheduler._publish_draft_job` (APScheduler-side) acquire a `publish.lease(host, platform)` row via `INSERT OR ABORT` against a UNIQUE index in the just-landed `events.db` before any Medium API call. Lease carries PID, started_at, and TTL (e.g., 600s for Medium throttle worst-case + retries). Lease auto-expires on TTL or on a `publish.confirmed`/`publish.failed` event for the same `(host, platform)` pair. Optional companion: `bp lease ls` / `bp lease clear --force` for stuck-lease recovery.

**Rationale:** Real concurrency hazard with concrete attack surface: `webui_app/scheduler.py:22` `max_workers=1` only protects in-process; cron is out-of-process. An operator hitting "publish draft" in webui while the 02:00 cron job is mid-publish produces back-to-back Medium posts that bypass the 60–300s throttle — which looks like a footprint burst to Google's link-spam team. `events.db` is the only cross-process durable substrate both surfaces touch; using it for the lease is "free" infrastructure that proves the substrate's value as more than a passive log. First real cross-surface consumer of the event store.

**Downsides:** Lease TTL needs to outlive the longest legitimate Medium publish (300s throttle + retries → easily 10min) — too-short TTL causes false collisions, too-long causes operator-visible delay after a crash. Stale-lease recovery policy needs to be unambiguous (TTL expiry vs. explicit `bp lease clear`). Webui must surface "lease held by cron — webui publish queued" rather than silently fail, else operators think the button is broken.

**Confidence:** 85%
**Complexity:** Low-Medium (~100 LOC for lease table + `INSERT OR ABORT` + TTL sweeper + webui surfacing)
**Status:** Unexplored

### 6. OAuth Token Bump-Version Invariant

**Description:** Add `token_rev` integer to `medium-token.json` / `blogger-token.json`. Webui's `save_medium_token` (webui_app/routes/oauth.py:121) increments `token_rev` on every save. CLI re-reads the token file (with rev check) before every Medium API call inside the publish loop — not just at process start. If `token_rev` jumped mid-run, abort the current row with `publish.failed reason=token_revoked` event + exit code 45 (mid-run config-drift-style; pairs with R4-#4 strict-recon).

**Rationale:** Concrete cross-surface staleness bug class that no R1–R4 survivor addresses: `webui_app/routes/oauth.py:118-126` writes a new token while a running cron has loaded the old one into a module-level var — cron silently keeps using the revoked token until it 401s, while the human in webui who "fixed" auth assumes the next cron is fine. R1 OAuth pre-flight refresh is process-start only and doesn't see mid-run rev bumps. ~30 LOC fix for an undocumented but very real seam invariant violation. The exit-code-45 namespace pairs cleanly with R4-#4 (40/42/43/44 already assigned).

**Downsides:** Mid-run abort feels harsh — operator might prefer "finish current row with old token, refuse next row"; the soft-fail semantics deserve a brainstorm. Token-file write race needs care (webui writes while CLI reads → fcntl or write-temp-then-rename pattern; can borrow from the carved `safe_write.py` in #2). Per-iteration file-read adds a `stat()` per publish — negligible vs. 60-300s Medium throttle.

**Confidence:** 80%
**Complexity:** Low (~30 LOC + parity with #2 safe_write + one cross-process test)
**Status:** Unexplored

### 7. Plan-Doc Frontmatter Contract + Merge-Time HEAD-Drift Gate (S3 = H3 + H1)

**Description:** Two-part discipline:

- **H3 (substrate):** Plan docs under `docs/plans/*.md` carry typed YAML frontmatter `claims: {shas: [], symbols: [], paths: [], prs: []}` — every external claim moves out of prose into a machine-parseable block. CI lint rejects PRs touching a plan whose frontmatter is missing or malformed. Existing plans grandfather: gate only enforces on plans with `claims:` present; older prose plans pass through unchecked, with a one-time migration sweep tracked separately.
- **H1 (consumer):** GitHub Action parses each PR's `Plan: docs/plans/<file>.md` reference (in PR body), extracts `claims.shas` / `claims.symbols` / `claims.paths` / `claims.prs`, and re-resolves them against current `main` at merge time. Fails with a diff table when claims diverged: SHAs no longer reachable, symbols renamed/deleted (via tree-sitter or simple grep), paths missing, PR states changed.

**Rationale:** Today's documented incident class: `feedback_verify_repo_state_before_planning.md` (2026-05-18) — plans drafted Tuesday cite a HEAD that has moved 15 commits by Friday's merge; `feedback_scan_parallel_prs_before_blocker.md` records PR #42 near-supersession (memory MEMORY.md). R4-C6 rejected the *per-document pre-commit* variant correctly — authorship-time is too early (the drift accumulates between authorship and merge), and pre-commit is per-file. **Merge-time is deterministic, runs once, and catches drift accumulated across the plan's entire lifetime**, regardless of which commit modified the plan. Frontmatter contract makes the gate tractable; without it the gate has to scrape prose. Only candidate this round whose motivation is a *just-today* incident memory.

**Downsides:** 30+ existing plan docs need backfilled frontmatter — needs the grandfather rule (and operator discipline to backfill during routine plan edits). Plan-doc authors will write `claims:` blocks lazily — likely needs a `ce:plan`/`ce:brainstorm` skill update to emit the block automatically (out of scope here; tracked as follow-up). Symbol resolution at merge time needs language-aware tooling (tree-sitter or `grep -r '^def <symbol>\\|^class <symbol>'`); paths and SHAs are straightforward.

**Confidence:** 80%
**Complexity:** Medium (H3 schema + lint ~120 LOC; H1 GitHub Action ~150 LOC + git resolution; one-time migration sweep separate)
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| E1 | `bp project verify.*` projector | Premature without incident class; R1+R2 already cover producer side; "build it the day a divergence incident lands" |
| E2 | `bp throttle daemon` (event-driven token bucket) | Violates cron-safe constraint (daemon-keep-alive foot-gun); F5 ThrottleClock captures 90% of value without daemon ceremony |
| E3 | `oauth.*` event lineage + TTL projector | R1 OAuth pre-flight refresh solved operator-facing problem; speculative forensics without documented OAuth-incident class |
| E4 | `bp replay --predicate` | Rejected-idea adjacent (R2-A7 `bp replay` was dev-ergonomics bounce); shell-script-in-disguise (sqlite3 one-liner) |
| E5 | `validate.*` contract gate + quarantine table | Premature abstraction layered on premature abstraction — events substrate just landed (U1/U2), no schema-drift incident yet |
| E6 | `plan.*` commitment ledger | Subsumed by R4 strict-recon exit codes + R3-#4 silent-drop tripwire (UUID reconciliation) |
| E7 | `retry.*` causal chain + `bp event why <id>` CLI | `parent_event_id` field is fine to add to schema; the dedicated CLI is shell-disguise (recursive SQL CTE delivers the same view) |
| E8 | `bp event tail --live` + APScheduler bridge | Two unrelated features bundled; live-tail is `tail -f` shell-disguise; APScheduler bridge re-emits state APScheduler already has |
| F1 | Promote SSRF guard to `net/guards.py` | Only 2 callers (`content_fetch.py:113-211` + `work_scraper.py:109`) — below project's ≥3 abstraction bar; revisit when Telegraph Phase 0 adds the 3rd caller |
| F4 | UrlPlan dataclass to fuse `_collect_candidate_urls_for_row` + `_build_links` | Speculative defense — "pure-string mirror drift hazard" documented in comment but never incident-bearing |
| F6 | Convert `content_fetch._STATS`/`_CACHE` globals to event emitters | Premature abstraction layered on premature abstraction; refactor cross-module reset call directly without inventing an event channel |
| F8 | `bp render-paragraph` CLI surfacing `_build_link_density_paragraph` | Shell-disguise (10-line Python REPL invocation already works); niche dev tuning loop decays to zero use after first session |
| G2 | Webui `/timeline` route tailing event store | Nice-to-have UI surface, no urgent forcing function; risks retrofit-of-legacy-webui constraint friction; subsumed by future projector work |
| G4 | `[throttle]` config section + webui slider | Subsumed by F5 alone; standalone adds operator-must-remember override layer ("which env var wins") |
| G5 | `bp ipc publish-now` (intent-emit then cron-fulfill) | Re-creates rejected `bp ship` single-command pattern; introduces async "did it actually fire?" indirection nobody asked for |
| G6 | Webui `config_sha` lens / orphaned-artifact mark | Subsumed by R4-#7 `bp runs cohort` (same data, CLI vs UI surface); add the UI surface as small follow-up if R4-#7 lands first |
| G7 | `tests/seam/` cross-surface invariant suite | Useful but no immediate forcing function; the (d) drafts_store + `publish.confirmed` double-count assertion is captured directly by G1 lease design |
| G8 | Cron-exit-to-human envelope (sticky banner row) | Retrofits legacy webui template (constraint violation risk); events-side half duplicates E6 plan-ledger thinking |
| H2 | PR-stack base-drift refuse-to-merge invariant | `rebase-acknowledged: <sha>` label is operator foot-gun ("must remember"); may fight CI `pr_filter` quirk per `reference_ci_workflow_pr_filter.md` |
| H4 | PR-body required `Plan:` trailer + nightly reverse-index | Trailer is must-remember foot-gun; reverse-index nightly cron job decays to zero use; half-value of H1+H3 at full ceremony cost |
| H5 | Velocity-tripwire scheduled CI annotations on long-lived PRs | Speculative defense at scale; sticky-comment noise trains operators to ignore; threshold tuning will be miserable for small team |
| H6 | `bp wt lock` advisory + pre-checkout/pre-switch git hooks | Real incident class (worktree_concurrent_switching memory) but git-hook install across N worktrees is operator foot-gun; documented `.bp-wt-lock.json` convention without hooks may be revisited |
| H7 | Force-push audit ledger with `+refspec`-aware detection | Memory shows force-push is user-authorized (`feedback_force_push_refspec_when_hook_blocks.md`); speculative defense without an actual lost-commits incident to point to |
| H8 | Two-docs-tree divergence guard + `docs/CANONICAL.toml` | No documented two-docs-tree divergence incident; invents config to solve hypothetical; `dual-write-approved:` trailer is operator UX foot-gun par excellence |
| S1 | Throttle Substrate Bundle (F5 + E2 + G4) | Bundle compounds demote+kill items (E2 violates cron-safe; G4 subsumed by F5); ship F5 alone, revisit substrate on demand |
| S2 | Event Substrate First-Consumers Bundle (F6 + G2 + G6) | All three demoted/killed individually; bundle has nothing left to consolidate |

## Cross-cutting Observations

- **F2 + F3 + F5 cluster as "small surgical extracts from monoliths"** — all three clear the ≥3-caller (or zero-internal-caller carve-out) bar, all three are ~80 LOC PRs. F7 (LOC ceilings) is the *enforcement* layer that makes any of them durable — ship F7 first so the extracts don't get silently undone within 2 sprints.
- **G1 + G3 form a "seam coordination" pair** — G1 is the concurrency primitive (publish lease), G3 is the staleness primitive (token rev). Together they convert "cron and webui collide silently" into "cron and webui coordinate through explicit invariants." G1 also serves as the first real cross-surface consumer of the just-landed events.db, proving the substrate's value beyond passive logging.
- **S3 (H3+H1) is the only round-5 idea with a today-dated incident memory as motivation** (`feedback_verify_repo_state_before_planning.md` recorded 2026-05-18). Highest urgency by recency-of-incident alone.
- **The 3 R5 frames that produced no survivors** (E entirely, plus most of G and H) are signal: the event substrate is too young to productize meaningfully, and most velocity-defense ideas are speculative without a 2nd documented incident class. Revisit E and H in 2-4 weeks once a second incident lands.
- **R1–R4 saturation is now visible.** R5 produced only 7 survivors from 32 raw candidates — a 22% pass rate vs. R4's 31% and R3's 27%. The marginal value of additional fresh-pass rounds is declining; future rounds should be *focused* (specific module, specific incident) rather than open-ended.

## Session Log

- 2026-05-18: Initial Round-5 ideation — 32 raw candidates generated across 4 R5-distinct frames (E event substrate, F mass-concentration surgery, G CLI↔webui seam, H plan/PR velocity defense), 30 unique after dedupe + 3 cross-cutting synthesis combos (S1 throttle, S2 event-first-consumers, S3 plan-doc discipline). Independent skeptic agent attacked the merged 35-item list; orchestrator re-scored independently. **7 survivors** after rubric pass: F2 ErrorClass oracle, F3 safe_write carve, F5 ThrottleClock, F7 monolith LOC ceiling, G1 publish lease, G3 OAuth token rev, S3 plan-doc frontmatter + merge-time HEAD-drift. S1 and S2 bundles rejected — components survive only individually where they cleared the rubric.
- 2026-05-18: Handing off Idea #4 (F7 Monolith LOC Ceiling Structural Test) to `ce:brainstorm` for detailed scoping. Picked as a structural ratchet that must land BEFORE any of F2/F3/F5 surgical extracts so the extracted mass doesn't silently grow back within 2 sprints.
