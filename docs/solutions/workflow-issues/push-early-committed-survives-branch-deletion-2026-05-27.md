---
title: "Push early: a concurrent cleanup can delete your branch mid-session — committed work survives as git objects, uncommitted does not"
date: 2026-05-27
category: docs/solutions/workflow-issues
module: git workspace hygiene + bp-*/ worktrees
problem_type: workflow_issue
component: development_workflow
severity: high
applies_when:
  - "Working in a bp-*/ worktree while concurrent cleanup scripts/agents reshape the workspace (rename worktrees, prune branches)"
  - "You have uncommitted edits OR local-only commits that exist on no remote"
  - "Mid-session your `cd` / `git` suddenly fails: worktree path renamed, branch ref gone"
  - "You need to know what you can still recover and what is genuinely lost"
root_cause: missing_workflow_step
resolution_type: workflow_improvement
tags:
  - worktree
  - concurrent-cleanup
  - branch-deletion
  - git-object-survival
  - push-early
  - recover-via-git-branch
  - cat-file
---

# Push early: a concurrent cleanup can delete your branch mid-session — committed work survives as git objects, uncommitted does not

## Context

On 2026-05-27, mid-way through implementing Phase 1 of the thin-WebUI refactor, a concurrent cleanup process renamed the worktree `bp-webui-typed-error/` to `bp-webui-error-fidelity/` and **deleted the branch** `feat/webui-typed-error-contract` it was on. The next `cd` failed; the branch ref was gone.

What survived and what didn't split cleanly along the commit boundary:

- **Unit 1 was committed** (`29faf94`). Even with the branch ref deleted, the commit still existed in the object store — `git cat-file -t 29faf94` returned `commit`. Recovered by re-pointing a fresh branch at it: `git branch recover/... 29faf94`.
- **Unit 2 was uncommitted** working-tree edits. It was **gone** — not in any stash (the stashes belonged to other concurrent agents and must not be touched). It had to be reconstructed from session context.

This is distinct from `scaffold-worktree-commit-before-writes` (empty branch == origin/main gets pruned) and `salvage-unmerged-work-from-dirty-behind-main-tree` (picking unmerged work out of a dirty tree). The lesson here is narrower: **deleting a branch ref does not delete its commits — but it does abandon your uncommitted changes.**

## Guidance

### Push as soon as a unit is committable

A local-only commit is one `git branch -D` (yours or a concurrent agent's) away from being unreachable. Pushing to origin makes it a remote ref no local cleanup can touch. In a workspace with concurrent agents actively renaming worktrees and pruning branches, the window between "committed locally" and "pushed" is a real exposure, not a theoretical one.

The cadence:

1. Commit each unit as soon as it's a complete logical change (you do this already for incremental commits).
2. **`git push -u origin HEAD` immediately after** — don't batch pushes to the end of the session.

This costs nothing extra and converts "recoverable only if I notice and re-point a branch in time" into "safe on the remote."

### If a branch vanished under you, check the object store before assuming loss

A deleted branch ref is recoverable as long as the commits haven't been garbage-collected (default gc grace is 2 weeks — plenty of time within a session):

```bash
# Is the commit still there despite the missing branch?
git cat-file -t <sha>          # prints "commit" if it survived
# (NOTE: cat-file exits 128, not 1, for a truly-missing object — see
#  git-cat-file-exits-128-not-1; discriminate on stderr, not exit code)

# Recover by re-pointing a fresh branch at the surviving commit
git branch recover/<topic> <sha>
git checkout recover/<topic>
```

Find candidate SHAs in `git reflog` (the common-gitdir reflog, shared across worktrees) even after the branch ref is gone.

### Uncommitted work has no object — accept it's gone, reconstruct deliberately

There is no `cat-file` recovery for working-tree edits that were never `git add`ed into even a stash. Don't burn time hunting for them in other agents' stashes (and never pop/drop those — they're concurrent-agent WIP). Reconstruct from session context, then **commit + push immediately** so the rebuild isn't exposed to the same race.

## Why This Matters

The instinct after losing work is "I should have stashed" — but in a concurrent-agent workspace, stashes are a shared, contested namespace you can't rely on (other agents' WIP lives there). The reliable durability primitive is the **remote ref**, not the stash and not the local branch. `git push` early is the single move that would have made Unit 1's recovery a non-event and prevented Unit 2's loss.

Cost of getting it wrong: ~15 minutes reconstructing Unit 2 from context, plus the uncertainty of not knowing whether the reconstruction matched the lost original.

## When to Apply

- Any session in a `bp-*/` worktree where you can see (via `git worktree list`) other active worktrees or know concurrent agents are running.
- Immediately after every incremental commit — make `commit && push` the unit, not `commit` alone.
- The moment a `cd` or `git` command fails with a missing-path / missing-ref error mid-session: stop, run `git worktree list` + `git reflog`, check object survival before re-doing work.

Skip when:

- You're the only agent and no scheduled cleanup runs (a normal solo repo) — local commits are safe until you choose to push.

## Examples

**Recovery sequence used on 2026-05-27:**

```bash
# branch feat/webui-typed-error-contract deleted, worktree renamed under me
git cat-file -t 29faf94          # → commit   (Unit 1 survived)
git branch recover/webui-typed-error-unit1 29faf94
git checkout recover/webui-typed-error-unit1
# Unit 1 back. Unit 2 (uncommitted) had no object → reconstructed from context,
# then committed + PUSHED before doing anything else.
```

**The prevention that makes the recovery unnecessary:**

```bash
git add <unit files> && \
git commit -m "feat(scope): unit N" && \
git push -u origin HEAD          # ← this line is the whole lesson
```

Related: `scaffold-worktree-commit-before-writes` (empty-branch prune race), `salvage-unmerged-work-from-dirty-behind-main-tree` (dirty-tree salvage), `git-cat-file-exits-128-not-1` (don't branch on its exit code), `[[feedback_verify_external_commits_before_push]]`.
