---
title: "Floating-point noise breaks tie-break ordering in deficit-driven selectors"
date: 2026-05-15
category: logic-errors
module: backlink-publisher / anchor_scheduler
problem_type: logic_error
component: anchor_selection
symptoms:
  - "Configured tie-break order (e.g. Branded > Partial > LSI > Exact) never triggers; one anchor type wins consistently when it should be a tie"
  - "Anchor type distribution drifts away from configured target proportions in deterministic but unintuitive ways"
  - "An explicit tie-break unit test (e.g. `test_partial_wins_against_lsi_when_tied`) fails on a setup where the two deficits are mathematically equal"
root_cause: logic_error
resolution_type: code_fix
severity: medium
related_components:
  - anchor_metrics
  - anchor_scheduler
tags:
  - floating-point
  - ieee-754
  - tie-break
  - anchor-distribution
  - scheduler
  - rounding
applies_when:
  - "Implementing a 'pick max by (target − actual) deficit' selector with a configured tie-break ordering"
  - "Comparing two floats produced by different arithmetic paths that are mathematically equal"
  - "Any code that orders by `max()` or `sorted(key=...)` over float deltas and expects ties to be resolved by a secondary key"
---

# Floating-point noise breaks tie-break ordering in deficit-driven selectors

## Problem

A scheduler that picks the anchor type with the largest `(target_proportion - actual_proportion)` deficit, with ties broken by a configured ordering, will see ties **never trigger** in practice when the two deficits are produced by different arithmetic paths but are mathematically equal. IEEE-754 floats can express `0.25 - 0.20 = 0.04999999999999999` and `0.10 - 0.05 = 0.05` (exact) as **different** numbers — the selector picks the bit-level larger deficit deterministically, and the configured tie-break never gets a chance.

## Symptoms

- The deficit-driven anchor selector consistently picks one type over another in setups where they should tie. Distribution metrics drift away from configured target proportions in a stable but unconfigured way.
- A unit test that constructs an explicit tied scenario (e.g. `test_partial_wins_against_lsi_when_tied`) fails on the bit-level winner instead of the configured-tie-break winner.
- A 400-iteration convergence simulation against the configured proportions over-represents the bit-level winner type.

## What Didn't Work

- **Trusting that mathematically equal deficits are float-equal.** `0.25 - 0.20` and `0.10 - 0.05` are both `0.05` in algebra and both `0.04999999999999999` and `0.05` in IEEE-754. The exact form wins every comparison; the tie-break never sees a tie.
- **Comparing with `math.isclose()` at the comparison site.** `isclose` reduces an inequality to "approximately equal", but `max()` and `sorted()` do not respect approximate equality — they need a deterministic total order. Sprinkling `isclose` into the selector doesn't help unless the entire ordering algorithm is rewritten around it.

## Solution

Round the deficit to a precision that is **far below any meaningful proportion granularity, but far above floating-point noise**, before comparing.

```python
deficits[t] = round(target_proportions.get(t, 0.0) - actual, 6)
```

Six decimal places: target proportions in this project are quantized to 0.55, 0.25, 0.10, 0.05 (two decimal places). Deficits live in the same space. Floating-point noise from subtraction is at the 1e-16 level. Rounding to 1e-6 collapses mathematically equal values to byte-equal floats; the configured tie-break gets a chance to fire.

## Why This Works

The selector's contract is "pick by deficit, break ties by configured order". Tie-break only gets to vote when two deficits are **byte-equal** in the comparison the `max()` / `sorted()` operator runs. Raw subtraction yields byte-equal results only when the two operand pairs are in the same IEEE-754 representation neighborhood — which is rare for any realistic mix of fractional arithmetic.

Rounding to a coarser precision than any input granularity collapses mathematically-equal deficits to byte-equal floats. The selector keeps its float-deficit semantics for non-ties; ties now actually trigger.

## Prevention

1. **For any selector that orders by float deltas with a configured tie-break, round before comparing.** Default precision: 1e-6 unless the input granularity demands tighter. Wider rounding is rarely a problem; tighter rounding silently loses tie-break.
2. **Test the tie-break path explicitly.** A test like `test_partial_wins_against_lsi_when_tied` constructs deficits that are mathematically equal but produced by different arithmetic paths. The test passes only when the round-then-compare discipline is in place.
3. **Add a property test for tie-break stability.** With `hypothesis @given` over arbitrary deficit configurations that include at least one tie pair, assert the configured tie-break order always wins. Raw-float comparison fails; round-then-compare passes.
4. **Watch for related patterns** in any code doing `max()`, `sorted()`, or `min()` over expressions like `(a - b)` or `(target - actual)` where `target` and `actual` come from different code paths. Rounding at the moment of comparison is the structural defense.

## Related Issues

- `docs/solutions/best-practices/recon-log-level-for-always-on-signals-2026-05-15.md` — the always-on log channel that catches anchor-distribution drift (silent-drop tripwire).
- Provenance: `feedback_floating-point-tiebreak.md` (auto memory [claude], first encountered 2026-05-13).
