---
title: "fix: Enforce CLI exit-code contract (fix out-of-range exit_code=45 + add guard test)"
type: fix
status: active
date: 2026-05-25
claims:
  paths:
    - src/backlink_publisher/cli/_publish_helpers.py
    - src/backlink_publisher/_util/errors.py
    - src/backlink_publisher/cli/plan_check.py
    - src/backlink_publisher/cli/_report_format.py
    - tests/test_token_revocation_midrun.py
  shas:
    - "b037364"
---

# fix: Enforce CLI exit-code contract (fix out-of-range exit_code=45 + add guard test)

## Overview

The pipeline CLIs document a 0–6 exit-code contract, but `AGENTS.md:139` itself flags
it as *"a documented contract, not enforced by `sys.exit()` in CLI code"*. A static
audit of every exit literal in `src/` confirms exactly one violation:
`src/backlink_publisher/cli/_publish_helpers.py` raises `exit_code=45` from
`_check_token_drift` — a long-standing out-of-range value, born `45` in `d186f3b`
(2026-05-20) and merely relocated by the `#186` helper extraction.

**The `45` is not unguarded — it is *enshrined*.** `tests/test_token_revocation_midrun.py`
(on `origin/main`, 2026-05-22) has the docstring *"the publisher must abort with code 45"*
and asserts `exc.code == 45`. So a test froze the contract-violating value rather than the
contract. This is the textbook way an out-of-range exit becomes permanent without a
*contract* guard — making this plan more justified, not less: the existing test pins the
**specific value**; nothing enforces the **0–6 contract**. (An earlier draft of this plan
wrongly claimed "no test pins 45"; the document-review coherence pass caught it.)

This plan therefore (a) fixes the out-of-range exit and updates the test that enshrines
it, and (b) adds the missing contract guard so a future drift fails CI.

Both files it touches (`cli/_publish_helpers.py` and `tests/test_token_revocation_midrun.py`)
are byte-identical across `origin/main` `b037364` and the active feature branches.

> **Coordination note (2026-05-25, added at hand-off).** When this plan was authored the
> exit-code work was un-owned. While it was being written, a concurrent agent claimed
> branch **`test/cli-exit-code-contract`** (pushed to origin) and committed
> `tests/test_exit_code_contract.py`. **This plan is now a complementary spec, not a fresh
> build** — see "Division of labor vs. `test/cli-exit-code-contract`" below. It is handed
> over (not implemented) precisely to avoid colliding with that live branch.

### Division of labor vs. `test/cli-exit-code-contract`

The concurrent branch and this plan are complementary; together they fully close the gap.

| Concern | `test/cli-exit-code-contract` (exists) | This plan (the gap) |
|---|---|---|
| Lock `_util.errors` `PipelineError` subclass codes (Arm 2) | ✅ done — `test_exit_code_contract.py` | (defer to that branch; do not duplicate) |
| Force new `PipelineError` subclass to declare its code | ✅ done | — |
| Parentage invariants (AntiBot=4, AuthExpired=3, siblings) | ✅ done | — |
| **Fix the actual `exit_code=45` violation** | ❌ **not fixed** (still `45`) | ✅ **Unit 1** |
| **Arm 1 — AST literal scan of CLI source** (the only arm that *catches* the `45`) | ❌ absent — an errors.py-only test cannot see a literal kwarg in `_publish_helpers.py` | ✅ **Unit 2 Arm 1** |
| Update the test that enshrines `45` | ❌ | ✅ **Unit 1** |
| Arm 3 — `plan_check.py`'s `7`/`8` table (incl. code `8` class attr) | ❌ (their contract is 0–6 only) | ✅ **Unit 2 Arm 3** |
| Named constant `_EXIT_CODE_ALARM=6`, subpackage recursion | ❌ | ✅ **Unit 2 Arm 1** |

**Key correctness point:** the concurrent branch's guard validates `_util.errors` class
attributes, so it would stay green while `exit_code=45` lives on. Closing the bug *requires*
the source fix (Unit 1) and the literal scan (Unit 2 Arm 1). The cleanest integration is for
the owner of `test/cli-exit-code-contract` to fold Units 1 + Arm 1 + Arm 3 into that branch
rather than open a second branch on the same surface.

## Problem Frame

The six pipeline entrypoints chain via exit codes — a downstream step (or an operator
script) reads the exit code to decide whether to continue, retry, or abort. An exit
code of `45` is outside the documented 0–6 contract, so any consumer that
discriminates on the documented codes (e.g. "3 = fix credentials and re-run",
"4 = transient, retry later") will mis-handle the *one* condition the code is meant to
signal — a mid-run credential/config drift abort, which is precisely the
safety-critical path where correct signalling matters most.

Two distinct exit-code contracts coexist and must not be conflated:

| CLI surface | Valid exit codes | Source of truth |
|---|---|---|
| Pipeline CLIs (`plan`/`validate`/`publish`/`report-anchors`/`footprint`/`phase0-seal`) | `0,1,2,3,4,5,6` | `_util/errors.py` `PipelineError` subclasses + `_report_format._EXIT_CODE_ALARM=6` |
| `plan-check` CLI (dev tooling) | `0,1,2,7,8` | `cli/plan_check.py` + `AGENTS.md:201-203` |

A naive "all exits must be ≤6" check would false-positive on `plan_check.py`'s
legitimate `7`/`8`. The guard test must therefore be **contract-aware** (per-module
allowed sets), not a single global bound.

Note a structural subtlety surfaced in review: `plan-check`'s `7` appears as a literal
(`exit_code=7` / `SystemExit(7)`), but `8` exists **only as a class attribute**
(`exit_code: int = 8`) on a non-`PipelineError` exception (`PlanClaimsMissingOnPostCutoff`),
reached dynamically via `raise SystemExit(exc.exit_code)`. So `8` is never a literal and is
not a `PipelineError` subclass — the guard must collect class-body `exit_code` attributes
from `plan_check.py`'s exception classes too, or it silently fails to protect code `8`
(see Unit 2 Approach).

## Requirements Trace

- R1. The `_check_token_drift` mid-run abort exits with a code inside the 0–6 pipeline
  contract, with a value whose documented semantics match the failure (operator must
  act on changed credentials/config).
- R2. A test enforces the exit-code contract so a future out-of-range literal fails CI
  rather than shipping silently — covering both the pipeline 0–6 table and the
  `plan-check` 0/1/2/7/8 table.
- R3. No behavioral change to any exit path other than the corrected `45`→`3` value and
  the matching update to the test that enshrined `45`.

## Scope Boundaries

- **Not** re-numbering or re-classifying any other exit code — every other literal
  (`0,1,2,3,4,5` in pipeline CLIs; `7` in `plan_check.py`) is already in-contract and
  stays as-is.
- **Not** touching the three active branches' files (no `publishing/`, `events/`,
  `config/`, `cli/plan_backlinks/`, ledger, or `errors.py` *edits* — `errors.py` is
  read as the contract source only, not modified).
- **Not** converting the documented exit-code table into a runtime-emitted machine
  contract or changing `emit_error`'s default (`5`).
- **Not** auditing exit codes of the WebUI Flask layer (HTTP status, not process exit).
- Dynamic exits that re-raise a `PipelineError`'s own `exit_code` (e.g.
  `raise SystemExit(exc.code)`) are validated indirectly via the `errors.py`
  subclass assertion, not by literal scanning — see Key Technical Decisions.

## Context & Research

### Relevant Code and Patterns

- **Contract source** — `_util/errors.py`: `PipelineError.exit_code: int = 5` (default)
  with subclasses `UsageError=1`, `InputValidationError=2`, `DependencyError=3`
  (+ `AuthExpiredError`/`BannerUploadError`/`ContentRejectedError` all `=3`),
  `ExternalServiceError=4` (+ `AntiBotChallengeError=4`), `RegistryError=5`,
  `InternalError=5`. `emit_error(message, exit_code=5)` and `handle_error(exc)` /
  `handle_unexpected_error(exc)` are the three exit funnels.
- **The bug** — `cli/_publish_helpers.py` `_check_token_drift(initial_revs)`:
  on detecting a token-rev change mid-run it calls
  `emit_error("...updated mid-run. Aborting to prevent using revoked credentials.", exit_code=45)`.
  The surrounding `publish_backlinks` exit cluster uses `exit_code=3` pervasively for
  credential/config-precondition failures.
- **`6` is legitimate** — `cli/_report_format.py:32` `_EXIT_CODE_ALARM: int = 6`
  (anchor-alarm verdict), referenced as a named constant rather than a bare literal.
- **The separate `plan-check` table** — `cli/plan_check.py:910,918` use `exit_code=7`
  / `SystemExit(7)`; `8` is the claims-gate code (`AGENTS.md:158-160`).
- **Full audit (this plan's grounding)** — every exit literal across `src/**/*.py`:
  `exit_code` ∈ {0,1,2,3,4,**45**,5,7}; `SystemExit(...)` ∈ {0,2,4,5,7}. Only `45` is
  out of every contract.
- **CI style gate** — CI uses `py_compile` + `ast.parse` (not Black/flake8), so an
  AST-based static scan is consistent with the repo's existing tooling approach.

### Institutional Learnings

- No `docs/solutions/` entry covers exit codes or contract tests — this gap is fresh,
  so there is no prior art to mirror and no superseding decision to honor.
- Test isolation: four autouse `conftest` fixtures (sandbox config / URL pass / content
  pass / socket block) apply by default; the behavioral test for `_check_token_drift`
  must not require network and should stub `snapshot_token_revs` to force drift.

### External References

None. This is an internal contract with strong local grounding; no framework or
third-party behavior is involved.

## Key Technical Decisions

- **`45` → `3` (DependencyError family), and update the test that enshrines `45`.** The
  abort fires when configuration/credentials changed mid-run and signals the operator must
  act (re-run against settled config / re-check rotated credentials) — textbook
  `DependencyError` per the `errors.py` family rule ("user must take action"), and
  consistent with the surrounding `exit_code=3` cluster in the publish path. **Why `45` is
  a violation, not an intentional sentinel:** it is the *sole* out-of-range value in the
  entire codebase, the documented contract (`AGENTS.md`, `errors.py`) defines no meaning
  for `45`, and `test_token_revocation_midrun.py`'s docstring merely restates the value
  ("must abort with code 45") without any rationale for choosing `45` over `3` — i.e. the
  test codified whatever the code happened to do. The `3`-vs-`5` fork resolves to `3` on
  semantics. **The fix is two coordinated edits, not one:** the literal in
  `_publish_helpers.py` *and* the assertion + docstring in
  `tests/test_token_revocation_midrun.py` (otherwise CI fails on the frozen `== 45`).
  Operator confirmation point (low-cost, see Open Questions): confirm `45` was not a
  deliberately-distinguished sentinel for the security-sensitive revocation abort before
  collapsing it into the generic `3`.
- **Guard test is a contract-aware AST scan, not a runtime probe.** An `ast.parse` walk
  over every `*.py` under `cli/` — **recursing into subpackages** (`plan_backlinks/`,
  `_bind/`), not just top-level `cli/*.py` — collects integer-literal exit values from
  `SystemExit(<int>)` and `exit_code=<int>` keywords, then asserts membership in a
  per-module allowed set: `{0,1,2,7,8}` for `plan_check.py`, `{0,1,2,3,4,5,6}` for every
  other CLI module. Recursion is load-bearing: `plan_backlinks/_work_themed.py:161` carries
  `exit_code=4` and would be missed by a single-level glob. This matches CI's existing
  AST-based style checks and needs no subprocess.
- **Three collection arms, because the contract lives in three shapes.** (1) bare
  `SystemExit(<int>)` and `exit_code=<int>` literals in `cli/` (the scan above);
  (2) `PipelineError` subclass `exit_code` class attributes in `_util/errors.py` (assert
  ∈ {0..6}); (3) **class-body `exit_code` attributes on `plan_check.py`'s own exception
  classes** (e.g. `PlanClaimsMissingOnPostCutoff`, which is a plain `Exception`, not a
  `PipelineError`) — assert ∈ {0,1,2,7,8}. Arm 3 is required because code `8` exists *only*
  as such a class attribute (never a literal, never a `PipelineError`); without it the
  guard silently fails to protect the `plan-check` surface, defeating R2 there.
- **Allowed-set definitions live in the test module, not in `errors.py`.** Keeping the two
  contract tables as test-local constants avoids editing the contended `errors.py` and
  keeps source changes to `_publish_helpers.py` + the enshrining test (plus the new test
  file).
- **Dynamic re-raises are bounded at their source funnel, not by the subclass walk.** The
  three `raise SystemExit(exc.code)` sites (`publish_backlinks.py`, `validate_backlinks.py`,
  `plan_backlinks/core.py`) catch a `SystemExit` raised by `read_jsonl` and re-propagate its
  `.code` — `exc` is a `SystemExit`, **not** a `PipelineError`, so the errors.py subclass
  walk does *not* cover these. Their values are transitively bounded because `read_jsonl`
  exits via `emit_error(..., exit_code=<int>)`, which the literal scan *does* catch at the
  `emit_error` call site. (An earlier draft wrongly claimed the subclass walk covered these;
  corrected per feasibility review.)
- **Named-constant and callee handling.** `_EXIT_CODE_ALARM` (`= 6`) is resolved by also
  collecting module-level `NAME = <int>` assignments; if brittle, allow-list with a comment
  and document the limitation. The scan collects `exit_code=<int>` keywords **callee-
  agnostically** (not only on `emit_error`) so it also catches non-funnel kwargs like
  `plan_check.py`'s `_build_json_payload(..., exit_code=7/0)` — these are in `plan_check.py`'s
  allowed set, so no false positive, and the agnostic approach is strictly safer against a
  future out-of-range kwarg.

## Open Questions

### Resolved During Planning

- *Correct value for the `45`?* → `3` (DependencyError), on family-rule semantics +
  neighborhood consistency.
- *Does any test pin `45`?* → **Yes** — `tests/test_token_revocation_midrun.py` asserts
  `exc.code == 45` with a matching docstring. Unit 1 must update both the assertion and
  the docstring; this was missed in the first draft and corrected in review.
- *One global "≤6" bound or two tables?* → Two tables; `plan_check.py` legitimately uses
  `7`/`8`. A global bound would be wrong.
- *Modify `errors.py` to centralize the contract?* → No; `errors.py` is contended by an
  active branch and the contract is already expressed by the subclass `exit_code`
  attributes. Keep allowed sets test-local.

### Deferred to Implementation

- **Operator confirmation: was `45` a deliberate sentinel?** Low-cost check before
  collapsing to `3`. Evidence says no (sole violation, undocumented, test merely froze the
  value), but the revocation abort is security-sensitive, so confirm the operator did not
  intend a distinguished out-of-band code. If they did, the right fix is to *document* `45`
  in the contract instead — but that contradicts the 0–6 table, so the burden is on keeping
  it.
- Exact AST-collection strategy for named-constant exits (`_EXIT_CODE_ALARM`): resolve
  module-level `int` assignments vs. allow-list. Either is acceptable as long as `6` is
  recognized as valid.
- Is `cli/_bind/*` (login/bind helpers, not one of the six documented pipeline CLIs)
  subject to the 0–6 pipeline table, a separate table, or exempt? Decide when enumerating
  the scan's module set; default assumption is the 0–6 pipeline table unless a `_bind`
  exit literal proves otherwise.

## Implementation Units

This is a two-unit change with a simple linear dependency (fix first so the behavioral
assertion in the test reflects the corrected value). No dependency graph is warranted.

- [ ] **Unit 1: Fix the out-of-range `exit_code=45` and the test that enshrines it**

**Goal:** `_check_token_drift` exits with the in-contract code `3` instead of `45`, and the
test that froze `45` is updated to the corrected value — so the suite stays green and the
behavior is pinned to a contract-valid code.

**Requirements:** R1, R3

**Dependencies:** None

**Files:**
- Modify: `src/backlink_publisher/cli/_publish_helpers.py` (the `emit_error(..., exit_code=45)` call in `_check_token_drift`)
- Modify: `tests/test_token_revocation_midrun.py` (assertion `exc.code == 45` → `== 3`; docstring "must abort with code 45" → "code 3"; the inline message string)

**Approach:**
- Change the literal `exit_code=45` → `exit_code=3` in `_check_token_drift`.
- In the **same commit**, update `tests/test_token_revocation_midrun.py`: the
  `assert exc.code == 45` → `== 3`, the function docstring, and the `"Should exit with 45..."`
  message — otherwise CI fails on the frozen assertion. This is the coordinated second edit;
  it is *not* a one-character change.
- Before changing, confirm via the operator-confirmation point (Open Questions) that `45`
  was not a deliberate sentinel.

**Patterns to follow:** The adjacent credential/config-precondition exits in the publish
path already use `exit_code=3` via `emit_error` (`_publish_helpers.py` lines 66, 419).

**Test scenarios:**
- Happy path / behavioral (the updated enshrining test): mid-run token rev change causes
  `_run_resume`/`_check_token_drift` to raise `SystemExit` with `code == 3` (was `45`),
  aborting before the second item is processed, with the stderr message still naming the
  platform and the "revoked credentials" reason. Note: the existing test asserts inside an
  `except SystemExit` block (vacuously passing if no exit) — strengthen it to assert a
  `SystemExit` *was* raised (e.g. wrap in `pytest.raises(SystemExit)`), so it can no longer
  pass without the abort firing.
- Edge case: when no rev changed, `_check_token_drift` returns normally (no `SystemExit`).

**Verification:** The drifted-rev path exits `3`; `tests/test_token_revocation_midrun.py`
passes against `3`; full suite green; `git grep "exit_code=45"` and
`git grep "code == 45"` return nothing.

---

- [ ] **Unit 2: Add the exit-code contract guard test**

**Goal:** A test that fails CI if any CLI exit literal falls outside its contract table,
covering both the pipeline 0–6 table and the `plan-check` 0/1/2/7/8 table, plus the
`errors.py` `PipelineError` subclass `exit_code` attributes.

**Requirements:** R2

**Dependencies:** Unit 1 (so the behavioral assertion expects `3`)

**Files:**
- Create: `tests/test_cli_exit_code_contract.py`

**Approach:** three collection arms (see Key Technical Decisions), all `ast`/import-based,
no subprocess. Test-local constants `PIPELINE_EXIT_CODES = {0,1,2,3,4,5,6}` and
`PLAN_CHECK_EXIT_CODES = {0,1,2,7,8}` document the two tables.
- **Arm 1 — literal scan:** `ast.parse` every `*.py` under `src/backlink_publisher/cli/`,
  **recursing into subpackages** (`plan_backlinks/`, `_bind/`) via `rglob`/walk — a
  single-level glob misses `plan_backlinks/_work_themed.py:161` (`exit_code=4`). Collect
  integer-literal `SystemExit(<int>)` args and `exit_code=<int>` keywords
  (callee-agnostically). Resolve module-level `NAME = <int>` for named exits
  (`_EXIT_CODE_ALARM = 6`); allow-list with a comment if brittle. Assert each value ∈ the
  module's table (`plan_check.py` → plan-check table; all others → pipeline table).
- **Arm 2 — PipelineError attrs:** import `_util.errors`, walk `PipelineError.__subclasses__()`
  recursively, assert every `exit_code` class attribute ∈ {0..6}. **Likely already covered**
  by `test_exit_code_contract.py` on `test/cli-exit-code-contract` — reuse it, do not
  duplicate; this plan's net-new arms are Arm 1 and Arm 3 plus the Unit 1 fix.
- **Arm 3 — plan_check exception attrs:** import `cli.plan_check`, enumerate its module-level
  `Exception` subclasses carrying an `exit_code` attribute (e.g. `PlanClaimsMissingOnPostCutoff`,
  which holds `8` and is *not* a `PipelineError`), assert each ∈ {0,1,2,7,8}. Without this
  arm, code `8` is validated by neither Arm 1 (never a literal) nor Arm 2 (not a PipelineError).
- **Behavioral anchor:** the updated `tests/test_token_revocation_midrun.py` (exit `3`)
  grounds the contract in real behavior. The mid-run stub must patch the *lazily-imported*
  source — `backlink_publisher.config.snapshot_token_revs` (`_check_token_drift` does
  `from backlink_publisher.config import snapshot_token_revs` inside the function; patching a
  consumer-level name would silently fail to force drift — a documented mock-path trap in
  this repo).
- Network-free; the autouse socket-block fixture is satisfied (AST parse + imports + stubbed
  drift open no sockets — confirmed in review).

**Execution note:** Start by writing the static-scan assertion against the *fixed* tree
and confirm it passes; then temporarily re-introduce `45` locally to confirm the test
*fails* (red-proof), then revert. This guarantees the guard actually bites.

**Patterns to follow:** Repo CI already uses `ast.parse` for style gating — mirror that
parsing approach. Test naming follows `tests/test_*.py`. Use `pytest.raises(SystemExit)`
and assert `.value.code` for the behavioral case.

**Test scenarios:**
- Happy path: scanning the current tree, every CLI exit literal is in its module's
  allowed set (passes post–Unit 1).
- Red-proof (Error path): a synthetic module source string containing
  `emit_error(..., exit_code=45)` is rejected by the scan helper (proves the guard
  catches out-of-range values) — exercise the helper on an in-memory source, not by
  mutating real files.
- Edge case: `plan_check.py`'s `7` is accepted (not a false positive) because it is
  scanned against the `plan-check` table.
- Edge case: a named-constant exit resolving to `6` (`_EXIT_CODE_ALARM`) is accepted.
- Edge case (subpackage recursion): `plan_backlinks/_work_themed.py:161` (`exit_code=4`) is
  collected and accepted — proves the scan recurses, not just top-level `cli/*.py`.
- Contract-source (Arm 2): every `PipelineError` subclass `exit_code` ∈ {0..6}.
- Contract-source (Arm 3): `plan_check.py`'s non-PipelineError exception classes carrying
  `exit_code` (code `8`) are enumerated and ∈ {0,1,2,7,8} — proves code `8` is actually
  guarded, not silently skipped.
- Behavioral: the updated `test_token_revocation_midrun.py` drift case exits `3` (shared
  with Unit 1), patching `backlink_publisher.config.snapshot_token_revs`.

**Verification:** Test passes on the fixed tree; re-introducing any out-of-range literal
(e.g. `45`) makes it fail; `plan_check.py`'s `7`/`8` do not trip it.

## System-Wide Impact

- **Interaction graph:** `_check_token_drift` is called from the `publish-backlinks`
  pre-dispatch path (two call sites in `publish_backlinks.py`). Only the *value* of the
  abort exit changes (`45`→`3`); call structure is untouched. `publish_backlinks.py` is
  contended by `feat/phase1-channel-expansion` — but this plan does **not** edit it; the
  edits are isolated to `_publish_helpers.py` and `tests/test_token_revocation_midrun.py`
  (both verified byte-identical across the three active branches, so no collision).
- **Error propagation:** The corrected `3` places the abort in the `DependencyError`
  family ("operator must act"), aligning the signal with how the rest of the publish
  path reports credential/config preconditions.
- **API surface parity:** The exit code is a cross-consumer contract; the guard test now
  protects all six pipeline CLIs *and* `plan-check` at once, so a future adapter or CLI
  edit that introduces an out-of-range exit fails CI.
- **Unchanged invariants:** All other exit literals are unchanged; `emit_error`'s default
  (`5`) is unchanged; the `plan-check` 0/1/2/7/8 table is unchanged; no `errors.py`,
  `publishing/`, `events/`, or `config/` edits, so no collision with the three active
  branches. Monolith budget unaffected (one digit changed; new test file is not budgeted).

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `test_token_revocation_midrun.py` enshrines `45` → CI fails if only the source changes | **Realized, handled:** Unit 1 updates the assertion + docstring in the same commit (Files list + Approach) |
| `45` was actually a deliberate sentinel, not a typo | Operator-confirmation point (Open Questions) before collapsing to `3`; evidence strongly favors typo (sole violation, undocumented) |
| Guard misses code `8` (class attr on a non-PipelineError exception) | Arm 3 enumerates `plan_check.py`'s exception classes' `exit_code` attrs |
| Scan misses subpackage literals (`_work_themed.py` `exit_code=4`) | Arm 1 recurses into `plan_backlinks/`, `_bind/`; explicit recursion test scenario |
| Naive "≤6" guard false-positives on `plan_check.py`'s `7`/`8` | Contract-aware per-module allowed sets (explicit `plan-check` table) |
| Named-constant / dynamic exits evade the literal scan | Resolve module-level `int` constants; validate dynamic `SystemExit(exc.code)` indirectly via the `errors.py` subclass assertion; document the scan's literal-only limitation |
| Writing the plan/test file into the contended `feat/phase1-channel-expansion` worktree | Plan + new test are **new untracked files**; they do not touch the live agent's tracked edits or its running pytest. Execute on a fresh branch off `origin/main` to keep the fix isolated |

## Documentation / Operational Notes

- After landing, update `AGENTS.md:139` to note the contract is now **enforced** by
  `tests/test_cli_exit_code_contract.py` (remove the "not enforced" caveat).
- Consider a one-line `docs/solutions/` entry (the corpus has no exit-code lesson yet):
  "two coexisting exit-code contracts — guard tests must be per-surface, not a global
  bound."

## Sources & References

- Audit grounding (this session): full `git grep` of `exit_code=`/`SystemExit(` literals
  across `src/**/*.py` on `origin/main` `b037364`; `git log -S "exit_code=45"` →
  introduced in `d186f3b` (2026-05-20), relocated by `#186` (`0d21add`).
- Contract source: `src/backlink_publisher/_util/errors.py` (`PipelineError` subclasses).
- Enshrining test: `tests/test_token_revocation_midrun.py` (docstring "must abort with code
  45", asserts `exc.code == 45`) — the test Unit 1 updates.
- Pipeline exit-code caveat: `AGENTS.md:139`.
- `plan-check` exit-code table: `AGENTS.md:201-203`; `cli/plan_check.py:910,918`; code `8`
  as a class attribute on `PlanClaimsMissingOnPostCutoff`.
- Alarm exit code: `cli/_report_format.py:32` (`_EXIT_CODE_ALARM=6`).
- Review correction: the document-review coherence pass caught the false "no test pins 45"
  audit claim; feasibility's "no test pins 45 on origin/main" was contradicted by direct
  `git cat-file`/`git show origin/main` verification — the test is tracked on origin/main.
