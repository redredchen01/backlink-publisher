---
title: "Foreign-agent WIP spreads across worktrees as identical broken modifications — diff before force-reset"
date: 2026-05-20
category: docs/solutions/workflow-issues
module: bp-*/ worktrees + cleanup scripts
problem_type: workflow_issue
component: development_workflow
severity: high
applies_when:
  - "Multiple bp-*/ worktrees show the same dirty-file pattern"
  - "About to run `git restore --worktree . --source HEAD` or `git reset --hard` across worktrees"
  - "Cleanup script (e.g., prune-stale-worktrees.sh) reports many worktrees as stale at once"
related_components:
  - tooling
tags:
  - worktree-cleanup
  - concurrent-agents
  - foreign-wip
  - rescue-stash
  - find-and-replace-tour
  - bp-worktree
---

# Foreign-agent WIP spreads across worktrees as identical broken modifications

## Context

`backlink-publisher` keeps 15-25 sibling `bp-*/` `git worktree` checkouts open at a time for parallel feature branches. Multiple agents (mine + concurrent sessions + automated cleanup scripts) edit and clean these worktrees concurrently.

When the **same dirty content appears across multiple worktrees simultaneously**, the cause is almost never local installer noise or random drift. It is a **concurrent agent doing find-and-replace across the worktree tree**. Hitting "yes" to `--force` cleanup at this moment destroys real work-in-progress that belongs to another session.

On 2026-05-20, a broken f-string regression (`f"..."` → `"..."` leaving literal `{x}` in output) plus a duplicate `DeprecationWarning` patch appeared identically across 7+ worktrees. Initial instinct was to `git restore` and move on. Diffing first revealed the pattern: a foreign agent was mid-tour with a planned refactor, and a `--force` sweep would have erased ~30 minutes of foreign work plus broken the in-flight branch into a state the foreign agent couldn't recover.

## Guidance

### Detection: same dirty file, multiple worktrees, identical hash

```bash
# Quick triage across all bp-* worktrees
for dir in bp-*/; do
  echo "=== $dir ==="
  git -C "$dir" diff --stat 2>/dev/null | tail -5
done

# Confirm "identical" via content hash, not just paths
for dir in bp-*/; do
  if [ -f "$dir/src/backlink_publisher/cli/plan_backlinks.py" ]; then
    md5 "$dir/src/backlink_publisher/cli/plan_backlinks.py" 2>/dev/null
  fi
done | sort -k4 | uniq -c -f3 -w32
```

If the same file has the same hash across N>1 worktrees and that hash differs from `origin/main`, you are looking at a **concurrent agent's find-and-replace tour**, not your own session noise.

### Categorize before action

For each affected worktree, decide:

| Category | Signal | Action |
|----------|--------|--------|
| **Your active branch** | You committed here this session, ahead of origin | `git stash push -u -m "wip-rescue-<topic>"` then proceed |
| **Foreign WIP, recent** | mtime on dirty files in the last hour, branch ahead of origin, no commits from your session | Leave alone. Do not `--force`. Notify the operator. |
| **Foreign WIP, stale** | branch tip == origin/main, dirty files only from `pip install -e .` (egg-info, __pycache__) | Safe to clean — but verify with `git status --short` |
| **Foreign find-and-replace** | identical content hash across multiple worktrees, branch ahead, no commits from your session | Rescue: `git stash push -u -m "foreign-wip-<dir>-<topic>"`. Don't reset. |

### Rescue-stash before `--force`

If you must clean a worktree containing potentially-foreign WIP:

```bash
cd bp-suspect/
git stash push -u -m "rescue-from-cleanup-$(date +%s)-${PWD##*/}"
# Now safe to reset; stash recoverable via `git stash list` from any worktree
# sharing the common gitdir
```

The `-u` flag captures untracked files (which `git restore` would silently delete). The descriptive message lets the foreign agent (or you, later) identify and recover the stash via `git stash list | grep rescue-from-cleanup`.

### Cross-worktree rule of thumb

Before running any of these across multiple worktrees, **diff against `origin/main` first**:

- `git restore --worktree . --source HEAD`
- `git reset --hard`
- `git clean -fd`
- `prune-stale-worktrees.sh` with `--force`

If `git diff origin/main --stat` shows content you didn't write, stop. Stash with description. Investigate.

## Why This Matters

The cost asymmetry is severe:

- **Skipping the diff**: saves ~10 seconds of bash. Risk: erase 30+ minutes of foreign agent's work across N worktrees, leaving the foreign session in an incoherent state mid-edit. The foreign agent's next action (assuming its diff is still there) may now operate on stale or corrupted state and introduce real bugs.
- **Doing the diff + stash**: costs ~30 seconds. Worst case the stash is noise and you `git stash drop` it later. Best case you preserved a colleague's in-flight refactor.

The asymmetry tilts further when the concurrent agent is automated cleanup. A find-and-replace agent doesn't watch for human signals — it tours every worktree it can reach and commits when done. If you `--force` mid-tour, the agent's coherence model is broken silently.

This is structurally the same as the [[worktree_concurrent_switching]] rule but caught at a different layer: that one is about a single foreign branch-switch erasing one worktree's WIP; this is about a foreign find-and-replace tour leaving identical changes across many.

## When to Apply

- Cleaning up `bp-*/` worktrees after a session, especially when memory or task notes mention "many worktrees" or "stale cleanup."
- A cleanup script (`prune-stale-worktrees.sh`, `scripts/cleanup-bp-*.sh`) reports >3 worktrees as candidates for removal at once.
- About to `--force` anything across more than one worktree in a single bash block.
- `git diff --stat` shows changes you don't recognize in a worktree you "didn't touch this session."

Skip when:

- Single worktree, dirty files clearly from your own session (`git log -1 --format=%an` is you, recently).
- All dirty content is install noise (`.egg-info/`, `__pycache__/`, `*.pyc`) — no `.py` source diffs.

## Examples

**Right (2026-05-20 actual):**

```
Observation:  prune-stale-worktrees.sh flagged 12 worktrees as stale
Triage:       md5 cli/plan_backlinks.py across bp-* → 7 worktrees identical hash,
              different from origin/main
Diff:         git diff origin/main → found f-string regression
              + DeprecationWarning patch in all 7
Conclusion:   concurrent agent tour, not install noise
Action:       per-worktree `git stash push -u -m "rescue-foreign-wip-<dir>"`
              before allowing cleanup
Outcome:      foreign agent's branch state preserved; user verified attribution
              the next morning and recovered from stash
```

**Wrong (counterfactual):**

```
Observation:  prune-stale-worktrees.sh flagged 12 worktrees as stale
Action:       `for d in bp-*/; do git -C $d restore --worktree . --source HEAD; done`
Outcome:      foreign agent's 30 minutes of refactor erased across 7 worktrees;
              foreign session continues making edits assuming its prior state;
              compounding incoherence; 2-hour rescue session next morning
```

## Related

- `docs/solutions/workflow-issues/worktree-concurrent-switching.md` (if promoted from memory) — single-branch concurrent switching.
- AGENTS.md "Worktree cleanup" — canonical guidance for `bp-*/` lifecycle.
- `scripts/prune-stale-worktrees.sh` — cleanup script that triggered the 2026-05-20 incident.
- `git stash --include-untracked` — non-negotiable for capturing untracked WIP before any reset.
