---
title: "Memory-recommended next unit ≠ unclaimed — turf-check worktrees and branches before acting"
date: 2026-05-20
category: docs/solutions/workflow-issues
module: multi-agent coordination + bp-*/ worktrees
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "Memory or session notes say 'next session start point: Unit N' or 'pick up X next'"
  - "Multiple agents/sessions concurrently work on the same repo via bp-*/ worktrees"
  - "Main worktree is dirty when you arrive — looks like your prior session"
related_components:
  - tooling
tags:
  - multi-agent
  - turf-check
  - memory-staleness
  - concurrent-sessions
  - worktree-attribution
  - wip-protection
---

# Memory-recommended next unit ≠ unclaimed

## Context

Memory notes like "下次 session 起点：U6" or "next: pick up Phase B" record what *should* happen next from your perspective at write-time. Between sessions, **other agents (concurrent Claude sessions, automated workflows, the operator manually, codex/cline/gemini side-sessions) may have already claimed and started that unit**. Acting on the memory recommendation without checking creates two-agents-on-one-unit collisions.

The collision shape: both agents make commits to the same conceptual unit on different branches, one agent's commits get force-pushed over the other's, or both agents open PRs for the same scope.

A subtler shape: the main worktree (e.g., `backlink-publisher/`) is dirty when you arrive. Your memory says "I was working on file X in this worktree." The dirty file might be **another agent's mid-flight WIP**, not yours.

## Guidance

### Turf-check at session start: 4 commands

Before acting on any memory-recommended next step:

```bash
# 1. What worktrees exist and what branches do they point at?
git worktree list

# 2. What branches exist (local + remote)?
git branch -a

# 3. What bp-*/ siblings are around, and what's dirty?
ls -d bp-*/ 2>/dev/null
for d in bp-*/; do
  echo "=== $d ==="
  git -C "$d" status --short 2>/dev/null | head -10
done

# 4. Any open PRs in your nominal scope?
gh pr list --state open --limit 20
```

Read the output before doing anything substantive. The questions to answer:

- Is there a `bp-<topic>/` worktree where `<topic>` matches your memory-recommended unit? → another agent already started it.
- Is there a branch named after your unit that you don't remember creating? → another agent.
- Is there an open PR with the unit's title? → already in flight.

If any answer is yes, **do not start the unit**. Either:

- Pick up where the other agent left off (coordinate with the operator first).
- Pick a different unit.
- Tell the operator about the collision and ask which agent should own it.

### Dirty main worktree: cluster + semantic check

If the main worktree (`backlink-publisher/`) is dirty when you arrive, do not assume it's your prior WIP. Two checks:

```bash
# 1. mtime cluster: when were the files modified?
git status --short | awk '{print $2}' | xargs -I{} stat -f '%Sm %N' {} 2>/dev/null | sort

# 2. Semantic check: do the dirty files match what your memory says you were doing?
git diff --stat
git diff | head -100
```

Cases:

| Mtime pattern | Semantic match to memory | Action |
|---------------|--------------------------|--------|
| All in your prior session window | Yes | Your WIP. Resume. |
| All in your prior session window | No | Concurrent session edited during your absence. Stash with description, investigate. |
| Last hour, you haven't been here in days | Either | Another active agent. `git stash push -u -m "foreign-wip-rescue-$(date +%s)"`. Do not reset. |
| Mixed timestamps | Mixed | Long-running concurrent work. Stash all, examine, attribute. |

### `git stash push -u -m "..."` is non-negotiable

Whenever there's any uncertainty about WIP attribution, **stash with `-u` (untracked) and a descriptive message** before touching anything:

```bash
git stash push -u -m "wip-protect-$(date +%Y%m%d-%H%M)-foreign-suspect"
```

The `-u` captures untracked files (which `git restore` would silently delete). The descriptive message lets the other agent (or you, later) identify and recover via `git stash list`.

## Why This Matters

Two-agents-on-one-unit collisions are expensive to untangle:

- Force-pushes lose work silently — the loser has no notification.
- Diverging commits to the "same" unit create review confusion (which PR is the real one?).
- Operator has to arbitrate ("which of these two PRs do you want?") — pure overhead.
- Trust between agent sessions erodes — memory becomes less reliable as a coordination mechanism.

The turf-check is 4 commands, ~10 seconds. The skip cost is sometimes hours of recovery and operator-visible chaos.

This pairs with [[foreign-agent-wip-spreads-across-worktrees]], which covers the recovery side. This doc covers the prevention side at session start.

## When to Apply

- Every session start, before acting on memory recommendations.
- After returning from an absence (>4 hours) on a repo with multiple concurrent agents.
- When the operator says "continue from where we left off" and memory shows a specific unit — verify it's still unclaimed.
- When the main worktree is dirty in any unexpected way.

Skip when:

- Solo agent, no concurrent sessions (e.g., the operator confirms "you're the only one touching this repo").
- The unit is too narrow for collision (e.g., "fix typo in README" — claims-conflicts are unlikely).

## Examples

**Right (turf-check before claiming):**

```bash
$ git worktree list
backlink-publisher/         abc1234 [main]
bp-medium-spike/            def5678 [feat/medium-spike-scaffold]   # ← memory says I should start this
bp-banner-u6-ghpages/       ghi9abc [feat/banner-u6-ghpages]

$ git -C bp-medium-spike/ log --oneline --all -10
def5678 (HEAD -> feat/medium-spike-scaffold) scaffold: B.2 decision matrix
abc... initial spike template
# → Another agent already scaffolded this. Don't restart; ask operator.
```

**Wrong (memory-driven action without check):**

```
Memory:    "Next: Unit 6 ghpages adapter"
Action:    git worktree add -b feat/banner-u6-ghpages bp-banner-u6/ origin/main
           [start writing adapter]
Reality:   another agent already created bp-banner-u6-ghpages/ 2 hours ago,
           has 3 commits, PR #122 is open
Result:    two parallel implementations of the same feature; operator has to
           pick one and discard the other's work
```

## Related

- `docs/solutions/workflow-issues/foreign-agent-wip-spreads-across-worktrees-2026-05-20.md` — recovery side: handling concurrent-agent WIP detected mid-session.
- `docs/solutions/workflow-issues/scaffold-worktree-commit-before-writes-2026-05-20.md` — adjacent: protecting your own scaffold from cleanup races.
- AGENTS.md → "Worktree cleanup" — bp-*/ lifecycle.
- `MEMORY.md` index — the source of memory-recommended next steps; always cross-check against live repo state.
