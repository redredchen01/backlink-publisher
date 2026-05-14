---
date: 2026-05-14
topic: raise-the-bar-improvements
focus: open-ended (stricter bar; orthogonal to already-shipped/in-flight ideas)
---

# Ideation: Backlink-Publisher — Raise-the-Bar Pass (2026-05-14)

This is a **second round** of open-ended ideation, building on `2026-05-12-backlink-publisher-ideation.md`. The 5 survivors from that round (auto-retry, OAuth pre-flight refresh, real-publish verification, checkpoint/resume, work-themed backlinks) are now either shipped or have plans in `docs/plans/`. This round deliberately excludes all of them and looks for the next tier of leverage, with a stricter rubric.

## Codebase Context

- **Project**: Python 3.11+ CLI (`plan-backlinks` → `validate-backlinks` → `publish-backlinks` → `report-anchors`) + Flask web UI (`webui.py`, 1400+ lines, ~200KB monolith at repo root) for publishing SEO backlink articles to Blogger and Medium.
- **Adapters**: Blogger API v3 + Medium API + Medium browser fallback (Playwright). Adapter pattern is the natural extension seam.
- **State today**: TOML config at `~/.config/backlink-publisher/config.toml` (`save_config` has a documented silent data-loss bug). Per-feature sidecar state (checkpoint, anchor scheduler, publish-history) — fragmentation trajectory visible.
- **Test infra**: 37 test files, autouse HTTP mock fixtures, ~999 tests on feat branches.
- **Underused assets**: `linkcheck.py` and `language_check.py` exist but are not on the publish path. `_medium_selectors.py` isolates Medium selector drift.
- **Documentation split**: two `docs/` trees (root + `backlink-publisher/docs/`).
- **Velocity**: 15+ plan documents in the last 2 weeks; recent additions include adapter retry/backoff, OAuth pre-flight refresh, checkpoint/resume, work-themed backlinks, draft-queue scheduled publish, anchor profile scheduler, zh-short scheduler.

### Frame & guardrails for this round

- **Stricter bar.** Reject ideas that are "ceremony" without backlink-yield or operator-outcome impact.
- **Five frames** in divergent ideation: operator pain & friction, inversion/removal/automation, assumption-breaking, leverage/compounding, extreme cases & power-user pressure.
- **Synthesis combos** produced by the orchestrator after merging frame outputs (S1–S3 in the rejection table).
- **Adversarial filter** in two passes: product/SEO realism, then engineering pragmatism. The critic explicitly attacks each candidate before scoring.

## Ranked Ideas

### 1. RAG-Grounded Article Co-Authoring against the Live Target

**Description:** Before generating the host article, fetch the target URL's current content + top-3 SERP competitors. Compose the host article as topical commentary that references the target's actual content, with the anchor placed at a contextually-earned position. Same plan→validate→publish pipeline; the change is upstream of `plan-backlinks`.

**Rationale:** The project's value is ranking lift, not article count. Google SpamBrain explicitly classifies templated backlink networks; articles that demonstrably reference live target content read as editorial, not seeded. This is the single highest expected yield on the candidate list. Builds on existing `linkcheck.py` fetch infrastructure.

**Downsides:** Per-article LLM cost + fetch round-trip (bounded but real). Fetch failures need a fallback path. Prompt design needs iteration. Also: target page can change between plan-time and publish-time — the grounded reference may go stale.

**Confidence:** 78%
**Complexity:** Medium
**Status:** ❌ Killed 2026-05-14 — brainstorm 中发现核心 leverage 全靠运行时 LLM，与项目 "LLM-free 运行时" 硬约束冲突（见 `feedback_no-runtime-llm.md`）。若改为 LLM-free 抽取式 grounding（extractive paraphrase + 模板 rotate），剩余的"反 SpamBrain 模板指纹"价值急剧下降——此时已不优于现有 work-themed 静态模板。废案。

---

### 2. Post-Publish Health Monitor (Decaying Re-Check)

**Description:** Background daemon re-fetches every published URL on a decaying schedule (1h, 6h, 24h, 7d, 30d). Detects: 404 (post deleted), 200 with `noindex` injected (shadowban), Google `site:` query miss (deindexed), content stripped, account-suspension page. Surfaces dead posts in a casualty dashboard with auto-republish candidates.

**Rationale:** Project is open-loop today — published backlinks die silently. You cannot measure backlink *yield* without this layer. Real-publish verification (already in flight) is one-shot at publish time; this is the post-publish persistent monitoring tier. Becomes the data source for any future reactive-publishing or competitor work.

**Downsides:** `site:` query is rate-limited by Google; need careful pacing. Decaying-schedule daemon is a new mechanism distinct from the existing CLI flow. Casualty UX needs design work (which dead URLs should auto-republish, which need human review).

**Confidence:** 88%
**Complexity:** Medium
**Status:** Unexplored

---

### 3. Append-Only Event Log + Per-Run State Directories (minimal scope)

**Description:** Single append-only `publish_events.jsonl` event stream + per-run directory at `~/.local/state/backlink-publisher/runs/<run_id>/` capturing `config.snapshot.toml`, `input.jsonl`, `stderr.jsonl`, and the run's `checkpoint.json`. Each meaningful action (draft_created, publish_attempted, publish_succeeded, publish_failed, verify_ok, oauth_refreshed, anchor_used) appends a typed event record.

**Explicitly OUT of scope for v1:** SQLite indexed views, dashboard UI, `report-*` command family, replay command. Those can be added later when concrete users surface.

**Rationale:** Health monitor (#2) needs persistent outcome storage; today each subsystem owns its own sidecar (checkpoint file, anchor-scheduler state, flat publish-history JSON) and the fragmentation trajectory is visible. Substrate now is cheaper than retroactive consolidation. Append-only also sidesteps the `save_config`-style full-rewrite data-loss bug class.

**Downsides:** Easy to scope-creep into a yak-shave (mitigated by hard "no SQLite/UI in v1" rule). Introducing the `run_id` convention touches every CLI entrypoint. Event schema needs migration story (mitigated by append-only + per-event type tag).

**Confidence:** 85%
**Complexity:** Low-Medium
**Status:** Unexplored

---

### 4. Link Velocity Governor (Sigmoid Ramp per Target Domain)

**Description:** Global throttle constraining backlinks pointing to any target domain along a sigmoid curve (e.g., week 1 ≤ 3 links, week 2 ≤ 8, week 4 ≤ 20, plateau at week 8). The scheduler refuses to enqueue a publish that would breach the current ceiling. Curve parameters per-target (operator chooses risk profile: conservative/balanced/aggressive).

**Rationale:** Sudden link spikes are the #1 trigger for Google manual actions and SpamBrain algorithmic penalties. Anchor scheduler (in flight) handles diversity but not pacing — velocity is an orthogonal dimension. A single backlink network getting penalized nukes months of work; this is the cheapest defensive primitive.

**Downsides:** Must coordinate with anchor scheduler, draft queue, and zh-short scheduler — they all enqueue work that must respect the same ceiling. Curve parameters need empirical tuning; defaults are a guess until production data accumulates.

**Confidence:** 82%
**Complexity:** Medium
**Status:** Unexplored

---

### 5. Mandatory Pre-Publish Gate: linkcheck + language_check

**Description:** Bind the existing-but-underused `linkcheck.py` and `language_check.py` into the publish pipeline as a mandatory pre-flight gate. Target URL must HTTP 200 with consistent canonical; anchor-text language must match article body language. Failure aborts publish with a structured error. Remove all "run after the fact" code paths and documentation; add a narrow `--skip-gate` escape hatch for legitimate edge cases.

**Rationale:** Already-written quality tools that aren't on the critical path effectively don't exist — humans won't remember to run them. Dead-link publishes and language-mismatch anchors are the lowest-bar quality failures and the most embarrassing. Strapping them in is structural, not best-effort.

**Downsides:** "Mandatory" risks breaking existing flows that depend on publishing to URLs that briefly return non-200 (e.g., CDN warm-up). `--skip-gate` softens this but introduces a footgun. May surface latent linkcheck bugs that have hidden until now.

**Confidence:** 92%
**Complexity:** Low
**Status:** Explored — brainstorm started 2026-05-14

---

### 6. Multi-Candidate Medium Playwright Selectors

**Description:** Each Medium selector in `_medium_selectors.py` carries 2–3 candidate selectors plus a one-line semantic description ("publish button in editor toolbar"). The fallback adapter tries candidates in order; full failure emits a structured `BP-SELECTOR-DRIFT` event (consumed by #3). **Deliberately out of scope:** LLM-generated new candidates + auto-PR — too much security and review burden for a solo operator.

**Rationale:** Medium DOM changes every few months and each one is a reactive incident. Cheap multi-candidate fallback covers the high-probability drift cases (renamed class, restructured wrapper) without the cost of an LLM/PR autonomous loop. Pure ROI play.

**Downsides:** Candidate selector maintenance is still manual — multi-candidate raises the ceiling, doesn't eliminate the work. Multiple candidates risk hitting the wrong element (e.g., similar selector matches a draft button instead of publish); needs guard assertions.

**Confidence:** 90%
**Complexity:** Low
**Status:** Unexplored

---

### 7. Add One Dofollow Adapter (Self-Hosted WordPress or Dev.to)

**Description:** Without replacing Blogger or Medium, add one additional adapter targeting a platform that actually passes dofollow authority. Two routes operator can pick by niche: (a) **self-hosted WordPress REST API** — full dofollow control, no platform ToS risk, works for any niche; (b) **Dev.to** — zero-infrastructure dofollow, restricted to tech-themed money pages. **Deliberately out of scope:** formal `AdapterProtocol v2` + plugin entry points + unified throttle service — defer abstracting until a fourth adapter forces it.

**Rationale:** The project's most load-bearing assumption — that publishing to Medium and Blogger yields meaningful authority transfer — is contradicted by years of widely-documented `rel=nofollow` policy on Medium and Blogger's flat-to-declining authority. The infrastructure is sound; the targets are the problem. One additive adapter delivers real backlink yield without disrupting in-flight work.

**Downsides:** Self-hosted WP requires operator to own VPS + domain (out-of-band setup cost). Dev.to only serves tech niches. Choosing one route means committing to an audience segment.

**Confidence:** 80%
**Complexity:** Medium
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason rejected |
|---|------|-----------------|
| S2 | Typed sharded config (full Pydantic rewrite) | Atomic per-section write fixes save_config alone; full rewrite is merge-conflict factory during 15-plan velocity |
| S3 | Adapter ecosystem v2 (contract + plugin + throttle) | Three abstractions to justify one new adapter — ship the adapter directly |
| A1 | `bp doctor` health check | OAuth pre-flight refresh (in flight) covers the highest-frequency check; marginal |
| A4 | Error catalog + `bp explain` | Solo-operator ceremony; permanent maintenance tax per new failure mode |
| A5 | `bp cron` wrapper | 20 lines of shell already does this; locks scheduling semantics into the app |
| A7 | `bp replay <run_id>` | Pure dev ergonomics; Playwright DOM snapshot determinism is a research project |
| B6 | Anchor auto-infer from history | Conflicts with in-flight anchor profile scheduler — revisit after it lands |
| B7 | `bp ship` single-command | Inter-stage inspection is intentional; collapsing hides drift |
| B8 | Routing rules auto-dispatch | Rule DSL surface bigger than the one-click savings |
| C1 | Pivot platforms (drop Medium/Blogger) | Torpedoes PR #9 + half the codebase — folded into #7 as *additive* dofollow adapter |
| C3 | Reactive publishing (GSC-driven) | Depends on tracked-keyword infrastructure that doesn't yet exist |
| C5 | Campaign primitive + tier link-graph | Multi-week build; tiered templated networks are exactly what SpamBrain targets |
| C6 | GSC + referrer anchor harvesting | Overlaps B6 + anchor scheduler in flight |
| C7 | Webui rewrite (HTMX + FastAPI) | MEMORY explicitly warns *sibling page > retrofit*; rewriting daily-use tool during 15-plan velocity is a foot-cannon |
| D3 | vcrpy cassette fixtures | Cassette rot is a new maintenance class; vcrpy + Playwright don't compose cleanly |
| D7 | Living docs (generated README + Mermaid) | Solo operator doesn't read own-tool docs; CI drift checker costs more than it saves |
| E1 | Multi-account rotation pool | Violates platform ToS; subsystem-scale build for hypothetical scale |
| E4 | Distributed multi-VPS coordinator | Solo / near-solo operator — zero current need |
| E5 | Adversarial recovery playbook | Depends on E1 (rejected); scope is tiny without an account pool |
| E6 | Competitor anchor mirror | Scrape is fragile + ToS-grey; output rarely actionable at solo scale |
| E7 | Encrypted snapshot + scheduled restore | `git clone + paste tokens` is the existing DR — ceremony |
| E8 | Headless `--ci` flag | Cron already runs headless; formalizing what works adds little |

## Session Log

- 2026-05-14: Initial raise-the-bar pass — 40 raw candidates across 5 frames (operator pain, inversion/removal, assumption-breaking, leverage, extreme cases) → 28 after dedupe + 3 cross-cutting synthesis combos → 7 survivors after two-pass adversarial filter (product/SEO critic + engineering pragmatist). Builds on `2026-05-12-backlink-publisher-ideation.md`; all 9 already-shipping/in-flight ideas explicitly excluded.
- 2026-05-14: Idea #1 (RAG-Grounded Article Co-Authoring) selected for brainstorm.
- 2026-05-14: **Idea #1 废案** — brainstorm 进行到 "grounding source 选择" 时，用户重申 "运行时不接 LLM" 硬约束（`feedback_no-runtime-llm.md`）。RAG-body 失去主要 leverage，剩余 LLM-free 重构形态不优于现有 work-themed 模板。Mark Killed。本次 ideation 的 LLM-依赖判定有遗漏，应在 Phase 3 critique 时增加 "LLM-free 兼容性" 这一维度。
- 2026-05-14: 重新从 6 个剩余 survivor 里选 #5 (Mandatory linkcheck + language_check pre-publish gate) 进入 brainstorm。
