---
title: WebUI Comprehensive Optimization — Audit Findings & Phased Roadmap
type: refactor
status: active
date: 2026-05-21
deepened: 2026-05-21
---

# WebUI Comprehensive Optimization — Audit Findings & Phased Roadmap

## Overview

A 5-dimension audit of `webui_app/` + `webui_store/` (≈6,800 SLOC Python + 4,648 SLOC Jinja, on `origin/main` HEAD `73d9984`) produced 40+ findings across architecture, code simplicity, security, performance, and UX. This plan **does not** implement everything — it groups findings by severity, ships low-risk "quick win" PRs inline, and recommends 4 dedicated follow-on plans for the larger refactors.

**Audit cutoff:** `origin/main` HEAD `73d9984` (post PR #143/#148/#140/#150/#154). The local worktree `feat/bind-medium-pipeline-repair` is behind main.

## Corrections Applied After Document Review (2026-05-21 deepening pass)

The initial draft of this plan inherited stale-branch citations from the audit agents. Post-review verification against `git show 73d9984:...` confirmed the following corrections — these are NOT findings, they are mistakes already-fixed in this revision:

- **Phantom findings dropped:** F5 (`_DOFOLLOW_BY_CHANNEL` already migrated to `publishing.registry` via Plan 2026-05-20-009 U5), F11 (history cap `_HISTORY_MAX_ITEMS=100` enforced at every write site), F12 (`/ce:dashboard/api/stats` doesn't exist — endpoint is now a 302 redirect), F14 (`dashboard.html` doesn't exist at audit SHA — deleted in PR #132), F25 (CSRF error UX on dashboard.html — file doesn't exist).
- **Plan D (Registry reverse-drive) dropped** — entire motivating premise (F5) is already shipped. Residue (binding_method/status onto adapter base) is small; folded into Plan A.
- **Line citations re-pinned to audit SHA:** F1 `routes/llm.py:11-37` → `:70-115` (`settings_test_llm()`); F7 `routes/oauth.py:64,107` → `:69,116`; Unit 2.1 `index.html:1449` → re-verify at implementation; F18 `helpers.py:24,219,241,289,298` line numbers reflect local branch, not 73d9984 — implementer must re-run `pyflakes` at HEAD.
- **Unit 1 scope expanded:** deleting `routes/llm_diag.py` also requires removing the `refreshLogs()` polling block in `settings.html:719-731` (active `setInterval(refreshLogs, 5000)` calling `/settings/llm-logs`); otherwise every operator sees console errors every 5s. Also: `__init__.py:56` has `# noqa: F401` for the adapter side-effect import — the pyflakes sweep MUST NOT delete it.
- **Unit 2.4 corrected:** `_push_history_per_row(rows, *, ...)` takes a `list[dict]` of CLI publish-result rows, NOT a pre-built entry dict. The inline at `pipeline.py:284` builds a single aggregate entry across N rows; the helper writes N per-row entries. Direct substitution is a behavior change. See revised Unit 2.4 below for the corrected scope decision. Also: `routes/checkpoint.py:55,70` contain the same `_history_store.update(lambda hist: [{...}, *hist][:100])` anti-pattern — Unit 2.4 must decide whether to expand scope or narrow the regression grep.
- **Unit 3.1 SSRF reference corrected:** canonical SSRF gate is `src/backlink_publisher/_util/net_safety.py` (`_check_url_for_ssrf`, `_BLOCKED_NETWORKS`), NOT `content.scraper._safe_get` (that's a fetch wrapper, not the gate) and NOT `linkcheck` (no SSRF policy lives there).
- **F28 documentation corrected:** `services/bind_job.py:85` does `env = os.environ.copy()` then passes `env=env` at line 97 — the env is NOT curated. Deferral rationale changed from "curated env dict, low actual risk" to "operator-owned parent process; acceptable for local-loopback UI."
- **F2/F3 severity reclassified** from P0 to P1 per scope-guardian + adversarial consensus: monoliths are continuously shrinking via the 30-PR/week refactor cadence; P0 should be reserved for correctness/security. F1 (SSRF), F7 (OAuth env), F8 (open redirect) remain P0-equivalent in their own section.
- **F22 (history-row select breaks publish-history invariant) promoted** from P2 to P1 — the invariant is R4 load-bearing; UI-side bypass is not "polish".
- **Unit 2 split** into Unit 2a (perf micro-fixes: 2.1/2.2/2.3) and Unit 2b (invariant consolidation: 2.4) — they have different risk surfaces and the plan's own "ship independently" rationale demands the split.
- **Diagram notation defined:** `─blocks→` means strict sequencing (A must merge before B can start); `─soft-dep→` means the work is easier if A lands first but not required.

## Problem Frame

The WebUI has shipped rapidly across ~30 PRs in the last week. The team is actively refactoring under that cadence (PR #132 deleted publishPanel + dashboard.html; PR #154 dropped dead `content/body.py`; PR #150 dead-imports sweep; PR #136 HIDDEN_FROM_UI; Plan 012 Phase A reduced WebUI surface). The audit confirms that trajectory but surfaces four concrete debts the team hasn't yet absorbed:

1. **Two monoliths still in-flight** — `helpers.py` (1,252 SLOC, 8 unrelated concerns) and `index.html` (2,013 SLOC, 4 tab-panes + inline CSS/JS). Shrinking via incidental refactors but not on a deliberate extraction path. P1, not P0 — the team's velocity is already absorbing this.
2. **Singleton store freeze** — `webui_store` paths are bound at import time; `_refresh_paths()` exists only because PR #87 wiped operator's real publish-history. Subprocess/embedded use is still vulnerable. A simpler `@property` fix may obviate the factory migration.
3. **Per-render disk-I/O cost** — `_settings_context` does ~14 disk reads + lazy imports on every index render; `_render` auto-injects 4–6 store loads even on POST→redirect→GET. Doesn't hurt at current scale (<200 history rows, capped at 100) but is wasteful work.
4. **Security gaps that CSRF guard alone doesn't close** — SSRF in `/settings/test-llm-connection` (F1), OAuth env leakage (F7), open-redirect in profiles (F8), reflected exceptions in flash query strings (F26). These are the real P0-equivalent items in this plan.

## Requirements Trace

- R1. **Quantify and triage** every meaningful WebUI debt item with P0/P1/P2 severity grounded in evidence (file:line, MEMORY learning, or measured/projected impact).
- R2. **Ship low-risk wins now** — pyflakes cleanup, JS micro-fixes, SSRF/OAuth hardening — without coupling them to the larger refactors.
- R3. **Sequence the larger refactors** into independently-shippable follow-on plans, each with its own scope and risk model.
- R4. **Preserve all current shipped fixes** — global CSRF guard (#143), 0o600 secret writes (#140), HIDDEN_FROM_UI (#136), publish-history invariant helper (#87/#97/#156). New work must not regress these.
- R5. **Reduce audit re-cost** — when a finding is acted on (in this plan or a follow-on), the next audit should not re-surface it. Test scenarios + invariant tests are the durable mechanism.

## Scope Boundaries

- **In scope:** WebUI Python (`webui_app/`, `webui_store/`, `webui.py`), templates, static JS/CSS, and WebUI-only test fixtures.
- **Out of scope:** CLI pipeline changes, adapter publishing logic, monolith budget enforcement (handled by `tests/test_no_monolith_regrowth.py`), CI workflow changes.
- **Deferred to follow-on plans:** Anything touching helpers.py >100-line extraction, anything touching index.html >200-line extraction, store-backend migration.

## Trust Boundary (locked 2026-05-21)

**The WebUI is a localhost-only operator tool.** The threat model assumes:
- WebUI binds to `127.0.0.1` and is reached only by the operator's own browser on the same machine.
- `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` is **documented as an unsupported state**. If an operator sets it, they own the security implications (no TLS termination, no auth gate, ephemeral SECRET_KEY → session forgery is possible).
- Security work in this plan (Unit 3) is belt-and-suspenders against post-CSRF-bypass / local-malicious-page scenarios, not against off-loopback attackers.

**Consequences of the loopback-only posture:**
- F27 (ephemeral SECRET_KEY) and F28 (subprocess inherits parent env) remain deferred — acceptable for operator-owned localhost.
- `SESSION_COOKIE_SECURE=True` is currently unconditional (`webui_app/__init__.py:32`) — this should be loopback-conditional. Add as a Unit 3 sub-fix: set `SESSION_COOKIE_SECURE=False` when bound to loopback HTTP, `True` when behind a TLS reverse proxy (env-driven).
- `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` should print a startup WARNING ("unsupported configuration; ephemeral session secret in use") to make the trust-boundary breach obvious to the operator.

## Context & Research

### Relevant Code and Patterns

- `webui_app/__init__.py:96-111` — `_global_csrf_guard` already enforces CSRF on all state-mutating verbs (PR #143). Five routes still call `_check_csrf_or_abort()` inline (`bind.py`, `settings_basic.py`, `sites.py`, `token_paste.py`, `url_verify.py`) — kept after PR #148 dedup; revisit whether intentional.
- `webui_app/__init__.py:53-64` — `inject_platforms` already reverse-drives platform list from `registered_platforms()`. The same pattern should extend to dofollow, binding methods, and status (see Unit 7 of follow-on plan).
- `webui_app/binding_status.py:35-52` — `_DOFOLLOW_BY_CHANNEL` is a static 9-entry dict; not registry-delegated.
- `webui_app/helpers.py:42-45` — `_llm_settings_path()` is the correct pattern (function, not module constant). Reuse for any new config-path helper.
- `webui_app/helpers.py:435-526` — `_push_history_per_row` is the canonical write-invariant helper. `routes/pipeline.py:284` replicates the invariant inline instead of calling it.
- `webui_app/templates/settings.html:108` — `_channel_card_macro.html` is imported and used; not dead.
- `webui_store/__init__.py:65-83` — `_refresh_paths()` exists because of [[pr87-verification-complete]]; tests must call it. Production subprocess use is still vulnerable.

### Institutional Learnings

- `[[publish-history-invariant-helper]]` — `webui_app/` writes to publish-history MUST route through `_push_history_per_row`. Direct `history_store.update(...)` writes are PR-rejected.
- `[[feedback-grep-dofollow-map-before-shipping-adapter]]` — R9 registry pattern validates *presence*, not *value*. PR #108→#109 9-min revert from nofollow platform shipping.
- `[[feedback-webui-store-config-dir-frozen]]` — module-level path constants ignore `BACKLINK_PUBLISHER_CONFIG_DIR`.
- `[[feedback-render-auto-inject-over-per-route]]` — auto-inject pattern (PR #132 Unit 2) is the right default for cross-route context.
- `[[feedback-dead-code-audit-blind-spots]]` — pyflakes/grep miss 5 categories: `as` aliases, pyproject scripts, `__main__`, `mock.patch` targets, dynamic registries.
- `[[feedback-atomic-write-canonical-for-secrets]]` — all secret-storing JSON must go through `safe_write.atomic_write` with 0o600.
- `[[feedback-never-smoke-test-real-save-endpoints]]` — POST to running WebUI with empty form can wipe real config; tests must use isolated `BACKLINK_PUBLISHER_CONFIG_DIR`.
- `[[feedback-grep-before-writing-brainstorm-plan-claims]]` — grep before claiming file/line/context-key existence.

### External References

Not used. All evidence is repo-internal.

## Key Technical Decisions

- **Phased delivery over a single mega-PR.** Three quick-win PRs ship in this plan; five follow-on plans sequence the structural refactors. Rationale: each structural refactor has different risk surfaces (helpers split → invariant preservation; template split → JS context wiring; registry reverse-drive → all-adapter coordination); bundling makes review impossible and a single bug blocks all wins.
- **Bias toward "delete then move", not "move then delete".** Where the agents flagged genuine dead code (`llm_diag.py` placeholder, pyflakes-flagged imports, redundant inline CSRF calls now covered by global guard), delete first. Refactor extractions come after, when the surface is smaller.
- **Verify P0 severity against `origin/main`, not local branches.** The audit pass classified several P0s based on a worktree branched before PR #143 landed. Re-classified to "already-fixed" and dropped. This plan only lists findings verified against `73d9984`.
- **No new abstractions in quick-win PRs.** Quick wins are deletions, single-line bug fixes, and parameter-value tweaks (e.g., poll-interval backoff). All abstraction work (subpackage extraction, ABC introduction, factory migration) lives in follow-on plans where they can be designed properly.

## Open Questions

### Resolved During Planning

- **Are the 5 remaining inline `_check_csrf_or_abort()` calls intentional after PR #148 dedup?** Resolved: PR #148 dedup'd routes covered by the new global guard; the 5 remaining inline calls are belt-and-suspenders for state-mutating routes. Keep; revisit during helpers/security.py extraction in Plan A.
- **`pipeline.py:284` history-write — bug or design?** Re-resolved: this is **not** a near-duplicate of `_push_history_per_row` — the helper writes per-row, the inline writes per-aggregate. Two abstractions, not two implementations. Unit 2b decision-pending between scope expansion (new aggregate helper) and scope narrowing (document divergence).
- **Is `SqliteStore` earning its abstraction?** Resolved: no — single-row JSON blob defeats indexing. Plan C decides between (a) revert to JsonStore, (b) actually index, or (c) `@property`-based lazy path (simpler than factory).
- **F5 / Plan D fate.** Resolved: phantom finding. Migration to `publishing.registry` already shipped via Plan 2026-05-20-009 U5. Plan D dropped from the roadmap.

### Strategic Decisions (resolved 2026-05-21)

The 5 strategic decisions raised by document-review have been resolved by the user. Each is folded into the relevant Unit/Plan section below; collected here for traceability.

1. **Unit 2b — Path A (new aggregate helper).** Add `_push_history_aggregate(entry)` + `_apply_history_cap()` to helpers; migrate `pipeline.py:284` + `checkpoint.py:55,70`; pair with F22 template-side guard at `index.html:1510`. See Unit 2b below for the locked scope.
2. **F1 — Host allowlist (default-deny).** Maintain `_LLM_HOST_ALLOWLIST` covering OpenAI, Anthropic, Ollama, SiliconFlow, DeepSeek, Moonshot (+ docs to extend). `BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST=1` env opt-in. Unit 3.1 expanded with this gate.
3. **F27 — Loopback-only trust boundary.** The WebUI's posture is "localhost-only operator tool". `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` is documented as an unsupported state. F27 (ephemeral SECRET_KEY) and F28 (`os.environ.copy()` in subprocess) stay deferred as acceptable for this posture. `SESSION_COOKIE_SECURE=True` should be re-checked — under HTTP loopback the secure flag prevents cookie return; treat as a separate small fix.
4. **Plan A — Delete-in-same-PR (no shim).** Mirrors PR #124 discipline. Plan A becomes a single migration PR per sub-module: split `helpers/security.py`, update all 14 call sites, delete from `helpers.py`. No transitional re-export layer. Mock-patch-target scan is mandatory pre-step.
5. **Plan C — Factory migration (option b).** Replace 5 singletons + `_refresh_paths()` with a `WebUIStores` registry cached on the Flask app; lazy resolution. 4-5 units. Test fixture migration is part of scope. The `@property` alternative was attractive but the user picked the cleaner boundary.

### Deferred to Implementation

- **Exact extraction boundaries inside `helpers.py`** — Plan A will re-validate against current code state when written.
- **History pagination UX** — only relevant if cap is ever raised above 100; currently unnecessary.
- **Whether to merge `bind_channel.js` and `channel-binding.js`** — depends on whether legacy partial-button UI is being retired. Plan B owner decides.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification.*

```
This plan      ──→  Quick-Win PRs (4 units, ship in 1-3 days)
                       │
                       ├─ Unit 1: Dead-code + pyflakes sweep + settings.html log-console cleanup
                       ├─ Unit 2a: Perf micro-fixes (JS taskMap, lift _platform_slugs, poll backoff)
                       ├─ Unit 2b: pipeline.py history-write invariant consolidation
                       └─ Unit 3: SSRF + OAuth env + open-redirect + flash-msg CRLF hardening

Follow-on plans (recommend writing after Quick-Wins land):
   ┌──────────────────────────────────────────────────────────────┐
   │ Plan A: helpers/ subpackage extraction (5 sub-modules)        │
   │ Plan B: index.html template split (resume Plan 013 + Phase C) │
   │ Plan C: webui_store path-laziness (property> factory)        │
   │ Plan E: UX consistency pass — split into E-perf/E-correctness │
   │         /E-polish per scope-guardian                          │
   └──────────────────────────────────────────────────────────────┘
   (Plan D dropped — F5 premise already shipped at 73d9984)

Dependency edges:
   Plan B ──blocks→ Plan E-polish   (nav/vocab depend on extracted tab partials)
   Plan A ──soft-dep→ Plan C        (helpers/contexts.py uses webui_store; cleaner if A lands first)

Notation:
   ──blocks→   strict sequencing: predecessor must merge before successor can start
   ──soft-dep→ predecessor not required, but successor is materially easier with it
```

## Findings Summary (Audit Output, Prioritized)

The table below merges all 5 audit dimensions, deduplicated, verified against `origin/main`. Severity is conservative: P0 = ship soon (correctness/security/blocker); P1 = scheduled (drift risk, perf at scale, UX friction); P2 = polish (dead code, cosmetic).

| # | Sev | Theme | Finding | Evidence | Action |
|---|---|---|---|---|---|
| F1 | P0 | Security | SSRF via `/settings/test-llm-connection`: raw `endpoint` form value, no host validation, forwards `Bearer api_key` to attacker host | `routes/llm.py:70-115` (`settings_test_llm()`) | Unit 3 |
| F2 | P1 | Code | `helpers.py` 1252 SLOC god module (8 concerns, 14 importing route modules); 202-SLOC `_settings_context` | `helpers.py:940-1141`, 14 routes | Plan A |
| F3 | P1 | Code | `index.html` 2013 SLOC mono-template (4 tab-panes + inline CSS + inline JS) | `index.html` whole-file | Plan B |
| F4 | P1 | Arch | `webui_store` singleton paths frozen at import; subprocess/embedded use vulnerable | `webui_store/__init__.py:52-83` | Plan C |
| ~~F5~~ | — | Arch | ~~`_DOFOLLOW_BY_CHANNEL` hardcoded~~ | **PHANTOM** — already shipped via Plan 2026-05-20-009 U5; `binding_status.py:64` calls `dofollow_status()` from registry | DROPPED |
| F6 | P1 | Code | `pipeline.py:284` writes history with shape divergent from `_push_history_per_row`; `checkpoint.py:55,70` same pattern | `routes/pipeline.py:271-284`, `routes/checkpoint.py:55,70`, `[[publish-history-invariant-helper]]` | Unit 2b |
| F7 | P0 | Sec | `OAUTHLIB_INSECURE_TRANSPORT=1` mutates global `os.environ` in request handlers; never unset | `routes/oauth.py:69,116` | Unit 3 |
| F8 | P0 | Sec | `redirect(request.referrer or '/')` open-redirect in `profiles.py:45` | `routes/profiles.py:45` | Unit 3 |
| F9 | P1 | Perf | `_render` auto-injects 4-6 store loads on every response (incl. POST→redirect→GET) | `helpers.py:1227-1256` | Plan A |
| F10 | P1 | Perf | `_settings_context` does ~14 disk reads + lazy imports per index render | `helpers.py:940-1141` | Plan A |
| ~~F11~~ | — | Perf | ~~`history_store` unbounded; 1K rows → 40MB HTML~~ | **PHANTOM** — `_HISTORY_MAX_ITEMS=100` enforced at every write site (helpers.py:447,505,533; pipeline.py:284; checkpoint.py:63,80) | DROPPED |
| ~~F12~~ | — | Perf | ~~`/ce:dashboard/api/stats` O(history) on every poll~~ | **PHANTOM** — endpoint deleted in PR #132; `routes/dashboard.py` is now a 17-line 302 redirect | DROPPED |
| F13 | P2 | Arch | `SqliteStore` is single-row JSON blob; defeats indexing promise; only 2 of 5 stores use it | `webui_store/sqlite.py:52-71`, `base.py` | Plan C |
| ~~F14~~ | — | UX | ~~`dashboard.html` unreachable from nav~~ | **PHANTOM** — `dashboard.html` doesn't exist at 73d9984 (deleted PR #132); `/ce:dashboard` redirects to `/ce:history?section=in-progress` | DROPPED |
| F15 | P2 | UX | Channel state vocabulary inconsistent across surfaces (navbar / 5 per-channel partials / `_settings_channel_binding`) | `index.html:733`, `settings.html:174,200,234,266,308` | Plan E-polish |
| F16 | P1 | UX | No loading state on bind/publish buttons (long-running, no spinner/disabled) | `_settings_channel_binding.html:174-179`, `index.html:1336,1352` | Plan E-polish (or partial in Unit 2a) |
| F17 | P2 | Code | `routes/llm_diag.py` placeholder + active 5s polling consumer at `settings.html:719-731` | `routes/llm_diag.py:1-20`, `settings.html:719-731` | Unit 1 (template cleanup required) |
| F18 | P2 | Code | Pyflakes-flagged dead imports + unused locals + f-strings without placeholders | Re-run `pyflakes webui_app/ webui_store/` at HEAD to get current line numbers | Unit 1 (with noqa allowlist) |
| F19 | P2 | Perf | JS task map: O(n×m) lookup in batch poll (50×50 ops per 2.5s) | `index.html:~1927` (verify) | Unit 2a |
| F20 | P2 | Perf | Jinja `_platform_slugs` rebuilt per history row in `{% for %}` loop | `index.html:~1449` (verify) | Unit 2a |
| F21 | P2 | Perf | `bind_channel.js` polls every 1s without backoff | `static/js/bind_channel.js:17,127` | Unit 2a |
| F22 | P1 | Correctness | History row `update-status` `<select>` allows operator to set `published` without URL — bypasses publish-history invariant from UI side | `index.html:1510` | Unit 2b (paired with the helper-side guard) |
| F23 | P2 | UX | AI-slop visual signals (gradient navbar, uniform 16px border-radius, hover-lift on every button) | `index.html:13-22,50,99-101` | Plan E-polish |
| F24 | P2 | UX | Structural HTML bug — orphan `<div class="card-body">` in settings.html log section | `settings.html:585-604` | Plan E-polish |
| ~~F25~~ | — | UX | ~~CSRF error UX on dashboard.html~~ | **PHANTOM** — dashboard.html doesn't exist; need to re-evaluate against `/ce:history` page if still relevant | DROPPED (re-file if applicable) |
| F26 | P1 | Sec | Exception messages reflected in 302 `flash_msg=` query strings (CR/LF in `Location:` risk) — 11 reflection sites | `routes/oauth.py:38,58,79,111`, `settings_basic.py:129,144,159,170,179,189`, `token_paste.py:73,106` | Unit 3 (`_safe_flash_redirect` helper) |
| F27 | P2 | Sec | `SECRET_KEY` regenerated per process (UUID4 default); `SESSION_COOKIE_SECURE=True` unconditional contradicts loopback HTTP framing | `webui_app/__init__.py:28,32` | Defer pending trust-boundary decision (see Open Questions) |
| F28 | P2 | Sec | `subprocess.Popen` in `bind_job.py` passes `env=os.environ.copy()` (NOT curated, contrary to F28 original wording) | `services/bind_job.py:85,97` | Defer (operator-owned parent process; acceptable for local-loopback UI) |
| F29 | P2 | UX | Backend `<select>` (Playwright/Chrome/Auto) is choice-paralysis for operator | `_settings_channel_binding.html:120-134` | Plan E-polish |
| F30 | P2 | UX | Settings sticky tab bar may not reach last anchor on short viewports | `settings.html:153,585`, `[[sticky-tab-nav-short-page-click-pin]]` | Plan E-polish |

## Implementation Units (This Plan — Quick Wins Only)

The remaining structural refactors are listed under **Follow-on Plans** below.

---

- [ ] **Unit 1: `llm_diag` end-to-end removal + pyflakes sweep**

**Goal:** Delete `llm_diag.py` placeholder AND its template consumer (active 5s polling), then sweep pyflakes findings while preserving side-effect imports. Net: ~50-60 SLOC removed across 8-10 files + template polling block.

**Requirements:** R2, R5

**Dependencies:** None.

**Files:**
- Delete: `webui_app/routes/llm_diag.py`
- Modify: `webui_app/routes/__init__.py` (remove `llm_diag` blueprint registration)
- Modify: `webui_app/templates/settings.html` (remove `refreshLogs()` function and `setInterval(refreshLogs, 5000)` at lines 719-731; also remove the `llmLogConsole` DOM container the function targets)
- Modify: `webui_app/helpers.py` (apply pyflakes findings from a fresh run at HEAD; line numbers in original draft were from local branch)
- Modify: route modules per fresh `pyflakes` run output
- Add `# noqa: F401` annotation to side-effect imports (NOT delete them): currently `webui_app/__init__.py:56` already has this for `backlink_publisher.publishing.adapters` — verify it survives the sweep. Any newly-flagged side-effect import gets the same treatment.
- Test: `tests/test_webui_route_contract.py` (add assertion `llm_diag` blueprint is no longer registered)
- Test: `tests/test_webui_app_factory_reconcile_wiring.py` (add boot-time assertion `len(registered_platforms()) > 0` after `inject_platforms` — durable R5 guard against the side-effect-import-deletion class of bug)

**Approach:**
- Step 1 — fresh `pyflakes webui_app/ webui_store/`. Classify each finding into:
  - **Delete** (dead import / unused local / unused arg)
  - **Keep with `# noqa: F401`** (side-effect import; registry registration; `__main__` re-export)
  - **Convert to lazy import** (avoids circular dep but pyflakes can't tell)
  - **Fix f-string** (drop `f` prefix when no placeholder)
- Step 2 — delete `llm_diag` blueprint AND its settings.html consumer together. Run a local WebUI and open Settings; verify no console errors and no "LLM 日志" panel.
- Mirror the PR #150/#154 pattern: small focused commits, one PR. The settings.html change is one commit; the pyflakes sweep is another.

**Patterns to follow:**
- PR #150 dead-imports sweep (small-diff convention)
- PR #154 drop dead `content/body.py` (delete-with-callers)
- `[[feedback-dead-code-audit-blind-spots]]` — 5 categories pyflakes misses

**Test scenarios:**
- Happy path: `pytest tests/test_webui_*.py` passes (all 17+ webui test files).
- Happy path: `pyflakes webui_app/ webui_store/` exits 0 OR only emits findings on `# noqa: F401` lines.
- Edge case: `test_inject_platforms_non_empty` (new) — cold app boot, no other adapter import path, `inject_platforms()` returns ≥1 platform. Catches accidental deletion of the side-effect import.
- Edge case: open Settings in dev WebUI; browser console clean (no `GET /settings/llm-logs 404` errors).
- Integration: `test_webui_route_contract.py::test_all_blueprints_registered` updated to exclude `llm_diag` and passes.
- Integration: `python -m py_compile webui_app/**/*.py` succeeds.

**Verification:**
- `grep -rn "llm_diag\|/settings/llm-logs\|refreshLogs\|llmLogConsole" webui_app/ tests/` returns zero matches.
- Cold app boot still populates `inject_platforms` context with ≥1 platform.
- `pyflakes` clean on swept files OR only flags `# noqa`-annotated lines.

---

- [ ] **Unit 2a: Perf micro-fixes (3 single-line wins)**

**Goal:** Ship three low-coupling perf micro-fixes that don't touch invariants. Each lands as one commit; bundled in one PR for review compactness only.

**Requirements:** R2

**Dependencies:** None.

**Files:**
- Modify: `webui_app/templates/index.html` (lift `_platform_slugs` outside `{% for item in history %}` loop; JS taskMap O(n+m) refactor in batch-poll handler — line numbers must be re-verified at HEAD)
- Modify: `webui_app/static/js/bind_channel.js` (poll backoff: 1s → 2s → 5s; add `MAX_CONSECUTIVE_ERRORS` for error-path handling per design-lens finding)

**Approach:**

*2a.1 — Lift `_platform_slugs` outside history `{% for %}`.* One-line move.

*2a.2 — JS taskMap.* Build `const taskMap = Object.fromEntries(tasks.map(t => [t.full_id, t]));` once per poll; lookup is `taskMap[tid]`.

*2a.3 — Poll backoff with error budget.* Replace flat `POLL_INTERVAL_MS = 1000` with `nextInterval = Math.min(prevInterval * 2, 5000)`. On poll error, increment consecutive-error counter; after 3 consecutive failures, stop polling and surface error to UI (per design-lens finding — pure backoff without error-path UX is a regression on F16). Reset counter on any successful response.

**Patterns to follow:**
- `[[feedback-fetch-json-must-guard-content-type]]` — JS fetch hardening

**Test scenarios:**
- Edge case (2a.3): bind_job completing in <1s sees exactly 1 poll; 60s bind sees ≤9 polls (vs 60 today).
- Error path (2a.3): inject 3 consecutive 500 responses — poll loop stops, user-visible error surfaces (e.g., disabled state on bind button + flash).
- Integration: manual smoke — trigger bind via WebUI, observe DevTools network tab; intervals grow; an injected error after 3 retries stops the loop.

**Verification:**
- Manual: bind a channel; DevTools shows poll intervals 1s, 2s, 4s, 5s, 5s…
- Pyflakes/jslint clean (if jslint configured).

---

- [ ] **Unit 2b: pipeline.py history-write invariant consolidation (Path A locked)**

**Goal:** Close the publish-history invariant DRY-gap at `pipeline.py:284` + `checkpoint.py:55,70` + F22 UI-side bypass. Single PR.

**Requirements:** R4, R5

**Dependencies:** None. (Plan A may later move the helpers into `helpers/history.py`; current call sites just reference `webui_app.helpers`.)

**Helper design (locked):** `_push_history_per_row` already exists for per-row writes. Add a sibling for the aggregate shape:

```python
# helpers.py (or future helpers/history.py)
def _apply_history_cap(hist: list[dict]) -> list[dict]:
    return hist[:_HISTORY_MAX_ITEMS]

def _push_history_aggregate(entry: dict) -> list[dict]:
    """Append a single aggregate entry. Caller-built entry.
    Invariant: if entry['status'] in {'published', 'drafted', ...}, then
    entry['article_urls'] must be non-empty. Raises ValueError otherwise."""
    if entry.get('status') in {'published', 'drafted'} and not entry.get('article_urls'):
        raise ValueError("published/drafted entry requires non-empty article_urls")
    return _history_store.update(lambda hist: _apply_history_cap([entry, *hist]))
```

> *Directional guidance — not implementation specification.*

**Files:**
- Modify: `webui_app/helpers.py` — add `_push_history_aggregate(entry)` and `_apply_history_cap(hist)`. `_push_history_per_row` refactored to call `_apply_history_cap` internally instead of literal `[:_HISTORY_MAX_ITEMS]`.
- Modify: `webui_app/routes/pipeline.py:284` — replace inline `history_store.update(lambda hist: [entry, *hist][:100])` with `_push_history_aggregate(entry)`.
- Modify: `webui_app/routes/checkpoint.py:55,70` — same replacement; preserve the `failed_partial` / `stderr_summary` fields in the entry dict.
- Modify: `webui_app/templates/index.html:1510` — F22 UI guard. Add Jinja conditional: `<option value="published" {% if not item.article_urls %}disabled{% endif %}>` (and same for `drafted`).
- Modify: `webui_app/routes/history.py` — the POST handler that accepts the `<select>`-bound status update must also enforce the server-side invariant (defense-in-depth, since the disabled HTML attr is client-side advisory). Use `_push_history_aggregate` for any new write triggered here too.
- Test: `tests/test_webui_pipeline_history_invariant.py` (new)
- Test: `tests/test_webui_checkpoint_history_invariant.py` (new)
- Test: `tests/test_webui_history_route_status_guard.py` (new — server-side invariant on the manual status-update endpoint)

**Patterns to follow:**
- `[[publish-history-invariant-helper]]`
- `_push_history_per_row` existing structure (helpers.py:447+)

**Test scenarios:**
- Happy path: pipeline publish 3 URLs successfully → 1 aggregate history entry with `status='published'` AND `article_urls=[3 URLs]`.
- Edge case: pipeline publishes 0 URLs → entry `status='failed'` AND `error` set; `_push_history_aggregate` does NOT raise.
- Error path: caller constructs `entry={'status':'published','article_urls':[]}` → `_push_history_aggregate` raises `ValueError`. Caller must handle (in practice, pipeline.py's status-collapse logic at lines 271-282 prevents this).
- Edge case (F22 client): operator views row with `article_urls=[]` — `<option value="published">` is disabled in DOM; manual status flip is blocked at the UI layer.
- Edge case (F22 server): forged POST to history status-update endpoint with `status=published` on a urlless row → HTTP 400 with `reason: invariant_violation`.
- Edge case (checkpoint): retry-after-partial-failure path writes `status='failed_partial'` with `stderr_summary` — entry passes through `_push_history_aggregate` (status not in `{published, drafted}` so invariant doesn't gate).
- Integration: `grep -rn "history_store\.update\|_history_store\.update" webui_app/routes/` returns zero matches after migration.

**Verification:**
- `grep -rn "history_store.update" webui_app/routes/` zero matches.
- `pytest tests/test_webui_*_history*.py` passes.
- Manual: open WebUI, find a history row with no URL, confirm "已发布" option is disabled in the select.

---

- [ ] **Unit 3: SSRF + OAuth env + open-redirect + flash-msg CRLF hardening**

**Goal:** Close four security findings that the new `_global_csrf_guard` alone does not address (F1, F7, F8, F26).

**Requirements:** R2, R4

**Dependencies:** None.

**Files:**
- Modify: `webui_app/routes/llm.py` (`settings_test_llm()` at lines 70-115 — endpoint URL validation + response-size cap + content-type check)
- Modify: `webui_app/routes/oauth.py` (lines 69, 116 — scope `OAUTHLIB_INSECURE_TRANSPORT` mutation; assert callback URI is loopback)
- Modify: `webui_app/routes/profiles.py:45` (same-origin redirect)
- Add: `webui_app/helpers.py` (or `_util.py`) — `_safe_flash_redirect(path, msg)` helper that strips `\r\n` and length-bounds `msg` to 200 chars
- Modify: All 11 reflected-flash callsites — `oauth.py:38,58,79,111`, `settings_basic.py:129,144,159,170,179,189`, `token_paste.py:73,106` — to route through the new helper
- Test: `tests/test_webui_llm_endpoint_ssrf.py` (new)
- Test: `tests/test_webui_oauth_env_scope.py` (new)
- Test: `tests/test_webui_flash_redirect_safety.py` (new)

**Approach:**

*3.1 — `/settings/test-llm-connection` SSRF + host allowlist (F1 fully closed).*

**IP-class gate:** Reuse `_check_url_for_ssrf` from `src/backlink_publisher/_util/net_safety.py`. Reject with HTTP 400 + structured `{status: 'failed', reason: 'url_rejected', detail: <code>}` JSON response.

**Loopback:** Default-deny; opt-in via `BACKLINK_PUBLISHER_LLM_ALLOW_LOOPBACK=1` for operators running Ollama on localhost.

**Host allowlist (F1 closed per user decision):** Maintain `_LLM_HOST_ALLOWLIST` covering canonical providers:
```
api.openai.com, api.anthropic.com, api.deepseek.com, api.siliconflow.cn,
api.moonshot.cn, generativelanguage.googleapis.com, api.together.xyz,
api.groq.com, openrouter.ai, localhost (when loopback opt-in set)
```
Endpoint host not in the allowlist → reject with `reason: 'host_not_allowlisted'`. Opt-in via `BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST=1` env var with explicit docstring warning that operator's API key will be transmitted to that host. The allowlist lives in `_util/llm_allowlist.py` (new) so the CLI can reuse it if needed.

**Response handling:** Cap response size (max 64KB streamed read), require `application/json` content-type. Reject non-conforming with structured error.

*3.2 — `OAUTHLIB_INSECURE_TRANSPORT` scope.* Replace `os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'` with a context manager that sets-then-restores around the OAuth flow. Add assertion: `_oauth_callback_uri()` must resolve to a loopback host; refuse to enable insecure transport otherwise. Note: OAuth callback arrives in a SEPARATE request, so the context manager must wrap each handler independently (set during start AND set during callback exchange, restored at each handler exit).

*3.3 — `redirect(request.referrer or '/')` same-origin guard.* Parse `request.referrer`; if scheme/host don't match `request.host_url`, redirect to `/` instead. Helper: `_safe_referrer_redirect(default='/')`.

*3.5 — `SESSION_COOKIE_SECURE` loopback awareness (locked per Trust Boundary).* Change `webui_app/__init__.py:32` from unconditional `True` to env-driven: `False` for loopback HTTP, `True` for off-loopback TLS-proxy deployments. Also: print startup WARNING when `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` is set ("unsupported configuration; ephemeral session secret in use").

*3.4 — Flash-msg CRLF hardening (F26).* New helper `_safe_flash_redirect(path, msg)` that:
1. Strips `\r`, `\n` from `msg`
2. Caps length to 200 chars
3. `urllib.parse.quote`s the value
4. Returns a `redirect(f"{path}?flash_msg=...")` response

Migrate all 11 callsites. The flash render in `settings.html:146` stays as-is (Jinja autoescapes).

**Patterns to follow:**
- `src/backlink_publisher/_util/net_safety.py` — canonical SSRF policy
- `[[feedback-atomic-write-canonical-for-secrets]]` — defensive-by-default

**Test scenarios:**
- Happy path (3.1): POST `endpoint=https://api.openai.com` → request proceeds (or returns connection-level failure as 200 with `status: failed`, not 400).
- Error path (3.1): POST `endpoint=http://169.254.169.254` → HTTP 400 + `reason: url_rejected, detail: metadata_ip`.
- Error path (3.1): POST `endpoint=http://10.0.0.1` → HTTP 400 + `reason: url_rejected, detail: rfc1918`.
- Error path (3.1): POST `endpoint=http://127.0.0.1:6379` → HTTP 400 unless `BACKLINK_PUBLISHER_LLM_ALLOW_LOOPBACK=1` set.
- Error path (3.1): POST `endpoint=file:///etc/passwd` → HTTP 400 + `reason: scheme_rejected`.
- Error path (3.1): POST `endpoint=https://evil.example.com/v1` → HTTP 400 + `reason: host_not_allowlisted` unless `BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST=1`.
- Happy path (3.1): POST `endpoint=https://api.openai.com` → passes allowlist; `endpoint=https://api.deepseek.com` → passes allowlist; `endpoint=https://generativelanguage.googleapis.com` → passes.
- Error path (3.1): mock endpoint returns 100MB body → request aborts at 64KB cap with `reason: response_too_large`.
- Error path (3.1): mock endpoint returns `text/html` → HTTP 400 + `reason: bad_content_type`.
- Happy path (3.2): inside OAuth flow, env var is set; after BOTH start and callback handlers return, env value is restored to its prior state.
- Error path (3.2): `_oauth_callback_uri()` returns `https://prod.example.com/...` → refuse to enable insecure transport; flow returns error.
- Edge case (3.3): POST `/profiles/delete` with `Referer: https://evil.com/x` → redirect to `/`.
- Edge case (3.3): POST with no `Referer` → redirect to `/`.
- Error path (3.4): flash msg containing `\r\nSet-Cookie: evil=1` is stripped to a single line before redirect.
- Error path (3.4): flash msg of 500 chars is truncated to 200 chars.
- Integration: tests run under both `client` and (when promoted to conftest per architecture finding) `csrf_client`.

**Verification:**
- `pytest tests/test_webui_llm_endpoint_ssrf.py tests/test_webui_oauth_env_scope.py tests/test_webui_flash_redirect_safety.py` passes.
- `grep -rn "os.environ\[.OAUTHLIB_INSECURE_TRANSPORT.\]" webui_app/routes/` returns zero matches outside the context manager body.
- `grep -rn 'flash_msg=.*str(e)\|flash_msg=.*exc' webui_app/routes/` returns zero matches.
- Manual: POST `endpoint=http://169.254.169.254` returns 400 with the expected JSON.

---

## System-Wide Impact

- **Interaction graph:** Unit 1 touches blueprint registration in `routes/__init__.py` (affects route-discovery tests); Unit 2.4 touches publish history invariant (affects every UI surface that reads history — index history tab, dashboard stats, history filter); Unit 3 touches oauth env which is module-global (any subsequent OAuth lib call in the process inherits the value today).
- **Error propagation:** Unit 3.1 SSRF rejection should surface as a 400 + flash, not a 500 — the current handler returns 200 with `{"status": "failed", "error": ...}` JSON; quick-win should keep that shape and add a `reason: "url_rejected"` field for the new gate so downstream tests/JS can distinguish.
- **State lifecycle risks:** Unit 2.4 history invariant test is the durable guard (R5). Without it, the next refactor in pipeline.py could silently regress.
- **API surface parity:** Unit 1 deletes a route; if any external link or doc references `/settings/llm-logs`, it 404s after merge. Searched `docs/`, `README.md`, `AGENTS.md`, `CLAUDE.md` — no references found.
- **Integration coverage:** Unit 3 needs the CSRF-on fixture (`csrf_client`) to verify the 400-vs-403 ordering matters for real attackers, not just unit-level input parsers.
- **Unchanged invariants explicitly:** `_global_csrf_guard` (PR #143) is not altered. `0o600` secret writes (PR #140) are not altered. `HIDDEN_FROM_UI` (PR #136) is not altered. Publish-history invariant (PR #87/#97/#156) is *strengthened* by Unit 2.4 (consolidated to single helper) and Unit 2.4's regression test.

## Risks & Dependencies

| Risk | Mitigation |
|---|---|
| Unit 2.4 helper signature mismatch with the inline at `pipeline.py:284` (helper may expect a different entry shape) | Read `_push_history_per_row` signature first; adjust either the helper or the call site to align; do not silently drop fields like `error` or `article_urls` |
| Unit 3.1 SSRF gate breaks legitimate self-hosted LLM endpoints (e.g., Ollama on `localhost:11434`) | Allowlist `127.0.0.1` and `::1` explicitly; document loopback exception in the route docstring |
| Unit 3.2 OAuth env scoping breaks Blogger OAuth flow if the lib reads the env at a different point than expected | Add an integration test that exercises a full OAuth start→callback round-trip and asserts the env is correct at each phase |
| `pyflakes` sweep accidentally removes an `as` alias still referenced via `mock.patch` | Per `[[feedback-dead-code-audit-blind-spots]]`: run full `pytest` after each commit, not just `py_compile`; relative-import miss only crashes at import time |
| Unit 1 removes `llm_diag` blueprint but some external integration script polls `/settings/llm-logs` for monitoring | Searched ops scripts and `docs/` — no references. Acceptable to delete |

## Follow-on Plans (Recommended Next)

Each follow-on is a separate `/ce:plan` invocation. None of these block the Quick-Wins.

### Plan A — `helpers/` subpackage extraction (delete-in-same-PR)
- **Scope:** Split `helpers.py` (1252 SLOC) into `helpers/{security,url_meta,history,contexts,cli_runner}.py`. **No transitional re-export shim** — mirrors PR #124 discipline. Each sub-module ships as a single PR that splits + migrates all 14 call sites + deletes the original code from `helpers.py` (which eventually becomes an empty `__init__.py` for the subpackage, or is deleted).
- **Why now:** F2, F10 (perf wins are blocked behind extraction).
- **Risk:** High — 14 importing route modules + mock-patch string targets break silently per `[[feedback-grep-all-legacy-import-forms]]` + `[[feedback-dead-code-audit-blind-spots]]`.
- **Mandatory pre-step per PR:** Grep all 7 legacy-import forms (4 absolute + 3 relative) + `mock.patch` string targets + full `pytest` run before merge. This is the non-shim safety net.
- **Sequencing:** Lowest-coupling first: `security` → `url_meta` → `history` → `contexts` → `cli_runner`. Each PR independent.
- **Estimated:** 5-7 units (one PR per sub-module).

### Plan B — `index.html` template split (resume Plan 013 + Phase C)
- **Scope:** Inline `<style>` → `static/css/index.css`; 4 tab-panes → `_tab_*.html` partials; inline `<script>` → `static/js/index_main.js`.
- **Why now:** F3 (drops template review surface from 2013 to ~400 SLOC; PR conflicts in worktrees compound today).
- **Risk:** Medium — JS context wiring via `data-*` attrs or `<script id="bootstrap-data" type="application/json">{{ ctx|tojson }}</script>`.
- **Dependencies:** Plan 013 B-1.1 (untracked) is the starting point; refresh against current main first.
- **Estimated:** 3-4 units.

### Plan C — `webui_store` factory migration (locked)
- **Scope:** Replace 5 module-level singletons + `_refresh_paths()` with a `WebUIStores` registry cached on the Flask app context (`app.extensions['webui_stores']`); lazy resolution via `_config_dir()` on first access. Drop `SqliteStore` (F13) — fold into JSON revert (single-row JSON blob defeats indexing; if true SQL is ever needed it's a separate plan).
- **Why now:** F4 (singleton freeze) + F13 (SqliteStore inconsistency).
- **Risk:** Medium-High — 14+ import sites need migration; test fixtures must change (`_refresh_paths()` calls removed); the per-test `app` fixture becomes the source of truth.
- **Sequencing:**
  - **Unit C1:** Introduce `WebUIStores` registry class + Flask extension wiring. Old singletons unchanged.
  - **Unit C2:** Migrate `history_store` + `_history_store` → `current_app.extensions['webui_stores'].history`. Update helpers.py + all 3 route writers.
  - **Unit C3:** Migrate `profiles_store` + `drafts_store` + `schedule_store` + `queue_store` + `channel_status_store`.
  - **Unit C4:** Drop `_refresh_paths`; migrate all test fixtures to per-test `app` factory; delete `SqliteStore` + `webui_store/sqlite.py`.
  - **Unit C5:** Update CLAUDE.md / AGENTS.md singleton documentation.
- **Estimated:** 4-5 units.

### ~~Plan D — Registry reverse-drive~~ **DROPPED**
F5 was a phantom finding — `_DOFOLLOW_BY_CHANNEL` already migrated to `publishing.registry` via Plan 2026-05-20-009 U5. The residue (move `binding_method()` and `status()` onto the adapter base class to shrink `_settings_context`) is small and folds into Plan A's `helpers/contexts.py` extraction.

### Plan E — UX consistency pass (split into 3 sub-plans per scope-guardian)

The original Plan E mixed perf, correctness, and pure UX — different risk surfaces. Split into:

**Plan E-polish:** F15 (vocab), F16 (loading states — overlap with Unit 2a poll backoff), F23 (AI-slop), F24 (HTML merge debris), F29 (backend select), F30 (sticky tab pin). Pure UX, low risk. 4-6 units.

**Plan E-perf:** F9, F10 → mostly absorbed by Plan A's `helpers/contexts.py` extraction (where `_settings_context` lives). Pull request-scoped memo (`flask.g`) for tokens/config/load_config; cache `dashboard_channels` per-render. 2-3 units.

(Plan E-correctness was originally suggested but F22 + F26 are now folded into Unit 2b and Unit 3 respectively — no separate sub-plan needed.)

- **Dependencies:** Plan B (template split) blocks Plan E-polish for the vocab/AI-slop work; Plan A blocks Plan E-perf for the context extraction.

## Documentation / Operational Notes

- Each follow-on Plan should grep `MEMORY.md` for related feedback memos before being written — several already exist (e.g., `[[feedback-render-auto-inject-over-per-route]]`, `[[publish-history-invariant-helper]]`).
- `CLAUDE.md` and `backlink-publisher/AGENTS.md` should be updated when Plan A extraction lands (helpers location changes) and when Plan C factory ships (singleton paragraph in CLAUDE.md becomes stale).
- No operational rollout concerns for Quick-Wins; all changes are local-WebUI behavior.

## Sources & References

- **Audit source:** 5 parallel agent runs (architecture-strategist / code-simplicity-reviewer / security-sentinel / performance-oracle / design-lens-reviewer), all dispatched 2026-05-21.
- **Cross-checked against:** `origin/main` HEAD `73d9984` (post-#143/#148/#140/#150/#154).
- **Excluded as stale-branch artifacts:** "_global_csrf_guard absent" (security-sentinel P0), "HIDDEN_FROM_UI absent" (architecture-strategist P0), "_LLM_SETTINGS_FILE hardcoded" (architecture-strategist P1, simplicity P2), "_channel_card_macro dead" (design-lens P0), **F5 _DOFOLLOW_BY_CHANNEL** (already migrated to registry via Plan 2026-05-20-009 U5), **F11 history unbounded** (cap enforced at every write site), **F12 dashboard.html stats endpoint** (endpoint deleted in PR #132), **F14 dashboard.html unreachable** (dashboard.html deleted in PR #132), **F25 dashboard.html CSRF UX** (file doesn't exist).
- **MEMORY learnings referenced:** `[[publish-history-invariant-helper]]`, `[[feedback-grep-dofollow-map-before-shipping-adapter]]`, `[[feedback-webui-store-config-dir-frozen]]`, `[[feedback-render-auto-inject-over-per-route]]`, `[[feedback-dead-code-audit-blind-spots]]`, `[[feedback-atomic-write-canonical-for-secrets]]`, `[[feedback-never-smoke-test-real-save-endpoints]]`, `[[feedback-grep-before-writing-brainstorm-plan-claims]]`, `[[sticky-tab-nav-short-page-click-pin]]`, `[[feedback-fetch-json-must-guard-content-type]]`, `[[feedback-grep-all-legacy-import-forms]]`.
- **Prior plans referenced (not blocked):** Plan 012 WebUI IA Phase A+B-1 (MERGED), Plan 013 B-1.1 (untracked draft, recommended as starting point for Plan B above), Plan 006 Channel Binding Dashboard (Phase 3 MERGED).
