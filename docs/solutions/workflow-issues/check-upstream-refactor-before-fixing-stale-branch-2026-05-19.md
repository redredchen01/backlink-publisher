---
title: "Check `origin/<branch>:<path>` before fixing a bug on a long-lived feat branch — upstream may have collapsed the file"
date: 2026-05-19
category: docs/solutions/workflow-issues
module: feat-branch maintenance
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "Fixing a bug in a file on a stale feat branch (last touched >3 days ago)"
  - "About to cherry-pick a commit to a different branch"
  - "Pushing a commit and noticing the cherry-pick hunk applies empty"
related_components:
  - tooling
tags:
  - stale-branch
  - upstream-refactor
  - cherry-pick
  - cat-file-verify
  - file-collapse
  - hunk-empty
---

# Check upstream before fixing a stale-branch bug

## Context

Long-lived feat branches diverge from upstream refactors. A bug you spot in `bp-feat-foo/src/.../velog_login.py` may not exist anywhere on origin anymore — the file may have been collapsed into a different module, renamed, or deleted as part of a larger refactor that landed on main while your branch sat unmerged.

Committing the fix on your stale branch and cherry-picking to origin produces an **empty hunk**: the file the cherry-pick targets doesn't have the lines your fix modifies, so the change drops on the floor with no error message. The cherry-pick "succeeds" but applies nothing.

On 2026-05-19, an agent fixed two bugs in `velog_login.py` (logger setup + URL handling) on a stale feat branch. Cherry-picking to origin revealed the file had been collapsed into a `bind-channel` alias dispatch — both hunks applied empty, the fixes never reached origin, and the bugs (if they were still bugs after the refactor) remained.

## Guidance

### Before committing a fix on a stale branch: verify the bug still exists on origin

For each file the fix touches, check what's on origin:

```bash
git fetch origin <target-branch>

# Inspect the file as it exists on origin
git cat-file -p "origin/<target-branch>:<path>" | grep -nE 'pattern_of_bug'
```

Cases:

| Origin state | Action |
|--------------|--------|
| File exists, bug pattern present | Fix is needed. Proceed. |
| File exists, bug pattern absent (or refactored) | Bug was already fixed upstream. Drop your fix. |
| File doesn't exist on origin | File was collapsed/renamed. Find new location. |
| File exists but is a thin shim/alias | The bug — if still a bug — lives in the new module. Refactor your fix. |

The check is 2 commands, ~5 seconds. The cost of skipping it is a fix that silently doesn't apply.

### Finding the new location after a collapse

If `git cat-file` reports the file is missing or the pattern is gone:

```bash
# Find when the file was last touched
git log --all --oneline -- '<old-path>' | head -10

# Find renames
git log --all --diff-filter=R --oneline | grep '<old-name>'

# Search for moved content by a distinctive snippet
git log --all -S 'distinctive_code_snippet' --oneline
```

`git log -S` (the "pickaxe") is the most powerful: it finds commits that introduced or removed the snippet, so even content that moved across files surfaces.

### Push the fix only after verification

```bash
# 1. Fetch fresh upstream state
git fetch origin

# 2. Verify the bug exists in the actual upstream file you'll be cherry-picking to
for path in <paths-the-fix-touches>; do
  git cat-file -p "origin/main:$path" 2>&1 | head -50 | grep -nE '<bug-pattern>' \
    && echo "OK: bug present in $path on origin/main" \
    || echo "WARN: bug NOT in $path on origin/main — verify before cherry-picking"
done

# 3. Only then: cherry-pick + push
```

## Why This Matters

The failure mode is silent:

- `git cherry-pick` returns 0 (success) when a hunk applies as empty in context.
- The commit lands on the target branch with no new content.
- Your PR description claims the fix, but `git diff` shows no change in the relevant file.
- Tests pass (because nothing changed).
- Reviewer might catch it if they read the diff carefully; might not if they trust the commit message.

The fix-was-needed-on-stale-branch + fix-now-irrelevant-on-main case is even worse: you waste cycles fixing a bug that was already addressed by the refactor, and the cherry-pick noise pollutes commit history.

This pairs with [[validate-main-before-planning-off-feat-branch]] (same family: stale-state assumptions about origin). The other doc is about planning; this doc is about pre-commit verification of individual fixes.

## When to Apply

- Fixing a bug in a feat branch that's been unmerged for ≥3 days.
- Cherry-picking commits between branches.
- After running pytest in a sibling worktree and finding a failure — verify the failure is reproducible against `origin/main` before "fixing" it.
- When memory or notes say "fix bug X in file Y" — verify file Y still has the bug shape memory describes.

Skip when:

- Fix is local to your own session's work (you wrote the file this session, nothing to drift against).
- Branch is hours old, fast-moving repo.

## Examples

**Right:**

```bash
# Spot a bug in bp-velog-feat/src/.../velog_login.py
$ git fetch origin main
$ git cat-file -p origin/main:src/backlink_publisher/cli/velog_login.py 2>&1 | head -5
fatal: path 'src/.../velog_login.py' does not exist in 'origin/main'

# Investigate: file collapsed?
$ git log --all --oneline -- src/backlink_publisher/cli/velog_login.py | head
abc1234 refactor: collapse velog_login into bind-channel alias
def5678 feat: velog_login.py initial

# Find where the equivalent logic lives now
$ git cat-file -p origin/main:src/backlink_publisher/cli/_bind/recipes/velog.py | grep -nE 'logger'
# → 23 occurrences; that's the new home

# Refactor your fix to target the new file before committing
```

**Wrong (2026-05-19 actual):**

```
1. Spot logger setup + URL handling bugs in bp-velog-feat/velog_login.py
2. Commit both fixes
3. cherry-pick to origin → both hunks apply EMPTY
4. push → no actual change
5. Open PR claiming fix → reviewer reads diff, asks "where's the fix?"
6. Discover refactor collapsed velog_login.py weeks ago
7. Hunks were targeting non-existent lines on the new file
```

## Related

- `docs/solutions/workflow-issues/validate-main-before-planning-off-feat-branch-2026-05-19.md` — same family: verify origin state before plan-time decisions.
- `docs/solutions/logic-errors/git-cat-file-exits-128-not-1-2026-05-20.md` — relevant: `cat-file` exit code semantics when path is missing.
- `git log -S` documentation — the pickaxe for finding moved content.
- AGENTS.md → "Sibling worktrees and editable installs" — broader stale-state context.
