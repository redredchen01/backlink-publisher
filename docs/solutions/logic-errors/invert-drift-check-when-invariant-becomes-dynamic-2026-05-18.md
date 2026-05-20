---
title: "Module-level drift assertions break when the invariant becomes registry-delegating — invert direction or demote to test-time"
date: 2026-05-18
category: docs/solutions/logic-errors
module: publishing/registry + adapter drift checks
problem_type: logic_error
component: tooling
symptoms:
  - "ImportError or partial-module state at startup after refactoring a static constant into a registry-delegating function"
  - "Test fails with `RuntimeError: ... half-loaded module` from a module-level assertion"
  - "Circular import surfaces only after the refactor, despite no new direct imports"
root_cause: scope_issue
resolution_type: code_fix
severity: medium
related_components:
  - testing_framework
tags:
  - drift-check
  - module-level-assertion
  - registry-delegation
  - circular-import
  - lazy-import
  - r9-extension
---

# Invert drift check when invariant becomes dynamic

## Problem

Module-level drift assertions of the form "constant A equals derived-from-B" worked when both A and B were static literals. After refactoring B into a registry-delegating function (e.g., `B() returns registered_consumers()`), the assertion at module-import time fires **during a half-loaded import chain**: the registry module is still loading, `B()` returns a partial or empty set, and the assertion either crashes or — worse — passes incorrectly.

The R9 extension pattern in `backlink-publisher` (`publishing.registry.registered_platforms()`) hit this. A static `CHANNELS = {"medium", "blogger", "telegraph"}` assertion at module-import time worked. The same assertion against `registered_platforms()` broke because `registered_platforms()` requires the registry module to have finished its top-level `register("x", XAdapter)` calls — which hasn't happened yet at the moment a transitively-imported module evaluates the assertion.

## Symptoms

- `ImportError` or `AttributeError` at startup, originating from a module-level assertion in a file that didn't directly import the registry before the refactor.
- Test failures of the form `assert {} == {"medium", "blogger", ...}` — the empty set is from a half-loaded registry.
- The assertion **passes** under `python -c "import backlink_publisher"` (full import resolved) but **fails** under pytest's import collection (modules import in a different order).
- New circular-import warnings even though the explicit imports look unchanged.

## What Didn't Work

- **Reorder imports in the affected module**. The half-load issue is structural; reordering one module's imports just moves which module sees the empty registry, not whether anything does.
- **Add a guard `if not registered_platforms(): return` at the assertion site**. Skips the check silently and defeats the entire purpose of the drift assertion.
- **Eager-load the registry from a top-level `__init__.py`**. Creates the circular-import cycle this refactor was designed to avoid.
- **Convert the assertion to a warning**. Drops the drift detection that the original assertion provided value for.

## Solution

Two options, pick based on what the assertion needs to catch:

### Option 1 — Invert direction: assert from the dynamic side, not the static side

Original (broken after refactor):

```python
# Some module that uses CHANNELS at import time
from .publishing.registry import registered_platforms

# Module-level assertion (broken — fires before registry fully loads)
CHANNELS = {"medium", "blogger", "telegraph"}
assert CHANNELS == registered_platforms(), "channel drift"
```

Inverted:

```python
# publishing/registry.py — at the END of the file, after all register() calls
_EXPECTED_INVARIANT = {"medium", "blogger", "telegraph"}

def _verify_registry_invariant() -> None:
    """Run after all register() calls land. Safe to call from registry module's
    own bottom-of-file, because by then the registry is fully populated."""
    actual = set(_REGISTRY.keys())
    drift = actual ^ _EXPECTED_INVARIANT
    if drift:
        raise RuntimeError(f"registry drift: {drift}")

_verify_registry_invariant()  # called at registry's bottom-of-file
```

The assertion now fires from the dynamic side after the dynamic side is fully populated, instead of from the static side during import-chain race.

### Option 2 — Demote to test-time

If the assertion is documentation-of-invariant rather than runtime safety, move it to a test:

```python
# tests/test_registry_invariant.py
def test_registered_platforms_matches_expected_set():
    from backlink_publisher.publishing.registry import registered_platforms
    assert registered_platforms() == {"medium", "blogger", "telegraph", "velog",
                                       "ghpages", "hashnode", "writeas", ...}
```

Test-time evaluation guarantees the registry is fully loaded (pytest imports the module to completion before running the test body). The assertion runs once in CI and once locally — same coverage as module-level for the drift question, no import-order fragility.

### Pick Option 1 vs Option 2

| Need | Pick |
|------|------|
| Catch drift at production startup, not just CI | Option 1 |
| Drift is configuration-shaped (operator could break it) | Option 1 |
| Drift is developer-visible only (only changes via PR) | Option 2 |
| Want to keep the registry module free of import-time side effects | Option 2 |

For `backlink-publisher`'s R9 extension, the R9 extension recipe is the only path that changes the set, so Option 2 (test-time) is sufficient. The drift assertion lives in `tests/test_r9_extension_readiness.py`.

## Why This Works

Python's import semantics evaluate module-level code top-to-bottom during the first `import` of the module. If module A's top-level code references something that requires module B to be fully imported, and B hasn't been imported yet (or is mid-import, transitively reaching A), B's state is partial. Assertions against B from A see this partial state.

The fix is structural: either run the assertion from inside B (after B is done loading — Option 1) or run it from a context that explicitly imports both to completion before evaluating (Option 2).

## Prevention

- **Lint rule** for module-level assertions against function calls:

```bash
# Find module-level assertions that call functions (likely to evaluate during import)
grep -rnE '^[[:space:]]*assert.*\([a-z_]+\(' src/
```

Each hit needs review: is the called function safe at import time? If it touches a registry or any state populated by other modules, the assertion is fragile.

- **Reviewer checklist** for R9-style registry refactors: every module-level constant being moved to a function should also surface every existing reference. Treat the refactor as a search-and-replace audit, not just a definition change.
- **Test for the failure mode**: add a test that imports modules in alphabetical order (or pytest's default collection order) and confirms no module-level assertions fire. Catches the bug before it ships.

## Related Issues

- R9 extension pattern (`publishing/registry.py`) — the dynamic-set refactor that this lesson came from (PR landing 2026-05-18 family).
- `tests/test_r9_extension_readiness.py` — current test-time drift check (Option 2).
- `docs/solutions/best-practices/grep-alleged-drift-sites-before-locking-framing-2026-05-19.md` — adjacent: drift detection in plans, not at runtime.
- Python docs on import mechanics — formal semantics of partial-module visibility.
