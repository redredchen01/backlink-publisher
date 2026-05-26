---
title: "refactor: remove dead _impl orphan adapter files (N1)"
type: refactor
status: active
date: 2026-05-26
claims:
  paths:
    - src/backlink_publisher/publishing/adapters/_telegraph_api_impl.py
    - src/backlink_publisher/publishing/adapters/_velog_graphql_impl.py
  shas: []
---

# refactor: remove dead `_impl` orphan adapter files (N1)

## Overview

Delete two unreferenced, stale near-duplicate adapter modules that were
accidentally materialised into the tree by the #236 phase1 snapshot/revert
churn (`1808fe4` snapshot → `637e5d4` revert → `4852ee0`). Pure deletion,
zero behavioural change, ~1183 lines removed.

- `src/backlink_publisher/publishing/adapters/_telegraph_api_impl.py` (602 lines)
- `src/backlink_publisher/publishing/adapters/_velog_graphql_impl.py` (581 lines)

This is item **N1** from the 2026-05-26 code-quality stocktake (the
highest-leverage, zero-collision item: high impact, small effort).

## Problem Frame

`adapters/` carries two `_impl` files that no code imports. The registry and
all call sites import from the canonical `telegraph_api.py` / `velog_graphql.py`.
The `_impl` copies are 96% identical to the canonical files but **strictly
poorer** — they are stale variants missing functionality the canonical files
have. They inflate the tree, confuse `radon cc` / readers, and present a trap
where a future dev could wire the inferior copy by mistake.

## Requirements Trace

- R1. Remove both `_impl` orphan files with no loss of functionality.
- R2. Repo compiles and the full test suite stays green after removal.
- R3. No collision with the concurrently-active session working the main worktree.

## Scope Boundaries

- **Only** the two named `_impl` files are deleted. No edits to the canonical
  `telegraph_api.py` / `velog_graphql.py`, the registry, or anything else.
- Not touching `scripts/remove-writeas.py` (its reference is inert — see below).
- Not adding a regrowth-guard test (see Deferred to Implementation).
- Not part of, and must not touch, the parked `feat/verify-consolidation`
  WIP or `stash@{0..2}` (concurrent session's, marked do-not-touch).

## Context & Research

### Verified facts (all read-only, against clean `main @ 04d0443`)

1. **Registry uses the canonical files, never `_impl`** — `adapters/__init__.py`
   lines 72–73 / 540 / 861 do `from .telegraph_api import …` /
   `from .velog_graphql import …`.
2. **Zero import references** — repo-wide `*.py` grep for `_telegraph_api_impl` /
   `_velog_graphql_impl` matching `import` / `patch` / `importlib` returns empty.
3. **No test references** — `tests/` grep (incl. `mock.patch` string targets) empty.
4. **Not in `monolith_budget.toml` or `pyproject.toml`** — deletion needs no
   budget or packaging cleanup.
5. **Deletion direction proven by diff** (`diff CANONICAL ORPHAN`):
   - telegraph orphan has **0** unique lines; it is **missing**
     `verify_telegraph_setup()` (the setup hook `adapters/__init__.py` calls) and
     the rotation-lock `@contextmanager`.
   - velog orphan's 4 "unique" lines are all noise: single-line form of the same
     import (canonical adds `ContentRejectedError`), the stale pre-#204
     `"writeas-style"` wording (canonical migrated to `"explicit"`), and old
     null-handling args (canonical has the fuller `_save_null_artifact` /
     `_mask_cookies` from PR #200). **No net-new functionality.**
   The canonical files are a strict functional superset.
6. **Only lazy reference** — `scripts/remove-writeas.py:393-396` does
   `_replace(fp, "writeas-style", "explicit")` on `_velog_graphql_impl.py` guarded
   by an existence check. It is an already-run one-off write.as retirement script
   (PR #204); after deletion that line is a no-op and does not error.

### Institutional Learnings

- `[[feedback_dead_code_audit_blind_spots]]` — grep misses as-alias / pyproject
  scripts / `__main__` / `mock.patch` / dynamic registry. All of these were
  explicitly checked above (registry, scripts, tests-with-patch, importlib).
- `[[feedback_multi_agent_turf_check]]` + `[[feedback_stash_message_as_concurrent_agent_handshake]]`
  — a concurrent session is live in the main worktree with do-not-touch stashes;
  this work must run from an isolated worktree off `origin/main`.
- `[[feedback_pythonpath_src_for_sibling_worktree]]` — editable install binds the
  main worktree; run pytest in the sibling worktree with `PYTHONPATH=src`.

## Key Technical Decisions

- **Delete, don't consolidate**: the orphans contain nothing the canonical files
  lack, so there is no merge step — straight deletion is correct and safe.
- **Isolated worktree off `origin/main`**: mirrors the zero-collision pattern that
  O4/O5/O7 used while the main worktree is a live multi-agent zone. Do **not**
  edit in the main worktree.
- **Single atomic commit / single PR**: one self-contained refactor; tier-1
  inline self-review is sufficient (pure deletion, zero behaviour change).

## Open Questions

### Resolved During Planning

- *Which file is canonical — the newer-mtime `_impl` or the registered one?*
  Resolved: the registered (`telegraph_api.py` / `velog_graphql.py`) files are
  canonical and a strict superset. The orphans' newer mtime (`4852ee0`) is an
  artifact of the phase1 snapshot/revert re-materialising old content; content,
  not mtime, governs.
- *Does deleting break `scripts/remove-writeas.py`?* No — existence-guarded, inert.

### Deferred to Implementation

- *Add a regrowth-guard test asserting the `_impl` files never reappear?* Deferred
  and leaning **no** (YAGNI — the root cause was a one-time phase1 churn, not a
  recurring pattern). Revisit only if the orphans reappear after merge.
- Exact baseline test count to diff against is taken at execution time from the
  isolated worktree (`PYTHONPATH=src pytest tests/`), not pinned here, because the
  concurrent session may land PRs that shift the count before this runs.

## Implementation Units

- [ ] **Unit 1: Delete the two orphan files in an isolated worktree**

**Goal:** Remove both `_impl` files from a clean worktree off `origin/main`.

**Requirements:** R1, R3

**Dependencies:** None.

**Files:**
- Delete: `src/backlink_publisher/publishing/adapters/_telegraph_api_impl.py`
- Delete: `src/backlink_publisher/publishing/adapters/_velog_graphql_impl.py`

**Approach:**
- Create a sibling worktree (e.g. `bp-rm-dead-impl`) on a fresh branch off
  `origin/main` — do not work in the main worktree (concurrent session active).
- Remove the two files. No other edits.

**Patterns to follow:**
- Zero-collision isolated-worktree workflow used by O4/O5/O7 (see
  `[[project_codebase_optimization_backlog]]`).

**Test scenarios:**
- Test expectation: none — pure file deletion, no behavioural change. Coverage is
  the existing suite staying green (Unit 2).

**Verification:**
- Both files are gone; `git status` shows exactly two deletions and nothing else.

- [ ] **Unit 2: Confirm compile + suite + CI gates stay green**

**Goal:** Prove the deletion is invisible to behaviour and gates.

**Requirements:** R2

**Dependencies:** Unit 1.

**Files:**
- (verification only — no files changed)

**Approach:**
- `python -m py_compile` across `src/backlink_publisher/**/*.py` from the worktree.
- Full suite via `PYTHONPATH=src pytest tests/` (editable install binds the main
  worktree, so PYTHONPATH is required here).
- Sanity-check that `adapters/__init__.py` still imports and the registry resolves
  telegraph + velog adapters (import the package, list registered platforms).

**Test scenarios:**
- Happy path: full suite result matches the pre-deletion baseline taken in the
  same worktree (same pass count, no new failures/errors attributable to the
  deletion).
- Integration: importing `backlink_publisher.publishing.adapters` succeeds and
  `registered_platforms()` still includes telegraph and velog.
- Error path: confirm no `ModuleNotFoundError` / `ImportError` referencing
  `_telegraph_api_impl` or `_velog_graphql_impl` anywhere in the run.

**Verification:**
- `py_compile` clean; suite green with no deletion-attributable regressions;
  monolith-budget gate unaffected (the `_impl` files were never budgeted).

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| A dynamic/string import of `_impl` was missed by grep | Checked registry, `mock.patch` targets, `importlib`, pyproject scripts; Unit 2 full-suite run is the backstop tripwire (a missed dynamic import fails at import time). |
| Concurrent session collision in main worktree | Work entirely from an isolated worktree off `origin/main`; never touch `stash@{0..2}` or `feat/verify-consolidation`. |
| `scripts/remove-writeas.py` breaks | Reference is existence-guarded and the script is already-run/historical; verified inert. |
| Orphans reappear via a future phase1-style snapshot | Out of scope; noted as a deferred regrowth-guard question. |

## Sources & References

- Origin: 2026-05-26 code-quality stocktake (this session's brainstorm; item N1).
- Related: `[[project_codebase_optimization_backlog]]` (O1–O9, zero-collision worktree pattern).
- Related code: `src/backlink_publisher/publishing/adapters/__init__.py` (registry imports),
  `src/backlink_publisher/publishing/adapters/telegraph_api.py`,
  `src/backlink_publisher/publishing/adapters/velog_graphql.py`.
- Related PRs: #236 (phase1, introduced the orphans), #200 (velog null-artifact, present only in canonical), #204 (write.as retirement / `scripts/remove-writeas.py`).
