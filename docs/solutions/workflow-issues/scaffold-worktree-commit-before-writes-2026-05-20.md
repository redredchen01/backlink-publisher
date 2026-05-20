---
title: "Scaffold worktree: `worktree add` + write + commit must be one atomic step — `prune-stale-worktrees.sh` eats empty branches"
date: 2026-05-20
category: docs/solutions/workflow-issues
module: scripts/prune-stale-worktrees.sh + bp-*/ scaffolding
problem_type: workflow_issue
component: tooling
severity: medium
applies_when:
  - "Creating a new `bp-<topic>/` worktree to scaffold a spike or planning artifact"
  - "About to `git worktree add` followed by a series of `Write` / `Edit` tool calls"
  - "Cleanup script (`prune-stale-worktrees.sh`) runs on a schedule or as part of another agent's flow"
related_components:
  - development_workflow
tags:
  - worktree
  - scaffold
  - prune-stale
  - atomic-block
  - branch-tip-origin-main
  - concurrent-cleanup
---

# Scaffold worktree: `worktree add` + write + commit must be one atomic step

## Context

`scripts/prune-stale-worktrees.sh` (and similar cleanup scripts) classify a worktree as **stale** when its branch tip equals `origin/main` — i.e., the branch has no commits beyond what's already on main. Fresh `git worktree add` followed by uncommitted writes leaves the worktree in exactly this state: branch exists, points at main, no commits ahead.

If a concurrent cleanup agent runs `prune-stale-worktrees.sh --force` between the `worktree add` and the first commit, the new worktree is pruned, the uncommitted writes are deleted, and the agent that was scaffolding it loses everything.

On 2026-05-20, Plan 003 Phase B scaffold for `bp-medium-spike/` was eaten **twice** in succession before the agent batched the operations into a single atomic bash block.

## Guidance

### Atomic scaffold pattern

Run `worktree add`, write the files, `git add`, `git commit`, `git push` as **one bash block** with `&&` chaining:

```bash
git worktree add -b feat/<topic>-scaffold bp-<topic>/ origin/main && \
cd bp-<topic>/ && \
# Use Write/Edit tools here OR cat <<EOF for simple cases
echo "scaffold marker" > .scaffold-marker && \
git add . && \
git commit -m "scaffold: $TOPIC initial commit" && \
git push -u origin HEAD
```

After the first commit lands, branch tip is ahead-of-main and `prune-stale-worktrees.sh` correctly skips it.

If the scaffold needs significant tool-driven file writes that can't fit in one bash block, **commit a placeholder first**, then add content in subsequent steps:

```bash
# Step 1: claim the worktree with a trivial commit
git worktree add -b feat/<topic>-scaffold bp-<topic>/ origin/main && \
cd bp-<topic>/ && \
echo "Scaffold for: $TOPIC" > SCAFFOLD.md && \
git add SCAFFOLD.md && \
git commit -m "scaffold: $TOPIC placeholder" && \
git push -u origin HEAD

# Step 2: now safe to use Write/Edit at leisure
# (subsequent commits add real content)
```

The placeholder commit costs nothing and immunizes the worktree against the prune race.

### Don't trust "I'll commit in a minute"

Between `worktree add` and the first commit, every minute of uncommitted state is a window where the worktree can be eaten. Concurrent agents and scheduled scripts don't coordinate with your in-flight work. The window must be near-zero.

If a `git worktree add` happens and the **next** tool call isn't a `git commit`, treat it as a bug to fix immediately.

## Why This Matters

`prune-stale-worktrees.sh`'s "stale" definition is correct for the common case (worktree created, abandoned without any work). The same definition matches the "scaffold-in-progress" case from the outside — there's no observable difference until the first commit. The script can't tell which is which, so the discipline has to live in the scaffolding side.

Cost of getting it wrong:

- `Write` tool calls return success → false sense of progress.
- Cleanup script runs → worktree directory deleted, branch reference pruned.
- Agent retries the `Write` → fails because the worktree doesn't exist anymore.
- Agent investigates → discovers the worktree is gone → re-scaffolds → potentially same race.

On 2026-05-20, this loop happened twice before the agent batched into one block. Each occurrence cost ~5 minutes of investigation. The atomic-block fix is a 30-second rewrite of the scaffolding shell command.

## When to Apply

- Any `git worktree add` for scaffolding (new feature branch, spike, planning artifact).
- Any time a worktree is created in the same agent session as scheduled cleanup scripts (`prune-stale-worktrees.sh`, `cleanup-bp-*.sh`, cron-driven hygiene).
- Reviewing a script or playbook that does `worktree add` and then defers writes — flag for batching.

Skip when:

- You're recovering a worktree that was already committed and pushed (no race window).
- The new worktree is created with `--detach` or against a specific committed ref ahead-of-main (the prune script's heuristic doesn't fire on those).

## Examples

**Right (2026-05-20, after the lesson):**

```bash
git worktree add -b feat/medium-spike-scaffold bp-medium-spike/ origin/main && \
cd bp-medium-spike/ && \
mkdir -p scratch && \
cat > scratch/spike-template.md <<'EOF'
# Plan 003 Phase B — Medium GraphQL spike

[B.2 GO/NO-GO decision matrix here]
EOF
cat > scripts/scrub-spike-capture.py <<'EOF'
#!/usr/bin/env python3
# 5-way self-tested capture scrubber
EOF
git add scratch/ scripts/ && \
git commit -m "scaffold: Plan 003 Phase B Medium GraphQL spike (scratch + scrub gate)" && \
git push -u origin HEAD
```

One bash block. `prune-stale-worktrees.sh` cannot find a window.

**Wrong (2026-05-20, first two attempts):**

```
1. Bash: git worktree add -b feat/medium-spike-scaffold bp-medium-spike/ origin/main
2. Write: scratch/spike-template.md
3. Write: scripts/scrub-spike-capture.py
   [concurrent prune runs — bp-medium-spike/ deleted]
4. Bash: cd bp-medium-spike/ && git add .  → fatal: not a git repository
5. Investigation → re-scaffold → repeat
```

## Related

- `scripts/prune-stale-worktrees.sh` — the cleanup script whose heuristic defines "stale."
- `docs/solutions/workflow-issues/foreign-agent-wip-spreads-across-worktrees-2026-05-20.md` — adjacent: the rescue-stash flow for the post-prune recovery case.
- AGENTS.md → "Worktree cleanup" — canonical lifecycle guidance.
- `git worktree add` — primitive that creates the at-risk state.
