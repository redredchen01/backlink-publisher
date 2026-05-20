---
title: "'Scan / full debug' requests get a 30-second pytest first — don't design an N-unit plan on imagined failures"
date: 2026-05-18
category: docs/solutions/best-practices
module: /ce:plan + sweep-task workflow
problem_type: best_practice
component: testing_framework
severity: medium
applies_when:
  - "User requests 'sweep', 'scan', 'full debug', or 'audit the whole module'"
  - "Tempted to enumerate failure modes from code reading alone"
  - "About to design a multi-unit plan for a remediation that hasn't been measured"
related_components:
  - development_workflow
  - tooling
tags:
  - pytest-first
  - sweep-tasks
  - empirical-grounding
  - plan-scope
  - real-failures
  - false-effort
---

# Sweep tasks: run pytest before designing the plan

## Context

Requests phrased as "scan everything", "full debug", "audit X module", "fix what's broken" invite the agent to enumerate failure modes from code reading. The agent ends up designing a plan around **hypothetical** failures: "test_a might be brittle because of fixture X", "test_b probably depends on operator state", and so on. The plan can grow to 6+ units before any failure has been observed.

The empirical truth is almost always smaller. Running pytest for 30 seconds shows the actual failing tests. Often the answer is "1 test is failing, takes 12 seconds to fix, no plan needed."

On 2026-05-18, an agent received a "scan and debug" request and designed a 6-unit plan targeting 53 files based on code-reading hypotheses. Running pytest first would have revealed exactly 1 failing test that took 12 seconds to fix. The 6-unit plan was scrapped.

## Guidance

### First action on a sweep request: run pytest

```bash
cd backlink-publisher/
pytest tests/ --tb=short 2>&1 | tee /tmp/sweep-pytest-$$.log
```

30 seconds. The output is the ground truth: actual failing tests, with stack traces, that the agent now has empirical data about.

If pytest passes entirely: the "scan" request has no concrete remediation target. Tell the user. Don't design a plan for hypothetical issues.

If pytest fails: the failing tests are the plan scope. Anything else the agent might "scan for" is hypothetical and lower-priority by definition.

### Frame the plan around real failures

```markdown
## Plan: Sweep remediation (post-pytest-2026-05-18)

Pytest results: 1 failure (test_foo_bar_edge_case), 0 errors, 0 warnings new this run.

Unit 1: Fix test_foo_bar_edge_case
  - Failing assertion: assert config["mode"] == "strict"
  - Root cause: operator-config fixture leaks state from previous test
  - Fix: add fixture cleanup in conftest.py
  - Est: 12 seconds

[no Unit 2 — pytest is otherwise green]
```

The plan is honest about scope: 1 unit, 12 seconds. The user can confirm-or-redirect ("actually, I also want to check X"), and the agent has the empirical baseline to compare any expanded scope against.

### Adjacent: ad-hoc requests with measurable scope

The pytest-first principle generalizes: any "audit / scan / sweep" request has a cheap empirical check that bounds the work.

| Request | Cheap empirical check |
|---------|------------------------|
| "Scan tests" | `pytest tests/` |
| "Audit linting" | `ruff check src/` or `flake8 src/` |
| "Check type errors" | `mypy src/` |
| "Find dead code" | `vulture src/` or `git log --diff-filter=D` |
| "Check security" | `bandit -r src/` or `safety check` |
| "Audit dependencies" | `pip-audit` or `pip list --outdated` |
| "Check WebUI bugs" | `curl http://localhost:8888/<route>` smoke (with throwaway config — see [[never-smoke-test-real-save-endpoints]]) |

Each is 30s-2min. Run it first.

### Frame the plan after measurement, not before

The plan-doc should cite the empirical result it's responding to:

```markdown
## Pre-plan measurement

```bash
$ pytest tests/ --tb=short
============== 1 failed, 312 passed in 12.4s ==============
FAILED tests/test_config.py::test_save_round_trip - AssertionError
```

## Plan units (driven by the failure above)

- Unit 1: fix test_config.py::test_save_round_trip
```

The measurement is part of the plan-doc. Future readers see the empirical baseline and don't second-guess scope.

## Why This Matters

Plans designed on hypotheticals are:

- **Wrong-sized**: usually too big (the imagined failures cover more ground than reality).
- **Wrong-shaped**: the units target plausible-but-not-actually-broken areas, while the real broken area gets a small unit or none.
- **Slow to course-correct**: once the plan is locked, the agent invests in deepening / document-review / planning passes before discovering the empirical mismatch.

The cost of the 30-second pytest is rounding-error compared to the cost of a 6-unit plan thrown out.

This is a workflow-level instance of the empirical-grounding principle: every other "verify before locking" lesson in `docs/solutions/` ([[grep-alleged-drift-sites-before-locking-framing]], [[validate-main-before-planning-off-feat-branch]], [[brainstorm-review-defers-to-plan-grounding]]) is the same pattern applied at different inputs. Sweep tasks are the most common trigger.

## When to Apply

- User requests with "scan", "sweep", "audit", "full debug", "check everything".
- Tasks the agent is about to estimate as multi-unit based on code reading.
- "Refactor X module" requests — measure the tech debt before designing the refactor.
- Returning to a long-running task — re-measure; reality may have moved.

Skip when:

- The user gives a specific failing test, error message, or reproducer. The empirical work is already done.
- The cheap check is unavailable (no test suite, no linter, no measurable target).
- The request is explicitly about hypothetical / forward-looking design ("what if we wanted to support X?") — there's nothing to measure yet.

## Examples

**Right (2026-05-18 sweep, hypothetical correct version):**

```
User:        "Sweep the test suite, scan for issues, debug what's broken"
Agent:       runs pytest first → 1 failure, 312 passed in 12s
             reads the 1 failing test's traceback → fixes in conftest.py
             reports: "Fixed 1 brittle fixture; pytest now green."
Total time:  ~3 minutes
```

**Wrong (2026-05-18 actual):**

```
User:        "Sweep the test suite, scan for issues, debug what's broken"
Agent:       reads 53 test files
             designs 6-unit plan: fixture isolation, mock cleanup, env var
                                   leakage, parallelism races, ...
             user approves plan
             agent begins implementation
             realizes nothing in pytest output supports any of these claims
             runs pytest belatedly → 1 actual failure, the only real issue
Total time:  ~45 minutes for what should have been 3
```

## Related

- `docs/solutions/best-practices/grep-alleged-drift-sites-before-locking-framing-2026-05-19.md` — sibling: verify "X is missing" claims per-site.
- `docs/solutions/workflow-issues/validate-main-before-planning-off-feat-branch-2026-05-19.md` — sibling: verify file existence on origin before planning.
- `docs/solutions/best-practices/brainstorm-review-defers-to-plan-grounding-2026-05-19.md` — sibling: brainstorm critiques lose to plan-time grep.
- `docs/solutions/best-practices/never-smoke-test-real-save-endpoints-2026-05-19.md` — when running cheap empirical checks (incl. curl), isolate config first.
- AGENTS.md → "Testing" — current pytest setup.
