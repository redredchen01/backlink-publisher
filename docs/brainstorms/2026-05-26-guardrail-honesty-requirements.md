---
date: 2026-05-26
topic: guardrail-honesty
---

# Guardrail Honesty — Retire Orphaned Quality Guards

## Problem Frame

This repo is developed by parallel AI agents across 20+ `bp-*/` worktrees. Every
agent reads `AGENTS.md` / `CLAUDE.md` / `docs/` to decide what is safe. So a guard
that *claims* to protect something but never runs is worse than no guard: it gives
every future session false confidence.

A single abandoned "quality automation" drop (commit `f927fd0`, "feat: add quality
automation") left **four artifacts that all present as active quality guards but
that CI never executes**:

| Artifact | Reality (verified in source, 2026-05-26) | The guard that *actually* protects this |
|---|---|---|
| `scripts/check_imports.py` | Greps **1 of 7** documented legacy-import forms (`from backlink_publisher.(errors\|adapters\|content_fetch)`); not in `ci.yml`. `py_compile` (the CI style step) does not execute imports, so it can't catch flat imports either. | The full `pytest tests/` run — any **test-reachable** module with a dead import explodes at collection. (Caveat: see R1 — a few orphan modules are imported by nothing and slip past pytest too.) CI already runs it on 3.11 + 3.12. |
| `scripts/check_monolith_budget.py` | Duplicates `tests/test_no_monolith_regrowth.py`; contains a dead `measure_sloc()` that returns `None` and is never called; not in `ci.yml`. | `tests/test_no_monolith_regrowth.py`, run by CI via pytest. |
| `.coveragerc` `fail_under = 85` | Declares an 85% coverage floor, but CI runs `pytest tests/ -v --tb=short --timeout=30` **without `--cov`** — the floor is never evaluated. | Nothing. Pure unenforced claim. |
| `docs/quality_optimization_plan.md` | Lists all of the above under "**已实施的自动化**" (implemented automation), then admits "**CI 集成待添加**" (CI integration pending) — self-contradictory; the narrative source of the false confidence. | n/a — it is the misleading doc. |

None of these four are referenced by `ci.yml`, hooks (`scripts/install-*.sh`), the
canonical or workspace-root `Makefile`, `pyproject.toml`, or pre-commit (no
`.pre-commit-config.yaml` exists). They are inert except as misinformation.

**Decided direction (all low-risk):** retire each orphaned guard rather than wire
it up, because the real protection already exists (pytest) or was never wanted as a
gate (the 85% floor). Then prevent regrowth.

## Requirements

**Retire the orphaned guards**
- R1. Delete `scripts/check_imports.py`. It catches only 1 of 7 import forms and only
  3 module names, and is not wired to CI. Its one theoretical advantage over pytest —
  statically scanning *every* `src/` file including modules no test imports — is not
  realized at that narrow pattern, and the real fix for the orphan-module gap it
  gestures at is R6 (delete the dead orphan modules), not a near-empty grep. (Do not
  claim it is a "strict subset" of pytest: pytest catches dead imports only in
  test-reachable modules; verified-orphan modules like
  `publishing/adapters/_telegraph_api_impl.py` and `_velog_graphql_impl.py` are
  imported by nothing and slip past pytest, `py_compile`, and `ast.parse` alike.)
- R2. Delete `scripts/check_monolith_budget.py`. It duplicates the CI-run
  `tests/test_no_monolith_regrowth.py` and carries dead code.
- R3. Remove the `fail_under = 85` line from `.coveragerc` (and any now-orphaned
  coverage-gate config that exists solely to support it). Do not add a coverage gate
  to CI in this work. Also address the matching dangling artifact: `ci.yml` runs
  `pip install pytest pytest-cov` but never passes `--cov`, so after R3 `pytest-cov`
  is itself "looks-like-coverage-is-configured" dead weight — drop it from the CI
  install step (or explicitly note why it stays for ad-hoc local `--cov` runs).

**Make the docs honest**
- R4. Delete `docs/quality_optimization_plan.md`, or replace it with a short, true
  statement of where quality is actually enforced: the full pytest suite on 3.11 +
  3.12 (import tripwire + `test_no_monolith_regrowth.py`), `py_compile` + `ast.parse`
  style checks, and `plan-check` claim gates. No "待添加 / pending" promises.
- R5. Make the regrowth rule itself an *enforced* guard — not another inert prose
  principle (the very failure this document condemns). Add a CI-executed test (e.g.
  `tests/test_no_orphaned_guard_scripts.py`) that asserts every `scripts/check_*.py`
  is referenced by `ci.yml`, a `Makefile`, a git hook, or pre-commit, and fails
  otherwise. Then document the now-enforced principle in the canonical
  `backlink-publisher/AGENTS.md`: **a quality guard must live as a CI-executed test or
  workflow step; a `scripts/check_*.py` that no CI surface invokes will fail the
  orphan-guard test.** (The AGENTS.md line documents the gate; the test *is* the gate.)

**Delete the orphan dead-code modules**
- R6. Delete `src/backlink_publisher/publishing/adapters/_telegraph_api_impl.py`
  (~602 SLOC) and `_velog_graphql_impl.py` (~581 SLOC). Verified during review: no
  module or test imports either (the live adapters import `telegraph_node` /
  `velog_graphql`, not these `_impl` copies); nothing loads them dynamically
  (`importlib`/`__import__`/string targets); their exported helpers (`_token_path`,
  `_load_token`, `_write_token_atomic`, …) are line-for-line stale duplicates of the
  live `telegraph_api.py` / `velog_graphql.py`. They are phase-1-refactor leftovers.
  The only mention is a conditional `if it exists` branch in the completed one-off
  `scripts/remove-writeas.py`, not a runtime consumer. These two modules ARE the
  import-hygiene gap R1's grep gestured at — deleting the dead code closes the gap
  better than keeping a near-empty guard. (Verified: neither file is tracked in
  `monolith_budget.toml`, so no ceiling edit is needed — a clean pure-deletion.)
  **Delegated:** a parallel code-quality stocktake already planned this exact deletion
  as item N1 — `docs/plans/2026-05-26-003-refactor-rm-dead-impl-adapters-plan.md`
  (`status: active`). To avoid duplicate work, R6 is executed by that plan; the
  guardrail-honesty plan (#006) carries only R1–R5 and cross-references #003.

## Success Criteria
- After the change, the two script names are absent from all live, non-archival
  files: `grep -rn 'check_imports\|check_monolith_budget' . --exclude-dir=docs`
  returns nothing. (Brainstorm/ideation/plan docs under `docs/` may still name them
  historically; if R4 produces a replacement stub, it must reference them only to
  explain that they were deleted.)
- No file in the repo claims an active quality gate that CI does not actually run.
- `pytest tests/` and CI stay green — no behavior change to live product code; only
  removal of inert tooling/config/doc plus two unreferenced dead modules (R6), and one
  new enforcing test (R5).
- A reader of `AGENTS.md` can tell, in one place, exactly which guards are real and
  why orphaned guard scripts are disallowed.

## Scope Boundaries
- **Not** wiring an 85% (or any) coverage gate into CI — explicitly out (decided).
- **Not** building the "doc-claim self-verification" meta-checker (route/adapter/
  monolith counts asserted vs reality, AST negative-assertion lint). That is the
  larger round-8 idea #7; this pass only retires the four named artifacts, the two
  orphan `_impl` modules (R6), and the dangling `pytest-cov` install.
- **Not** touching `tests/test_no_monolith_regrowth.py`, the import test tripwire,
  or any real guard — they stay as-is.
- **Not** consolidating the `*-login` CLIs or cleaning `scripts/*spike*` artifacts —
  separate code-quality items, deliberately deferred.

## Key Decisions
- **Delete over complete (R1/R2):** for test-reachable code, completing
  `check_imports.py` to all 7 forms would only re-implement, more weakly, what the
  full pytest run already guarantees. For the *un*reachable code where a static grep
  genuinely would add coverage, the honest fix is to delete that dead code (R6), not
  to keep a guard alive for code that shouldn't exist. Either way the grep is not
  worth maintaining. Same logic retires the monolith script (the CI-run test is
  authoritative).
- **Drop the coverage claim over enforcing it (R3):** operator-confirmed this session
  (not an inferred assumption about intent); chosen as the low-risk path — enforcing
  85% could block CI pending an unknown-size coverage backfill, which is out of scope
  for a quality-hygiene pass. Deletion is reversible if a coverage goal is revived.
- **Single root cause:** the four named artifacts trace to one abandoned `f927fd0`
  drop ("feat: add quality automation"); retiring them together is one coherent
  change, not four unrelated edits. (The dangling `pytest-cov` CI install predates
  `f927fd0` but belongs to the same "unenforced coverage" theme — folded into R3.)

## Dependencies / Assumptions
- The working tree has live WIP (`content/fetch.py`, `monolith_budget.toml`) and
  open PRs #242/#243/#244 (verified OPEN as of 2026-05-26). None of them touch the
  four named guard artifacts or the two `_impl` modules, and R6 is a pure deletion
  (the orphan modules aren't in `monolith_budget.toml`) — so there is no file overlap
  with the WIP. Still do this work from a clean worktree off `origin/main` per the
  repo's standard isolation practice.

## Outstanding Questions

### Resolve Before Planning
- *(none — direction and the one product decision (R3) are resolved)*

### Deferred to Planning
- [Affects R4][Technical] Delete `quality_optimization_plan.md` outright vs. replace
  with an honest "where quality is enforced" pointer — decide during planning based
  on whether any true content is worth keeping (current content is entirely about the
  retired guards, so deletion is likely).
- [Affects R3][Resolved during review] `.coveragerc`'s `[report]`/`[html]`/`[xml]`
  sections are independent of `fail_under` and remain valid/usable for ad-hoc local
  `pytest --cov` runs — planning decides whether to keep them (local convenience) or
  delete `.coveragerc` entirely. Only the single `fail_under = 85` line is the
  unenforced gate that must go.

## Next Steps
→ `/ce:plan` for structured implementation planning
