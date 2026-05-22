---
title: "refactor: Remove write.as platform completely"
type: refactor
status: completed
date: 2026-05-22
claims: {}
---

# refactor: Remove write.as platform completely

## Overview

Completely excise the write.as (writeas) publishing platform from the codebase. PR #136 (2026-05-21) already hid it from the WebUI via `HIDDEN_FROM_UI`, but the adapter code, config types, token helpers, and test suite all remain. This plan finishes the job: delete adapter source files, remove config-layer types, empty the `HIDDEN_FROM_UI` frozenset, delete writeas tests, and lower the monolith budget ceiling accordingly.

## Problem Frame

write.as was never a high-value publishing target (nofollow on most posts, fragile CDP path that was never wired into the registry dispatch chain). PR #136 suppressed its UI surface; the operator has confirmed the platform should be fully removed. Keeping dead adapter code incurs ongoing maintenance cost and test surface for zero user benefit.

## Requirements Trace

- R1. `registered_platforms()` no longer returns `"writeas"` after removal
- R2. `Config` no longer has `.writeas` or `.writeas_token_path` attributes
- R3. All writeas test files are deleted; full pytest suite passes
- R4. `HIDDEN_FROM_UI` is cleared; drift-check tests still pass (they use the constant dynamically)
- R5. `monolith_budget.toml` ceiling for `adapters/__init__.py` is lowered to reflect actual SLOC
- R6. `grep -r "writeas" src/ webui_app/ tests/` returns only stale pattern-name comments in non-writeas adapters (acceptable hygiene noise), no functional code

## Scope Boundaries

- Do NOT remove the `HIDDEN_FROM_UI` constant from `binding_status.py` — keep it as `frozenset()` so the drift-check test infrastructure stays intact for future use
- Do NOT touch `TelegraphCdpAdapter` — it lives in `instant_web.py` alongside `WriteAsCdpAdapter` and must be preserved exactly
- Do NOT remove comments in `velog_graphql.py`, `hashnode.py`, `image_gen/types.py` that use "writeas-style" as shorthand for the None-returning embed_banner pattern — these are documentation of a design pattern, not functional code; they can be updated as low-priority hygiene but are not blockers

## Context & Research

### Relevant Code and Patterns

- `src/backlink_publisher/publishing/adapters/__init__.py` — adapter registry; `register("writeas", WriteAsAPIAdapter, dofollow=True)` at line 92; writeas verify branch in `verify_adapter_setup` (~lines 247–267, 331–332); `_verify_writeas_live` function (~lines 866–930)
- `src/backlink_publisher/publishing/adapters/writeas.py` — 276 lines, full WriteAsAPIAdapter; safe to delete entirely
- `src/backlink_publisher/publishing/adapters/instant_web.py` — `WriteAsCdpAdapter` class at lines 226–300; `__all__` includes `"WriteAsCdpAdapter"` at line 305; `TelegraphCdpAdapter` stays untouched
- `src/backlink_publisher/config/types.py` — `WriteAsConfig` dataclass at ~line 205
- `src/backlink_publisher/config/loader.py` — writeas TOML section parsing at lines 192–198, `writeas=writeas` arg at line 226
- `src/backlink_publisher/config/writer.py` — writeas serialization branch (one if-block)
- `src/backlink_publisher/config/tokens.py` — `("writeas", "writeas-token.json")` in snapshot list; `load_writeas_token()` and `save_writeas_token()` functions
- `src/backlink_publisher/config/_toml_utils.py` — `"writeas"` in the known-sections set at line 13
- `src/backlink_publisher/config/__init__.py` — re-exports `WriteAsConfig`, `load_writeas_token`, `save_writeas_token`
- `webui_app/binding_status.py` — `HIDDEN_FROM_UI: frozenset[str] = frozenset({"writeas"})` at ~line 39
- `monolith_budget.toml` — `adapters/__init__.py` ceiling 720; writeas.py itself not tracked (so no ceiling-removal needed, only the ceiling lowering)

### Institutional Learnings

- **HIDDEN_FROM_UI drift tests use the constant dynamically** — `test_settings_dashboard_rendering.py` subtracts `len(HIDDEN_FROM_UI)` from expected platform counts. Setting `HIDDEN_FROM_UI = frozenset()` is safe; the tests auto-adjust without editing.
- **R9 acceptance test is fully dynamic** — uses `registered_platforms()` via a FakeAdapter fixture, no hardcoded platform set. Removing `register("writeas", ...)` does NOT break `test_r9_extension_readiness.py`.
- **`save_config` write paths must be removed when removing config types** — any serialization branches for writeas keys in `writer.py` re-create the TOML section on next save. Removal of the branch must land in the same unit as the type removal.
- **`config.example.toml` has no `[writeas]` stanza** — confirmed by grep; no change needed there.
- **Removing `("writeas", "writeas-token.json")` from `snapshot_token_revs()`** — correct behavior; the function no longer watches for writeas credential rotation.

### External References

None needed — this is a pure deletion with no external API surface.

## Key Technical Decisions

- **Keep `HIDDEN_FROM_UI` as `frozenset()` rather than deleting it**: The constant is referenced in `webui_app/__init__.py`, `webui_app/helpers/contexts.py`, and drift-check tests. Deleting it breaks more sites than emptying it. The empty frozenset is semantically correct.
- **Delete `WriteAsCdpAdapter` from `instant_web.py`, not the whole file**: `TelegraphCdpAdapter` and `_ChromeSession` in the same file are live and used. Surgical class removal preserves them.
- **Update `test_banner_dispatcher.py` by replacing writeas parametrize cases with `telegram`/`telegraph`**: The dispatcher test validates the None-return path pattern, not writeas specifically. Use `telegraph` (which also returns `None` from `embed_banner`) as a substitute so the behavioral coverage is preserved.
- **Lower monolith budget ceiling in the same PR**: After removal, `adapters/__init__.py` loses ~120 SLOC. Leaving the ceiling at 720 while the file is ~570 SLOC allows unintended regrowth without a CI signal. Measure with radon post-edit and set to `round_up_to_10(new_sloc + 30)`.

## Open Questions

### Resolved During Planning

- **Will R9 tests break?** No — `test_r9_extension_readiness.py` uses `registered_platforms()` dynamically via FakeAdapter fixture. No hardcoded platform set to update.
- **Is `_DOFOLLOW_BY_CHANNEL` in `binding_status.py` a separate removal site?** No — dofollow knowledge was already moved to the registry in an earlier PR. The `register(..., dofollow=True)` call is the only site; removing it removes dofollow metadata too.
- **Does `config.example.toml` have a `[writeas]` stanza?** No — confirmed by grep; no change needed.
- **Will `test_settings_dashboard_rendering.py` drift-check fail?** No — it uses `len(HIDDEN_FROM_UI)` dynamically; emptying the frozenset auto-adjusts the expected count.

### Deferred to Implementation

- **Exact SLOC of `adapters/__init__.py` after removal**: Run `python -m radon raw -s src/backlink_publisher/publishing/adapters/__init__.py` after editing and set the new ceiling accordingly.
- **Which `test_hashnode_banner.py`/`test_velog_banner.py` docstring lines to rewrite**: Low-priority hygiene; implementer decides whether to rename "writeas-style" pattern references or leave as historical documentation.

## Implementation Units

- [ ] **Unit 1: Delete adapter source files**

**Goal:** Remove all adapter implementation code for writeas — the API adapter file and the CDP adapter class from `instant_web.py`.

**Requirements:** R1, R6

**Dependencies:** None

**Files:**
- Delete: `src/backlink_publisher/publishing/adapters/writeas.py`
- Modify: `src/backlink_publisher/publishing/adapters/instant_web.py`

**Approach:**
- Delete `writeas.py` entirely — 276 lines, no other file exports from it that must be preserved
- In `instant_web.py`: delete the `WriteAsCdpAdapter` class (lines 226–300 approx.), remove `"WriteAsCdpAdapter"` from `__all__` (keep `"TelegraphCdpAdapter"`), rewrite the module docstring line that says "telegra.ph and write.as/new both allow..." to only mention telegra.ph
- Do NOT touch `TelegraphCdpAdapter`, `_ChromeSession`, or any other class in the file

**Test scenarios:**
- Happy path: `python -m py_compile src/backlink_publisher/publishing/adapters/instant_web.py` exits 0 after edit
- Edge case: `from backlink_publisher.publishing.adapters.instant_web import WriteAsCdpAdapter` raises `ImportError` after deletion
- Edge case: `from backlink_publisher.publishing.adapters.instant_web import TelegraphCdpAdapter` still imports successfully

**Verification:**
- `writeas.py` file no longer exists on disk
- `instant_web.py` compiles cleanly; `WriteAsCdpAdapter` is absent from `__all__`

---

- [ ] **Unit 2: Clean adapter registry and verify function**

**Goal:** Remove all writeas references from `adapters/__init__.py` — imports, `register()` call, the verify dispatch branch, and the `_verify_writeas_live()` function.

**Requirements:** R1, R6

**Dependencies:** Unit 1 must precede this unit because the module-level imports at lines 36 and 43 (`from .instant_web import WriteAsCdpAdapter`, `from .writeas import WriteAsAPIAdapter`) will raise `ImportError` at import time if the source files are deleted without removing these lines first. Note: the `from .writeas import _load_token, ...` inside `_verify_writeas_live()` at line 887 is a lazy import (inside the function body) — it would only fail when called, not at module load time. The module-level imports are the binding constraint.

**Files:**
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py`
- Test: `tests/test_r9_extension_readiness.py` (spot-check only — no edits expected)

**Approach:**
- Line 36: remove `WriteAsCdpAdapter` from the `from .instant_web import ...` line (keep `TelegraphCdpAdapter`)
- Line 43: delete `from .writeas import WriteAsAPIAdapter`
- Lines 64–68: rewrite or delete the comment about "CDP adapters not yet in dispatch chain" since its only example was `WriteAsCdpAdapter`
- Line 92: delete `register("writeas", WriteAsAPIAdapter, dofollow=True)`
- Lines 247–267: delete the `if platform == "writeas":` branch inside `verify_adapter_setup()` entirely
- Lines 331–332: delete `if platform == "writeas": return _verify_writeas_live(config)`
- Lines 866–930 approx.: delete `_WRITEAS_VERIFY_TIMEOUT_S` constant and `_verify_writeas_live()` function (~95 lines)

**Test scenarios:**
- Happy path: `"writeas" not in registered_platforms()` — import the registry and assert
- Happy path: full pytest passes (Unit 1 + Unit 2 together are the first green state)
- Integration: `verify_adapter_setup("writeas", config)` raises `DependencyError` or `ValueError` rather than returning a writeas-specific result (since writeas is no longer a registered platform, the function's platform-in-registry guard should fire first)
- Edge case: importing `backlink_publisher.publishing.adapters` succeeds without `ImportError` (the imports we removed pointed to now-deleted files)

**Verification:**
- `grep -n "writeas\|WriteAs" src/backlink_publisher/publishing/adapters/__init__.py` returns zero matches
- `pytest tests/test_r9_extension_readiness.py` passes without modification

---

- [ ] **Unit 3: Remove config layer types and token helpers**

**Goal:** Delete all writeas-specific config types, token functions, TOML parsing, serialization branches, and config re-exports.

**Requirements:** R2, R6

**Dependencies:** Unit 2 (adapters/__init__.py's `_verify_writeas_live` imported `_load_token` from writeas.py; that import is already gone after Unit 2)

**Files:**
- Modify: `src/backlink_publisher/config/types.py`
- Modify: `src/backlink_publisher/config/__init__.py`
- Modify: `src/backlink_publisher/config/loader.py`
- Modify: `src/backlink_publisher/config/writer.py`
- Modify: `src/backlink_publisher/config/tokens.py`
- Modify: `src/backlink_publisher/config/_toml_utils.py`

**Approach:**
- `types.py`: delete `WriteAsConfig` dataclass; delete `writeas: WriteAsConfig | None = None` field and `writeas_token_path` property from `Config`
- `__init__.py`: remove `WriteAsConfig` from imports and `__all__`; remove `load_writeas_token` and `save_writeas_token` from imports and `__all__`
- `loader.py`: remove `WriteAsConfig` from imports; delete the `writeas_section = data.get("writeas")` block (lines 192–198); delete `writeas=writeas` from the `Config(...)` constructor call
- `writer.py`: remove `WriteAsConfig` from imports; delete `writeas_config: WriteAsConfig | None = None` parameter; delete `writeas_cfg = ...` local; delete the `if writeas_cfg is not None: lines.append("[writeas]")` serialization block
- `tokens.py`: remove `("writeas", "writeas-token.json")` tuple from the list inside `snapshot_token_revs()`; delete `load_writeas_token()` function; delete `save_writeas_token()` function
- `_toml_utils.py`: remove `"writeas"` from the known-sections set

**Test scenarios:**
- Happy path: `from backlink_publisher.config import Config` succeeds; `Config` has no `writeas` attribute
- Happy path: `from backlink_publisher.config import WriteAsConfig` raises `ImportError`
- Edge case: load a config TOML that has a `[writeas]` section — the loader should silently ignore the unknown section (since `_toml_utils.py` removal takes it out of the known-sections set; verify the behavior is "skip unknown" not "raise")
- Integration: `python -m py_compile src/backlink_publisher/config/loader.py` exits 0

**Verification:**
- `grep -rn "WriteAsConfig\|writeas_token\|load_writeas_token\|save_writeas_token" src/backlink_publisher/config/` returns zero results
- `pytest tests/ -k "config" --tb=short` passes

---

- [ ] **Unit 4: WebUI surface and binding status cleanup**

**Goal:** Empty `HIDDEN_FROM_UI` and clean up the few remaining WebUI template/route comments that mention writeas.

**Requirements:** R4, R6

**Dependencies:** Units 1–3 must be complete so `registered_platforms()` no longer includes writeas before the drift-check tests run

**Files:**
- Modify: `webui_app/binding_status.py`
- Modify: `webui_app/routes/token_paste.py`
- Modify: `webui_app/templates/_settings_channel_token_paste.html`

**Approach:**
- `binding_status.py` line ~39: change `frozenset({"writeas"})` to `frozenset()` — keep the constant definition and its docstring comment; do not delete the constant
- `token_paste.py`: update any comment that says "writeas was..." to remove the reference or generalize it
- `_settings_channel_token_paste.html`: remove `"writeas"` from the example comment in the template

**Test scenarios:**
- Happy path: `test_settings_dashboard_rendering.py` passes without edits — it uses `len(HIDDEN_FROM_UI)` dynamically; the empty frozenset auto-adjusts expected counts
- Happy path: `"writeas" not in HIDDEN_FROM_UI` is True
- Integration: `pytest tests/test_settings_dashboard_rendering.py tests/test_webui_route_contract.py --tb=short` passes

**Verification:**
- `HIDDEN_FROM_UI == frozenset()`
- No writeas references remain in `webui_app/` Python files (grep returns zero functional hits)

---

- [ ] **Unit 5: Delete and clean writeas test files**

**Goal:** Remove the two writeas-dedicated test files and surgically edit the handful of other test files that reference writeas.

**Requirements:** R3, R6

**Dependencies:** Units 1–4 must be complete; test files importing from writeas adapters or config types will import-error until the source is gone

**Files:**
- Delete: `tests/test_adapter_writeas.py`
- Delete: `tests/test_writeas_banner.py`
- Modify: `tests/test_canonical_contract.py`
- Modify: `tests/test_banner_dispatcher.py`
- Modify: `tests/test_save_config_new_channel_roots.py`
- Modify: `tests/test_webui_publish_backend_pill.py`
- Modify (docstring only): `tests/test_config_public_api_resolvable.py`, `tests/test_hashnode_banner.py`, `tests/test_velog_banner.py`, `tests/test_webui_route_contract.py`, `tests/test_webui_token_paste.py`

**Approach:**
- Delete `test_adapter_writeas.py` and `test_writeas_banner.py` entirely
- `test_canonical_contract.py`: delete the `from backlink_publisher.publishing.adapters.writeas import _build_post_body` import line; delete the `TestWriteasCanonical` class and `test_writeas_forwards_verbatim` test
- `test_banner_dispatcher.py`: replace the four `platform="writeas"` parametrize entries with `platform="telegraph"` (or another adapter that also returns `None` from `embed_banner`) — the test validates the None-return dispatcher pattern, not writeas specifically
- `test_save_config_new_channel_roots.py`: remove `WriteAsConfig` import; delete `test_writeas_block_survives_save_when_only_on_disk`; also strip the `[writeas]` fixture stanza from `test_round_trip_is_idempotent_for_all_three_channels` (lines ~244–279) — that test uses a three-channel TOML fixture that must have the writeas block removed; delete the `assert "[writeas]" in text` assertion and `[writeas]` entry in the `for chan in ("...", "[writeas]"):` loop inside `test_emitted_channel_blocks_carry_only_routing_fields` (lines ~336, 341); remove `writeas_config=WriteAsConfig(...)` keyword argument from any remaining `save_config(...)` call; update module docstring to remove `/ [writeas]`
- `test_webui_publish_backend_pill.py`: remove `("writeas", "api")` from the parametrize list
- Docstring/comment updates in remaining files: update stale mentions of writeas in `test_config_public_api_resolvable.py` (docstring historical example), `test_hashnode_banner.py`/`test_velog_banner.py` (docstring pattern name), `test_webui_route_contract.py` (comment about writeas retired), `test_webui_token_paste.py` (stale absence assertions that can be removed)

**Test scenarios:**
- Happy path: `pytest tests/ --tb=short -q` exits 0 with no writeas-related failures
- Edge case: `pytest tests/ -k writeas` returns "no tests ran" (zero collected)
- Integration: `pytest tests/test_banner_dispatcher.py --tb=short` passes with the telegraph-substituted parametrize entries

**Verification:**
- `grep -r "writeas\|WriteAs" tests/` returns only stale pattern-name comments in `test_hashnode_banner.py`/`test_velog_banner.py` docstrings (acceptable) — no imports, no test classes, no parametrize entries

---

- [ ] **Unit 6: Lower monolith budget ceiling and update AGENTS.md**

**Goal:** Reduce `adapters/__init__.py` SLOC ceiling to reflect the ~120-line deletion; remove writeas from AGENTS.md adapter table.

**Requirements:** R5

**Dependencies:** Unit 2 must be complete (the lines must actually be removed before measuring)

**Files:**
- Modify: `monolith_budget.toml`
- Modify: `AGENTS.md`
- Delete: `docs/plans/2026-05-21-002-fix-writeas-content-blocked-clarity-plan.md`

**Approach:**
- After Unit 2 edits are done: run `python -m radon raw -s src/backlink_publisher/publishing/adapters/__init__.py` to get new SLOC
- In `monolith_budget.toml`: update `[files."src/backlink_publisher/publishing/adapters/__init__.py"]` ceiling to `round_up_to_10(new_sloc + 30)` with a rationale explaining the writeas removal and date
- Note: `SLOC_CANARY_EXPECTED` in `tests/fixtures/sloc_canary.py` does NOT need updating — it only changes when the `radon` version is bumped (currently pinned at `==6.0.1`). This plan does not change radon.
- Note: `writeas.py` itself is NOT tracked in `monolith_budget.toml` — there is no ceiling entry to delete for it; only the `adapters/__init__.py` ceiling needs lowering
- `AGENTS.md`: remove the writeas row from the adapter table; remove `- **writeas**: NO media-upload API → ...` bullet from the per-platform upload contract section (if present)
- Delete `docs/plans/2026-05-21-002-fix-writeas-content-blocked-clarity-plan.md` — it was a plan for a platform that no longer exists and was never shipped

**Test scenarios:**
- Happy path: `pytest tests/test_no_monolith_regrowth.py --tb=short` passes with the new ceiling
- Edge case: new ceiling < 720 (verifies the ceiling was actually lowered, not left at the old value)
- Test expectation: none for AGENTS.md edit — documentation only

**Verification:**
- `monolith_budget.toml` ceiling for `adapters/__init__.py` is < 720
- `pytest tests/test_no_monolith_regrowth.py` passes
- `docs/plans/2026-05-21-002-*` file no longer exists

## System-Wide Impact

- **`registered_platforms()` shrinks by one**: All callers that iterate platform lists (schema validation, CLI argparse choices, WebUI channel selects) will no longer see `"writeas"`. This is the intended effect.
- **`HIDDEN_FROM_UI` becomes empty**: drift-check tests that compare `len(registered_platforms()) - len(HIDDEN_FROM_UI)` to a dashboard channel count will auto-adjust because both sides shrink by 1 in the same PR.
- **`Config` loses `.writeas` and `.writeas_token_path`**: Any operator code or plugin that reads `config.writeas` will get `AttributeError` after this change. Acceptable — no external consumers were identified.
- **`snapshot_token_revs()` no longer watches writeas-token.json**: Correct; there is no writeas to rotate credentials for.
- **`save_config` will no longer re-create a `[writeas]` block**: Operator configs with a `[writeas]` stanza will have it silently skipped by the loader (unknown section). They will not lose other data.
- **Unchanged invariants**: Telegraph, Medium, Velog, Hashnode, Blogger, GitHub Pages adapters — all untouched. The `HIDDEN_FROM_UI` constant itself is preserved (as empty frozenset) so future platform retirements can reuse the pattern.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `TelegraphCdpAdapter` accidentally removed alongside `WriteAsCdpAdapter` from `instant_web.py` | Unit 1 explicitly calls out which class to delete; add import smoke test |
| `test_banner_dispatcher.py` parametrize replacement breaks if telegraph also lost `embed_banner` support | Verify telegraph adapter implements `embed_banner` returning `None` before substituting |
| `_verify_writeas_live` deletion misses line range | Use grep to count remaining writeas function references after edit; CI compile check catches import-time errors |
| Lowered monolith ceiling causes spurious failure if implementer edits `adapters/__init__.py` after measuring | Measure SLOC as the final step of Unit 6, after all Unit 2 edits are settled |
| Drift-check in `test_settings_dashboard_rendering.py` fails if writeas was also in a separate hardcoded set | Run the test after Unit 4 before moving to Unit 5; roll back if unexpected |

## Sources & References

- PR #136 (MERGED 2026-05-21 `c2560ba`) — introduced `HIDDEN_FROM_UI` pattern
- `webui_app/binding_status.py` — `HIDDEN_FROM_UI` definition
- `src/backlink_publisher/publishing/adapters/__init__.py` — registry, verify, `_verify_writeas_live`
- `src/backlink_publisher/publishing/adapters/writeas.py` — the adapter to delete
- `src/backlink_publisher/publishing/adapters/instant_web.py` — `WriteAsCdpAdapter` to remove
- `monolith_budget.toml` — ceiling at 720 must be lowered
- `docs/solutions/workflow-issues/grep-dofollow-map-before-shipping-adapter-2026-05-20.md` — two-site registration reminder
- `docs/solutions/logic-errors/invert-drift-check-when-invariant-becomes-dynamic-2026-05-18.md` — drift test behavior
