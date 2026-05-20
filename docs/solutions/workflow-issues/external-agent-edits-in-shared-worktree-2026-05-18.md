---
title: "Concurrent agents in the same worktree — stage explicit files, never `git add -A`"
date: 2026-05-18
category: docs/solutions/workflow-issues
module: shared worktree commit workflow
problem_type: workflow_issue
component: development_workflow
severity: high
applies_when:
  - "Committing in a worktree where another agent or process may also be writing files"
  - "Tempted to use `git add -A` or `git add .` for convenience"
  - "Working in the canonical `backlink-publisher/` worktree during parallel agent sessions"
related_components:
  - tooling
tags:
  - git-add-A
  - shared-worktree
  - concurrent-agents
  - commit-isolation
  - explicit-staging
  - foreign-changes
---

# Concurrent agents in the same worktree — stage explicit files

## Context

`git add -A` and `git add .` stage **every** dirty path in the worktree — including changes made by other concurrent agents, automated cleanup scripts, install-noise files (`.egg-info/`, `__pycache__/`), and any background tooling that writes to disk. Using either command in a worktree that may be shared mixes foreign content into your commit silently.

In `backlink-publisher`, the canonical worktree often has install-noise from `pip install -e .`, files modified by concurrent Claude/codex sessions, and updates from `prune-stale-worktrees.sh`. A `git add -A` here grabs all of it. The resulting commit lands with attribution to your session and content from elsewhere.

## Guidance

### Default rule: stage by explicit path

When committing in a potentially-shared worktree:

```bash
# Right
git add src/backlink_publisher/cli/foo.py tests/test_foo.py docs/changes.md
git commit -m "fix: foo edge case"
```

The staged paths are exactly what you intended. Any other dirty content in the worktree is untouched and remains for the agent that wrote it to handle.

### Never use `git add -A` / `git add .` in shared worktrees

```bash
# Wrong — sweeps everything, including foreign WIP
git add -A
git commit -m "..."
```

Even when `git status` shows only your changes, a concurrent write between `status` and `add` lands in your commit. The check-then-act race is real.

### When you need broad staging: isolate first

If the change touches many files and explicit staging is impractical, isolate your work before committing:

```bash
# 1. List exactly what you intended to change
git status --short

# 2. For anything you don't recognize, isolate it
git restore --staged --worktree -- <foreign-path>   # restore unwanted dirty file to HEAD
# OR
git stash push -u -m "foreign-wip-$(date +%s)" -- <foreign-path>   # safer: preserve, don't delete

# 3. Now `git add -A` is acceptable because only your changes remain dirty
git add -A
git commit -m "..."
```

`git restore --staged --worktree` resets a single path to HEAD; useful when you know the foreign content is install noise (no value to preserve). `git stash push -u -- <path>` is safer when you can't tell — it preserves the foreign content for recovery while letting you commit cleanly.

### Recognize concurrent agent attribution signals

Before staging, scan for hints of foreign work:

```bash
git status --short
```

Signals that suggest concurrent activity:

- Files in directories you didn't touch this session (e.g., dirty `webui_app/` when you've been in `cli/`).
- Files modified very recently (`stat -f %m -t %Y-%m-%dT%H:%M:%S <file>`) outside your work window.
- Identical dirty content across multiple sibling worktrees (a find-and-replace tour — see [[foreign-agent-wip-spreads-across-worktrees]]).
- `.egg-info/` or `__pycache__/` recently touched (install ran in another session).

Any signal means you should stage by explicit path, never `-A`.

## Why This Matters

Mixed-attribution commits cause:

- **Blame confusion**: `git blame` points at your commit for foreign content, leading future agents to debug code you didn't write.
- **Hidden bugs**: foreign WIP is often mid-edit and incomplete; landing it as part of your commit propagates a half-done change.
- **Concurrent-agent breakage**: the other agent's `git status` no longer shows the WIP they were working on; they lose their state without notification.
- **PR review noise**: reviewers see unexpected files and ask "why are you touching this?"; the answer is "I didn't, but my `git add -A` did."

The cost of explicit staging is typing the file names. The cost of mis-attribution is investigation time, sometimes hours, sometimes a rollback.

This pairs with [[foreign-agent-wip-spreads-across-worktrees]] (recovery side: how to identify and protect WIP from concurrent agents) and [[multi-agent-turf-check-before-claiming-work]] (prevention side: confirm work ownership at session start).

## When to Apply

- Any commit in a worktree where multiple agents may operate (canonical `backlink-publisher/`, long-lived `bp-*/`).
- When `git status` shows files you don't remember editing.
- When the operator mentions other agent sessions running in parallel.
- During Claude/codex side-by-side workflows.

Skip when:

- You're the only agent on the repo right now and the operator confirms it.
- The worktree is a fresh scaffold with only your changes (no prior dirty state).

## Examples

**Right:**

```bash
$ git status --short
 M src/backlink_publisher/cli/foo.py
 M tests/test_foo.py
 M docs/changes.md
?? webui_app/scratch.tmp                # foreign: not mine
 M src/backlink_publisher/__init__.py.bak  # foreign: install noise

# Stage only mine
$ git add src/backlink_publisher/cli/foo.py tests/test_foo.py docs/changes.md
$ git status --short
M  src/backlink_publisher/cli/foo.py
M  tests/test_foo.py
M  docs/changes.md
?? webui_app/scratch.tmp
 M src/backlink_publisher/__init__.py.bak

$ git commit -m "fix: foo edge case"
# webui_app/scratch.tmp + __init__.py.bak left for their owner
```

**Wrong:**

```bash
$ git status --short
 M src/backlink_publisher/cli/foo.py
 M tests/test_foo.py
 M docs/changes.md
?? webui_app/scratch.tmp
 M src/backlink_publisher/__init__.py.bak

$ git add -A
$ git commit -m "fix: foo edge case"
# Commit now includes scratch.tmp (foreign WIP, possibly secrets) and a binary
# install-noise file. PR reviewer asks: "what is webui_app/scratch.tmp?"
# You don't know.
```

## Related

- `docs/solutions/workflow-issues/foreign-agent-wip-spreads-across-worktrees-2026-05-20.md` — recovery side: detecting and rescuing concurrent-agent WIP.
- `docs/solutions/workflow-issues/multi-agent-turf-check-before-claiming-work-2026-05-20.md` — prevention side: turf-check at session start.
- `docs/solutions/workflow-issues/scaffold-worktree-commit-before-writes-2026-05-20.md` — adjacent: atomic scaffold protects against concurrent cleanup.
- `git add` manual — semantics of `-A`, `.`, and explicit paths.
