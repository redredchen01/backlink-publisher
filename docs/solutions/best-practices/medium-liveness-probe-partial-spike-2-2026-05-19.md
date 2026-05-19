---
title: "Medium liveness probe — Spike 2 partial result (6/10 headless probes clear)"
date: 2026-05-19
category: best-practices
module: backlink-publisher / webui_app.medium_liveness
problem_type: best_practice
component: liveness_probe
severity: medium
applies_when:
  - "Deciding whether to flip `MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED` in `webui_app/medium_liveness.py` from `False` to `True`"
  - "Adding similar liveness probes for other channels (Velog, Blogger) — anti-bot challenge rate must be characterized before turning on automated headless probes"
tags:
  - liveness-probe
  - cloudflare
  - anti-bot
  - medium
  - spike-output
  - partial-data
---

# Medium liveness probe — Spike 2 partial run

## Observation

On 2026-05-19, Spike 2 ran 6 out of a planned 10 headless `goto("https://medium.com/me")` probes against the operator's logged-in `@redredchen01` profile (5-minute interval between probes, `~/.cache/medium-spike`). The script was terminated at the 30-minute mark before completing iterations 7-10.

All 6 completed probes:

```
[ 1/10] OK -> https://medium.com/@redredchen01
[ 2/10] OK -> https://medium.com/@redredchen01
[ 3/10] OK -> https://medium.com/@redredchen01
[ 4/10] OK -> https://medium.com/@redredchen01
[ 5/10] OK -> https://medium.com/@redredchen01
[ 6/10] OK -> https://medium.com/@redredchen01
```

Zero Cloudflare challenges (`challenges.cloudflare.com`, `__cf_chl_`), zero Datadome interrupts, zero `/m/signin` redirects. The probe consistently landed on the operator's profile page.

## Why this is *not* a green flag to flip the constant

The data is consistent with "Medium does not gate headless probes at the 5-minute cadence over the first 30 minutes", which is encouraging. But the original spike was designed to span 50 minutes (10 × 5-min intervals) for a reason: anti-bot decisioning systems frequently use **windowed rate budgets** (e.g., "more than N requests from this fingerprint in any 60-minute window triggers escalation"). A 6-of-10 sample does not characterize the back half of the budget where the most likely failure mode lives.

Decision: **leave `MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED = False`**. A future operator should run the full 10-iteration spike (or, better, a 24-hour observation against a real Settings-page polling cadence) before flipping. The cost of getting this wrong is operator-visible — the probe IP gets challenged, real publishes from the same host inherit the IP reputation, and unrelated workflows fail at a layer the operator didn't change.

## When to apply

**Apply this caution** before turning on any always-on liveness probe against a Cloudflare-fronted channel:

- Velog, Blogger, future channels — the same windowed-budget hypothesis applies. Six clean probes do not promise the next six are clean.
- New "active health check" features added to existing channels — the heuristic "headless never gets flagged" is false; the headless fingerprint is exactly what anti-bot vendors look for.

**Skip this caution** for one-shot probes triggered by operator action (e.g., a single liveness check on Settings page load behind an explicit user-clicked "Refresh status" button). One-shot is qualitatively different from automated polling.

## What we'd need to flip the flag

Either of:

1. **Full 10-iteration Spike 2 with zero challenges**, OR
2. **A 24-hour observation** of automated probes at the production cadence (5-min TTL) against a real account that also does occasional publishes; verify both probe and publish keep landing without challenges over the full window.

The current code already implements probe-copy isolation (live `storage_state.json` is never mutated by the probe) — that defends against the worst case where the probe gets challenged and rotates `cf_clearance` on the IP. See [[medium-httponly-auth-cookies-spike-3a-2026-05-19]] for why `cf_clearance` is segregated from auth.

## Related issues

- Plan: `docs/plans/2026-05-19-003-feat-medium-bind-hardening-plan.md` — Plan 003, Spike 2 was the headless-anti-bot gate before flipping `MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED`.
- Code: `webui_app/medium_liveness.py:53` (the constant), `:130-197` (`_active_probe` body that runs when the constant is `True`).
- Sibling spike: `docs/solutions/best-practices/medium-httponly-auth-cookies-spike-3a-2026-05-19.md` — same profile, same operator.
- Reviewer flagged the IP-reputation coupling between probe and publish (PR #83 adversarial review P1 #5); the doc comment near `MEDIUM_LIVENESS_ACTIVE_PROBE_ENABLED` calls for ≤1×/day probe rate before flipping, separate from this challenge-rate question.
