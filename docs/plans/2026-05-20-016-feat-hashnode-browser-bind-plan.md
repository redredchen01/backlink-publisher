---
title: Hashnode Browser-Bind Adapter (Free-tier Replacement for Paywalled GraphQL)
type: feat
status: blocked-on-spike-deepening
date: 2026-05-20
spike: docs/spike-notes/2026-05-20-hashnode-bind-discovery.md
spike_commit: d818662
spike_completed: 2026-05-21
note: |
  Original plan body (written during ce:plan workflow 2026-05-20) was lost
  from the main worktree before being committed (concurrent agent activity
  during spike). This reconstructed version is intentionally compact —
  the spike-notes findings + amendment list below are the new source of
  truth. Next-session ce:plan deepening pass should rebuild full plan
  structure from this skeleton + spike evidence + original requirements
  trace below.
---

# Hashnode Browser-Bind Adapter (Free-tier Replacement for Paywalled GraphQL)

> ## 🚦 Pre-Unit 2 status: BLOCKED on plan deepening pass
>
> **Unit 1 spike completed 2026-05-21** (commit `d818662` on `feat/hashnode-browser-bind`). Findings invalidate several plan decisions. Re-run `ce:plan` deepening pass with the amendment list below BEFORE starting Unit 2-6 implementation.
>
> ### Required amendments (from spike — `docs/spike-notes/2026-05-20-hashnode-bind-discovery.md`)
>
> 1. **Scope Boundaries**: move "Chrome DevTools CDP backend" from OUT-of-scope to IN-scope for hashnode (both bind and publish). CF blocks Playwright Chromium for both — empirically confirmed across 3 bind attempts. Real Chrome via CDP backend is the only viable path.
>
> 2. **Add Unit 0 (chrome backend prerequisites)** — three fixes to `cli/_bind/chrome_backend.py` shipped as standalone PR (affects telegraph in production today too):
>    - Add `--remote-allow-origins=*` to Chrome launch args (Chrome 111+ requirement — fix landed on `aab913a` in dev branch but not yet on origin/main; spike worked around inline)
>    - Replace all `http://127.0.0.1:` with `http://localhost:` (Chrome 148 binds CDP IPv6-only — `lsof -i :9222` shows `IPv6 ... LISTEN`; 127.0.0.1 IPv4 request hangs)
>    - Apply `recipe.cookie_host_filter` inside `_provider` callback before `json.dumps(state)` (currently `cdp.all_cookies()` returns ALL cookies cross-domain; spike captured 101 cookies including google/criteo/doubleclick/youtube/IMT — security blast-radius regression P0)
>
> 3. **Add Unit 0b (registry AuthExpired fix)** — `publishing/registry.py:dispatch()` catches `except DependencyError` which catches `AuthExpiredError` (subclass per `_util/errors.py:54`). Insert `except AuthExpiredError: raise` BEFORE the `DependencyError` clause. Add regression tests for all 4 bind channels (medium / velog / blogger / telegraph) confirming expired credentials raise AuthExpired and dispatch does not call subsequent chain adapter. Independent PR opportunity — benefits all channels.
>
> 4. **Unit 3 approach reworked** — drop "1:1 mirror `medium_browser.py`" framing. Hashnode needs CDP-attached real Chrome for `available()` check session AND `publish()` session. Mirror **telegraph_api** for chrome backend invocation, then layer publish-session-specific logic. Estimate ~2x prior effort.
>
> 5. **Unit 2 recipe details (now grounded)**:
>    - `cookie_host_filter` = apex `hashnode.com` + `*.hashnode.dev`
>    - `bound_predicate` = cookie-presence check for `hashnode-session` (HttpOnly, on `hashnode.com` apex). NOT URL-pattern — Hashnode's onboarding wizard captures the URL into `/onboard/*` paths even when session is established
>    - Required-for-publish cookie whitelist = `{hashnode-session, cf_clearance}` (both load-bearing — without `cf_clearance` next publish triggers CF challenge)
>    - Blacklist (validated by spike) = `{cf_chl_rc_ni, _cfuvid, __cf_bm, xsrf-token, xsrf, _ga, _gcl_au, FPID, FPLC}` + prefixes `_ga_*, ph_phc_*_posthog`
>    - BIND_TIMEOUT_MS bump from 5min → 15min (CF + SSO + onboarding wizard budget)
>
> 6. **Unit 5 state matrix additions**:
>    - State 11 (NEW): `account-incomplete` — session cookie present + first navigation to `/draft` redirects to `/onboard?callbackUrl=...` → operator sees "請先在 Hashnode 完成 onboarding wizard (選 username + 建 blog subdomain)" with deep link
>    - State 10 (existing): `awaiting-operator` realistic budget is 15min (was implied 5min)
>    - Unit 5 must distinguish "bind not started" vs "bind in progress (CF challenge)" vs "session captured but account not editor-ready"
>
> 7. **Unit 6 dofollow probe approach** — drop Playwright-driven sampling. CF challenges every `*.hashnode.dev` subdomain probe (cf_clearance is `.hashnode.com` apex-scoped only). Two pragmatic options:
>    - (a) Real-Chrome CDP backend for Unit 6 sampling (operator clicks through CF once per subdomain)
>    - (b) **Operator manual inspect-element** on 3-5 sample posts (5 min, zero engineering)
>    Recommend (b) — gates the entire plan-016 GO/NO-GO with minimal investment.
>
> ### Operator prerequisite before Unit 2-4 implementation
>
> Operator must EITHER:
> - (a) complete the Hashnode onboarding wizard on the spike-created account (`/tmp/hn-spike-config/real-chrome-profile` — choose username + set up blog subdomain), OR
> - (b) bind against an EXISTING fully-set-up Hashnode account (different email) — separate spike profile.
>
> Editor URL / selector / banner upload mechanism / identity-mismatch identifier probes all require an editor-accessible account. Cannot complete from current spike state.
>
> ### Recommended next-session steps
>
> 1. Operator completes Hashnode account onboarding (prerequisite)
> 2. Operator does 5-min manual dofollow inspect-element on 3-5 hashnode posts (gates the entire plan)
> 3. Run `/ce:plan deepen docs/plans/2026-05-20-016-feat-hashnode-browser-bind-plan.md` with this amendment list as context
> 4. After deepening lands: run `/ce:work` on Unit 0 (chrome_backend prereqs) + Unit 0b (registry AuthExpired fix) as **standalone PRs** that ship independently of plan-016
> 5. Then Unit 2-6 as a stacked PR series on top of Unit 0/0b
>
> ---

## Overview

Hashnode 在 2026-05-13 把 GraphQL API 整體搬到 Pro 訂閱後面，repo 既有的 `HashnodeAPIAdapter` 從那天起對 free-tier operator 等同壞掉。本 plan 用 chrome CDP backend + cookies-only 認證讓 Hashnode 重新可用（spike 證實 Playwright Chromium 過不了 CF，只有 real Chrome 過得了）。

## Problem Frame

- **既有狀態**：`HashnodeAPIAdapter` paywall 後必死。Dashboard 顯示 hashnode "暫不啟用"。`_DOFOLLOW_BY_CHANNEL["hashnode"] = None`（dofollow 屬性未驗）。
- **業務價值**：Hashnode 是 dofollow blog-aggregator 短名單 DA 最高的之一。但複雜度經 spike 證實 ~2x 於原 plan 估計，product-lens "6 units for one channel" 警告值得重審。

## Requirements Trace (original — may need deepening updates)

- **R1**. `bind-channel --channel hashnode --backend chrome` 成功 emit `channel.bind.persisted`，cookies (含 `hashnode-session` + `cf_clearance`) 落 `<config_dir>/hashnode-cookies.json` (0600)。
- **R2**. `publish-backlinks` 對 `platform=hashnode` 透過 chrome backend publish 路徑成功，回傳真實 `*.hashnode.dev/<slug>` URL。
- **R3**. Adapter chain 錯誤語義（Unit 0b 修 registry.dispatch 後）：cookies 缺 → DependencyError → fallthrough；cookies 過期 → AuthExpiredError → 不 fallthrough → operator UX 提示 re-bind。
- **R4**. WebUI Settings 顯示 hashnode bind 卡，跟 Telegraph/Medium/Velog 視覺一致。
- **R5**. `embed_banner` 走 chrome-backend short-lived session（plan 原為 Playwright Option A，spike 證 CF 阻擋，必須升級 chrome backend）。
- **R6**. `_DOFOLLOW_BY_CHANNEL["hashnode"]` flip 前須 operator 親自 inspect-element 5 篇現有公開 hashnode post 並記錄 `rel=`（CF blocks Playwright 自動 sampling）。
- **R7**. 既有 `tests/test_adapter_hashnode.py` 測試保留並通過（chain 走 fallthrough 仍 reach API adapter）。
- **R8**. `tests/test_r9_extension_readiness.py` 通過（新 adapter 透過 `register()` 一行延伸）。

## Unit Skeleton (for deepening pass to expand)

- **Unit 0** (NEW): chrome_backend.py 3 前置 fixes → standalone PR (telegraph 立即受惠)
- **Unit 0b** (NEW): registry.dispatch AuthExpired fix → standalone PR (4 channels 受惠)
- **Unit 1**: Spike — ✅ COMPLETE (this branch, commit `d818662`)
- **Unit 2**: HashnodeRecipe — cookie-presence predicate, host_filter, blacklist (replace stub from spike)
- **Unit 3**: HashnodeBrowserAdapter — chrome backend short-lived session for publish (mirror telegraph_api, not medium_browser)
- **Unit 4**: `embed_banner` — chrome backend short-lived session OR `None`-fallback per editor probe (probe deferred until operator account complete)
- **Unit 5**: WebUI bind card — 11 states (add `account-incomplete`); 15-min budget for `awaiting-operator`
- **Unit 6**: Dofollow flip — operator manual sampling (5min) → flip `_DOFOLLOW_BY_CHANNEL`

## Scope Boundaries

**In scope (amended)**:
- Chrome CDP backend for hashnode bind + publish + banner
- Pre-flight Unit 0/0b cross-cutting fixes

**Out of scope (unchanged)**:
- Pro plan subscription integration
- Plain Playwright Chromium path (proven non-viable for hashnode by spike)
- Schema changes — `[hashnode]` config + channel name reused

## Sources & References

- **Spike report**: `docs/spike-notes/2026-05-20-hashnode-bind-discovery.md` (canonical evidence)
- **Spike commit**: `d818662` on `feat/hashnode-browser-bind`
- **Spike runners**: `docs/spike-notes/2026-05-20-hashnode-chrome-bind.py`, `2026-05-20-hashnode-probes.py`, etc.
- **Original plan**: lost to concurrent worktree edit (see frontmatter note); reconstructed here from in-context memory + spike notes. Deepening pass should expand with full Implementation Units / Test scenarios / Verification rigor.
- **Related memory**: `reference_hashnode_graphql_paywall`, `feedback_chrome_devtools_cdp_traps` (update to 5 traps after spike), `feedback_grep_dofollow_map_before_shipping_adapter`
