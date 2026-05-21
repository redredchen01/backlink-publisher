---
title: "feat: Adapter Dofollow Gate (Capability Manifest v1)"
type: feat
status: completed
date: 2026-05-20
claims: {}
deepened: 2026-05-20
origin: docs/brainstorms/2026-05-20-adapter-dofollow-gate-requirements.md
pr: 126
---

# Adapter Dofollow Gate (Capability Manifest v1)

## Overview

Add a mechanical gate at adapter registration time so a future PR that re-introduces a nofollow platform (Dev.to / Mastodon / WordPress.com retry, or any new platform with `rel="nofollow"`) cannot reach `main` silently — turning the PR #108 → #109 (2026-05-20) 9-minute revert into a CI/import-time error. Minimal wedge: one capability field (`dofollow`) on `publishing.registry.register()`, paired with a `_REJECTED_PLATFORMS` runtime registry that catches re-attempts at previously-rejected names.

## Problem Frame

R9's extension contract (`tests/test_r9_extension_readiness.py`) validates that a new adapter is *shaped* like an adapter (one-line `register()` extension, no CLI/schema touches). It does not validate that the adapter actually produces a dofollow link — that truth lives in `webui_app/binding_status.py:31-58` (`_DOFOLLOW_BY_CHANNEL`), outside the registry. PR #108 → #109 (2026-05-20 03:29 → 03:38 UTC) made this gap concrete: three nofollow adapters landed cleanly and were reverted minutes later, with the institutional response being a memory-feedback rule (`feedback_grep_dofollow_map_before_shipping_adapter`) — tribal knowledge that decays. The fix is structural: move dofollow truth into the registry, gate it in CI, and preserve the negative knowledge from PR #108 in a runtime-enforceable rejection registry.

See origin: `docs/brainstorms/2026-05-20-adapter-dofollow-gate-requirements.md`.

## Requirements Trace

**Registry contract**
- R1. `register(name, *adapter_cls, dofollow=, rationale=)` accepts `dofollow: Literal[True, False, "uncertain"]`.
- R2. `register()` without `dofollow=` is a hard error at import time (TypeError-equivalent). No default value at gate-active state.
- R3. `dofollow=False` or `dofollow="uncertain"` requires `rationale=` with `len(rationale.strip()) >= 80`.
- R4. `dofollow=True` may pass `rationale=` informationally; not length-validated.
- R5. Pure-read accessors: `registry.dofollow_status(name) -> Literal[True, False, "uncertain"] | None` (None = unregistered) and `registry.dofollow_rationale(name) -> str | None`.

**Existing adapter backfill**
- R6. Backfill all current `register()` call sites with explicit `dofollow=` in one atomic PR. Production set → `True` except hashnode → `False` (CF + paywall = publish path dead). Test fixtures: `tests/conftest.py:213` + six lines in `tests/test_publish_backlinks_banner_integration.py`.
- R7. Delete `_DOFOLLOW_BY_CHANNEL` after R11 negative-knowledge migration + WebUI consumer migration complete.

**WebUI surface preservation**
- R8. `get_channel_status` + `_token_paste_status` read from `registry.dofollow_status(name)`. Badge contract (matches `webui_app/templates/_channel_card_macro.html:23-31` rendering): True → green "dofollow" badge (`badge-dofollow good`); False → weak "nofollow" badge (`badge-dofollow weak`); `"uncertain"` → orange "dofollow?" badge (`badge-dofollow unknown`); None → no badge rendered (template's existing fallthrough). Hashnode visual change: orange "dofollow?" (None today via `_DOFOLLOW_BY_CHANNEL.get("hashnode") is None`) → weak "nofollow" badge (False post-migration) — a one-time honest rebadging. Template gains one branch in U4 to handle the literal string `"uncertain"` (today it only branches on True / False / `is none`).

**CI gate**
- R9. `tests/test_adapter_dofollow_gate.py` asserts every `registered_platforms()` entry has `dofollow_status` set; if `False` or `"uncertain"`, `dofollow_rationale` is ≥80-char stripped.
- R10. Test enforces presence + length only — rationale content is PR-review concern.

**Negative knowledge preservation**
- R11. `_REJECTED_PLATFORMS: dict[str, str]` (name → rationale ≥80 chars) lives alongside `_REGISTRY` in `publishing/registry.py`. Initial entries: devto / mastodon / wordpresscom with rationales transcribed from `webui_app/binding_status.py:53-55` comments. Plain dict — no dataclass — because only the rationale string has a programmatic consumer; the `rejected_at` date is recoverable from `git log` and `dofollow=False` is implicit (it's a rejection map).
- R12. `register("devto", …)` raises `RegistryError` at import time if the name appears in `_REJECTED_PLATFORMS`. Un-rejection path: delete the name from `_REJECTED_PLATFORMS` in the same PR as the re-attempt's `register()` call. The PR diff makes the un-rejection visible to reviewers (literal dict edit + commit message captures "what changed since prior verdict"). No override kwarg; no parallel set-tracking mechanism.
- R13. CI gate (R9) asserts `_REGISTRY.keys() ∩ _REJECTED_PLATFORMS.keys() == ∅` — strictly disjoint, no exceptions. R12's un-rejection-by-deletion path makes this an absolute invariant.

## Scope Boundaries

- **Out of scope (v1):** `banner_upload`, `oauth_dialect`, `daily_cap`, `observed_rel_attrs`, `bindable` capability fields. Per-capability backfill pattern is acknowledged as linear cost; migration to `AdapterCapability` dataclass deferred to capability field #2.
- **Out of scope:** replacing duck-typed `embed_banner` Protocol opt-in. PR #123's lazy-config-load contract preserved.
- **Out of scope:** live web probe for dofollow auto-discovery.
- **Out of scope:** rationale content linting / NLP review.
- **Deferred to v2:** scheduled CI job sampling published URLs + alarming on declared/observed `rel` mismatch (drift defense).

## Context & Research

### Relevant Code and Patterns

- **Registry storage**: `src/backlink_publisher/publishing/registry.py:90,93-98` — `_REGISTRY: dict[str, list[type[Publisher]]]` and `register(platform, *publishers) -> None` with "last call wins" semantics. No existing kwargs.
- **Registry dispatcher**: `src/backlink_publisher/publishing/adapters/__init__.py:45-51` — the 7 production `register()` call sites.
- **Monolith-budget CI gate** (the pattern to mirror): `tests/test_no_monolith_regrowth.py:32-83,128-154,212-228` + `monolith_budget.toml` — TOML data file → pytest parametrize at collection → per-entry schema test + value test + synthetic red-path test proving the gate fires. Failure messages tell operator *exactly what to edit*.
- **R9 extension contract**: `tests/test_r9_extension_readiness.py:38-87` — all assertions are positive ("works with X"); no negative assertion forbids new kwargs. Signature extension is safe.
- **conftest registry snapshot**: `tests/conftest.py:206-221` — `fake_platform_registered` fixture targeted at single key `"fake"`. Heavier whole-registry snapshot pattern at `tests/test_publish_backlinks_banner_integration.py:105-111`.
- **WebUI consumers of `_DOFOLLOW_BY_CHANNEL`**: `webui_app/binding_status.py:74` (`get_channel_status`) and `webui_app/helpers.py:929,947` (`_token_paste_status`). Both use `.get(name)` so `None` is the unmapped-platform sentinel today.
- **Error taxonomy**: `_util/errors.py` houses domain error classes. New `RegistryError` should live there.

### Institutional Learnings

- `docs/solutions/logic-errors/invert-drift-check-when-invariant-becomes-dynamic-2026-05-18.md` — R9 implementation had a module-level `assert CHANNELS == registered_platforms()` fire during half-loaded pytest collection. **Apply preemptively**: any new module-level constants derived from `_REGISTRY` (e.g. a dofollow-set frozenset) must be lazy or be a function call, never a module-level constant computed at import.
- `docs/solutions/test-failures/tests-coupled-to-operator-config-state-2026-05-18.md` — registry mutations during tests need conftest snapshot/restore matching the new parallel dicts.
- `docs/plans/2026-05-18-006-feat-monolith-sloc-ceiling-plan.md` — same-PR rationale-bump discipline for CI gates with `≥80` char rationale.
- Memory `feedback_grep_dofollow_map_before_shipping_adapter` — preserves PR #108 retrospective; the negative-knowledge invariant the new `_REJECTED_PLATFORMS` map must respect. Promote this memory entry to `docs/solutions/best-practices/` as part of Unit 6.
- Memory `feedback_publish_history_invariant_helper` — single-helper-accessor pattern (`_push_history_per_row`) is the right model for `is_dofollow_eligible(channel)` and family.

### External References

Skipped. Codebase has the exact precedent (`monolith_budget.toml` + `test_no_monolith_regrowth.py`).

## Key Technical Decisions

- **Single-capability minimal wedge** (per origin §Key Decisions). Add `dofollow=` only; do not introduce `AdapterCapability` dataclass until field #2.
- **Strict-required at gate-active state**: `register()` without `dofollow=` is a hard error. During the U2→U5 migration window inside the PR, `dofollow` is optional with `None` sentinel — required-flip happens atomically with the CI gate test landing.
- **Parallel-dict storage** over `_REGISTRY` value type change: `_DOFOLLOW_BY_PLATFORM: dict[str, Literal[True, False, "uncertain"]]` and `_RATIONALE_BY_PLATFORM: dict[str, str]` sit alongside `_REGISTRY`. Keeps `_REGISTRY` value shape unchanged so the conftest single-key snapshot pattern survives (extended to snapshot the matching key in the parallel dicts).
- **Un-rejection by deletion, not override-kwarg** (chosen after document-review F8). Re-attempting a previously-rejected platform requires editing `_REJECTED_PLATFORMS` (delete the entry) in the same PR as the re-`register()`. Rejected via the kwarg-override alternative because: (a) override preserves stale negative knowledge (record says nofollow, register() claims otherwise — two facts disagree); (b) kwarg-override needs a parallel `_OVERRIDE_REGISTRATIONS: set[str]` tracker to make R13 testable; (c) deletion path keeps `_REGISTRY` and `_REJECTED_PLATFORMS` strictly disjoint, making R13 an unconditional invariant. Audit trail lives in the literal-dict-deletion diff plus the commit message — same surface area as the override rationale would have occupied.
- **Hashnode honest-degrade** (per origin §Key Decisions): `dofollow=False`, not `"uncertain"`. CF + paywall = publish path is dead, not just verification-blocked.
- **Map delete vs computed-view re-export**: prefer delete. Plan 003 Phase C is the only downstream consumer plan (not yet shipped) — update its plan-doc reference in a follow-up PR (see U6) instead of preserving the map as a computed view.
- **CI gate mirrors `monolith_budget` shape**: a `dofollow_gate.py` test that parametrizes over `registered_platforms()` + asserts each entry's `dofollow_status` is set + rationale ≥80 if non-True + the R13 disjoint-keys invariant. Include a synthetic red-path test proving the gate fires when violated.
- **PR merge strategy: squash-merge** (chosen after document-review F2). GitHub `pull_request` CI runs on every push to the PR head, so the U1→U6 commit sequence may individually fail CI even though the final state is green. The PR uses squash-merge — only the final HEAD state needs to pass CI; intermediate per-commit pushes MAY fail and that is acceptable. This matches the "single atomic PR" framing throughout the plan. Reviewers reading the squash-merge commit see the full delta as one atomic change.

## Open Questions

### Resolved During Planning

- **`RegistryError` class location** — Resolved: live in `_util/errors.py` next to existing domain errors (`DependencyError`, `ExternalServiceError`). Avoids new module surface.
- **`dofollow` positional vs kw-only** — Resolved: kw-only. Forces grep-friendly `dofollow=True` at every call site; matches `monolith_budget.toml` rationale-passing convention.
- **Un-rejection mechanism** — Resolved: delete-from-rejection-map in same PR as re-`register()`. Audit trail lives in the diff + commit message (same surface as kwarg rationale would have occupied). See §Key Technical Decisions.
- **Plan 003 Phase C coordination** — Resolved: Phase C hasn't shipped. Update its plan-doc reference in a same-day follow-up PR (not this one — keeps this PR's surface focused on the gate machinery; see U6).
- **conftest fixture extension** — Resolved: both fixtures need extension with different shapes — see U3 §Approach (per document-review F4 finding that the "three additional lines" framing applied only to the per-key fixture; the whole-registry fixture needs symmetric clear/update treatment).
- **U2→U5 transition state** — Resolved (flow-analyzer flagged): during the PR's intermediate commits, `register()` accepts `dofollow=None` as legal sentinel. Required-flip + CI gate land atomically in U5. Single-PR atomicity guarantees no exposed window post-merge.

### Deferred to Implementation

- **Exact `RegistryError` message wording** — final phrasing of the "previously rejected: <name>; prior rationale: ...; to retry, delete this entry from `_REJECTED_PLATFORMS` in the same PR as the new `register()` call" message will be tuned when writing U1.
- **`_DOFOLLOW_BY_PLATFORM` typing precision** — whether to use `Literal[True, False, "uncertain"]` directly or define a `DofollowStatus` type alias is a typing-ergonomics call best made when writing U2.
- **Synthetic red-path test shape for R9** — whether the gate test calls a helper to install a violation and then re-runs the assertion, or uses a parametrized fixture, mirrors the existing `tests/test_no_monolith_regrowth.py:212-228` choice — pick whichever pattern is in use at implementation time.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```text
publishing/registry.py
├── _REGISTRY: dict[str, list[type[Publisher]]]      # unchanged shape
├── _DOFOLLOW_BY_PLATFORM: dict[str, T|F|"uncertain"]  # NEW parallel dict
├── _RATIONALE_BY_PLATFORM: dict[str, str]            # NEW parallel dict
├── _REJECTED_PLATFORMS: dict[str, str]              # name → rationale ≥80 chars
│      └── seeded with devto, mastodon, wordpresscom from PR #108 comments
│
├── register(name, *adapter_cls, dofollow, rationale=None)
│      ├── if name in _REJECTED_PLATFORMS → raise RegistryError
│      │      (un-rejection path: delete entry from _REJECTED_PLATFORMS in
│      │       the same PR as the new register() call)
│      ├── if dofollow ∈ {False, "uncertain"} and len(rationale.strip()) < 80
│      │      → raise RegistryError
│      ├── store _REGISTRY[name], _DOFOLLOW_BY_PLATFORM[name], _RATIONALE_BY_PLATFORM[name]
│      └── (None default removed in U5 — gate-active state)
│
├── dofollow_status(name) -> T|F|"uncertain"|None
└── dofollow_rationale(name) -> str|None

webui_app/binding_status.py (post-U4)
├── get_channel_status reads registry.dofollow_status(name)
└── _DOFOLLOW_BY_CHANNEL deleted (U5)

webui_app/helpers.py (post-U4)
└── _token_paste_status reads registry.dofollow_status(channel)

webui_app/templates/_channel_card_macro.html (post-U4)
└── new branch for status.dofollow == "uncertain" → orange "dofollow?" badge

tests/test_adapter_dofollow_gate.py (NEW, U5)
├── for each name in registered_platforms():
│      assert dofollow_status(name) is not None
│      if dofollow_status(name) in (False, "uncertain"):
│          assert len(dofollow_rationale(name).strip()) >= 80
├── assert _REGISTRY.keys() ∩ _REJECTED_PLATFORMS.keys() == ∅
│         (strictly disjoint — un-rejection is by deletion, not override)
└── synthetic red-path: install violation, assert gate fires
```

## Implementation Units

- [ ] **Unit 1: Add `_REJECTED_PLATFORMS` infrastructure + `RegistryError`**

**Goal:** Land the negative-knowledge registry and a domain error class with no behavior change to existing `register()` callers. Establishes the storage for U2 to enforce against.

**Requirements:** R11, partial R12

**Dependencies:** None

**Files:**
- Modify: `src/backlink_publisher/publishing/registry.py` — add `_REJECTED_PLATFORMS: dict[str, str]` (name → rationale string, ≥80 chars) at module level, populated with devto / mastodon / wordpresscom entries transcribed from `webui_app/binding_status.py:53-55`. Plain dict — no `RejectionRecord` dataclass. The `rejected_at` date is recoverable from `git log` if ever needed; `dofollow=False` is implicit (rejection map); only the rationale string has a programmatic consumer.
- Modify: `src/backlink_publisher/_util/errors.py` — add `RegistryError(PipelineError)` class next to `DependencyError` / `ExternalServiceError`, with `exit_code = 5` (InternalError class — registry violations are programmer bugs, not user input errors; consistent with the existing taxonomy where InputValidation=2 / Dependency=3 / External=4 / Internal=5).
- Test: `tests/test_registry_rejected_platforms.py` (new)

**Approach:**
- `_REJECTED_PLATFORMS` is a literal module-level dict — no `reject()` API, no runtime mutation. Entries land via PR diff.
- Initial three entries: rationale strings ≥80 chars transcribed from existing inline comments (devto: "rel=nofollow ugc since ~2022 per dev.to platform policy …", mastodon: "hardcoded rel=nofollow noopener noreferrer on outbound links since federation default …", wordpresscom: "free tier nofollow; paid tier dofollow per WP.com SEO guide …"). Implementer tunes to ≥80 chars during writing.
- `RegistryError(Exception)`: subclass of base error per repo taxonomy.

**Patterns to follow:**
- Existing exception classes in `_util/errors.py` for `RegistryError` shape
- `webui_app/binding_status.py:36-55` comment style for rationale wording transcription

**Test scenarios:**
- Happy path: `_REJECTED_PLATFORMS["devto"]` is a string ≥80 chars stripped.
- Happy path: `RegistryError` is importable from `_util/errors.py` and is a `PipelineError` subclass with `exit_code == 5`.
- Edge case: every value in `_REJECTED_PLATFORMS` has `len(value.strip()) >= 80` (loop assertion, future-proof for new entries).

**Verification:**
- `_REJECTED_PLATFORMS` and `RegistryError` are importable from their target modules.
- No existing tests fail (this unit is additive, no behavior change).

---

- [ ] **Unit 2: Extend `register()` signature with optional `dofollow=` + rationale validation**

**Goal:** Add `dofollow` and `rationale` keyword arguments to `register()` with `dofollow=None` default, plus validation logic. Backward-compatible: existing callers (no kwarg) continue to work; the U5 commit will remove the default and make it required.

**Requirements:** R1 (partial — type accepted), R3, R12 (rejection check wired)

**Dependencies:** Unit 1

**Files:**
- Modify: `src/backlink_publisher/publishing/registry.py` — extend `register()` signature to `register(platform, *publishers, dofollow=None, rationale=None)`. Add validation: (a) if `platform in _REJECTED_PLATFORMS` → `RegistryError` with message "previously rejected: <name>; prior rationale: <prior>; to retry, delete this entry from `_REJECTED_PLATFORMS` in the same PR as the new register() call"; (b) if `dofollow in (False, "uncertain") and (rationale is None or len(rationale.strip()) < 80)` → `RegistryError`. Add `_DOFOLLOW_BY_PLATFORM` and `_RATIONALE_BY_PLATFORM` module-level dicts. Add `dofollow_status(name)` and `dofollow_rationale(name)` accessors. When `dofollow is None` (back-compat path during U2→U5 window), also delete any prior stale parallel-dict entry for that name to avoid stale residue on a second `register()` call.
- Test: `tests/test_registry_dofollow_kwargs.py` (new)

**Approach:**
- Kw-only via `*publishers, dofollow=None, rationale=None`. The `*publishers` already enforces kw-only on everything after. No `accept_rejection_override` kwarg — un-rejection is by deletion, see §Key Technical Decisions.
- Accessors are simple `dict.get(name)` calls returning `None` for unregistered.
- Do NOT raise on missing `dofollow=` yet — this is U5's job. U2 must leave existing callers (which pass no `dofollow`) working.

**Patterns to follow:**
- `src/backlink_publisher/publishing/registry.py:93-98` for existing `register()` body shape
- `tests/test_r9_extension_readiness.py` for positive-assertion test style

**Test scenarios:**
- Happy path: `register("foo", FooAdapter, dofollow=True)` → `dofollow_status("foo") == True`, no error.
- Happy path: `register("bar", BarAdapter, dofollow=False, rationale="X" * 80)` → registered, `dofollow_rationale("bar")` returns the string.
- Error path: `register("baz", BazAdapter, dofollow=False, rationale="too short")` → raises `RegistryError`.
- Error path: `register("baz", BazAdapter, dofollow=False)` (rationale=None) → raises `RegistryError`.
- Error path: `register("baz", BazAdapter, dofollow="uncertain", rationale=None)` → raises `RegistryError`.
- Error path: `register("devto", DevtoAdapter, dofollow=False, rationale="X" * 80)` → raises `RegistryError` citing the prior rejection rationale and the un-rejection instruction ("delete this entry from `_REJECTED_PLATFORMS` in the same PR as the new `register()` call").
- Happy path (un-rejection by deletion): in a test fixture, `_REJECTED_PLATFORMS.pop("devto")` followed by `register("devto", DevtoAdapter, dofollow=False, rationale="X" * 80)` → registered, `dofollow_status("devto") == False`, `"devto" not in _REJECTED_PLATFORMS`. Fixture restores `_REJECTED_PLATFORMS["devto"]` afterward.
- Edge case: `register("foo", FooAdapter)` (no dofollow kwarg) → succeeds, `dofollow_status("foo")` returns `None`. This back-compat is removed in U5.
- Edge case: `register("foo", FooAdapter, dofollow=True, rationale="ignored")` → succeeds; `dofollow_rationale("foo")` returns the string (R4: allowed but not validated).
- Integration: existing `tests/test_r9_extension_readiness.py` continues to pass without modification (no dofollow kwarg passed, back-compat path).

**Verification:**
- All 9 scenarios pass.
- Existing test suite green (3204 pre-change tests).

---

- [ ] **Unit 3: Backfill register() call sites (7 production + 1 conftest + 6 banner-integration) + extend snapshot fixtures**

**Goal:** Add explicit `dofollow=` to every existing `register()` call site (7 production + 7 test) and extend the conftest snapshot fixture to cover the parallel dicts.

**Requirements:** R6

**Dependencies:** Unit 2

**Files:**
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py` — add `dofollow=` to all 7 lines (blogger, medium, telegraph, velog, ghpages, hashnode, writeas). Hashnode gets `dofollow=False, rationale="GraphQL API moved behind paywall on 2026-05-13; Cloudflare anti-bot blocks *.hashnode.dev direct fetch; publish path operationally dead"` (tune wording to ≥80 chars).
- Modify: `tests/conftest.py:206-221` — extend `fake_platform_registered` to snapshot+restore `_DOFOLLOW_BY_PLATFORM.get("fake")` and `_RATIONALE_BY_PLATFORM.get("fake")` alongside `_REGISTRY`. Call the `_register("fake", FakeAdapter, dofollow=True)` with explicit kwarg.
- Modify: `tests/test_publish_backlinks_banner_integration.py:145,176,202,225,256,280` — add `dofollow=True` to all 6 synthetic adapter registrations.
- Modify: `tests/test_publish_backlinks_banner_integration.py:105-111` — extend the heavier whole-registry snapshot pattern to include parallel dicts.

**Approach:**
- Six production True registrations: blogger / medium / telegraph / velog / ghpages / writeas. One False: hashnode.
- Test fixture registrations are synthetic, all `dofollow=True` (they don't represent real platforms, no rationale needed).
- **conftest `fake_platform_registered` extension (per-key pattern)**: the existing fixture saves a single key via `previous = __REGISTRY.get("fake")` then restores it. Mirror this for the new parallel dicts: snapshot `_DOFOLLOW_BY_PLATFORM.get("fake")` and `_RATIONALE_BY_PLATFORM.get("fake")` before, restore (or pop if None) after. Three additional save lines + three restore lines.
- **banner-integration `_register_test_platform` extension (whole-registry pattern)**: the existing fixture does `snapshot = {k: list(v) for k, v in _REGISTRY.items()}; yield; _REGISTRY.clear(); _REGISTRY.update(snapshot)` — wholesale clear + restore. Apply symmetric treatment to the parallel dicts: capture three snapshots before yield (`dofollow_snapshot = dict(_DOFOLLOW_BY_PLATFORM)`, `rationale_snapshot = dict(_RATIONALE_BY_PLATFORM)`); after yield do three `clear() + update(snapshot)` pairs. Approximately six additional lines, not three — different shape than the per-key fixture.
- The two fixture extensions are NOT symmetric and must be specified separately. A fixture-interleaving test should verify state doesn't leak when both run in sequence.

**Patterns to follow:**
- `src/backlink_publisher/publishing/adapters/__init__.py:45-51` existing one-line registration style
- `tests/conftest.py:212-220` snapshot/restore pattern

**Test scenarios:**
- Happy path: full test suite passes post-backfill with no behavior change to the suite's existing assertions.
- Happy path: `registry.dofollow_status("blogger")` returns `True`; `registry.dofollow_status("hashnode")` returns `False`; `registry.dofollow_rationale("hashnode")` is ≥80 chars.
- Edge case: `fake_platform_registered` fixture teardown leaves no stale entries in `_DOFOLLOW_BY_PLATFORM` or `_RATIONALE_BY_PLATFORM` (assert via post-fixture inspection).
- Edge case: `_register_test_platform` fixture teardown restores all three dicts to their pre-fixture state (post-fixture inspection: `dict(_DOFOLLOW_BY_PLATFORM) == pre_snapshot`).
- Edge case (fixture interleaving): run a test using `fake_platform_registered` then a test using `_register_test_platform` then a third test that asserts neither "fake" nor any synthetic platform name (`optin_test` etc.) appears in any of the three dicts. Verifies no cross-fixture state leak.
- Integration: `tests/test_publish_backlinks_banner_integration.py` continues to pass (6 synthetic registrations work with `dofollow=True`).

**Verification:**
- Full pytest suite green.
- `radon raw -s src/backlink_publisher/publishing/registry.py` shows no monolith-budget impact (file not in budget).
- Grep verification: `grep -rn "register(" tests/ src/` — every match is either (a) a site in this unit's backfill list, (b) a `test_registry_*` or `test_adapter_*` test exercising the contract, or (c) a docstring/comment. No untracked production registration exists.

---

- [ ] **Unit 4: Migrate WebUI consumers to registry accessor**

**Goal:** Switch `webui_app/helpers.py` and `webui_app/binding_status.py` from `_DOFOLLOW_BY_CHANNEL.get(name)` to `registry.dofollow_status(name)`. The map still exists at this point; this unit only changes read sites.

**Requirements:** R7 (partial — read-path migration), R8

**Dependencies:** Unit 3 (backfill must be done so accessor returns correct values for all 7 platforms)

**Files:**
- Modify: `webui_app/binding_status.py:74` — replace `_DOFOLLOW_BY_CHANNEL.get(name)` with a lazy import of `registry.dofollow_status(name)` (lazy to avoid circular: `webui_app` → `publishing.registry`).
- Modify: `webui_app/helpers.py:945` — replace `_DOFOLLOW_BY_CHANNEL.get(channel)` with `registry.dofollow_status(channel)`. Remove the now-unused lazy import at line 927 (`from .binding_status import _DOFOLLOW_BY_CHANNEL`).
- Modify: `webui_app/templates/_channel_card_macro.html:23-31` — add a branch for the literal string `"uncertain"` (renders the existing `badge-dofollow unknown` orange "dofollow?" badge). Today's template branches only on `== True` / `== False` / `is none`; without this branch, an `"uncertain"` value falls through to no-badge.
- Modify: `tests/test_webui_token_paste.py:176` + `tests/test_save_config_new_channel_roots.py:288` + `tests/test_settings_dashboard_rendering.py:91` — update any assertions or comments that mention `_DOFOLLOW_BY_CHANNEL` to reference the accessor instead (these are mostly comment-references per repo-research; verify exact shape during implementation).
- Test: extend existing `tests/test_webui_*` files with regression assertions confirming badge state matches `registry.dofollow_status` output.

**Approach:**
- Preserve the `.get(name)`-returns-`None`-for-unmapped semantics: `registry.dofollow_status` returns `None` for unregistered platforms, matching today's behavior for binding cards with no card rendered.
- Hashnode visual change is the only operator-observable shift (orange → no badge). Call it out in the PR description.
- The map (`_DOFOLLOW_BY_CHANNEL`) still exists post-U4 — deletion is U5. This unit only switches read paths.

**Patterns to follow:**
- `webui_app/helpers.py:929` lazy-import pattern (avoid module-level `from backlink_publisher.publishing.registry import dofollow_status`)
- `webui_app/binding_status.py:59-76` `get_channel_status` shape

**Test scenarios:**
- Happy path: `get_channel_status("blogger", config)` returns `{"dofollow": True, ...}` post-migration.
- Happy path: `_token_paste_status(cfg, "ghpages", load_fn)` returns `{..., "dofollow": True, ...}`.
- Edge case: `get_channel_status("hashnode", config)` returns `{"dofollow": False, ...}` (the badge rebadging).
- Edge case: `get_channel_status("nonexistent_platform", config)` returns `{"dofollow": None, ...}` — matches today's `.get()` returning `None` for unmapped.
- Integration: WebUI dashboard render-test (`tests/test_settings_dashboard_rendering.py`) renders Hashnode card with the weak "nofollow" badge (replacing the prior orange "dofollow?" unknown badge).
- Integration: A synthetic registration with `dofollow="uncertain"` renders the orange `badge-dofollow unknown` "dofollow?" badge via the new template branch.

**Verification:**
- WebUI tests pass.
- Manual: launch `python webui.py`, navigate to settings, confirm: blogger/medium/etc. render green "dofollow" badges; hashnode renders the weak "nofollow" badge (no longer the orange "dofollow?" unknown badge).

---

- [ ] **Unit 5: Delete map, flip `dofollow=` to required, land CI gate test**

**Goal:** The atomic gate-activation commit. Removes the now-unused `_DOFOLLOW_BY_CHANNEL`, removes the `dofollow=None` default from `register()`, and lands the CI gate test that asserts every registration has `dofollow_status` set + rationale ≥80 for non-True. Includes a WebUI grep-gate sub-test.

**Requirements:** R2 (required-flip), R7 (map deletion), R9, R10, R13

**Dependencies:** Unit 4 (all read sites migrated first so deletion is safe)

**Files:**
- Modify: `webui_app/binding_status.py:31-58` — delete `_DOFOLLOW_BY_CHANNEL` map and its type alias.
- Modify: `src/backlink_publisher/publishing/registry.py` — remove `dofollow=None` default from `register()`; missing kwarg now raises `TypeError`. Add explicit `raise RegistryError("…explicit message…")` if a caller still somehow sets `dofollow=None` explicitly (defensive — should be unreachable post-U3).
- Create: `tests/test_adapter_dofollow_gate.py` — parametrized over `registered_platforms()`; asserts (a) `dofollow_status(name)` is not None for every entry; (b) if `dofollow in (False, "uncertain")`, `dofollow_rationale(name)` is ≥80 chars stripped; (c) `_REGISTRY.keys() ∩ _REJECTED_PLATFORMS.keys() == ∅` strictly (no exceptions — un-rejection is by deletion, R12).
- Create: synthetic red-path test in the same file proving the gate fires when violated (mirror `tests/test_no_monolith_regrowth.py:212-228`).

**Approach:**
- Atomic in the same commit: deletion + required-flip + gate test together. No exposed transition window.
- No grep-gate sub-test — the ImportError verification (`python -c "from webui_app.binding_status import _DOFOLLOW_BY_CHANNEL"` raises) is a stronger correctness signal than substring scanning, and the post-deletion green test suite is the final safety net. (Dropped per document-review F3 + scope-guardian finding: substring grep false-fires on comment-only references that U4 may leave updated-but-still-mentioning-the-name.)
- Synthetic red-path: write a helper `_install_violation()` that monkey-patches `_REGISTRY` with a missing `dofollow` entry, asserts the gate test raises, then restores. Use the conftest fixture pattern to ensure cleanup.

**Patterns to follow:**
- `tests/test_no_monolith_regrowth.py:32-83,128-154,212-228` — full structure: parametrize → per-entry schema test → value test → synthetic red-path
- Failure messages tell operator exactly what to edit: "platform '<name>' is missing dofollow=. Add dofollow={True,False,'uncertain'} to its register() call in publishing/adapters/__init__.py."

**Test scenarios:**
- Happy path: gate test passes against current `registered_platforms()` (7 entries, all properly declared).
- Happy path: gate test asserts `_REGISTRY.keys() ∩ _REJECTED_PLATFORMS.keys() == ∅` against the current state (no overlap with devto / mastodon / wordpresscom).
- Edge case: synthetic red-path — install a `dofollow=None` violation, assert the gate test raises with a useful failure message naming the offending platform.
- Edge case: synthetic red-path — install a `dofollow=False` with `rationale=None` violation, assert gate fires.
- Edge case: synthetic red-path — install a `_REJECTED_PLATFORMS` entry whose name overlaps with a `_REGISTRY` entry, assert the disjoint-keys gate fires.
- Integration: full test suite green post-deletion. No `ImportError` or `AttributeError` from a forgotten reader of `_DOFOLLOW_BY_CHANNEL` (caught by suite, not by grep-gate).

**Verification:**
- `pytest tests/test_adapter_dofollow_gate.py -v` passes all parametrized cases.
- `pytest` full suite green.
- `python -c "from webui_app.binding_status import _DOFOLLOW_BY_CHANNEL"` raises `ImportError`.

---

- [ ] **Unit 6: AGENTS.md note (in-PR documentation only)**

**Goal:** Add a single-sentence pointer in AGENTS.md so contributors discover the new gate when adding a new publisher adapter.

**Requirements:** None directly — supports the contributor-walkthrough surface.

**Dependencies:** Units 1-5 (semantic completion)

**Files:**
- Modify: `backlink-publisher/AGENTS.md` — add one paragraph under the "Adding a new publisher adapter" section: a single sentence pointing at the new gate (`tests/test_adapter_dofollow_gate.py`) and the `_REJECTED_PLATFORMS` invariant, with a link to `docs/plans/2026-05-20-009-feat-adapter-dofollow-gate-plan.md` for context.

**Approach:**
- Genuinely co-located with this PR's code — the AGENTS.md edit references machinery shipping in the same PR.
- Plan 003 Phase C plan-doc edit and the memory→solutions promotion are intentionally **out of this PR's scope** — see Deferred Followups below. Keeps the gate PR surface focused.

**Patterns to follow:**
- AGENTS.md "Adding a new publisher adapter" section for the contributor-walkthrough style.

**Test scenarios:**
- Test expectation: none — documentation-only change. Verification is human review of the AGENTS.md diff.

**Verification:**
- AGENTS.md "Adding a new publisher adapter" section mentions the gate, the rationale-length rule, and the `_REJECTED_PLATFORMS` invariant in a single paragraph.

---

### Deferred Followups (separate PR, same day)

Two pieces from the original Unit 6 scope are deferred to a same-day follow-up PR so this PR's surface stays focused on the gate machinery (per document-review scope-guardian finding):

1. **Plan 003 Phase C plan-doc edit**: `docs/plans/2026-05-20-003-feat-portfolio-roundtrip-spike-quality-plan.md:86,348-353,411` — replace `_DOFOLLOW_BY_CHANNEL` references with `registry.dofollow_status(name)`. Update §G3 unchanged-invariants if it cited the old map. Phase C hasn't shipped, so this is a literal text substitution with no live consumer impact.
2. **Memory → `docs/solutions/` promotion**: create `docs/solutions/best-practices/grep-dofollow-map-before-shipping-adapter-2026-05-20.md` promoting `feedback_grep_dofollow_map_before_shipping_adapter` per AGENTS.md "Lessons capture" rules. Frontmatter MUST NOT include a `category:` key (per `feedback_solutions_category_frontmatter`). Strip operator domain names per CLAUDE.md "Known traps". Identifier-scrub check: `grep -E "\.(com|net|org|io)"` returns zero hits other than well-known platform names (devto / hashnode / dev.to / mastodon / wordpresscom / write.as).

## System-Wide Impact

- **Interaction graph:** `publishing.registry.register()` mutates 3 module-level dicts (`_REGISTRY`, `_DOFOLLOW_BY_PLATFORM`, `_RATIONALE_BY_PLATFORM`). All readers go through accessor functions, not direct dict access — keeps mutation surface single-source. WebUI lazy-imports the accessor (matches existing pattern at `helpers.py:929`).
- **Error propagation:** `RegistryError` raised at import time → adapter package import fails → CLI / WebUI / tests all fail-fast with clear stderr message. No silent registrations. The CI gate test catches the same class of error one layer earlier (PR-CI rather than runtime).
- **State lifecycle risks:** Parallel-dict storage means the conftest fixture must snapshot/restore three dicts instead of one. Tracked explicitly in Unit 3 — risk mitigated by atomic-PR landing of all three dict's keys together.
- **API surface parity:** `register()` signature change is additive in the U2 commit, becomes breaking in U5. Single-PR atomicity ensures no half-state visible to external consumers (no external consumers exist — `register()` is internal to `publishing/adapters/__init__.py`).
- **Integration coverage:** WebUI dashboard render (U4 scenarios) is the cross-layer test that mocks alone won't prove. The gate test (R9) is the cross-cutting invariant test.
- **Unchanged invariants:** R9 extension contract (`tests/test_r9_extension_readiness.py`) — one-line `register()` extension still works (gains one required kwarg, doesn't break the "one line per platform" pattern). `embed_banner` Protocol opt-in — unchanged. Throttle metadata via `AdapterResult.post_publish_delay_seconds` — unchanged. Plan 003 Phase A/B/D — unchanged in this PR; Phase C plan-doc text edit moved to follow-up PR.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Rationale length-only gate is anti-correlated with truth (5-char honest fact rejected, 80-char filler accepted). | Accept — PR review is the human content gate; CI enforces presence/length only. No worse than today's no-gate baseline. Surfaced in origin §Residual concerns. |
| Hashnode UI rebadge (orange → no badge) might be perceived as regression. | PR description explicitly calls out the visual delta + rationale. Operator-side runbook unchanged (Hashnode publish path was already broken). |
| Conftest fixture extension missed in some test file → registry state leaks across tests. | Unit 3 explicitly enumerates the two snapshot sites (`tests/conftest.py:206-221` + `tests/test_publish_backlinks_banner_integration.py:105-111`). Test scenarios assert post-fixture cleanup. |
| Module-level invariant trap from `docs/solutions/logic-errors/invert-drift-check-when-invariant-becomes-dynamic-2026-05-18.md` — any module-level constant derived from `_REGISTRY` will fire during half-loaded pytest imports. | Use accessor functions, never module-level constants computed from `_REGISTRY`. Gate test calls `registered_platforms()` at function scope, not at module scope. |
| Concurrent worktree wipes (this session lost two doc files to a `git reset` from a parallel process per memory `feedback_worktree_concurrent_switching`). | Commit each Unit atomically; do not leave the plan file untracked for extended periods. |
| Plan 003 Phase C ships independently with stale reference. | Same-day follow-up PR (per Unit 6 Deferred Followups) updates the plan doc text. If Phase C ships in between, the same-day follow-up still applies — Phase C is a plan doc, not yet code. |
| Per-commit CI failure during the U1→U6 sequence on the PR branch. | Squash-merge strategy (§Key Technical Decisions): only the final HEAD state is required to pass CI; intermediate per-commit pushes MAY fail. PR description calls this out for reviewers. |

## Documentation / Operational Notes

- AGENTS.md "Adding a new publisher adapter" walkthrough gains one sentence about the dofollow gate (Unit 6).
- PR description: explicit callout of (a) Hashnode visual rebadge as one-time honest correction, (b) the breaking-change semantics for any external code calling `register()` (none exist today), (c) the new `_REJECTED_PLATFORMS` runtime invariant.
- Per-adapter rationale strings become a permanent commit-history record at the call site — Unit 3's hashnode rationale should be carefully worded since it's the single example contributors will look at when wondering "how do I write a rationale?"

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-20-adapter-dofollow-gate-requirements.md](docs/brainstorms/2026-05-20-adapter-dofollow-gate-requirements.md)
- **Related code:** `src/backlink_publisher/publishing/registry.py`, `src/backlink_publisher/publishing/adapters/__init__.py`, `webui_app/binding_status.py`, `webui_app/helpers.py:920-948`, `tests/conftest.py:206-221`, `tests/test_no_monolith_regrowth.py` (pattern reference), `tests/test_r9_extension_readiness.py`, `tests/test_publish_backlinks_banner_integration.py:105-111,145-280`
- **Related PRs:** #108 (Phase 4 nofollow scaffold shipped), #109 (9-minute revert), #112 (WebUI token-paste cards — introduced the second `_DOFOLLOW_BY_CHANNEL` consumer in `helpers.py`), #99 + #114 + #116 (config managed-roots ext — the "minimal wedge then iterate" pattern this plan follows)
- **Related plans:** `docs/plans/2026-05-18-006-feat-monolith-sloc-ceiling-plan.md` (CI gate precedent), `docs/plans/2026-05-20-003-feat-portfolio-roundtrip-spike-quality-plan.md` (Phase C downstream consumer — plan-doc edit in Unit 6)
- **Memory references:** `feedback_grep_dofollow_map_before_shipping_adapter`, `project_phase4_scaffold`, `feedback_publish_history_invariant_helper` (helper-centralization pattern), `feedback_solutions_category_frontmatter` (do not add `category` field when promoting to solutions)
