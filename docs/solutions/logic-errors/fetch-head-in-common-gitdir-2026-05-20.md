---
title: "FETCH_HEAD lives in the common gitdir, not `.git/FETCH_HEAD` — use `git rev-parse --git-common-dir`"
date: 2026-05-20
category: docs/solutions/logic-errors
module: scripts + cli helpers that read FETCH_HEAD
problem_type: logic_error
component: tooling
symptoms:
  - "Script works in `backlink-publisher/` but fails with `No such file or directory: bp-foo/.git/FETCH_HEAD`"
  - "Same script reads stale FETCH_HEAD content in some worktrees"
  - "Tests pass locally in the canonical repo, fail when run from a sibling `bp-*/` worktree"
root_cause: wrong_api
resolution_type: code_fix
severity: medium
related_components:
  - testing_framework
tags:
  - git-worktree
  - fetch-head
  - common-gitdir
  - bp-worktrees
  - linked-worktree
  - script-portability
---

# FETCH_HEAD lives in the common gitdir, not `.git/FETCH_HEAD`

## Problem

Linked git worktrees (created via `git worktree add`) keep `FETCH_HEAD` in the **common gitdir** — the directory shared with the original repo — **not in the worktree's per-tree `.git/` directory**. Scripts and helpers that hard-code `.git/FETCH_HEAD` (or any path-relative-to-`.git/`) work in the canonical worktree and break in every sibling.

`backlink-publisher/` has 20+ `bp-*/` linked worktrees at any time. A FETCH_HEAD reader that hard-codes `.git/FETCH_HEAD` is broken for 95% of the workspace.

## Symptoms

- `FileNotFoundError: bp-foo/.git/FETCH_HEAD` from any script touching FETCH_HEAD when run from a `bp-*/` worktree.
- Stale FETCH_HEAD reads — the worktree has its own `.git/` directory (it does — it's a small file with a `gitdir:` pointer), but FETCH_HEAD isn't there. Some readers might find an old or partial file from a previous failed read.
- Tests pass in `backlink-publisher/` and fail in `bp-anything/`.
- CI green if jobs always run from the canonical worktree; flaky if a job ever happens to land in a sibling.

## What Didn't Work

- **Reading `<worktree>/.git/FETCH_HEAD`**. Caught by feasibility-reviewer in PR #98 doc-review before code landed — the linked-worktree case had no fallback.
- **Trying `<worktree>/.git/worktrees/<name>/FETCH_HEAD`**. Per-worktree gitdirs exist but FETCH_HEAD specifically is not duplicated there; git centralizes it.
- **Symlinking `<worktree>/.git/FETCH_HEAD` to the common dir's copy**. Fragile, breaks on worktree migration, doesn't survive `git worktree repair`.

## Solution

Use `git rev-parse --git-common-dir` to discover the right path:

```python
import subprocess
from pathlib import Path

def _fetch_head_path(cwd: Path | str | None = None) -> Path:
    """Return the FETCH_HEAD path for the worktree at `cwd` (or CWD)."""
    common_dir = subprocess.check_output(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=cwd,
        text=True,
    ).strip()
    return Path(common_dir) / "FETCH_HEAD"
```

```bash
# Shell equivalent
FETCH_HEAD_PATH="$(git rev-parse --git-common-dir)/FETCH_HEAD"
```

`git rev-parse --git-common-dir` returns the canonical-worktree's `.git/` for both:

- The canonical worktree itself (where it equals `--git-dir`).
- Linked worktrees (where `--git-dir` is `<worktree>/.git/worktrees/<name>/` but `--git-common-dir` is the original).

This is the same primitive `git fetch` itself uses to decide where to write FETCH_HEAD, so it's guaranteed consistent.

## Why This Works

Git's worktree model distinguishes per-worktree state (HEAD, index, working tree) from repo-wide state (objects, refs, remotes, FETCH_HEAD, ORIG_HEAD). FETCH_HEAD is repo-wide because a fetch is a remote operation that updates remote-tracking refs — those are shared across all worktrees. Hard-coding `.git/FETCH_HEAD` works in the canonical worktree only by coincidence: in that case `--git-dir == --git-common-dir`.

The split is documented but easy to miss because most scripts develop in the canonical worktree, where the bug never fires. The bug is purely structural — there's no runtime behavior in the canonical worktree that would catch it.

## Prevention

- **Lint pattern**: grep for `.git/FETCH_HEAD`, `.git/ORIG_HEAD`, `.git/packed-refs` in scripts and source — all three live in the common gitdir, all three hit the same bug.

```bash
grep -rn "\.git/\(FETCH_HEAD\|ORIG_HEAD\|packed-refs\)" scripts/ src/ tests/
```

- **Reviewer checklist for tooling PRs**: when a PR touches git plumbing, ask "does this work from a linked worktree?" Answer must be backed by a test run from `bp-*/`, not a code-review intuition.
- **CI matrix**: run at least one job from a linked worktree (via `git worktree add` in the CI setup step) so the bug is caught structurally.
- **Documentation review check**: PR #98 caught this at doc-review time via feasibility-reviewer. Keep the feasibility-reviewer in any PR that adds git plumbing.

The "same family" siblings — `ORIG_HEAD`, `MERGE_HEAD`, `CHERRY_PICK_HEAD`, `packed-refs`, `config` — also live in the common dir. The same `--git-common-dir` rewrite fixes all of them.

## Related Issues

- PR #98 — original plan-claims-gate where feasibility-reviewer flagged this before code landed.
- `man gitrepository-layout` — formal documentation of per-worktree vs common files.
- `docs/solutions/workflow-issues/foreign-agent-wip-spreads-across-worktrees-2026-05-20.md` — adjacent worktree-specific failure mode.
- AGENTS.md → "Sibling worktrees and editable installs" — broader worktree handling guidance.
