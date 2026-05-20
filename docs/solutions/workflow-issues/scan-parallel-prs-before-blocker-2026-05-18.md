---
title: "User names PR #N as the blocker? Run `gh pr list` first — parallel PRs may have superseded it"
date: 2026-05-18
category: docs/solutions/workflow-issues
module: PR landing workflow
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "User says 'PR #N is the blocker' or 'wait for PR #N' or 'after #N lands we can ship'"
  - "Memory or session notes reference a specific PR number as a dependency"
  - "About to base work / wait time on a PR-merge timeline given by the user"
related_components:
  - tooling
tags:
  - gh-pr-list
  - parallel-prs
  - supersession
  - user-framing
  - blocker-verification
  - pr-staleness
---

# Scan parallel PRs before treating a user-named PR as the blocker

## Context

When the user (or memory) names a specific PR number as the gating dependency — "we're waiting on #42 before we can land this", "after #42 ships I'll take the next unit", "block on #42" — there's a natural assumption that #42 is still the current candidate. That assumption is **often wrong** because the user's mental model can lag the repo state:

- Another agent or contributor opened a parallel PR (#43, #45) that covers the same scope and is further along.
- The original PR (#42) became stale, got force-superseded by a fresh cherry-pick (a la PR #77 → #81 in [[cherry-pick-to-main-when-parent-pr-blocks-ci]]), or was closed in favor of a different design.
- The user's framing is from a planning session days ago; the maintainers picked a different ordering in the meantime.

Acting on the user's PR number without verification means waiting on a PR that may never land, planning around a diff that no longer reflects what's coming, or rebasing onto the wrong target.

On 2026-05-18, the user explicitly named PR #42 as the blocker. PR #42 had been near-superseded — a parallel PR opened later was actually the merge candidate — but the user's last view of the repo was from before the supersession. A 30-second `gh pr list` would have surfaced this; without it, the agent prepared work targeting #42 that needed re-targeting once the supersession was discovered.

The lesson: **scan parallel PRs even when the user names a specific number**. Respect the user's framing (their #42 reference is still the working name), but sanity-check that #42 is still the relevant target.

## Guidance

### When the user names a PR, run `gh pr list` immediately

```bash
# Get a broad view of open PRs touching nearby scope
gh pr list --state open --limit 30 --json number,title,headRefName,baseRefName,updatedAt,author

# Or filter by keyword from the named PR's scope
gh pr list --state all --search "<keyword>" --limit 20
```

For each open PR with overlapping scope, check:

```bash
# Is the named PR still active?
gh pr view <N> --json state,mergeable,statusCheckRollup,updatedAt

# Is there a newer PR by the same author or in the same area?
gh pr list --search "author:<author> created:>2026-05-15" --limit 10
```

### Decision matrix

| Named PR state | Parallel PR exists | Action |
|----------------|---------------------|--------|
| Open, recent activity, CI green, mergeable | None | User framing valid. Proceed. |
| Open, recent activity, CI green | Another open PR covers same scope, further along | Surface to user: "I see #42 is open but #45 covers similar scope and is closer to landing — which should I plan against?" |
| Open, stale (>3 days no commits) | Newer PR by same author | Likely supersession. Confirm with user. |
| Closed/merged | N/A | User's mental model is stale. Surface the actual current state. |
| Draft / DIRTY / failing CI | N/A | Named PR isn't actually shippable. Surface the blocker. |

### Respect user framing during the report

When you find a divergence, **don't unilaterally re-target**. The user's PR number is still the operating reference; you're flagging the divergence so they can decide:

```
> I see PR #42 (the one you mentioned) is still open, but PR #45 was opened
> 2 days later by the same author covering the same scope and is already
> CI-green / mergeable. Looks like #45 may supersede #42. Do you want me
> to plan against #45 instead, or wait on #42?
```

Two paths from here:

- User confirms #45 is the new target → update plan.
- User confirms #42 is still the target (for design reasons) → proceed; #45 is noise.

Either way the user has the choice. The wrong move is silently switching to #45 without telling them.

## Why This Matters

PR-number references are the most common form of cross-session coordination. When they go stale:

- The agent waits on the wrong PR — work is delayed and may need re-targeting.
- The agent plans against the wrong diff — when the supersession lands, the plan is partially invalidated.
- The user is surprised — "wait, that was already merged?" — eroding trust in the agent's repo-state awareness.

The `gh pr list` cost is 5 seconds. The skip cost ranges from minor (one re-target) to major (a multi-day plan based on the wrong PR).

This is structurally the same lesson as [[validate-main-before-planning-off-feat-branch]] and [[check-upstream-refactor-before-fixing-stale-branch]] applied to PR identity instead of file content. All three share the principle: **verify external state references live, regardless of how confident the source feels**.

## When to Apply

- User says "PR #N", "after #N", "blocked on #N", "wait for #N".
- Memory or `MEMORY.md` entries reference a specific PR number as a dependency.
- Resuming a multi-session task whose last note referenced a PR.
- Planning around someone else's PR (parallel agent or human contributor).

Skip when:

- The user just created PR #N this session (no time for supersession).
- The named PR is your own and you've been tracking it actively.
- The PR is already merged (no further activity possible).

## Examples

**Right (post-2026-05-18 lesson):**

```
User:        "PR #42 is the blocker for this work"
Agent:       gh pr view 42 --json state,mergeable,updatedAt
             → OPEN, MERGEABLE, last update 3 days ago
             gh pr list --search "<scope-keyword>" --limit 10
             → finds PR #45 by same author, scope-overlapping, CI-green,
               updated 6 hours ago
Agent reply: "Quick check: PR #42 is still open but PR #45 (same scope,
              opened later by same author) looks closer to landing. Are
              you tracking #42 or has #45 superseded it?"
User:        "Oh — #45 is the real one. Use that."
Outcome:     Avoided ~4 hours of work targeting the wrong PR.
```

**Wrong (2026-05-18 actual):**

```
User:        "PR #42 is the blocker"
Agent:       starts preparing work targeting #42's diff
             after ~3 hours: discovers #45 superseded #42
             re-target everything to #45
             rework cost: ~half the time already spent
```

## Related

- `docs/solutions/workflow-issues/cherry-pick-to-main-when-parent-pr-blocks-ci-2026-05-19.md` — supersession mechanism (the source of #77 → #81 churn).
- `docs/solutions/workflow-issues/validate-main-before-planning-off-feat-branch-2026-05-19.md` — sibling: verify file existence on origin before planning.
- `docs/solutions/workflow-issues/check-upstream-refactor-before-fixing-stale-branch-2026-05-19.md` — sibling: verify file shape on origin before fixing.
- `docs/solutions/workflow-issues/multi-agent-turf-check-before-claiming-work-2026-05-20.md` — adjacent: turf-check at session start (different angle: claiming unowned work).
- `gh pr list` / `gh pr view` documentation — the verification primitives.
