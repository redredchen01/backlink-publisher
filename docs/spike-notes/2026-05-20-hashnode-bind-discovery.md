# Plan 2026-05-20-016 — Unit 1 Spike Notes (Hashnode Browser-Bind)

**Status**: IN PROGRESS (bind in flight, login awaiting operator)
**Date**: 2026-05-20 / 2026-05-21
**Branch**: `feat/hashnode-browser-bind` (worktree `bp-hashnode-bind`)
**Commits (so far)**:
- `26b73ef` spike(hashnode): stub recipe + post-bind inspector
- `af44b43` spike(hashnode): explicit 600s timeout on wait_for_url

> SPIKE PATCHES (in worktree, NOT committed — to be reverted before Unit 2):
> - `chrome_backend.py`: add `--remote-allow-origins=*` Chrome arg
> - `chrome_backend.py`: replace `http://127.0.0.1:` → `http://localhost:` (5 occurrences) — Chrome 148 IPv6 binding
> - `driver.py`: `BIND_TIMEOUT_MS` from `5*60*1000` → `15*60*1000`

---

## Step 0 — Dofollow Pre-Check

**Status**: PIVOTED. Original plan called for Chrome MCP-driven inspection of public Hashnode posts. Pivots:

1. **Chrome MCP extension was not connected at spike time** (operator hadn't installed `https://claude.ai/chrome`).
2. **`curl` and `WebFetch` both hit HTTP 403** on Hashnode posts (CF bot-detection — confirms memory `reference_hashnode_graphql_paywall.md` finding that "CF 把 `curl` 全擋了" generalizes to all Hashnode HTML, not just GraphQL endpoint).
3. **New pivot**: post-bind Playwright probe — re-launch chromium with the bind profile's `cf_clearance`-bearing cookies to query rendered Hashnode HTML and extract `<a rel=>` attributes. Script staged at `docs/spike-notes/2026-05-20-hashnode-dofollow-probe.py`. Will run after bind completes.

**Implication**: Plan-016 Risks table item "30-min manual browser inspect before any code" was infeasible at spike time given CF aggression. The post-bind probe path is the practical mitigation.

---

## Cloudflare Findings (load-bearing for Unit 2 + plan amendments)

### Confirmed: CF blocks Playwright Chromium

- **Symptom**: hashnode.com/onboard renders a CF challenge interstitial (operator-reported text: "此網站使用安全服務抵禦惡意機器人").
- **Stack**: `playwright sync_playwright().launch(headless=False, args=["--disable-blink-features=AutomationControlled"])` — i.e., the exact incantation the bind driver's `_PlaywrightBrowserRunner` (driver.py:469-475) uses.
- **`--disable-blink-features=AutomationControlled` is NOT sufficient** for Hashnode CF — even with the flag, CF detects Playwright via other signals (likely TLS fingerprint, WebGL, or proprietary heuristics).
- **3 attempts failed identically**: operator could not complete the CF puzzle in the Playwright Chromium window.

### Confirmed: CF lets through real Chrome with CDP backend

- **Symptom**: same flow with `RealChromeBrowserRunner` (chrome_backend.py) launching `/Applications/Google Chrome.app` with `--remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir=...` allowed the operator to clear CF (confirmed: `cf_clearance` HttpOnly cookie issued on `.hashnode.com`).

### Plan-016 amendment required

The original plan-016 Scope Boundaries explicitly excluded Chrome DevTools CDP backend:

> **out of scope (non-goals)**: 不做 Chrome DevTools CDP backend 整合

This spike **invalidates that decision**. CDP backend is the **only** path that gets past CF for Hashnode. Plan must be amended:

- Move chrome backend INTO scope for Hashnode (other channels still default to Playwright)
- `hashnode` recipe needs `required_backend = "chrome"` (or accept both; the recipe's `bound_predicate` is compatible with `_CdpPage` since `_CdpPage.wait_for_url` exists)
- `HashnodeBrowserAdapter.publish()` will need the same chrome-backend treatment (Playwright `launch_persistent_context` won't pass CF for publish session either) — this is **substantial unforeseen complexity** not in the plan

---

## CDP Backend Traps Found

Memory `feedback_chrome_devtools_cdp_traps.md` documented 4 traps. This spike adds **one more** (#5).

### Trap #1 (memory): `--remote-allow-origins=*` required (Chrome 111+)

- Already known. Fix landed in dev branch `fc41561` but **not yet on origin/main**.
- My branch's chrome_backend.py is missing it → applied as spike patch.

### Trap #2 (memory): Dynamic port (9222 often occupied)

- Not hit this spike (port 9222 was free).

### Trap #3 (memory): stderr must NOT be DEVNULL

- Honored by chrome_backend.py (writes stderr to `<profile>/.last-launch.stderr`).

### Trap #4 (memory): pytest fixture must clean up

- N/A for spike.

### Trap #5 (**NEW** — discovered this spike): Chrome 148 binds IPv6 (`::1`) **only**, not IPv4

- **Symptom**: `chrome_backend.py` uses `http://127.0.0.1:9222/json/version` for CDP discovery → returns nothing → script errors `chrome_cdp_unavailable`.
- **Verification**: `curl http://localhost:9222/json/version` works, `curl http://127.0.0.1:9222/...` hangs, `lsof -i :9222` shows `IPv6 ... TCP localhost:teamcoherence (LISTEN)` (IPv6 only).
- **Fix**: replace all 5 `http://127.0.0.1:` references in chrome_backend.py with `http://localhost:` (DNS resolves to `::1` on macOS by default).
- **Implication for plan-016**: this fix must be PR'd separately — affects ALL chrome-backend channels (telegraph already in production), not just hashnode. Update memory `feedback_chrome_devtools_cdp_traps.md` from "4 traps" to "5 traps".

### Trap #6 (also new this spike): BIND_TIMEOUT_MS = 5min too short for operator with CF + SSO + 2FA

- Default 5 min runs out before operator can clear CF + complete SSO + handle Hashnode onboarding wizard. Recommend doubling to 10 or 15 min (or making it env-configurable). Spike patched to 15min.

---

## Cookie Findings

### Logged-OUT baseline (8 cookies on hashnode.com, captured pre-login)

| Name | HttpOnly | Source / Purpose | Classify |
|---|---|---|---|
| `FPID` | ✓ | Google FedCM partition ID | tracker |
| `FPLC` | ✗ | Google Privacy Sandbox client | tracker |
| `_ga` | ✗ | Google Analytics | tracker |
| `_ga_72XG3F8LNJ` | ✗ | GA4 stream | tracker |
| `_ga_JM0KZQBCYG` | ✗ | GA4 stream | tracker |
| `_gcl_au` | ✗ | Google AdWords conversion linker | tracker |
| `ph_phc_8kd2luK..._posthog` | ✗ | PostHog product analytics | tracker |
| `cf_chl_rc_ni` | ✗ | CF challenge transient state | cf_baseline |

After CF challenge passed (real Chrome bind attempt):

| Name | HttpOnly | Source | Classify |
|---|---|---|---|
| `cf_clearance` | ✓ | CF clearance token (long expiry) | cf_baseline |

### CAPTURED: Logged-IN cookies (9 cookies on hashnode.com after bind)

| Name | HttpOnly | Secure | Val len | Expires | Classify |
|---|---|---|---|---|---|
| **`hashnode-session`** | ✓ | ✓ | 1289 | **+760 days** | **AUTH (whitelist)** |
| `cf_clearance` | ✓ | ✓ | 490 | +1095 days | CF baseline (blacklist) |
| `FPID` | ✓ | ✓ | 65 | +1130 days | tracker (blacklist) |
| `FPLC` | ✗ | ✓ | 136 | +731 days | tracker (blacklist) |
| `_ga` | ✗ | ✗ | 26 | +1130 days | tracker (blacklist) |
| `_ga_72XG3F8LNJ` | ✗ | ✗ | 54 | +1130 days | tracker (blacklist) |
| `_ga_JM0KZQBCYG` | ✗ | ✗ | 54 | +1130 days | tracker (blacklist) |
| `_gcl_au` | ✗ | ✗ | 23 | +820 days | tracker (blacklist) |
| `ph_phc_..._posthog` | ✗ | ✓ | 394 | +1095 days | tracker (blacklist; match prefix `ph_phc_*_posthog`) |

**Two transient Auth.js flow cookies** appeared mid-login then disappeared after Hashnode session was issued: `__Host-authjs.csrf-token`, `__Secure-authjs.callback-url`. Confirmed Hashnode's auth stack is **NextAuth.js (Auth.js)** — useful for production reasoning about session refresh / expiry behavior.

### Unit 2 cookie sanity gate (FINAL — based on logged-in data)

```python
HASHNODE_AUTH_WHITELIST = {"hashnode-session"}

HASHNODE_BLACKLIST = {
    # CF triplet
    "cf_clearance", "_cfuvid", "__cf_bm", "cf_chl_rc_ni",
    # XSRF (defensive — not seen in spike but pattern-known)
    "xsrf-token", "xsrf",
    # Google tracking
    "_ga", "_gcl_au", "FPID", "FPLC",
}
HASHNODE_BLACKLIST_PREFIXES = ("_ga_", "ph_phc_")
```

Single auth cookie (`hashnode-session`) is the **only required** persistence target. cf_clearance is **also load-bearing** (otherwise next publish would re-trigger CF challenge) — must be kept in adapter's `add_cookies()` even though it's not "auth" semantically. Unit 2 must broaden whitelist to **"required-for-publish"** instead of strict "auth-only".

### 🔴 Chrome backend bug — `cdp.all_cookies()` ignores `recipe.cookie_host_filter`

**Symptom**: spike captured **101 cookies** in storage_state — including 92 third-party ad/tracking cookies from sites operator visited unrelated to Hashnode (googleadservices.com, criteo.com, doubleclick.net, .youtube.com, .immersivetranslate.com, .wallethighlighter.com, .stackadapt.com, etc.).

**Root cause**: `chrome_backend.py` `_provider` callback hardcodes `cdp.all_cookies()` (no filter), unlike Playwright runner's `context.storage_state(host_filter=...)`. So even though my recipe sets `cookie_host_filter` to apex hashnode.com + *.hashnode.dev only, chrome backend ignores it.

**Impact** (HIGHEST severity for production): every channel that uses chrome backend (currently telegraph; soon hashnode) ships **cross-channel + cross-origin SSO + cross-site tracker** cookies in its credential file. Telegraph operators today have google/youtube/SSO cookies persisted in their telegraph-storage-state.json (if they ever logged into anything else in the same profile). This is the EXACT security-lens P0 finding from plan-016 document review (point #1) but now empirically reproduced.

**Fix**: chrome_backend.py `_provider` must apply `recipe.cookie_host_filter` to filter `cdp.all_cookies()` before persisting. Mechanically:
```python
def _provider(*, path) -> None:
    raw = cdp.all_cookies()
    host_filter = getattr(recipe, "cookie_host_filter", None)
    if host_filter:
        raw = [c for c in raw if host_filter(c.get("domain", ""))]
    state = {"cookies": raw, "origins": []}
    Path(path).write_text(json.dumps(state, ensure_ascii=False))
```
(Same logic Playwright runner already has.)

**Recommend**: ship this as standalone PR independent of plan-016 (affects telegraph in production today).

---

## URL Pattern Findings

**Login URL that worked**: `https://hashnode.com/onboard` (per spike recipe).

**Post-login landing URL**: **UNCLEAR from this spike** — bind predicate timed out (didn't match), meaning final URL was NOT in our regex's accept set. Chrome was terminated before we could probe it. Inference from auth cookies present + onboard URL → likely Hashnode kept operator on `hashnode.com/onboard/...` sub-paths (account setup wizard) or redirected to a CDN-style URL not matching `hashnode.com` apex.

**Unit 2 production predicate**: should use **cookie presence (`hashnode-session` set with non-empty value on apex)** as the bind signal, NOT URL pattern. URL alone is too brittle for Hashnode's multi-step onboarding flow. Cookie-presence predicate is also robust against operator stopping mid-wizard but with valid session already established.

---

### Unit 2 cookie blacklist starter (validated by spike Step 0/1)

Production blacklist for `cookie_sanity_passes` must include:
```python
{
    # CF triplet (memory baseline + spike-confirmed)
    "cf_clearance", "_cfuvid", "__cf_bm", "cf_chl_rc_ni",
    # XSRF
    "xsrf-token", "xsrf",
    # Google tracking trio
    "_ga", "_gcl_au", "FPID", "FPLC",
    # PostHog (Hashnode-specific)
    # Match by prefix: "ph_phc_*_posthog"
}
```

Whitelist (auth-likely) TBD — depends on logged-in cookie capture.

---

## Other Findings

### 🔴 Plan blocker: `AuthExpiredError` does NOT prevent chain fallthrough (contradicts plan R3)

**Evidence**:
- `_util/errors.py:54`: `class AuthExpiredError(DependencyError):` — subclass.
- `publishing/registry.py:172`: `except DependencyError as e: ... continue` — catches both base + subclass.
- Plan-016 R3 says: "raise `AuthExpiredError` → **不 fallthrough**". This is FALSE against current code.

**Implication**: When Hashnode browser cookies expire (or for medium / velog / blogger), dispatch silently falls through to next adapter in chain instead of surfacing "re-bind needed" UX.

**Unit 3 work item**:
- Add `except AuthExpiredError: raise` BEFORE `except DependencyError` in `registry.py:dispatch()`.
- Add regression test for each existing bind channel (medium, velog, blogger, hashnode) that expired cookies raise `AuthExpiredError` and dispatch does NOT call subsequent chain adapters.

**Bonus**: This fix benefits all bind channels, not just hashnode. May warrant a standalone PR landing before plan-016 Unit 3.

---

## Editor / Selectors / URL Pattern

**BLOCKED — operator account incomplete**. After bind, all editor URLs redirect to onboarding wizard:

| Probed URL | Final URL after redirect | Page title |
|---|---|---|
| `https://hashnode.com/` | `https://hashnode.com/onboard` | "Get Started \| Hashnode" |
| `https://hashnode.com/draft` | `https://hashnode.com/onboard?callbackUrl=%2Fdraft` | "Get Started \| Hashnode" |
| `https://hashnode.com/dashboard` | `https://hashnode.com/onboard?callbackUrl=%2Fdashboard` | "Get Started \| Hashnode" |

**Reason**: operator's session is established (`hashnode-session` cookie issued + valid) but the Hashnode account hasn't completed signup wizard (no username chosen → no personal blog subdomain → no editor access). Hashnode's middleware forces `/onboard` for incomplete accounts before any `/draft` or `/dashboard` is reachable.

**To unblock editor probing**: operator must complete the onboarding wizard (pick username, set up blog subdomain, choose interests), THEN re-run probe. Editor probing is **out of scope for THIS spike** — defer to Unit 2/3 implementation phase after operator commits to a real Hashnode identity. Could also pivot to operator running probe against an EXISTING fully-set-up Hashnode account on a different bind profile.

Target captures (still TBD):
- Editor entrypoint URL (likely `hashnode.com/draft` once account complete)
- Title input selector
- Body editor selector (likely contenteditable / TipTap)
- Publish button selector
- Tag input UX
- Banner upload mechanism

---

## Identity Identifier (for `hashnode-last-account.txt`)

**TBD** — operator hasn't completed wizard so no username / publication slug exists yet. Plan R6's preference for username over email holds; commit to picking from session JWT or `/api/me`-style endpoint once available.

---

## Dofollow Per-Surface Table

**BLOCKED — Playwright can't reach Hashnode subdomain posts**. CF challenges every probe:

| Surface | Sample URL | Probe verdict |
|---|---|---|
| `*.hashnode.dev` subdomain | `tanujabhatnagar.hashnode.dev/...` | **CF blocked** ("Just a moment...") |
| `*.hashnode.dev` subdomain | `aksbad007.hashnode.dev/...` | **CF blocked** |
| `*.hashnode.dev` subdomain | `wearedev.hashnode.dev/...` | **CF blocked** |
| Publication on subdomain | `townhall.hashnode.dev/series/hackathons` | **CF blocked** |
| Custom domain (personal) | not tested | not tested |
| Publication on custom domain | not tested | not tested |

**Root cause**: `cf_clearance` cookie issued during bind is **scoped to `.hashnode.com` apex** (Domain=.hashnode.com), so it doesn't apply to `*.hashnode.dev` subdomains. Each subdomain has its own CF challenge that requires its own clearance cookie. And Playwright (even with bind cookies) gets fresh-fingerprinted on each new subdomain → CF challenges all of them.

**Implication for plan-016 Unit 6**: dofollow verification **cannot run via Playwright** for Hashnode subdomain posts. Two paths:
1. **Real-Chrome-CDP** for Unit 6 dofollow probe (operator clicks through CF on each sample post once; cf_clearance accumulates per-subdomain in real Chrome profile)
2. **Manual operator inspection** (operator opens 3-5 Hashnode posts in their real Chrome and reports `rel=` directly)

**For THIS spike**: ship as TBD, recommend Path 2 (operator manual inspection — 5min, no engineering) for plan-016 Unit 6 gating decision.

---

---

## Unit 2-6 Implications (running notes — FINAL)

1. **Plan-016 Scope Boundaries amendment (REQUIRED)**: chrome backend MUST be in-scope for hashnode — both for bind AND for publish (CF blocks Playwright Chromium at both layers). Original "out of scope: CDP backend" decision is invalidated by empirical evidence.

2. **5 CDP traps documented** (was 4) — new IPv6-binding trap discovered (Chrome 148+). Plus separately found: BIND_TIMEOUT_MS = 5min too short, default `cdp.all_cookies()` ignores recipe's host_filter (security blast-radius bug affecting telegraph today).

3. **R3 AuthExpiredError-fallthrough fix** (independent PR opportunity): subclass of DependencyError means current `registry.dispatch()` silently falls through on expired cookies for ALL bind channels (medium / velog / blogger / telegraph). 3-line fix + 4 regression tests.

4. **HashnodeBrowserAdapter cannot mirror `medium_browser.py` straight** — Hashnode needs CDP-attached real Chrome for both `available()`-check session AND `publish()` session. This is a **major architectural delta** from the plan's "1:1 mirror medium_browser" approach. Unit 3 needs a redesign.

5. **`cookie_host_filter` Unit 2 design**: positive signal is presence of `hashnode-session` (1289-byte HttpOnly, +760 day expiry). Auth.js / NextAuth stack confirmed. Required-for-publish whitelist includes `cf_clearance` (necessary to avoid re-CF on next publish). Blacklist enumerated above.

6. **Banner upload (Unit 4)** — same CF/CDP constraint. Option A "short-lived Playwright session" from plan must be reworked to "short-lived chrome backend session". Higher latency cost than plan estimated (chrome bin launch ~2-3 sec vs Playwright Chromium ~7s; net comparable but chrome backend wraps cleaner).

7. **Operator UX (Unit 5 state matrix)** — `awaiting-operator` and `bind-in-progress` states need realistic 15-min budget. Onboarding wizard for fresh accounts adds another 5-10 min on top — Unit 5 needs explicit `account-incomplete` state for the case where operator binds but their Hashnode account isn't editor-ready.

8. **chrome_backend.py `cdp.all_cookies()` filter bug** — should be standalone PR fix, affects all chrome-backend channels (telegraph today). Mechanically simple.

9. **Probe-then-pivot for editor selectors / banner upload** (Unit 4 Option A vs B) — **DEFERRED** to implementation phase. Spike account is incomplete; selectors can only be probed against a real fully-setup operator account.

10. **Dofollow Unit 6** — Playwright-driven sampling infeasible (CF). Recommend operator does 5-min manual inspect-element on 3-5 sample posts and reports `rel=` directly. Cheaper than building chrome-CDP-driven Unit 6 probe machinery.

---

## Spike Verdict (recommendation to operator)

**GO** for plan-016 with major amendments. Hashnode browser-bind IS technically possible but plan-016 underestimated complexity:

- ✅ Real-Chrome CDP backend gets past CF (`cf_clearance` issued, `hashnode-session` captured).
- ✅ Cookie sanity gate design clear (single auth cookie, blacklist set).
- ✅ AuthExpired fallthrough fix is a bonus side-deliverable (helps all channels).
- ⚠️ Plan-016 Scope Boundaries needs CDP backend MOVED IN-SCOPE.
- ⚠️ HashnodeBrowserAdapter Unit 3 needs ~2x effort estimate (not 1:1 mirror of medium_browser).
- ⚠️ chrome_backend.py needs 3 prerequisite fixes shipped first (or as part of plan-016): IPv6 binding, `--remote-allow-origins=*`, `cdp.all_cookies()` host_filter.
- 🔴 Operator account onboarding completion is a manual prerequisite — Unit 2 bind UX must handle "session exists but account incomplete" state explicitly.

**Alternative for operator consideration**: with this much friction (real-Chrome backend required + 5 prerequisite plumbing fixes + account onboarding gating + dofollow uncertain), reconsider whether Hashnode justifies the build cost vs. alternative dofollow channels. Original plan-016 product-lens P0 finding ("six units of work for one channel") gains weight.

---

*Spike complete 2026-05-21.*
