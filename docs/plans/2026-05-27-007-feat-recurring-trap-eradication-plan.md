---
title: "feat: Recurring-Trap Eradication — Audit First, Then Guard"
type: feat
status: active
date: 2026-05-27
origin: docs/brainstorms/2026-05-27-recurring-trap-eradication-requirements.md
claims: {}  # opt-out: no unmerged SHAs to pin; required for plan-check on ≥2026-05-20 plans
---

# feat: Recurring-Trap Eradication — Audit First, Then Guard

## Overview

Turn the *genuinely recurring, mechanizable, currently-unguarded* traps in this repo's
history into permanent CI-run guards, so a fixed class can't silently come back. The work
is **audit-first**: a one-time audit over committed evidence (`docs/solutions/` + git
history) produces a dated candidate list and a count **N**; the durable-artifact shape is
then decided from N (default: inline each trap's evidence into its guard's docstring, not a
standalone register); then the first-batch guards are built and proven.

This is **backfill only**. The going-forward half already exists: `AGENTS.md` §"Lessons
capture (dual-track)" (landed via PR #265) already mandates that a regression/recurring/subtle
bug gets a failing test + a "why prior code allowed it" note + `/ce:compound` promotion. This
plan does not add a new fix-time convention (see origin: scope boundaries).

## Problem Frame

The repo's *capture* half is mature (58 committed `docs/solutions/` entries; ~91 operator-private
memory `feedback_*` files) but capture ≠ prevention — several traps have bitten more than once.
Two corpus realities surfaced in brainstorm review reshape the approach (see origin: Problem Frame):

1. **The recurrence signal is mostly uncommitted.** 72 of ~91 memory files cite ≤1 PR, and
   memory files are operator-private and **never committed** (`AGENTS.md` line 150). A committed
   guard/doc cannot auditably cite a memory filename. The auditable evidence base is
   `docs/solutions/` + git history.
2. **N is unknown — could be 0, could be 15+.** Spot-checking the brainstorm's four exemplar
   traps suggests several are already guarded or not cheaply mechanizable (see Context), but this
   is a 4-item sample, not a count. The "likely small" intuition is **not** load-bearing: the
   audit (Unit 1) produces the real N, and the artifact-shape decision (Unit 2) consumes it. We
   audit before committing to any register ceremony.

## Requirements Trace

### Audit (R1–R3)
- R1. One-time audit over committed evidence only; memory files are a discovery aid, every
  recurrence claim resolves to a committed artifact (SHA/PR/solution entry). → Unit 1
- R2. Per-candidate record (name, committed evidence, mechanizability + coverage note, current
  guard status); produce **N** = recurred ∩ mechanizable ∩ unguarded. → Unit 1
- R3. Recurrence defined from git (same root cause re-broken/reverted/re-fixed across ≥2 distinct
  commits/PRs), not from a single prospective "remember to do X" lesson. → Unit 1

### Artifact Decision (R4)
- R4. Decide durable artifact *after* the audit, from N; default to inlining evidence into guard
  docstrings unless N justifies a standalone register. → Unit 2

### Guard Construction (R5–R6)
- R5. Build an honest guard for each qualifying trap; mechanizability is a coverage judgment;
  procedural traps demote to `not-mechanizable` by design. → Unit 3, Unit 4
- R6. Guardrail honesty: red on faithful repro → green after fix; wired into `pytest tests/` CI;
  lands under `tests/`, never an orphan `scripts/check_*.py`. → Unit 3, Unit 4

### Closure & Going-Forward (R7–R8)
- R7. Completion pinned to the frozen audit snapshot; post-snapshot recurrences flow to the
  going-forward path, not back into this round. → Unit 4
- R8. Going-forward catalog stays passive; `AGENTS.md` §"Lessons capture" owns fix-time discipline
  — link, don't duplicate. → Unit 4 (no new gate)

## Scope Boundaries

- No guards for hypothetical / never-occurred problems (committed recurrence is the bar).
- Memory `feedback_*` files are not an auditable citation source.
- Not salvaging the orphan guards already retired by `guardrail-honesty`.
- Not forcing partial guards onto procedural/judgment traps — recorded `not-mechanizable`.
- No committed corpus-scanning tool that outlives the one-time audit (throwaway grep/log is fine).
- No new fix-time enforcement convention; no change to the CI style step (`py_compile`/`ast.parse`).

## Context & Research

### Relevant Code and Patterns

- **`tests/test_cli_python_m_entrypoints.py`** — the canonical R4-default precedent: its docstring
  carries the recurrence evidence ("bug class showed up twice in the same session (2026-05-20)")
  *and* the "why prior code allowed it" narrative, co-located with the executable guard. New guards
  mirror this docstring shape.
- **`tests/test_no_orphaned_guard_scripts.py`** — enforces R6's anti-orphan clause; encodes that
  the workspace-root `Makefile` is **not** a CI surface. New guards land under `tests/` (the
  `pytest tests/` surface), not as `scripts/check_*.py`.
- **`tests/test_no_monolith_regrowth.py`** — the committed-register-plus-enforcing-test pattern
  (TOML budget read by a test); the reference shape *if* Unit 2 concludes N justifies a register.
- Existing guard tests that likely already cover recurred traps (mark `guarded` in the audit):
  `test_conftest_state_net.py` / `test_csrf_guard_canary.py` (the `del os.environ` / config-dir
  poisoning class — net guard + AST gate + CSRF canary, PR #266),
  `test_webui_store_isolation` (`webui_store` frozen `_CONFIG_DIR`), `test_exit_code_contract`,
  `test_adapter_dofollow_gate`, `test_r9_extension_readiness`, `test_manifest_contract`.
  (Exact test names must be re-verified by grep during the audit — Unit 1's "every citation
  resolves to a committed artifact" rule applies to these exemplars too.)

### Institutional Learnings

- `AGENTS.md` line 150: memory `feedback_*` files are operator-private and **never committed** —
  the load-bearing fact behind R1's committed-evidence-only rule.
- `AGENTS.md` line 153: promotion strips UUIDs/domains/paths; the `private-tokens.txt` grep gate
  "passes vacuously" if the tokens file is unpopulated — relevant only if Unit 2 chooses a register
  that copies prose. The default (SHA/PR citations + docstring evidence) sidesteps this entirely.
- `AGENTS.md` lines 162–170: the going-forward "regression/recurring → failing test + why-prior-
  code-allowed-it + `/ce:compound`" overlay already exists (PR #265). R8 links to it.
- Worked classification of the brainstorm's four exemplars (validates "N is small"):
  `channel removal must prune _AUTH_TYPE_BY_PLATFORM` → likely **candidate** (registry-drift
  assertion is cheap; PR #253); `del os.environ poisons later tests` → **guarded** (#266);
  `grep all 7 legacy-import forms` and `mock.patch paths after extraction` → **not-mechanizable**
  (the full `pytest` run is the only honest tripwire; a dedicated guard would be partial → R6).

## Key Technical Decisions

- **Audit-first; artifact shape gated on N.** Don't build a register before the real count is
  known (see origin: Key Decisions). The likely outcome is single-digit N → inline-into-docstrings.
- **Committed evidence only** (`docs/solutions/` + git). A committed artifact can't auditably cite
  an uncommitted per-operator memory file; memory is a discovery aid that points at git to verify.
- **Mechanizability is a coverage judgment, not a binary.** A guard must red on a faithful repro of
  the *specific committed incident*; procedural traps whose only honest tripwire is full `pytest`
  are `not-mechanizable`, not partial guards.
- **Default artifact = guard docstrings, not a standalone register.** Co-located with the executable
  guard, can't drift, zero identifier-leak surface. Mirrors `test_cli_python_m_entrypoints.py`. The
  docstring cites **committed artifacts** (SHA / PR number / `docs/solutions/` entry title) and the
  why-prior-code-allowed-it narrative — it must **not** copy prose from uncommitted memory files
  (which is what keeps the identifier-leak surface at zero).

## Open Questions

### Resolved During Planning

- *Is R8's going-forward owner landed?* **Yes** — `AGENTS.md` §"Lessons capture (dual-track)"
  (PR #265, merged). The brainstorm flagged this as a risk; grep of `AGENTS.md` confirms the
  overlay is present. R8 is a link, not a dependency on unlanded work.
- *Where does the recurrence evidence live?* Committed: `docs/solutions/` + git history. Memory is
  discovery-only (R1).
- *Standalone register vs inline?* Default inline; register only if Unit 1's N is large (Unit 2 gate).

### Deferred to Implementation

- **Exact N and the candidate set** — knowable only by running the audit (Unit 1). The per-trap
  guard enumeration in Unit 3 depends on this output.
- **Git queries that surface revert/re-fix cycles cheaply** — a throwaway `git log`/grep approach,
  chosen at audit time; not a committed tool.
- **Per-candidate guard shape** — AST-scan (like `test_cli_python_m_entrypoints`), contract test,
  or fixture-isolation test — decided per trap once its faithful repro is understood.
- **Audit artifact location** — place the dated audit doc **outside `docs/solutions/`** (e.g.
  `docs/audits/` or adjacent to `docs/brainstorms/`) and add **no category frontmatter**. Rationale:
  there is no frontmatter/category gate so a stray doc won't fail the build, but the `docs/solutions/`
  taxonomy has already drifted (9 dirs on disk vs 7 in AGENTS.md; `correctness/` + `ux-honesty/`
  undocumented) and the operator has reverted category normalization — a one-off audit doc must not
  become an ad-hoc category or be mistaken for a promoted lesson.

## Implementation Units

> **Gate:** Only **Unit 1** is committed scope today. Units 2–4 are **gated on Unit 1's N** and its
> candidate set — do not begin them until the audit lands. Their file paths and test scenarios are
> intentionally un-enumerable now because their inputs (N, the candidate list) do not yet exist; if
> N≈0, Units 3–4 produce zero guards and the deliverable is the audit doc + closure rationales alone.
> This keeps the single-document structure (the N-gate is the complexity-control valve) without
> treating the not-yet-specifiable downstream units as settled work.

- [ ] **Unit 1: Audit spike — enumerate & classify recurring traps from committed evidence**

**Goal:** Produce a dated candidate list and the count **N** of traps that are
*recurred ∩ mechanizable ∩ currently-unguarded*, every recurrence claim backed by a committed
citation (SHA/PR/solution entry).

**Requirements:** R1, R2, R3

**Dependencies:** None

**Files:**
- Create: dated audit doc **outside `docs/solutions/`** (e.g. `docs/audits/`; see Deferred), no
  category frontmatter
- Read-only: `docs/solutions/**`, git history, `tests/test_*` (to mark already-guarded traps)

**Approach:**
- Walk `docs/solutions/` (58 entries) as the auditable trap inventory; use memory `feedback_*`
  only to *point at* candidates, then verify each against git/solutions before recording.
- For each candidate classify into exactly one bucket: `guarded` (link the existing test) /
  `candidate` (recurred ∩ mechanizable ∩ unguarded) / `not-mechanizable` (full-pytest-only
  tripwire or pure judgment) / `backlog` (single-incident or low-cost or guard-deferred).
- Apply R3's recurrence test as an **explicit adjudication**, not a grep match: a trap qualifies
  as `recurred` only if the auditor can cite **two distinct commits/PRs where the *same root
  cause* was independently fixed** (one fix + N context references does not count). Prospective
  "this would prevent X" language in a solution entry is a single-incident lesson, **not**
  recurrence. Record the two citations per `recurred` row.
- Emit **N** = count of `candidate` rows.

**Execution note:** This is analysis, not behavioral code — but it is **manual root-cause tracing,
not a spike**. The recurrence verdict requires reading each lesson and tracing git, so budget Unit 1
as the bulk of this plan's effort (~58 entries). A throwaway `git log`/grep only *surfaces*
candidates; it cannot decide "same root cause re-broke." No committed scanner.

**Test scenarios:** Test expectation: none — produces an analysis artifact, no behavioral change.

**Verification:**
- A committed, dated audit doc exists; every `candidate`/`guarded` row cites a committed artifact;
  N is stated; the four brainstorm exemplars appear with their classifications.

- [ ] **Unit 2: Artifact-shape decision gate (consume N)**

**Goal:** Decide the durable artifact from Unit 1's N before any guard or register work is committed.

**Requirements:** R4

**Dependencies:** Unit 1

**Files:**
- Modify: the Unit 1 audit doc (append the decision + rationale)

**Approach:**
- If N is small (≈ single digits): **inline** each trap's evidence/why-prior-code-allowed-it into
  its guard docstring (default). No standalone register.
- Only if N is large enough that a standalone index beats per-guard docstrings: adopt a committed
  register, and if it copies prose, route identifier hygiene through the existing `private-tokens.txt`
  grep gate (verify the tokens file is populated, non-vacuous) rather than hand-stripping.
- Record the decision and its N-based rationale in the audit doc.

**Test scenarios:** Test expectation: none — a recorded decision, no behavioral change.

**Verification:**
- The audit doc states the chosen artifact shape and why, derived from the actual N.

- [ ] **Unit 3: Build first-batch guards (one honest guard per `candidate`)**

**Goal:** For each `candidate` from Unit 1, add a CI-run guard that turns red on a faithful repro of
the original bug and green once the fix is present.

**Requirements:** R5, R6

**Dependencies:** Unit 1 (the candidate set), Unit 2 (evidence-placement decision)

**Files:**
- Create: `tests/test_<trap-slug>_guard.py` per candidate (exact set = Unit 1 output)
- Modify (if extending coverage of an existing partial guard): the relevant existing `tests/test_*.py`

**Approach:**
- Mirror `tests/test_cli_python_m_entrypoints.py`: a module-level docstring carrying the recurrence
  evidence (committed citation) + the "why prior code allowed it" narrative, then the assertion(s).
- Prefer the lightest honest shape: AST/static scan for structural invariants (import forms,
  required kwargs, registry/map drift), contract test for cross-module agreements, fixture-isolation
  test for state-bleed classes.
- A guard that can only cover *part* of a trap's class leaves the uncovered part `candidate`/
  `backlog` — do not let a partial guard masquerade as complete (R6).

**Execution note:** Test-first by nature, with an explicit **red-on-repro protocol** (so "red on
repro" is a procedure, not an assertion): (1) make a *local-only* diff that reintroduces the bug
(restore the missing prune / drop the required kwarg / re-add the poisoning `del`); (2) run the new
guard and observe it fail; (3) capture the failing assertion text into the guard's docstring or the
commit body as evidence; (4) `git restore` the repro and confirm the guard now passes against `main`.
A guard that is green before the fix proves nothing. **Decidability rule:** if a trap's repro cannot
be expressed as a discardable local edit (e.g. state-bleed that only manifests under a specific
cross-test ordering), it is **by definition `not-mechanizable`** as a dedicated guard — the full
`pytest` run is its only honest tripwire. This converts the thin mechanizable/not-mechanizable
boundary into a test rather than a judgment.

**Patterns to follow:**
- `tests/test_cli_python_m_entrypoints.py` (docstring-carried evidence + AST/subprocess assertion)
- `tests/test_no_monolith_regrowth.py` / `tests/test_adapter_dofollow_gate.py` (invariant-as-gate)

**Test scenarios (per guard):**
- Happy path: against current `main` (bug fixed), the guard passes (green).
- Error path / regression: against a faithful repro of the original bug (e.g. reintroduce the
  missing prune / drop the required kwarg / restore the poisoning `del`), the guard fails (red) and
  names the offender in its assertion message.
- Edge case (where the trap has a class, e.g. registry drift): the guard covers every member of the
  enumerated class it claims to guard, not just the one historical instance — or explicitly scopes
  its claim to what it covers.

**Verification:**
- Each new guard demonstrably red-on-repro → green-on-main; runs under `pytest tests/`; assertion
  message names the offender. No guard lives outside `tests/`.

- [ ] **Unit 4: Snapshot closure — rationales, honesty & CI verification**

**Goal:** Close the frozen snapshot so every Unit 1 candidate is either `guarded` or carries a
one-line `not-mechanizable`/`backlog` rationale, and verify no new orphan/inert guard was introduced.

**Requirements:** R5, R6, R7, R8

**Dependencies:** Unit 3

**Files:**
- Modify: the Unit 1 audit doc (final status per row, frozen at a named date/SHA)

**Approach:**
- For every candidate not guarded in Unit 3, record a one-line rationale (`not-mechanizable`:
  full-pytest-is-the-tripwire / pure judgment; or `backlog`: deferred with reason). Demotion of
  procedural traps is expected, not a rare exception (R5).
- State explicitly that traps recurring *after* this snapshot flow to the `AGENTS.md` going-forward
  path (R7/R8), not back into this round.
- Confirm the going-forward owner is a link, not new work: cite `AGENTS.md` §"Lessons capture".

**Test scenarios:**
- Happy path: full `pytest tests/` green with the new guards included (pytest auto-collection is the
  enforcement surface — a guard under `tests/` runs in CI by virtue of being collected).
- Integration: each new guard has its red-on-repro evidence recorded (Unit 3 protocol), proving it
  is not inert.

**Verification:**
- No snapshot candidate left unaddressed; full suite green; each guard carries red-on-repro evidence;
  the audit doc reads as a frozen, dated record with committed citations only.

> Note: `test_no_orphaned_guard_scripts.py` is **not** the closure gate for this plan — it only scans
> `scripts/check_*.py` (currently zero), so it passes vacuously and does not see `tests/`-based guards.
> It is relevant only if Unit 2 chose a register coupled to a `scripts/check_*.py`; otherwise pytest
> collection is what makes a `tests/` guard non-orphan.

## System-Wide Impact

- **Interaction graph:** New files are `tests/test_*` only (plus one audit doc). No production code
  path, CLI, WebUI route, or adapter changes. Blast radius is the test suite + docs.
- **CI surface:** New guards run via the existing `pytest tests/` step (Python 3.11 + 3.12). No new
  CI workflow, no change to the `py_compile`/`ast.parse` style step.
- **Unchanged invariants:** No change to `monolith_budget.toml` ceilings, adapter registry behavior,
  config round-trip, or any documented contract. Guards only *observe* invariants; they don't alter them.
- **Integration coverage:** Each guard's red-on-repro step is the proof it actually catches the bug —
  the cross-layer assurance that unit-level green alone wouldn't give.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| N is ~0 after the audit (everything recurred is already guarded or not-mechanizable) | That is a *valid, honest outcome* — the deliverable becomes the audit doc + closure rationales. Unit 3 is sized by N, not assumed. The audit still compounds. |
| A guard passes green even against the bug's repro (false guard — the `f927fd0` failure mode) | R6 + Unit 3 execution note: red-on-repro is mandatory and is the unit's verification. A guard that can't be made red is recorded `not-mechanizable`. |
| Pressure to ship partial guards to "clear the class" (R5/R6 tension flagged in review) | Mechanizability is an explicit coverage judgment; partial coverage leaves the remainder `candidate`/`backlog`. Demotion is expected for procedural traps, not penalized. |
| Audit doc becomes another inert artifact next to the 58 solutions | Default artifact is guard docstrings (co-located, can't drift); the audit doc is a one-time frozen spike record, not a living index requiring maintenance. |
| Identifier leak if a register copies memory/plan prose | Default avoids prose copy (SHA/PR citations only); if a register is chosen, route through the existing `private-tokens.txt` grep gate, verified non-vacuous. |

## Documentation / Operational Notes

- No README/runbook changes. The audit doc is self-contained.
- If Unit 2 chooses a register coupled to an enforcing test, that test must itself satisfy R6
  (red-on-drift), and `AGENTS.md` §"CI surfaces" already documents the anti-orphan rule it must obey.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-27-recurring-trap-eradication-requirements.md](docs/brainstorms/2026-05-27-recurring-trap-eradication-requirements.md)
- Related code: `tests/test_cli_python_m_entrypoints.py`, `tests/test_no_orphaned_guard_scripts.py`,
  `tests/test_no_monolith_regrowth.py`
- Related convention: `AGENTS.md` §"Lessons capture (dual-track)" (PR #265), §"CI surfaces"
- Related prior art: `docs/brainstorms/2026-05-26-guardrail-honesty-requirements.md`,
  `docs/brainstorms/2026-05-27-bugfix-discipline-requirements.md`
