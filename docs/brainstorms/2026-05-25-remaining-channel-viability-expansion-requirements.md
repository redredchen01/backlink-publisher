---
date: 2026-05-25
topic: remaining-channel-viability-expansion
supersedes_probe: docs/spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md
related_plan: docs/plans/2026-05-25-001-feat-dofollow-tiering-platform-expansion-plan.md
related_brainstorm: docs/brainstorms/2026-05-25-dofollow-tiering-and-6-platform-expansion-requirements.md
---

# Remaining Channel Expansion — Build/Scope Decision (probe already complete)

## Problem Frame

The operator wants more distribution channels wired into the SEO backlink publisher.
This is **not** a fresh probing round — the Phase 0 read-only viability probe for all
candidates already ran earlier today
(`docs/spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md`,
status `probes-complete-awaiting-operator-go-no-go`). The verdicts already exist; this
document records the **build/scope decision** those verdicts force, so the choice is
auditable (R4) and not re-litigated.

Of the seven channels named across the parent effort
(`2026-05-25-dofollow-tiering-and-6-platform-expansion`), the landscape is:

| Channel | State | Source |
|---|---|---|
| **livejournal.com** | ✅ registered, `dofollow="uncertain"` (canary pending) | Plan 001 Unit 6 shipped |
| **txt.fyi** | ✅ registered, `dofollow="uncertain"` (canary pending) | Plan 001 Unit 7 shipped |
| **hashnode** | 🔴 removed 2026-05-22 (GraphQL API Pro-paywalled) — **out of scope** | PR #204 |
| **bloglovin.com** | 🔴 **NO-GO — platform effectively dead** (rebranded→Activate 2018, abandoned 2021, 403 Cloudflare to bots, no blog-post service) | Probe verdict |
| **justpaste.it** | ⚠️ **CONDITIONAL** — JS SPA, no native form, raw POST insufficient; needs XHR/API reverse-engineering or a browser recipe | Probe verdict |
| **teletype.in** | ⚠️ **CONDITIONAL** — JS SPA, account + JS editor; credential path (Unit 6-style, 0o600) | Probe verdict |
| **jkforum.net** | 🚫 **HOLD / likely NO-GO** — "bad-neighborhood" reputation risk (operator implicated in pornographic ads / arrests), Discuz default-nofollow, promo-post deletion | Probe verdict — **not selected this round** |

The operator selected **justpaste.it, teletype.in, and bloglovin.com** for this round
(hashnode and jkforum excluded). The probe has already settled two of those three:
**bloglovin is dead** (record retirement, no work), and the other two are **viable only
via the heavy adapter path** (JS/API reverse-engineering or a Playwright browser recipe +
credential persistence). So the genuine open decision is narrow: **is the heavy-adapter
investment for justpaste.it and/or teletype.in worth it, versus scoping them out and
instead closing the canary loop on the two channels already built (livejournal, txt.fyi)?**

⚠️ **"Adding a channel is cheap" does not apply here.** The cheap path (one `Publisher`
subclass + one `register(...)` line) is real only for a no-auth API/form platform like
txt.fyi. Both surviving candidates are confirmed JS SPAs: justpaste.it needs XHR/API
reverse-engineering or a browser recipe; teletype.in additionally needs an account +
credential persistence + bind flow — the heaviest adapter archetype in the codebase
(velog / medium_browser class). A new credential-bearing adapter also touches
`verify_adapter_setup()` and `_nofollow_rationales.py` in `adapters/__init__.py`, not just
one register line.

## Decision Flow

```
Per selected candidate — probe verdict already known:

  bloglovin.com ──────────────► NO-GO (dead platform)
                                Record retirement, 6→5 roster note. Zero build.

  justpaste.it  ──► JS SPA ──┐
  teletype.in   ──► JS SPA ──┤
                             ▼
              ┌─────────────────────────────────────────┐
              │ OPERATOR INVESTMENT DECISION (blocking)   │
              │ Heavy path required (reverse-eng/browser  │
              │ + credential path for teletype). Worth it │
              │ vs. closing livejournal/txt.fyi canaries? │
              └─────────────────────────────────────────┘
                  │                          │
         invest ▼                            ▼ scope out
   build adapter, then VALUE GATE       record CONDITIONAL-deferred
   (must clear before ship):            with rationale (R4).
   viable AND NOT (confirmed nofollow   Round may legitimately ship
   AND low referral value).             ZERO new adapters — the
   teletype heavy archetype needs       deliverable is the decision.
   the stricter bar: login AND
   (dofollow OR high referral value).
```

## Requirements

**Probe Reconciliation & Record-Keeping**
- R1. Treat the existing probe (`findings.md`) as authoritative for liveness/mechanism. Do **not** re-run read-only recon. Any further probing is limited to the post-build R4 canary (live publish + link-attribute check), not re-deciding viability.
- R2. Record bloglovin.com's NO-GO (dead platform) with the probe evidence and note the roster degrades 6→5, in a durable, auditable location (R4). No adapter work.
- R3. Acknowledge jkforum.net's HOLD verdict explicitly as out-of-scope-this-round with its rationale, so it is not silently dropped and not re-litigated.
- R4. Every NO-GO / HOLD / CONDITIONAL-deferred decision is recorded with probe evidence and date in a single auditable location (e.g. `docs/solutions/` or the plan's decision log), referenced from `MEMORY.md`, so future sessions inherit the verdict instead of re-probing.

**Build/Scope Decision (the blocking question)**
- R5. **RESOLVED (2026-05-25): defer both.** justpaste.it and teletype.in are recorded as CONDITIONAL-deferred (JS-SPA, heavy-adapter cost, uncertain value). No new adapter is built this round. The real deliverable becomes R14 — closing the existing livejournal / txt.fyi canary loop, which is the highest-leverage move given those channels are already built but stuck in `uncertain` limbo. This is an accepted "zero new adapters" outcome.
- R6. The "cheap channel-add" framing must not drive the decision. Cost is stated honestly per platform: justpaste.it = reverse-eng/browser recipe; teletype.in = browser recipe + account + credential persistence + bind flow + `verify_adapter_setup()` / `_nofollow_rationales.py` edits.

**Value Gate (applies only to a GO/invest platform, before ship)**
- R7. Build is justified **iff** technically viable **AND not** (confirmed `nofollow` **AND** low referral value). `dofollow` and (after the canary actually resolves) `"uncertain"` pass; `nofollow` + high referral value passes; `nofollow` + low referral value is rejected.
- R8. Because teletype.in is the heaviest adapter archetype, it carries a **stricter bar**: build only if the probe/canary shows login-required **AND** (dofollow **OR** high referral value). An uncertain-value + heavy-adapter combination is a NO-GO even if technically buildable (the cost class that drove hashnode's retirement).
- R9. "High" vs "low" referral value must be tied to a stated, reproducible threshold (e.g. a domain-authority / indexation cutoff) so the gate can objectively reject. Without a defined cutoff the `nofollow + high DA` branch is unfalsifiable.

**Adapter Implementation (per invest platform)**
- R10. Implement via the registry recipe: a `Publisher` subclass + a `register(...)` line. The no-edit guarantee covers `cli/*.py` and `schema.py` only — a credential/API adapter additionally requires an offline-readiness branch in `verify_adapter_setup()`, optionally a `_verify_live()` branch, and a `_nofollow_rationales.py` entry when `dofollow != True`.
- R11. justpaste.it: prefer XHR/API reverse-engineering to a lightweight HTTP adapter if a stable endpoint exists; fall back to a browser recipe only if not. teletype.in: browser recipe + account, following the velog/medium_browser credential path.

**Credential Safety (login-based platforms)**
- R12. teletype.in (Telegram-account login) and livejournal (password-equivalent XML-RPC secret, un-revocable) **must** bind via a **dedicated throwaway account**, never the operator's primary identity — consistent with the existing mastodon throwaway-account rationale. The bind flow documents this constraint.
- R13. Any persisted credentials, cookies, or browser storage-state for new login-based adapters **must** be written via `safe_write.atomic_write` at `0o600` — no hand-rolled writes.

**Canary Loop Must Actually Close**
- R14. The `dofollow="uncertain"` → canary → `amend to dofollow=True` loop must be a tracked, closing process, not fire-and-forget. Each uncertain platform carries: (a) a tracking artifact (issue / scheduled routine / test) that forces the amend step, and (b) a kill/keep deadline — if not confirmed dofollow or measurable referral within N publish cycles, the platform is retired. Apply retroactively to livejournal and txt.fyi, which already sit in `uncertain` limbo with no closing mechanism.
- R15. `verify_link_attributes` reads **static** HTML and regex-greps `<a>` tags; on a JS-SPA it returns `total_anchors=0` and a **false** dofollow signal. For any JS-SPA platform (justpaste.it, teletype.in), the canary verification must use a headless-render path, or the `uncertain→dofollow=True` amend is blocked pending manual inspection.

**Pipeline Reachability**
- R16. A GO platform must actually receive publish traffic, not sit registered-but-idle. If quota/proportion wiring stays deferred, the doc states explicitly that newly built channels produce zero backlinks until a named follow-on lands — so "publishes end-to-end" in Success Criteria is not mistaken for "is used in production."

**Reporting**
- R17. New platforms flow through the existing dofollow-tiering layers (`plan-backlinks` enrichment via `registry.dofollow_status()`/`referral_value()`, `report-anchors` tier/referral bucketing) automatically. No per-platform reporting code. (Verified: `cli/plan_backlinks/_payload.py` and `report_anchors.py` already read the registry generically.)

## Success Criteria
- bloglovin NO-GO and jkforum HOLD are recorded with evidence; the roster note reflects 6→5.
- The justpaste.it / teletype.in invest-or-defer decision is made and recorded per platform with rationale.
- **If invest:** the platform publishes end-to-end (plan → validate → publish), is registered, has green tests (`test_r9_extension_readiness.py` + adapter publish tests), uses a throwaway account with `0o600` credential storage, and its canary loop is tracked with a kill/keep deadline.
- **If defer:** a CONDITIONAL-deferred record exists; zero adapters shipped is an accepted outcome.
- No `nofollow` + low-referral-value platform is shipped; the heavy-archetype (teletype) bar (login AND dofollow-or-high-referral) is honored.

## Scope Boundaries
- hashnode is **not** re-added (API paywalled); jkforum is **not** built (reputation HOLD).
- No quota/proportion change in this round (tiering Phase 1 is observability-only); see R16 — built channels are idle until a later round wires quota.
- Do **not** touch the concurrent uncommitted working-tree WIP (the untracked `channel-manifest-architecture` brainstorm/plan `2026-05-25-002` and the ~28 modified files). Note: if that refactor changes the `register()` shape, R10/R17's registry assumptions must be re-checked.
- Do **not** re-run the read-only Phase 0 recon — it is complete (R1).

## Key Decisions
- The probe already ran; this round decides build/scope, not viability.
- bloglovin = NO-GO (dead). The remaining two candidates are viable only via the heavy adapter path.
- Heavy-archetype platforms (teletype) get a stricter value bar than cheap form-POST platforms.
- The uncertain→canary loop is currently non-closing and must be made trackable before more `uncertain` channels are admitted.

## Dependencies / Assumptions
- Reuses the registry recipe (AGENTS.md), `registry.register(...)` dofollow/referral gate (gate exists and is enforced), and the velog/medium_browser credential path for login adapters.
- `verify_link_attributes` requires a headless-render extension for SPA targets (R15) — not reusable as-is for justpaste.it / teletype.in.
- Assumes the registry-as-single-source-of-truth invariant holds and is not altered by the concurrent channel-manifest refactor.

## Outstanding Questions

### Resolve Before Planning
- (none — the build/scope decision is resolved: defer both new platforms, scope this round to the canary close-out.)

### Deferred to Planning
- [Affects R14][Technical] Concrete tracking mechanism for the canary close-out (issue vs scheduled routine vs test) and the kill/keep cycle count `N`.
- [Affects R14][Technical] The `amend dofollow="uncertain"→True` step today is a manual code edit; decide whether to add a registry helper or keep it a reviewed PR triggered by the tracking artifact.
- [Affects R9][Technical] Define the keep/kill rule for a canary that returns **nofollow**: txt.fyi is already `referral_value="low"`, so a nofollow result would make it a retire candidate — confirm the DA/indexation cutoff for "high" so the rule is objective. (Non-blocking: the primary path is dofollow-confirm → keep.)
- [Affects R12][Security] livejournal's XML-RPC secret is password-equivalent and un-revocable; confirm the already-bound account is a throwaway before publishing its canary. (Note, not a blocker — channel already shipped.)
- [Deferred candidates] If justpaste.it / teletype.in are revived later: justpaste.it XHR/API-vs-browser-recipe research; teletype.in JS-editor automation + Telegram throwaway bind; SPA headless-render verification (R15).

## Next Steps
→ `/ce:plan` for the canary close-out (R14/R15 for the two already-built channels) plus the record-keeping requirements (R2 bloglovin NO-GO, R3 jkforum HOLD, R5 justpaste.it/teletype.in CONDITIONAL-deferred, R4 audit location, 6→5 roster note). No new adapter is built this round.
