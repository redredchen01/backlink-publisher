---
date: 2026-05-26
topic: round8-fresh-pass
focus: open-ended
---

# Ideation: Round 8 Fresh Pass

## Codebase Context

### Project shape
Python 3.11/3.12 CLI tool (`backlink-publisher`) for SEO backlink automation. Six-stage JSONL pipeline (`plan → validate → publish-backlinks`, plus `report-anchors` / `footprint` / `phase0-seal`); stdout=data, stderr=diag, exit 0. 13 console scripts total, incl. `equity-ledger`, `plan-check`, and 4 binding CLIs (`bind-channel`, `velog-login`, `medium-login`, `frw-login`). Flask WebUI: **22 route blueprints** under `webui_app/routes/` + 7 `webui_store/` modules. Adapter registry: `register("x", Adapter, dofollow=..., **MANIFEST)`; **28 platforms**; CLI/schema/throttle/UI read the registry dynamically. Solo-operator tool, heavily developed by parallel AI agents across 20+ `bp-*/` worktrees sharing one `.git`.

### Shipped since round 7 (2026-05-25 → 05-26) — DONE, not candidates
- **Equity Ledger** (PR #221), **Publishing Health Dashboard** (PR #226), **events projector correctness fix** (PR #222), **token-drift exit-code fix** (#223), **Blogger OAuth callback security** (#225/#228/#229), **`fetch().json()` guards** (#232/#233/#234), **secret-file 0600 contract** (#230).
- In flight: **events.db kind-contract + reconcile hardening** (open PRs #235 / #237) — events.db authority is under active change *right now*.

### Grounding verified against source during critique
- **`footprint.py` fingerprints byte-level `<a>` attribute dimensions across the WHOLE corpus** — zero `target_host`/netloc partitioning. (`target_value` = the `<a target="_blank">` attr, not the linked-to host.)
- **`articles.live_url` is `UNIQUE`** (`events/schema.py:58`) and **`reconcile.py` "NEVER raises", degrades to a stale result by design** (`reconcile.py:17`) → legitimate republishes are silently swallowed; NULL-live_url orphans accumulate invisibly.
- **`LINK_KINDS`** (`schema.py:104`: main_domain/target/supporting/extra/category/detail) is projected into `events.kind` with indexes, but the only per-target aggregation that exists (`ledger/aggregate.py`) buckets by **dofollow class, NOT by link-kind** — the URL-hierarchy distribution view is genuinely absent.
- **`scripts/check_imports.py` greps ONE regex** (`from backlink_publisher\.(errors|adapters|content_fetch)`) — 1 import form / 3 module names, vs the 7 documented import forms. The 85% coverage threshold is **not** wired into `ci.yml`. Half-wired self-tooling = false confidence.
- **`_REJECTED_PLATFORMS`** (`registry.py:135`) is a hand-edited `dict[str,str]`. **`quarantine_log`** (`events/schema.py:75`) carries reasons for **corrupt/unparseable JSONL sources — NOT platform-rejection reasons** (killed the "mine quarantine_log to auto-reject platforms" idea on a false premise).
- The **0.30 anchor-language ratio** is a `TODO(ko-corpus-calibration)` in **`anchor/resolver.py:97`**, not in the `anchor/lang.py` gate (which is presence-based). Real, validatable, but mislocated by the raw idea.
- **`publish_leases` dead-PID takeover already ships** in `store.acquire_lease` via `_pid_alive` — only WebUI visibility would be new (and overlaps the shipped dashboard).
- **Plaintext `velog_cookies_flat.txt` + `velog_credentials_dump.json` (0644) at repo root** — live on-disk leak (gitignored but world-readable).
- 3 separate `*-login` CLIs (`velog_login.py`, `medium_login.py`, `frw_login.py`) coexist with the generic `bind-channel`.

### Past learnings consulted (docs/solutions/, ~49 files)
- **Multi-agent / worktree-collision = #1 recurring class (11+ files), ALL prose, zero tooling.** But rounds 1–7 twice rejected worktree tooling (flock, provisioning) as dev-meta; both round-8 critics rejected the claim-registry variant on the same grounds → kept as honorable mention only.
- Plan-grounding drift (6), test-assertions-enshrine-bugs (3), git-plumbing exit-code traps (4), config-persistence data-loss (2), adapter/dofollow gating (3).

### Round exclusion (rounds 1–7 survivors + rejects — disqualified from regeneration)
Equity Ledger, headless cookie-import bind, solutions-as-lint, phantom-success enforcer, IndexNow dispatcher, content near-dup footprint, config write-diff receipt, AdapterCapability manifest, save_config round-trip, bp explain replay, Indexation Oracle, plan invariant test generator, browser-bind recorder, ErrorClass oracle, ThrottleClock, footprint loop closure, config bisect, knowledge embedding bundle, soft-kill STOP, runs cohort, auto-retry, proactive OAuth, checkpoint+resume, bulk URL import, RAG co-authoring, post-publish health monitor, link velocity governor, per-target velocity throttle, mandatory pre-publish gate, dofollow adapter, anchor entropy alarm, footprint emission audit, silent-drop tripwire, secret redactor, adapter Protocol+conformance, HTTP cassettes, kill validate/auto-publish, anchor-budget simulator, anchor plan optimizer, footprint-aware adapter selection, publish recipe export/import, bulk-seed dedupe, multi-tenant profiles, capability behavior bus, collapse webui_store→events.db, auto-detect adapter capabilities, link-context relevance scorer, pre-publish content quality gate, referral-traffic GA4 importer, link-liveness watchdog/live liveness sentinel, publish lifecycle FSM, single bp dispatcher, exit-code conformance gate, live-by-default tests, boot-time chmod sweep, footprint diff across operators, worktree provisioning, worktree mutation flock, auto-seal phase0, global rate budget, anti-bot circuit breaker, events.db integrity hardening, bp doctor preflight, bp tail, seeds-from-site generator, reverse-backlink audit. PLUS round-7 shipped items above.

## Ranked Ideas

### 1. Dual-State Divergence Auditor
**Description:** A read-only CLI that diffs the two records of the same publishes — `webui_store/*.json` (e.g. `publish-history.json`) vs `events.db articles` — and surfaces: NULL-`live_url` orphans (publish crashed before URL capture), republishes silently rejected by the `articles.live_url` UNIQUE constraint, and count/URL drift between the stores. Emits a reconciliation *report*, not a write.
**Rationale:** Both critics' #1. The failure mode is confirmed in source: `reconcile.py` "NEVER raises" and degrades to a stale result by design, so over months the two stores diverge and the only thing papering over it hides the gap. This tells the operator "you think this published; the DB disagrees" — the trust the publish history is supposed to provide.
**Downsides:** Only a diagnostic until paired with a reconciliation path. **Hard scope rule:** read-only diff while #235/#237 are in flight — it must not write/reconcile.
**Confidence:** 88%
**Complexity:** Low–Medium
**Status:** Explored (brainstorm 2026-05-26)

### 2. Campaign Intent & Burndown
**Description:** A declarative `campaign.toml` per main_domain ("land 30 links to acme.com over 60 days, ≥12 dofollow-high, ≥8 distinct platforms") + a `campaign-status` verb that projects events.db against the declaration: links landed vs quota, dofollow/platform coverage gaps, days remaining, required pace. The natural home for the anchor-supply and platform-coverage gap readouts (folds in rejected M5/M6 as columns).
**Rationale:** The one genuine *product* gap on the list — the tool answers "did this link publish?" but never "am I on pace to finish the campaign?" Equity Ledger scores value-per-target, not progress-vs-intent; grep confirms no campaign/quota/deadline field exists anywhere. This is the leap from publisher to campaign manager, built on primitives already present.
**Downsides:** Risk of a dashboard nobody acts on if the "where next" recommendation isn't sharp. Keep `campaign.toml` declarative + projection read-only (no write-back).
**Confidence:** 80%
**Complexity:** Medium
**Status:** Unexplored

### 3. Seed-Decay Audit
**Description:** Before planning anchors, re-resolve each `seeds.jsonl` target URL through the existing `linkcheck/` machinery and flag decay: 301/302 redirects away from the intended page, changed canonical, or de-indexed/404 targets. "Your seed no longer points where you think" — caught before it poisons the anchor plan + article body + published link.
**Rationale:** The pipeline treats seeds as ground truth, but over a months-long campaign targets rot, and every downstream artifact inherits the stale assumption. Cheap: reuses `linkcheck/verify.py` resolve (which already knows `ACCEPTABLE_CODES={200,301,302}`); distinct from the excluded reverse-backlink audit (that checks *our* links; this checks *the target*).
**Downsides:** Adds a network round-trip per seed at plan time (gate behind a flag / honor the no-fetch-verify env). Redirect ≠ always-bad (some are benign) — report, let the operator judge.
**Confidence:** 82%
**Complexity:** Low–Medium
**Status:** Unexplored

### 4. Per-target Footprint Partitioning
**Description:** Refactor the footprint tool to partition the corpus by `target_host` before fingerprinting, so it answers "does **this** client's backlink set look machine-built?" instead of diluting the signal across all unrelated campaigns. (Drop the "cross-client leak guard" half — that drifts toward the excluded footprint-diff-across-operators / multi-tenant framing.)
**Rationale:** Confirmed in source: `footprint.py` fingerprints byte-level `<a>` attribute dimensions across the *entire* history with zero host partitioning. A search engine evaluates footprint per linked-to domain, so the current whole-corpus view both hides per-client patterns (false safety) and flags cross-client coincidences (false alarms) — it breaks exactly at the multi-target scale the tool targets. Slots into the proven multi-corpus + lex-tie-break footprint architecture as a partitioning key.
**Downsides:** Per-host partitions shrink each corpus → fewer samples per fingerprint, weaker statistics on small campaigns. Needs a min-sample fallback to the global view.
**Confidence:** 78%
**Complexity:** Medium
**Status:** Unexplored

### 5. Target-Page Link-Kind Distribution Map
**Description:** A report that pivots events.db by `target_url` within a main_domain along the **LINK_KIND axis** (main_domain/target/supporting/category/detail) and flags distribution skew — e.g. one inner page hoarding 80% of links while the money page or category pages get nothing — with a "rebalance" list of starved pages.
**Rationale:** `LINK_KINDS` is defined + projected + indexed but the only per-target aggregation (`ledger/aggregate.py`) buckets by dofollow class, never by link-kind — so the URL-hierarchy distribution is a genuinely unaggregated axis. Real campaigns deliberately spread equity across a site's hierarchy; a lopsided profile is also a footprint risk. Pure read-side, no NLP.
**Downsides:** Closest-to-the-line idea: must stay distinct from Equity Ledger (the LINK_KIND axis is the only novel sliver) or it collapses into "add a column to equity-ledger." Could fold into #2 as its distribution dimension.
**Confidence:** 70%
**Complexity:** Low
**Status:** Unexplored

### 6. Collapse the `*-login` CLIs into one bind front door
**Description:** Fold `velog-login` / `medium-login` / `frw-login` behind a single `bind-channel <platform>` (or `bp login <platform>`) front door that reads the registry to dispatch to the right backend. One login surface, one set of flags, one failure-message style — not four divergent entrypoints to memorize.
**Rationale:** Confirmed 3 parallel login CLIs + the generic `bind-channel`. The divergence is the documented source of bind fragility; one front door means one place to fix cookie/storage-state/timeout bugs.
**Downsides:** These diverged for *real* per-platform reasons (velog uses `runVelogLogin()`, medium has its own csrf_client). **Route to existing backends; do NOT merge the backends** — collapsing intentional divergence would regress working logins.
**Confidence:** 66%
**Complexity:** Low–Medium
**Status:** Unexplored

### 7. Guardrail & Doc Self-Verification
**Description:** Make the repo's self-descriptions executable, extending the proven `plan-check` claim-gate mechanism: (a) each quality script declares + proves its true coverage (would catch `check_imports.py` covering 1 form/3 names while claiming to guard imports; would catch the 85% coverage threshold missing from `ci.yml`); (b) countable doc claims (route count, monolith-file count, adapter count) asserted vs reality. Folds in the negative-assertion AST-lint (forbid `assertNotIn`-without-positive-companion) as one sub-rule.
**Rationale:** Half-wired safety tooling is worse than none — it gives every parallel AI agent false confidence. In an agent-developed repo, doc/guardrail honesty compounds across every session that reads CLAUDE.md/AGENTS.md. Same in-scope category as round-7's kept "solutions-as-lint."
**Downsides:** Dev-meta (tooling about the repo, not publishing) — one critic flagged it below product ideas. Claim-extraction needs tagged markers to avoid brittle prose parsing.
**Confidence:** 74%
**Complexity:** Low–Medium
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Negative-Knowledge Learning Loop (mine quarantine_log) | **FALSE PREMISE** — `quarantine_log` holds corrupt-JSONL-source reasons, not platform-rejection reasons; the mineable substrate doesn't exist. |
| 2 | events.db as disposable cache (JSON canonical, rebuild on boot) | Dangerous: inverts events.db authority into the in-flight #235/#237 kind-contract/reconcile work; = excluded "collapse webui_store→events.db" inverted. Wrong idea, wrong week. |
| 3 | Publish-lease visible queue / dead-PID reclaim | Dead-PID takeover **already ships** in `store.acquire_lease` via `_pid_alive`; only WebUI visibility is new and that overlaps the shipped health dashboard. |
| 4 | Anchor Inventory Forecaster | ≈ excluded "anchor-budget simulator"; cheap as a column inside #2's burndown, not a standalone forecaster. |
| 5 | Platform Coverage Planner | Thin standalone (a join of events.db platforms × registry referral_value); earns its keep as #2's coverage-gap section. |
| 6 | Campaign Timeline Pacing Advisor | ≈ excluded "link velocity governor"/"ThrottleClock"; "organic drip curve" is unfalsifiable threshold-tuning theater. Honest part = an #2 burndown line. |
| 7 | Content Angle Diversity Tracker | ≈ excluded content near-dup footprint with a structural-distance metric; NLP/threshold maintenance trap, scores your own output. |
| 8 | Cross-Client Footprint Leak Guard (half of M3) | Drifts toward excluded "footprint diff across operators" / multi-tenant framing; kept only the host-partitioning half (#4). |
| 9 | Session cockpit `bp status` | Self-admitted overlap with shipped Health Dashboard + excluded "bp doctor"/"bp tail"; half dev-meta. |
| 10 | Self-chaining `bp run` (no pipes) | ≈ excluded "single bp dispatcher"; CLAUDE.md calls the per-stage JSONL pipe boundary intentional + inspectable. |
| 11 | Worktree Claim Registry / collision detector | ≈ excluded worktree flock/provisioning; the #1 recurring pain (11 files) but it's the *developer's* pain, not the operator's — out of scope by the round's own rule. Honorable mention. |
| 12 | git_safe helper module | Real duplication (4 scripts re-roll git plumbing) but a refactor, not a feature; dev-meta. Honorable mention. |
| 13 | TOML Write-Path Sentinel | Config writes already centralize through `config/writer.py:save_config`; ≈ excluded save_config round-trip in spirit. Fold as a single AST test, not an "idea." |
| 14 | Repo-root secret auto-quarantine | Real leak (velog 0644 at root) but ≈ round-7 honorable-mention chmod-sweep + shipped #230 secret-storage hardening; demoted to honorable mention (ship detect-and-abort + chmod-in-place, never auto-move). |
| 15 | Anchor-Language Gate Corpus Validator (as framed) | **FALSE PREMISE** — the 0.30 ratio is in `anchor/resolver.py` (TODO ko-corpus-calibration), not the presence-based `lang.py` gate. Reframed → honorable mention (validate the resolver 0.30 ratio against a real corpus, retire the TODO). |
| 16 | Platform-ToS Surface Canary | No shared response-shape capture exists to build on; 28 adapters' worth of brittle golden snapshots = maintenance trap unless scoped to 1–2 high-volume adapters. Honorable mention. |

## Session Log
- 2026-05-26: Selected idea #1 (Dual-State Divergence Auditor) for brainstorm handoff → `ce:brainstorm`.
- 2026-05-26: Round 8 fresh pass — 5 ideation sub-agents (operator-pain / missing-capability / inversion-removal / leverage-compounding / edge-reframe), ~39 raw candidates → 23 unique after dedup + cross-cut synthesis (campaign-intent cluster merged B1+B2; footprint reframe E6+B8; dual-state E3+E7; negative-knowledge E1+A6+C6; guardrail A2+D4+D7+C4+D3). 2 adversarial critics (value+scope / novelty+grounding); critic-2 ran 25 source checks and killed 2 false premises (quarantine_log ≠ platform-rejection data; 0.30 ratio lives in resolver.py not the lang gate) + caught lease-reclaim already shipped. Orchestrator final-scored to 7 survivors. Strong both-critic consensus: #1 Dual-State Auditor, #2 Campaign Intent, #3 Seed-Decay, #4 Footprint Partition. Biggest kills: M11 (collides with in-flight events.db work), the velocity/angle threshold-tuning twins, and the dev-meta cluster (demoted to honorable mentions).
