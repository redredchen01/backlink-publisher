---
date: 2026-05-25
topic: round8-fresh-pass
focus: open-ended
---

# Ideation: Round 8 Fresh Pass

## Codebase Context

### Project shape
Mature (≈B+) Python 3.11/3.12 SEO backlink-automation tool. 7 CLI entrypoints chain via JSONL
(`plan-backlinks → validate-backlinks → publish-backlinks`, plus `report-anchors`, `equity-ledger`,
`footprint`, `phase0-seal`). Flask WebUI (`webui_app/`, ~22 route modules + `create_app()` factory),
state in `webui_store/` (5 JSON singletons) + `events.db` (SQLite, emerging state-of-truth). Adapters
register via one line; ~40 adapters in code but only **7 live** (Medium, Blogger, Telegraph, Velog,
GitHub Pages, LiveJournal, txt.fyi). ~150 src modules / ~32.7k SLOC / ~3300 tests. Dev model: 20+
`bp-*/` git worktrees sharing one `.git`, many concurrent AI agents.

### Verified during grounding
- `events/persona.py` computes a salted `persona_id` (provider+account) on **every event** — but
  nothing aggregates by it.
- `footprint.py` analyses the HTML **we emit** (self-similarity), never the inbound set per target.
- `content/fetch.py` + `content/scraper.py` exist and can fetch/parse HTML — but plan-time content
  is authored from `seed_keywords`/`topic` only; the destination page is never read.
- WebUI scheduler is in-process APScheduler with `_restore_scheduled_jobs()` at launch; quitting the
  launcher silently drops armed jobs.
- Bind credentials persist to per-channel `storage-state.json` (cookies carry `expires`, OAuth tokens
  carry issue-dates) — read only at use-time, never forecast.
- A verified P0 was just fixed where the events projector silently dropped CLI successes;
  `publish.unverified` honesty work + a health dashboard are **in flight now**.

### Past learnings consulted (docs/solutions/)
Config persistence = #1 recurrence zone. Recurring bug class = **"operator told something succeeded
when it didn't"** (PR #156 false-success, velog null-after-retry, projector silent-drop). Adapter
gating: R9 validates *pattern* not *value* (PR #108→#109 9-min revert). Sibling-worktree editable-install
caused a documented false-P0 review incident.

### Round exclusion (rounds 1–7 survivors + backlog — disqualified from regeneration)
AdapterCapability declarative manifest, save_config round-trip / config write-diff receipt, config
bisect/echo/safety-net, bp explain causality replay, Indexation Oracle (site:/GSC poll of OUR links),
IndexNow+sitemap ping dispatcher, plan claims/invariant test generator, browser-bind black-box recorder,
headless cookie-import bind, .config-history redaction, secret redactor, token hardening backbone, ErrorClass
enum oracle, ThrottleClock, publish lease via events.db, footprint loop closure/diff/emission audit,
content near-duplicate footprint detector, knowledge embedding bundle, --strict-recon, soft-kill STOP, runs
cohort, auto-retry, proactive OAuth, OAuth route tests, checkpoint+resume, bulk URL import, RAG co-authoring,
post-publish health monitor / health dashboard (in flight), append-only event log, link velocity governor,
per-target velocity throttle, mandatory pre-publish gate, multi-candidate Medium selectors, new dofollow
adapter, live dofollow probe, anchor entropy alarm, silent-drop tripwire, phantom/false-success invariant
enforcer, WebUI false-success route fixes, fetch().json content-type guard, gate property-test, Solutions-as-Lint,
MEMORY→solutions sediment, operational backbone, SQLite StateStore, adapter Protocol/conformance, adapter
manifest auto-wiring, HTTP cassettes/snapshots, kill validate/auto-publish, durability backbone, osascript
sandbox, containerized stub-adapter, onboarding kit, vintage+quota, time-decayed anchor profile, bp doctor
preflight, bp tail, reverse-backlink audit, seeds-from-site generator, live liveness sentinel,
six-CLI argparse→declarative schema, kill autouse fixtures, strip webui.py console-script, Backlink Equity
Ledger (shipped), exit-code contract test, config dry-run replace, content angle diversifier.

## Ranked Ideas

### 1. Scheduler Durability Probe (armed-jobs reconciliation)
**Description:** The WebUI scheduler is in-process APScheduler with a `_restore_scheduled_jobs()` startup
hook. If the operator closes the double-click launcher between scheduling and run-time, armed posts
silently never fire. Add a read-only reconciliation panel that on every page load diffs `drafts_store`
items with `status=='scheduled'` against APScheduler's live job registry, and flags any scheduled draft
whose `run_date` has passed with no corresponding job (a dropped schedule).
**Rationale:** This is the false-success class — the project's #1 recurring pain — applied to
time-deferred work, which is the highest-trust-cost failure mode (overnight batch, nothing posts, no
signal). Tightly grounded in named real code; small, read-only; directly compounds with the in-flight
honesty/dashboard work.
**Downsides:** Narrow bug-class guard, not a platform. Needs care to define "armed" correctly across
launcher restarts.
**Confidence:** 80%
**Complexity:** Low
**Status:** Unexplored

### 2. Persona-scoped fleet footprint analytics
**Description:** `persona_id` (a salted hash of provider+account) is already computed on every event but
never aggregated. Add a projection/report rolling up per-persona: targets touched, host diversity, anchor
exact-match %, publish cadence — i.e. "does this account look like a footprint to a search engine?"
Reuse the footprint lexical-scoring engine on a per-persona slice.
**Rationale:** SEO penalties land at the account/network level, not per-link, yet the tool has no
account-level lens. The signal is **already paid for** (persona_id on every row) and stable across
rebuilds, so it yields longitudinal per-account risk trends the longer the tool runs. Novel angle, low
cost, no external dependency.
**Downsides:** Depends on events.db read-side being sane (see #3). Value is analytical, not an action.
**Confidence:** 82%
**Complexity:** Low–Medium
**Status:** Unexplored

### 3. events.db backbone hardening (read CLI + event-kind contract)
**Description:** Two compounding moves on the emerging state-of-truth: (a) ship `bp-events-query` — a
small set of named, version-pinned, parameterized read queries emitting JSONL on stdout (e.g.
`targets-never-confirmed`, `host-failure-rate`) so every analytic and AI agent reads one stable contract
instead of reinventing inline joins; (b) promote the scattered event-kind string literals
(`publish.intent/confirmed/unverified/failed`, …) into an `events/kinds.py` registry with per-kind
required-field declarations, plus a CI gate that fails any `append` of an unknown kind or missing field.
**Rationale:** The just-fixed silent-drop projector bug came from exactly this gap. As events.db grows
into the backbone (ledger, dashboard, footprint, ideas #1/#2/#6 all read it), a canonical vocabulary +
a self-describing read CLI de-risks every future consumer. Highest *leverage* of the set.
**Downsides:** Plumbing with no direct operator-visible payoff, so it risks deprioritization. Must avoid
re-implementing the existing reconciliation test.
**Confidence:** 80%
**Complexity:** Medium
**Status:** Explored — handed to ce:brainstorm 2026-05-25

### 4. Money-Site Profiler → seed & anchor grounding
**Description:** Before planning, fetch each `target_url` and extract its on-page reality (title, H1,
canonical, `noindex`, declared language, existing internal anchors); emit a `target-profile` JSONL that
`plan-backlinks` consumes so generated anchors and article angles are grounded in the destination — not
just operator-supplied `seed_keywords`. Derive a per-target anchor-risk hint and flag anchor↔page
*semantic mismatch* (a strong modern spam signal).
**Rationale:** The only **upstream, input-quality** idea in the set — it improves what gets published
rather than reporting after the fact. Today the tool can pour equity at a `noindex` page or anchor-mismatch
a page whose real title moved on, and never warns. Builds on existing fetch/scrape machinery; distinct
from the excluded "seeds-from-site generator" (which *generates* seeds — this *grounds anchors against
the destination*).
**Downsides:** Touches the network (one fetch per target). Anchor-relevance scoring needs an LLM/embedding
call; coverage limited to fetchable pages.
**Confidence:** 78%
**Complexity:** Medium
**Status:** Unexplored

### 5. Bind-Credential Expiry Forecast (offline)
**Description:** Read each bound channel's stored cookie `expires` / OAuth token issue-dates from the
per-channel `storage-state.json` **offline** (zero network, zero Chrome) and surface a green/amber/red
forecast: fresh / expiring <7d / already-expired-but-never-rechecked — so the operator re-binds *before*
a 2am publish fails.
**Rationale:** Browser-bind silent failure is a documented top recurring pain; today a dead session is
discovered only mid-run. Reading expiry off the stored jar gives advance warning with no live probe —
explicitly distinct from the excluded live-liveness/dofollow probes. Cheap, offline, operator-felt.
**Downsides:** Coverage is patchy — not every adapter's stored state exposes a parseable `expires`; amber/red
could be noisy. Scope to channels whose state actually carries expiry.
**Confidence:** 72%
**Complexity:** Low
**Status:** Unexplored

### 6. `bp platforms` Reality Matrix
**Description:** Derive and emit a truth table classifying every registered adapter as LIVE /
BOUND-BUT-UNTESTED / NEVER-BOUND / KNOWN-PAYWALL / RETIRED-CODE-PRESENT, computed from registry state +
on-disk bind credentials + `_nofollow_rationales.py` + last successful publish in events.db. CI
drift-gates the committed snapshot.
**Rationale:** With ~40 adapters in code but only 7 live, and 20+ worktrees, the operator has no single
answer to "what can I actually publish to right now?" — and re-discovers it each session. This is a
*derived, read-only* report, **not** the excluded AdapterCapability declarative manifest (which is
authored). Prevents wasted binds on dead/paywalled platforms.
**Downsides:** Borderline conceptual overlap with the excluded manifest — must stay derived, never
hand-maintained. Gate it as a snapshot test, not a hand-edited file.
**Confidence:** 75%
**Complexity:** Low–Medium
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | GSC ROI Attribution Bridge | High external-API dependency; the headline "correct `referral_value` from realized lift" needs months of clean single-operator attribution data — the loop never closes within a useful horizon. Overlaps excluded Indexation-Oracle GSC plumbing. |
| 2 | Inbound-footprint reframe + disavow self-audit | Disavowing one's own links is a high-blast-radius, hard-to-reverse SEO action the tool can't responsibly ground; full inbound-set enumeration needs the same external data as the GSC idea. Re-slice overlaps excluded reverse-backlink audit. |
| 3 | Continuous campaign agent + exception inbox | Product re-architecture, not a Round-8 idea; presumes an unbuilt trust stack (decay/risk/decision quality) and inverts the current false-success-honesty posture by handing an unsupervised agent the publish trigger. → belongs in `ce:brainstorm` as a north-star. |
| 4 | Kill per-worktree editable-install footgun | Real recurring dev pain, but an auto-resolving `.pth`/`sitecustomize` shim is exactly the import cleverness that can bite 20+ concurrent agents harder than the documented dance. Dev ergonomics, not operator value. Deferred (ship only as opt-in conftest shim if at all). |
| 5 | Link Decay Reconciler + republish triage | Strong value but collides with the **in-flight health dashboard** + shipped equity ledger; risk of building the triage queue twice. Deferred — revisit as a `link.decay` follow-up on top of idea #3's event-kind registry once the dashboard lands. |
| 6 | Publish dry-run diff receipt | Overlaps excluded settings dry-run replace / config write-diff receipt. |
| 7 | Article angle diversifier | Overlaps round-7 content near-duplicate footprint detector. |
| 8 | Drop dofollow obsession / value referral-context fit | Fights the just-shipped dofollow-tiering + CI nofollow gate; contentious direction reversal. |
| 9 | Reverse pipeline `bp republish <url>` | Partially covered by the deferred decay-triage retry path; thin standalone value. |
| 10 | Remove config file (split campaign.toml) | Strong reframe but lands in the heavily-mined config-persistence zone (rounds 1–7). |
| 11 | WIP beacon / stash-handshake protocol / bind broker / self-cleaning worktrees | Agent-coordination meta-tooling; valuable for this repo but dev-infra not product, and overlapping among themselves. |

## Session Log
- 2026-05-25: Round 8 fresh pass — 40 raw candidates generated across 5 frames (operator-pain,
  missing-capability, inversion/automation, assumption-breaking, leverage/compounding), merged+deduped
  to ~11 clustered candidates, 1 adversarial critique pass; **6 survived**. Cross-cutting finding: 6 of 8
  assumption-breaking ideas converged on the same buried premise — *the tool is a self-referential
  publisher with no external outcome feedback loop* (seeds, anchor proportions, referral_value, footprint
  are all internally defined). The boldest north-star (wire one external truth signal) was cut as
  premature/expensive (see rejections #1, #3) but recorded here as the dominant latent direction.
- 2026-05-25: Idea #3 (events.db backbone hardening) selected → handed to ce:brainstorm.
