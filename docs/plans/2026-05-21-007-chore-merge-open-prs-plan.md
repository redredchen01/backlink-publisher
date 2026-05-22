---
title: "chore: Land open PRs — #160 direct merge + chrome-cdp stack rebase (#155/#157/#158/#159)"
type: chore
status: completed
date: 2026-05-21
claims: {}
---

# chore: Land open PRs — #160 direct merge + chrome-cdp stack rebase

## Overview

Five open PRs are ready (or near-ready) to land. PR #160 is clean; four
chrome-cdp unit PRs (#155/#157/#158/#159) need a sequential rebase because
the stack was built before units 1/2/4a were squash-merged to main.

One additional branch (`feat/webui-ia-phase-b`) has a single unmerged commit
that needs a PR created. One branch (`feat/hashnode-browser-bind`) is blocked
and must be held.

## Scope Boundaries

- Only PRs / branches listed here
- No new feature work, no WebUI restyling, no plan 013/016 scope
- `feat/hashnode-browser-bind` stays blocked (stealth spike gated)

## Current State of Open PRs

| PR | Branch | Status | Action |
|----|--------|--------|--------|
| #160 | `fix/verify-non-ascii-url` | MERGEABLE, CI PASS | merge now |
| #155 | `feat/chrome-cdp-unit3-hashnode` | CONFLICTING, no CI | rebase → push → CI → merge |
| #157 | `feat/chrome-cdp-unit4b-devto` | CONFLICTING, no CI | after #155, rebase → push → CI → merge |
| #158 | `feat/chrome-cdp-unit4c-mastodon` | CONFLICTING, no CI | after #157, rebase → push → CI → merge |
| #159 | `feat/chrome-cdp-unit5-webui-pill` | CONFLICTING, no CI | after #158, rebase → push → CI → merge |

## Conflict Analysis

All four CONFLICTING PRs share the same root cause: units 1/2/4a (velog) were
squash-merged to `main` individually **after** the stack branches were cut.

The only file with a real text conflict is
`src/backlink_publisher/publishing/adapters/__init__.py`. The conflict is
purely additive: main added velog recipe imports; each unit PR adds its own
platform's recipe import to the same region. Resolution = keep all imports.

`registry.py` and `browser_publish/` submodules were also modified by both
sides but they change different regions — auto-resolved once `__init__.py` is
manually resolved.

```
Base (6f4b29d / Unit 0 spike) ──┬── main: squash-merged units 1+2+4a+authfix
                                  └── stack tip: has units 2+4a stacked inline
                                      + unique Unit 3/4b/4c/5 commits
```

## Key Technical Decisions

- **Cherry-pick unique commits, not full rebase**: Each PR branch carries
  duplicate commits from earlier merged units. The cleanest path is to
  cherry-pick only the unique commit from each PR onto the growing main, then
  force-push to the PR branch. This avoids replaying already-merged content.

- **Sequential order**: #155 → #157 → #158 → #159 (dependency order, not
  alphabetical). Each CI run is short (~90s).

- **webui-ia-phase-b needs a PR**: The 1-commit branch
  (`e05fde7`, Plan 012 Phase B-1 single/batch toggle) is production-ready
  (3 files, 186 additions, tests included) but has no open PR. Create PR and
  let CI run before merging.

## Open Questions

### Deferred to Implementation

- **#159 webui-pill CI**: This PR touches WebUI templates; CI only runs
  `pytest` (not browser). Verify manually that the pill renders correctly
  before merge if time permits.
- **webui-ia-phase-b**: Confirm with user whether Phase B-1 is intentionally
  held or was missed. If held, skip this unit.

## Implementation Units

- [x] **Unit 1: Merge PR #160 (non-ASCII URL fix)**

**Goal:** Land the clean, CI-passing bug fix immediately.

**Requirements:** Zero-risk — MERGEABLE + CI PASS on both 3.11 and 3.12.

**Dependencies:** None.

**Files:** none — no local edits; merge via GitHub.

**Approach:**
- `gh pr merge 160 --squash --delete-branch`
- Confirm state: `gh pr view 160 --json state`

**Test scenarios:**
- Test expectation: none — CI already green; merge is safe.

**Verification:**
- `gh pr view 160 --json state` returns `MERGED`.

---

- [x] **Unit 2: Rebase + land PR #155 (chrome-cdp Unit 3 hashnode)**

**Goal:** Isolate the unique hashnode commit and rebase it cleanly onto main.

**Dependencies:** Unit 1 complete (main must be stable).

**Files:**
- Modify (conflict resolve): `src/backlink_publisher/publishing/adapters/__init__.py`
- Touch (auto-resolve): `src/backlink_publisher/publishing/registry.py`
- New (no conflict): `src/backlink_publisher/publishing/browser_publish/recipes/hashnode.py`, `_hashnode_selectors.py`
- Worktree: `bp-chrome-cdp-unit3/`

**Approach:**
1. In `bp-chrome-cdp-unit3/`, cherry-pick only `de702aa` onto `origin/main`
2. Resolve `adapters/__init__.py`: keep velog import (from main) **and** add hashnode import (from cherry-pick)
3. Force-push to `feat/chrome-cdp-unit3-hashnode`
4. Wait for CI to pass (plan-claims-gate + test 3.11/3.12)
5. `gh pr merge 155 --squash --delete-branch`

**Patterns to follow:**
- `adapters/__init__.py` recipe import block: see existing velog import at line ~48-49 for style
- `register("hashnode", ...)` call already present in branch; conflict is only the import line

**Test scenarios:**
- Happy path: CI passes after force-push and `test_browser_publish_hashnode.py` green
- Conflict resolution correctness: `python -m py_compile src/backlink_publisher/publishing/adapters/__init__.py` exits 0 after rebase

**Verification:**
- `gh pr view 155 --json state` returns `MERGED`.
- `git log --oneline origin/main -1` shows the hashnode squash commit.

---

- [x] **Unit 3: Rebase + land PR #157 (chrome-cdp Unit 4b devto)**

**Goal:** Cherry-pick devto unique commit onto main after #155 merged.

**Dependencies:** Unit 2 complete.

**Files:**
- `src/backlink_publisher/publishing/adapters/__init__.py` (devto import + register block)
- `src/backlink_publisher/publishing/browser_publish/recipes/devto.py`, `_devto_selectors.py` (new)
- Worktree: `bp-chrome-cdp-unit4b/`

**Approach:**
1. Fetch latest main (includes hashnode merge from Unit 2)
2. Cherry-pick `478f2d8` onto `origin/main`
3. `adapters/__init__.py`: main now has velog+hashnode; add devto import. No conflict expected.
4. Force-push → CI → merge #157

**Test scenarios:**
- Happy path: `test_browser_publish_dispatcher.py` covers devto dispatch, passes CI

**Verification:**
- `gh pr view 157 --json state` = MERGED.

---

- [x] **Unit 4: Rebase + land PR #158 (chrome-cdp Unit 4c mastodon)**

**Goal:** Cherry-pick mastodon unique commit onto main after #157 merged.

**Dependencies:** Unit 3 complete.

**Files:**
- `src/backlink_publisher/publishing/adapters/__init__.py` (mastodon import + register)
- `webui_app/` config plumbing (mastodon instance URL field)
- `src/backlink_publisher/publishing/browser_publish/recipes/mastodon.py`, `_mastodon_selectors.py`
- Worktree: `bp-chrome-cdp-unit4c/`

**Approach:**
- Cherry-pick `6d677b2` onto updated main
- Resolve any overlap with webui config routes (unlikely — mastodon is a new channel)
- Force-push → CI → merge #158

**Test scenarios:**
- Happy path: mastodon recipe unit tests pass, config plumbing renders in webui

**Verification:**
- `gh pr view 158 --json state` = MERGED.

---

- [x] **Unit 5: Rebase + land PR #159 → #163 (chrome-cdp Unit 5 webui-pill)**

**Goal:** Cherry-pick the webui publish-backend pill commit onto main after all recipe PRs merged.

**Dependencies:** Unit 4 complete (all recipe channels must be in main for pill to render correctly).

**Files:**
- `webui_app/templates/` (pill UI)
- `tests/test_webui_route_contract.py` or similar
- Worktree: `bp-chrome-cdp-unit5/`

**Approach:**
- Cherry-pick `9677be0` onto updated main
- Likely clean (webui template files not touched by recipe PRs)
- Force-push → CI → merge #159

**Test scenarios:**
- Happy path: CI passes (pytest webui route contract tests)
- Optional: manual browser smoke — pill renders and nofollow warning shows for devto/mastodon

**Verification:**
- `gh pr view 159 --json state` = MERGED.

---

- [x] **Unit 6: Create PR + land `feat/webui-ia-phase-b` (Plan 012 Phase B-1)**

**Goal:** Open PR for the single-commit single/batch mode toggle that has been sitting unmerged.

**Dependencies:** Units 1-5 complete (or can proceed in parallel — no file overlap with chrome-cdp work).

**Files:**
- `webui_app/templates/index.html` (+52 lines)
- `webui_app/static/js/mode_toggle.js` (+93 lines, new)
- `tests/test_webui_route_contract.py` (+47 lines)
- Worktree: `bp-webui-ia-phase-b/` (outside the main workspace root — at `/Users/dex/YDEX/INPORTANT WORK/外链/bp-webui-ia-phase-b/`)

**Approach:**
- Confirm with user this branch is ready to land (not intentionally held)
- `gh pr create --head feat/webui-ia-phase-b --base main --title "feat(webui): Plan 012 Phase B-1 — single/batch mode toggle replaces 批量 nav tab"`
- Wait for CI → merge

**Test scenarios:**
- Happy path: `test_webui_route_contract.py` additions pass
- Manual: toggle renders in browser, single mode hides batch inputs

**Verification:**
- `gh pr view <N> --json state` = MERGED.

---

## Branches to Leave Alone

| Branch | Reason |
|--------|--------|
| `feat/hashnode-browser-bind` | Plan 016 Tranche B gated on stealth spike validation (`bp-stealth-spike-validate/`). Do not merge or close. |
| `spike/hashnode-stealth-validate` | Spike docs only — not production code, never merge |
| `spike/chrome-cdp-unit0` | Already merged via #141/#144, branch just lingers |
| `docs/plan-chrome-cdp-multi-channel-publish` | Doc branch, never merge to main |

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Cherry-pick SHA wrong (picking merge commit not squash tip) | Verify `git log --oneline` tip before cherry-pick |
| CI flake on `PYTHONHASHSEED=0` footprint gate | Re-run CI once; footprint gate is deterministic on same hash seed |
| `webui-ia-phase-b` intentionally held | Ask user in Unit 6 before creating PR |
| Stale worktrees conflict with cherry-pick operations | Use `bp-chrome-cdp-unit3/` etc. as working dirs; fetch before each unit |

## Sources & References

- Related PRs: #155 #157 #158 #159 #160
- Chrome CDP plan: `docs/plans/2026-05-21-001-feat-chrome-cdp-multi-channel-publish-plan.md`
- WebUI IA plan: `docs/plans/2026-05-20-012-feat-webui-ia-phase-a-b1-plan.md` (approx)
- Conflict file: `src/backlink_publisher/publishing/adapters/__init__.py`
