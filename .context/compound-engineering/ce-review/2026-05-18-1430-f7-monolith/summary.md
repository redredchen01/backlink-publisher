# ce:review run 2026-05-18-1430-f7-monolith

**Mode:** autofix
**Base:** `08d0b7e` (origin/main)
**Branch:** `feat/monolith-sloc-ceiling`
**Plan:** `docs/plans/2026-05-18-006-feat-monolith-sloc-ceiling-plan.md` (explicit via `plan:` arg)
**Intent:** Implement R5 F7 monolith SLOC ceiling structural test. 5 units: radon pin, monolith_budget.toml seed, pytest assertion + SLOC canary fixture, CI radon-version print, AGENTS.md note.

## Reviewer Team

| Reviewer | Selection reason |
|---|---|
| correctness | always-on |
| testing | always-on |
| maintainability | always-on |
| project-standards | always-on |
| agent-native | always-on |
| learnings-researcher | always-on |
| kieran-python | Python module changes (244 + 77 LOC across 2 new files) |

Skipped: security/performance/api-contract/data-migrations/reliability/previous-comments (no signals), adversarial (heavy docs share; substance had 2 prior adversarial rounds), CE conditional (no migrations).

## Findings

### P1 — High

| # | File | Issue | Reviewer(s) | Confidence | Route |
|---|---|---|---|---|---|
| 1 | AGENTS.md:22 | 4 of 5 monitored paths abbreviated (missing `src/backlink_publisher/` prefix); mismatch with monolith_budget.toml | correctness, project-standards, agent-native | ~1.0 (3-reviewer agreement boost) | safe_auto → review-fixer |

### P3 — Low

| # | File | Issue | Reviewer(s) | Confidence | Route |
|---|---|---|---|---|---|
| 2 | tests/fixtures/sloc_canary.py:32 | `from pathlib import Path` unused at runtime (intentional construct exemplar for radon counter) | kieran-python | 0.95 | safe_auto → review-fixer (resolved via `# noqa: F401` to document intent) |
| 3 | tests/fixtures/sloc_canary.py:35-36 | `if TYPE_CHECKING: Iterable` unused (intentional exemplar) | kieran-python | 0.92 | safe_auto → review-fixer (resolved via `# noqa: F401`) |

## Applied Fixes

1. **AGENTS.md:22** — Expanded all 5 file paths to use full `src/backlink_publisher/` prefix, matching `monolith_budget.toml`. Resolves cross-reviewer consensus from correctness + project-standards + agent-native.
2. **tests/fixtures/sloc_canary.py:32 + :36** — Added `# noqa: F401` markers with rationale comments documenting that these imports are intentional construct exemplars for radon's SLOC counter (not "unused" from the fixture's design perspective). SLOC count unchanged (verified: still 31).

## Residual Actionable Work

None. All findings are either auto-applied or advisory.

## Advisory / By-Design Findings (report-only)

| # | Source | Note |
|---|---|---|
| A1 | testing-reviewer TF1 (0.85) | Module-level `BUDGET = tomllib.loads(...)` at collection time produces raw `FileNotFoundError` traceback on missing file rather than action-suggesting message. Plan documented as by-design (R5: missing/malformed = pytest collection error). Future option: wrap with try/except → custom pytest.fail message. Not blocking. |
| A2 | testing-reviewer TF2 (0.75) | `test_policy_to_seed_drift` allows `headroom >= 0`. Strict `> 0` would catch a typo where `ceiling = current_SLOC` exactly. Counter-argument: a deliberate post-extraction ratchet could legitimately set ceiling to current_SLOC. Current `>= 0` is the safer default. |
| A3 | maintainability M1 (0.62) | `SLOC_CANARY_FIXTURE`/`SLOC_CANARY_EXPECTED` naming pair could be more explicit (`_PATH`/`_COUNT`). Cosmetic; current names are comprehensible. |
| A4 | maintainability M2 (0.58) | Magic numbers 50, 500, 31 documented inline. Adequate. |
| A5 | learnings L1 (high relevance) | `docs/solutions/test-failures/tests-coupled-to-operator-config-state-2026-05-18.md` recommends env-var override + session-scope isolation for repo-config tests. F7's design explicitly diverges (R5 design choice: collection-time error is the desired behavior on missing budget file). Captured for future revisit. |
| A6 | project-standards | F7 test docstring references plan + brainstorm but not the upstream ideation doc (`docs/ideation/2026-05-18-round5-fresh-pass-ideation.md`). Not required; noted for completeness. |

## Pre-existing (not caused by this PR)

3 test collection errors exist on `origin/main` HEAD, unrelated to F7:
- `tests/test_events_store.py` — imports `from backlink_publisher.events import store as store_module`
- `tests/test_events_schema.py` — same package
- `tests/test_url_utils_canonicalize.py` — imports `url_utils.canonicalize_url` (moved to `_util/url.py` by PR #50 but tests not updated)

These will surface in CI but are baseline failures on `main`. Tracking them is owned by the team(s) that did PR #48 / PR #50.

## Coverage

- **Suppressed:** 0 findings (none below 0.60 confidence).
- **Untracked files excluded:** none — all F7 changes staged before review.
- **Reviewers completed:** 7/7.
- **Reviewers failed/timed-out:** 0.
- **Tests:** F7 alone 20/20 PASS (0.89s); full suite minus 3 pre-existing collection errors 1613/1613 PASS (17.72s).

## Verdict

**Ready to merge** (after final commit + PR creation by parent /ce:work workflow).

Code is clean, test coverage is comprehensive, plan requirements R1-R12 all advanced by Units 1-5. The cross-reviewer-consensus AGENTS.md path bug was the only material P1 — auto-fixed in this run. The pre-existing collection errors on `main` are not blocking and not this PR's responsibility.

## Requirements Completeness (plan source: explicit)

| R | Description | Status |
|---|---|---|
| R1 | radon SLOC metric | ✓ Unit 3 uses `radon.raw.analyze(text).sloc` |
| R2 | 5 named monitored files (post-PR48/PR50 paths) | ✓ monolith_budget.toml entries match plan |
| R3 | 2-field schema (ceiling, rationale ≥80 chars) | ✓ verified by `test_entry_schema` |
| R4 | Hard-fail on SLOC > ceiling | ✓ `test_sloc_within_ceiling` parametrized |
| R5 | Hard-fail on missing/malformed budget / short rationale | ✓ collection-time + `test_entry_schema` |
| R6 | radon pinned exact + bump-edits-budget rule | ✓ `radon==6.0.1` in pyproject; documented in AGENTS.md |
| R7 | Warning-only canary for undeclared >500 SLOC | ✓ `test_warning_canary_for_undeclared_large_files` + synthetic verification |
| R8 | Same-PR bump policy | ✓ documented in AGENTS.md |
| R9 | Journal-not-gate framing | ✓ AGENTS.md Monolith Budget section + failure messages |
| R10 | F7 ≠ config decomposition | ✓ documented in AGENTS.md |
| R11 | Initial ceilings = round_up_to_10(current_SLOC + 30) | ✓ verified by `test_policy_to_seed_drift`; seed values 1270/730/370/340/320 |
| R12 | Initial rationale fields explain expected settling shape | ✓ all 5 rationales reference settling plan |
