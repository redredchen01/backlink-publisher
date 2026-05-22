---
date: 2026-05-21
topic: retire-feat-bind-medium-pipeline-repair
---

# Retire `feat/bind-medium-pipeline-repair` + Salvage Delta

## Problem Frame

Canonical worktree (`backlink-publisher/`) sits on `feat/bind-medium-pipeline-repair` with **16 commits ahead / 27 commits behind origin/main** and **58 dirty files** (46 tracked modify + 3 deleted + 4 untracked plan/brainstorm docs). The branch started 2026-05-19 as a medium-bind pipeline repair, but over 48 hours main accumulated 15+ PRs covering the same subject matter — Chrome/CDP backend (#138), browser publish dispatcher (#147/#149), telegraph chrome (part of #138), velog chrome (#152), hashnode chrome (#155), Write.as content-blocked (#146), registry+config import paths (#161), non-ASCII URL normalize (#160), publish false-success fix (#156), etc.

`git cherry origin/main HEAD` shows all 16 commits as patch-id-novel, but the **subject matter is functionally already on main via different patches**. Cherry-picking will conflict file-by-file because main's restructures (`#161` registry/config, `#149` BrowserPublishDispatcher) collided with the same files. The branch is structurally retired-in-fact but not retired-in-name.

Compounding: 4 untracked docs in `docs/brainstorms/` and `docs/plans/` from 2026-05-21 reference work that already shipped via main:
- `docs/plans/2026-05-21-003-feat-canonical-contract-and-platform-expansion-plan.md` — belongs to `bp-canonical-contract` worktree (branch `feat/canonical-contract` still active)
- `docs/plans/2026-05-21-004-fix-webui-publish-false-success-plan.md` — belongs to `bp-fix-publish-false-success`; PR #156 already merged
- `docs/plans/2026-05-21-005-fix-verify-non-ascii-url-ascii-codec-plan.md` — belongs to `bp-fix-verify-ascii`; PR #160 already merged
- `docs/brainstorms/2026-05-21-canonical-contract-and-platform-expansion-requirements.md` — belongs to `bp-canonical-contract`

These docs are stranded in canonical instead of their target worktrees.

## Requirements

**Snapshot**
- R1. Stash all 58 dirty entries (`git stash push -u -m "..."`) before any branch-state mutation — protect against `[[worktree-concurrent-switching]]` losses.
- R2. Record the stash SHA + branch tip SHA + current `git status` to a salvage log under workspace root (not in canonical's tracked tree, to avoid creating new WIP during cleanup).

**Delta forensics**
- R3. For each of the 16 ahead commits, produce a per-commit comparison: (a) which file(s) it touches; (b) for each file, whether the relevant change is already present on origin/main (semantic check, not patch-id); (c) classify as `already-shipped` / `partial-delta-novel` / `wholly-novel`.
- R4. Output of R3 is a checklist of genuine deltas worth salvaging — expected size ≤5 small PRs based on initial scan (likely candidates: medium login Playwright crash fix `a5361f6`, hashnode UI-blocker rationale doc note `16b263a`, article_urls normalize `acbea6a`, possibly Telegraph `_settings_context` polish).

**Plan-doc migration**
- R5. Move the 4 untracked plan/brainstorm docs from canonical to their target worktree, commit there atomically:
  - `2026-05-21-003-...-plan.md` → `bp-canonical-contract/docs/plans/`
  - `2026-05-21-004-...-plan.md` → `bp-fix-publish-false-success/docs/plans/`
  - `2026-05-21-005-...-plan.md` → `bp-fix-verify-ascii/docs/plans/`
  - `2026-05-21-canonical-contract-...-requirements.md` → `bp-canonical-contract/docs/brainstorms/`
- R6. For each target worktree, if its corresponding PR is already merged (#156, #160), the moved plan doc gets a `claims: {}` frontmatter opt-out (per `[[plan-doc-on-cutoff-needs-claims-block]]`) and a one-line note that the plan is historical record only.

**Salvage PRs**
- R7. For each genuine delta from R3/R4, open a separate PR off freshly-fast-forwarded origin/main with a single-theme scope. PR title must NOT reuse the dead branch name; pick descriptive theme names (`fix/medium-login-playwright-lifecycle`, `feat/publish-article-urls-normalize`, etc.).
- R8. Each salvage PR includes a brief PR description noting its provenance from the retired branch (one line, no exhaustive history).

**Retirement**
- R9. After R5-R8 complete, on canonical: `git fetch && git checkout main && git pull --ff-only` to land canonical on origin/main HEAD. `git branch -D feat/bind-medium-pipeline-repair` locally. `git push origin :feat/bind-medium-pipeline-repair` to delete remote ref (no associated open PR — `gh pr list --head feat/bind-medium-pipeline-repair` confirmed empty).
- R10. `pip install -e ".[dev]"` re-run in canonical to refresh egg-info noise. Confirm `git status` clean.
- R11. Cleanup stash from R1 once R3-R10 verified — `git stash drop` only after the salvage PRs are open and the moved plan docs are committed in their target worktrees.

## Success Criteria

- `backlink-publisher/` on `main` branch at origin/main HEAD, `git status` clean.
- `feat/bind-medium-pipeline-repair` branch gone locally and remotely.
- Each genuine delta from R3 is either: (a) opened as a single-theme PR against current main, or (b) explicitly recorded as dropped with a one-line reason in the salvage log.
- The 4 stranded plan/brainstorm docs are committed in their target worktrees (not orphaned).
- No accidental loss: R2 salvage log + R1 stash provide a recovery path until R11.

## Scope Boundaries

- Not refactoring/rebasing the other 20+ `bp-*/` worktrees (separate cleanup, deferred).
- Not migrating salvaged plan docs to `docs/solutions/` (promotion is a separate lessons-capture workflow, and AGENTS.md says brainstorm/plan docs contain operator names that must never propagate to solutions).
- Not addressing potential `local main` staleness on dormant worktrees like `bp-launcher-self-heal` (it shows `[main]` at an old SHA — out of scope here; will surface separately when those worktrees get reused).
- Not promoting `feat/canonical-contract` work in `bp-canonical-contract` — only the docs migrate; whether to ship that feature is a separate decision.

## Key Decisions

- **Retire, do not rebase or cherry-pick**: rebase will conflict on every restructured file (registry, config, helpers, adapters); cherry-pick will duplicate already-shipped subject matter. Salvaging only the additive delta is cheaper and safer than fighting 27 commits of main divergence.
- **Plan docs go to their target worktree, not stay in canonical**: matches the actual ownership of the work; avoids canonical becoming a graveyard of completed-elsewhere plan docs.
- **`claims: {}` for retroactive plan docs**: PR #156 / #160 already merged means the plan doc's SHAs won't appear in `plan-check` drift detector; opt-out per `[[plan-doc-on-cutoff-needs-claims-block]]`.
- **Stash, not commit-WIP**: do not commit dirty WIP onto the dying branch — only stash. Stash survives `branch -D`; commit-WIP would die with the branch.

## Dependencies / Assumptions

- No other claude/agent session is concurrently mutating canonical's HEAD (per `[[ce-work-must-check-concurrent-rebase-before-commit]]` — verify `git rev-parse HEAD` + `status --short` immediately before R1 and R9).
- The 3 target `bp-*/` worktrees (`bp-canonical-contract`, `bp-fix-publish-false-success`, `bp-fix-verify-ascii`) still exist and are on their expected branches (verified 2026-05-21).
- No open PR uses `feat/bind-medium-pipeline-repair` as base (verified via `gh pr list --base feat/bind-medium-pipeline-repair` — empty; confirms safe to delete remote ref).

## Outstanding Questions

### Deferred to Planning

- [Affects R4][Needs research] How granular should the per-commit delta analysis be — full `git diff` per file, or is `git log --oneline -S<symbol>` enough to confirm a feature already landed?
- [Affects R7][Technical] Should each salvage PR include `radon` SLOC re-measurement if it touches a `monolith_budget.toml`-tracked file, or defer monolith re-check to a final pass?
- [Affects R5][Technical] When a plan doc moves to a target worktree whose PR already merged, should it land on `main` of that worktree (via fresh branch) or just commit-and-push on the existing feature branch?

## Next Steps

→ `/ce:plan` for structured implementation planning
