---
title: "Spike: Medium GraphQL adapter — GO/NO-GO"
date: 2026-05-XX
status: draft
plan: docs/plans/2026-05-20-003-feat-portfolio-roundtrip-spike-quality-plan.md
phase: B
verdict: PENDING
---

# Spike: Medium GraphQL adapter — GO/NO-GO

**This is a placeholder skeleton. It will be filled in by B.2 after B.1 reconnaissance completes.** The B.1 working-notes scratch file (`2026-05-20-medium-graphql-spike-notes.md`) is `git rm`'d in the same commit that lands this deliverable, regardless of verdict.

## Verdict

> _(One paragraph. GO / NO-GO / GO-WITH-CAVEATS. Justification ≤ 3 sentences pointing to the decision matrix below. If GO and Plan 005 was not recovered: GO triggers a fresh `ce:brainstorm` round to scope Phase 2, not invocation of any pre-existing Unit 2/3/4 work.)_

## Context

Plan 2026-05-20-003 Phase B is a reconnaissance spike: can we publish to Medium via the authoring UI's own GraphQL endpoint, and what is the auth / CSRF / rate-limit / ToS surface?

The cookies-only browser-bind path (PR #88 `fdeaebc`) already publishes to Medium successfully and is the current production posture. A GraphQL adapter would replace the browser dependency with direct HTTP — lower runtime cost, less Playwright fragility, but with abuse-risk and ToS exposure.

The cited "Plan 005 = Medium Phase 1" memory pointer turned out to be mis-labelled — the repo's plan-005 documents PR landing cleanup. This spike's authoritative origin is Plan 2026-05-20-003 Phase B.

## Decision matrix

Each dimension carries (a) the finding from B.1 reconnaissance and (b) a per-dimension sub-verdict. The top-of-doc Verdict aggregates them.

| Dimension | Finding | Sub-verdict |
|---|---|---|
| **Endpoint feasibility** | <Is the GraphQL endpoint reachable by an unauthenticated HTTP client? Does it respond to operation names captured from DevTools?> | GO / NO-GO / CAVEAT |
| **Auth feasibility** | <Can we replicate the header set (authorization, x-xsrf-token, cookies) outside the browser? Can we mint these from a stored login state without re-running the OAuth/captcha flow?> | GO / NO-GO / CAVEAT |
| **CSRF plumbing** | <Is the CSRF token static-per-session, rotated, or fetched from a setup endpoint? What's the cheapest reproduction path?> | GO / NO-GO / CAVEAT |
| **Rate-limit headroom** | <Observed throttle threshold at order of magnitude (e.g. "few-per-minute" / "few-per-hour" / "none within probe budget"). Adequate for the project's publish cadence?> | GO / NO-GO / CAVEAT |
| **Credential rotation** | <Does logout invalidate prior session cookies immediately, or is there a usable validity window? What's the rotation cost from the adapter's perspective?> | GO / NO-GO / CAVEAT |
| **ToS risk** | <Medium ToS language re: programmatic publishing. Risk band: low / medium / high. Is the throwaway-account probing already against ToS, even if successful?> | GO / NO-GO / CAVEAT |
| **Migration cost** | <Estimate vs. keeping cookies-only browser-bind: order-of-magnitude SLOC, test surface, expected reliability delta.> | GO / NO-GO / CAVEAT |

## Evidence (indirect framing)

- **Endpoint shape**: <paraphrase only, e.g. "Medium's authoring tab issues GraphQL to a single endpoint with operation names in JSON body">. **DO NOT** include full URLs with hostnames.
- **Operation names observed**: <CreatePostV2, PublishPost, etc. — names only, no full request body>.
- **Header set**: <names only, e.g. "authorization (bearer), x-xsrf-token (mirrors a cookie), cookie (session)">.
- **Throttle observation**: <order of magnitude only, e.g. "throttle observed at ~few-per-minute pacing">. **DO NOT** record exact thresholds.
- **Credential rotation**: <e.g. "logout invalidates session immediately">.

## Recommendation

> _(If GO: enumerate the minimum Phase 2 questions a fresh `ce:brainstorm` would need to answer — e.g. login-state capture strategy, refresh on 401, throttle backoff. If NO-GO: state which dimension is the blocker and recommend continuing on the cookies-only browser-bind path. If GO-WITH-CAVEATS: list the caveats explicitly.)_

## Operational notes

- Throwaway Medium account used for probing: **NOT** linked to operator identity, used from VPN, not logged in from operator IP for ≥ 48h.
- All test posts published during probing were visibility=unlisted and explicitly deleted via UI by end of session.
- Total GraphQL request budget consumed: <N / 10>.
- B.1 working-notes (`2026-05-20-medium-graphql-spike-notes.md`) `git rm`'d in the same commit that lands this file.
- This deliverable uses indirect framing (operation names + paraphrased endpoint + order-of-magnitude rates) to avoid doubling as abuse-vector documentation.
