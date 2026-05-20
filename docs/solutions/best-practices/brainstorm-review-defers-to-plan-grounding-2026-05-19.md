---
title: "Brainstorm-time 'simplify X' critiques lose to plan-time repo grounding — keep both, weigh empirically"
date: 2026-05-19
category: docs/solutions/best-practices
module: /ce:brainstorm + /ce:plan workflow
problem_type: best_practice
component: development_workflow
severity: medium
applies_when:
  - "Brainstorm-time document-review suggests simplifying a design decision"
  - "Plan-time grep reveals concrete repo state that contradicts the simplification"
  - "Reviewing whether a simplification is real reduction or false-economy"
related_components:
  - documentation
  - tooling
tags:
  - brainstorm-vs-plan
  - simplification-critique
  - repo-grounding
  - design-priors
  - empirical-disagreement
  - false-economy
---

# Brainstorm review defers to plan grounding

## Context

`/ce:brainstorm` produces design discussion before code exists. Document-review during brainstorm often suggests "simplifications": drop a flag, collapse two exit codes, remove staleness checking and always fetch, etc. These critiques sound reasonable in the abstract — fewer moving parts, less surface area.

`/ce:plan` then grounds the design in the actual repo. Plan-time greps reveal that the proposed simplification conflicts with existing code: the dropped flag is exercised by 3 call sites; the two exit codes are read by 2 downstream scripts; "always fetch" violates an existing offline-mode guarantee.

**Plan-time empirical grounding outweighs brainstorm-time abstract critique.** The reviewer at brainstorm-time was reasoning about a system that doesn't quite exist; the planner has the actual code in hand. Simplifications that don't survive contact with the repo are false economies — they save complexity in the proposed design but force a more disruptive simplification of the existing code, often at higher total cost.

The 2026-05-19 plan-claims-gate workflow hit this three times in one session:

1. Brainstorm: "drop `--json` output mode". Plan grep: `errors.py` and 4 scripts consume the JSON shape. Drop would cascade. → keep `--json`.
2. Brainstorm: "collapse exit codes 0/1". Plan grep: `registered_consumer` callers distinguish. → keep both.
3. Brainstorm: "always fetch instead of staleness check". Plan grep: explicit offline-mode test fixtures expect staleness check. → keep staleness check.

## Guidance

### Record brainstorm-time critiques but don't auto-apply them

When document-review during brainstorm proposes simplification, record the proposal verbatim in the brainstorm doc:

```markdown
## Document review notes (2026-05-19)

- [REVIEWER] "--json output adds surface for marginal benefit; drop it"
- [REVIEWER] "Exit codes 0/1 distinction unclear; collapse to 0/non-zero"
- [REVIEWER] "Staleness check is premature; always fetch fresh"
```

These are inputs to the plan, not decisions. The plan agent will evaluate each against the repo.

### Plan-time: grep before accepting/rejecting each critique

For each brainstorm critique, the plan agent runs a verification grep:

```bash
# Critique: "drop --json"
grep -rnE '\-\-json|format.*=.*json' src/ tests/ scripts/

# Critique: "collapse exit codes 0/1"
grep -rnE 'returncode\s*==\s*[01]|exit\s+[01]' src/ tests/ scripts/

# Critique: "always fetch instead of staleness check"
grep -rnE 'is_stale|staleness|fetch_if_stale' src/ tests/ docs/
```

Each grep produces one of three results:

| Grep result | Critique status |
|-------------|-----------------|
| 0-1 hits, all in the brainstorm-scope code | Critique is valid. Apply the simplification. |
| 2-10 hits, in code outside brainstorm scope | Critique is partial. Either expand scope to refactor those hits, or reject the simplification. |
| >10 hits, in load-bearing existing code | Critique is a false economy. Reject and record why. |

### Record the decision and the grep evidence in the plan-doc

Plan §"Design decisions" should include:

```markdown
- [BRAINSTORM-CRITIQUE-1] Drop --json
  - Grep hits: 4 in src/, 3 in scripts/
  - Decision: REJECTED. --json is consumed by errors.py and 3 downstream
    scripts; drop would require touching those.
  - Trade-off: keep --json (no simplification). Plan size +0.
```

This records the empirical disagreement and prevents the same simplification from being re-proposed in the next deepening pass.

### When brainstorm and plan disagree, plan wins (with one exception)

The exception: when the plan agent's grep is incomplete or the brainstorm reviewer had context the plan agent missed. In that case, the brainstorm critique re-surfaces during code review and is resolved with full information. But the **default** is plan-wins.

## Why This Matters

Brainstorm-time critiques optimize for abstract simplicity ("fewer flags, fewer codes, less state"). Plan-time grounding optimizes for actual change-cost ("how many existing call sites?"). The two metrics agree often but not always.

When they disagree, applying the brainstorm critique without plan-time verification cascades:

- "Drop `--json`" → 4 downstream scripts break → 4 more PRs to update them → coordination overhead.
- "Collapse exit codes" → existing CI guards on exit 1 misfire → flaky CI.
- "Always fetch" → offline-mode test fixtures break → tests fail in restricted CI environments.

The brainstorm simplification was real reduction in the proposed code, but caused larger disruption in the existing code. The net change in repo simplicity is negative.

This is the inverse of [[grep-alleged-drift-sites-before-locking-framing]]: that doc is about over-broad "X is missing" framings; this doc is about over-broad "X should be removed" framings. Both fail the same way — by skipping the per-site grep.

## When to Apply

- Every transition from `/ce:brainstorm` → `/ce:plan`.
- When a document-review pass on a brainstorm proposes simplifications.
- When the plan agent feels pressure to "honor reviewer feedback" without checking it against the repo first.
- Adversarial-reviewer critiques on a Tier-2 review — same pattern, later phase.

Skip when:

- Brainstorm and plan agree (no disagreement to resolve).
- The simplification is in code the brainstorm is *creating* from scratch (no existing call sites to break).

## Examples

**Right (2026-05-19 plan-claims-gate):**

```
Brainstorm:    "--json adds surface; consider dropping"
Plan grep:     `grep -rn json src/ tests/` → 7 hits across 5 files
Decision:      REJECT simplification; --json is load-bearing
Plan doc:      records the critique + the grep evidence + the rejection
PR #98:        ships with --json intact
Outcome:       no cascading PRs, downstream scripts untouched
```

**Wrong (counterfactual same scenario):**

```
Brainstorm:    "drop --json"
Plan:          "good idea, drops --json from CLI"
Code:          --json gone
Downstream:    4 scripts fail
Recovery:      4 follow-up PRs to handle the simplification's blast radius
Net cost:      higher than just keeping --json
```

## Related

- `docs/solutions/best-practices/grep-alleged-drift-sites-before-locking-framing-2026-05-19.md` — sibling: per-site grep for "X is missing" framings.
- `docs/solutions/workflow-issues/late-plan-revisions-skip-code-2026-05-20.md` — adjacent: even when accepted at plan-time, late revisions can fail to land in code.
- PR #98 (plan-claims-gate) — workflow that surfaced this pattern.
- AGENTS.md → "Plan workflow" — canonical brainstorm → plan → ship flow.
