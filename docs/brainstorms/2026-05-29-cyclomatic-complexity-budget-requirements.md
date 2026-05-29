---
date: 2026-05-29
topic: cyclomatic-complexity-budget
type: requirements
status: draft
---

# Cyclomatic-Complexity Budget + Publish-Path Hotspot Decomposition

## Problem Frame

The codebase is exceptionally well-maintained: ~44K SLOC / ~6575 tests, all A-rank
maintainability, near-zero dead code, an enforced **monolith SLOC budget**, a
plan-claims drift gate, and a recent comprehensive-optimization wave (Plan 008:
false-success, exit codes, exception handling, mypy, observability).

But there is one uncovered axis. The quality system gates **how long a file is**
(`monolith_budget.toml`) and **whether types are right** (mypy, non-blocking). Nothing
gates **how convoluted a function is**. Confirmed: there is zero cyclomatic-complexity
(CC) enforcement anywhere in CI, tests, or lint config.

The consequence is that the three most complex functions in the entire repo sit on the
**core publish path** — the highest-value, highest-churn code — and are invisible to
every existing guardrail:

| Function | CC | Rank | Tracked by any budget? | Churn (since 2026-04) |
|---|---|---|---|---|
| `cli/_resume.py::_run_resume` | 62 | F | **No** | 18 commits |
| `cli/plan_backlinks/_payload.py::_generate_payload` | 50 | F | No | — |
| `cli/publish_backlinks.py::main` | 49 | F | SLOC only, **9 SLOC from ceiling** | 37 commits (most-churned CLI) |

Radon ranks CC as A (1–5), B (6–10), C (11–20), D (21–30), E (31–40), F (41+); the three
functions above (CC 49/50/62) are the only F-rank ones, and the proposed CC-30 backstop (R3)
sits at the D/E boundary — so only E/F-rank functions are above it and need seeding.

Every change to the publish or resume flow forces a contributor to reason about a
CC 49–62 function. `publish_backlinks.py` is additionally at 361/370 SLOC — the next
feature that touches it hits the wall and either scrambles an extraction under deadline
or bumps the ceiling.

This is the highest-ROI optimization available because it (a) targets concentrated risk
on the path that matters most, and (b) compounds — a CC gate prevents regrowth forever.
A side effect (not a separate lever): because `publish_backlinks.py` is a single-function
file (`main()` only), decomposing it relieves the SLOC ceiling and lowers CC in the *same*
extraction — one operation, counted once. **Honest caveat:** CC is a proxy, not a defect
rate. This repo's bug history skews toward distributed-state/contract issues, not
intra-function branch density, and there is no evidence these three functions caused
incidents. The durable win is the *gate* (cheap, prophylactic, compounding). The
decomposition payoff is real but softer, so scope was deliberately narrowed (2026-05-29) to
just `_run_resume` — the one outlier at CC 62, far above the rest; the other two F-rank
functions are grandfathered and decomposed lazily.

## Requirements

**Complexity Budget Gate**
- R1. Add a `complexity_budget.toml` (sibling to `monolith_budget.toml`) keyed by
  **`<file path>::<radon block fullname>`** — i.e. `module.func` for module functions and
  `Class.method` for methods (radon's `cc_visit()` emits Function, Method, **and** Class
  blocks; bare `<file>::<function>` collides — e.g. `medium_api.py` has both a `MediumAPIAdapter`
  class block at CC 30 and a `MediumAPIAdapter.publish` method at CC 29). Each entry is
  `{ ceiling: int (radon CC), rationale: str ≥80 chars }`. Gate **Function + Method blocks**;
  decide explicitly in planning whether Class-aggregate blocks are gated or skipped. Enforced
  by a new test that **adapts the structure of** `tests/test_no_monolith_regrowth.py` (see R4 —
  it is not a literal copy; the SLOC `round_up_to_10(+30)`/headroom policy has no CC analog).
- R2. Seed the budget with **every existing function above the backstop** (R3) at its current
  measured CC, locking the baseline so no listed function silently regrows. At a backstop of
  CC 30 that is only the **~6 E/F-rank blocks** today (`_run_resume` 62, `_generate_payload`
  50, `publish.main` 49, `_project_checkpoint` 39, `save_config` 36, `_build_links` 36) —
  re-enumerate with `radon cc src -s --min E` at implementation time and key by `cc_visit`
  fullname. The many D-rank functions (CC 21–30) sit *under* the backstop and are **not**
  seeded.
- R3. **Enforcement = named set + high backstop** (resolved 2026-05-29). Two rules:
  (a) every **seeded** function is held to its individual ceiling (no regrowth, monolith-style);
  (b) every **unlisted** function is held to a single global **backstop** (proposed CC ≤ 30,
  the D/E boundary — confirm in planning). The backstop catches a *born-complex* new function
  (the actual failure mode: `_run_resume` reached CC 62 in one extraction, not by creep) while
  imposing zero friction on normal new code under it. **Seed floor = backstop + 1**, so there
  is no un-gated over-backstop gap. Net-new code above the backstop needs an explicit,
  rationalized entry. This is laxer and harder to game than a low default-on-everything cap,
  and stricter than the SLOC budget's named-set-only model.
- R4. Raising a ceiling (or adding a new entry for a function that crosses the backstop) must
  happen in the **same PR** that grows the function, with a ≥80-char rationale — no
  override label, defense is `git blame`. Same *contract* as the monolith budget, but CC
  needs its **own seed convention** (the monolith `ceiling = round_up_to_10(SLOC + 30)`,
  headroom ≤ 50 policy is SLOC-specific): planning must define the CC ceiling formula
  (e.g. seed at exact current CC, or current + small constant) and a CC headroom cap.
- R5. The gate runs as a normal pytest test (blocking in CI like the monolith gate), not
  an orphaned `scripts/check_*.py` (per AGENTS.md "no orphaned guard scripts").

**Publish-Path Hotspot Decomposition** *(scope resolved 2026-05-29: gate + decompose only
`_run_resume`; the other two F-rank functions are grandfathered and decomposed lazily — see
Key Decisions)*
- R6. Decompose `cli/_resume.py::_run_resume` (CC 62 — the single worst function in the repo
  and not under any budget) into a thin orchestration shell plus extracted, independently
  testable units; each unit lands its own behavior-pinning tests. Target: shell + units each
  comfortably under the backstop (no new seed entries), or a justified entry if one must
  stay high. **Behavior-pinning tests come first** —
  `_run_resume` has thin direct coverage (2 test files), so characterization tests must lock
  current behavior *before* any extraction.
- R7. `cli/plan_backlinks/_payload.py::_generate_payload` (CC 50) and
  `cli/publish_backlinks.py::main` (CC 49) are **seeded at current CC and grandfathered, not
  decomposed now**. They are decomposed lazily the next time a feature touches them — at which
  point R8's note applies to `publish_backlinks.py` (single-function file; extracting `main()`
  must move logic to a new sibling module AND lower the file's ceiling in the existing
  `monolith_budget.toml`, whose rationale is already stale — claims "335 SLOC … 35 headroom",
  actual 361/9).
- R8. Decomposition is **behavior-preserving** — no change to stdout JSONL contract, stderr
  diagnostics, exit codes, or publish/resume semantics. Existing tests stay green; new tests
  pin the extracted units, not just the old end-to-end behavior.
- R9. The decomposition is an independently revertable PR/unit (matches the project's
  one-concern-per-PR norm and Plan 008's R3/R8).

**Sequencing**
- R10. Land the budget gate (R1–R5) seeded at *current* CC values **first**, then land the
  `_run_resume` decomposition, lowering its ceiling in the same PR as the extraction. This
  makes the gate the safety net for its own follow-up work and prevents regression during the
  riskiest edit.

## Success Criteria
- CI fails if a seeded function exceeds its budgeted CC, **or** an unlisted function exceeds
  the backstop — both verified by deliberately-too-complex canary fixtures, mirroring
  `tests/fixtures/sloc_canary.py`.
- After decomposition, `_run_resume` is gone (CC 62 → shell + units each under the backstop,
  or with a rationalized entry); the extracted units' target CC is set in planning alongside
  the extraction boundaries (see R6). The other two F-rank functions remain, seeded at their
  current CC.
- `publish_backlinks.py` regains meaningful SLOC headroom below a re-tightened ceiling.
- Full suite remains green (~6575 collected); publish/resume behavior unchanged,
  confirmed by the existing E2E pipeline (`seed.jsonl | plan | validate | publish`).

## Scope Boundaries
- **Not** a *retroactive* hard cap — every existing function above the backstop is
  grandfathered via a seeded ceiling, not force-rewritten. The backstop (proposed CC ≤ 30)
  is a forward cap only on net-new/modified code that tries to land *born-complex*; normal
  new code under it sees zero friction.
- **Not** touching the ~6 seeded E/F-rank functions beyond locking their baseline (and the
  one `_run_resume` decomposition); the ~24 D-rank functions (CC 21–30) are under the
  backstop and not even listed. Opportunistic cleanup is out of scope.
- **Not** changing mypy's non-blocking status or the monolith SLOC budget.
- **Not** addressing test-suite runtime (125s for the full suite — measured, not a
  bottleneck) or the residual vulture/TODO items (trivial).
- **No** functional/product behavior change — pure internal-quality + guardrail work.

## Key Decisions
- **Gate model = budget file in the shape of `monolith_budget.toml`** (vs. ratchet-only or
  global hard cap): reuses a pattern the team already trusts and reviews via `git blame`.
  Seed cost is small — only ~6 E/F-rank entries above a CC-30 backstop, not the ~50 a low
  default would require. **Open sub-decision (see Outstanding Questions):** extend the
  *existing* `test_no_monolith_regrowth.py` machinery with a CC dimension (it already imports
  radon, enforces ≥80-char rationale, and pins a canary) rather than stand up a parallel
  test + budget file — extending is the leaning default.
- **Enforcement = named set + high backstop** (resolved 2026-05-29, vs. default-on-everything
  or named-set-only): seeded functions locked individually, all others capped at a high
  backstop (~CC 30). Chosen because the observed failure mode is *born-complex* functions
  (`_run_resume` hit 62 in one extraction), which a named-set-only gate would miss and a
  low default-on-everything cap would over-tax / invite metric-gaming via helper-file
  proliferation.
- **Scope = gate + decompose only `_run_resume`** (resolved 2026-05-29, narrowed from
  all-three): the gate is the durable, compounding win; `_run_resume` (CC 62) is decomposed
  because it is the lone extreme outlier and carries no budget today, while
  `_generate_payload` (50) and `publish.main` (49) are seeded/grandfathered and decomposed
  lazily when next touched — limiting blast radius on the publish path to one function now.
- **Tool = radon CC** — already a dev dependency (`radon==6.0.1`) and already the unit the
  monolith budget's tooling speaks, so no new dependency and consistent measurement.

## Dependencies / Assumptions
- Reference to **adapt** (not copy verbatim): `tests/test_no_monolith_regrowth.py`,
  `monolith_budget.toml`, `tests/fixtures/sloc_canary.py`. The structure transfers; the
  SLOC-specific policy (`round_up_to_10(+30)`, `radon.raw.analyze().sloc`) does not — CC
  uses `radon.complexity.cc_visit()` and needs its own ceiling convention (R4).
- Seeding source: with a CC-30 backstop the seed set is just the **E/F-rank blocks (CC ≥ 31)**:
  `_run_resume` (F=62), `_generate_payload` (F=50), `publish.main` (F=49),
  `_project_checkpoint` (E=39), `save_config` (E=36), `_build_links` (E=36). Re-enumerate with
  `radon cc src -s --min E` at implementation time and key by `cc_visit` fullname. (The ~30
  D-rank+ blocks from `-n D` are mostly under the backstop and need no entry.)
- radon CC determinism across the CI matrix is **verified, not assumed**: all blocks measured
  byte-identical on Python 3.11.15 and 3.12.13 under radon 6.0.1 (and 3.14 in a spot check).
  The real drift vector is a **radon version bump**, not a Python bump — `radon==6.0.1` is
  already pinned; planning should add a CC-behavior canary (a hand-crafted fixture with a
  pinned expected CC) so a future radon upgrade fails loudly rather than silently re-seeding.

## Outstanding Questions

### Reviewer challenges (status)
- ✅ **RESOLVED (2026-05-29) — decomposition scope.** CC is a proxy, not a defect rate, and
  the decompositions touch the highest-blast-radius path, so scope was narrowed to the gate
  + `_run_resume` only (the CC-62 outlier). `_generate_payload` and `publish.main` are
  grandfathered and decomposed lazily. This also moots the "F-rank cutline vs E-rank"
  challenge — nothing below CC 62 is decomposed now; everything else is seeded and locked.
- ✅ **RESOLVED (2026-05-29) — R3 enforcement model.** Named set + high backstop: seeded
  functions locked individually; unlisted functions capped at a high backstop (~CC 30). Catches
  born-complex new functions without taxing normal PRs or inviting helper-file gaming.

### Deferred to Planning
- [Affects R3][Technical] Exact backstop value — proposed CC 30 (D/E boundary). It sets the
  seed floor (R3: floor = backstop + 1) and thus which blocks get seeded (CC ≥ 31 at 30).
  A lower backstop pulls more functions into the seed set; confirm in planning.
- [Affects R6][Technical] Extraction boundaries for `_run_resume` — which sub-steps become
  named helpers vs. a sub-module, and the target CC of each extracted unit — is a
  codebase-exploration decision for `/ce:plan`. Behavior-pinning tests must come before
  extraction (thin direct coverage today).
- [Affects R1][Technical] Extend the existing `test_no_monolith_regrowth.py` machinery with a
  CC dimension (now the leaning default) vs. a parallel test file. Both scan `src/` via the
  same `rglob`, so a shared scan helper is feasible.

## Next Steps
All blocking decisions resolved (scope = gate + `_run_resume`; enforcement = named set + CC-30
backstop; budget-file model; extend existing test machinery). No `Resolve Before Planning`
items remain. → `/ce:plan` for structured implementation planning.

---
**Aside (out of scope, flagged for the operator):** the full-suite run on 2026-05-29
showed one pre-existing failure — `tests/test_webui_settings_template_split.py::test_settings_html_final_size`
(a settings-HTML size-budget breach, likely from the in-flight channel-grouping work in
commits `dd31e047` / `94432b9a`). Unrelated to this brainstorm; surfaced here so it isn't lost.
