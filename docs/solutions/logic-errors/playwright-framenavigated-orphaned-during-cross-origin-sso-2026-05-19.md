---
title: "Playwright `page.on('framenavigated')` listener orphaned during cross-origin SSO redirect chain"
date: 2026-05-19
category: logic-errors
module: backlink-publisher / cli._bind drivers + scripts/medium_bind_spike.py
problem_type: logic_error
component: playwright_navigation_tracking
severity: high
symptoms:
  - "Operator completes a Google SSO + 2FA login flow end-to-end (cookies persisted, restart `goto('/me')` lands on `/@<user>`)"
  - "But the script's `page.on('framenavigated')` listener captured zero or near-zero events during the flow"
  - "And `page.url` polling, run every 3 seconds for up to 15 minutes, never observes the URL leaving `/m/signin`"
  - "Killing the script reveals the *real* tab(s) in `ctx.pages` are at the post-login URL — only the held `page` reference is stuck"
tags:
  - playwright
  - sso
  - cross-origin
  - framenavigated
  - bind-channel
  - silent-failure
---

# `framenavigated` is unreliable during cross-origin SSO

## Symptom

A driver or script holds a single Playwright `Page` reference, attaches `page.on("framenavigated", ...)`, navigates to a login URL (e.g., `https://medium.com/m/signin`), and waits for the operator to complete the login flow. When the login chain crosses origins — for example, the operator clicks "Sign in with Google" and is bounced through `accounts.google.com` for 2FA before returning to `medium.com` — the listener silently misses the navigation events. `page.url` polling never observes the post-login URL even though the underlying Chromium has clearly landed there (verified by `ctx.pages` enumeration after the fact and by cookies persisted to the profile directory).

## Reproduction

Two consecutive Spike 7 runs on 2026-05-19 against medium.com Google SSO produced this same behavior:

1. Spike opens headed Chromium, attaches `page.on("framenavigated", lambda f: nav_events.append(f.url))`, calls `page.goto("https://medium.com/m/signin")`.
2. Operator clicks "Sign in with Google", walks through 2FA, lands successfully on Medium.
3. `page.url` continues to return `/m/signin` indefinitely. `nav_events` does not grow past the initial signin-page load.
4. Cookie dump from the same profile after killing the script shows `sid`, `rid`, `cf_clearance`, `xsrf` all present with far-future expiries — login fully succeeded. Restart `goto('/me')` against the same profile lands on `/@redredchen01`.

The script's `page` reference was orphaned during the cross-origin handoff. The new page (or popup or replaced tab) that received the post-SSO Medium landing was not the one the listener was attached to.

## Why

A few plausible mechanisms, any of which is sufficient:

1. **Popup-based OAuth flow.** "Sign in with Google" can open a popup window for the Google flow. The popup is a *separate* `Page` in Playwright's `ctx.pages`. When the popup closes, the main tab's navigation may be replaced rather than `framenavigated`'d, or `framenavigated` fires on a tab the listener was never attached to.
2. **`window.location.replace` after redirect chain.** When the navigation goes A→B→C with `replace()` in the middle, intermediate `framenavigated` events may be dropped depending on whether the Page is still alive at the time the event would fire.
3. **Browser-context-level navigation in a new tab.** Some SSO providers open the post-login target in `_blank` or via `window.open`, leaving the original tab behind.

Playwright's `page` object models *one tab*. The cross-origin SSO flow does not model *one tab*. The mismatch is structural.

## What to do instead

In production drivers (not throwaway spikes), prefer one of:

1. **Listen at the `BrowserContext` level**: `ctx.on("page", ...)` plus `ctx.pages` enumeration on each poll tick. Track URL of every page in the context, not just one held reference. Apply the bound-predicate to *any* page in the context.
2. **Don't rely on `framenavigated` as a primary signal.** Use it only as a *fast-path* indicator that something is happening; gate success on positive URL match (regex against `_BOUND_URL_PATTERN`) plus cookie-sanity, and bound the wait with a wall-clock timeout that does not depend on event observation. This is what `recipes/medium.py:89-118` now documents.
3. **For interactive operator-side spikes**, accept that the data may be lossy and design the spike to capture profile state (cookies, last-visited URL) at the end of the run, not just event counts. The cookies survived the cross-origin chain; the in-memory event list did not.

## When to apply

**Apply when**:

- Writing or reviewing a Playwright-based bind / login / auth-validation flow that crosses origins (OAuth, SAML, third-party SSO).
- Investigating a driver that "times out" while the operator swears they completed login.
- Designing event-based progress indicators for any cross-origin flow.

**Do NOT apply when**:

- Same-origin login flows (e.g., username/password form on the target site itself with no third-party hop). `framenavigated` is reliable in that case.

## Examples

The Plan 003 driver (`recipes/medium.py:_medium_bound_predicate`) currently uses idle-detection-on-`page.on("framenavigated")` as a secondary fast-path, but the load-bearing safety floor is the 20-minute wall-clock `_ABSOLUTE_TIMEOUT_SECONDS` plus URL match against `_BOUND_URL_PATTERN`. The wall-clock floor is the only one that survived the Spike 7 observations; the idle-detector would have time-warped to a never-firing state had it been the only ceiling. The comment at `recipes/medium.py:89-118` documents this explicitly so future "cleanup" commits don't shorten the wall-clock floor.

## Prevention

1. **Never use `page.on("framenavigated")` as the *sole* completion signal for a cross-origin flow.** Pair it with positive URL match AND a wall-clock floor that doesn't depend on events.
2. **When designing an operator-interactive spike**, enumerate `ctx.pages` periodically and dump *all* page URLs, not just the one the script holds. The non-orphaned page often exists; the listener just isn't attached to it.
3. **When debugging a "timeout but operator says success"** failure pattern, first dump the profile's cookies and re-launch a fresh context with the same profile. If the fresh context lands logged-in, the orphan-listener pattern is the most likely root cause.

## Related issues

- Plan: `docs/plans/2026-05-19-003-feat-medium-bind-hardening-plan.md` — Plan 003 Unit 1 carries this constraint into the production driver.
- Code: `src/backlink_publisher/cli/_bind/recipes/medium.py:89-118` (load-bearing docstring), `:255-285` (the predicate body that uses idle-detection as fast-path with wall-clock floor).
- Spike script: `scripts/medium_bind_spike.py` (the spike that surfaced this — kept for archival; URL-polling refactor allows non-TTY re-runs).
- Sibling spike doc: `docs/solutions/best-practices/medium-liveness-probe-partial-spike-2-2026-05-19.md`.
