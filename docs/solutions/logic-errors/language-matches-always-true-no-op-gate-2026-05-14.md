---
title: "language_matches() always returned True — silent no-op validate-time gate"
date: 2026-05-14
category: docs/solutions/logic-errors
module: backlink-publisher / validate-backlinks
problem_type: logic_error
component: service_object
symptoms:
  - "validate-backlinks accepted every payload regardless of body/anchor language vs row.language"
  - "validation.warnings[] was always empty in the output JSONL even when body language clearly differed from row.language"
  - "Test test_validate_backlinks.py:189 (isinstance(warnings, list)) passed forever — never asserted the warnings were populated"
  - "Language-mismatched articles published silently for an unknown duration before R1 surfaced the bug"
root_cause: logic_error
resolution_type: code_fix
severity: medium
tags:
  - validation
  - language-check
  - silent-failure
  - test-locks-in-bug
  - characterization-first
---

# `language_matches()` always returned True — silent no-op validate-time gate

## Problem

`backlink_publisher.language_check.language_matches(detected, requested)` was the gate function `validate-backlinks._enhance_payload` used to decide whether to warn about a row whose detected content language did not match the declared `row.language`. **Every branch of the function fell through to `return True`** — including the "Cross-check" branch that explicitly described handling a known-different detection (commented "Allow some flexibility"). The validate-time language warning was therefore unreachable; language-mismatched articles published silently. The bug was discovered during a brainstorm code-read for an unrelated feature (the publish-time linkcheck gate, plan 2026-05-14-001), not via any test failure or operator report.

## Symptoms

- `validate-backlinks` produced no warnings for a zh-CN row whose `content_markdown` was entirely English, or any other mismatched (detected, requested) pair.
- Direct invocation confirmed the no-op: `language_matches("en", "zh-CN") → True`, `language_matches("zh-CN", "ru") → True`, every combination True.
- The output JSONL's `validation.warnings` array was always `[]`.
- The only test referencing the field — `tests/test_validate_backlinks.py:189` — asserted `isinstance(output["validation"]["warnings"], list)` (shape only); it never asserted that a known mismatch fixture produced a non-empty warnings array. The test passed for the entire life of the bug.

## What Didn't Work

- **Trusting the test suite.** 999 tests passed. None caught it because no test ever asserted the negative-shape contract ("known mismatch ⇒ warnings non-empty").
- **Reading the function in isolation.** On a casual glance the four explicit `if requested == "X" and detected == "X": return True` branches *look* like a real matcher. The bug lives in lines 67-71, the fall-through branch that's "supposed" to handle the differing-language case but doesn't.

## Solution

Rewrite `language_matches` to a tight, intention-revealing contract; introduce a `SUPPORTED_LANGUAGES` constant exposed at module top so downstream callers (validate-backlinks' enum guard, anchor_lang) can share the same definition without re-deriving the set.

**Before** (`src/backlink_publisher/language_check.py:57-71`):

```python
def language_matches(detected: str, requested: str) -> bool:
    """Check if detected language roughly matches the requested language."""
    if detected == "unknown":
        return True  # Can't disprove, allow through
    if requested == "zh-CN" and detected == "zh-CN":
        return True
    if requested == "en" and detected == "en":
        return True
    if requested == "ru" and detected == "ru":
        return True
    # Cross-check: if we detected something clearly different, fail
    if detected != requested:
        # Allow some flexibility — short texts may misdetect
        return True
    return True
```

**After**:

```python
SUPPORTED_LANGUAGES = frozenset({"zh-CN", "ru", "en"})


def language_matches(detected: str, requested: str) -> bool:
    """Check if the detected language matches the requested language.

    - "unknown" on either side is the escape valve — returns True (the
      caller can't disprove a mismatch when one side is undetermined).
    - Two known, equal languages match.
    - Two known, different languages do NOT match — return False so the
      validate-time gate can fail the row.

    Languages outside SUPPORTED_LANGUAGES are coerced to unknown semantics:
    the gate cannot speak for them, so they pass.
    """
    if detected == "unknown" or requested == "unknown":
        return True
    if detected not in SUPPORTED_LANGUAGES or requested not in SUPPORTED_LANGUAGES:
        return True
    return detected == requested
```

Verified via 21 new tests in `tests/test_language_check.py` covering self-match, all six cross-pairs, both unknown sides, and out-of-enum coercion.

## Why This Works

The original branches returned `True` four times correctly (the equal-known-language self-match cases). The bug lived in the fifth conditional — the one whose comment promised to enforce the cross-check — which silently returned `True` and was followed by yet another `return True` fall-through. The fixed function expresses the same intent in a single expression (`detected == requested`) guarded by the unknown escape valve, so there's no longer any unreachable branch hiding a wrong answer. The `SUPPORTED_LANGUAGES` constant is consumed by `validate-backlinks` and `anchor_lang` so any future language addition lands in one place.

## Prevention

1. **Pin behavior contracts, not just data shapes.** The existing test asserted `isinstance(warnings, list)` — a shape test. Shape tests are necessary but not sufficient. For any warning/error collection that's conditionally populated, also assert: (a) a known-trigger fixture produces a non-empty collection, and (b) a known-non-trigger fixture leaves it empty. This is the exact anti-pattern documented in the project's auto memory `feedback_test-locks-in-bug.md` (auto memory [claude]): "negative-shape assertions can fix data-loss bugs in place." Apply that lens to anything resembling `assert isinstance(x, list)` or `assert "..." not in result`.

2. **Characterization-first when contracts feel fishy.** The Unit 1 implementation deliberately ran a one-liner against the buggy code FIRST to confirm `language_matches("en", "zh-CN")` returned True, BEFORE writing the new test suite. This proves the bug exists and that the fix is the intended difference — not a coincidental refactor that happens to flip the result. The plan's Execution Note (`docs/plans/2026-05-14-001-feat-mandatory-linkcheck-lang-gate-plan.md` Unit 1) recorded this discipline for the next reader.

3. **Read past the explicit branches to the fall-through.** When a function ends with `if X: return True\n    return True`, both branches are the same value — the conditional is decorative. Run a quick grep for `return True\n.*return True` and similar patterns in any module owning a gate function.

4. **Expose enum constants from the source module.** `SUPPORTED_LANGUAGES` lives in `language_check.py`; `anchor_lang.py` imports it; `validate_backlinks.py` uses it for the R3 enum guard. A single source of truth prevents drift when the language list expands.

5. **Test the "Allow some flexibility" comment.** Any code comment that uses softening language — "allow flexibility", "be lenient", "for now", "TODO sharpen" — is a contract that needs an assertion. Either the flexibility is intentional (write a test asserting the flexible behavior) or it's accidental (the test will fail, exposing the bug).

## Related Issues

- `docs/plans/2026-05-14-001-feat-mandatory-linkcheck-lang-gate-plan.md` — Unit 1 of the larger plan that surfaced this bug while wiring R2/R4/R5 gates. R1 was an explicit prerequisite to R2 because R2 had nothing to fail against until language_matches actually rejected.
- `docs/brainstorms/2026-05-14-mandatory-linkcheck-lang-gate-requirements.md` — bug surfaced during the brainstorm code-read for an unrelated feature.
- Auto memory `feedback_test-locks-in-bug.md` (auto memory [claude]) — the project's prior encounter with this exact pattern in a different module. Cross-reference this anti-pattern in any new test review.
- `docs/solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md` — tonally adjacent ("tests pass while bug persists" family of failures via mock-every-branch lesson; not the same bug class but useful sibling reading).
