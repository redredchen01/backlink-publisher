---
title: "Grep each alleged drift site individually before locking a 'fully invisible' framing in the plan"
date: 2026-05-19
category: docs/solutions/best-practices
module: docs/plans + brainstorm workflow
problem_type: best_practice
component: development_workflow
severity: medium
applies_when:
  - "Brainstorm or research output claims 'X is completely invisible in Y' or 'X never appears anywhere'"
  - "Plan is about to commit to a scope based on a 'fully missing' framing"
  - "Reviewing a plan with absolute-quantifier statements ('always', 'never', 'all', 'none')"
related_components:
  - documentation
  - tooling
tags:
  - plan-grounding
  - drift-detection
  - partial-drift
  - absolute-quantifiers
  - brainstorm-review
  - empirical-verification
---

# Grep each alleged drift site individually before locking framing

## Context

Brainstorm-time framings often use absolute quantifiers — "X is completely invisible in Y", "X never appears anywhere in the codebase", "all Y are missing X". These framings are seductive because they sound decisive and they suggest a clean unit of work ("ship X to all Y").

In practice, **partial drift is the norm**, not the exception. Some sites of Y have X, some don't, often because someone shipped it incrementally and stopped halfway, or because the original framing of "X belongs in Y" was never settled. If the plan commits to "ship X to Y everywhere" without verifying per-Y, the agent ends up either:

- Re-shipping X to sites that already have it (wasted work, possible regressions).
- Surprised mid-implementation by sites with partial X and no clear migration path.
- Treating the existing partial implementations as bugs when they're prior shipped features.

On 2026-05-19, brainstorm output framed Velog as "fully invisible — no UI binding card, no JS counter, no settings entry". The plan committed to that framing. Grepping per-site revealed the actual state was 3/4 already shipped: UI card existed (PR #74), JS toggle existed, settings entry existed; **only** the JS counter was missing. The plan rewrite collapsed from 7 units to 1.

## Guidance

### Detection: absolute quantifiers in the framing

When a brainstorm or research output uses "fully", "completely", "never", "always", "all", "none" referring to a set of code sites, **treat that as a red flag** until each site is independently verified.

Useful phrases to flag:

- "X is fully missing in [module/feature/UI]"
- "Y has zero X anywhere"
- "All [channels/routes/handlers] need X added"
- "Nothing currently does X"

These are useful for **conjecture** but not for **planning scope**. The next step before committing the plan is per-site verification.

### Verification: one grep per alleged drift site

Enumerate the sites Y₁, Y₂, ..., Yₙ that the framing covers. Run one grep per site, ideally in parallel:

```bash
# Brainstorm: "Velog is fully invisible — no UI card, no JS, no settings, no counter"
# Verify each separately:

grep -nE "velog" webui_app/templates/settings.html
grep -nE "velog" webui_app/static/js/settings.js
grep -nE "\[targets\.velog\]" config.example.toml
grep -nE "velog" webui_app/binding_status.py
```

Each grep is one of three outcomes:

- **Empty** → the framing is correct for this site; include in plan scope.
- **Stub/comment only** → partial drift; needs a small fill-in, not a from-scratch ship.
- **Full implementation** → framing is wrong; exclude from plan scope.

The resulting plan reflects empirical reality: "ship X to Y₁ and Y₄; fill Y₂; Y₃ already done."

### Calibrate the absolute quantifier in the plan-doc

If the verification shows partial drift, **rewrite the framing** before the plan locks. Replace:

> Velog is fully invisible in the WebUI — needs UI card, JS, settings, counter

with:

> Velog WebUI integration is **3/4 shipped** (UI card via PR #74, JS toggle via PR #76, settings entry via PR #74). **Only the publish-history counter is missing.** Scope: add the counter; touch the existing three only if the counter requires shape changes.

The rewrite shrinks the plan from 7 units to 1 and prevents the agent from "re-shipping" existing work.

## Why This Matters

Plan-doc framings drive everything downstream — agent task lists, test scope, PR descriptions, reviewer expectations. An over-broad framing creates:

- **Wasted work**: 6 of the 7 units in the velog example would have been re-implementations of existing code.
- **Reviewer churn**: "why are you touching this file? it already does X" cycles.
- **Hidden regressions**: re-shipping an existing implementation with subtly different shape breaks downstream consumers that depended on the prior shape.
- **Plan-vs-code drift on top of plan-vs-reality drift**: see [[late-plan-revisions-skip-code]] — the agent often "honors" the plan by half-doing things, compounding the inconsistency.

The grep cost is ~30 seconds. The skip cost is sometimes catastrophic (7-unit plan thrown out, see [[validate-main-before-planning-off-feat-branch]] for a related case where a 7-unit plan was wasted for similar reasons).

## When to Apply

- Reviewing any brainstorm or research output that uses absolute quantifiers about code sites.
- Pre-`/ce:plan` step when the input is a brainstorm doc with sweeping claims.
- Mid-plan when an agent or reviewer says "but doesn't this already exist?" — that question is the trigger to run the per-site greps.
- When memory or prior notes claim "X is missing" — memories age, the codebase moves, verify before using.

Skip when:

- The framing is already specific (named sites, named files) and per-site verification has already been done in the brainstorm.
- The set Y is too large to enumerate (e.g., "every test file") — in that case, sample instead, but don't pretend the absolute quantifier is verified.

## Examples

**Right (2026-05-19, post-lesson):**

```
Brainstorm:  "Velog is fully invisible in WebUI"
Verify:      4 greps in parallel
Result:      UI card present (PR #74), JS toggle present (PR #76),
             settings entry present (PR #74), counter MISSING
Plan:        single unit, ship the counter; 1 PR vs original 7
```

**Wrong (counterfactual from the same session):**

```
Brainstorm:  "Velog is fully invisible in WebUI"
Plan:        7 units (UI card, JS toggle, settings entry, counter,
             routes, tests, docs)
Implementation:
   Unit 1:   "add velog UI card" → file already has it → confusion
   Unit 2:   "add JS toggle" → already exists → either skip or duplicate
   ... etc
PR:          rejected at review with "this is mostly re-shipping existing code"
Recovery:    plan rewrite + scope collapse, 4-day delay
```

## Related

- `docs/solutions/workflow-issues/late-plan-revisions-skip-code-2026-05-20.md` — adjacent: plan revisions don't auto-reach code.
- `docs/solutions/workflow-issues/validate-main-before-planning-off-feat-branch-2026-05-19.md` — adjacent: planning off stale state (this doc covers the "missing in scope" framing case; that one covers the "file doesn't exist on main" case).
- `docs/solutions/best-practices/document-review-catches-runtime-errors-at-plan-time-2026-05-14.md` — adjacent: doc-review's value at plan-time generally.
- PR #74, #76, #93 — velog UI work that was already shipped when the "fully invisible" framing arrived.
