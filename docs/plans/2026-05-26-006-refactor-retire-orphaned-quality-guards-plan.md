---
title: "refactor: retire orphaned quality guards (guardrail honesty)"
type: refactor
status: shipped
date: 2026-05-26
origin: docs/brainstorms/2026-05-26-guardrail-honesty-requirements.md
claims:
  # Shipped via PR #252 (squash 4f1b38a). Re-resolved against post-merge origin/main:
  # the surviving touched paths (the 3 deletion targets are intentionally gone).
  paths:
    - .coveragerc
    - .github/workflows/ci.yml
    - AGENTS.md
    - tests/test_no_orphaned_guard_scripts.py
  shas:
    - '4f1b38a'
---

# refactor: retire orphaned quality guards (guardrail honesty)

## Overview

A single abandoned "quality automation" drop (commit `f927fd0`, "feat: add quality
automation") left four artifacts that all *present* as active quality guards but
that CI never executes. In a repo developed by parallel AI agents across 20+ `bp-*/`
worktrees, an unenforced guard is worse than none — it gives every future session
false confidence. This plan retires those artifacts and, critically, replaces the
"don't do this again" rule with an *enforced* test rather than another inert prose
principle.

Pure hygiene: no live product code changes. Deletes inert tooling/config/doc, drops a
dangling CI install, and adds one enforcing meta-test.

**Scope note:** the brainstorm's R6 (delete the two dead `_impl` orphan adapter
modules) is **delegated** to the already-active
`docs/plans/2026-05-26-003-refactor-rm-dead-impl-adapters-plan.md` (stocktake item N1)
to avoid duplicate work. This plan carries only R1–R5.

## Problem Frame

Verified in source (2026-05-26):

| Artifact | Reality | Real guard that already covers this |
|---|---|---|
| `scripts/check_imports.py` | Greps 1 of 7 legacy-import forms, only 3 module names; not in `ci.yml`. `py_compile` (CI style step) doesn't execute imports either. | Full `pytest tests/` — dead imports in test-reachable modules explode at collection. CI runs it on 3.11 + 3.12. |
| `scripts/check_monolith_budget.py` | Duplicates `tests/test_no_monolith_regrowth.py`; contains a dead `measure_sloc()` returning `None`; not in `ci.yml`. | `tests/test_no_monolith_regrowth.py`, CI-run via pytest. |
| `.coveragerc` `fail_under = 85` | Declares an 85% floor, but CI runs `pytest tests/ -v --tb=short --timeout=30` **without `--cov`** — never evaluated. | Nothing. Unenforced claim. |
| `docs/quality_optimization_plan.md` | Lists the above under "已实施的自动化" (implemented), then admits "CI 集成待添加" (pending) — self-contradictory; the narrative source of the false confidence. | n/a — it is the misleading doc. |

Plus one matching dangling artifact: `ci.yml` runs `pip install pytest pytest-cov`
while the test step never passes `--cov`. `pytest` is already in the `[dev]` extra;
`pytest-cov` is the only thing that line uniquely installs, and nothing uses it.

None of the four are referenced by `ci.yml`, hooks, the canonical or workspace-root
`Makefile`, `pyproject.toml`, or pre-commit (no `.pre-commit-config.yaml` exists).
Their only mention is inside `docs/quality_optimization_plan.md` itself.

## Requirements Trace

- R1. Delete `scripts/check_imports.py` (see origin: R1).
- R2. Delete `scripts/check_monolith_budget.py` (see origin: R2).
- R3. Remove `fail_under = 85` from `.coveragerc`; drop the dangling
  `pip install pytest pytest-cov` line from `ci.yml` (see origin: R3).
- R4. Retire `docs/quality_optimization_plan.md` — delete, or replace with a true
  pointer to where quality is actually enforced (see origin: R4).
- R5. Make the regrowth rule an *enforced* guard: add a CI-run test asserting every
  `scripts/check_*.py` is referenced by a CI surface, and document the now-enforced
  principle in `AGENTS.md` (see origin: R5).
- R6. *(Delegated to plan #003 — not in this plan's scope.)*

**Success criteria (from origin):**
- `grep -rn 'check_imports\|check_monolith_budget' . --exclude-dir=docs` returns nothing.
- No file claims an active quality gate CI does not run.
- `pytest tests/` and CI stay green; only inert tooling/config/doc removed + one new test.
- A reader of `AGENTS.md` can tell, in one place, which guards are real and why
  orphaned guard scripts are disallowed.

## Scope Boundaries

- **Not** wiring an 85% (or any) coverage gate into CI — operator-confirmed out.
- **Not** building the round-8 idea #7 "doc-claim self-verification" meta-checker
  (route/adapter/monolith counts asserted vs reality). R5 is scoped narrowly to
  orphaned `scripts/check_*.py`.
- **Not** touching `tests/test_no_monolith_regrowth.py`, the import tripwire, or any
  real guard.
- **Not** consolidating the `*-login` CLIs or cleaning `scripts/*spike*` artifacts.
- **Not** deleting the `_impl` orphan modules — that is plan #003 (R6).

## Context & Research

### Relevant Code and Patterns

- **Mirror for R5's test:** `tests/test_no_monolith_regrowth.py` — the canonical
  repo-invariant meta-test. Locates root via `REPO_ROOT = Path(__file__).resolve().parents[1]`,
  loads config at module-collection time with `tomllib`, parametrizes over discovered
  keys. Other contract meta-tests to match in spirit: `test_r9_extension_readiness.py`,
  `test_exit_code_contract.py`, `test_manifest_contract.py`.
- **`.coveragerc`:** `fail_under = 85` lives in `[report]`. The rest of `[report]`
  (`exclude_lines`, `omit`, `show_missing`) plus `[run]`/`[html]`/`[xml]` are
  independent and remain valid/useful for ad-hoc local `pytest --cov` runs — so R3
  removes only the one line, not the file.
- **`ci.yml` install step:** `pip install -e ".[dev]"` then `pip install pytest pytest-cov`.
  `[dev]` already includes `pytest>=7`; `pytest-cov` is absent from `[dev]` and unused.
- **AGENTS.md anchors:** the `### CI surfaces` section (≈line 210, currently
  enumerating the `plan-claims-gate`/`plan-claims-radar` workflows) is the natural home
  for the new enforced principle — it is the existing "what counts as a CI surface"
  enumeration, and keeping the principle there keeps it coupled to the test's
  surface-list. `## Monolith Budget` (≈line 232) is the model for "guard = CI-run test".
- **Makefile reality:** there is **no** `Makefile` inside `backlink-publisher/`. The
  only Makefile is at the workspace root (`../Makefile`, *outside* `REPO_ROOT` and not
  reachable from a `parents[1]`-rooted test); it does **not** reference the check
  scripts (it just iterates `pyproject.toml` dirs and runs pytest). So Unit 3's test
  scans only the CI surfaces reachable from `REPO_ROOT`: `.github/workflows/*.yml`,
  `scripts/install-*.sh`, and `.pre-commit-config.yaml` if present — **not** a Makefile.

### Institutional Learnings

- `[[feedback_grep_all_legacy_import_forms_not_just_from_dotted]]` — the full pytest
  run is the real 7-form import tripwire; `check_imports.py`'s single-form grep is a
  weak partial. Grounds R1.
- `[[feedback_multi_agent_turf_check]]` / `[[feedback_dead_code_audit_blind_spots]]` —
  surfaced the R6/plan-#003 collision and validated that the orphan modules have no
  dynamic/`importlib`/string consumers. Drives the R6 delegation.
- `[[reference_plan_check_cli]]` / `[[feedback_plan_doc_on_cutoff_needs_claims_block]]`
  — plans dated ≥2026-05-20 need a `claims:` frontmatter block or `plan-check` exits 8;
  this plan includes one.

### External References

None — internal hygiene, fully grounded in repo patterns. No external research needed.

## Key Technical Decisions

- **Delete over complete (R1/R2):** for test-reachable code, completing
  `check_imports.py` to all 7 forms only re-implements, more weakly, what the full
  pytest run guarantees. For unreachable code where a static grep *would* add coverage,
  the honest fix is deleting that dead code (plan #003), not maintaining a near-empty
  guard. The monolith script is strictly dominated by the CI-run test.
- **Remove the coverage claim, not enforce it (R3):** operator-confirmed. Enforcing
  85% could block CI pending an unknown-size backfill — out of scope. Remove only
  `fail_under`; keep the rest of `.coveragerc` for local convenience. Deletion is
  reversible if a coverage goal is revived.
- **R5 must be an enforced gate, not prose:** the plan's own thesis ("a guard that
  claims to protect but never runs is worse than none") forbids fixing the problem with
  another unenforced prose rule. The CI-run test *is* the gate; the AGENTS.md line only
  documents it.
- **Delegate R6 to plan #003:** a parallel stocktake already planned the identical
  deletion (item N1, `status: active`). Re-planning would duplicate work and risk
  conflicting edits to the same files.

## Open Questions

### Resolved During Planning

- *Remove whole `pip install pytest pytest-cov` line or just `pytest-cov`?* → Remove
  the whole line: `pytest` already comes from `.[dev]`, so the line's only unique
  effect is the unused `pytest-cov`.
- *Edit `.coveragerc` or delete it?* → Edit (remove one line). `[run]`/`[report]`/
  `[html]`/`[xml]` remain useful for local `--cov`; only `fail_under` is the false gate.
- *Where does the R5 principle live?* → Canonical `backlink-publisher/AGENTS.md`, in the
  `### CI surfaces` section (≈line 210), kept aligned with the test's surface-list.
- *Delete `quality_optimization_plan.md` vs. stub?* → **Delete outright** (content is
  entirely about the retired guards). Stub is an optional follow-up that must assert no
  gate.
- *Which CI surfaces does R5's test scan?* → The `REPO_ROOT`-reachable ones only:
  `.github/workflows/*.yml`, `scripts/install-*.sh`, and `.pre-commit-config.yaml` if
  present. **No Makefile** (none exists inside `backlink-publisher/`; the workspace-root
  `../Makefile` is outside `REPO_ROOT` and doesn't reference the scripts).

### Deferred to Implementation

- *Exact failure-message wording and whether to parametrize per-script or assert in a
  single test function* — a cosmetic choice settled while mirroring
  `test_no_monolith_regrowth.py` against the real tree.

## Implementation Units

- [ ] **Unit 1: Retire the orphaned guard scripts and their misleading doc (R1, R2, R4)**

**Goal:** Remove the three inert artifacts that present as quality guards but are
referenced by nothing except each other.

**Requirements:** R1, R2, R4

**Dependencies:** None

**Files:**
- Delete: `scripts/check_imports.py`
- Delete: `scripts/check_monolith_budget.py`
- Delete (or replace with honest stub): `docs/quality_optimization_plan.md`

**Approach:**
- **Decision: delete `docs/quality_optimization_plan.md` outright** (its entire content
  describes the retired guards and asserts false gates). A one-line honest pointer stub
  is an acceptable follow-up if later judged useful, but must assert no gate and make no
  "pending/待添加" promise.
- Delete the two scripts and the doc together — they are interlocked (the doc is the
  only place that references the scripts).

**Test scenarios:**
- Test expectation: none — pure deletion of unreferenced files. Correctness is proven
  by the full suite staying green and by Unit 3's new test confirming no orphaned
  `check_*.py` remain.

**Verification:**
- From a **clean checkout of the canonical tree** (not the shared multi-worktree
  parent): `grep -rn 'check_imports\|check_monolith_budget' . --exclude-dir=docs`
  returns nothing. (Caveat: `--exclude-dir=docs` only drops top-level `docs/`; sibling
  `bp-*/` worktrees still carry stale copies — run from an isolated worktree off
  `origin/main`, see Risks.)
- `pytest tests/` stays green (nothing imported these scripts).

- [ ] **Unit 2: Drop the unenforced coverage gate config + dangling pytest-cov (R3)**

**Goal:** Stop claiming an 85% coverage floor that CI never evaluates, and remove the
matching unused coverage-plugin install.

**Requirements:** R3

**Dependencies:** None

**Files:**
- Modify: `.coveragerc` (remove the single `fail_under = 85` line from `[report]`)
- Modify: `.github/workflows/ci.yml` (remove the `pip install pytest pytest-cov` line
  from the "Install dependencies" step)

**Approach:**
- Surgical edits only. Leave the rest of `.coveragerc` intact for local `--cov` use.
- After removal, `pytest` still installs via `pip install -e ".[dev]"`.

**Test scenarios:**
- Test expectation: none — config/CI-manifest change with no product behavior. Verified
  by `.coveragerc` remaining a valid INI and CI staying green.

**Verification:**
- `.coveragerc` no longer contains `fail_under`; remaining sections parse.
- `ci.yml` no longer installs `pytest-cov`; the CI run is unchanged in behavior (it
  never used `--cov`).

- [ ] **Unit 3: Add the enforcing orphan-guard test + document the principle (R5)**

**Goal:** Replace "write a rule in AGENTS.md and hope" with a CI-executed test that
fails if any `scripts/check_*.py` is unreferenced by a CI surface — so the regrowth
rule is itself enforced.

**Requirements:** R5

**Dependencies:** Tightly coupled with Unit 1 by *ordering*, not by "Unit 1 must land
first." The test is authored **before** Unit 1's deletions so it can be observed going
red on today's two unreferenced scripts; Unit 1's deletions then turn it green. Land
the test commit before (or in the same PR as, but committed before) Unit 1.

**Files:**
- Create: `tests/test_no_orphaned_guard_scripts.py`
- Modify: `AGENTS.md` (document the now-enforced principle in the `### CI surfaces`
  section, ≈line 210)

**Approach:**
- Mirror `tests/test_no_monolith_regrowth.py`: resolve `REPO_ROOT` via
  `Path(__file__).resolve().parents[1]`; discover guard scripts by globbing
  `scripts/check_*.py`; for each, assert its filename appears in at least one
  `REPO_ROOT`-reachable CI surface: `.github/workflows/*.yml`, `scripts/install-*.sh`
  hook installers, and `.pre-commit-config.yaml` if present. (No Makefile — see
  Context & Research "Makefile reality".) Fail with a message naming the unreferenced
  script and citing this principle.
- Keep the test's scanned-surface list aligned with whatever AGENTS.md `### CI surfaces`
  enumerates, so prose and test cannot drift apart.
- Test passes vacuously once zero `check_*.py` exist (post-Unit-1) and fails the moment
  a future agent re-adds an unreferenced one.
- AGENTS.md line: "A quality guard must live as a CI-executed test or workflow step. A
  `scripts/check_*.py` that no CI surface invokes will fail
  `tests/test_no_orphaned_guard_scripts.py`."

**Execution note:** Ordered, and the order is the proof — do not collapse it:
(1) write `tests/test_no_orphaned_guard_scripts.py`; (2) run it on the **pre-Unit-1**
tree and capture the red failure naming `check_imports.py` + `check_monolith_budget.py`;
(3) apply Unit 1's deletions; (4) re-run to confirm green. Authoring the test *after*
Unit 1 (when no `check_*.py` remain) makes it pass vacuously and forfeits the proof
that it detects the failure mode.

**Patterns to follow:**
- `tests/test_no_monolith_regrowth.py` (root resolution, collection-time discovery,
  clear failure messages).

**Test scenarios:**
- Happy path: with no `scripts/check_*.py` present (post-Unit-1), the test passes.
- Happy path: a `scripts/check_*.py` whose name appears in `ci.yml` passes.
- Error path: an unreferenced `scripts/check_foo.py` added to the tree → test fails with
  a message naming `check_foo.py` and citing the orphan-guard principle.
- Edge case: a `check_*.py` referenced only in a `Makefile` (not `ci.yml`) still passes
  (any CI surface counts).
- Edge case: reference-detection must not match incidental mentions inside `docs/`
  (only CI surfaces count) — a script named only in a markdown doc still fails.

**Verification:**
- The new test is collected and passes under `pytest tests/` after Unit 1 lands.
- Temporarily adding a dummy unreferenced `scripts/check_zzz.py` makes it fail; removing
  it makes it pass again.
- `AGENTS.md` states the enforced principle in one discoverable place.

## System-Wide Impact

- **Interaction graph:** None at runtime — deleted files are imported/invoked by
  nothing; `.coveragerc`/`ci.yml` edits don't change the executed test command.
- **API surface parity:** No CLI, schema, or adapter surface touched.
- **CI surface:** `ci.yml` install step shrinks by one line; test/style/fixture steps
  unchanged. The new test adds one more collected case to the existing pytest run.
- **Unchanged invariants:** `tests/test_no_monolith_regrowth.py`, the full-suite import
  tripwire, `monolith_budget.toml`, and all real guards are untouched and remain the
  authoritative enforcement.
- **Cross-plan coordination:** Plan #003 (R6) edits only `adapters/_*_impl.py` +
  `scripts/remove-writeas.py`'s reference; zero file overlap with this plan, so the two
  can land independently in any order.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| A hidden consumer of the deleted scripts exists (CI/hook/pre-commit) | Verified none exist: grep across `ci.yml`, the workspace-root `../Makefile`, `install-*.sh`, `pyproject.toml`; no `.pre-commit-config.yaml`. The only references are inside `docs/quality_optimization_plan.md` (itself deleted in Unit 1). Unit 3's test future-proofs the invariant. |
| Operator actually wanted the 85% coverage gate | Operator confirmed dropping it this session; removal is reversible (one line) if revived. |
| R5 test passes vacuously and never proves it works | Execution note mandates writing it red against today's two unreferenced scripts before Unit 1 makes it green. |
| Worktree collision with the active code-quality stocktake (#003/#004/#005) | This plan's files don't overlap those plans'. Work from a clean worktree off `origin/main` per repo isolation practice; `.coveragerc`/`ci.yml`/`AGENTS.md` are not touched by #003/#004/#005. |
| `plan-check` exits 8 on a post-cutoff plan without claims | `claims:` block included in frontmatter. |

## Documentation / Operational Notes

- `AGENTS.md` gains one enforced-principle line (Unit 3). If the worktree carries a
  stale `AGENTS.md` copy, edit the canonical `backlink-publisher/AGENTS.md`.
- No rollout/monitoring/migration impact — pure repo hygiene.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-26-guardrail-honesty-requirements.md](docs/brainstorms/2026-05-26-guardrail-honesty-requirements.md)
- Related plan (R6, delegated): `docs/plans/2026-05-26-003-refactor-rm-dead-impl-adapters-plan.md`
- Mirror pattern: `tests/test_no_monolith_regrowth.py`
- Origin commit of the four artifacts: `f927fd0` ("feat: add quality automation")
