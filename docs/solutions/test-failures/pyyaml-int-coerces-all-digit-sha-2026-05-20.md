---
title: "PyYAML int-coerces all-digit SHAs in test fixtures — quote SHA interpolations"
date: 2026-05-20
category: docs/solutions/test-failures
module: tests + scripts/plan-claims-gate.yml
problem_type: test_failure
component: testing_framework
symptoms:
  - "Flaky test failure on Python 3.11 CI: schema validation rejects a parsed SHA"
  - "Local rerun on the same commit passes — flake reappears intermittently on PR refreshes"
  - "Error like `schema validation: '<sha>' must match pattern '^[0-9a-f]{7,}$' (got: int)`"
root_cause: wrong_api
resolution_type: test_fix
severity: medium
tags:
  - pyyaml
  - sha
  - yaml-quoting
  - plan-claims-gate
  - schema-validation
  - python-3.11
  - test-fixture
---

# PyYAML int-coerces all-digit SHAs in test fixtures

## Problem

Bare YAML scalars composed entirely of digits get parsed as `int`, not `str`. Roughly **5% of 7-character hex SHAs are all-digit** (any SHA with no characters in `a-f`). Test fixtures that interpolate SHAs into unquoted YAML pass for non-all-digit SHAs and silently break for all-digit ones — a flake that scales with how many test runs you do.

PR #98's `plan-claims-gate` 3.11 CI surfaced this when the gate parsed an all-digit SHA from a generated YAML fixture and the downstream schema (`pattern: '^[0-9a-f]{7,}$'`) rejected the `int` value.

## Symptoms

- `pytest` on Python 3.11 fails intermittently with a schema-validation error against a YAML-parsed SHA.
- Running the same test in a loop locally: ~5% failure rate (matches the all-digit-SHA frequency).
- Error message names a SHA value but reports it as `int` or fails a `^[0-9a-f]+$` regex.
- Re-running on a different commit (different random SHA) appears to "fix" the test.

## What Didn't Work

- **Pinning PyYAML version**. The behavior is documented YAML 1.1 spec compliance, not a library quirk; no version of PyYAML "fixes" it.
- **Loosening the schema regex to allow ints**. Treats the symptom, hides the bug, and breaks the schema's invariant that SHAs are strings everywhere else in the pipeline.
- **Switching `yaml.load` to `yaml.safe_load`**. Same coercion — the type rule is at the parser layer, not the loader.

## Solution

Quote every SHA interpolation in YAML fixture generation:

```python
# tests/fixtures/plan_claims_gate.py — wrong
yaml_blob = f"""
plan_id: "2026-05-19-010"
commits:
    - {sha[:7]}
"""

# tests/fixtures/plan_claims_gate.py — right
yaml_blob = f"""
plan_id: "2026-05-19-010"
commits:
    - '{sha[:7]}'
"""
```

The single quotes force YAML to treat the value as a string regardless of content. Double quotes work too, but single quotes are safer inside f-strings (no shell-escape concerns) and idiomatic for "plain string, no interpolation."

Schema rejection is **correct** behavior — the fixture is wrong. Don't loosen the schema.

## Why This Works

YAML 1.1 (which PyYAML defaults to) auto-detects scalar types from the value's lexical shape:

| YAML source | Parsed Python type |
|-------------|---------------------|
| `1234567` | `int` (1234567) |
| `1a2b3c4` | `str` ("1a2b3c4") |
| `'1234567'` | `str` ("1234567") |
| `"1234567"` | `str` ("1234567") |

Hex SHAs are 16-symbol alphabet (`0-9a-f`). The probability that a given 7-char prefix is all-digit is `(10/16)^7 ≈ 5%`. That's high enough to bite often, low enough that the failure looks like a flake instead of a real bug, and uniformly distributed across PR commits.

Quoting is the canonical fix because it puts the type contract in the producing layer (fixture generator), not the consuming layer (schema validator).

## Prevention

- **Lint rule for fixture generators**: any `f"..."` or `format()` call inside a YAML-producing helper must wrap SHA interpolations in `'...'`. Easy `grep -nE '^\s*-\s+\{[a-z_]+sha[^}]*\}' tests/fixtures/` catches the unquoted pattern.
- **Property-based test for the fixture**: feed the generator a known all-digit SHA (e.g., `"1234567"` or any commit hash from the project history that happens to be all-digit) and assert it round-trips as `str`. One regression test is enough.
- **Default to quoting in any new YAML fixture**: there is no upside to leaving SHAs unquoted. Quote everything that's "supposed to be a string."

For the `plan-claims-gate` specifically, the fix landed in PR #104 (`0bdb546`) alongside the falsy-coerce P1 fix.

## Related Issues

- PR #98 (`b632bc0`) — original `plan-claims-gate` ship that surfaced the 3.11 flake.
- PR #104 (`0bdb546`) — Tier-2 review follow-up that fixed the fixture quoting plus the related falsy-coerce bug.
- `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` — adjacent pattern: tests that lock in the wrong invariant.
