# Medium GraphQL Spike — Working Notes (LOCAL SCRATCH)

**DO NOT PUSH OR COMMIT THIS FILE.** B.2 will `git rm` it before deliverable lands.

This is the operator's reconnaissance scratchpad for Plan 2026-05-20-003 Phase B.1.
Every commit attempt must first run:

```
python scripts/scrub-spike-capture.py --check docs/spikes/2026-05-20-medium-graphql-spike-notes.md
```

The gate must exit 0 before the file is staged. Even with a clean gate, B.2 removes
this file as part of the GO/NO-GO deliverable commit.

---

## Preflight (already done by AI)

- **Plan 005 search**: `git log --all --oneline -- 'docs/plans/**medium*'` returned **no Medium Phase 1 plan**. The repo's "Plan 005" (`docs/plans/2026-05-18-005-refactor-open-pr-landing-cleanup-plan.md`) is unrelated — it documents PR landing cleanup. Memory entry [[project-medium-graphql-phase1-pr88]] is mis-labelled; this plan is the authoritative origin for the Medium GraphQL spike.
- **PR #88 ship surface**: cookies-only browser-bind path. No GraphQL adapter code exists in main yet. Files changed: `cli/_bind/recipes/medium.py`, `cli/medium_login.py`, `publishing/adapters/medium_browser.py`, 4 test files.
- **Prior Medium spike outputs** to consult for context (do NOT re-run their work):
  - `docs/solutions/best-practices/medium-httponly-auth-cookies-spike-3a-2026-05-19.md`
  - `docs/solutions/best-practices/medium-liveness-probe-partial-spike-2-2026-05-19.md`

## Setup checklist (operator runs before probing)

- [ ] Throwaway Medium account on a separate IP/VPN. Account email is NOT the operator email.
- [ ] Verify the throwaway account by publishing one **unlisted** post via the UI manually. Note the unlisted URL pattern. Do not log into the throwaway account from the operator IP for ≥ 48h after the spike.
- [ ] Browser DevTools open on the Medium authoring tab. Network panel filtered to `/_/graphql` (or whatever Medium's GraphQL endpoint path actually is — verify in DevTools).
- [ ] Stopwatch / wall-clock notes for rate-limit spacing.
- [ ] Plain notepad open separately — DO NOT paste raw HAR into this file. Capture **operation names + header NAMES + observed shapes** only.

## Endpoint & operations capture (target: ≤ 4 GraphQL requests on this section)

For each operation observed, fill in below. Use **paraphrased** endpoint (e.g., "Medium's authoring GraphQL endpoint" — not the full URL). Operation names are fine to record verbatim because Medium ships them in their public bundles.

### Operation 1: <name, e.g. CreatePostV2>

- **Method**: POST
- **Endpoint shape**: <paraphrase, no hostname>
- **Headers present** (names only, REDACTED values):
  - `authorization: REDACTED`
  - `x-xsrf-token: REDACTED`
  - `cookie: REDACTED`
  - <list others as names only>
- **CSRF plumbing observed**: <e.g., x-xsrf-token mirrors a cookie value / fetched from a setup endpoint / static>
- **Set-Cookie on response**: <names only, e.g., "sid rotated">
- **Status returned**: 200 / 4xx / 5xx
- **Notes**: <free text, no values>

### Operation 2: <e.g. PublishPost>

(same shape)

### Operation 3+: as observed

## Rate-limit probe (target: 6 posts total, ≤ 6 GraphQL requests on this section)

Stop at first throttle signal (status 429, status 403 + body suggests rate, or visible UI banner).

| Post # | Spacing from prior | HTTP status | Observed signal |
|---|---|---|---|
| 1 | n/a (cold) | <e.g. 200> | <none / 429 / other> |
| 2 | 30 s | | |
| 3 | 30 s | | |
| 4 | 5 s | | |
| 5 | 5 s | | |
| 6 | 5 s | | |

**Observed throttle threshold**: <"none within probe budget" OR "throttled at post N, spacing X" — order of magnitude only>

**GraphQL request total**: <N / 10 budget>

## Credential rotation behaviour

- [ ] Log out via UI → log back in.
- [ ] Check: do the cookies captured pre-logout still authenticate a GraphQL call?
- [ ] Result: <"old cookies invalidated immediately" / "old cookies still valid for X minutes" / "not tested">

## ToS findings

- Medium ToS section read: <paste section title / heading, NOT full text>
- Programmatic-publishing language present: <yes / no / ambiguous>
- Relevant quote (paraphrased, ≤ 1 sentence): <…>
- Risk assessment: <low / medium / high>

## End-of-spike cleanup (operator must run before B.2 commit)

- [ ] All 6 test posts deleted via UI (verify they 404 when accessed).
- [ ] Throwaway account still logs in (account not banned by probing).
- [ ] `python scripts/scrub-spike-capture.py --check docs/spikes/2026-05-20-medium-graphql-spike-notes.md` exits 0.
- [ ] B.2 deliverable drafted from this file's findings.
- [ ] **`git rm docs/spikes/2026-05-20-medium-graphql-spike-notes.md`** as part of B.2's commit.

## Operator → AI handoff prompt

When B.1 reconnaissance is done, paste the filled-in sections back to the AI with:

> Phase B.1 done. Scrub gate passes. Findings above. Draft B.2 GO/NO-GO deliverable
> per `docs/spikes/2026-05-20-medium-graphql-spike.md` skeleton — fill the decision
> matrix, write the verdict paragraph, and prepare the `git rm` of this notes file
> for the same commit.
