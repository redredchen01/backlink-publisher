---
title: "Negative-shape assertions can enshrine the bug they appear to defend against"
date: 2026-05-15
category: test-failures
module: backlink-publisher / testing-discipline
problem_type: test_failure
component: testing_framework
symptoms:
  - "A test passes green for weeks while the documented bug it claims to test is live in production"
  - "Test docstring confidently describes what code 'must not' do, when an architectural decision elsewhere documents that exact behavior as a bug to fix"
  - "Fixing the underlying code path immediately turns the test red — the test was load-bearing for the bug, not for any real contract"
  - "The same anti-pattern recurred 2× in one week (PR #12 save_config + PR #13 RECON log level)"
root_cause: logic_error
resolution_type: test_fix
severity: high
related_components:
  - testing_framework
  - documentation
tags:
  - negative-assertion
  - test-locks-in-bug
  - assertion-inversion
  - regression-defense
  - recurring-pattern
applies_when:
  - "About to fix a P0 silent-drop or data-loss bug; first audit the test suite for negative-shape assertions in the same module"
  - "Reviewing new tests that use `assert ... not in`, `assert stderr == ''`, `assert len(...) == 0`, or test names like `does_not_*`/`must_not_*`/`should_not_*`/`is_dropped`/`is_ignored`"
  - "Adding a new always-on signal (e.g. recon-level log) — search for tests that assert the signal channel is empty"
---

# Negative-shape assertions can enshrine the bug they appear to defend against

## Problem

A test that uses negative-shape assertions (`assert X not in result`, `assert stderr == ""`, `assert len(...) == 0`, or test names like `test_does_not_round_trip`) is **structurally indistinguishable** from "the code is incorrectly failing to produce X". The test passes green either way — by the correct contract (X really shouldn't be there) or by the bug (X should be there but isn't). The docstring is the only signal that distinguishes the two, and docstrings rationalize whatever the code does.

When the negative behavior the test "protects" is itself a bug, the test becomes load-bearing for the bug — the next person trying to fix the underlying code finds their fix turns the test red, instinctively backs off, and the bug stays.

## Symptoms

- A test docstring uses softening or rationalizing language: "must not emit", "is read-only", "we deliberately drop", "intentionally absent".
- Multiple `assert "..." not in rewritten` lines as a test's only assertions, with no positive complement.
- A planning artifact or feedback memory describes the very behavior the test claims is correct, but as a bug to fix.
- Adding any new always-on signal (e.g. a new always-emit log level) trips otherwise-green stderr-empty assertions across multiple test files.

## What Didn't Work

- **Trusting test docstrings.** "Critical contract: new fields are read-only" looks authoritative until someone asks why the contract should exist. Often, no architectural reason exists — only the absence of a way to safely produce X. Docstrings are a rationalization layer, not a verification layer.
- **Reading negative-shape assertions in isolation.** "Asserts X is not in Y" is mechanically what the test does, but says nothing about whether X *should* be there. Without a positive complement asserting what the code *should* produce, the assertion is inscrutable.
- **Treating a turning-red test as a regression on the day of a fix.** First instinct on the failing test is "the test caught a regression — back off the fix". Inverting that read of the failure is the recovery step: the test caught the right code path; only its polarity was wrong.

## Solution

**On the day of the fix, invert the assertion. Do not delete the test.** The test caught the right code path; its semantic meaning is corrected by flipping the polarity from "asserts the bug" to "asserts the absence of the bug". Same fixture, same exercise of the code path — the test's value (catching changes in the relevant code path) is preserved.

Three changes, in order:
1. Rename the function and docstring to describe the *correct* contract.
2. Flip every `not in` to `in` (and analogous flips for `stderr == ""` → `"<expected-line>" in stderr`, etc.).
3. Add a positive complement: assert the system *does* produce the right thing, not just that it doesn't produce the wrong thing. For round-trip-style tests, also assert semantic equivalence (`load(save(cfg)) == cfg`), not just byte-level survival.

For the concrete pre/post diff of the canonical incident (the `save_config` data-loss bug), see `inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` — that entry is the **specific incident** record; this entry is the **general pattern** that recurred since then.

## Why This Works

Inverting the assertion preserves the test's structural value (an exercise that touches the relevant code path) while correcting its semantic meaning (now asserts the right invariant). Adding a positive complement closes the "tautological gate" failure mode — if the assertion mechanism stops producing the negative result for a different reason (e.g. the function silently returns early), the negative `not in` still passes, but the positive `in` fails.

The same logic applies to other negative shapes: `assert stderr == ""` becomes `assert "<expected-line>" in stderr` once the system has any always-on signal that should appear. `assert len(warnings) == 0` becomes `assert any(w.code == "<expected-warning>" for w in warnings)` once the system has a reachable warning path.

## Prevention

**Audit grep on every P0 fix in a documented bug class** — run before writing the fix:

```bash
rg -n 'assert\s+.+\s+not\s+in\b' tests/
rg -n 'assert\s+\w*(stderr|stdout|errors?)\s*==\s*""' tests/
rg -n 'assert\s+len\(.+\)\s*==\s*0' tests/
rg -n 'def test_.*(does_not|must_not|should_not|is_read_only|is_dropped|is_ignored)' tests/
```

For each hit, ask: **if the behavior this test is "protecting against" were actually the correct behavior, would this test go red?** If yes, this test is an inversion candidate the day that behavior gets fixed — flag it now, do not silently delete it later.

**Defensive-over-explanation smell**: a test docstring that explains *why* the negative behavior is correct (vs simply describing it) is a smell. Real contracts are stated, not politely rationalized.

**Pair every negative-shape assertion with a positive complement when writing new tests**. If you find yourself writing `assert X not in result`, ask whether there's also a positive thing the code *should* be doing that you can assert in the same test. The complement protects against the gate going tautological.

**Property-based tests are the structural defense**: when the failure mode is "gate returns True (or False) for every input", example-based tests cannot catch it. Use `hypothesis` `@given` with structural invariants — every iteration that returns the wrong answer becomes a counterexample, not a green test.

**For every new always-on signal** (e.g. a new RECON log level, a new mandatory warning), grep tests for `stderr == ""`, `len(...) == 0`, and similar empty-channel assertions. They were green only because the signal didn't exist; the signal's introduction makes them all P0 invert candidates. Plan to flip them in the same commit that introduces the signal.

## Why "general pattern" — recurrence in one week

The pattern recurred twice within one week:

1. **PR #12 (save_config)**: `test_save_config_does_not_round_trip_v2_fields` enshrined the data-loss bug — see specific-incident entry `inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` for the full diff.
2. **PR #13 (RECON log level)**: After landing the always-on `PipelineLogger.recon()` channel, three tests (`test_validate_no_stderr_on_success`, `test_plan_no_stderr_on_success`, `test_plan_three_rows`) all asserting `stderr == ""` turned red. They had been green only because INFO-level logs were filtered out at default `--log-level=WARN`. Same negative-assertion-locks-in-bug pattern; same one-line invert fix per test.

Recurrence within one week is the signal that elevates this from "specific incident" to "general pattern that needs codified prevention". The grep audit recipe above is the prevention; running it as part of every P0 fix or any always-on-signal addition is the discipline.

## Related Issues

- `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` — the **specific incident** entry; this entry generalizes from it.
- `docs/solutions/logic-errors/save-config-write-paths-bypass-preservation-2026-05-15.md` — the underlying bug pattern that the inverted test was protecting (sibling family entry from this same migration).
- `docs/solutions/best-practices/recon-log-level-for-always-on-signals-2026-05-15.md` — the second recurrence's trigger; adding a new always-on signal forces the audit.
- Provenance: `feedback_test-locks-in-bug.md` (auto memory [claude], first encountered 2026-05-13; updated 2026-05-14 with second-recurrence note).
