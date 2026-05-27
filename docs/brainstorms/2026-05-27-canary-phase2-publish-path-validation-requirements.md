---
date: 2026-05-27
topic: canary-phase2-publish-path-validation
---

# Canary Phase 2 — Real-Publish Forward-Path Validation

## Problem Frame

The adapter-contract canary v1 (PR #268, `link-alive` evergreen) re-fetches a
seeded canary post and asserts the target backlink survives + stays dofollow.
It catches **decay** of an existing link but is structurally **blind to forward
publish-path drift**: a platform that breaks *new* publishes — strips `rel` at
publish time, mangles the anchor, injects an interstitial — while leaving old
posts intact. The operator keeps dumping ineffective backlinks through a
silently-broken publish path and the evergreen signal never fires.

Phase 2 closes that loop by inspecting the **real backlinks the operator
actually publishes**, at publish time. The pipeline already fetches each
freshly-published post to verify the required link URL is present
(`_do_verify` → `verify_published`, with a 10–30s settle wait, gated by
`--no-verify`). Today that check confirms the link **exists** but never checks
its **`rel`/dofollow**. Phase 2 extends that same fetch to assert dofollow,
turning the post-publish verify into a forward-path drift detector on
production traffic.

> **Approach pivot (this session):** an earlier draft proposed a synthetic
> publish+delete round-trip on blogger/ghpages. Review showed that inspecting
> real publishes is strictly lighter and broader — no synthetic posts, no
> per-adapter delete primitive, no orphan lifecycle, no neutral-target/footprint
> concern, and it covers **every** platform instead of the two deletable ones.
> The synthetic approach is dropped.

Affected: the operator running `publish-backlinks`. No end-user impact.

## How v1 and Phase 2 differ

| Signal | Source | Catches | Latency |
|---|---|---|---|
| Evergreen decay (v1) | `canary-targets` re-fetches a kept canary post | A platform retroactively strips `rel` / deletes an *old* post | Operator's canary cadence |
| Forward-path drift (Phase 2) | `publish-backlinks` post-publish verify of the *real* backlink | A platform strips `rel` / mangles the anchor on a *new* publish | The very next real publish to that platform |

## Requirements

**Publish-path inspection**
- R1. Extend the existing post-publish verification (`_do_verify` / `verify_published`, which already fetches the published page with a settle wait) to also assert each *required* target link is **dofollow**, reusing v1's `inspect_target_anchor` rel-parsing on the page the verify step already fetches. Today the check asserts presence only.
- R2. A real publish where the required anchor is **present but nofollow**, or **rewritten/missing** despite the publish reporting success, is classified as forward-path drift — distinct from "post not reachable yet" (which is transient/unverifiable, see R5).
- R3. Forward-path drift is recorded **per platform** in the canary health store as a signal distinct from evergreen decay, so the operator can tell "this platform breaks new publishes" apart from "an old post decayed."
- R4. Coverage is **every platform that returns a `published_url`** to inspect — no delete-capability gating (that constraint belonged to the dropped synthetic approach). Browser-bound, highest-value platforms (medium, velog) are included.

**Drift signal handling**
- R5. Inspection is **fail-safe on uncertainty**: if the page is not readable (404/lag/transient) the result is `unverifiable`, never `drift` — only a readable page with a present-but-nofollow or missing anchor counts as drift. Drift is **debounced** (reuse v1's N=2 consecutive threshold) so a single flake never flags.
- R6. Forward-path drift is **advisory by default** (loud WARNING on the publish run + recorded health), consistent with v1. `hard_skip` opt-in may quarantine a platform from future publishes — which is the *correct* operator action here (stop publishing where new links are silently nofollowed).
- R7. The forward-path drift signal uses its **own counter and re-arm state**, separate from evergreen decay. An evergreen `link-alive` on an old kept post must **not** auto-clear a quarantine that a broken *publish path* set, and a forward-path drift must not be cleared except by a clean real publish. (Resolves the v1 shared-gate cross-contamination.)
- R8. Honor `--no-verify`: when post-publish verification is disabled, no forward-path signal is produced (consistent with today's verify behavior).

## Success Criteria
- A platform that strips `rel` on new publishes surfaces as forward-path drift on the **first** real backlink of a batch, so the operator stops before dumping the rest — where v1 evergreen would have stayed `link-alive`.
- Coverage spans all publish-capable platforms, including medium/velog, not just deletable ones.
- A transient fetch failure (post not live yet) never produces a false drift verdict.
- An evergreen green on an old post never silently un-quarantines a platform whose publish path is broken.
- Near-zero added publish latency — the dofollow assertion piggybacks the fetch the verify step already performs.

## Scope Boundaries
- **No** synthetic posts, **no** per-adapter delete primitive, **no** orphan lifecycle, **no** neutral-target handling — all dropped with the synthetic approach.
- **Not** a replacement for v1 evergreen — Phase 2 is additive: v1 watches decay of kept posts; Phase 2 watches drift at real-publish time.
- **Not** a new CLI verb — Phase 2 strengthens the existing `publish-backlinks` verify path; the forward-path health is surfaced through the existing canary health store + `/ce:health` card.
- **Not** a blocker on the publish that already happened — inspection is post-publish; it informs the *next* publish decision via advisory/quarantine.
- **Not** active when `--no-verify` is set.

## Key Decisions
- **Inspect real publishes over synthetic round-trip**: lighter (reuses an existing fetch), broader (all platforms), and catches drift on the traffic the operator actually cares about. (origin pivot this session)
- **Separate counter/re-arm for forward-path drift vs evergreen decay**: prevents a synthetic-vs-real-style cross-contamination where one stream clears the other's quarantine. (resolves review P0)
- **Fail-safe on unverifiable**: only a readable page with a confirmed nofollow/missing anchor is drift; lag/404 is never drift. (mirrors v1 marker-gated discipline)
- **Advisory default, hard_skip opt-in**: mirrors v1; here quarantine maps to the correct action (stop publishing where rel is stripped).

## Dependencies / Assumptions
- Builds on merged v1 (origin/main `7bbaf11`): `canary/store.py` (health record, debounce, advisory gate) and `inspect_target_anchor` (rel-parsing reused inside post-publish verify). NOTE: at time of writing the canary v1 code is on `origin/main` but the local `backlink-publisher/` checkout is behind and dirty with unrelated concurrent WIP — Phase 2 must branch off freshly-pulled `origin/main` and must not touch others' WIP.
- The existing `_do_verify`/`verify_published` path (cli/_publish_helpers.py) is the integration seam; it already fetches with a settle wait and is `--no-verify`-gated.

## Outstanding Questions

### Deferred to Planning
- [Affects R1][Technical] Does `verify_published` expose the fetched page body for rel-parsing, or does `inspect_target_anchor` need to re-fetch? Prefer threading the already-fetched body to avoid a second request.
- [Affects R3/R7][Technical] Exact `canary-health.json` schema for the separate forward-path stream (`publish_path_status`, its own `consecutive_failures`/`consecutive_oks`) coexisting with the evergreen fields.
- [Affects R2][Technical] How to distinguish "anchor rewritten/missing because the platform mangled it" from "the operator's own payload didn't include the link" — likely key off the `required` links the row declares.
- [Affects R6][Technical] How forward-path drift records into the advisory gate that `_canary_gate` already reads at publish time — and whether a drift detected mid-batch advises the operator for the *remaining* rows in the same run.
- [Affects R1][Needs research] Per-platform: does the post-publish settle wait (max 10–30s) reliably yield a readable page for dofollow inspection (esp. ghpages Jekyll build lag), or does drift inspection need its own longer/poll budget distinct from presence verification?

## Next Steps
→ `/ce:plan` for structured implementation planning
