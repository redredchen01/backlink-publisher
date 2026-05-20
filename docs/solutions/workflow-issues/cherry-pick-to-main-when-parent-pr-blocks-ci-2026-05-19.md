---
title: "Cherry-pick to main when parent PR blocks CI — supersede child PR with skipif gate"
date: 2026-05-19
category: docs/solutions/workflow-issues
module: PR landing workflow
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "Repo CI only runs on `base=main` (not on stacked feature branches)"
  - "A child PR is stacked on an unmerged parent PR that's not landing soon"
  - "Retargeting the child to main would create a DIRTY merge conflict"
related_components:
  - tooling
tags:
  - stacked-prs
  - parent-child-pr
  - cherry-pick
  - skipif-gate
  - ci-base-main
  - supersession
---

# Cherry-pick to main when parent PR blocks CI

## Context

`backlink-publisher`'s GitHub Actions CI only runs on PRs with `base=main`. A child PR stacked on an unmerged parent feature branch will sit indefinitely without CI signal because no CI workflow triggers for `base=feat/parent`.

The naive fix — retarget the child PR to `base=main` via `gh pr edit --base main` — fails when the child's diff includes parent-only context. GitHub marks the PR `DIRTY` with merge conflicts, and operators see a red banner that doesn't reflect any real conflict at the file level — the conflict is just "your diff assumes parent commits that aren't on main."

PR #77 (Telegraph channel, stacked on PR #75 velog adapter) hit this. PR #75 was still in review. Retargeting #77 to main showed DIRTY. The cleaner workaround was to **cherry-pick #77's 4 commits onto a fresh main-based worktree** and open PR #81 as a supersession, skipping CI for the parent-dependent tests via `skipif`. PR #81 squash-merged the same day; PR #77 closed as superseded.

## Guidance

### Decide: cherry-pick vs. wait

| Situation | Action |
|-----------|--------|
| Parent PR will merge in <1 day | Wait. Child gets CI automatically once parent lands. |
| Parent PR review is stuck for an open-ended reason | Cherry-pick. Don't let the child block on parent. |
| Child diff is small (<200 lines) and parent-independent in spirit | Cherry-pick is easy. Do it. |
| Child diff is large and tightly entangled with parent | Wait or split child. |
| Repo enforces strict stacking (parent must merge before child) | Wait — local convention overrides this guidance. |

`backlink-publisher` has no strict stacking convention; cherry-pick is acceptable when parent is genuinely stuck.

### Cherry-pick procedure

```bash
# 1. Create a fresh worktree off main for the supersession PR
git fetch origin main
git worktree add -b feat/<child>-onto-main bp-<child>-onto-main/ origin/main
cd bp-<child>-onto-main/

# 2. Cherry-pick the child's commits in order
git log feat/<child> --oneline ^origin/main  # confirm the commit range
git cherry-pick <first-sha>..<last-sha>

# 3. Resolve any cherry-pick conflicts (these are real diff conflicts, not the
#    parent-context conflicts that made GitHub mark the original PR DIRTY)

# 4. Gate parent-dependent tests behind skipif
# In each test file that needs parent functionality:
@pytest.mark.skipif(
    not _parent_feature_available(),
    reason="depends on PR #<parent>; will activate once parent merges",
)
def test_feature_that_needs_parent():
    ...

# 5. Push and open the supersession PR
git push -u origin HEAD
gh pr create --base main \
    --title "<child title> (supersedes #<original-PR>)" \
    --body "Supersedes #<original-PR>; cherry-picked onto main while parent #<parent-PR> is in review."

# 6. Close the original PR with a comment pointing at the supersession
gh pr close <original-PR> -c "Superseded by #<new-PR> — cherry-picked onto main for CI signal."
```

### `skipif` gate, not deletion

The parent-dependent tests must be **gated**, not deleted. Deleting them loses coverage for the eventual parent-merge moment; the operator who lands the parent has no signal that the child feature now needs activation.

```python
# tests/test_<feature>.py
def _parent_feature_available():
    """True when PR #<parent> functionality is present on main."""
    try:
        from backlink_publisher.<parent_module> import <parent_symbol>
        return True
    except ImportError:
        return False

@pytest.mark.skipif(
    not _parent_feature_available(),
    reason="depends on PR #<parent>",
)
def test_uses_parent_feature():
    ...
```

When the parent lands, the import succeeds, `skipif` flips off, and the tests activate automatically.

### Document the supersession

The new PR's description must:

1. Name the original PR being superseded.
2. List the cherry-picked commit SHAs (so reviewers can audit they match).
3. Name the parent PR that's still in review.
4. Note which tests are `skipif`-gated.

This prevents the next reviewer or agent from asking "wait, didn't this already get reviewed?"

## Why This Matters

The "wait for parent" pattern compounds cost:

- Child agent's context expires (memory ages, surrounding work moves on).
- Parent PR stuck on unrelated reasons (review backlog, design re-discussion, holiday) blocks an otherwise-ready child indefinitely.
- Stacked branches accumulate — three deep before someone notices.

The cherry-pick costs ~15 minutes for the typical child PR. The wait can cost days, and the cost of "child PR is stale when parent finally merges" is sometimes worse than starting over.

The `skipif` discipline is what makes the cherry-pick safe. Without it, the child PR either ships with broken tests (parent-dependent tests fail) or deletes test coverage (which is then forgotten when parent merges).

## When to Apply

- Stacked PR situation where parent has no clear landing ETA.
- Child PR has been ready for ≥24h waiting for parent.
- CI is genuinely blocked by `base=parent` not running workflows — confirm by checking GitHub Actions on the original PR.

Skip when:

- Parent is hours from landing.
- Child diff is genuinely tangled with parent (the cherry-pick would conflict on real content, not just on missing parent commits).
- Repo enforces strict stacking via branch protection or convention.

## Examples

**Right (PR #77 → PR #81, 2026-05-19):**

```
Setup:    PR #77 (Telegraph) stacked on PR #75 (velog adapter)
Block:    PR #75 in review, no ETA; CI base=main only
Try:      gh pr edit #77 --base main → DIRTY (expected — depends on velog code)
Pivot:    git worktree add bp-telegraph-onto-main off origin/main
          cherry-pick 4 commits from feat/telegraph
          add skipif gate to test_velog_canary (depends on PR #75)
          push → PR #81 → squash-merged `0318138` same day
          close PR #77 with "superseded by #81"
Outcome:  Telegraph landed in 6 hours instead of 6 days
```

**Wrong (counterfactual):**

```
Try:      wait for PR #75 to land
Reality:  PR #75 review reopened twice for design questions
          PR #77 sits 5 days
          surrounding work moves; PR #77 rebases needed twice
          eventual land cost: 2x the cherry-pick path
```

## Related

- PR #77 (closed) — original stacked PR.
- PR #81 (`0318138`) — supersession that landed.
- `docs/solutions/workflow-issues/late-plan-revisions-skip-code-2026-05-20.md` — adjacent: managing plan-vs-code divergence.
- `pytest.mark.skipif` documentation — the gate primitive.
- AGENTS.md → "CI" — confirms `base=main` is the only triggering branch.
