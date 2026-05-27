---
title: "plan-claims-gate: use claims: {} opt-out for pre-merge plans with net-new files"
date: 2026-05-26
category: docs/solutions/workflow-issues
module: plan-check
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - Plan doc is dated 2026-05-20 or later (post grandfather cutoff)
  - Plan creates at least one net-new file that does not yet exist on origin/main
  - plan-claims-gate CI exits 8 (missing claims block) or 7 (drift)
tags: [plan-check, ci, claims, frontmatter, plan-doc, grandfather-cutoff]
---

# plan-claims-gate: use claims: {} opt-out for pre-merge plans with net-new files

## Context

`plan-claims-gate` is a CI check that runs `plan-check` on every plan doc touched
by a PR. Any plan doc dated 2026-05-20 or later (post grandfather cutoff) must
include a `claims:` block in its YAML frontmatter.

PR #235 (`feat/events-kind-contract`) demonstrated a two-step failure pattern:
1. Plan doc had no `claims:` block → CI exited 8 ("missing claims block")
2. Adding `claims.paths` listing the five net-new source files → CI exited 7 ("drift")

The exit 7 after the "fix" surprised: the paths listed were real files the branch
creates. The confusion came from a design assumption: `plan-check` validates
`claims.paths` against files that exist on `origin/main`, **not** on the feature
branch.

## Guidance

The `plan-claims-gate` has two distinct failure modes that look like a fixable
progression but require the same root solution.

**exit 8 — missing claims block**

Any plan doc dated 2026-05-20 or later (grandfather check is strict `< 2026-05-20`)
must have a `claims:` key. Add an explicit opt-out:

```yaml
claims: {}
```

**exit 7 — claims drift (net-new files)**

`plan-check` validates `claims.paths` by checking whether each path exists on
`origin/main`. For a pre-merge feature branch that creates net-new files, those
files do not yet exist on main. Listing them in `claims.paths` always produces
exit 7 drift until the PR is merged. The gate is intentionally designed this way —
it tracks drift of *already-merged* artifacts.

Fix is the same: use `claims: {}` opt-out. `plan-check` source treats
`len(paths) == 0 and len(shas) == 0` as explicit opt-out and exits 0.

**General rule for pre-merge branches**

Use `claims: {}` unless the plan *only* modifies files that already exist on
`origin/main` AND you have merged SHAs to reference. If the plan creates any
net-new file, `claims: {}` is the correct form.

Add a comment to communicate intent:

```yaml
# claims: {} — explicit opt-out. This plan creates net-new files that do
# not exist on origin/main yet; path/SHA drift validation does not apply
# pre-merge.
claims: {}
```

Verify locally before push:

```bash
plan-check docs/plans/<your-plan>.md; echo $?   # expect 0
```

## Why This Matters

Without understanding the `origin/main` validation rule, an engineer hitting exit 7
after "fixing" exit 8 will loop through multiple CI attempts. Each attempt pushes a
commit, burns CI minutes, and delays the PR. The two errors look like a progression
(8 → 7) that tempts iterative patching, but the correct fix for both is identical.

Listing net-new paths in `claims.paths` also creates future noise: once those files
land on main, the block becomes a stale artifact that reviewers may try to reconcile
with actual merge SHAs, adding unnecessary drift-check churn on follow-up PRs.

## When to Apply

- Any plan doc dated 2026-05-20 or later being opened as a PR before merge.
- Any plan that creates at least one net-new file (file not on `origin/main`).
- Any plan modifying existing files for which you don't have merged SHAs yet.
- When CI log shows: `plan-check: missing claims block` (exit 8).
- When CI log shows: `paths_missing: <path> — N paths missing` (exit 7) after
  you already added a `claims.paths` block.

**Do NOT use `claims: {}` when:** the plan is a post-mortem or amendment to an
already-merged change and you want to pin specific merged SHAs for drift tracking.
In that case, list real merged paths and SHAs.

## Examples

**Before — exit 8 (missing claims block entirely):**

```yaml
---
title: "feat: events.db Kind & Classification Contract"
type: feat
status: active
date: 2026-05-26
---
```

CI output:
```
plan-check: missing claims block — plan-doc post-cutoff requires a `claims:` block
##[error]plan-check failed with exit 8
```

---

**After failed attempt — exit 7 (drift, net-new paths listed):**

```yaml
claims:
  paths:
    - src/backlink_publisher/events/kinds.py   # net-new: not on origin/main yet
    - src/backlink_publisher/events/store.py
  shas: []
```

CI output:
```
paths_missing: src/backlink_publisher/events/kinds.py — 1 paths missing, 0 shas unreachable on origin/main
##[error]plan-check failed with exit 7
```

---

**Correct fix — exit 0 (explicit opt-out):**

```yaml
# claims: {} — explicit opt-out. This plan creates net-new files that do
# not exist on origin/main yet; path/SHA drift validation does not apply pre-merge.
claims: {}
```

Local verification:
```
$ PYTHONPATH=src plan-check docs/plans/2026-05-26-001-feat-events-db-kind-contract-plan.md
$ echo $?
0
```

---

**Decision tree:**

```
Is plan doc date < 2026-05-20?
  YES → grandfathered, no claims block needed
  NO  →
    Does plan ONLY modify files already on origin/main
    AND do you have their merged SHAs?
      YES → claims: { paths: [...], shas: ["<sha1>"] }
      NO  → claims: {}   ← covers net-new files AND pre-merge state
```

## Related

- `docs/solutions/test-failures/pyyaml-int-coerces-all-digit-sha-2026-05-20.md` — adjacent YAML frontmatter trap: all-digit 7-char SHAs get int-coerced unless quoted; plan-check claims.shas needs quoted entries
- `docs/solutions/workflow-issues/late-plan-revisions-skip-code-2026-05-20.md` — adjacent plan-vs-code drift: plan doc changes not landing in code; also covers running `plan-check` locally
- GitHub issue #137 (radar issue tracking plan-claims drift state since 2026-05-20)
- PR #235 commit `229786b` — working fix for the exit 8→7→0 progression documented here
- PR #113 / PR #115 — first documented exit 8 instance (url-derive v1.0, dated 2026-05-20), fixed with `claims: {}` in follow-up
