---
title: "feat: Mandatory pre-publish linkcheck + language_check gate"
type: feat
status: completed
date: 2026-05-14
deepened: 2026-05-14
completed: 2026-05-14
origin: backlink-publisher/docs/brainstorms/2026-05-14-mandatory-linkcheck-lang-gate-requirements.md
---

# feat: Mandatory pre-publish linkcheck + language_check gate

## Overview

Today `validate-backlinks` imports `linkcheck.py` and `language_check.py` but the resulting quality gate is partially broken:

1. **`language_matches` always returns `True`** (every branch of `language_check.py:57-71` falls through to `return True`). The warning emitted in `_enhance_payload` is unreachable — language-mismatched articles publish silently.
2. **No publish-time reachability re-check** — `linkcheck.check_urls_strict` only runs at validate-time. A target URL reachable hours ago can be 404 at publish-time and the pipeline publishes a dead-link backlink.
3. **Anchor text is never language-checked** — only `content_markdown` is. zh-CN articles with English anchors pass silently.

This plan makes the gate actually fail, adds a publish-time re-check with per-row skip semantics, adds per-anchor language verification (scoped to primary backlinks only), splits the escape hatch into two flags so the publish-time bypass is opt-in rather than free-riding on validate-time convenience, and hard re-validates the checkpoint on `--resume` to clear retroactively-corrupted rows.

## Problem Frame

See origin: `backlink-publisher/docs/brainstorms/2026-05-14-mandatory-linkcheck-lang-gate-requirements.md`.

Backlinks pointing to dead/redirected URLs are the lowest-bar quality failure and the most embarrassing to the operator. Language-mismatched anchors are the documented `feedback_llm-free-pool-sizing` failure mode that the work-themed generator's pool sizing helped mitigate but did not eliminate. The bug in `language_matches` has been live for an unknown period — historical `pending`/`failed` checkpoint rows may carry stale `validation.warnings` that under the new contract are `validation.errors`.

## Requirements Trace

- R1 — Fix `language_check.language_matches` to return `False` on known-language mismatch; preserve `unknown→True`. (Unit 1)
- R2 — `validate-backlinks` row-level abort on body language mismatch; row not forwarded; batch exits non-zero at end. (Unit 3)
- R3 — `"unknown"` continues to pass; `row.language` outside enum skips R2/R4 with WARN. (Unit 1, Unit 3)
- R4 — Anchor codepoint check using BMP-only ranges, scoped to `link.kind ∈ {"main_domain", "target"}`; `branded_pool` membership additionally exempts. (Unit 2, Unit 3)
- R5 — R4 failures cause row-level abort (same shape as R2). (Unit 3)
- R6 — Existing validate-time `check_urls_strict` whole-batch abort preserved unchanged. (No unit — no-op verification.)
- R7 — Existing `--no-check-urls` flag preserved as deprecated alias for the renamed `--no-validate-url-check` (validate-time only). (Unit 6.)
- R8 — Publish-time per-row reachability re-check immediately before `adapter.publish()` call. (Unit 4, Unit 5)
- R9 — Per-URL retry via `linkcheck._check_url_with_retry` (no wrapping retry layer); any URL exhaustion → whole-row skip; checkpoint item stays `pending` so `--resume` retries. (Unit 5)
- R10 — Two independent, symmetrically-named flags: rename `validate-backlinks --no-check-urls` to `--no-validate-url-check` (old name kept as deprecated alias with WARN); add new `publish-backlinks --skip-publish-time-check` (default off, persisted in checkpoint metadata). Neither flag disables language checks. (Unit 6)
- R11 — `validation.errors[]` array + `validation.status="failed"`; failed rows not written to stdout; completion log reports failure count. (Unit 3)
- R12 — Structured WARN line per skipped row + simple aggregate completion log (count only; per-row WARN already names the failing URL). (Unit 5)
- R13 — `publish-backlinks --resume` hard re-validates R2/R5 over `pending` + `failed` items; reclassifies failing items as `failed` with `error_class = "retro_language_failed"` / `"retro_anchor_failed"`. (Unit 7)

Cross-cutting:
- All 999 tests pass post-change, or updated with rationale comment naming this plan. (Unit 8 — test sweep)

## Scope Boundaries

- **Out**: canonical-URL consistency check (`<link rel=canonical>`). Origin §Scope Boundaries.
- **Out**: final URL == target URL after redirect-chain following.
- **Out**: hard-removing `--no-check-urls` (kept as deprecated alias for v0.2; targeted for removal in v0.3 per Unit 6 WARN message).
- **Out**: replacing keyword-based `detect_language` with a dependency (`langdetect`, `lingua`).
- **Out**: any LLM-driven anchor/body checking (per `feedback_no-runtime-llm` hard constraint).
- **Out**: webui banner/UI surface for new `validation.errors` — listed in System-Wide Impact as a downstream change for a follow-up plan, not for this PR.
- **Out**: rewriting checkpoint state machine. New `error_class` values land on top of existing `failed` status; no new status enum.
- **Out**: extending `LINK_KINDS` to add `"branded"`. R4 exemption uses runtime config lookup, not schema change.

## Context & Research

### Relevant Code and Patterns

- **`src/backlink_publisher/language_check.py:57-71`** — bug site for R1. Heuristic keyword-based detector. 70 lines total.
- **`src/backlink_publisher/cli/validate_backlinks.py:20-38`** (`_enhance_payload`) — only place where `validation` block is built; natural slot for R2/R5. Currently writes `status: "passed"` unconditionally.
- **`src/backlink_publisher/cli/validate_backlinks.py:101-121`** — accumulator pattern: collect every row's errors, then `SystemExit(2)` if `all_errors`. R2/R5 should follow this same pattern (don't bail on first row).
- **`src/backlink_publisher/cli/validate_backlinks.py:54-59`** — `--no-check-urls` flag definition (template for new `--skip-publish-time-check`).
- **`src/backlink_publisher/linkcheck.py:59-76`** (`_check_url_with_retry`) — 3 attempts with progressive 1s + 2s delays against `ACCEPTABLE_CODES = {200, 301, 302}`. Worst-case ~3s per dead URL.
- **`src/backlink_publisher/linkcheck.py:93-109`** (`check_urls_strict`) — aborts on first failure; incompatible with per-row continue semantics. R8 must NOT call this. Need an additive per-URL public wrapper (Unit 4).
- **`src/backlink_publisher/cli/publish_backlinks.py:498-504`** — exact adapter dispatch call site; new R8 check fires immediately before this.
- **`src/backlink_publisher/cli/publish_backlinks.py:74-238`** (`_run_resume`) — re-runs checkpoint items in `("pending", "failed")` status (line 98). R13 hard re-validation inserts between `load_checkpoint` (line 82) and the `to_process` filter.
- **`src/backlink_publisher/cli/publish_backlinks.py:313-318`** — `--no-verify` flag definition (template for new `--skip-publish-time-check`).
- **`src/backlink_publisher/checkpoint.py:50-90`** (`create_checkpoint`) — top-level keys: `run_id, started_at, platform, mode, status, items`. **No flag persistence today** — Unit 6 adds a `flags` dict here.
- **`src/backlink_publisher/checkpoint.py:65-77`** — per-item shape: `id, status, title, platform, adapter, published_url, error, error_class, completed_at, payload`. R13 reuses `error_class` (no new enum).
- **`src/backlink_publisher/checkpoint.py:102`** (`_OPTIONAL_ITEM_FIELDS`) — if Unit 5/6/7 add per-item fields, they need to be appended here or `update_item` will leak them.
- **`src/backlink_publisher/schema.py:48`** — `LINK_KINDS = {"main_domain", "target", "supporting", "extra", "category", "detail"}`. `"branded"` is NOT a kind today — confirmed via grep. R4 exemption must NOT depend on `kind == "branded"`.
- **`src/backlink_publisher/schema.py:125-127`** — link required fields: `("url", "anchor", "kind", "required")`. `link.anchor` is a flat string (never asserted as `str` in schema, but all plan-time builders write flat strings — confirmed at `plan_backlinks.py:173, 184, 205, 215, 223, 230, 252, 629, 632`).
- **`src/backlink_publisher/anchor_resolver.py:836`** — `anchor_type="branded"` flows into `ProfileEntry`, not the published link payload. Branded membership is config-state, not row-state.
- **`src/backlink_publisher/config.py:get_anchor_pool_v2`** — reachable from validate-time (`get_anchor_pool_v2(config, main_domain).get("home", {}).get("branded", [])`). Validate-backlinks does NOT load Config today — this plan adds that import edge.
- **`tests/test_validate_backlinks.py:80, 187`** — confirmed `validation.status == "passed"` assertion sites. Fixture uses 6 links: 1 main + 1 target + 4 supporting (all en, all ASCII anchors). Under Unit 3, fixture continues to pass because R4 only checks main/target.
- **`tests/test_validate_backlinks.py:189`** — `isinstance(output["validation"]["warnings"], list)` — drives R11 decision to keep `validation.warnings` as an empty list.

### Institutional Learnings

From `backlink-publisher/docs/solutions/`:

- **`test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md`** — module-level imports must be patched at the **consumer's reference**. Unit 5 imports a linkcheck helper into `publish_backlinks.py`; tests must patch `backlink_publisher.cli.publish_backlinks.<name>`, not the source module. `time.sleep` must be mocked in batch tests. Use `pytest-timeout=30` on new tests since permissive→strict can cause silent hangs in retry loops.
- **`ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md`** — operations >5s require the existing loading overlay; webui will eventually need updated copy when publish-time linkcheck slows `/ce:publish-real`. Surface `validation.errors` as actionable rows, not a generic red block. Listed in System-Wide Impact as a follow-up.

From global MEMORY:

- **`feedback_test-autouse-verify-mock`** — adding HTTP behavior to publish required autouse mock fixtures across all publish test files. Unit 5 reuses this pattern.
- **`feedback_python-mock-datetime-patterns`** — function-level import mock paths; relevant when patching `linkcheck` callers.
- **`feedback_macos-adapter-test-isolation`** — Brave throttle sleep must be mocked; Unit 5/6 tests inherit this.
- **`feedback_config-save-overwrite-pattern`** — when extending `checkpoint.json` shape (Unit 6's `flags` key), read-mutate-write the full dict — never overwrite shape-aware regions blindly.
- **`feedback_no-runtime-llm`** — hard constraint; this plan does not introduce any LLM code path.
- **`feedback_llm-free-pool-sizing`** — branded_pool is the canonical source-of-truth for branded anchor identification, supporting Unit 3's runtime-lookup strategy.

### External References

None — work is plumbing changes to existing modules with established patterns.

## Key Technical Decisions

- **R4 exemption uses `link.kind ∈ {"main_domain", "target"}` AND branded_pool membership, NOT a new `kind="branded"`.** Reason: `LINK_KINDS` schema doesn't include `"branded"`; brainstorm's preferred mechanism resolves to runtime config lookup. Scoping to primary backlinks also avoids accidentally rejecting legitimate ASCII auxiliary citations (Wiki, MDN, GitHub) in zh-CN articles. The branded_pool lookup is the explicit user-required carve-out; the kind scoping is the natural narrowing that the user's intent ("zh-CN article using English brand names as backlink anchors") already required.
- **R4 anchor check lives in a new pure module `src/backlink_publisher/anchor_lang.py`.** Reason: `validate_backlinks.py` is already 123 lines doing CLI parsing + orchestration; adding 30+ lines of codepoint logic inline mixes concerns and bloats the file. Pure module is independently testable, no config dependency in itself (the config-loaded branded_pool list is passed in by the caller), and naturally extends with new languages later.
- **Branded_pool lookup is payload-first with config fallback.** `plan-backlinks` emits `row['metadata']['branded_pool']: list[str]` as a snapshot at plan time (closes the validate→publish TOCTOU window). `validate-backlinks` reads from there preferentially; if `metadata.branded_pool` is absent (older JSONL produced before this PR), falls back to `load_config()` + `get_anchor_pool_v2(...)`. This eliminates the new hard config dep for fresh-clone / CI workflows that pipe pre-built JSONL, and pins the branded set at validate-time so the operator can't accidentally invalidate a row by editing the config between commands. The fallback path preserves the original brainstorm intent.
- **R9 reuses `linkcheck._check_url_with_retry` directly; no wrapping retry layer.** Reason: brainstorm decision (closed during document-review). The helper's existing 3-attempt schedule (1s + 2s) is acceptable per-row latency. Wrapping would duplicate Plan 2026-05-12-002's exponential-backoff layer.
- **Unit 4 publishes a public additive wrapper `linkcheck.check_url(url) -> tuple[bool, str | None]` instead of editing `check_urls_strict`.** Reason: R6 mandates `check_urls_strict` preserved unchanged. New public function calls existing private `_check_url_with_retry` and returns a tuple instead of raising. Cheapest, smallest blast radius.
- **R13 stores skip reasons in existing `error_class` field on checkpoint items**, not a new status enum. Values: `"retro_language_failed"`, `"retro_anchor_failed"`. Reason: keeps the `pending/failed/done` state machine unchanged; `--list-runs` already reads `error_class` for display.
- **Publish-time skipped rows leave checkpoint item in `pending`, not `failed`.** Reason: brainstorm decision (closed during document-review). `--resume` re-attempts when upstream may have recovered. The corresponding output JSONL row carries `status: "skipped_unreachable"` for downstream visibility.
- **Flag persistence lives in a new top-level `flags` key on the checkpoint** (`checkpoint.create_checkpoint(..., flags={"skip_publish_time_check": False})`). Reason: per-item flags would multiply state across N rows for a single CLI-level decision; one top-level dict is the simplest expressive form. `_run_resume` reads `ckpt.get("flags", {})` with a default to handle pre-flags checkpoints from before this PR.
- **Single PR shipping R1–R13.** Reason: brainstorm decision (closed during document-review). R1 alone changes pass/fail behavior of `language_matches` and would flip tests; bundling with the gate work means one test sweep instead of two.

## Open Questions

### Resolved During Brainstorm Review (Confirmed in Planning)

- **Branded exemption mechanism** (origin §Deferred to Planning) — Resolved: runtime `branded_pool` membership lookup via `get_anchor_pool_v2(config, row['main_domain'], 'home', 'branded')`. The `link.kind="branded"` path is closed because `LINK_KINDS` schema does not include it and retrofitting plan_backlinks emission has wider blast radius.
- **R4 anchor scope** (discovered during planning) — Resolved: R4 only checks anchors on links where `kind ∈ {"main_domain", "target"}`. Supporting/extra/category/detail anchors are exempt from codepoint check by virtue of their kind (consistent with the project intent — primary backlink language must match; auxiliary citation language is independent).
- **`validation.warnings` deprecation** (origin §Deferred) — Resolved for v1: keep field as empty list. `test_validate_backlinks.py:189` asserts `isinstance(warnings, list)`; removing requires test update. Defer hard-remove to a follow-up plan if downstream consumers don't surface.
- **Where R8 fires in publish loop** (origin ambiguity) — Resolved: per-row, **inside** the per-row loop (`publish_backlinks.py` main path around line 498, just before `adapter_publish`). Not the batch pre-flight loop at line 382. Reason: per-row granularity is required for R9's skip-and-continue semantic; batch pre-flight would force whole-batch decisions.
- **Exit code for R2/R5 failure** — Resolved: `SystemExit(2)` (existing schema-validation code), preserving the contract that "exit 2 = invalid payload, exit 4 = URL unreachable" stays distinguishable.

### Deferred to Implementation

- **Whether `_enhance_payload` should detect `row.language == "unknown"` before applying R2/R4, or only when `detect_language(content_markdown)` returns `"unknown"`** — implementation-time concern; the cleanest place is a single `if row['language'] not in {"zh-CN", "ru", "en"}: emit warn + return early` guard at the top of `_enhance_payload`. Decide at code time.
- **Whether to mock at module level (`linkcheck.check_url`) or function-level (`publish_backlinks.check_url`) for Unit 5 tests** — per `feedback_python-mock-datetime-patterns` + the test-failures learning, function-level (consumer reference) is the proven pattern. Confirm by running one test before scaling.
- **Test fixture additions for zh-CN + ru paths** — current `test_validate_backlinks.py` is en-only. Implementer adds `_make_valid_payload(language="zh-CN", body=..., anchors=...)` helper at code time.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
validate-backlinks flow                          publish-backlinks flow (per row)
──────────────────────                           ────────────────────────────────
                                                                │
  read JSONL ─→ schema_validate                                 ▼
                       │                          checkpoint item.status ∈ {pending,failed}?
                       ▼                                        │
              for each row:                                     ▼
                _enhance_payload(row, config)         ┌─── if --resume ─────────────┐
                  │                                  │   R13: re-run R2/R5 over     │
                  ├─ if row.language ∉ enum:         │   pending+failed items;       │
                  │    WARN, set status=passed       │   reclassify failing items    │
                  │    return                       │   to failed/error_class=...   │
                  │                                  └──────────────┬───────────────┘
                  ├─ R2: detect_language(body)                      │
                  │   ≠ row.language (known)?       ┌───────────────┘
                  │   → status=failed,              │
                  │     errors.append("...")        ▼
                  │                          for each item:
                  ├─ R4 (for kind ∈ {main_domain,    │
                  │    target}):                    ▼
                  │    if anchor in                 if not args.skip_publish_time_check:
                  │       branded_pool: exempt        for url in [target_url, *row.links[*].url]:
                  │    elif not                        ok, err = linkcheck.check_url(url)
                  │       codepoint_match(            if not ok:
                  │       anchor, row.lang):           emit WARN row_id=… status=skipped_unreachable url=…
                  │   → status=failed,                 write JSONL { status: "skipped_unreachable", …}
                  │     errors.append("...")           checkpoint stays "pending"
                  │                                    skip remainder of row, continue batch
                  └─ return row                       break
                                                   adapter.publish(row, …)
              accumulate all_errors                  → checkpoint → "done" or "failed"
              if all_errors: SystemExit(2)
              else: write_jsonl(outputs)         after loop: emit aggregate completion log
                                                  (skipped_unreachable count + distinct hosts)
```

Key boundaries this design preserves:
- `validate-backlinks` is pure config + row-level transformation; no network calls beyond the existing `check_urls_strict` batch step.
- `publish-backlinks` per-row dispatch order: re-check → publish → checkpoint update. Re-check sits in the same logical "pre-flight" tier as the existing `validate_publish_payload` call but at row granularity.
- `linkcheck.py` gains one additive public function; internal `_check_url_with_retry` unchanged.
- `language_check.py` gets a 1-line bug fix; the keyword-list scorer is unchanged.

## Implementation Units

- [x] **Unit 1: Fix `language_matches` bug + `unknown`/non-enum handling for `row.language`**

**Goal:** Make `language_matches` return `False` when `detected` is a known language ≠ `requested`. Preserve `unknown→True`. Add safe handling for `row.language` outside the supported enum (skip gating with WARN).

**Requirements:** R1, R3 (partial).

**Dependencies:** None — foundational.

**Files:**
- Modify: `src/backlink_publisher/language_check.py`
- Test: `tests/test_language_check.py` (new file — currently there's no dedicated test)

**Approach:**
- Replace lines 57-71 with branches that return `False` for `detected != requested` when both are in `{"zh-CN", "ru", "en"}`, and `True` for the `"unknown"` cases. The "Allow some flexibility" branch is the entire bug — fix the fall-through.
- Add a `SUPPORTED_LANGUAGES = frozenset({"zh-CN", "ru", "en"})` constant exposed at module top — Unit 3 reuses it for the row.language enum guard.

**Execution note:** Characterization-first. Write tests pinning the current always-True behavior (`assert language_matches("en", "zh-CN") is True` etc.) and run them green first, then flip to the desired contract by updating the assertions and the code in the same edit. This proves the bug exists and that the fix is the intended difference.

**Patterns to follow:**
- Module-level constants pattern: see `linkcheck.py:13-19` (`REQUEST_TIMEOUT`, `MAX_CONCURRENT`, `ACCEPTABLE_CODES`).

**Test scenarios:**
- Happy path: `language_matches("en", "en")` → `True`; `language_matches("zh-CN", "zh-CN")` → `True`; `language_matches("ru", "ru")` → `True`.
- Happy path (the bug): `language_matches("en", "zh-CN")` → `False` (today: `True`). All 6 cross-pairs of the 3 enum values should return `False`.
- Edge case: `language_matches("unknown", "en")` → `True`; `language_matches("unknown", "zh-CN")` → `True`; `language_matches("unknown", "ru")` → `True` (preserved).
- Edge case: `language_matches("en", "unknown")` → reasoned behavior; suggested `True` (caller's `requested` is unknown, can't disprove either way) but document the choice in a code comment so reviewers don't flag it.
- `SUPPORTED_LANGUAGES` exposed and contains exactly `{"zh-CN", "ru", "en"}`.

**Verification:**
- `pytest tests/test_language_check.py -v` runs the full new suite green.
- The "Allow some flexibility" comment is removed (or replaced with rationale for the new branch logic) so the next reader doesn't re-introduce the same bug.

---

- [x] **Unit 2: New `anchor_lang.py` module — codepoint heuristic + exemption helper**

**Goal:** Pure-function helper that given `(anchor: str, row_language: str, link_kind: str, branded_pool: list[str])` returns `(ok: bool, reason: str | None)`. Encapsulates the BMP-only CJK / Cyrillic / Latin-strict rules from R4 plus the kind-scoping and branded-pool exemption.

**Requirements:** R4 (helper portion).

**Dependencies:** Unit 1 (uses `SUPPORTED_LANGUAGES`).

**Files:**
- Create: `src/backlink_publisher/anchor_lang.py`
- Test: `tests/test_anchor_lang.py`

**Approach:**
- Public function signature (directional, not literal): `check_anchor_language(anchor, row_language, link_kind, branded_pool) -> tuple[bool, str | None]`.
- Exemption order: if `link_kind not in {"main_domain", "target"}` → exempt (returns `True, None`). Else if `anchor in branded_pool` → exempt (returns `True, None`). Else apply the codepoint rule per language.
- Codepoint helpers: `_has_cjk(text) -> bool` checks `U+4E00..U+9FFF` only; `_has_cyrillic(text) -> bool` checks `U+0400..U+04FF`; `_has_latin_letter(text) -> bool` checks `[A-Za-z]`. Reject rule for `en`: `_has_latin_letter` AND NOT `_has_cjk` AND NOT `_has_cyrillic`.
- For `row_language` outside `SUPPORTED_LANGUAGES`, return `True, None` with no codepoint check (consistent with R3 enum guard).
- Reason string is a short tag like `"anchor missing CJK codepoint"` so callers (Unit 3) can compose error messages without re-implementing the heuristic.

**Patterns to follow:**
- Pure-function module pattern: `markdown_utils.py`, `url_utils.py` — module-level constants + small helpers + a single public entry, no class.

**Test scenarios:**
- Happy path: zh-CN, kind="main_domain", anchor="苹果官网", branded_pool=[] → `(True, None)`.
- Happy path: zh-CN, kind="main_domain", anchor="Apple", branded_pool=["Apple"] → `(True, None)`. Reason: branded-pool exempts.
- Error path: zh-CN, kind="main_domain", anchor="Apple", branded_pool=[] → `(False, "anchor missing CJK codepoint")`. Reason: codepoint rule fires.
- Error path: zh-CN, kind="main_domain", anchor="learn more", branded_pool=[] → `(False, "...")`.
- Edge case (kind exemption): zh-CN, kind="supporting", anchor="MDN", branded_pool=[] → `(True, None)`. Reason: supporting kinds are exempt regardless of language.
- Edge case (kind exemption): zh-CN, kind="extra", anchor="github.com", branded_pool=[] → `(True, None)`.
- Error path: en, kind="main_domain", anchor="在线 Apple 体验店" (mixed-script), branded_pool=[] → `(False, "...")`. Strict-en rejects mixed scripts.
- Happy path: en, kind="main_domain", anchor="Apple Store", branded_pool=[] → `(True, None)`.
- Edge case: en, kind="main_domain", anchor="" (empty), branded_pool=[] → `(False, ...)`. Empty anchor is a separate failure mode but should not crash.
- Edge case (Unicode general punctuation): en, anchor="Apple — Inc.", branded_pool=[] → `(True, None)`. Em-dash allowed.
- Edge case (non-enum language): row_language="ja", kind="main_domain", anchor="Tokyo", branded_pool=[] → `(True, None)`. R3 contract.
- Edge case (BMP boundary): zh-CN, anchor="𠀀" (U+20000, CJK Extension B beyond BMP), branded_pool=[] → `(False, ...)`. BMP-only rule.

**Verification:**
- `pytest tests/test_anchor_lang.py -v` passes the full table.
- Coverage report shows 100% branch coverage on `anchor_lang.py` (the module is pure logic, should be reachable).

---

- [x] **Unit 3: Wire R2 + R4/R5 + R11 into `validate-backlinks._enhance_payload`**

**Goal:** Replace the always-passing `_enhance_payload` with row-level fail logic for language mismatch (R2) and anchor codepoint failures (R4/R5). Populate `validation.errors[]` and set `status="failed"` (R11). Preserve `validation.warnings` as empty list for back-compat. Add config-load at CLI startup so the branded_pool is available per row.

**Requirements:** R2, R3, R4, R5, R11.

**Dependencies:** Unit 1, Unit 2.

**Files:**
- Modify: `src/backlink_publisher/cli/validate_backlinks.py`
- Modify: `src/backlink_publisher/cli/plan_backlinks.py` (emit `row['metadata']['branded_pool']` snapshot — see Approach step 0)
- Modify: `src/backlink_publisher/schema.py` (allow optional `metadata.branded_pool: list[str]` field on output rows; do NOT make it required — back-compat)
- Test: `tests/test_validate_backlinks.py` (existing — update assertions and add new fixtures)
- Test: `tests/test_plan_backlinks.py` (existing — add assertion that emitted row carries `metadata.branded_pool`)

**Approach:**
- **Step 0 (plan_backlinks emit)**: At each emission site (`plan_backlinks.py:629, 632`, and the `report_anchors` / work-themed emission paths), include `row['metadata'] = {**row.get('metadata', {}), 'branded_pool': get_anchor_pool_v2(config, row['main_domain'], 'home', 'branded')}` as a frozen snapshot. This is the validate→publish TOCTOU close.
- **Step 1 (validate_backlinks lookup)**: At the top of `main()` (around line 75), call `config = load_config()` after argparse as a fallback source. Pass `config` through to `_enhance_payload(row, config)`. Each call resolves `branded_pool` as: `row.get('metadata', {}).get('branded_pool')` first; if `None`, fall back to `get_anchor_pool_v2(config, row['main_domain'], 'home', 'branded')`. `row['main_domain']` is guaranteed present per `schema.py:37` `OUTPUT_REQUIRED_FIELDS`. Graceful degrade: if `load_config()` itself fails AND the payload has no branded_pool snapshot, emit a WARN ("branded_pool unavailable — anchor exemption disabled for this row") and proceed with empty branded_pool. CI / smoke-test environments piping pre-built JSONL with the snapshot are unaffected.
- Inside `_enhance_payload`:
  1. Initialize `errors: list[str] = []`, `warnings: list[str] = []`.
  2. If `row['language'] not in SUPPORTED_LANGUAGES`: emit `validate_logger.warn(...)` (note: `PipelineLogger` exposes `.warn` not `.warning`, see `logger.py:45`), set `status="passed"`, return early.
  3. R2: `detected = detect_language(row['content_markdown'])`. If `not language_matches(detected, row['language'])`: append `f"body language '{detected}' does not match requested '{row['language']}'"` to `errors`.
  4. R4/R5: for each link in `row['links']`: call `check_anchor_language(link['anchor'], row['language'], link['kind'], branded_pool)`. If `(ok, reason) = (False, reason)`: append `f"link[{i}] anchor '{link['anchor']}' failed: {reason}"`.
  5. Set `validation = {"status": "failed" if errors else "passed", "checked_at": ..., "warnings": [], "errors": errors}`.
- In the row-iteration loop (lines 101-115), accumulate `_enhance_payload(row, config)` output to `outputs` only when its `validation.status == "passed"`. Failed rows are NOT appended to `outputs` (so `write_jsonl(outputs)` continues to not emit them — consistent with origin's "failed rows NOT written to stdout").
- After the loop, if any row failed, accumulate to `all_errors` and trigger the existing `SystemExit(2)` path. Adjust the completion log message to include `f"validated {passed_count} passed, {failed_count} failed"`.

**Patterns to follow:**
- Accumulator pattern: `all_errors` loop at `validate_backlinks.py:101-115`.
- Logger pattern: `validate_logger.info(...)` and `validate_logger.warning(...)` already in use at lines 70, 75, 117.

**Test scenarios:**
- Happy path: en row with all-ASCII anchors, all main/target anchors in branded_pool (or English-only with `kind=main_domain`) → `validation.status == "passed"`, `errors == []`, `warnings == []`. Existing `test_validate_valid_payload` covers this.
- Error path (R2 body language): zh-CN row whose `content_markdown` is "This is an English article about ..." → `validation.status == "failed"`, `errors` contains "body language 'en' does not match requested 'zh-CN'", row NOT in stdout, CLI exits 2.
- Error path (R5 anchor): zh-CN row with `kind=main_domain` anchor "learn more" and empty branded_pool → `errors` contains "link[X] anchor 'learn more' failed: anchor missing CJK codepoint", CLI exits 2.
- Happy path (R4 branded exemption): zh-CN row with `kind=main_domain` anchor "Apple" and branded_pool=["Apple"] → passes. Requires test config fixture.
- Happy path (R4 kind exemption): zh-CN row with `kind=supporting` anchor "MDN" → passes. Existing `_make_valid_payload` supporting links cover the structural case (under en); add a zh-CN variant fixture.
- Edge case (R3 non-enum language): row with `language="ja"` → WARN log emitted, `status="passed"`, no errors. CLI exits 0 (assuming nothing else fails).
- Integration: a batch of 5 rows where rows 2 and 4 fail R2/R5 → completion log reports "validated 3 passed, 2 failed", `write_jsonl` writes only 3 rows, CLI exits 2.
- Edge case: row.validation.warnings is preserved as an empty list (back-compat with `test_validate_backlinks.py:189`).
- Error path (no main_domain anchor in row): branded_pool lookup returns `[]` cleanly via `.get('home', {}).get('branded', [])`. Missing config-pool for the target domain should not crash — the absence behaves as empty pool.

**Verification:**
- `pytest tests/test_validate_backlinks.py -v` passes all updated assertions.
- A manual CLI invocation against a known-bad fixture (`echo '{...zh-CN+English-body...}' | validate-backlinks`) exits non-zero with a structured error message.
- `_enhance_payload` is unit-testable as a pure function (called directly from tests, not just via CLI).

---

- [x] **Unit 4: Additive `linkcheck.check_url(url)` public helper**

**Goal:** Add a per-URL public function that wraps `_check_url_with_retry` and returns `(ok: bool, error: str | None)` instead of raising. Used by Unit 5 for per-row publish-time re-check. `check_urls_strict` stays untouched (R6).

**Requirements:** R8 (foundation), R9 (foundation).

**Dependencies:** None.

**Files:**
- Modify: `src/backlink_publisher/linkcheck.py`
- Test: `tests/test_linkcheck.py` (existing — add cases)

**Approach:**
- Add `def check_url(url: str) -> tuple[bool, str | None]:` at the bottom of `linkcheck.py`, near `check_urls` (line 77).
- Implementation: `_, ok, err = _check_url_with_retry(url); return ok, err`. The `_` discards the URL re-emit from the private helper.
- Add it to the module's public exports if there's an `__all__`; if not, just leave it module-public (consistent with `check_urls_strict`).

**Patterns to follow:**
- `check_urls_strict` shape at line 93-109 — wrapper around `check_urls`.

**Test scenarios:**
- Happy path: `check_url("https://example.com")` (mocked 200) → `(True, None)`.
- Error path: `check_url("https://example.com")` (mocked 404 across all retries) → `(False, "<error_string>")` where error string mentions HTTP status.
- Edge case: `check_url("https://example.com")` (mocked Timeout 3 times) → `(False, "...timeout...")`.
- Verify retry count: mock the underlying urlopen 3 times and confirm `_check_url_with_retry` is called exactly once and ranges over the 3 attempts internally.
- Edge case: malformed URL → `(False, "...")` with a clear error string; does not crash.

**Verification:**
- `pytest tests/test_linkcheck.py::test_check_url -v` passes the table.
- `check_urls_strict` regression: existing tests pass unchanged.

---

- [x] **Unit 5: Publish-time per-row reachability re-check (R8/R9/R12)**

**Goal:** In `publish-backlinks` per-row loop, immediately before `adapter_publish` (line 498), call `linkcheck.check_url` on `target_url` and each `row.links[*].url`. On any failure, skip the row (no partial publish), emit a structured WARN line, write `status: "skipped_unreachable"` to output JSONL, leave checkpoint item `pending`, continue to next row. After the loop, emit an aggregate completion log line.

**Requirements:** R8, R9, R12.

**Dependencies:** Unit 4 (uses `check_url`), Unit 6 (gated by `--skip-publish-time-check`).

**Files:**
- Modify: `src/backlink_publisher/cli/publish_backlinks.py`
- Test: `tests/test_publish_backlinks.py`, `tests/test_publish_backlinks_publish_time_linkcheck.py` (new file for focused coverage)
- Create: `tests/conftest.py` (file does NOT exist today — `find tests/ -name conftest.py` returns empty; per `feedback_test-autouse-verify-mock` existing tests carry per-file autouse mocks). New top-level `conftest.py` introduces TWO autouse fixtures: (1) `disable_real_http` patches `backlink_publisher.cli.publish_backlinks.check_url` at the consumer reference; (2) `pytest_socket.disable_socket()` as a CI-grade safety net so any test that bypasses the patch still cannot reach real network. Add `pytest-socket` to dev dependencies (`pyproject.toml`). Implementer should NOT mass-migrate existing per-file mocks in this PR; the new fixture is additive.
- Modify: `pyproject.toml` (add `pytest-socket` to dev deps).

**Approach:**
- New helper inside `publish_backlinks.py`: `_check_row_reachability(row, logger) -> tuple[bool, str | None]`. Collects `urls_to_check = [row['target_url']] + [link['url'] for link in row['links']]`. Dispatches all URLs **in parallel** via `concurrent.futures.ThreadPoolExecutor(max_workers=linkcheck.MAX_CONCURRENT)` (reuse the existing constant; 10 by default) calling `linkcheck.check_url` for each. Returns `(True, None)` if all succeed; `(False, failing_url)` on the first failure observed (cancel pending futures). Worst-case per-row latency drops from N×3s serial to ~3s. Worst-case batch latency for 50 rows × 7 URLs becomes ~50 × 3s ≈ 2.5 min (vs ~17.5 min serial). 
- **First-run upgrade warning**: at `publish-backlinks` startup, when `args.skip_publish_time_check is False` AND the user has not yet seen a one-shot first-run banner, emit ONE prominent WARN to stderr: `"publish-backlinks now performs a publish-time reachability re-check on every row. Use --skip-publish-time-check to restore prior behavior. This message will not repeat."` Suppress on subsequent runs by writing a sentinel file at `~/.cache/backlink-publisher/v0.3-gate-warning-seen` (or similar versioned path). Sentinel name encodes plan ID so a future toggle can re-warn.
- Inside the main per-row loop (around line 498), before `adapter_publish`: if `not args.skip_publish_time_check`, call `_check_row_reachability(row, publish_logger)`. On `(False, failing_url)`:
  - `publish_logger.warn(f"[publish-backlinks] row_id={row_id} status=skipped_unreachable url={failing_url}")` (note: `PipelineLogger` exposes `.warn` not `.warning`, see `logger.py:45`).
  - Write JSONL line: `{**row, "status": "skipped_unreachable", "failing_url": failing_url}` to the output stream.
  - Do NOT update checkpoint item — it stays `pending`.
  - `continue` to next row.
- After the per-row loop, aggregate count: `skipped_count`. Emit `publish_logger.info(f"publish-backlinks completed: {ok_count} done, {skipped_count} skipped_unreachable")`. Per-row WARN lines already carry the failing URL, so an explicit hostname enumeration in the completion log is noise — drop it. Operators can `grep status=skipped_unreachable` over the WARN lines if they need the host distribution.
- Add the same loop in `_run_resume` (lines 74-238) per-item path so resumed runs also re-check before dispatching.
- Import `check_url` at the consumer module level: `from ..linkcheck import check_url` near the top of `publish_backlinks.py`. Tests will patch `backlink_publisher.cli.publish_backlinks.check_url`.

**Patterns to follow:**
- `_do_verify` helper pattern (line 36-area) — small pure-ish function called from the per-row loop.
- WARN log format: existing log lines in `publish_backlinks.py` use a `[publish-backlinks] ...` prefix.
- Autouse mock pattern: `tests/conftest.py` per `feedback_test-autouse-verify-mock`.

**Test scenarios:**
- Happy path: 3-row batch, all URLs reachable (mocked 200) → all 3 publish, no skip_unreachable JSONL, completion log says "3 done, 0 skipped".
- Error path (target_url 404): 1-row batch, target_url returns 404 (3 retries exhausted) → 0 publishes, 1 JSONL line with `status: "skipped_unreachable"` and `failing_url: "<target_url>"`, WARN log emitted, checkpoint item stays `pending`, adapter.publish NOT called.
- Error path (mid-batch failure): 3-row batch, row 2's first `link.url` 404 → rows 1 and 3 publish, row 2 written as `skipped_unreachable`, completion log "2 done, 1 skipped across 1 host (example.com)".
- Edge case (skip flag): `--skip-publish-time-check` is set → `_check_row_reachability` never called, all rows dispatch directly. Verify via `mock.assert_not_called()` on the helper.
- Edge case (retry latency): a row with one 404 URL takes ~3s in the test if not mocked. Tests MUST mock `_check_url_with_retry` to avoid real HTTP. Use `pytest-timeout=30`.
- Integration: full publish run with autouse HTTP mock — 5 rows, 1 with bad target_url, 1 with bad link.url, 3 healthy → exactly 3 published, exactly 2 skipped JSONL, checkpoint shows 3 done + 2 pending.
- Edge case (--resume path): a checkpoint with item.status=pending whose target_url is now reachable → resume processes it; if it's now unreachable → re-classified appropriately (skipped, stays pending). Verifies Unit 5 integrates with the resume loop, not just fresh `main`.
- Verification: autouse fixture in `conftest.py` patches `backlink_publisher.cli.publish_backlinks.check_url` at module-consumer reference (per learnings test-failures doc) and asserts no real network call escapes.

**Verification:**
- `pytest tests/test_publish_backlinks_publish_time_linkcheck.py -v --timeout=30` passes the table.
- `pytest tests/ -q` (full suite) still green or only with intentional failures addressed by Unit 8.
- No real HTTP fires during test run — confirm via `--no-network` or by inspecting fixture coverage.

---

- [x] **Unit 6: `--skip-publish-time-check` flag + checkpoint flag persistence**

**Goal:** Add new opt-in flag on `publish-backlinks` that disables Unit 5's per-row re-check. Persist the flag value in the checkpoint metadata so `--resume` honors the original posture.

**Requirements:** R10.

**Dependencies:** None (Unit 5 depends on Unit 6 — the flag must exist before Unit 5 reads it; the relationship is one-way).

**Sequencing within `publish_backlinks.py`:** Both Unit 5 and Unit 6 modify the same file. **Implement Unit 6's argparse addition FIRST** so that Unit 5 can reference `args.skip_publish_time_check` in its per-row check. The two changes can ship in one PR but the edit order matters for incremental commits.

**Files:**
- Modify: `src/backlink_publisher/cli/publish_backlinks.py` (argparse + thread-through)
- Modify: `src/backlink_publisher/checkpoint.py` (`create_checkpoint` signature + serialization)
- Test: `tests/test_publish_backlinks_flags.py` (new), `tests/test_checkpoint.py` (existing — confirm back-compat with missing `flags` key)

**Approach:**
- argparse addition in `publish_backlinks.py` (template: `--no-verify` at line 313-318):
  - `--skip-publish-time-check`, `action="store_true"`, `default=False`, help mirrors brainstorm R10.
- argparse rename in `validate_backlinks.py:54-59`:
  - Rename primary flag to `--no-validate-url-check`. Add `--no-check-urls` as a deprecated alias (`add_argument("--no-check-urls", dest="no_validate_url_check", action="store_true", ...)` — both write to the same dest). On parse, if the user passed the old name, emit a one-line WARN via `validate_logger.warn(...)`: `"--no-check-urls is deprecated; use --no-validate-url-check. Removed in v0.3.0."`. Detection: parse argv before argparse handles it (`"--no-check-urls" in sys.argv` is the simplest signal; record at module level before argparse rebinds).
- Thread the flag through `main()` to `create_checkpoint(..., flags={"skip_publish_time_check": args.skip_publish_time_check})`.
- `checkpoint.create_checkpoint` (currently signature at line 50): add `flags: dict[str, Any] | None = None` parameter; serialize as top-level `"flags": flags or {}`.
- `_run_resume` (line 82-ish, after `load_checkpoint`): read `ckpt.get("flags", {}).get("skip_publish_time_check", False)` and apply to its loop (Unit 5 already added the loop in `_run_resume`; here we wire the flag to it).
- For back-compat: pre-flags checkpoints have no `flags` key. `ckpt.get("flags", {})` returns `{}`; `.get("skip_publish_time_check", False)` returns `False` — meaning resume of an old checkpoint enables the new gate by default. **This is the safer direction** — accidental upgrade strengthens the gate. Document in CHANGELOG.

**Patterns to follow:**
- argparse pattern: `validate_backlinks.py:54-59` (`--no-check-urls`).
- Checkpoint extension pattern: `checkpoint.py:50-90` (`create_checkpoint`).
- `feedback_config-save-overwrite-pattern` guidance: read full dict, mutate, write — never overwrite shape-aware regions blindly.

**Test scenarios:**
- Happy path: `publish-backlinks --skip-publish-time-check < fixture.jsonl` → checkpoint JSON contains `"flags": {"skip_publish_time_check": true}`; no `check_url` calls.
- Happy path: `publish-backlinks < fixture.jsonl` (no flag) → checkpoint `flags.skip_publish_time_check` is `false`; `check_url` is invoked per row.
- Edge case (resume w/ pre-flags checkpoint): old checkpoint without `flags` key → `--resume` runs with `skip_publish_time_check=False` (the safer default), check_url fires per row.
- Edge case (resume preserves flag): `publish-backlinks --skip-publish-time-check ...` interrupted, then `publish-backlinks --resume <run_id>` → still no check_url calls.
- Integration: `_OPTIONAL_ITEM_FIELDS` (checkpoint.py:102) — confirm the new top-level `flags` key does not interact with per-item update_item leakage.

**Verification:**
- `pytest tests/test_publish_backlinks_flags.py -v` green.
- `pytest tests/test_checkpoint.py -v` green (back-compat).
- Manual test: kill mid-batch, resume — flag posture preserved.

---

- [x] **Unit 7: `--resume` hard re-validates checkpoint (R13)**

**Goal:** When `publish-backlinks --resume` reads a checkpoint, before processing `pending`/`failed` items, re-run R2 (body language) and R5 (anchor codepoint) against each item's stored payload. Reclassify failing items to `failed` with `error_class ∈ {"retro_language_failed", "retro_anchor_failed"}` and skip them from this run. Emit a one-shot INFO summary line.

**Requirements:** R13.

**Dependencies:** Unit 3 (uses the same gate logic — extract a shared helper or call `_enhance_payload` directly).

**Files:**
- Modify: `src/backlink_publisher/cli/publish_backlinks.py` (`_run_resume` only)
- Test: `tests/test_publish_backlinks_resume.py` (existing — add R13 cases)

**Approach:**

**Sequencing within `_run_resume`:** Unit 7 inserts the re-validation pass BEFORE Unit 5's per-row reachability check. Logical reason: invalid rows (R2/R5 failure) should be filtered before any network operations are dispatched against them. Both units edit `_run_resume`; Unit 7's edit lands first in the function body, Unit 5's per-row check sits inside the existing per-item loop.

- Inside `_run_resume`, immediately after `load_checkpoint` (line 82) AND `config = load_config()` (line 87, so the branded_pool lookup has its config): for each item in `ckpt['items']` where `status in ("pending", "failed")`, run `_enhance_payload(item['payload'], config)`. If the returned `validation.status == "failed"`:
  - Classify: if any error string starts with "body language" → `error_class = checkpoint.RETRO_LANGUAGE_FAILED`; if starts with "link[" → `error_class = checkpoint.RETRO_ANCHOR_FAILED`. Both string constants are defined as module-level constants in `checkpoint.py` (alongside any existing error_class constants) and imported wherever they're produced or consumed (Unit 7 producer, `to_process` filter, `--list-runs` display). No magic strings.
  - Call `checkpoint.update_item(ckpt_path, item['id'], "failed", error=errors_joined, error_class=...)`.
- After the loop, emit `publish_logger.info(f"resume: re-validated {n_total} items, reclassified {n_lang} language_failed + {n_anchor} anchor_failed; resuming {n_proceeding} pending/failed items")`.
- Then proceed with the existing `to_process` filter — items just reclassified to `failed` will still be picked up because the filter is `status in ("pending", "failed")`. To ACTUALLY skip them, exclude items with `error_class in ("retro_language_failed", "retro_anchor_failed")` from `to_process`.
- Out of scope: re-validating `done` items. Those are already published; harm done.

**Patterns to follow:**
- `checkpoint.update_item` invocation pattern: existing calls at `publish_backlinks.py:525, 554, 171, 180`.
- `validate_logger.info` for one-shot summary: pattern at `validate_backlinks.py:75, 117`.

**Test scenarios:**
- Happy path: checkpoint with 3 pending items, all currently valid → re-validate all 3 pass, none reclassified, all 3 proceed.
- Error path (retro language): checkpoint with 1 pending item whose payload was generated under the buggy `language_matches` (e.g., zh-CN row with English body) → re-validate reclassifies to `failed` + `error_class="retro_language_failed"`, the item is excluded from `to_process`, INFO log says "reclassified 1 language_failed".
- Error path (retro anchor): checkpoint pending item with main_domain anchor "learn more" in a zh-CN row → reclassified `retro_anchor_failed`.
- Edge case (both failures): single item failing both R2 and R5 → classified to whichever fires first (or a combined class — implementer's call); errors include both messages.
- Edge case (already retro-failed): if the item's existing `error_class` is already `retro_language_failed`, re-validation is idempotent (it's still failed, gets re-classified to same).
- Integration: full `--resume` run with 5-item checkpoint where 2 are retro-failed and 3 are valid → INFO summary, only 3 items dispatched (1 of which has updated reachability fail = skipped by Unit 5), final checkpoint shows 1 retro_failed + 1 retro_failed + 1 done + 1 done + 1 still pending (the Unit 5 skip).

**Verification:**
- `pytest tests/test_publish_backlinks_resume.py -v` green.
- Manual: hand-craft a buggy-era checkpoint JSON, run `--resume`, confirm reclassification and that `--list-runs` surfaces the new error_class via existing display.

---

- [x] **Unit 8: Test sweep + back-compat verification**

**Goal:** Audit existing tests for assertions that the buggy `language_matches` happened to satisfy, update them with explicit rationale comments. Confirm `pytest -q` exits 0 on the full suite (999 tests verified via `pytest --collect-only -q` at plan time). Confirm webui's `validation.warnings` reader (if any) still functions because the field is preserved as empty list.

**Pre-finalization grep results** (run during document-review):
- `grep -rn "language_matches" tests/` → **0 hits**. No direct assertions on the buggy contract.
- `grep -rn 'validation\[\"status\"\] == \"passed\"' tests/` → **2 hits**: `tests/test_validate_backlinks.py:80, 187`. Both use `_make_valid_payload()` default (`language="en"`, content_markdown is detectable English) — under R1+R2, both still pass because EN/EN matches. **No test flip required** for the existing happy-path assertions.
- `grep -rn "validation.*warnings" tests/` → **1 hit**: `tests/test_validate_backlinks.py:189` (the `isinstance(..., list)` shape assertion already addressed by R11's "preserve as empty list" decision).
- Sizing: Unit 8 is effectively ~half a day of work, not week-long. Most effort goes into the NEW zh-CN / ru fixture additions (Unit 3 deliverable, not Unit 8).

**Requirements:** Cross-cutting (origin §Success Criteria — "all 999 tests pass").

**Dependencies:** Units 1, 3, 5, 6, 7.

**Files:**
- Modify: any test file flipped by R1's fix (most candidates already identified: `tests/test_validate_backlinks.py:80, 187, 189`).
- No new file unless missing coverage discovered.

**Approach:**
- Grep `tests/` for `validation.status == "passed"` and `validation.warnings`. Read each assertion and trace the fixture's `language` and content to predict whether R1+R2+R5 still pass. If the test fixture's `(language, content_markdown)` pair would now fail R2 under correct `language_matches`, either:
  - **Fix the fixture** if the test's *intent* was a happy-path test (give it content that legitimately matches the language).
  - **Update the assertion** to expect `status: "failed"` if the test was implicitly relying on the bug to make a should-be-failure look like a pass; add a code comment `# Updated 2026-05-14 per plan 2026-05-14-001: language_matches no longer always True`.
- Grep for `language_matches` direct calls in tests — fix any that asserted the all-True behavior.
- Grep `webui.py` (and templates) for readers of `validation.warnings` to confirm R11's "preserved as empty list" decision keeps them green; no code change needed there for v1.
- Grep all output JSONL consumers (tests + webui + any post-processing script) for `status` field readers to ensure new `status: "skipped_unreachable"` doesn't crash anything that assumes only `published` / `failed`.
- Verify `tests/conftest.py` autouse HTTP mock fixture covers all new HTTP callsites added by Unit 5 (consumer-reference patching per learnings).

**Test scenarios:**
- N/A — this unit is verification of cross-cutting impact, not new tests. Count of touched test files is the deliverable.

**Verification:**
- `pytest -q --timeout=30` exits 0 on the full suite.
- `grep -rn "validation.status == \"passed\"" tests/` shows no orphaned assertions on fixtures that should now fail.
- Manual: `webui.py` loads + processes a sample JSONL containing the new `status: "skipped_unreachable"` without crashing.

## System-Wide Impact

- **Interaction graph:** `validate_backlinks → config.load_config → get_anchor_pool_v2` (new edge). `publish_backlinks → linkcheck.check_url` (new edge). `publish_backlinks._run_resume → _enhance_payload` (new edge — R13 reuses validate-time gate logic).
- **Error propagation:** R2/R5 failures populate `row.validation.errors`, exit 2 via existing `SystemExit(2)` accumulator. R8/R9 failures emit WARN + write `skipped_unreachable` JSONL + leave checkpoint `pending`; no exit code change. R13 failures land in checkpoint `error_class` + exclude from `to_process`; no exit code change.
- **State lifecycle risks:**
  - Checkpoint shape change: top-level `flags` key added. Existing `_run_resume` must `.get("flags", {})` with default to handle pre-flags JSON. Listed in Unit 6 tests.
  - Output JSONL shape change: new `status: "skipped_unreachable"` + `failing_url` field. Listed in Unit 8 grep.
  - `validation.errors[]` field added; `validation.warnings[]` preserved as empty list. Listed in R11 Key Decisions.
- **API surface parity:**
  - CLI: new `--skip-publish-time-check` flag on `publish-backlinks`. README + `--help` text need refresh as part of Unit 6 (documentation included in the unit).
  - Internal: `linkcheck.check_url` is new public function. No removed/renamed surface.
- **Integration coverage:** Cross-layer scenarios covered by Unit 5's integration test (autouse mock + multi-row batch with mid-batch failures) and Unit 7's `--resume` integration with retro reclassification.
- **Unchanged invariants:** `check_urls_strict` semantic at validate-time (R6). Existing exit-code contract (2 = invalid payload, 4 = URL unreachable at validate-time). `LINK_KINDS` schema enum. `--no-check-urls` continues to work as a deprecated alias (no breaking change for existing cron scripts using the old name).
- **Newly changed**: `plan-backlinks` emission paths gain `row.metadata.branded_pool` snapshot (Unit 3 step 0); `validate-backlinks` argparse renames primary flag with deprecated alias (Unit 6).

### Downstream not in scope for this plan

- **webui banner for `validation.errors`** — `validate-backlinks` failed rows are NOT written to stdout, so the webui's subprocess invocation only sees stderr + non-zero exit. Webui surfacing of error details is a follow-up plan; learning from `ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md` applies when that lands.
- **webui copy refresh for the new publish-time linkcheck latency** — `/ce:publish-real` will run slower with R8 in the loop; loading overlay copy should be updated in a follow-up.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `language_matches` fix flips many tests; count unknown ahead of time | Unit 8 is the explicit test-sweep unit; grep + read fixture content before deciding fix-fixture vs flip-assertion |
| `validate-backlinks` now loads Config at startup; failures here become CLI-level failures | `load_config` already exists and is used by `plan-backlinks`; expect same failure modes (missing config → existing error class). No new failure surface. |
| Publish-time linkcheck adds ~3s worst-case latency per dead URL × N URLs per row | Brainstorm decision accepts this. The skip happens fast in normal cases. Aggregate completion log surfaces severity. `--skip-publish-time-check` is the escape hatch when cron load is critical. |
| Pre-flags checkpoint JSONs from before this PR — back-compat | `ckpt.get("flags", {}).get(...)` with default; tested in Unit 6. Behavior: missing flag → safer default (gate enabled). |
| `_OPTIONAL_ITEM_FIELDS` leakage if new per-item fields are added later | None added in this plan (Unit 5 writes to output JSONL, not checkpoint item). If a follow-up adds per-item flags, they go through `_OPTIONAL_ITEM_FIELDS` per `feedback_config-save-overwrite-pattern`. |
| Real HTTP fires during tests if autouse mock fixture misses Unit 5's callsite | Patch at `backlink_publisher.cli.publish_backlinks.check_url` (consumer reference, per `test-failures` learning). Verify via `--no-network` flag or fixture coverage report. |
| Branded-pool lookup edge cases: missing pool, missing target, malformed config | `get_anchor_pool_v2` itself returns `[]` when any lookup layer is missing (`config.py:701-726`); covered in Unit 3 edge-case tests. No defensive `.get()` chain needed at call site. |
| Branded-pool TOCTOU between validate and publish — operator edits `branded_pool` between commands, validate-time exempted anchor may fail R13 on `--resume` because R13 re-loads current config | Accepted for v1. Document in CHANGELOG and Operational notes: "If you edit `branded_pool` between `validate` and `publish` (or `--resume`), expect previously-exempted rows to potentially reclassify." Future option: snapshot `branded_pool` into checkpoint metadata at create_checkpoint time. |

## Documentation / Operational Notes

- **CHANGELOG entry**: this plan is a behavior-breaking change for any cron caller that was implicitly relying on `language_matches` silently passing. Document:
  - `language_matches` bug fix (R1) — rows that previously passed with a language mismatch warning will now fail with exit 2.
  - New `--skip-publish-time-check` flag (publish-backlinks) — default off; existing cron must add it if reachability re-checks are unwanted. Per-row HTTP latency added (parallelized via ThreadPoolExecutor; ~3s worst-case per row instead of ~21s).
  - Flag rename: `validate-backlinks --no-check-urls` → `--no-validate-url-check`. Old name kept as **deprecated alias** with WARN; targeted for removal in v0.3.0. Existing cron scripts using `--no-check-urls` continue working (with WARN per invocation).
  - New `status: "skipped_unreachable"` in output JSONL + new optional `metadata.branded_pool: list[str]` snapshot — downstream consumers should handle gracefully (both are additive).
  - `--resume` hard re-validates checkpoint — pre-existing pending items may be reclassified `retro_language_failed` / `retro_anchor_failed`.
  - Branded-pool TOCTOU: if operator edits `branded_pool` between `validate` and `publish` (or `--resume`), the validate-time snapshot stored in `row.metadata.branded_pool` continues to apply (closing the original TOCTOU concern from plan-review).
- **First-run banner**: publish-backlinks emits a one-shot prominent WARN explaining the new gate behavior on the first invocation after upgrade (suppressed via versioned sentinel file in `~/.cache/`). Cron operators who skip CHANGELOG still see the new behavior surfaced once.
- **README**: refresh the "validation behavior" section to describe the row-level abort + accumulator pattern + two-flag escape hatch.
- **Operational**: cron operators should run `validate-backlinks` once locally after upgrade to surface any retroactively-suspect rows in their pipeline before the production cron picks up the new behavior. Recommend in CHANGELOG.

## Sources & References

- **Origin document:** [backlink-publisher/docs/brainstorms/2026-05-14-mandatory-linkcheck-lang-gate-requirements.md](../brainstorms/2026-05-14-mandatory-linkcheck-lang-gate-requirements.md)
- **Sibling plans:**
  - [2026-05-12-002-feat-adapter-retry-exponential-backoff-plan.md](2026-05-12-002-feat-adapter-retry-exponential-backoff-plan.md) — adapter retry layer; R9 deliberately does NOT extend it.
  - [2026-05-13-003-feat-checkpoint-resume-plan.md](2026-05-13-003-feat-checkpoint-resume-plan.md) — checkpoint state machine; this plan adds `flags` top-level key and reuses `error_class`.
- **Institutional learnings:**
  - [test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md](../solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md)
  - [ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md](../solutions/ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md)
- **Code references (selected):**
  - `src/backlink_publisher/language_check.py:57-71` (R1 site)
  - `src/backlink_publisher/cli/validate_backlinks.py:20-38, 101-121` (R2/R5/R11 site)
  - `src/backlink_publisher/linkcheck.py:59-76, 93-109` (R8/R9 helpers)
  - `src/backlink_publisher/cli/publish_backlinks.py:74-238, 313-318, 498-504` (R8/R10/R13 sites)
  - `src/backlink_publisher/checkpoint.py:50-90, 102` (R10 persistence site)
  - `src/backlink_publisher/schema.py:48, 125-127` (LINK_KINDS + anchor field shape)
- **Memory references** (all under `/Users/dex/.claude/projects/-Users-dex-YDEX-INPORTANT-WORK----0511-opencli-backlink-by-opencode/memory/`):
  - `feedback_no-runtime-llm.md`, `feedback_llm-free-pool-sizing.md`, `feedback_test-autouse-verify-mock.md`, `feedback_python-mock-datetime-patterns.md`, `feedback_macos-adapter-test-isolation.md`, `feedback_config-save-overwrite-pattern.md`, `feedback_test-locks-in-bug.md`, `feedback_plan-vs-code-drift.md`
