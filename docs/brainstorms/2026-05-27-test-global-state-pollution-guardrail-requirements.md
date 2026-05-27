---
date: 2026-05-27
topic: test-global-state-pollution-guardrail
---

# Test Global-State Pollution: Root-Cause Fix, Class Elimination, Guardrail

## Problem Frame

The test suite has a recurring **order-dependent pollution** class: a test mutates
global or module-level mutable state and never restores it, so later tests inherit
the polluted state. Two failure shapes have already bitten us:

- **Silent false-green (worst case).** **20** test files set
  `webui.app.config["WTF_CSRF_ENABLED"] = False` or `CSRF_ENABLED = False` — **both
  flags are honored** by the guard (`webui_app/__init__.py`: `CSRF_ENABLED` is the
  canonical key, `WTF_CSRF_ENABLED` the legacy alias). Some target the shared
  module-level `webui.app` singleton (`webui.py:24`, `app = create_app()`); others
  mutate a **private `create_app()` instance** built inside the test's own fixture
  (e.g. `test_manifest_webui_wiring.py`, `test_webui_image_gen.py`) — the latter is
  *not* shared-state pollution and needs no restore. A few deliberately toggle the
  flag on/off mid-test (`test_webui_route_contract.py`, `test_webui_three_url.py`) and
  must not be misclassified as offenders. Because order is deterministic (no
  pytest-randomly; `PYTHONHASHSEED=0`), an alphabetically-earlier test that mutates the
  *singleton* and never restores it *stably* disables the global CSRF guard, so medium's
  CSRF tests passed every run while the guard was actually dead code (surfaced + fixed
  in PR #261).
- **Hard breakage.** `del os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"]` in a test's
  `finally` removed the var entirely instead of restoring the session-fixture's
  isolated value, so later tests fell back to the operator's real `~/.config` —
  "single test green, full suite red" (PR #259). Sibling: `webui_store/__init__.py`
  freezes `_CONFIG_DIR` at import and ignores the env var.

The common root cause: **tests reach for raw global mutation (`app.config[...]=`,
`os.environ[...]=`, `del os.environ`, module singletons) instead of a save/restore
mechanism, and nothing structurally forces restoration or detects the leak.**

This matters because false-greens erode trust in the suite (we ship believing we have
coverage we don't) and order-pollution makes failures non-reproducible in isolation,
which is expensive to bisect.

## Approach (selected)

A layered defense, decided during brainstorm. A runtime "tripwire" was considered and
**dropped** (see Scope Boundaries) — it overlapped the net's surface, creating an
unsolvable teardown-ordering paradox, and its registry dimension would false-positive on
one-time lazy adapter import.

| Layer | Mechanism | What it buys |
|---|---|---|
| **Containment net** | autouse function-scope fixture: restore an explicit set of security-relevant config/env keys to a clean baseline after each test | Stops *singleton* config/env leaking between tests with ~zero per-test churn. Note: it contains leakage, it does not by itself recover the lost CSRF coverage (see R9 / R11) |
| **Static AST gate** | `test_no_monolith_regrowth`-style test that bans raw `os.environ[...]=` / `del os.environ` / `*.config[...]=` for `_SECURITY_TOGGLE_KEYS` outside sanctioned fixtures, with a grandfather allowlist | Stops the *pattern* from being reintroduced; allowlist is ratcheted to only shrink |
| **Positive canary** | isolation-independent test asserting a token-less request is rejected under default config | Proves the guard is *live*, catching dead-guard regressions no leak-detector can see |

## Requirements

**Containment Net**
- R1. Provide an autouse, function-scope fixture that restores `webui.app.config` after each test so config mutations on the shared singleton cannot leak between tests. **The restore baseline must be a clean source of truth — a fresh `create_app()` config captured under a pinned, fixture-controlled environment at session start, not a read of the already-imported (possibly-mutated) live singleton** — and the captured baseline must be asserted to have CSRF *enabled*, failing loudly if it is not. `create_app()` is **not** fully deterministic (it sets `secret_key` via `uuid4()` and reads env at construction), so the net must restore an **explicit allowlist of security-relevant keys** (`CSRF_ENABLED`, `WTF_CSRF_ENABLED`, and any others added to `_SECURITY_TOGGLE_KEYS` per R5) rather than diffing/restoring the whole config dict; non-deterministic keys like `secret_key` are intentionally excluded.
- R2. The fixture must also restore the process environment to its pre-test state (re-add deleted keys, revert changed keys, drop added keys), covering vars beyond the two the session fixture already guards (e.g. `OAUTHLIB_INSECURE_TRANSPORT`).
- R3. The net must apply only where relevant without forcing imports on unrelated (e.g. pure-CLI) tests — config snapshotting engages when `webui.app` is in play; env snapshotting is universal.

**Guardrails**
- R4. _(Dropped — a runtime global-state tripwire was considered and removed; see Scope Boundaries. ID retired to keep R5–R11 stable.)_
- R5. A static AST gate test scans `tests/` and fails when a test file uses raw `os.environ[...] =`, `del os.environ[...]`, or `*.config[...] =` for any key in a single shared named constant `_SECURITY_TOGGLE_KEYS` — at minimum `CSRF_ENABLED`, `WTF_CSRF_ENABLED`, `BACKLINK_PUBLISHER_ALLOW_NETWORK`, `OAUTHLIB_INSECURE_TRANSPORT` — outside a sanctioned fixture. The same constant feeds the net (R1) and the gate so the two layers cannot drift. It carries a grandfather allowlist of the current offenders.
- R6. The grandfather allowlist is a closed, enumerated set seeded from the verified live offender list (20 files) and may only shrink. The gate fails if a file not on the allowlist introduces a banned pattern. A canary count constant (mirroring `SLOC_CANARY_EXPECTED`) fails CI if the allowlist *grows*, making the shrink-only intent enforced rather than aspirational; the allowlist is documented as debt to retire.
- R7. Provide a sanctioned, discoverable fixture (e.g. `disable_csrf`) so contributors have an obvious correct path instead of raw mutation; reference it from `tests/AGENTS.md`.
- R11. Add an **isolation-independent positive canary test**: build a fresh `create_app()` (CSRF default on), issue a token-less state-mutating request (POST/PUT/PATCH/DELETE) to a representative protected route, and assert it is rejected (403). This proves the guard is *live under default config* — a failure mode the net and AST gate (both leak-detectors) cannot catch. The canary must not depend on the net and must live outside the grandfather allowlist.

**Migration and Compatibility**
- R8. The verified **20** offender files are **not** force-migrated; they are grandfathered and (for the singleton-mutating subset) rendered leak-harmless by the net. Planning must classify each as singleton-mutating (matters), private-`create_app()`-instance (already harmless), or deliberate-toggle (must stay allowlisted, never auto-migrated). Opportunistic migration to the sanctioned fixture is allowed but not required.
- R9. **Distinguish two failure shapes.** (a) Tests passing on *inherited* leaked state (another test left the singleton's CSRF off): the net flips these red, and they must be fixed — not suppressed — in the same change. (b) Tests whose *own body* disables CSRF so their own assertions pass: the net does **not** flip these red (it only restores *after* the test), so it does not by itself recover their lost coverage. For shape (b), "fixed" for a security-relevant test means adding a **positive enforcement assertion** (token-less state-mutating request returns 403 under default config), **not** merely routing it through the sanctioned `disable_csrf` fixture. A reviewer/CI check must flag any R9 "fix" whose only change is adding `disable_csrf` to a CSRF test. **Sizing:** before planning commits, run a draft net to enumerate the actual flip-to-red set. If small, fix in the same PR; if large, land the net + gate + canary first (delivers the containment goal) and fix the surfaced false-greens in a separate, sized follow-up PR. The net PR must not be held unbounded behind cleanup.
- R10. Preserve existing invariants: `PYTHONHASHSEED=0`, the session-scope `_isolate_user_dirs` fixture, and the four existing autouse isolation fixtures must keep working; new fixtures compose with them rather than replace them.

## Success Criteria
- Re-introducing the known leak bugs (singleton CSRF-disable-no-restore, `del os.environ`) is caught automatically — the net renders cross-test leakage harmless, and the AST gate fails CI on a new raw-mutation of a security toggle.
- The CSRF guard is proven live under default config by the R11 canary, independent of any test-isolation fixture.
- The full suite passes with per-test config/env isolation in force; inherited-leak false-greens (R9 shape a) are fixed, and any security tests touched are fixed with positive enforcement assertions, not by silencing.
- A contributor adding a webui route test has a documented one-line sanctioned way to disable CSRF, and no reason to touch `webui.app.config` directly.
- The grandfather allowlist is enumerated from the verified 20-file set, guarded by a count canary, and only ever shrinks.

## Scope Boundaries
- Not converting webui tests to per-test `create_app()` instances (the heavier alternative was considered and rejected in favor of the net).
- Not fixing `webui_store/__init__.py` `_CONFIG_DIR` frozen-at-import in this work — related, but a distinct production-code change tracked separately (`[[feedback_webui_store_config_dir_frozen]]`).
- Not adding pytest-randomly in this work (would change the footprint-gate determinism contract); may be revisited as separate defense-in-depth.
- Not a general test-quality sweep (mock.patch path drift, `available()=False` skips) — that was the broader "false-green" scope explicitly not chosen.
- **No runtime global-state tripwire.** Considered and dropped: to "render harmless" the grandfathered offenders the net must restore *before* a per-test teardown check could read them, so a tripwire on the same surface is dead code; flipped to run first, it would fail every grandfathered offender by name (defeating R8). Its only non-overlapping dimension (registry) has never leaked and would false-positive on lazy adapter import. The AST gate (pre-run) + canary (enforcement) + net (containment) cover the goal without it.

## Key Decisions
- **Net over migration**: a blanket snapshot/restore net was chosen over migrating each test to fresh app instances — same safety, far less churn, and it protects future tests automatically.
- **Net + AST gate + canary, no tripwire**: the runtime tripwire was dropped (see Scope Boundaries) as either dead or net-defeating; the static AST gate blocks the pattern pre-run and has direct repo precedent, while the canary covers the one failure the leak-detectors cannot.
- **Grandfather, don't migrate**: consistent with the low-churn intent, the 20 existing offenders are allowlisted and (for the singleton subset) neutralized by the net rather than rewritten.
- **Net contains leakage; the canary proves enforcement**: the net stops cross-test leaks but does not recover coverage for tests that disable CSRF in their own body — that is what R11's positive canary and R9-shape-(b) assertions are for.

## Dependencies / Assumptions
- `create_app()` is **not** fully deterministic (`uuid4()` `secret_key`, env-derived `SESSION_COOKIE_SECURE`/`ALLOW_NETWORK`); the net therefore restores an explicit security-key allowlist, not the whole config (R1).
- The net's new fixtures must reconcile env ownership with the existing config-dir isolation fixtures (`_isolate_user_dirs` and, in concurrent WIP, a per-test config re-assert fixture) — `BACKLINK_PUBLISHER_CONFIG_DIR`/`_CACHE_DIR` are owned by those and must be excluded from the net's restore set or the net must run strictly inside them.
- Mechanically aligned with the repo's existing AST/gate test precedent (`test_no_monolith_regrowth.py`, `test_r9_extension_readiness.py`) and the existing `fake_platform_registered` registry save/restore fixture.

## Outstanding Questions

### Resolve Before Planning
- _(none — scope, mechanism, and guardrail strategy are decided)_

### Deferred to Planning
- [Affects R5][Technical] AST-gate detection precision: exact call-shapes to flag, scoping to `tests/` only (production `webui_app/__init__.py` writes `app.config[...]` legitimately), and fixture-sanctioning via marker vs hardcoded fixture-name allowlist. `[Needs research]`
- [Affects R8][Technical] Classify each of the 20 offenders into singleton-mutating / private-`create_app()`-instance / deliberate-toggle, to seed the grandfather allowlist correctly. `[Needs research]`
- [Affects R10][Technical] Pin and test the autouse fixture ordering between the net, the chosen guardrail, and the existing config-dir fixtures (`PYTHONHASHSEED=0` does not pin teardown order); add a meta-test that deliberately leaks and asserts the contract. `[Needs research]`
- [Affects R2][Technical] Confirm the net's env-restore excludes `BACKLINK_PUBLISHER_CONFIG_DIR`/`_CACHE_DIR` (owned by existing fixtures) and handles intra-test asserts like `OAUTHLIB_INSECURE_TRANSPORT` "1"→"0" without breaking them.
- [Affects R9][Needs research] Run a draft net to enumerate the actual flip-to-red set; the count decides whether false-green fixes ride the net PR or split into a sized follow-up.

## Next Steps
→ `/ce:plan` for structured implementation planning
