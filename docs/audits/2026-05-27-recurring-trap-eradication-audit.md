# Recurring-Trap Eradication Audit — 2026-05-27 (frozen)

> One-time audit per `docs/plans/2026-05-27-007-feat-recurring-trap-eradication-plan.md`.
> **Frozen at** `origin/main` `7a7f216` (worktree base), corpus = 57 committed `docs/solutions/`
> entries + git history + `tests/` cross-reference. Memory `feedback_*` files were a discovery aid
> only; every verdict below resolves to a committed artifact (test file / PR / solution entry).
> Not a living index — traps recurring after this snapshot flow to the going-forward path
> (`AGENTS.md` §"Lessons capture (dual-track)"), not back into this audit.

## Result

**N = 0** — the *recurred ∩ mechanizable ∩ currently-unguarded* class is **empty**.

Every trap in the corpus that is both genuinely recurring (R3: same root cause re-broken/re-fixed
across ≥2 distinct commits/PRs) **and** cheaply mechanizable is **already guarded** by an existing
CI-run test. The genuinely-recurring traps that are *not* yet guarded are all `not-mechanizable`
(their only honest tripwire is human doc-review or the full `pytest` run, not a dedicated guard —
a dedicated guard would be partial/dishonest, which R6 forbids).

Per the plan's Risk table, **N = 0 is a valid, honest outcome**: the deliverable is this frozen
audit record + the closure rationales below. No first-batch guards are built (Unit 3 = ∅), and no
standalone register is created (Unit 2 decision: N=0 ⇒ neither register nor new docstrings needed).

## Method

- Enumerated 57 `docs/solutions/` entries; extracted `problem_type` + distinct `#PR` references as
  a recurrence *pointer* (not a verdict).
- For each multi-PR / code-invariant candidate, read the entry and adjudicated R3 recurrence by
  hand (two independent fixes of the *same root cause*, not one fix + N context refs).
- Cross-referenced `tests/` to determine current guard status; verified the two load-bearing
  "guarded" verdicts by reading the guard's assertions (see citations).
- PR-count is a weak proxy: 72 of the memory corpus cite ≤1 PR; in `docs/solutions/` the high-PR
  entries are dominated by **workflow/judgment** lessons (planning discipline, worktree hygiene),
  which are `not-mechanizable` by nature.

## Adjudicated traps

### Recurred ∩ mechanizable → already `guarded` (the class this effort targets — already closed)

| Trap (solution entry) | Recurrence evidence | Guarded by |
|---|---|---|
| `del os.environ[CONFIG_DIR]` poisons session-scoped fixture | #43 → #253/#257/#259 (same config-dir/global-state poisoning root cause, re-broken) | `tests/test_conftest_state_net.py` (meta-test proving the autouse `_restore_global_state_net` net contains config-singleton + security-env leaks; Plan 2026-05-27-003, landed @ `7a7f216` "#277") + `test_net_safety.py` |
| Tests silently coupled to operator's local config state | #40/#43/#44/#259 (operator config bleeding into tests, repeated) | Four autouse `conftest` isolation fixtures (config-dir sandbox, URL pass, content-fetch pass, sockets blocked) + `test_conftest_state_net.py` |
| Nofollow adapter shipped without dofollow gate (revert saga) | #102/#103/#107/#108/#109 (shipped→reverted→re-shipped) | `test_adapter_dofollow_gate.py` + `test_registry_dofollow_kwargs.py` |
| `python -m <module>` silent no-output after package split | #75 (decompose→empty `__main__`, twice same session 2026-05-20) | `test_cli_python_m_entrypoints.py` |
| Channel removal must prune `_AUTH_TYPE_BY_PLATFORM` (+ invert-drift) | #253 + `invert-drift-check` lesson | `test_auth_type_classification.py` (asserts `_AUTH_TYPE_BY_PLATFORM` ⟷ `active_platforms()` coverage + extras drift, at test-time per the invert-drift learning) |
| Publish-history helper invariant | #87/#97 | `test_webui_history_invariant.py` |

These rows are the proof that the eradication target was *already met* by the repo's existing
guard-test discipline — exactly why N=0.

### Recurred ∩ `not-mechanizable` (honest tripwire is human judgment / full pytest — no guard)

| Trap | Recurrence | Why not-mechanizable |
|---|---|---|
| Negative-shape assertion enshrines the bug it appears to defend | two entries, same root cause (#12 → #14, generalized #12/#13) | A guard could only AST-scan for "tests whose sole assertions are negative-shape" — hundreds of legitimate negatives ⇒ a partial/false guard (R6 forbids). The honest tripwire is doc-review, already captured as a lesson. |
| Workflow/judgment cluster: grep-alleged-drift-before-framing (#74/#76/#93), probe-then-pivot (#121/#122), scan-parallel-PRs (#42/#43/#45/#77/#81), verify-repo-state-before-planning (#75/#98/#104/#119), validate-main-before-planning (#74/#83), late-plan-revisions-skip-code (#98/#106), cherry-pick-when-parent-blocks-CI (#75/#77/#81), salvage-unmerged-work (#242/#250/#254), multi-agent-turf-check (#122) | multiple PRs each | Process/judgment lessons with no checkable code invariant; the `bugfix-discipline` AGENTS.md convention + agent SOP own these, not a CI guard. |

### `backlog` (recurrence < 2 strict, or niche / low-cost / already mitigated in place)

| Trap | Why backlog (not first-batch) |
|---|---|
| PyYAML int-coerces all-digit SHA in fixtures (#98/#104) | One root cause surfaced once as a flake, not re-broken ≥2×; already mitigated by quoting at the single generation site. A dedicated scan is niche; revisit if it recurs. |
| `argparse choices=` exit-2 vs repo `UsageError` exit-1 (0 PR refs) | Single prospective lesson, **not** recurred; exit-code contract is otherwise covered by `test_exit_code_contract.py` / `test_cli_exit_code_literals.py`. |

## Closure (Unit 4)

- **First-batch (Unit 3): ∅** — N=0, no guards built.
- **Artifact decision (Unit 2):** N=0 ⇒ neither a standalone register nor new guard docstrings are
  warranted; this frozen audit doc is the whole durable artifact.
- **Going-forward:** any trap recurring after this snapshot is handled by `AGENTS.md` §"Lessons
  capture (dual-track)" (regression/recurring ⇒ failing test + why-prior-code-allowed-it +
  `/ce:compound`) — a link, not new work in this effort.
- **Compounding value:** the audit confirms the repo's guard-test discipline has *already* eradicated
  the mechanizable recurrence class; future audits can start from this frozen baseline.
