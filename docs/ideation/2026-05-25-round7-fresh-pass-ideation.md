---
date: 2026-05-25
topic: round7-fresh-pass
focus: open-ended
---

# Ideation: Round 7 Fresh Pass

## Codebase Context

### Project shape
Python 3.11/3.12 CLI tool (`backlink-publisher`) for SEO backlink automation. Six CLI entrypoints chain via JSONL (`plan → validate → publish-backlinks`, `report-anchors`, `footprint`, `phase0-seal`). Flask WebUI (`webui_app/`, 21 route modules, `create_app()` factory). Adapters register via one line: `register("x", XAdapter, dofollow=..., referral_value=...)`; CLI/schema read the registry dynamically (`test_r9_extension_readiness.py` forbids editing them). Solo-operator tool; 20+ `bp-*/` worktrees for parallel AI-agent dev share one `.git`.

### Shipped since round 6 (2026-05-20 → 05-25) — these are DONE, not candidates
- **Hashnode + Write.as fully retired** (PR #204); channel manifest complete (CI gate `legacy_platforms()==[]`).
- **dofollow tiering** (PR #206 + Phase 2): registry now carries a `referral_value` capability + a CI nofollow gate; `report-anchors` segments output by dofollow tier; `plan-backlinks` marks tier on payloads.
- **New adapters:** LiveJournal (XML-RPC), txt.fyi (form-POST). Pure-HTTP form-POST helper + `AntiBotChallengeError`.
- **`http.py` shared module** extracted; all adapters migrated. SSRF primitives + `ArticleScopedCollector` + fuzz harness extracted from verifier.
- **Monolith decomposition wave** — 11 files now under SLOC budget (writer.py 618→169, phase0_seal 720→449, etc.).
- **WebUI:** unbound platforms hidden from publish select (#197); settings collapse panel (#199); multi-row publish runs grouped in history; blogger concurrent-token-refresh flock.

### Grounding verified against source during critique
- `config/writer.py` **already** preserves *unknown* sections via `_preserve_unknown_sections`; `_SAVE_CONFIG_KNOWN_ROOTS = {blogger, medium, targets, ghpages, mastodon}`. The documented "5-section data-loss" is a *structured-rewrite-of-known-roots* problem, **not** absence of passthrough — the eventual fix is ~80% built.
- `registry.py:151-156` explicitly states there are 2 parallel capability dicts (`dofollow`, `referral_value`) and "a third would justify migrating all of them to a `RegistryEntry` dataclass" — an in-repo green light for narrow registry consolidation.
- `anchor/metrics.exact_match_ratio` + `anchor/scheduler` (deficit-based picking) already exist; `anchor/preflight.py` is ko-Hangul sanitization, NOT a budget simulator.
- `velog_credentials_dump.json` (0644) + `velog_cookies_flat.txt` sit at repo root, plaintext — gitignored but world-readable on disk. Live leak, not hypothetical.
- `events.db` already holds `publish_leases`, projector views, quarantine_log — and `webui_store/*.json` singletons coexist (real dual state).

### Past learnings consulted (docs/solutions/)
- **Config persistence = #1 recurrence zone** — `save-config-write-paths-bypass-preservation-2026-05-15`, `inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14`, `negative-assertion-locks-in-bug-2026-05-15` (recurred 2× in one week).
- **Phantom success** — `publish-history-helper-invariant-2026-05-20` (`_push_history_per_row` enforces `status=published ⟹ url`, PR #156).
- **Adapter/SEO gating** — `grep-dofollow-map-before-shipping-adapter-2026-05-20` (R9 validates *pattern* not *value*; PR #108→#109 9-min revert).
- **Browser-bind** — `bind-channel-diagnostic-playbook-2026-05-20`, `playwright-framenavigated-orphaned-during-cross-origin-sso-2026-05-19` (real post-login tab is in `ctx.pages`, not the held ref).
- No prior solutions on indexation/crawl mechanics — greenfield there.

### Round exclusion (rounds 1–6 survivors — disqualified from regeneration)
AdapterCapability manifest, save_config round-trip (basic), bp explain causality replay, Indexation Oracle (Google `site:`/GSC poll), plan invariant test generator (`derive-claims-tests.py`), browser-bind black-box recorder, `.config-history` redaction, ErrorClass enum oracle, ThrottleClock, publish lease via events.db, footprint loop closure, config bisect, knowledge embedding bundle, `--strict-recon`, soft-kill STOP, runs cohort, auto-retry, proactive OAuth, checkpoint+resume, bulk URL import, RAG co-authoring, post-publish health monitor, append-only event log, link velocity governor, mandatory pre-publish gate, multi-candidate Medium selectors, dofollow adapter, config safety net, anchor entropy alarm, footprint emission audit, silent-drop tripwire, gate property-test, secret redactor, MEMORY→solutions sediment, operational backbone, SQLite StateStore, adapter Protocol+conformance, HTTP cassettes+snapshots, kill validate/auto-publish, durability backbone, token hardening backbone, osascript sandbox, containerized stub-adapter, onboarding kit, vintage+quota, time-decayed anchor profile, bp doctor preflight, bp tail, live dofollow probe, reverse-backlink audit, seeds-from-site generator, live liveness sentinel, per-target-URL velocity throttle, footprint diff across operators, six-CLI argparse→declarative schema, kill autouse fixtures, strip webui.py console-script.

## Ranked Ideas

### 1. Backlink Equity Ledger
**Description:** A per-target-page scorecard that composes signals the registry now already carries — `referral_value` × dofollow tier — with anchor diversity (`exact_match_ratio`) and current live status read from `events.db`. Output: "target X has 12 live dofollow links across 4 platforms, anchor profile healthy, equity index 0.62; under-served vs target Y." Pure read-side aggregation; no re-execution.
**Rationale:** The dofollow-tiering work just shipped `referral_value` as a capability but it only gates nofollow — its value is unrealized. This is the synthesis layer that cashes it in, turning "what was published" (a flat count) into "what each target actually has working for it" — the decision surface SEO work actually needs (where to add links next). Absorbs the link-graph and lifecycle-scorecard ideas without an enterprise graph engine.
**Downsides:** "Live status" needs a liveness signal; ledger is only as good as the freshest recheck. Risk of becoming a dashboard nobody acts on if the "where next" recommendation isn't sharp.
**Confidence:** 80%
**Complexity:** Medium
**Status:** Explored (brainstorm 2026-05-25)

### 2. Headless cookie-import bind
**Description:** Replace interactive per-channel login with a one-shot import of cookies from the operator's already-logged-in real Chrome profile (extends the proven `chrome_backend --user-data-dir` session-reuse pattern). Bind becomes "select which domains to import," not "log in again in a fragile automated browser."
**Rationale:** Interactive browser-bind is the single most-documented operator pain (CDP traps, SSO tab-survival, Cloudflare workarounds litter the memory). The operator is *already* logged into these sites in their daily browser — the tool forces a redundant, captcha-prone second login. Makes the cross-origin-SSO framenavigated trap largely moot.
**Downsides:** Cookies expire — but expiry is strictly better than the current interactive dance. Cookie extraction from a live Chrome profile has its own brittleness (locked SQLite, profile encryption on macOS).
**Confidence:** 70%
**Complexity:** Medium
**Status:** Unexplored

### 3. Solutions-as-Lint
**Description:** Each `docs/solutions/*.md` learning gains an optional frontmatter `lint:` block declaring a grep-based detector + message. A single CI step iterates all solutions and fails the build with the *learning's own URL* when a regression matches (e.g. "new adapter must grep `_DOFOLLOW_BY_CHANNEL`", "secret JSON must use `atomic_write`").
**Rationale:** The institutional knowledge in `docs/solutions/` is the project's biggest compounding asset but it's inert prose — agents must remember to read it. Self-enforcing learnings mean every captured lesson automatically prevents its own recurrence. Aligns with plan-claims-gate becoming a required check 2026-06-02. Absorbs the "capability blast-radius gate" as one detector instance.
**Downsides:** Grep detectors are coarse — false positives erode trust; needs an allowlist/escape-hatch convention. Only as good as the discipline of authoring `lint:` blocks.
**Confidence:** 80%
**Complexity:** Low-Medium
**Status:** Unexplored

### 4. Phantom-success invariant enforcer
**Description:** Promote `status="published" ⟹ non-empty url` from the `_push_history_per_row` *convention* to a hard invariant enforced at the events.db / history-store write boundary — any record violating it raises before persistence. Add a one-time scan + repair for existing phantom rows.
**Rationale:** Phantom success (published-without-URL) is an already-experienced corruption class (PR #156 "publish false-success fix"). It's guarded today only by one helper; any new code path or contributor bypassing it silently corrupts the publish history the operator trusts for "did this actually link?" Convention-only invariants rot. Cheapest reliability win on the list.
**Downsides:** Must enumerate every write site, not just the helper; a hard raise could break a publish mid-run if a legitimate edge case emits an empty URL (needs a clear error path).
**Confidence:** 90%
**Complexity:** Low
**Status:** Unexplored

### 5. IndexNow + sitemap ping dispatcher
**Description:** A new CLI verb that, after publish, submits freshly-published backlink URLs to IndexNow (Bing/Yandex) and pings any listing sitemaps, recording submission receipts as JSONL chainable into a later check. Batches per-host, respects throttle.
**Rationale:** Published links sit undiscovered for days/weeks; proactively notifying crawlers shortens time-to-index — the actual SEO payoff. Distinct from the excluded Indexation Oracle: this *pushes* (an action) rather than *polls* Google (an observation). Cheap (HTTP POST + receipt via existing `http.py`/SSRF primitives), no perpetual-maintenance API tail.
**Downsides:** IndexNow covers Bing/Yandex, not Google directly (Google ignores it) — value is partial for Google-first SEO. Requires per-host key file hosting for IndexNow verification on owned sites; free-platform hosts can't host the key, limiting which URLs qualify.
**Confidence:** 80%
**Complexity:** Low-Medium
**Status:** Unexplored

### 6. Content near-duplicate footprint detector
**Description:** Extend the footprint tool beyond timing/pattern fingerprints to detect *content* footprints: near-duplicate paragraphs, repeated boilerplate intros/CTAs, and recurring phrase n-grams across the published corpus (shingle/Jaccard). Flags clusters that make a link network trivially detectable.
**Rationale:** Footprint detection today catches timing/pattern fingerprints, but a network of articles reusing the same spun template is the classic footprint that gets whole networks deindexed *together* — the most damaging blind spot in the existing tool. Slots into the proven footprint architecture (multi-corpus + lex tie-break engine + named error classes) as an added corpus.
**Downsides:** Near-dup thresholds need tuning; risk of flagging legitimately templated structural elements (banner, attribution). Corpus grows unbounded — needs a windowing/sampling strategy at scale.
**Confidence:** 70%
**Complexity:** Medium
**Status:** Unexplored

### 7. Config-save write-diff receipt
**Description:** Every WebUI settings save returns a structured receipt comparing on-disk TOML before/after: which keys were written, which were preserved untouched, and which were silently dropped (the known-root rewrite gap). Surfaced as a toast/expandable panel. `_config_io._snapshot_config` already exists to source the diff.
**Rationale:** The silent drop is what makes the `save_config` gap so painful — operators trust the save succeeded and discover loss days later. A visible receipt converts a silent data-loss bug into an honest, diagnosable one *even before* the underlying known-root rewrite fix lands — and is the natural test harness for that fix. Genuinely distinct from the excluded round-trip-closure idea: it surfaces loss, it doesn't fix it.
**Downsides:** A receipt without a fix is "we ate your data, here's the itemized list" — only fully valuable paired with the (already 80%-built) known-root rewrite. Pure diagnostic until then.
**Confidence:** 85%
**Complexity:** Low
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Multi-tenant profiles (`--profile`) | Bold reframe, but speculative agency-SaaS pivot for a verified solo operator; `BACKLINK_PUBLISHER_CONFIG_DIR` already isolates per-site. Same solo constraint that killed "footprint diff across operators" in prior rounds. |
| 2 | Capability-driven behavior bus | Grand "drive throttle/banner/tier/UI from one dict" = creep (throttle/tier already delegate); the grounded narrow `RegistryEntry` dataclass is just the partly-shipped excluded AdapterCapability manifest. |
| 3 | Collapse `webui_store` → events.db projector | Architecturally pure but ROI-negative: massive, high-risk migration of 5 live singletons touching every WebUI route, for a solo tool where dual state isn't actively breaking. |
| 4 | Auto-detect adapter capabilities by probing | Directly contradicted by the repo's own 9-minute-revert lesson (PR #108→#109): the hand-curated nofollow map is a *deliberate* safety gate. Auto-probing would ship the bug. |
| 5 | Link-context relevance scorer | NLP-grade topical scoring of surrounding text = research project with perpetual tuning burden; also scores your own generated output. Over-engineering for solo scale. |
| 6 | Pre-publish content quality gate | Fuzzy thin/keyword-stuffing scoring with threshold-tuning churn; blocks your own LLM output with a second LLM-ish judge. Maintenance trap. |
| 7 | Referral-traffic correlation importer (GA4/Plausible) | External-API auth + join + perpetual API-drift maintenance to confirm what the Equity Ledger (#1) already estimates. Low marginal insight. |
| 8 | Link-liveness watchdog | ≈ excluded "live liveness sentinel" / "post-publish health monitor" verbatim; the nofollow-flip detector is the only novel bit → feeds #1, not standalone. |
| 9 | Publish lifecycle state machine | Real states, but as a formal FSM with transition sweeps it's heavy; the useful subset is #1's ledger reading current state + #5's IndexNow receipts. Build the columns, not the machine. |
| 10 | Plans-as-executable (Verification grep → pytest) | ≈ excluded "plan invariant test generator"; fights the already-chosen plan-claims-gate (claims, not free-text grep). #3 (solutions-as-lint) is the distinct survivor of this pair. |
| 11 | Single `bp` dispatcher binary | Six JSONL-chaining entrypoints are a working, documented Unix-pipe design; one-verb reorg is cosmetic with real breakage risk to scripts/AGENTS.md. |
| 12 | Exit-code conformance gate | CLAUDE.md says the 0–6 table is a *deliberately* unenforced contract; formalizing risks more argparse-2-vs-1 clashes for low solo value. |
| 13 | Invert test default (live-by-default) | ≈ excluded "kill autouse fixtures" inverted; makes 3700 tests hit network/sockets by default — slower, flaky, SSRF-exposed. Actively worse. |
| 14 | Boot-time credential hardening (chmod sweep) | Real on-disk leak (velog 0644 at root), but narrow/near-one-time; `atomic_write` 0o600 already canonical for new writes. Honorable mention — fold a one-time chmod into a startup check rather than a headline idea. |
| 15 | Pre-flight anchor-budget simulator | Cheap and unbuilt (primitives `exact_match_ratio` + scheduler exist), but adjacent to excluded anchor-alarm family; strong honorable mention if anchor-mistake-prevention is prioritized. |
| 16 | Anchor plan optimizer (prescriptive) | `scheduler._pick_anchor_type` already does deficit-based prescriptive picking; the novel "given live profile" part = the simulator (#15) feeding it. |
| 17 | Footprint-aware adapter selection | ≈ excluded "footprint loop closure"; a consumer of #6's signal, not its own generator. |
| 18 | Publish recipe export/import | JSONL stdin/stdout means "freeze a run" ≈ `cat flags \| bp ...`; a named-recipe registry is ceremony over a shell alias. |
| 19 | Bulk-seed dedupe & triage | ≈ excluded "bulk URL import" + "seeds-from-site"; dedup/dead-domain triage is a network-probing creep tail for a hand-curated solo seed list. |
| 20 | Per-channel bind-health badge | Overlaps excluded "post-publish health monitor" + existing `_verify` tiers + `channel_status_store`; dashboard candy. Folds into #2. |
| 21 | Worktree provisioning + audit (`bp workspace`) | Dev-environment meta-tooling, not the product; Makefile + AGENTS.md recipe + per-worktree-venv guidance already cover it. |
| 22 | Worktree mutation fencing (flock on `.git`) | Mitigation has always been process discipline + git primitives; advisory flock risks deadlocking agent worktrees. Dev-meta, not product. |
| 23 | Auto-seal phase0 from events.db | Re-platforming a working, low-frequency Telegraph-specific git-notes routine onto events.db = migration-for-purity, no pain signal. |
| 24 | Global cross-platform rate budget | ≈ excluded "link velocity governor" / "ThrottleClock"; a multi-tenant flood problem a solo operator doesn't have. |
| 25 | Anti-bot circuit breaker + cooldown | ≈ throttle family (excluded ThrottleClock/velocity governor); real failure mode (`AntiBotChallengeError`) but only bites at volume. Honorable mention for a reliability-focused round. |
| 26 | events.db integrity/concurrency hardening | WAL + busy_timeout is a ~5-line PRAGMA worth folding into whichever events.db-writing idea lands first (#1/#4), not a standalone idea. |
| 27 | Cross-origin SSO tab-survival harness | Narrow fix to the interactive-login path #2 is trying to *delete*; contradictory to keep both. Folds into #2. |

## Session Log
- 2026-05-25: Selected idea #1 (Backlink Equity Ledger) for brainstorm handoff → `ce:brainstorm`.
- 2026-05-25: Round 7 fresh pass — 5 ideation sub-agents (operator-pain / missing-capability / inversion-removal / leverage-reframing / edge-reliability), ~39 raw candidates → ~30 unique after dedup + cross-cut synthesis. 2 adversarial critique agents (value+scope / novelty+overlap); critic-1 read source and inverted several premises (config loss is narrower than documented; registry invites RegistryEntry; velog creds verified leaking; preflight.py unrelated). Orchestrator final-scored to 7 survivors. Key collapses: lifecycle cluster 5→1 (Equity Ledger), browser-bind 3→1 (cookie import), registry 3→0 (manifest already partly shipped), config 2→1 (write-diff receipt over excluded round-trip). Multi-tenant cut on solo-operator constraint.
