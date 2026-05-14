---
title: "Multi-persona document-review catches runtime-breaking errors at plan time (before any code is written)"
date: 2026-05-14
category: docs/solutions/best-practices
module: workflow / ce:plan + document-review
problem_type: best_practice
component: development_workflow
severity: medium
applies_when:
  - "Writing an implementation plan for a non-trivial feature (Standard or Deep complexity)"
  - "Plan touches multiple modules / cross-cutting concerns / new architectural seams"
  - "Plan includes pseudo-code, function signatures, or named file/test paths that an implementer will later trust verbatim"
  - "Plan resolves >=5 product decisions whose rationale will outlive memory of the brainstorm session"
tags:
  - plan-review
  - document-review
  - ce-plan
  - multi-persona
  - pre-implementation
  - feasibility-reviewer
  - workflow
---

# Multi-persona `document-review` catches runtime-breaking errors at plan time

## Context

The Backlink-Publisher feat `feat/mandatory-linkcheck-lang-gate` (PR #10) was a 13-requirement, 8-unit plan that touched `validate_backlinks.py`, `publish_backlinks.py`, `language_check.py`, `linkcheck.py`, `checkpoint.py`, `schema.py`, plus 5 new files and a test-sweep. Before any code was written, the plan was reviewed by the `document-review` skill — which dispatches a configurable set of persona agents in parallel (coherence, feasibility, scope-guardian, adversarial, plus optional product/design/security lenses).

This documents the concrete value the multi-persona pass delivered on this specific plan, so future contributors know **when** the heavier review is worth invoking and **what** it actually catches that the lighter `ce-plan` confidence check (Phase 5.3) does not.

The auto-memory entry `feedback_cereview-finds-latent-bugs.md` (auto memory [claude]) previously recorded the same pattern for `ce:review` (post-code review) catching XML injection, dedup bypass, and duplicate-anchor bugs. This learning is the **pre-code** sibling.

## Guidance

**For Standard or Deep plans, run `document-review` on the plan file before invoking `/ce:work`.** The mandatory `ce:plan` Phase 5.3.8 already does this, but contributors writing plans by hand or via other tooling should invoke `document-review` explicitly. Concretely:

```text
Skill("compound-engineering:document-review", "docs/plans/<your-plan>.md")
```

Or in pipeline mode:

```text
Skill("compound-engineering:document-review", "mode:headless docs/plans/<your-plan>.md")
```

The persona set is selected automatically based on plan content. For a typical feature plan, that's:

- **coherence-reviewer** (always-on) — cross-checks dependencies, terminology drift, internal contradictions
- **feasibility-reviewer** (always-on) — has file-read access; **verifies every cited line number, signature, and file path against the actual repo**
- **scope-guardian-reviewer** (activated when >8 requirements or explicit "Out of v1" sections)
- **adversarial-document-reviewer** (activated when >5 units + explicit architectural decisions)

The feasibility reviewer is the single highest-value persona for runtime-error prevention. Its repo-grounding ability is what catches signature mismatches and stale path claims.

## Why This Matters

**Concrete count of runtime-breaking errors this caught on plan 2026-05-14-001 before any code was written:**

1. **`get_anchor_pool_v2` signature mismatch (would crash at first invocation).** Plan repeatedly wrote `get_anchor_pool_v2(config, row['main_domain'])['home']['branded']`, treating it like a nested-dict getter. The actual signature is `(config, main_domain, url_category, anchor_type) -> list[str]` (4 positional args returning a flat list). The wrong call shape would have produced `TypeError: get_anchor_pool_v2() missing 2 required positional arguments` on first execution. **Caught by feasibility-reviewer** via direct read of `config.py:701-726`.

2. **Logger `.warning()` vs `.warn()` (would crash at first log call).** Plan's pseudo-code used `validate_logger.warning(...)` and `publish_logger.warning(...)`. The project's `PipelineLogger` (defined in `logger.py:39-48`) exposes only `.debug/.info/.warn/.error`. Existing call sites use `.warn`. The plan's verbiage would produce `AttributeError: 'PipelineLogger' object has no attribute 'warning'`. **Caught by feasibility-reviewer** via direct read of the logger module.

3. **`tests/conftest.py` does NOT exist (plan said "Modify").** The plan's Unit 5 file list claimed `Modify: tests/conftest.py`. Verification: `find tests/ -name conftest.py` returned empty. Implementer following the plan verbatim would either fail at "no such file" or silently write nothing. **Caught by feasibility-reviewer** via `find`.

4. **Circular Unit 5 ↔ Unit 6 dependency.** Plan declared Unit 5 depends on Unit 6 (correct — Unit 5 reads the flag Unit 6 adds), AND Unit 6 depends on Unit 5 ("the gate it's controlling exists" — wrong; the flag's existence doesn't require its consumer). The actual relationship is one-way. **Caught by coherence-reviewer** via dependency-graph check.

Beyond the four runtime-breakers, the same pass also surfaced:

- **A silent product-level scope reduction** (R4 kind-scoping was added during planning without flagging to the user) — adversarial-reviewer flagged as P1, returned to user for explicit decision.
- **Test sweep effort blindness** (plan deferred the "count affected tests" grep that the brainstorm explicitly required) — adversarial reviewer caught and the grep was run inline, confirming Unit 8 was half-day work, not week-long.
- **Branded-pool TOCTOU** (the original plan's runtime config-lookup left a window where pool edits between validate and publish could flip rows) — adversarial reviewer surfaced, resolution was payload-first + config-fallback (closes the window via plan-time snapshot).

**Cost of NOT running document-review on this plan**: 4 separate "huh, this crashes immediately" debug loops during implementation, plus a likely-shipped silent scope reduction, plus probably-discovered-mid-PR TOCTOU bug.

**Cost of running document-review**: 4 parallel sub-agent dispatches (~2 minutes wall time), reading the 568-line plan file once.

The math is one-sided. **For any plan touching >5 modules or naming specific signatures, the heavy review pays for itself many times over.**

## When to Apply

**Always:**
- After `/ce:plan` writes a Standard or Deep plan (the skill does this automatically in Phase 5.3.8 — don't bypass it)
- When a plan is written by hand or by another tool and is about to be handed to an implementer

**Especially when:**
- Plan cites specific function signatures, line numbers, or file paths the implementer will trust verbatim
- Plan introduces new modules or new public API surfaces
- Plan touches existing modules' contracts (deprecation aliases, schema changes, CLI flag renames)
- Plan resolves multiple product decisions whose rationale will outlive immediate memory

**Skip / lighten when:**
- Lightweight plan (2-3 units, trivial changes, pure docs, single-file rename)
- The plan is purely declarative and contains no signatures or path claims
- The contributor is already pair-reviewing in real time with another engineer

**Headless mode** is for automation pipelines (LFG / SLFG / disable-model-invocation contexts) — it applies safe auto-fixes silently and returns structured findings without interactive prompts. Use it when the plan-review is part of a larger automated workflow; otherwise interactive mode is the default.

## Examples

### Example 1: signature drift caught at plan time vs at runtime

**Plan-as-written** (excerpt from the original `2026-05-14-001` plan before document-review):

```markdown
## Key Technical Decisions

- ... validate-backlinks loads Config at startup so each row's
  `branded_pool` can be looked up via
  `get_anchor_pool_v2(config, row['main_domain'])['home']['branded']`.
```

**Feasibility reviewer's finding** (raw JSON shape; severity P0, confidence 0.95):

> `config.py:701-726` defines `get_anchor_pool_v2(config, main_domain, url_category, anchor_type) -> list[str]`. The function takes 4 positional args and returns a flat list. Plan repeatedly writes `get_anchor_pool_v2(config, row['main_domain']).get('home', {}).get('branded', [])`. That call would raise TypeError (missing required arg) and the chained .get('home', {}).get('branded', []) is incoherent against a list[str] return.

**Auto-fix applied**: plan globally rewritten to `get_anchor_pool_v2(config, row['main_domain'], 'home', 'branded')`.

**Counterfactual cost**: implementer copies the wrong shape into `validate_backlinks.py` step 1, runs the first test, crashes. Debug loop: read traceback → find the call → check the source → fix. ~15 min × every site the plan used the wrong shape (3 occurrences). The plan-time fix took one targeted Edit call.

### Example 2: dependency graph check catches a circular cross-unit claim

**Plan-as-written** (Unit 5 and Unit 6 both touched `publish_backlinks.py`):

```markdown
- [ ] **Unit 5: Publish-time per-row reachability re-check (R8/R9/R12)**
  **Dependencies:** Unit 4 (uses check_url), Unit 6 (gated by --skip-publish-time-check).

- [ ] **Unit 6: --skip-publish-time-check flag + checkpoint flag persistence**
  **Dependencies:** Unit 5 (the gate it's controlling exists).
```

**Coherence reviewer's finding**: "Unit 6 incorrectly lists Unit 5 as a dependency. The relationship is one-way: Unit 5 depends on Unit 6, not vice versa."

**Auto-fix applied**: Unit 6 dependencies changed to "None"; sequencing note added clarifying Unit 6's argparse change must land first within the shared file.

**Counterfactual cost**: implementer sees both units list each other as deps and stalls on which to start. Either picks one and discovers mid-edit that the flag they're reading doesn't exist yet, or interprets the dep loosely and ships out-of-order commits that don't bisect cleanly.

### Example 3: file existence check catches stale path claims

**Plan-as-written** (Unit 5 file list):

```markdown
**Files:**
- Modify: tests/conftest.py (extend autouse HTTP mock fixture to cover check_url callsite)
```

**Feasibility reviewer's finding**: "tests/conftest.py does NOT exist — plan claim is stale. `find tests/ -name conftest.py` returns empty. There is no existing autouse fixture file."

**Auto-fix applied**: "Modify" changed to "Create"; note added that existing tests carry per-file autouse mocks and the new conftest is additive (don't mass-migrate in this PR).

**Counterfactual cost**: implementer opens `tests/conftest.py`, sees an empty buffer (or `No such file` error), spends time figuring out whether the file was supposed to exist, whether they should grep for an alternate location, whether the plan was wrong, etc.

## Related Issues

- `docs/plans/2026-05-14-001-feat-mandatory-linkcheck-lang-gate-plan.md` — the plan whose document-review pass surfaced the four runtime-breakers documented above. Search for the term "auto-fix" or read the post-review session log entries.
- `docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md` — sibling compound from the same session. That entry's prevention rule ("verify claims against actual code, don't trust appearances") is the **post-bug** angle; this entry is the **pre-bug** angle of the same principle. Cross-link these whenever introducing new contributors to the review discipline.
- Auto memory `feedback_cereview-finds-latent-bugs.md` (auto memory [claude]) — equivalent learning for `ce:review` (post-code review). 10-persona review caught XML injection, dedup bypass, duplicate anchor. Together, the two lessons cover both ends of the pipeline: pre-code via `document-review` on the plan, post-code via `ce:review` on the diff.
- Auto memory `feedback_plan-vs-code-drift.md` (auto memory [claude]) — concrete reminder that "plan对'现有 API'描述可能 stale；动既有模块前重读源". The feasibility reviewer is the automated enforcement of this discipline; this doc is its anecdotal evidence.
