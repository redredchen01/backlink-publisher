---
title: "Late plan revisions don't auto-land in code — annotate doc-only fixes as deferred"
date: 2026-05-20
category: docs/solutions/workflow-issues
module: docs/plans + /ce:work pipeline
problem_type: workflow_issue
component: development_workflow
severity: high
applies_when:
  - "A plan goes through multiple deepening passes / document-review iterations"
  - "P0 / P1 fixes are added to the plan during late review (esp. document-review or codex challenge)"
  - "Reviewing a Tier-2 /ce:review where project-standards-reviewer + api-contract-reviewer flag the same gap"
related_components:
  - documentation
  - tooling
tags:
  - plan-vs-code-drift
  - document-review
  - deepening-pass
  - tier-2-review
  - defer-with-docs-update
  - cross-reviewer-agreement
---

# Late plan revisions don't auto-land in code

## Context

`/ce:plan` and `/ce:work` produce a plan document. Deepening passes (especially `document-review`, codex `challenge`, plan-design-review) often surface P0 / P1 fixes that get folded into the plan text. The plan then lands as the shipping artifact.

The trap: **the plan text gets updated, the implementation skips the late-added fix entirely**. The agent reads the plan top-to-bottom while writing the code, but the new constraint was inserted in the wrong section, or expressed as prose rather than a checklist item, or added after the agent had already finished that unit. The plan ships clean; the code ships missing the fix.

PR #98 (plan-claims-gate) shipped with `exit code 9 + --strict-fetch` promised in plan §P0-3 — neither reached the code. PR #106 (`b450426`) shipped the annotation-only fix on 2026-05-20, four days later, after the Tier-2 review caught it.

## Guidance

### Detection: cross-reviewer agreement on Tier-2 `/ce:review`

When `project-standards-reviewer` AND `api-contract-reviewer` both flag the same gap on a Tier-2 review, the gap is almost certainly a **plan-vs-code drift** from a late deepening pass. Each reviewer compares the diff against a different reference (project conventions vs. external API contract); when both independently land on the same missing piece, that piece is not a code bug — it's a plan-doc artifact the implementation never honored.

This is a stronger signal than either reviewer alone. Single-reviewer findings are often style or scope. Two-reviewer agreement on a specific missing behavior is structural.

### Default resolution: defer-with-docs-update

Three options when this is caught post-merge or just-pre-merge:

| Option | When to use | Cost |
|--------|-------------|------|
| **Implement now** | Fix is <50 lines, clearly scoped, no schema/exit-code/contract change | Same PR or fast follow |
| **Defer-with-docs-update** | Fix touches schema / exit codes / cross-cutting contract; design intent is correct but implementation deferred | One small PR; preserves design |
| **Remove from plan** | Fix turned out to be wrong (deepening pass over-engineered) | Plan-doc revert |

**Defer-with-docs-update** is the default for non-trivial cases. PR #106 is the canonical example: 4-line annotation in three plan sections + docstring annotation, zero code change. The plan-doc now reads "exit 9 + --strict-fetch deferred to v1.1, see [link to follow-up issue]" instead of pretending the feature shipped.

The annotation discipline:

1. In the plan section that promised the fix, add `> **Deferred to v1.1:** <one-line reason>. Tracking issue: #N.`
2. In the source file's module docstring (or the symbol the plan referenced), add a one-line comment cross-referencing the plan section.
3. File a tracking issue for v1.1 with the original plan-section text quoted verbatim.

This is not "we'll do it later" hand-waving. It is an explicit honest contract update: the plan said X, the code does not yet do X, here is the next planned moment when it will, and X is preserved as design intent rather than silently dropped.

## Why This Matters

The plan-vs-code drift is a silent failure mode of compound engineering:

- **Future agents trust the plan**. If the plan says "exit 9 + --strict-fetch" and the code does not implement it, the next agent reading the plan and trying to extend the feature will assume the feature exists and break their own work on top of phantom behavior.
- **Tier-2 reviews fire repeatedly** on the same gap until the plan is reconciled, wasting review-budget on a known-known.
- **Operators and downstream consumers** read the plan as the spec — exit-code tables, CLI contracts, behavior promises — and file bug reports against missing features.

The annotation-only fix is cheap (4 lines × 3 sections), preserves the design conversation, and prevents the drift from compounding.

## When to Apply

- Tier-2 `/ce:review` on a recently-merged PR where the plan went through ≥2 deepening passes.
- Post-merge audit when two unrelated agents independently file the same "feature X missing" issue.
- Pre-merge moment: the implementing agent realizes a late plan revision wasn't honored. Default action: annotate the plan-doc as deferred, do not "quickly add it" without re-reviewing the cascading implications.

Skip when:

- Plan never went through a deepening pass (no late-added P0/P1 layer to drift from).
- Late revision was a code-correctness fix (typo, wrong API call) — those are normal code changes, not plan-doc drift.

## Examples

**Right (PR #98 → PR #106, 2026-05-20):**

```
2026-05-19  PR #98 (plan-claims-gate) ships. Plan §P0-3 promises:
            "exit code 9 for stale-grandfather; --strict-fetch flag for CI"
            Code: exit codes 0/1/2/7/8 only, no --strict-fetch.

2026-05-20  Tier-2 /ce:review finds the gap.
            project-standards-reviewer:    "exit code 9 missing from CLI"
            api-contract-reviewer:         "--strict-fetch promised in plan, not in argparse"
            → cross-reviewer agreement → plan-vs-code drift

            PR #106 ships: plan §D3, §D16, §P0-3 annotated as deferred to v1.1,
            docstring on plan_check.py also annotated. No code change.

Outcome:    Plan-doc honest. v1.1 tracking issue filed. Next agent reading
            the plan sees "deferred" not "exists."
```

**Wrong (counterfactual, same scenario):**

```
2026-05-20  Tier-2 review finds the gap.
            "I'll just add exit 9 quickly" → implements without re-running design review
            → exit 9 conflicts with the grandfather short-circuit logic (added later in same PR)
            → CI breaks in unrelated way
            → revert + redesign
```

## Related

- `docs/solutions/best-practices/document-review-catches-runtime-errors-at-plan-time-2026-05-14.md` — adjacent: doc-review's value at plan-time, before code lands.
- PR #98 (`b632bc0`) — original ship that introduced the drift.
- PR #106 (`b450426`) — annotation-only fix.
- AGENTS.md § "Plan-claims gate" — current cutoff policy and gate exit codes.
