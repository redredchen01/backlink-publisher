---
title: "Validate `origin/main` for every file the plan references — feat branches lie about main"
date: 2026-05-19
category: docs/solutions/workflow-issues
module: docs/plans + plan grounding
problem_type: workflow_issue
component: development_workflow
severity: high
applies_when:
  - "Writing a plan based on files in the current working tree (not necessarily on main)"
  - "Plan-doc references specific source files, functions, or line numbers"
  - "Working out of a long-lived feat branch or stacked worktree"
related_components:
  - documentation
  - tooling
tags:
  - plan-grounding
  - origin-main
  - feat-branch-drift
  - cat-file-verify
  - upstream-refactor
  - plan-invalidation
---

# Validate `origin/main` for every file the plan references

## Context

When writing a plan from a feat-branch worktree (or any non-main branch), the agent reads files in the **local working tree**. Those files may not exist on `origin/main` — they could be:

- Created by the parent feat branch (and not yet merged).
- Renamed or moved by an upstream refactor that landed on main since the worktree was created.
- Collapsed into another file (e.g., monolith decomposition where `cli/velog_login.py` became an alias to `bind-channel`).
- Already deleted.

A plan that references `src/foo/bar.py:42` without verifying that file exists on main becomes invalid the moment the implementing agent tries to operate on it. On 2026-05-19, a 7-unit plan was written to fix a `setsid` bug in `browser_login.py`. The file **didn't exist on main** — the main-line PRs (#74 + #83) had moved that logic to a different `bind_job.py` shape. The entire plan was wasted.

## Guidance

### Pre-plan checklist: fetch + verify every referenced file

Before locking the plan, for every file path the plan references, confirm it exists on `origin/main`:

```bash
# Fetch fresh state from origin
git fetch origin main

# For each file the plan touches:
for path in \
    src/backlink_publisher/cli/browser_login.py \
    src/backlink_publisher/cli/_bind/recipes/medium.py \
    webui_app/templates/settings.html ; do
  if git cat-file -p "origin/main:$path" >/dev/null 2>&1; then
    echo "OK: $path exists on main"
  else
    echo "MISSING ON MAIN: $path"
  fi
done
```

If any path is `MISSING ON MAIN`, the plan needs revision before locking. The missing path means one of:

- The plan is referencing a file from the parent feat branch (not main). Either rebase the plan onto the parent feat branch context (and note the parent dependency explicitly) or rewrite the plan around files that exist on main.
- Upstream refactor has moved the file. Find the new location: `git log --all --oneline -- '<old-path>' | head -5` and `git log --all --oneline --diff-filter=R | grep '<old-name>'` to find renames.
- The file was deleted intentionally. The plan needs to operate on the replacement code path.

### Verify the bug exists on main, not just on your branch

A common related failure: the plan fixes a bug that's already been fixed on main. The bug exists in your worktree because your worktree is behind. The plan ships a duplicate fix, or worse, re-introduces the bug shape from before the upstream fix.

```bash
# Verify the bug is still present in the file on main
git cat-file -p "origin/main:src/path/to/file.py" | grep -nE 'pattern_of_bug'

# If empty: bug is already fixed on main; check git log to find the upstream fix
git log origin/main --oneline -- src/path/to/file.py | head -10
```

### Verify entry-points still exist

If the plan references CLI entry points (`backlink-publisher foo-cmd`), verify the entry-point declaration in `pyproject.toml` on main:

```bash
git cat-file -p origin/main:pyproject.toml | grep -A 20 '\[project.scripts\]'
```

Entry points are often renamed in monolith decomposition or restructuring. Plan references to renamed CLIs invalidate the entire plan unit.

## Why This Matters

Plans are expensive to write (deepening passes, document-review, codex challenge) and even more expensive to invalidate. A plan invalidated mid-implementation:

- Wastes the agent's research and planning context.
- Confuses reviewers who already engaged with the plan-doc.
- Often gets "salvaged" partially — the agent implements pieces that referenced files that *did* exist, and the result is incoherent.
- Leaves residue in `docs/plans/` that future agents have to recognize as stale.

The cost of the verification (~30 seconds of grep + cat-file) is negligible relative to the plan-writing investment. There is no upside to skipping it.

The 2026-05-19 wasted-plan example was particularly costly because the plan went through full deepening (7 units, document-review, codex challenge) before anyone tried to apply it. The `git cat-file -p origin/main:browser_login.py` check would have surfaced the issue in 5 seconds.

## When to Apply

- Every `/ce:plan` invocation.
- Especially when the worktree is a `bp-*/` sibling and the agent has been working in it for hours — the longer the session, the more likely main has drifted.
- When memory or prior notes are the source of file paths in the plan — memories age, files move, verify before trusting.
- Before the deepening pass — verifying file existence at this stage saves the deepening agent's work too.

Skip when:

- Plan operates on files the same agent created in this session (you know they exist because you wrote them).
- Plan operates only on conceptual entities, not specific file paths (rare — most plans cite paths).

## Examples

**Right (post-2026-05-19, hypothetical):**

```bash
# Before writing the plan:
$ git fetch origin main
$ git cat-file -p origin/main:src/backlink_publisher/cli/browser_login.py 2>&1
fatal: path 'src/backlink_publisher/cli/browser_login.py' does not exist in 'origin/main'

# Investigate:
$ git log --all --oneline -- src/backlink_publisher/cli/browser_login.py | head
abc1234 (origin/main) refactor: collapse browser_login into bind_job.py
def5678 feat: original browser_login.py

# Rewrite plan around bind_job.py on main, OR document parent-feat-branch dependency
```

**Wrong (2026-05-19 actual):**

```
1. Agent reads src/backlink_publisher/cli/browser_login.py in bp-velog-feat/
2. Spots a setsid bug
3. Writes 7-unit plan citing browser_login.py:42, :89, :130
4. document-review pass: 30 minutes
5. codex challenge: 20 minutes
6. Begin implementation → discover browser_login.py doesn't exist on main
7. Discover main has collapsed it into bind_job.py with completely different shape
8. Plan thrown out
```

## Related

- `docs/solutions/best-practices/grep-alleged-drift-sites-before-locking-framing-2026-05-19.md` — adjacent: verify "X is missing" claims per-site before planning.
- `docs/solutions/workflow-issues/cherry-pick-to-main-when-parent-pr-blocks-ci-2026-05-19.md` — adjacent: how to ship child work when parent isn't on main yet.
- `docs/solutions/logic-errors/git-cat-file-exits-128-not-1-2026-05-20.md` — relevant: `git cat-file` exits 128 (not 1) when path missing; the verification loop above needs to handle that.
- AGENTS.md "Sibling worktrees and editable installs" — broader worktree-staleness context.
