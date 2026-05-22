---
title: helpers/__init__.py final extraction — last 779 SLOC into 5 focused sub-modules
type: refactor
status: obsolete
date: 2026-05-22
deepened: 2026-05-22
obsoleted_at: 2026-05-22
origin: docs/plans/2026-05-21-006-refactor-webui-comprehensive-optimization-plan.md
---

> **OBSOLETE 2026-05-22**: Plan 2026-05-21-007 Unit 5 shipped as PR #180 (commit `ceac466`) **the same day this plan was written**. Origin/main `0413f65` shows `helpers/__init__.py` at 11 SLOC (docstring only, no re-export hub) and a new `helpers/contexts.py` (612 LOC / 499 SLOC) holding the remaining 8 concerns in a single file — master plan F2's ≤120-SLOC goal already over-achieved.
>
> The 5-unit decomposition this plan proposed (splitting contexts.py further into llm / schedule / channel_status / token_paste / contexts / render) was considered NOT YET WORTH DOING by the user when re-asked with the new reality (contexts.py at 499 SLOC is "less urgent fan-out" vs. the original 779 SLOC god-module). Re-open this plan if contexts.py grows past ~600 SLOC or if a future audit re-surfaces it.
>
> **Lesson:** `[[feedback-ce-work-must-reverify-state]]` — the plan was written, deepened, and document-reviewed all on 2026-05-22; within those hours PR #180 merged on origin/main and silently invalidated every premise. The `git fetch + check origin/main HEAD` step at ce:work Phase 1 caught it before any code was touched. Keep this as a worked example.

# helpers/__init__.py final extraction

## Overview

This is the **Plan A** referenced by the WebUI master plan `2026-05-21-006`. Plan 007 Units 1–4 already extracted `helpers/{url_meta,history,security,cli_runner}.py` (mid May 2026). What remains in `helpers/__init__.py` is **779 LOC / 541 radon SLOC of 8 unrelated concerns** — LLM settings, schedule, channel-status probes, token-paste status, settings-context builder, render auto-inject, plus tiny strays (`_load_incomplete_run`, `_persist_three_tier_config`, `_draft_tab_extra`).

This plan extracts the remainder into 5 new sub-modules so `__init__.py` shrinks to a re-export hub + `__all__` declaration. No behavior changes; no transitional top-level shim file.

**Honest framing of the win:** Total project SLOC stays roughly flat (~541 → ~120 hub + ~563 across 6 new/extended sub-modules = ~683 SLOC). This is a *fan-out / concern-isolation* refactor, not a SLOC reduction. Maintainability gains come from (a) per-concern reviewer load instead of god-module scanning, (b) per-concern test attribution (the existing `test_webui_helpers_medium_status.py`, `test_webui_image_gen.py`, `test_webui_checkpoint.py` already match sub-module boundaries), and (c) the acyclic-edges test makes inter-sub-module dependencies explicit and reviewable. The justification stands but is *fan-out*, not *shrink*.

## Problem Frame

Master plan finding **F2**: `helpers.py` is the WebUI's last god-module. Eight unrelated concerns share the same file; 23 import sites (14 test files + `webui.py` + 8 internal `webui_app/` modules — grounded census 2026-05-22) reach into it; pyflakes finds it tractable but humans don't. The team has been chipping away (Plan 007 Units 1–4) but stopped half-way. Until the split lands, every WebUI maintenance plan re-pays the cost of reading 779 lines to find where a symbol lives.

This plan finishes the job using the same mechanics Plan 007 used:
- one sub-module per Unit
- `__init__.py` re-imports the moved names so the public surface stays stable (existing pattern; not a deprecated `helpers.py` shim)
- extend `tests/test_webui_helpers_subpackage_acyclic.py` `KNOWN_MODULES` per Unit
- delete original code in the same PR that adds the sub-module

## Requirements Trace

- **R1** (from master plan R3): Sequence into independently-shippable units that mirror Plan 007 discipline.
- **R2** (from master plan R4): Preserve the publish-history invariant helper, global CSRF guard, 0o600 secret writes, `HIDDEN_FROM_UI` filter. `_render` and `_settings_context` reach into all of them — extraction must be behavior-preserving.
- **R3** (from master plan R5): Each extraction lands with a sub-module acyclic-invariant entry, so the next audit doesn't have to re-discover this split.
- **R4** (new): The post-Unit-5 `helpers/__init__.py` must shrink to ≤120 radon SLOC **total** (includes re-import lines + `__all__` block; measured via `python -m radon raw -s webui_app/helpers/__init__.py`). Budget ladder: hard-gate at ≤140 SLOC (PR blocked above); ≤120 SLOC is the target. Implementer drafts the post-U5 `__init__.py` body in a scratch file and runs radon BEFORE opening U5 PR — if the draft is 121–140 SLOC, ship with a rationale comment; if >140 SLOC, pause and redesign (e.g., consolidate the `__all__` block, drop the noqa annotations). The 120-vs-140 gap exists because the current `__all__` block alone is ~26 SLOC and a small group-comment + import-line accretion can blow a tight target.

## Scope Boundaries

- **In scope:** `webui_app/helpers/__init__.py` and 5 new sibling modules under `webui_app/helpers/`. The existing extracted sub-modules (`url_meta.py`, `history.py`, `security.py`, `cli_runner.py`) are touched only when sub-module imports need to be updated.
- **Out of scope:** Behavior changes inside any of the moved functions. Pyflakes sweep (master plan Unit 1). `_render` auto-inject perf optimization (master plan Plan E-perf). `_settings_context` disk-I/O reduction (master plan F10 → Plan E-perf). Adding new monolith_budget ceilings (master plan declared out-of-scope, kept).
- **Deferred:** Migrating callers from `from webui_app.helpers import _X` to `from webui_app.helpers.X import _X`. Re-exports at `__init__.py` keep the package surface stable; targeted caller-side migration is a separate housekeeping task and not load-bearing for the SLOC win.

## Context & Research

### Relevant Code and Patterns

- `webui_app/helpers/__init__.py:43-779` — the 8 concerns being extracted; see "Concern inventory" below.
- `webui_app/helpers/url_meta.py`, `helpers/history.py`, `helpers/security.py`, `helpers/cli_runner.py` — Plan 007 reference implementations to mirror. Each is a flat module with a focused docstring header. None re-exports anything; `__init__.py` is the only re-export surface.
- `webui_app/helpers/__init__.py:49` — existing pattern: `from .security import _FLASK_PORT, _LOOPBACK_HOSTS, _TRUTHY_BYPASS, _ensure_csrf_token, _oauth_callback_uri  # noqa: E402`. Each new Unit appends a similar line.
- `webui_app/helpers/__init__.py:297` — `from .history import _push_history_aggregate  # noqa: E402` — same pattern, for symbols still referenced by `__init__.py` local code.
- `webui_app/helpers/__init__.py:756-779` — the `__all__` list. New names get added per Unit; the comment groupings remain.
- `tests/test_webui_helpers_subpackage_acyclic.py:18` — `KNOWN_MODULES = {"url_meta", "history", "security", "cli_runner"}` + `ALLOWED_EDGES` dict. Every new sub-module that imports from a sibling must declare the edge.
- `tests/test_webui_checkpoint.py:79,89` — `patch("webui_app.helpers._load_incomplete_run", ...)`. Package-level patch path must continue to resolve after the move.
- `tests/test_webui_helpers_medium_status.py:20` — `from webui_app.helpers import _get_medium_browser_status`. Same constraint.
- `tests/test_webui_image_gen.py:155` — `from webui_app.helpers import _image_gen_status`. Same constraint.

### Concern inventory (current state of `helpers/__init__.py`)

Verified by reading current `helpers/__init__.py` (779 LOC / 541 radon SLOC). Lines approximate within ±5 between commits — re-verify at implementation.

| Lines | SLOC | Concern | Target sub-module | Unit |
|------|------|---------|-------------------|------|
| 43–123 | ~80 | LLM settings file path + `_load_llm_settings` + `_image_gen_status` | `helpers/llm.py` | U1 |
| 125–306 | ~180 | `_get_blogger_token_status` + `_get_velog_status` | `helpers/channel_status.py` | U3 |
| 307–340 | ~33 | `_persist_three_tier_config` | `helpers/contexts.py` | U5 |
| 341–394 | ~50 | `_load_schedule_settings` + `_save_schedule_settings` + `_calc_next_available` | `helpers/schedule.py` | U2 |
| 395–429 | ~15 | `_load_incomplete_run` | folded into existing `helpers/history.py` | U2 |
| 430–491 | ~62 | `_get_medium_browser_status` | `helpers/channel_status.py` | U3 |
| 493–557 | ~65 | `_token_paste_status` + `_token_paste_status_notion` | `helpers/token_paste.py` | U4 |
| 558–706 | ~150 | `_settings_context` + `_draft_tab_extra` | `helpers/contexts.py` | U5 |
| 707–745 | ~38 | `_render` auto-inject | `helpers/render.py` | U5 |

After all 5 units land, only the following remain in `helpers/__init__.py`:
- module docstring
- stdlib / 3rd-party / project imports needed by `__all__`-listed re-imports
- `from .X import _Y` re-import lines (one per sub-module)
- the `__all__` declaration

Target: ≤120 SLOC.

### Institutional Learnings

- `[[publish-history-invariant-helper]]` — `_push_history_per_row` lives in `helpers/history.py` already (Plan 007 Unit 2). `_render` references `_history_store.load()` directly, not the invariant helper, because it's reading not writing — preserve this distinction across the move.
- `[[feedback-grep-all-legacy-import-forms]]` — 7 import forms + `mock.patch` string targets. Each Unit MUST grep all 7 forms across `webui_app/`, `webui.py`, and `tests/` before claiming "no callers break". Full `pytest tests/` is the only tripwire that catches relative-import miss.
- `[[feedback-dead-code-audit-blind-spots]]` — pyflakes won't see `mock.patch("webui_app.helpers._X")` references. Two known patch sites: `_load_incomplete_run` in `tests/test_webui_checkpoint.py:79,89`. Verify post-extract.
- `[[feedback-render-auto-inject-over-per-route]]` — `_render` is the canonical context-injection seam (PR #132 Unit 2 pattern). Moving it must not change the auto-inject contract; preserve the exact set of injected keys.
- `[[ce-work-must-check-concurrent-rebase-before-commit]]` — this plan touches a hot file (`helpers/__init__.py`) under active parallel work; each Unit's `ce:work` run must re-`git rev-parse HEAD` + `status --short` before each commit.
- `[[feedback-ce-work-must-audit-worktrees-first]]` — before starting Unit 1, run `git worktree list` + check if any `bp-*/` has touched `helpers/__init__.py`. Stash or rebase as needed.
- `[[memory-md-budget]]` — this plan's Memory index entry must stay ≤150 chars / one line.

### External References

Not used. All evidence is repo-internal. The master plan's audit (5 parallel reviewer agents, 2026-05-21) is the authoritative external grounding.

## Key Technical Decisions

- **Sequencing low-coupling first.** Order: U1 (`llm.py`, zero sibling deps) → U2 (`schedule.py` + fold `_load_incomplete_run`, zero sibling deps) → U3 (`channel_status.py`, zero sibling deps — grounded verification 2026-05-22 confirmed no `_TRUTHY_BYPASS` reference) → U4 (`token_paste.py`, zero sibling deps) → U5 (`contexts.py` + `render.py`, depends on ALL prior sub-modules). Rationale: U5 is the highest-coupling because `_settings_context` and `_render` consume everything else; landing it last means each prior Unit can ship without cascading re-imports.
- **One PR per sub-module.** Mirrors Plan 007 Units 1–4 cadence and PR #124 discipline. Bundling makes review impossible and a single regression blocks the whole win.
- **Re-import at `__init__.py`, not a top-level `helpers.py` shim.** Master plan called for "no transitional shim". The interpretation: don't create a deprecated module file path. Within-package re-imports at `helpers/__init__.py` are the existing pattern (Plan 007 already does it) and preserve `from webui_app.helpers import _X` callers without scattering imports across multiple files. External call-site migration is deferred — re-exports are stable forever.
- **Caller migration deferred to a follow-on housekeeping pass.** The original master plan said "split + migrate 14 call sites + delete from helpers.py" in one PR. After grounding: each Unit's call-site list spans 3–8 files including tests. Migrating callers within the extraction PR doubles the diff and increases revert risk. Better: ship 5 extraction PRs first (each tightly scoped to one sub-module + `__init__.py` + acyclic test), then a 6th caller-migration PR if the team still wants the public surface to "speak the sub-module path". The 6th is a separate plan or scoped follow-on; not required for the SLOC win or the maintainability goal.
- **Package-level re-exports are the canonical pattern, NOT a banned shim.** PR #124 deleted `_LegacyPathFinder` + `_REEXPORT_MAP` — a `sys.meta_path` finder that silently rewrote `backlink_publisher.<flat_name>` → canonical paths. That's the kind of "transitional shim" the master plan's "no shim" discipline targets. Plain `from .submodule import X` lines inside a package's `__init__.py` are the **endorsed** pattern (see `src/backlink_publisher/linkcheck/__init__.py:from .http import *` — explicitly preserved by PR #124 plan as "真包级 re-export，不是 meta-path bridge"). Plan 007 Units 1–4 already shipped this exact pattern (e.g., `webui_app/helpers/__init__.py:49` re-imports from `security`; `:297` re-imports from `history`). This plan continues that precedent verbatim.
- **U5 sibling vs. function-local imports.** Default choice: U5 sub-modules import siblings at module top (`from .llm import _image_gen_status`, etc.) so the acyclic test produces a clean DAG. Trade-off: U5 is gated on U1–U4 merging first. **Escape hatch:** if U2/U3/U4 stall >1 week, U5 can ship with function-local imports (`def _settings_context(...): from webui_app.helpers import _image_gen_status, ...`) — costs DAG clarity but unblocks the SLOC win. Picked default for cleanliness; documented fallback for risk mitigation.
- **Posture A: `helpers/__init__.py` is the permanent public API.** After this plan lands, `__init__.py` becomes ~120 SLOC of re-exports + `__all__` and stays that way. `__all__` is the contract; sub-module paths are internal-only and may move. Reason for picking Posture A over Posture B (transitional + scheduled deletion): the repo already follows Posture A for `src/backlink_publisher/linkcheck/__init__.py` and `src/backlink_publisher/_util/__init__.py`; introducing a second "must-delete-this-hub" abstraction creates more debt than it removes. Future agents reading `helpers/__init__.py` will see the `__all__` block + the comment markers above the re-import lines and understand: this file is the surface.
- **Posture A canonical-path rule (closes the two-style-import drift).** To prevent "either import style is fine" from rotting into half-of-each-style:
  - **External callers** (route modules under `webui_app/routes/`, `webui.py`, tests/) MUST import via the package surface: `from webui_app.helpers import _X`.
  - **Cross-sibling imports inside `webui_app/helpers/`** MUST use sub-module paths: `from .channel_status import _Y` (relative) or `from webui_app.helpers.channel_status import _Y` (absolute).
  - **New helpers added to a sub-module** stay sub-module-private (NOT added to `__all__`) by default. They graduate into `__all__` only when an external caller materializes — at which point the graduating PR adds the re-import line + the `__all__` entry together. The `__all__` set is therefore expected to grow slowly and only when an external caller needs it.
  - **Enforcement:** a follow-on micro-PR adds a grep guard like `rg 'from webui_app\.helpers\.[a-z_]+ import' webui.py webui_app/routes/ tests/` (excluding the helpers/ directory itself) → if hits, the PR must justify the sub-module-path import OR migrate to package-level. Out-of-scope for this plan; flagged so the next housekeeping PR can pick it up.
- **`_load_incomplete_run` folds into existing `helpers/history.py`** rather than its own module. Rationale: 15 SLOC, semantically about checkpoint-history, and reuses none of `schedule.py`'s timestamp logic. A new file for 15 SLOC fails the simplicity bar.
- **`_persist_three_tier_config` belongs with `_settings_context` in `contexts.py`** because both wrap the settings-page persistence/render pair. Putting it in `token_paste.py` was a tempting co-location (it persists token state), but the function shape is generic "save N tiers of config" not token-specific.
- **`_render` gets its own file `helpers/render.py`** (not folded into `contexts.py`). Rationale: `_render` is the auto-inject framework seam used by every route module; `_settings_context` is the settings-page-specific data assembler. Mixing them obscures the contract — `_render` injects 6 keys for any template; `_settings_context` builds 30+ keys for one template. Two responsibilities → two files.
- **No new tests for moved functions.** Existing tests (`test_webui_image_gen.py`, `test_webui_helpers_medium_status.py`, `test_webui_checkpoint.py`, `test_webui_platforms_context.py`, etc.) already cover behavior. Each Unit's test scenarios verify *the move is invisible* — pre-existing tests still pass, package-level imports still resolve, acyclic invariant test extends cleanly.

## Open Questions

### Resolved During Planning

- **Re-import strategy at `__init__.py`.** Resolved: append `from .X import _Y, _Z` lines per Unit + extend `__all__`; mirrors Plan 007 Units 1–4 verbatim.
- **Caller migration scope.** Resolved: defer; the SLOC win comes from extraction, not import-path rewrites. Re-exports are stable.
- **Where does `_load_incomplete_run` go?** Resolved: fold into existing `helpers/history.py`. Single concern, 15 SLOC, no need for a separate module.
- **Should `_render` live with `_settings_context`?** Resolved: no — separate `helpers/render.py`. Different responsibilities (auto-inject framework vs. settings page data assembler).
- **Does the acyclic test need new ALLOWED_EDGES?** Resolved: U1, U2, U3, U4 contribute **zero** new edges (grounded 2026-05-22: `_get_velog_status`/`_get_medium_browser_status` do NOT reference `_TRUTHY_BYPASS` or any other helpers/security symbol). U5 contributes 8 edges: `contexts.py` → llm/schedule/channel_status/token_paste/history (5), and `render.py` → history/schedule/channel_status (3). See U5 for exact list. Each Unit's PR declares its edges in `tests/test_webui_helpers_subpackage_acyclic.py:ALLOWED_EDGES`.

### Deferred to Implementation

- **Exact line ranges within each concern.** The "Concern inventory" table is accurate as of audit but `helpers/__init__.py` is a hot file; ±5 line drift between commits. Each Unit re-verifies with `grep -n "^def " webui_app/helpers/__init__.py` before cutting.
- **Whether `helpers/contexts.py` should further split `_settings_context`** (~150 SLOC) into smaller helpers. Out-of-scope for this plan; defer to master plan E-perf where the function's disk-I/O can also be addressed.
- **Whether to add a monolith_budget.toml ceiling on `helpers/__init__.py`** after the shrink. Master plan declared monolith budget out-of-scope; this stays deferred but is the natural next step to prevent regrowth.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification.*

```
webui_app/helpers/                         BEFORE (LOC/SLOC)   AFTER (LOC/SLOC)
├── __init__.py                            779 / 541    →      ≤180 / ≤120 (re-exports + __all__)
├── security.py        (existing)          ~177         unchanged
├── url_meta.py        (existing)          ~197         unchanged
├── history.py         (existing)          ~151         →      ~166  (+ _load_incomplete_run)
├── cli_runner.py      (existing)          ~123         unchanged
├── llm.py             NEW (U1)            —            →      ~80
├── schedule.py        NEW (U2)            —            →      ~50
├── channel_status.py  NEW (U3)            —            →      ~245
├── token_paste.py     NEW (U4)            —            →      ~65
├── contexts.py        NEW (U5)            —            →      ~185
└── render.py          NEW (U5)            —            →      ~38

Total project SLOC: ~541 → ~120 (hub) + ~683 (sub-modules) ≈ ~803. Fan-out, not shrink.

Dependency graph (within helpers/):
    llm.py          →  (no siblings)
    schedule.py     →  (no siblings)
    channel_status  →  (no siblings — verified 2026-05-22)
    token_paste.py  →  (no siblings)
    contexts.py     →  llm, schedule, channel_status, token_paste, history
    render.py       →  history, schedule, channel_status

External call-site contract (preserved by re-imports at __init__.py):
    from webui_app.helpers import _settings_context, _render, _get_medium_browser_status, ...
    →  still resolves, all names re-exported at package level
```

## Implementation Units

- [ ] **Unit 1: extract `helpers/llm.py`**

**Goal:** Move LLM-settings + image-gen status into a focused sub-module. Lowest-coupling extraction; serves as the template for U2–U5.

**Requirements:** R1, R4

**Dependencies:** None.

**Files:**
- Create: `webui_app/helpers/llm.py` (~80 SLOC; functions `_llm_settings_file`, `_load_llm_settings`, `_image_gen_status`)
- Modify: `webui_app/helpers/__init__.py` (delete the 3 functions; append `from .llm import _llm_settings_file, _load_llm_settings, _image_gen_status  # noqa: E402`; ensure `__all__` includes the 3 names with a `# llm → helpers/llm.py (Plan 2026-05-22-001 Unit 1)` group comment)
- Modify: `tests/test_webui_helpers_subpackage_acyclic.py` (add `"llm"` to `KNOWN_MODULES`)
- Test: `tests/test_webui_image_gen.py` — already covers `_image_gen_status` (line 155); no changes needed if package-level import resolves.

**Approach:**
- Read `helpers/__init__.py:43-123` to capture exact function bodies (re-verify line range with `grep -n "^def " webui_app/helpers/__init__.py`).
- Carry only the imports each function needs (`json`, `Path`, `_config_dir`, `load_config`); do NOT carry unused imports.
- Add module docstring: `"""LLM credential storage + image-gen status. Extracted from helpers/__init__.py — Plan 2026-05-22-001 Unit 1."""`
- Re-import order at `__init__.py`: place after the existing `from .security import ...` line, before the function definitions.

**Patterns to follow:**
- `webui_app/helpers/url_meta.py` (Plan 007 Unit 1) — flat module, focused docstring header, no sibling re-exports.
- `webui_app/helpers/cli_runner.py` (Plan 007 Unit 4) — same shape.

**Test scenarios:**
- Happy path: `pytest tests/test_webui_image_gen.py` passes unchanged (the `from webui_app.helpers import _image_gen_status` import at line 155 still resolves).
- Happy path: `pytest tests/test_webui_helpers_subpackage_acyclic.py::test_no_undocumented_sibling_imports` passes after `KNOWN_MODULES` extension (no new sibling edges introduced).
- Happy path: `python -c "from webui_app.helpers import _llm_settings_file, _load_llm_settings, _image_gen_status; print('ok')"` prints `ok`.
- Happy path: `python -c "from webui_app.helpers.llm import _llm_settings_file, _load_llm_settings, _image_gen_status; print('ok')"` prints `ok` (direct sub-module path also works).
- Integration: full `pytest tests/test_webui_*.py` passes — confirms no other test inadvertently broke.
- Integration: `python -m py_compile webui_app/helpers/*.py` exits 0.

**Verification:**
- `grep -n "^def _llm_settings_file\|^def _load_llm_settings\|^def _image_gen_status" webui_app/helpers/__init__.py` returns zero matches.
- `grep -n "^def _llm_settings_file\|^def _load_llm_settings\|^def _image_gen_status" webui_app/helpers/llm.py` returns 3 matches.
- `python -m radon raw -s webui_app/helpers/__init__.py` shows SLOC dropped by ~70-80.

---

- [ ] **Unit 2: extract `helpers/schedule.py` + fold `_load_incomplete_run` into `helpers/history.py`**

**Goal:** Move schedule-related helpers into a new module and absorb the stray `_load_incomplete_run` into the existing history sub-module (15 SLOC, doesn't earn its own file).

**Requirements:** R1, R4

**Dependencies:** None.

**Files:**
- Create: `webui_app/helpers/schedule.py` (~50 SLOC; functions `_load_schedule_settings`, `_save_schedule_settings`, `_calc_next_available`)
- Modify: `webui_app/helpers/history.py` (append `_load_incomplete_run` definition + its `_checkpoint_mod` import; add a small docstring note `# _load_incomplete_run merged here — Plan 2026-05-22-001 Unit 2`)
- Modify: `webui_app/helpers/__init__.py` (delete the 4 functions; append `from .schedule import _load_schedule_settings, _save_schedule_settings, _calc_next_available  # noqa: E402`; ensure `from .history import _load_incomplete_run` is added next to the existing `_push_history_aggregate` re-import; preserve `__all__`)
- Modify: `tests/test_webui_helpers_subpackage_acyclic.py` (add `"schedule"` to `KNOWN_MODULES`)
- Test: `tests/test_webui_checkpoint.py:51-89` — verify `_webui._load_incomplete_run()` direct call AND `patch("webui_app.helpers._load_incomplete_run", ...)` both still resolve.

**Approach:**
- `_calc_next_available` reads `_drafts_store` + `_history_store` directly (not via helpers siblings) — carry the imports.
- `_load_incomplete_run` reads `_checkpoint_mod.list_incomplete()` — carry the import into `helpers/history.py`.
- The `patch("webui_app.helpers._load_incomplete_run", ...)` mock targets in `tests/test_webui_checkpoint.py:79,89` keep working because the package-level re-import preserves the name on `webui_app.helpers`. Verify by running `pytest tests/test_webui_checkpoint.py -x` after the move.

**Patterns to follow:**
- `webui_app/helpers/history.py` (existing) — for the `_load_incomplete_run` append.
- `webui_app/helpers/llm.py` (just-created in U1) — for `schedule.py` shape.

**Test scenarios:**
- Happy path: `pytest tests/test_webui_checkpoint.py` passes unchanged. The 2 `mock.patch("webui_app.helpers._load_incomplete_run", ...)` patch points still resolve.
- Happy path: `python -c "from webui_app.helpers import _load_schedule_settings, _save_schedule_settings, _calc_next_available, _load_incomplete_run; print('ok')"` prints `ok`.
- Happy path: `python -c "from webui_app.helpers.history import _load_incomplete_run; from webui_app.helpers.schedule import _calc_next_available; print('ok')"` prints `ok` (both direct paths).
- Edge case: `_render` (still in `__init__.py` until U5) references `_load_incomplete_run` and `_calc_next_available` — confirm the re-imports preserve module-local name resolution by running `pytest tests/test_webui_platforms_context.py` (exercises `_render`).
- Integration: full `pytest tests/test_webui_*.py` passes.

**Verification:**
- `grep -n "^def _load_schedule_settings\|^def _save_schedule_settings\|^def _calc_next_available\|^def _load_incomplete_run" webui_app/helpers/__init__.py` returns zero.
- The new lines `from .schedule import ...` and `from .history import _load_incomplete_run` are present at `__init__.py`.
- Acyclic test passes with extended `KNOWN_MODULES`.

---

- [ ] **Unit 3: extract `helpers/channel_status.py`**

**Goal:** Move the 3 channel-status probe functions (blogger token, velog, medium browser) into a focused module. Largest extraction (~245 SLOC) and the only one likely to introduce a sibling edge to `security`.

**Requirements:** R1, R2, R4

**Dependencies:** None on prior units (the 3 functions don't depend on `llm.py` or `schedule.py`). U3 can run in parallel with U1/U2/U4 if desired, but ordering keeps PRs reviewable.

**Files:**
- Create: `webui_app/helpers/channel_status.py` (~245 SLOC; functions `_get_blogger_token_status`, `_get_velog_status`, `_get_medium_browser_status`)
- Modify: `webui_app/helpers/__init__.py` (delete the 3 functions; append `from .channel_status import _get_blogger_token_status, _get_velog_status, _get_medium_browser_status  # noqa: E402`; preserve `__all__`)
- Modify: `tests/test_webui_helpers_subpackage_acyclic.py` (add `"channel_status"` to `KNOWN_MODULES` only — no new ALLOWED_EDGES needed; grounded verification confirmed `_get_velog_status` and `_get_medium_browser_status` do NOT reference any `helpers/security.py` symbol)
- Test: `tests/test_webui_helpers_medium_status.py` — already covers `_get_medium_browser_status`; the `from webui_app.helpers import _get_medium_browser_status` at line 20 must continue to resolve.

**Approach:**
- Read `helpers/__init__.py:125-306` (blogger + velog) and `:430-491` (medium browser). Note: lines 307-340 (`_persist_three_tier_config`) sit BETWEEN these two ranges and are NOT part of U3 — leave them; U5 picks them up.
- Carry imports each function uses: `requests`, `bs4.BeautifulSoup`, `urllib.parse.urlparse`, `load_blogger_token`, `_config_dir`. Grounded verification (2026-05-22) confirmed neither function references `_TRUTHY_BYPASS` or any other `helpers/security.py` symbol — no sibling edge to declare.

**Patterns to follow:**
- `webui_app/helpers/url_meta.py` for module shape (it has `("url_meta", "security")` ALLOWED_EDGE for its own reasons; this module does NOT need that).

**Test scenarios:**
- Happy path: `pytest tests/test_webui_helpers_medium_status.py` passes unchanged (covers `_get_medium_browser_status` — filesystem-only probe; no network/Playwright; 8+ scenarios already in place).
- Happy path: `python -c "from webui_app.helpers import _get_blogger_token_status, _get_velog_status, _get_medium_browser_status; print('ok')"` prints `ok`.
- Happy path: `python -c "from webui_app.helpers.channel_status import _get_medium_browser_status; print('ok')"` prints `ok`.
- Edge case: the velog status probe reads `_TRUTHY_BYPASS` from security — verify the import resolves AND the acyclic test still passes after adding the edge.
- Edge case: `_settings_context` (still in `__init__.py` until U5) calls `_get_velog_status()` and `_get_medium_browser_status(...)` — confirm by running `pytest tests/test_settings_dashboard_rendering.py` (exercises `_settings_context`).
- Integration: full `pytest tests/test_webui_*.py` passes.

**Verification:**
- `grep -n "^def _get_blogger_token_status\|^def _get_velog_status\|^def _get_medium_browser_status" webui_app/helpers/__init__.py` returns zero.
- Acyclic test passes; if a sibling edge was added, it's declared in `ALLOWED_EDGES`.

---

- [ ] **Unit 4: extract `helpers/token_paste.py`**

**Goal:** Move the 2 token-paste status helpers into a focused module. Small extraction (~65 SLOC); no sibling edges.

**Requirements:** R1, R4

**Dependencies:** None.

**Files:**
- Create: `webui_app/helpers/token_paste.py` (~65 SLOC; functions `_token_paste_status`, `_token_paste_status_notion`)
- Modify: `webui_app/helpers/__init__.py` (delete the 2 functions; append `from .token_paste import _token_paste_status, _token_paste_status_notion  # noqa: E402`; preserve `__all__`)
- Modify: `tests/test_webui_helpers_subpackage_acyclic.py` (add `"token_paste"` to `KNOWN_MODULES`)
- Test: existing token-paste-status coverage lives in `tests/test_webui_platforms_context.py` and route-level token-paste tests — the package-level import must continue to resolve.

**Approach:**
- Read `helpers/__init__.py:493-557` for the 2 function bodies.
- `_token_paste_status` takes a `load_fn` callable parameter; `_token_paste_status_notion` reads `load_notion_token` directly. Carry only `_config_dir` from project imports.
- The `# noqa: F841` / unused-arg pattern (if any) survives literal copy.

**Patterns to follow:**
- `helpers/llm.py` (U1) — same shape, similar size class.

**Test scenarios:**
- Happy path: `python -c "from webui_app.helpers import _token_paste_status, _token_paste_status_notion; print('ok')"` prints `ok`.
- Happy path: `python -c "from webui_app.helpers.token_paste import _token_paste_status; print('ok')"` prints `ok`.
- Edge case: `_settings_context` calls `_token_paste_status(cfg, "ghpages", load_ghpages_token)` and 3 similar calls — confirm by running `pytest tests/test_webui_platforms_context.py`.
- Integration: full `pytest tests/test_webui_*.py` passes.

**Verification:**
- `grep -n "^def _token_paste_status\b\|^def _token_paste_status_notion" webui_app/helpers/__init__.py` returns zero.
- `grep -n "^def _token_paste_status\b" webui_app/helpers/token_paste.py` returns 1.

---

- [ ] **Unit 5: extract `helpers/contexts.py` + `helpers/render.py` (final shrink)**

**Goal:** Move the settings-page context builder, draft-tab extras, three-tier-config persistence, AND the `_render` auto-inject framework into 2 focused modules. After this Unit, `helpers/__init__.py` is ≤120 SLOC.

**Requirements:** R1, R2, R3, R4

**Dependencies:** Units 1, 2, 3, 4 must merge first — `_settings_context` calls `_image_gen_status` (U1), `_load_schedule_settings` (U2), `_get_velog_status` + `_get_medium_browser_status` (U3), `_token_paste_status` + `_token_paste_status_notion` (U4); `_render` calls `_get_blogger_token_status` (U3), `_calc_next_available` (U2), `_load_incomplete_run` (U2). Shipping U5 before any of these means contexts/render import from `helpers/__init__.py` which then imports back — circular at sibling level. Wait until all prior sub-modules exist.

**Workflow note:** Develop U5 in a worktree branched off the latest `main` AFTER U1–U4 have merged. If you must develop U5 in parallel (e.g., U4 in flight), stack U5's branch atop the most recent prior unit's branch, NOT off `main` — otherwise `from .llm import ...` lines fail import on the U5 base and CI is loud. If any of U1–U4 is reverted post-merge, U5 also becomes broken — track the revert risk like PR #109 (`[[feedback-grep-dofollow-map-before-shipping-adapter]]` is the precedent for this class of cascade revert).

**Files:**
- Create: `webui_app/helpers/contexts.py` (~185 SLOC; functions `_persist_three_tier_config`, `_settings_context`, `_draft_tab_extra`)
- Create: `webui_app/helpers/render.py` (~38 SLOC; function `_render`)
- Modify: `webui_app/helpers/__init__.py` (delete the 4 functions; append `from .contexts import _persist_three_tier_config, _settings_context, _draft_tab_extra  # noqa: E402` and `from .render import _render  # noqa: E402`; preserve `__all__`; verify final SLOC ≤120 via radon)
- Modify: `tests/test_webui_helpers_subpackage_acyclic.py` (add `"contexts"` and `"render"` to `KNOWN_MODULES`; add ALLOWED_EDGES `("contexts", "llm")`, `("contexts", "schedule")`, `("contexts", "channel_status")`, `("contexts", "token_paste")`, `("contexts", "history")` and `("render", "history")`, `("render", "schedule")`, `("render", "channel_status")` — re-verify exact edges from actual imports)

**Approach:**
- `_settings_context` (~150 SLOC) has many cross-sibling reads; import them with explicit `from .llm import _image_gen_status, _load_llm_settings`, `from .schedule import _load_schedule_settings`, `from .channel_status import _get_velog_status, _get_medium_browser_status`, `from .token_paste import _token_paste_status, _token_paste_status_notion`. Match the existing function body exactly. (No external tests `mock.patch("webui_app.helpers._settings_context")` at runtime against its internal calls, so module-top imports are safe here.)
- **`_render` uses package-namespace imports, NOT sibling imports — this is load-bearing for mock.patch resolution.** The existing tests at `tests/test_webui_checkpoint.py:79,89` do `patch("webui_app.helpers._load_incomplete_run", return_value=...)`. If `render.py` does `from .history import _load_incomplete_run` at module top, the name binds into `render.py`'s namespace AT IMPORT TIME — subsequent patches on `webui_app.helpers._load_incomplete_run` only rebind the package attribute, not the local binding inside `render.py`. The mock silently no-ops; the test still passes but for the wrong reason (it hits the real `_checkpoint_mod.list_incomplete()`). Same hazard applies to `_calc_next_available` and `_get_blogger_token_status`. **Required shape for `render.py`:**

  ```python
  # helpers/render.py — directional sketch
  from datetime import datetime, timedelta
  from flask import render_template
  from webui_store import (
      drafts_store as _drafts_store,
      history_store as _history_store,
      profiles_store as _profiles_store,
      queue_store as _queue_store,
  )
  from webui_app import helpers as _h  # package-namespace import; preserves mock.patch resolution

  def _render(template_name, **kwargs):
      if 'history' not in kwargs:
          kwargs['history'] = _history_store.load()
      if 'blogger_token_status' not in kwargs:
          kwargs['blogger_token_status'] = _h._get_blogger_token_status()
      # ... profiles, draft_queue, tasks ...
      if 'now_iso' not in kwargs:
          now = datetime.now()
          kwargs['now_iso'] = now.strftime('%Y-%m-%dT%H:%M')
          kwargs.setdefault(
              'suggested_next',
              _h._calc_next_available(now + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
          )
      if 'incomplete_run' not in kwargs:
          kwargs['incomplete_run'] = _h._load_incomplete_run()
      return render_template(template_name, **kwargs)
  ```

  The acyclic test (`tests/test_webui_helpers_subpackage_acyclic.py`) inspects `ast.ImportFrom` at top-level. The `import webui_app.helpers as _h` form (an `Import`, not `ImportFrom`) bypasses the sibling-edge detector — which is correct here: `render.py` depends on the *package surface*, not specific siblings. Document this in `ALLOWED_EDGES` as a comment ("render.py reaches via package namespace; no AST-detectable sibling edges") so reviewers know it's intentional, not a missed edge.

  **Stores** (`_history_store`, `_profiles_store`, `_drafts_store`, `_queue_store`) are NOT patched by name in current tests (grounded 2026-05-22), so they CAN be imported at module top from `webui_store` directly. Future-proof: if a test patches `webui_app.helpers._history_store`, that test will silently no-op post-U5 — call out in mock-patch grep protocol below.
- `_persist_three_tier_config` co-locates with `_settings_context` because both wrap settings-page state.
- `_draft_tab_extra` (15 SLOC, returns a single dict) co-locates because it shares the `_load_schedule_settings` import and the "settings/drafts UI context" concern.
- Final `helpers/__init__.py` body (after this Unit lands) is roughly:
  - module docstring
  - stdlib imports needed only by `__all__` exposure (probably none)
  - 5 `from .X import ...` re-import lines (one per sub-module, comma-listed names)
  - the `__all__` block, grouped by sub-module with `# X → helpers/X.py (Plan ...)` comments
- Verify `radon raw -s webui_app/helpers/__init__.py` reports SLOC ≤120; if it's higher, identify what slipped through and either move it or document why it stayed.

**Patterns to follow:**
- `[[feedback-render-auto-inject-over-per-route]]` — preserve auto-inject contract exactly.
- `webui_app/helpers/history.py` and `webui_app/helpers/cli_runner.py` (Plan 007) for module docstring style.

**Test scenarios:**
- Happy path: `pytest tests/test_webui_platforms_context.py` passes (exercises `_settings_context` end-to-end).
- Happy path: `pytest tests/test_settings_dashboard_rendering.py` passes (asserts `_settings_context.dashboard_channels` shape).
- Happy path: `pytest tests/test_webui_route_contract.py` passes (route-level contract; references `_settings_context` in the assertion at line 1315).
- Happy path: `python -c "from webui_app.helpers import _settings_context, _render, _persist_three_tier_config, _draft_tab_extra; print('ok')"` prints `ok`.
- Happy path: `python -c "from webui_app.helpers.render import _render; from webui_app.helpers import _render as r2; assert _render is r2"` — package re-export AND sub-module-path import resolve to the same function object (no double-import, no module-load-order surprise).

**`_render` contract test (new file `tests/test_webui_render_contract.py`).** The auto-inject contract is **8 distinct named injections** (verified against `helpers/__init__.py:719` docstring + body), NOT 6 or 7. Four scenario groups; a naive parametrized-per-key test would silently allow the coupling and mock-patch contracts to break:

  - **Scenario group A (caller-omits-all):** Caller invokes `_render('template.html')` with no kwargs. All 8 keys present in template context: `history`, `blogger_token_status`, `profiles`, `draft_queue`, `tasks`, `now_iso`, `suggested_next`, `incomplete_run`. Underlying stores/helpers mocked; assert presence + value-equals-mocked-return for each. Also assert `set(rendered_context.keys()) >= EXPECTED_INJECTED_KEYS` where `EXPECTED_INJECTED_KEYS = frozenset({...8 names...})` — set-equality forces a test update if anyone adds or drops an injection.
  - **Scenario group B (caller-overrides-each-individually):** For each of the 8 keys, one test case where caller passes that key explicitly. Assert caller's value wins; remaining 7 keys are still auto-injected. (8 parametrized cases.)
  - **Scenario group C (coupling cases — the load-bearing ones):**
    - `now_iso` provided, `suggested_next` omitted → `suggested_next` is **NOT** auto-injected (current `setdefault` semantics are gated on the `if 'now_iso' not in kwargs:` branch; injecting `suggested_next` would be a behavior change).
    - `now_iso` omitted, `suggested_next` provided → `now_iso` auto-injected, `suggested_next` value preserved (caller's wins via `setdefault`).
    - Both provided → both caller values preserved.
    - Neither provided → both auto-injected via the inner `setdefault` path.
    - `_queue_store.load()` raises `Exception` → `tasks` becomes `[]` (the try/except → [] contract). Other 7 keys still injected.
  - **Scenario group D (patch-resolution from package namespace — the post-U5 regression guard):** For each of `_load_incomplete_run`, `_calc_next_available`, `_get_blogger_token_status`:
    - `with patch("webui_app.helpers._X", return_value=sentinel): _render(...)` — assert `sentinel` appears in the rendered context under the corresponding key (`incomplete_run`, `suggested_next`, `blogger_token_status`).
    - This locks in the contract that `tests/test_webui_checkpoint.py:79,89` and any future package-level patch depends on. If `render.py` accidentally reverts to `from .history import _load_incomplete_run` (module-top sibling import), group D fails LOUDLY (sentinel doesn't reach the context).
- Edge case (singleton binding order, per `[[feedback-webui-store-config-dir-frozen]]`): a **subprocess test** (because Python's module cache prevents re-importing in the same process):
  ```python
  # tests/test_webui_render_contract.py
  def test_subprocess_sandbox_config_dir(tmp_path):
      import subprocess, sys
      r = subprocess.run(
          [sys.executable, "-c",
           "import webui_app.helpers.render; "
           "from webui_store import history_store; "
           "import sys; sys.exit(0 if str(history_store._path).startswith('/tmp/x') else 1)"],
          env={**os.environ, "BACKLINK_PUBLISHER_CONFIG_DIR": "/tmp/x"},
      )
      assert r.returncode == 0
  ```
  Captures the bug where sub-module-path imports might trigger singleton initialization earlier than the conftest fixture expects. (Same-process tests can't catch this because `webui_store` is already cached in `sys.modules` by conftest autouse fixtures.) **Optional:** if singleton initialization is confirmed lazy (no eager bind at sub-module import), this scenario can be dropped — verify by running `python -c "import webui_app.helpers.llm; import webui_store; print(webui_store.history_store._path)"` early in U1 and decide whether the U5 subprocess test adds value.
- Edge case (acyclic test extension): the 8 new ALLOWED_EDGES are declared; running the acyclic test post-merge passes; deliberately introducing an undeclared edge fails the test.
- Error path: `_settings_context` is called from a route handler when `cfg` is corrupt — confirm it still raises the same exception class it raised pre-extract. Manual reproduction in a dev WebUI is sufficient.
- Error path: `_render` is called with a non-existent template name — Flask raises `TemplateNotFound`. Confirm the exception class survives the move (not swallowed by a try/except in `render.py`).
- Integration: full `pytest tests/` passes (not just `test_webui_*`).
- Integration: `python webui.py` boots; navigate to `/`, `/settings`, `/history` — no 500s; auto-injected keys present in rendered templates (confirm via DevTools or template debug output).
- Integration: `python -m radon raw -s webui_app/helpers/__init__.py` reports SLOC ≤120 (the R4 acceptance gate).

**Verification:**
- `grep -n "^def _settings_context\|^def _render\|^def _persist_three_tier_config\|^def _draft_tab_extra" webui_app/helpers/__init__.py` returns zero.
- `wc -l webui_app/helpers/__init__.py` ≤180 (raw line count is a coarse check; radon SLOC is the real gate).
- All 8 new ALLOWED_EDGES (only from U5: 5 for `contexts` + 3 for `render`) are declared in `tests/test_webui_helpers_subpackage_acyclic.py`; acyclic test passes. (U1–U4 contribute zero edges.)
- The R4 acceptance: `python -m radon raw -s webui_app/helpers/__init__.py | grep SLOC` shows ≤120 (target) — ship-with-rationale at 121–140, redesign if >140.

## System-Wide Impact

- **Interaction graph:** Every route module that imports from `webui_app.helpers` is potentially affected, but the package-level re-import strategy preserves all `from webui_app.helpers import _X` call sites verbatim. Grounded census (2026-05-22): 14 test files + `webui.py` + 8 internal `webui_app/` modules import from `webui_app.helpers`; no `src/`, `scripts/`, or other CLI/util consumers. The only files that change behavior surface are the new sub-modules + `__init__.py` + the acyclic test. Tests that use `mock.patch("webui_app.helpers._X", ...)` continue to resolve because the patched names live on the package namespace as re-exports.
- **Error propagation:** No change. Each moved function's exception classes and propagation paths are preserved literally. The risk is a misplaced `try/except` during the copy — the test scenarios above (especially the U5 `_render` template-not-found case and the U5 `tasks → []` contract case) are the tripwires.
- **State lifecycle risks:** Singleton import order is the one non-trivial concern. `webui_store` exposes 5 module-level singletons whose paths are captured at first import (`[[feedback-webui-store-config-dir-frozen]]`). After U5, `render.py` and `contexts.py` both import these singletons; if a test imports `webui_app.helpers.render` directly BEFORE `webui_app.helpers`, the singleton-initialization order changes vs. the production code-path. Mitigation: the new singleton binding-order test in U5 covers this exact scenario. No mutation semantics change — `_persist_three_tier_config` still writes via `save_config`; `_load_incomplete_run` still reads from `_checkpoint_mod`; `_render` still reads stores in the same order.
- **API surface parity:** External callers see no change. The 14 importing test files + `webui.py` + 8 internal modules keep their import lines verbatim. If a follow-on housekeeping plan migrates callers to sub-module paths, that's separate.
- **Integration coverage:** `tests/test_webui_helpers_subpackage_acyclic.py` is the durable structural guard. **Scope caveat:** this test detects undeclared sibling **edges**, not import-time **cycles** — two siblings can declare edges to each other in `ALLOWED_EDGES` and a cycle ships undetected. U5's "Approach" enforces the cycle-free property by reading the actual graph (one-way: contexts/render → leaf siblings; leaves never re-import from contexts/render). A future-proof guard would topo-sort the declared graph; out-of-scope for this plan but flagged for the next pass.
- **Unchanged invariants explicitly:**
  - `_push_history_per_row`, `_apply_history_cap`, `_push_history_aggregate` stay in `helpers/history.py` (Plan 007 Unit 2). The publish-history invariant is untouched.
  - `_global_csrf_guard` (in `webui_app/__init__.py`, not `helpers/`) is untouched.
  - `safe_write.atomic_write` 0o600 (PR #140) — this plan does not touch any write path.
  - `HIDDEN_FROM_UI` (PR #136, in `webui_app/binding_status.py`) — untouched.
  - `_render` auto-inject contract — preserved exactly, with **8 distinct named injections** (`history`, `blogger_token_status`, `profiles`, `draft_queue`, `tasks`, `now_iso`, `suggested_next`, `incomplete_run`) locked in `tests/test_webui_render_contract.py` (U5). Four sub-shapes covered: (a) plain `if not in kwargs: kwargs[k] = ...` for 6 keys; (b) `now_iso`/`suggested_next` setdefault coupling (the inner setdefault is gated on the `now_iso` branch); (c) `tasks` try/except → `[]`; (d) patch-resolution from package namespace — `mock.patch("webui_app.helpers._X")` must reach `_render` post-U5 (else the `tests/test_webui_checkpoint.py:79,89` patch silently no-ops).
  - `__all__` set-equality: the post-U5 `__all__` block contains exactly the same names as the pre-plan one (no additions, no deletions; only the grouping comments change to reflect new sub-module ownership). Treat `__all__` content (not order) as a contract — a future linter could `assert set(__all__) == EXPECTED` against a frozen snapshot.
  - `# noqa: E402` discipline: each re-import line carries `# noqa: E402` because it follows the module docstring and standard imports. After U5 strips function bodies, if the re-imports become contiguous at top-of-file (no intervening function/class definition), the E402 noise can be removed in a separate follow-on. Within this plan: keep the noqa annotations conservatively to avoid churn.
  - Mock-patch grep protocol (`[[feedback-grep-all-legacy-import-forms]]`): every Unit runs all 7 import-form greps + `mock.patch` string-target grep against the symbols being moved, BEFORE merge. Grounded sweep (2026-05-22) found exactly 2 patch sites for `_load_incomplete_run` + 1 for `cli_runner.run_pipe` already at sub-module path — re-verify per Unit as the protocol is the discipline, not a one-time check.
  - **Mock-patch singleton case** (an extension of the protocol Unit 5 must run): `rg -n 'monkeypatch\.setattr\("webui_app\.helpers\._(history|drafts|queue|profiles|schedule)_store' tests/` AND `rg -n 'mock\.patch\("webui_app\.helpers\._(history|drafts|queue|profiles|schedule)_store' tests/`. Grounded sweep (2026-05-22) found ZERO hits, so U5 can ship store imports at module-top in `render.py`/`contexts.py`. If a future test adds such a patch BEFORE U5 lands, that test silently no-ops post-U5 → migrate the patch target to `webui_store.<name>_store` or to the new owning sub-module path BEFORE U5 merges.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `mock.patch("webui_app.helpers._load_incomplete_run", ...)` (2 sites in `test_webui_checkpoint.py`) silently mis-resolves after U2 fold | Re-import line `from .history import _load_incomplete_run` at `__init__.py` preserves the patch target while `_render` still lives in `__init__.py`. Confirmed by `pytest tests/test_webui_checkpoint.py -x` after U2 merge. |
| **U5 mock-patch regression (the real hazard)** — once `_render` moves to `render.py`, `from .history import _load_incomplete_run` at `render.py`'s module top binds the name into `render.py`'s namespace at import time; `patch("webui_app.helpers._load_incomplete_run", ...)` only rebinds the package attribute, NOT `render.py`'s local. The mock silently no-ops → test still passes but for the wrong reason. Same for `_calc_next_available`, `_get_blogger_token_status`. | `render.py` MUST use `import webui_app.helpers as _h` and reference via `_h._load_incomplete_run()` (see U5 Approach above). Scenario group D in `tests/test_webui_render_contract.py` locks this in: `with patch("webui_app.helpers._load_incomplete_run", ...): _render(...)` must reach the sentinel. |
| Concurrent edit on `helpers/__init__.py` from a `bp-*/` worktree drops or duplicates a function during the move | Per `[[ce-work-must-check-concurrent-rebase-before-commit]]`: re-`git rev-parse HEAD` + `status --short` before each commit; abort & rebase on detected drift. Worktree audit at start of each Unit per `[[feedback-ce-work-must-audit-worktrees-first]]`. |
| `_settings_context` loses one of its ~30 auto-built keys during the copy | Tests `test_webui_platforms_context.py` + `test_settings_dashboard_rendering.py` exercise key-by-key. Run both before merging U5. |
| `_render` auto-inject contract drift (e.g., the `kwargs.setdefault('suggested_next', ...)` semantics get rewritten as `kwargs['suggested_next'] = ...` mid-copy) | New `tests/test_webui_render_contract.py` (U5) has 4 scenario groups covering 8 named injections including the setdefault coupling and the patch-resolution contract. Catches the drift even when nothing crashes. |
| Sibling edge unintentionally introduced into a U1/U2/U3/U4 sub-module (e.g., `channel_status.py` accidentally imports from `token_paste.py`) | Acyclic test (`test_webui_helpers_subpackage_acyclic.py`) fails the build. ALLOWED_EDGES is opt-in — undeclared edges are violations. |
| Caller migration urge: someone tries to bundle "migrate the 14 call sites" into the same PR | Explicit Key Technical Decision says NO. Re-exports are stable; caller migration is a separate plan. Reviewers reject mixed-scope PRs. |
| `__init__.py` shrinks below 120 SLOC but a module-level constant or import slips into the wrong sub-module | The U5 acceptance gate runs `radon raw -s` AND a manual diff review confirms only re-imports remain. If extra constants stay in `__init__.py`, document why (e.g., `_HISTORY_MAX_ITEMS` if used by `__all__` exports — actually that's already in history.py per Unit 2b of master plan). |
| Plan 007 Unit 5+ exists in a competing draft elsewhere | Master plan references Plan 007 as `chore-merge-open-prs-plan.md` (already shipped Units 1-4). No separate Plan 007 Unit 5 draft was found. This plan IS the Unit 5+ work, renamed. |
| U5 blocked indefinitely if U2/U3/U4 stall (concurrent edits, unexpected test breakage) | Documented Key Technical Decision escape hatch: U5 can ship with function-local imports inside `_render`/`_settings_context` if siblings haven't merged. Trades DAG clarity for unblock. Threshold: invoke if any of U2/U3/U4 sits unmerged for >1 week with no clear path forward. |
| Acyclic test passes but an import-time cycle still ships (test detects edges, not cycles) | Until a topo-sort assertion lands, U5's "Approach" pins the one-way graph by design (contexts/render import only from leaf sub-modules; leaves never import contexts/render). Reviewer must visually confirm during U5 PR. A future small PR can promote the test to a true DAG check. |
| Worktree + editable-install cross-binding gives misleading pytest results | Per `[[per-worktree-venv-for-editable-install]]` + `[[pythonpath-src-for-sibling-worktree]]` + `[[feedback-ce-review-false-p0-from-sibling-worktree-no-pythonpath]]`: each Unit's `bp-*/` worktree must either (a) run `PYTHONPATH=src pytest tests/` to bypass the editable-install cross-binding, or (b) own its own `.venv` with `pip install -e ".[dev]"`. Without this, pytest reads the canonical `backlink-publisher/src/` and the Unit's source edits appear invisible — exactly the false-P0 case from PR #124's review. |
| Eager singleton initialization in a U1–U4 sub-module trips the conftest sandbox before U5's test exists to catch it | U1's first verification: run `python -c "import webui_app.helpers.llm; import webui_store; print(webui_store.history_store._path)"` in a sandboxed `BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/x`. If the path is `/tmp/x/...`, the singleton initialization is lazy and safe — drop the U5 subprocess test as redundant. If the path is `Path.home()/.config/...`, the early-import hazard is real; fold the U5 subprocess test forward into U1's regression suite. |

## Documentation / Operational Notes

- `CLAUDE.md` (project) currently says: "Flask app under `webui_app/` (20 route modules + `create_app()` factory)" — no helpers/__init__.py size claim, no need to update.
- After U5 merges, the next master-plan audit should refresh the F2 finding ("helpers.py 1252 SLOC god module") to "RESOLVED — helpers/__init__.py now ≤120 SLOC, 9 focused sub-modules under helpers/".
- Consider (but not in scope here): adding `[files."src/backlink_publisher/webui_app/helpers/__init__.py"]` (or wherever helpers/ ends up) to `monolith_budget.toml` with ceiling=130 to lock in the win.
- No rollout, monitoring, or migration concerns. WebUI behavior unchanged.

## Sources & References

- **Origin document:** [docs/plans/2026-05-21-006-refactor-webui-comprehensive-optimization-plan.md](2026-05-21-006-refactor-webui-comprehensive-optimization-plan.md) — Plan A section (lines 441-447).
- **Plan 007 sequencing template:** [docs/plans/2026-05-21-007-chore-merge-open-prs-plan.md](2026-05-21-007-chore-merge-open-prs-plan.md) — Units 1-4 (url_meta/history/security/cli_runner).
- **Acyclic invariant test:** `tests/test_webui_helpers_subpackage_acyclic.py` (Plan 2026-05-21-007 artifact).
- **Existing sub-modules:** `webui_app/helpers/url_meta.py`, `helpers/history.py`, `helpers/security.py`, `helpers/cli_runner.py`.
- **Audit cutoff for this plan:** worktree `backlink-publisher/` as of 2026-05-22; `helpers/__init__.py` SLOC = 779.
- **MEMORY learnings referenced:** `[[publish-history-invariant-helper]]`, `[[feedback-grep-all-legacy-import-forms]]`, `[[feedback-dead-code-audit-blind-spots]]`, `[[feedback-render-auto-inject-over-per-route]]`, `[[ce-work-must-check-concurrent-rebase-before-commit]]`, `[[feedback-ce-work-must-audit-worktrees-first]]`, `[[memory-md-budget]]`.
