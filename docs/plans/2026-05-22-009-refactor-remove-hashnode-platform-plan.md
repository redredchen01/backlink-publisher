---
title: "refactor: Remove Hashnode platform completely"
type: refactor
status: completed
date: 2026-05-22
deepened: 2026-05-22
claims: {}
---

# refactor: Remove Hashnode platform completely

## Overview

Completely excise the Hashnode publishing platform from the codebase. The operator no longer uses Hashnode; the GraphQL API was paywalled behind a Pro tier as of 2026-05-13, and the browser-bind spike (Plan 016) was never completed. Remove the adapter, browser recipe, bind recipe, config types, WebUI surface, and all tests. No replacement or migration path is needed.

## Problem Frame

Hashnode accumulated significant surface area across six layers (API adapter, browser publish recipe, CSS selectors, bind recipe, config types, WebUI settings card + token-paste route) without ever producing a production-ready publishing path. The GraphQL API paywall made the API adapter permanently non-functional for free-tier operators. Retaining the code imposes test maintenance and cognitive overhead for zero user benefit.

## Requirements Trace

- R1. `registered_platforms()` no longer returns `"hashnode"` after removal
- R2. `Config` no longer has `.hashnode` or `.hashnode_token_path` attributes
- R3. All Hashnode-specific test files are deleted; full pytest suite passes
- R4. Cross-cutting tests that used "hashnode" as a fixture string use a different channel; behavioral coverage is preserved
- R5. WebUI settings page has no Hashnode section; token-paste route has no Hashnode handler
- R6. `monolith_budget.toml` ceiling for `adapters/__init__.py` is lowered to reflect actual post-removal SLOC
- R7. `grep -r "hashnode" src/ webui_app/ tests/` returns no functional code; known acceptable comment residuals after full removal: `browser_publish/dispatcher.py:140` (inline comment example), `browser_publish/recipes/__init__.py:4` (docstring), `writeas.py` docstring lines (covered by the parallel write.as removal plan)

## Scope Boundaries

- Do NOT touch write.as removal; its own plan (`2026-05-22-008-refactor-remove-writeas-platform-plan.md`) is active and in progress — no overlap needed
- Do NOT modify the `HIDDEN_FROM_UI` constant in `binding_status.py`. Hashnode was never added to `HIDDEN_FROM_UI` (unlike write.as which used it as a transitional step), so no change is needed there — leave the frozenset untouched
- `_bind/recipes/hashnode.py` exists but is NOT imported by any module or test — delete the file; no registry change needed in `_bind/recipes/__init__.py`
- Do NOT delete `docs/solutions/` entries that reference Hashnode as a canonical example of "probe-then-pivot" or "None-returning embed_banner" — those document a design pattern, not the adapter
- Spike notes in `docs/spike-notes/` that are purely Hashnode-specific may be deleted; leave non-Hashnode spike files untouched
- `docs/solutions/probe-then-pivot-when-api-unverifiable-2026-05-20.md` should be preserved but its inline code snippet referencing `publishing/adapters/hashnode.py` should be updated to use a generic placeholder (`<adapter>.py`) — the pattern being documented is valid, but the concrete module path will point to a deleted file

## Context & Research

### Relevant Code and Patterns

**Deletion targets (standalone files):**
- `src/backlink_publisher/publishing/adapters/hashnode.py` — `HashnodeAPIAdapter`; full GraphQL-based API adapter
- `src/backlink_publisher/publishing/browser_publish/recipes/hashnode.py` — `BrowserPublishRecipe` for hashnode; populates `RECIPES["hashnode"]` at import time
- `src/backlink_publisher/publishing/browser_publish/recipes/_hashnode_selectors.py` — CSS selectors used by the browser recipe
- `src/backlink_publisher/cli/_bind/recipes/hashnode.py` — bind recipe; NOT in `_bind/recipes/__init__.py` RECIPES dict; exists but is unreachable from any running code

**Surgical modification targets in `adapters/__init__.py` (current SLOC: 602, ceiling: 640):**
- Line 35: `from .hashnode import HashnodeAPIAdapter` — import to remove
- Line 51: `from ..browser_publish.recipes import hashnode as _hashnode_recipe  # noqa: F401` — side-effect import to remove
- Lines 83–89: `register("hashnode", HashnodeAPIAdapter, BrowserPublishDispatcher.for_channel("hashnode"), dofollow=False, rationale=_R["hashnode"])` — registration block to remove
- `verify_adapter_setup`: `if platform == "hashnode":` branch (~lines 233–243) — DependencyError guard for missing config/token; remove
- `_verify_live`: `if platform == "hashnode": return _verify_hashnode_live(config)` — routing line to remove
- `_verify_hashnode_live` function (~line 758+) — full function to remove

**Config layer (6 files):**
- `config/types.py` — `HashnodeConfig` dataclass (line 166); `Config.hashnode: HashnodeConfig | None = None` field (line 375); `hashnode_token_path` property (line 404)
- `config/tokens.py` — `("hashnode", "hashnode-token.json")` in `snapshot_token_revs` list (line 29); `load_hashnode_token()` (line 96); `save_hashnode_token()` (line 101)
- `config/__init__.py` — re-exports `HashnodeConfig` (line 31), `load_hashnode_token` (line 65), `save_hashnode_token` (line 72), and their `__all__` entries
- `config/loader.py` — `hashnode_section` parsing block (lines 183–189); `hashnode=hashnode` kwarg (line 225)
- `config/writer.py` — `hashnode_config` parameter (line 51); merge branch (line 94); serialization block (lines 153–156)
- `config/_toml_utils.py` — `"hashnode"` in known-sections set (line 13)

**WebUI surface (3 files):**
- `webui_app/helpers/contexts.py` — imports `load_hashnode_token` (line 241); `hashnode_status` (line 254); `hashnode_config_summary` (lines 260–261); return dict entries (lines 336–337)
- `webui_app/routes/token_paste.py` — `"hashnode": (save_hashnode_token, "hashnode-token.json", "token")` entry (line 36) + `save_hashnode_token` import (line 21)
- `webui_app/templates/settings.html` — entire `#channel-hashnode` accordion section (lines ~237–270)

**Nofollow rationale (1 file):**
- `src/backlink_publisher/publishing/adapters/_nofollow_rationales.py` — `"hashnode": (...)` entry (lines 14–19)

### Institutional Learnings

- **R9 acceptance test is fully dynamic** — `test_r9_extension_readiness.py` uses `registered_platforms()` via a FakeAdapter fixture, no hardcoded platform set. Removing `register("hashnode", ...)` does NOT break it.
- **The `save_config` write path re-creates TOML sections on next save** — removing the `hashnode_config` branch from `writer.py` must land in the same unit as removing the `HashnodeConfig` type.
- **config.example.toml has no `[hashnode]` stanza** — confirmed by grep; no change needed there.
- **`test_browser_publish_dispatcher.py` uses "hashnode" as a publish-only channel fixture** (not in `_bind/recipes/__init__.py` RECIPES dict). After removing hashnode, replace with "devto" — also a publish-only BrowserPublishDispatcher channel not in bind RECIPES.
- **`test_browser_publish_chrome_session.py` uses "hashnode" as an arbitrary channel string** for ChromeAttachSession mechanics tests. Replace with "devto" or any string; the tests do not exercise hashnode-specific code paths.
- **`test_banner_dispatcher.py` uses `platform="hashnode"` to test generic banner error paths** — replace with `platform="devto"` (also nofollow, also BrowserPublishDispatcher-based).
- **Monolith ceiling must drop in the same PR** — current ceiling is 640, current SLOC is 602. Hashnode removal cuts ~60–80 SLOC (register block + imports + verify_adapter_setup branch + `_verify_hashnode_live` function). Measure with radon post-edit and set ceiling to `round_up_to_10(new_sloc + 30)`.

### External References

None — pure deletion with no external API surface.

## Key Technical Decisions

- **Full deletion, not HIDDEN_FROM_UI**: Write.as used `HIDDEN_FROM_UI` as a transitional step (PR #136) before full deletion. Hashnode was never in `HIDDEN_FROM_UI`, and the operator explicitly confirmed complete removal. Skip the transitional step; delete directly.
- **Replace "hashnode" fixture strings in dispatcher/chrome-session tests rather than deleting tests**: `test_browser_publish_dispatcher.py` and `test_browser_publish_chrome_session.py` test generic BrowserPublishDispatcher mechanics, not Hashnode behavior. Replacing the channel string with "devto" preserves behavioral coverage at zero cost.
- **Delete `_bind/recipes/hashnode.py` without touching `_bind/recipes/__init__.py`**: The file exists but is not imported anywhere. Its deletion is safe and complete without a RECIPES registration change.
- **Lower monolith budget ceiling in the same PR**: Post-removal SLOC will be ~530–540. Leaving the ceiling at 640 removes the signal that guards against regrowth of the dispatching layer. Measure with radon and set `ceiling = round_up_to_10(new_sloc + 30)`.

## Open Questions

### Resolved During Planning

- **Will R9 tests break?** No — `registered_platforms()` is dynamic; no hardcoded platform set to update.
- **Does `config.example.toml` have a `[hashnode]` stanza?** No — confirmed by grep.
- **Is `_bind/recipes/hashnode.py` imported anywhere?** No — grep confirms it is not. Safe to delete without touching `_bind/recipes/__init__.py`.
- **Does HIDDEN_FROM_UI need updating?** No — Hashnode was never in it.
- **Will `test_settings_dashboard_rendering.py` drift-check break?** No — it subtracts `len(HIDDEN_FROM_UI)` dynamically. Hashnode was not in the frozenset, so no count change.

### Deferred to Implementation

- **Exact SLOC of `adapters/__init__.py` after removal**: Run `python -m radon raw -s src/backlink_publisher/publishing/adapters/__init__.py` post-edit and set the new ceiling.
- **Whether `test_browser_publish_dispatcher.py` line 163 fixture needs "hashnode" semantics**: The test checks that a channel IN bind RECIPES raises `AuthExpiredError`; "devto" is NOT in bind RECIPES. Implementer should verify the auth-expired vs dependency-error branch distinction still holds after the channel swap, and pick a channel that is in bind RECIPES if the test requires it.

## Implementation Units

```
Units 1+5 (atomic) → Unit 2 → Unit 3 → Unit 4
                                           ↓
                                      Unit 6 (depends on 1–4)
Unit 7 (depends on Unit 2 for SLOC measure; AGENTS.md edit is independent)
```
*Units 1 and 5 must land in the same commit — test_hashnode_paywall_detection.py imports from hashnode.py.*

- [ ] **Unit 1: Delete standalone Hashnode source files**

**Goal:** Remove all Hashnode-specific source files that have no non-Hashnode content.

**Requirements:** R1, R7

**Dependencies:** None

**Files:**
- Delete: `src/backlink_publisher/publishing/adapters/hashnode.py`
- Delete: `src/backlink_publisher/publishing/browser_publish/recipes/hashnode.py`
- Delete: `src/backlink_publisher/publishing/browser_publish/recipes/_hashnode_selectors.py`
- Delete: `src/backlink_publisher/cli/_bind/recipes/hashnode.py`

**Approach:**
- Straight file deletion; no module needs updating because the imports are removed in Unit 2 and Unit 3.
- Do not touch `instant_web.py` — `TelegraphCdpAdapter` there is unrelated to Hashnode.

**Patterns to follow:**
- Mirrors write.as adapter deletion pattern from the same session.

**Test scenarios:**
- Test expectation: none — this unit only deletes files; correctness is confirmed by full pytest passing after Unit 2 cleans the imports.

**Verification:**
- `ls src/backlink_publisher/publishing/adapters/hashnode.py` → file not found
- `ls src/backlink_publisher/publishing/browser_publish/recipes/hashnode.py` → file not found
- `ls src/backlink_publisher/cli/_bind/recipes/hashnode.py` → file not found

---

- [ ] **Unit 2: Deregister Hashnode from adapter registry and nofollow rationales**

**Goal:** Remove all Hashnode references from `adapters/__init__.py` and `_nofollow_rationales.py` so `registered_platforms()` no longer includes "hashnode".

**Requirements:** R1, R6, R7

**Dependencies:** Unit 1 (file deletions must precede import removals to keep the module importable)

**Files:**
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py`
- Modify: `src/backlink_publisher/publishing/adapters/_nofollow_rationales.py`

**Approach:**
- In `__init__.py`, remove six sites: (1) `from .hashnode import HashnodeAPIAdapter`, (2) `from ..browser_publish.recipes import hashnode as _hashnode_recipe`, (3) the `register("hashnode", ...)` block, (4) the `if platform == "hashnode":` branch in `verify_adapter_setup` (offline mode), (5) the `if platform == "hashnode": return _verify_hashnode_live(config)` line in `_verify_live`, (6) the `_verify_hashnode_live` function body (including its lazy `from .hashnode import ...` statement inside the function).
- In `_nofollow_rationales.py`, remove the `"hashnode": (...)` entry from `NOFOLLOW_RATIONALES`.
- After editing, run `python -m radon raw -s src/backlink_publisher/publishing/adapters/__init__.py` to get the new SLOC for Unit 7's monolith budget update.

**Patterns to follow:**
- Matches write.as deregistration pattern; see `monolith_budget.toml` rationale for the precedent commit.

**Test scenarios:**
- Happy path: `registered_platforms()` returns a set that does not include "hashnode"
- Happy path: `verify_adapter_setup("hashnode", cfg)` raises `ExternalServiceError("unsupported platform: hashnode")` (falls through to the unknown-platform branch)
- Happy path: `dispatch({"platform": "hashnode", ...}, ...)` raises `ExternalServiceError` (no registered adapters)
- Edge case: `_nofollow_rationales.NOFOLLOW_RATIONALES["hashnode"]` raises `KeyError`
- Test file: `tests/test_r9_extension_readiness.py` (existing) — must still pass

**Verification:**
- `python -c "from backlink_publisher.publishing import registered_platforms; assert 'hashnode' not in registered_platforms()"` exits 0
- `python -m py_compile src/backlink_publisher/publishing/adapters/__init__.py` exits 0

---

- [ ] **Unit 3: Purge Hashnode from config layer**

**Goal:** Remove `HashnodeConfig` dataclass, `Config.hashnode` field, `hashnode_token_path` property, and all token helpers from the config module.

**Requirements:** R2, R7

**Dependencies:** Unit 1 (hashnode.py adapter references `HashnodeConfig`)

**Files:**
- Modify: `src/backlink_publisher/config/types.py`
- Modify: `src/backlink_publisher/config/tokens.py`
- Modify: `src/backlink_publisher/config/__init__.py`
- Modify: `src/backlink_publisher/config/loader.py`
- Modify: `src/backlink_publisher/config/writer.py`
- Modify: `src/backlink_publisher/config/_toml_utils.py`
- Test: `tests/test_save_config_new_channel_roots.py`

  *(All edits to `test_save_config_new_channel_roots.py` live here in Unit 3 — do NOT repeat them in Unit 6. The config-type removal and test repairs are one atomic change.)*

**Approach:**
- `types.py`: Delete the `HashnodeConfig` dataclass (lines 166–200), `Config.hashnode` field (line 375), and `hashnode_token_path` property (lines 404–406).
- `tokens.py`: Remove `("hashnode", "hashnode-token.json")` tuple from `snapshot_token_revs` list, delete `load_hashnode_token()` and `save_hashnode_token()` functions.
- `__init__.py`: Remove `HashnodeConfig`, `load_hashnode_token`, `save_hashnode_token` from imports and `__all__`.
- `loader.py`: Remove the `hashnode_section = data.get("hashnode")` block (lines 183–189) and `hashnode=hashnode` kwarg in the `Config(...)` constructor call.
- `writer.py`: Remove `hashnode_config` parameter from `save_config()` signature, its merge branch (`hashnode_cfg = ...`), and the `if hashnode_cfg is not None:` serialization block.
- `_toml_utils.py`: Remove `"hashnode"` from the known-sections frozenset.

**Patterns to follow:**
- Mirrors write.as config cleanup (which has already been completed in the same session).

**Test scenarios:**
- Happy path: `Config()` instantiates without a `hashnode` field — `hasattr(cfg, "hashnode")` is `False`
- Happy path: `load_config()` on a TOML file that contains a `[hashnode]` section silently ignores it (unknown section) — no exception; `"hashnode"` is removed from the known-sections set so `_toml_utils` no longer lists it as valid
- Error path: `from backlink_publisher.config import load_hashnode_token` raises `ImportError` after removal
- Edge case: `save_config(existing_cfg)` on a config that previously had a `hashnode` section does not re-emit `[hashnode]` in the output file (serialization branch removed)
- Tests to remove/repair in `tests/test_save_config_new_channel_roots.py`:
  - Delete `test_hashnode_block_survives_save_when_only_on_disk`
  - In `test_emitted_channel_blocks_carry_only_routing_fields` (~line 289): remove the `hashnode_config=HashnodeConfig(publication_id="pub")` kwarg and the `assert "[hashnode]" in text` assertion; keep the ghpages half intact
  - Remove the `HashnodeConfig` import at line 34 (used only by the above test sites)
  - Remove any other round-trip assertions that expect `"[hashnode]"` to survive save

**Verification:**
- `python -m py_compile src/backlink_publisher/config/types.py src/backlink_publisher/config/loader.py src/backlink_publisher/config/writer.py` exits 0
- `python -c "from backlink_publisher.config import Config; cfg = Config(); assert not hasattr(cfg, 'hashnode')"` exits 0

---

- [ ] **Unit 4: Purge Hashnode from WebUI**

**Goal:** Remove the Hashnode settings card, token-paste handler, and context variables from the WebUI layer.

**Requirements:** R5, R7

**Dependencies:** Unit 3 (contexts.py imports `load_hashnode_token` from config)

**Files:**
- Modify: `webui_app/helpers/contexts.py`
- Modify: `webui_app/routes/token_paste.py`
- Modify: `webui_app/templates/settings.html`

**Approach:**
- `contexts.py`: Remove `load_hashnode_token` import (line 241), `hashnode_status` variable (line 254), `hashnode_config_summary` list (lines 260–261), and the two return-dict entries `hashnode_status=...` and `hashnode_config_summary=...` (lines 336–337).
- `token_paste.py`: Remove `save_hashnode_token` import (line 21) and `"hashnode": (save_hashnode_token, "hashnode-token.json", "token")` entry (line 36) from the channel handler dict.
- `settings.html`: Delete the entire Hashnode accordion section — the `<button data-bs-toggle="collapse" data-bs-target="#channel-hashnode">` block through the matching closing `</div>` for `id="channel-hashnode"` (~lines 237–270).

**Patterns to follow:**
- Matches how Velog, DevTo, and other channels are wired in `contexts.py` — removing one channel does not affect others.

**Test scenarios:**
- Happy path: `GET /settings` does not render any element with `id="channel-hashnode"` or text "Hashnode"
- Happy path: `POST /api/token-paste {"channel": "hashnode", "token": "..."}` returns 400 or 404 (channel no longer registered in the handler dict)
- Edge case: `settings_context()` does not include `hashnode_status` or `hashnode_config_summary` keys in its return dict

**Verification:**
- `grep -n "hashnode" webui_app/helpers/contexts.py webui_app/routes/token_paste.py webui_app/templates/settings.html` returns no matches
- Existing WebUI tests for other channels (velog, devto, etc.) continue to pass

---

- [ ] **Unit 5: Delete Hashnode-only test files**

**Goal:** Remove all test files that exist exclusively to test Hashnode functionality.

**Requirements:** R3

**Dependencies:** Must land **atomically with Unit 1** (same commit / same working-tree state). `tests/test_hashnode_paywall_detection.py` imports `_probe_hashnode_paywall` and `_paywall_cache` directly from `hashnode.py`. If Unit 1 deletes `hashnode.py` before Unit 5 deletes this test file, pytest collection fails for the entire module with `ImportError`.

**Files:**
- Delete: `tests/test_adapter_hashnode.py`
- Delete: `tests/test_hashnode_banner.py`
- Delete: `tests/test_hashnode_paywall_detection.py`
- Delete: `tests/test_browser_publish_hashnode.py`

**Approach:**
- Straight file deletion. Each file covers only Hashnode; no other test file depends on them.

**Test scenarios:**
- Test expectation: none — deletion is confirmed by `ls` and full pytest passing after Units 1–4 complete.

**Verification:**
- `ls tests/test_hashnode*.py tests/test_adapter_hashnode.py tests/test_browser_publish_hashnode.py` → all not found

---

- [ ] **Unit 6: Repair cross-cutting tests**

**Goal:** Remove or replace "hashnode" references in test files that primarily test non-Hashnode behavior.

**Requirements:** R3, R4

**Dependencies:** Units 1–4 (imports removed; tests will fail to compile if Hashnode symbols are still referenced)

**Files:**
- Modify: `tests/test_canonical_contract.py`
- Modify: `tests/test_browser_publish_dispatcher.py`
- Modify: `tests/test_browser_publish_chrome_session.py`
- Modify: `tests/test_webui_publish_backend_pill.py`
- Modify: `tests/test_registry_auth_expired_fallthrough.py`
- Modify: `tests/test_banner_dispatcher.py`
- Modify: `tests/test_chrome_backend_host_filter.py`

  *(Note: `test_chrome_backend_host_filter.py` has no import dependency on Hashnode — its only change is a docstring cleanup and can be done at any point, not blocked on Units 1–4.)*

**Approach:**
- `test_canonical_contract.py`: Remove the `from backlink_publisher.publishing.adapters.hashnode import _build_publish_input` import (line 29) **and all test methods that use `_build_publish_input`** — this function appears in at least lines 67, 72, 77, and 221 across multiple test methods (not only `test_hashnode_forwards_verbatim`). Leaving any of these after Unit 1 deletes `hashnode.py` causes `ImportError` at collection time for the entire file.
- `test_browser_publish_dispatcher.py`: Replace all `"hashnode"` channel string literals with `"devto"`. Replace signin URL stubs (e.g., `"https://hashnode.com/signin?return_to=/new"`) with `"https://dev.to/sign-in"` — `/sign-in` matches the dispatcher's signin regex (`/signin|sign-in|log-?in`); `/enter` does NOT and will silently break the `DependencyError` branch test. Update match pattern strings like `"hashnode browser publish failed"` to `"devto browser publish failed"` (the error format is `f"{channel} browser publish failed: {exc}"`). Rationale: "devto" is not in `CHANNELS` (`frozenset({"velog", "medium", "blogger"})` in `cli._bind.channels`) — the actual dispatcher check — so publish-only `DependencyError` semantics are preserved. Verify: `python -c "from backlink_publisher.cli._bind.channels import CHANNELS; assert 'devto' not in CHANNELS"`.
- `test_browser_publish_chrome_session.py`: Replace `"hashnode"` channel string literals with `"devto"`, and `"https://hashnode.com/new"` with `"https://dev.to/new"`. These tests exercise ChromeAttachSession mechanics, not Hashnode DOM.
- `test_save_config_new_channel_roots.py`: Handled in Unit 3 — do not re-edit here.
- `test_webui_publish_backend_pill.py`: Remove the `("hashnode", "api+chrome")` entry from the parametrize decorator.
- `test_registry_auth_expired_fallthrough.py`: Remove the docstring/comment line mentioning "hashnode: browser adapter ships in plan-016 Unit 3, not yet".
- `test_banner_dispatcher.py`: Replace `platform="hashnode"` with `platform="devto"` in the three banner error test cases (lines ~110, ~279, ~288). "devto" also uses `BrowserPublishDispatcher` as its banner path so the behavioral coverage is equivalent.
- `test_chrome_backend_host_filter.py`: Remove the Hashnode spike-report reference from the module docstring.

**Patterns to follow:**
- `test_browser_publish_dispatcher.py` and `test_browser_publish_chrome_session.py` use channel strings as opaque fixture values; swapping to "devto" preserves test intent while removing the Hashnode dependency.

**Test scenarios:**
- Happy path: `pytest tests/test_browser_publish_dispatcher.py` passes with devto as the fixture channel
- Happy path: `pytest tests/test_canonical_contract.py` passes after removing hashnode import and test
- Happy path: `pytest tests/test_banner_dispatcher.py` passes with devto as the fixture platform
- Edge case: `pytest tests/test_save_config_new_channel_roots.py` passes after hashnode round-trip tests are removed
- Integration: `pytest tests/` full suite passes (all 4 deleted files + 8 modified files)

**Verification:**
- `pytest tests/` exits 0
- `grep -r "hashnode" tests/` returns no matches (except acceptable pattern-name comments in unrelated test docstrings)

---

- [ ] **Unit 7: Update monolith budget and delete docs artifacts**

**Goal:** Lower the monolith budget ceiling for `adapters/__init__.py` to reflect actual post-removal SLOC, and delete Hashnode-specific documentation artifacts.

**Requirements:** R6

**Dependencies:** Unit 2 (adapters/__init__.py must be edited before measuring new SLOC)

**Files:**
- Modify: `monolith_budget.toml`
- Delete: `docs/plans/2026-05-20-016-feat-hashnode-browser-bind-plan.md`
- Delete: `docs/spike-notes/2026-05-20-hashnode-chrome-bind.py`
- Delete: `docs/spike-notes/2026-05-20-hashnode-dofollow-probe.py`
- Delete: `docs/spike-notes/2026-05-20-hashnode-probes.py`
- Delete: `docs/spike-notes/2026-05-20-hashnode-bind-discovery.md`
- Delete: `docs/spike-notes/2026-05-21-hashnode-probes-raw.json`
- Delete: `docs/spike-notes/2026-05-22-hashnode-playwright-stealth-spike.md`
- Delete: `docs/spike-notes/extract_hashnode_cookies.py`
- Delete: `docs/spike-notes/2026-05-22-hashnode-stealth-runners/` (entire directory)
- Modify: `backlink-publisher/AGENTS.md` — remove `"hashnode"` from the adapters reference table (~line 89) and delete the hashnode upload-media contract entry (~line 347, "hashnode: uploadMedia GraphQL mutation …")

**Approach:**
- After Unit 2 edits, run `python -m radon raw -s src/backlink_publisher/publishing/adapters/__init__.py` to get new SLOC. Set `ceiling = round_up_to_10(new_sloc + 30)` and update the `rationale` to note the Hashnode removal with the date and SLOC delta.
- Spike notes and the Plan 016 doc are dead artifacts; no test or code references them.

**Test scenarios:**
- Happy path: `pytest tests/test_no_monolith_regrowth.py -k "R4"` passes with the updated ceiling

**Verification:**
- `pytest tests/test_no_monolith_regrowth.py` exits 0
- `ls docs/plans/2026-05-20-016-feat-hashnode-browser-bind-plan.md` → not found
- `ls docs/spike-notes/ | grep hashnode` → no matches

---

## System-Wide Impact

- **Interaction graph:** `registered_platforms()` loses one entry. The R9 extension test, the drift-check test, and the settings dashboard rendering test all consume this dynamically — they auto-adjust. No hardcoded platform set exists.
- **Error propagation:** `publish({"platform": "hashnode", ...}, ...)` will raise `ExternalServiceError("unsupported platform: hashnode")` after removal, same as any unknown platform. Existing callers that gated Hashnode will start hitting this error; this is intended.
- **State lifecycle risks:** `hashnode-token.json` on disk (if it exists for an operator) will become an orphan file; the app will no longer read or write it. No migration needed — the file is harmless at rest.
- **API surface parity:** `verify_adapter_setup("hashnode", cfg)` changes from a Hashnode-specific DependencyError path to the generic "unsupported platform" path. No external callers are expected.
- **Unchanged invariants:** All other platforms (medium, velog, devto, blogger, telegraph, ghpages, notion, mastodon) are untouched. The adapter registry contract and NOFOLLOW_RATIONALES pattern are unchanged.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Cross-cutting tests use "hashnode" as a fixture value — swapping to "devto" may change test semantics if devto is in bind RECIPES | Confirmed: "devto" is not in `_bind/recipes/__init__.py` RECIPES dict; publish-only channel semantics are preserved |
| `test_browser_publish_dispatcher.py` auth-expired vs dependency-error distinction depends on channel being in/not-in CHANNELS | Implementer must verify devto is not in CHANNELS before using it for the publish-only DependencyError test; if needed, pick "mastodon" or "notion" instead |
| `contexts.py` changes break other settings routes if removal is incomplete | Full pytest + manual `GET /settings` smoke verifies; template changes are isolated to the hashnode accordion block |
| Monolith ceiling left too high allows unintended regrowth | Measure with radon post-edit and set ceiling in the same commit (Unit 7 is explicitly gated on Unit 2 completion) |

## Documentation / Operational Notes

- `config.example.toml` has no `[hashnode]` stanza — no update needed.
- Operators who previously had `hashnode-token.json` at `~/.config/backlink-publisher/` can delete it manually; the app will not touch it going forward.
- AGENTS.md "Adding a new publisher adapter" recipe remains valid — no changes needed there.

## Sources & References

- Hashnode GraphQL paywall: `docs/memory/reference_hashnode_graphql_paywall.md` — paywall confirmed 2026-05-13; gql.hashnode.com 301s to announcement; Pro plan required; adapter effectively broken for free-tier operators
- Related plan (write.as precedent): `docs/plans/2026-05-22-008-refactor-remove-writeas-platform-plan.md`
- PR #136 (HIDDEN_FROM_UI pattern): `c2560ba` — reference for why we skip the transitional step here
- `backlink-publisher/AGENTS.md` → "Adding a new publisher adapter" recipe
