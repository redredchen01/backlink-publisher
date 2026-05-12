---
date: 2026-05-12
topic: backlink-publisher-improvements
focus: open-ended
---

# Ideation: backlink-publisher Improvements

## Codebase Context

**Project**: Python 3.11+ CLI tool + Flask web UI for publishing SEO backlink articles to Blogger and Medium.

**Architecture**: Three composable CLI commands (`plan-backlinks` → `validate-backlinks` → `publish-backlinks`) pipe JSONL; Flask webui.py wraps them via subprocess. Adapters: Blogger API v3, Medium API + Playwright browser fallback. Config: TOML file at `~/.config/backlink-publisher/config.toml`.

**Current pain points**: no retry logic on API failures; no batch/campaign UX; config requires manual TOML editing; OAuth expiry causes mid-batch failures; no article editing before publish; CLI pipeline has no checkpoint/resume for partial failures; publish-history is a flat JSON file.

**Key leverage points**: CLI is fully composable over JSONL; adapter pattern makes new platforms straightforward; Playwright already installed; `linkcheck.py` and `language_check.py` exist but underused; `_medium_selectors.py` isolates Medium selector drift.

---

## Ranked Ideas (Refined — Stricter Bar, 2026-05-12 second pass)

> **Refinement note:** Two adversarial reviewers (skeptical product critic + engineering pragmatist) re-attacked the original 7 survivors with a higher bar. Consensus: #6 cut, #7 cut/deferred, #3 demoted (hidden complexity), and one new idea (Real-Publish Verification) surfaced that addresses a documented historical failure (fake-publish via prior opencli adapter). Sequencing dependency now explicit: **#1 → #5 → #2** is a hard chain.

**Strict Top 5 (raise-the-bar pass):**

### 1. Auto-Retry with Exponential Backoff
**Description:** Wrap every adapter network call (Blogger API, Medium API, Playwright publish) in a `@retry_transient(max_attempts=3, backoff_base=2)` decorator in `adapters/base.py`. Catches `requests.Timeout`, `ConnectionError`, HTTP 429, and HTTP 5xx. Each retry waits `2^attempt` seconds with ±10% jitter. Only escalates to `ExternalServiceError` after all attempts fail.
**Rationale:** Transient network errors currently abort the entire batch with exit code 4 — already-completed items are written, but remaining items are lost. Retry makes individual article failures self-healing without user intervention. Low burden because the error hierarchy contract is already established.
**Downsides:** Masks real configuration errors if jitter isn't tuned. Need to distinguish retryable (429, timeout) vs. non-retryable (401, 403) errors carefully.
**Confidence:** 92%
**Complexity:** Low
**Status:** Explored — brainstorm started 2026-05-12

---

### 2. Proactive OAuth Pre-Flight Refresh (Badge split out)
**Description:** In `_build_credentials()` (blogger_api.py L17-63), add a pre-flight check: if token expires within 5 minutes, refresh immediately before any API call. **Defer** the webui badge/`/api/token-status` endpoint to V2 — the refresh alone resolves the documented mid-batch 401 bug. Refresh path is ~5 LOC; the badge UI was scope creep.
**Rationale:** This is the **only** survivor with hard production evidence — the 60s `creds.expired` tolerance window has actually caused mid-batch failures. Highest ROI/effort ratio after #1.
**Downsides:** Splitting badge out means users still discover expiry via failures *between batches* (just not *during*). Acceptable tradeoff for V1.
**Confidence:** 92% (refresh path); badge demoted to V2 polish
**Complexity:** Low
**Status:** Unexplored

---

### 3. Real-Publish Verification (NEW — added in raise-the-bar pass)
**Description:** After each adapter returns a `published_url`, perform a `linkcheck.py`-style HTTP GET on that URL, assert HTTP 200, and verify a stable content fingerprint (e.g., title substring + N target-link anchors present) is in the response body. Mismatch → mark the article as `published_unverified` in the result JSONL and exit-code 5; do not mark `done`.
**Rationale:** A prior opencli-based adapter **fake-published** for an extended period — fabricated `https://medium.com/p/{sha256}` URLs while the pipeline reported green. None of the existing 7 survivors prevent regression to that state. This is the single cheapest defense against the most catastrophic failure class in the project's history. Reuses the existing `linkcheck.py` machinery.
**Downsides:** Adds one HTTP round-trip per published article (negligible vs. 60–300s Medium throttle). Verification heuristic needs per-platform tuning (Blogger renders synchronously; Medium may have indexing lag — needs a short retry window for the verifier itself).
**Confidence:** 90%
**Complexity:** Low
**Status:** Explored — brainstorm started 2026-05-12

---

### 4. Checkpoint & Resume for Batch Pipeline (prerequisite for #5)

---

### 4. Checkpoint & Resume for Batch Pipeline
**Description:** `publish-backlinks` writes each payload's `id` to `~/.cache/backlink-publisher/checkpoints/<run_id>.jsonl` as `pending` before processing, then updates to `done` or `failed`. Add `publish-backlinks --resume <run_id>` to skip `done` items and retry only `failed`/`pending`, preserving throttle intervals. In the web UI, a "Resume" banner appears on page load if an unfinished run exists.
**Rationale:** A crash or network failure mid-batch (at article 13/20) currently requires restarting from scratch — risking duplicate publishes on already-completed articles (Blogger has no dedup). **Hard prerequisite for #5 Bulk Input to be safe** — a 50-URL batch hitting an unrecoverable failure at item 13 loses 37 articles' generation cost without checkpointing.
**Downsides:** Checkpoint files accumulate; needs a `--cleanup` flag. Browser session state (generated articles) still needs separate persistence from CLI state. #1 retry alone covers ~80% of transient failures — checkpoint is justified mainly by #5's blast radius.
**Confidence:** 88% (upgraded — dependency role clarified)
**Complexity:** Medium
**Status:** Unexplored

---

### 5. Bulk URL Batch Input (CSV / Paste / Sitemap) — sequenced after #1 + #4
**Description:** Accept `plan-backlinks --from-csv urls.csv` or `--from-sitemap https://example.com/sitemap.xml` to process N target URLs in one invocation. Each URL becomes one JSONL payload flowing through the existing pipeline unchanged. Web UI adds a "paste multiple URLs" text area as a zero-config entry point.
**Rationale:** Without this, the tool is a 1-URL-at-a-time toy — for agencies, this is *the* product. The CLI pipeline is already composable over JSONL streams. **But:** solo, without #1 retry and #4 checkpoint, it is a damage multiplier (one transient 5xx kills the whole batch). Must ship after #1 + #4.
**Downsides:** Sitemap parsing requires `xml.etree` (stdlib). Net-negative engineering if shipped before #1+#4.
**Confidence:** 90%
**Complexity:** Low (mechanically) — high blast radius if mis-sequenced
**Status:** Unexplored

---

## Demoted in Raise-the-Bar Pass

### D1. Named Campaign Profiles (was #3, 88%)
**Why demoted:** Reclassified Medium → **Medium-High**. Schema migration (`[profiles.<name>]` nested TOML), profile-vs-flag precedence rules, "save from completed run" requires webui→config writer plumbing, plus stale `blog_id` validation = second API call path. Leverage scales only with multi-client ICP — agency with 3 clients hits it 3×/week. Defer until bulk-input usage proves the pain. 80% subset (read-only profiles, no "save as" UI) cuts half the work.

### D2. Inline Article Editor Before Publish (was #7, 82%)
**Why demoted:** Speculative pain — not reported. If AI output needs hand-editing every batch, the *generator* is broken. Round-tripping markdown through JSONL is a bug farm. Defer as textarea-only V2 if user demand surfaces; cut CodeMirror entirely.

### D3. Config Init Wizard (was #6, 85%)
**Why cut:** Classic onboarding-polish trap. One-time pain; agencies configure once. Two UX surfaces (CLI + webui `/setup`), interactive OAuth popup, live validation. **Replace with `backlink-publisher config check`** — a read-only validator subcommand. Fraction of the work, captures most abandonment with clearer error messages.

---

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | SQLite full state migration | Covered by targeted checkpoint/history improvements without migration risk |
| 2 | Async Job Queue (Celery/threads) | High architecture burden; loading overlay already addresses immediate UX need |
| 3 | Google Search Console indexing watchdog | GSC scraping violates ToS and is technically fragile |
| 4 | Playwright as platform scouter | Scope creep — this is a different product (link prospecting vs. publishing) |
| 5 | Language-sharded publishing strategy | High complexity, niche audience |
| 6 | Content quality feedback loop | Speculative LLM engineering with long feedback cycles |
| 7 | Pre-publish browser preview | Expensive (full Playwright render per article); niche benefit |
| 8 | Article snapshot export | Low value; manual clipboard copy is sufficient for occasional need |
| 9 | Multi-platform fanout | Premature — Substack/Dev.to/LinkedIn adapters don't exist yet |
| 10 | Plain-English Dry-Run Report Card | Good UX polish but lower priority than filling structural gaps |
| 11 | Publish Scheduler (per-day budget) | Overlaps with Campaign Profiles + Bulk Input; can be added as campaign attribute later |
| 12 | Correlation ID thread-through | Low user-facing value; internal debugging improvement for later |
| 13 | Content recycling via re-generation | Niche power-user scenario; low priority given structural gaps |
| 14 | Quality gate pre-flight scoring | Overlaps with validate-backlinks; marginal improvement |
| 15 | Webhook-triggered publish mode | Interesting leverage but low immediate user demand |
| 16 | Medium CAPTCHA interactive recovery | High complexity Playwright interaction; medium value |
| 17 | SEO risk scoring from history | Speculative; no clear threshold data to calibrate alerts |
| 18 | Post-publish link health monitor | Low burden (linkcheck.py exists) — close call; deferred as V2 polish |

---

## Session Log
- 2026-05-12: Initial open-ended ideation — 38 raw candidates generated (5 agents), 25 unique after dedup, 7 survivors after adversarial filtering
- 2026-05-12: Idea #1 (Auto-Retry with Exponential Backoff) selected for brainstorm
- 2026-05-12: **Raise-the-bar refinement (Phase 3 second pass).** Two adversarial reviewers (product critic + engineering pragmatist) re-attacked the 7 survivors. Result: 7 → 5 strict survivors. Cut: #6 Config Wizard (replaced with `config check` validator). Demoted: #3 Profiles (Medium-High, multi-client ICP only), #7 Inline Editor (speculative, defer as textarea-only V2). **Added:** Real-Publish Verification (defends against documented fake-publish failure class from prior opencli adapter). Sequencing chain made explicit: **#1 Retry → #4 Checkpoint → #5 Bulk Input**; #2 OAuth Pre-flight is independent (badge split to V2); #3 Verification is independent.
- 2026-05-12: Idea #3 (Real-Publish Verification) selected for brainstorm.
