---
date: 2026-05-27
topic: recurring-trap-eradication
---

# Recurring-Trap Eradication — Audit First, Then Guard

## Problem Frame

This repo's *capture* half is mature: 58 committed `docs/solutions/` entries plus ~91
project-memory `feedback_*` files record nearly every trap an agent has hit. But capture
is not eradication. Many records are "remember to do X" human conventions
(`channel removal must prune _AUTH_TYPE_BY_PLATFORM`, `grep all 7 legacy-import forms`,
`mock.patch paths after extraction`, `del os.environ poisons later tests`) and some have
bitten more than once. Documented ≠ prevented — nothing mechanically stops them recurring.

The eradication move is to convert the *genuinely recurring, mechanizable, currently-unguarded*
traps into permanent guards that CI actually runs. The repo already has ~18 guard-style
tests (`test_no_monolith_regrowth`, `test_global_state_pollution`, `test_r9_extension_readiness`,
`test_cli_python_m_entrypoints`, `test_no_orphaned_guard_scripts`, contract/gate/isolation
tests) proving the pattern works.

**Two corpus realities, surfaced by review, reshape the approach:**

1. **The recurrence signal is mostly uncommitted.** Of ~91 memory `feedback_*` files, 72
   cite ≤1 PR; only ~6 cite ≥3. And memory files are operator-private and **never
   committed** (AGENTS.md). A register or guard committed in-repo cannot *auditably* cite a
   memory filename. The committed, auditable evidence base is `docs/solutions/` (58 entries)
   **plus git history** — not the memory files.
2. **We don't yet know how many traps actually qualify.** Because of #1, the size of the
   *recurred ∩ mechanizable ∩ unguarded* set is unknown. It could be single digits.

So the work is **audit-first**: run a one-time audit over committed evidence to produce the
count and candidate list, **then** decide the durable artifact (a standalone register only
if the count justifies it) and build the guards. We do not pre-commit to register ceremony
before the count is known.

A hard constraint is inherited from the `guardrail-honesty` brainstorm (and the `f927fd0`
orphan-guard incident): **a guard that claims to protect but never runs or never catches is
worse than none** — it gives parallel AI agents false safety. Every guard here must be proven.

## Requirements

### Audit (one-time spike — do this first)

- R1. Run a one-time audit over **committed evidence only**: `docs/solutions/` (58 entries)
  and git history (revert / re-fix cycles, PR sequences). Memory `feedback_*` files are a
  **discovery aid** — they may point at a trap, but every recurrence claim in any committed
  output must resolve to a committed artifact (a git SHA / PR number / `docs/solutions/`
  entry), never a memory filename.
- R2. For each candidate trap record: name, committed recurrence evidence (SHA/PR/solution
  entry), mechanizability verdict with a coverage note (R5), and current guard status (link
  the existing test if already guarded). Produce **N** = the count of traps that are
  *recurred ∩ mechanizable ∩ currently-unguarded*.
- R3. Define **recurrence from git, not memory**: the same root cause fixed and then
  re-broken / reverted / re-fixed across ≥2 distinct commits or PRs. A single prospective
  "remember to do X" lesson with one fix is **not** recurrence — at most it is backlog.

### Artifact decision (gated on N)

- R4. Decide the durable artifact **after** the audit, from N. Default: if the qualifying
  set is small (≈ single digits), **skip a standalone register** and inline each trap's
  evidence/rationale into its guard's docstring — co-located with the executable artifact,
  can't drift, and carries no operator-identifier leak surface. Adopt a separate committed
  register only if N is large enough that a standalone index earns its keep over guard
  docstrings. This dissolves two review risks at once: a passive register risks becoming
  another inert doc next to the 58 solutions that already failed to prevent re-bites, and a
  hand-stripped register over ~91 files is a one-way identifier-leak hazard.

### First-batch guardrails

- R5. Build a guard for each qualifying trap (the R2 set). **Mechanizability is a coverage
  judgment, not a binary:** a guard must turn red on a faithful repro of the *specific
  committed incident*; record its coverage fraction. Procedural traps whose only honest
  tripwire is the full `pytest tests/` run itself — e.g. legacy-import-form and
  mock.patch-after-extraction lessons — are recorded **not-mechanizable** rather than forced
  into a partial guard. For this corpus, that demotion is *expected*, not a rare exception.
- R6. **Guardrail honesty (load-bearing).** Each new guard must be demonstrably proven: it
  fails (red) against the original bug or a faithful repro, passes (green) after the fix, and
  is wired so `pytest tests/` in CI actually executes it. No inert, orphaned, or
  partial-coverage guard that presents as complete. New guards land as tests under `tests/`
  (the `pytest tests/` CI surface), **not** as `scripts/check_*.py` wired only to the
  workspace-root Makefile — `tests/test_no_orphaned_guard_scripts.py` already enforces this
  anti-orphan clause and flags any guard not on a CI surface. Reuse it; do not reinvent.
- R7. Completion is pinned to the **frozen audit snapshot** (the R2 candidate list at a named
  date/SHA): each candidate ends as either `guarded` (a landed honest guard) or a documented
  decision (`not-mechanizable` or `backlog`, with a one-line rationale). A trap that recurs
  *after* the snapshot flows to the going-forward path (R8), **not** back into this round —
  this keeps a bounded backfill from becoming an unbounded standing commitment.

### Going-forward

- R8. Keep the going-forward catalog **passive**, not a new enforcement gate; the
  `bugfix-discipline` AGENTS.md convention owns fix-time discipline — link rather than
  duplicate. ⚠️ `bugfix-discipline` is not yet landed (same-day brainstorm + plan); if it
  does not land first, R8 needs a stated fallback or the two must be sequenced.

## Success Criteria

- The audit (R1–R3) produces a dated, committed candidate list with **N** stated, every
  recurrence claim resolving to a committed artifact (SHA/PR/solution entry).
- For every candidate in the frozen snapshot: a landed honest guard, or a one-line
  `not-mechanizable` / `backlog` rationale — none left unaddressed.
- Every new guard carries red→green evidence and runs under `pytest tests/` in CI; zero new
  orphaned/inert guards (verified by `test_no_orphaned_guard_scripts.py`).
- The artifact-shape decision (R4) is made from the actual N, not assumed up front.

## Scope Boundaries

- No guards for hypothetical / never-occurred problems (YAGNI — committed recurrence is the bar).
- Memory `feedback_*` files are not an auditable citation source; not committed, not relied on.
- Not salvaging the orphan guards already retired by `guardrail-honesty`.
- Not forcing partial guards onto procedural / judgment traps — those are `not-mechanizable`.
- Not building a corpus-scanning tool that outlives this one-time audit; a throwaway grep is fine.
- Not introducing a new fix-time enforcement convention; `bugfix-discipline` owns that.
- Not changing the CI style step (`py_compile` / `ast.parse`) policy; guards are pytest tests.

## Key Decisions

- **Audit-first; artifact shape gated on N.** Don't pre-commit to a register before the real
  count is known. Rationale: review showed the qualifying set may be single-digit, in which
  case a register is ceremony and guard docstrings carry the evidence better.
- **Committed evidence only** (`docs/solutions/` + git); memory is a discovery aid. Rationale:
  a committed artifact can't auditably cite an uncommitted, per-operator memory file.
- **Mechanizable is a coverage judgment, not a binary;** procedural traps demote to
  `not-mechanizable` by design, not as a rare escape hatch.
- **Honesty constraint inherited** from `guardrail-honesty`: a fake guard is worse than none.

## Dependencies / Assumptions

- The committed, auditable evidence base is `docs/solutions/` (58 entries) + git history.
  Memory `feedback_*` files (~91) are operator-private and never committed (AGENTS.md) — a
  discovery aid only.
- `bugfix-discipline` (R8's going-forward owner) is a same-day (2026-05-27) brainstorm + plan,
  not yet a landed contract.
- Existing infra to reuse: `test_no_orphaned_guard_scripts.py` (anti-orphan enforcer), the
  `private-tokens.txt` grep-gate + `/ce:compound` promotion path (identifier hygiene), and the
  ~18 guard-test patterns (AST-scan / contract / fixture-isolation).

## Outstanding Questions

### Deferred to Planning
- [Affects R1][Technical] How to run the audit over git history — which queries surface
  revert/re-fix cycles cheaply (a throwaway grep/log script, not a committed tool).
- [Affects R2][Needs research] For each candidate, whether an honest guard can red on a
  faithful repro, and the guard shape (AST-scan like `test_cli_python_m_entrypoints`,
  contract test, or fixture-isolation test).
- [Affects R4][Decision-after-audit] The N threshold above which a standalone register beats
  inlining evidence into guard docstrings — decide once N is known, not before.
- [Affects R6][Technical] Which existing guard tests to extend vs. add a new file.

## Next Steps
→ `/ce:plan` — plan the audit spike first (R1–R3), with the artifact-shape decision (R4) as
an explicit gate that consumes the audit's N before any register or guard work is committed.
