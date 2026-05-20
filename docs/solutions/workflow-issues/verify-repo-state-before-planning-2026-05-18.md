---
title: "Verify every external state reference live before planning — memory ages, repos move"
date: 2026-05-18
category: docs/solutions/workflow-issues
module: /ce:plan grounding
problem_type: workflow_issue
component: development_workflow
severity: high
applies_when:
  - "Writing a plan that cites a SHA, branch state, PR merge status, or 'last known' fact"
  - "Memory or prior session notes claim 'PR X merged', 'branch Y is at HEAD', 'commit Z is on main'"
  - "Reusing research output from an earlier session as plan inputs"
related_components:
  - documentation
  - tooling
tags:
  - plan-grounding
  - external-state
  - memory-staleness
  - sha-verification
  - pr-merge-status
  - live-recheck
---

# Verify every external state reference live before planning

## Context

Plans regularly cite **external state references** — facts about the repo that live outside the plan-doc and change independently of it:

- "PR #98 is merged" / "PR #75 is in review"
- "main is at SHA `abc1234`"
- "branch `feat/foo` is ahead of main by 3 commits"
- "the `velog` adapter is on main as of 2026-05-19"
- "config-writer.py has 340 SLOC"

Each of these was true at some specific moment. None are stable. Between the moment they entered memory (or a prior plan-doc, or a research subagent's output) and the moment the current plan is being written, the repo may have moved.

When the plan locks in a stale external-state reference, downstream work breaks:

- Plan says "rebase onto post-#98 main" → #98 hadn't actually merged → rebase target wrong.
- Plan says "main is at `abc1234`" → main is at `def5678` (10 commits ahead) → plan units already half-shipped by someone else.
- Plan says "this branch is ahead by 3" → branch is ahead by 11 → plan size wrong.

Each is structurally avoidable with a live recheck before locking the plan.

## Guidance

### Pre-plan checklist: re-fetch every cited external fact

Before locking the plan, for each external-state reference in the plan-doc, run the live command:

| Plan claim | Live verification |
|------------|-------------------|
| "PR #N is merged" | `gh pr view N --json state,mergedAt,mergeCommit` |
| "main is at SHA X" | `git fetch origin main && git rev-parse origin/main` |
| "branch Y is ahead by N" | `git fetch origin && git rev-list --count origin/main..origin/Y` |
| "PR #N has Tier-2 review" | `gh pr view N --json reviews,reviewDecision` |
| "feature X is on main" | `git cat-file -p origin/main:<path>` (see [[validate-main-before-planning-off-feat-branch]]) |
| "Plan 005 = Medium Phase 1" | Read `docs/plans/2026-05-XX-005-*.md` directly; don't trust the memory label |
| "config-writer has N SLOC" | `python -m radon raw -s src/.../config/writer.py` |

The pattern is universal: for every claim of shape "<entity> has property <P>", run the command that returns <P> right now, and compare against the plan's claim.

### When live state disagrees with memory: trust live, update memory

If the live recheck disagrees with the plan-doc draft (or memory, or a prior session's claim), live wins. Update the plan-doc to reflect reality before locking, and update the memory entry if the source was memory:

```bash
# Memory said: "PR #98 merged 2026-05-19"
$ gh pr view 98 --json state,mergedAt
{"state": "OPEN", "mergedAt": null}

# Live disagrees → memory is stale.
# 1. Update plan-doc: "PR #98 still open as of 2026-05-20"
# 2. Update memory: rewrite the project_pr98_*.md entry to reflect current state
```

This prevents the staleness from propagating into the next session.

### Mis-labeled plan IDs are a special case

Memory entries sometimes encode plan-ID claims that don't match what's in `docs/plans/`. E.g., memory says "Plan 005 = Medium Phase 1" but `docs/plans/2026-05-XX-005-*.md` is actually about something else entirely. The agent who wrote that memory entry had a mental model that the next session needs to validate.

Resolve by reading the plan-doc directly:

```bash
ls docs/plans/2026-05-*-005-*.md
cat docs/plans/2026-05-*-005-*.md | head -30   # frontmatter + title section
```

The plan-doc is the source of truth for what Plan 005 is. Memory is a pointer that can dangle.

## Why This Matters

Plans cascade. A plan that locks on stale external state propagates the staleness:

- Implementation units reference incorrect SHAs → rebase fails or applies wrong content.
- "After #98 merges" framing becomes the trigger for downstream actions; if #98 isn't actually merged, those actions never fire (or fire incorrectly when something else lands).
- Reviewer reads "main is at X" in the plan-doc and reviews against X; if main is actually at Y, the review is anchored on a phantom baseline.

The cost of the live recheck (~10s of `gh` / `git` calls in parallel) is negligible. The cost of cascade-failure from stale references is sometimes a full plan re-write.

This is the same principle as [[validate-main-before-planning-off-feat-branch]] (verify file existence on main) and [[scan-parallel-prs-before-blocker]] (verify PR #N is still the relevant target), generalized to all external-state references. Each is an instance of: **memory and prior outputs are pointers, not facts; resolve them live at plan-time**.

## When to Apply

- Every `/ce:plan` invocation. Yes, every one — the cost is small enough to be unconditional.
- When a prior plan-doc is being extended into a new plan.
- When research subagents return output containing concrete state claims (SHAs, PR states, file metrics).
- When `MEMORY.md` entries are used as plan inputs.

Skip when:

- All external references in the plan are facts you established this session and haven't been written to disk in a way that other agents could have touched.
- The plan is purely conceptual (no concrete state references at all — rare).

## Examples

**Right:**

```bash
# Memory entry says:
#   "Plan 003 Phase B = Medium GraphQL spike scaffold (PR #119 squash ba74bd2)"
#   "Plan 005 = Medium Phase 1 (rebased onto post-#104 main)"

# Pre-plan verification:
$ git fetch origin main
$ gh pr view 119 --json state,mergedAt,mergeCommit
{"state": "MERGED", "mergedAt": "2026-05-20T06:22:00Z", "mergeCommit": {"oid": "ba74bd2..."}}
# OK — PR #119 actually merged as memory claims.

$ ls docs/plans/2026-05-*-005-*.md
docs/plans/2026-05-18-005-open-pr-landing-cleanup.md
$ head -3 docs/plans/2026-05-18-005-open-pr-landing-cleanup.md
# Plan 005: Open PR Landing Cleanup
# DISAGREES with memory: memory says "Plan 005 = Medium Phase 1" but plan-doc
# is about PR-landing cleanup. Memory was mis-labeled.

# Update memory; plan around correct labels.
```

**Wrong (2026-05-20 actual, Plan 003 Phase B scaffold preflight):**

```
Memory ref:     "Plan 005 = Medium Phase 1, see project_medium_graphql_phase1_pr88.md"
Plan written:   assumes Plan 005 is the Medium Phase 1 spec
Implementation: discovers docs/plans/2026-05-18-005-*.md is about PR-landing cleanup
                  (completely unrelated to Medium)
Recovery:       re-read all plan-doc filenames, realize memory mis-labeled
                Plan 005, update memory, replan with correct plan ID
Cost:           ~20 minutes of confusion + 1 memory entry rewritten
```

## Related

- `docs/solutions/workflow-issues/validate-main-before-planning-off-feat-branch-2026-05-19.md` — sibling: verify file existence on origin/main before planning.
- `docs/solutions/workflow-issues/check-upstream-refactor-before-fixing-stale-branch-2026-05-19.md` — sibling: verify file shape on origin before fixing.
- `docs/solutions/workflow-issues/scan-parallel-prs-before-blocker-2026-05-18.md` — sibling: verify user-named PR is still the relevant target.
- `docs/solutions/best-practices/grep-alleged-drift-sites-before-locking-framing-2026-05-19.md` — sibling: verify "X is missing" claims per-site.
- `docs/solutions/best-practices/sweep-tasks-run-pytest-before-planning-2026-05-18.md` — sibling: verify failure modes empirically before planning.
- `MEMORY.md` and `/Users/dex/.claude/projects/.../memory/` — common source of pointer-shaped references that need live verification.
