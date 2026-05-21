---
title: "fix: WebUI publish button shows success banner with no article URL"
type: fix
status: completed
date: 2026-05-21
---

# fix: WebUI publish button shows success banner with no article URL

## Overview

The `发布` submit button on the WebUI plans card POSTs to `/ce:publish`. Operators
report that the page navigates (renders the publish-results card) but no article
is actually published on the target platform. Root-cause investigation shows
two independent defects that must both be fixed for the symptom to stop:

1. **Route-side**: `webui_app/routes/pipeline.py:240-291` (`ce_publish`) is the
   last surviving publish callsite that writes history via a hand-rolled
   `history_store.update(...)` dict instead of routing through
   `_push_history_per_row` (`helpers.py:435`). Every other publish callsite
   (`scheduler.py:99/203`, `batch.py:134/174`) was migrated; this one was
   missed. The dict it constructs collapses an N-row JSONL stdout into a
   single entry whose `status` is hard-coded to `'published'` or `'drafted'`
   whenever **any** of the rows produced a URL — masking per-row failures.
2. **Template-side**: `webui_app/templates/index.html:1192-1194` unconditionally
   renders a green `发布成功！` banner whenever `published` (the raw stdout) is
   truthy. There is no branch on actual outcome. When `publish-backlinks`
   emits a row with `status: failed` and empty `published_url`/`draft_url`,
   the operator still sees a green check.

The combined effect: the page "跳转" but no article exists, and no usable error
surfaces. This is exactly the failure mode that PR #87/#97 introduced
`_push_history_per_row` to prevent — see memory feedback
`publish_history_invariant_helper`.

## Problem Frame

Operator workflow: enter URL → generate plans → click `发布` (the submit button
referenced in the bug report) → page reloads showing publish-results card with
green `发布成功！` banner, no article URL on the target platform, no clear error
to act on. History panel may also show a `'published'` entry with an empty
`article_urls` list, depending on which row was first.

This affects every publishing platform routed through `/ce:publish` (blogger,
medium, velog, hashnode, devto, telegraph, mastodon, ghpages, write.as — all
adapters dispatched by `publish-backlinks`). The bug is platform-agnostic;
the trigger is any path where the CLI returns a row whose `status` is
`failed` / `*_unverified` and whose URL fields are empty.

## Requirements Trace

- **R1.** `/ce:publish` must honor the per-row history invariant: published
  status only when the row has a usable URL, otherwise `failed` (or the
  CLI-emitted `*_unverified` status preserved verbatim). Implementation **must**
  delegate to `_push_history_per_row` — no inline dict construction.
- **R2.** The WebUI must **never** display the `发布成功！` banner when zero rows
  produced an article URL. Partial success (some rows ok, some failed) must be
  rendered distinctly from full success.
- **R3.** When publish fails (CLI nonzero, empty stdout, or all-failed rows),
  the operator must see (a) the adapter / error message inline and (b) the
  history panel must contain a per-row `failed` entry with the error string.
- **R4.** A regression test must lock R2 — given a mocked `run_pipe` returning
  JSONL with all-failed rows, the response body must not contain `发布成功！`
  and must contain the error string from the row.
- **R5.** No behavior regression for the happy path — fully successful publish
  must still render the green banner and per-row `已发布` / `草稿` badges with
  article links.

## Scope Boundaries

**In scope:**

- `webui_app/routes/pipeline.py` `ce_publish` route only.
- `webui_app/templates/index.html` publish-results card branching only.
- New WebUI route test (`tests/test_webui_publish_route.py` — new file).
- `plan_logger` diagnostic logging on adapter failure inside the route.

**Out of scope (explicit non-goals):**

- The two other forms of the same button (`/ce:draft/publish-now`,
  `/ce:draft/bulk-publish-now`, `/ce:publish-real`) — they already use
  `_push_history_per_row` correctly.
- Changes to `publish-backlinks` CLI behavior or adapter error contracts.
- Reworking the `_parse_publish_results` helper — it correctly returns a list
  of row dicts; the bug is downstream.
- WebUI session-loss handling (separate concern, low overlap).
- Renaming or moving `/ce:publish` route.
- Adding new failure-recovery UI (retry button, etc.) — pure rendering fix.

## Context & Research

### Relevant Code and Patterns

- **Defect site (route):** `webui_app/routes/pipeline.py:239-291` —
  `ce_publish` function. Specifically lines 265-284 (status synthesis +
  history write) are the rewrite target.
- **Defect site (template):** `webui_app/templates/index.html:1186-1233` —
  `{% if published %}` block; the unconditional `发布成功！` banner is at
  lines 1192-1194.
- **Canonical helper:** `webui_app/helpers.py:435-491`
  (`_push_history_per_row`) — already handles `failed` synthesis when no URL
  is returned, preserves `*_unverified` suffixes, and writes one history
  entry per row. Docstring explicitly states this was the Plan 2026-05-19-006
  Unit 1 root-cause fix.
- **Failure-only helper:** `webui_app/helpers.py:494-525`
  (`_push_history_single_failure`) — for the CLI-blew-up branch where there
  are no rows to forward.
- **Reference migrated callsite #1:** `webui_app/routes/batch.py:130-190` —
  cleanest exemplar of the dual-path pattern. On success, parses rows and
  calls `_push_history_per_row(...)`; on subprocess-level exception, calls
  `_push_history_single_failure(...)`. Mirror this structure for
  `ce_publish`.
- **Reference migrated callsite #2:** `webui_app/scheduler.py:99-211` —
  same pattern from the draft-job side; useful for double-checking shape.
- **Template precedent — failure rendering:** `index.html:1235-1244` already
  has a clean `{% if error %}` red error card. Reuse that styling for the
  failure / partial-failure case of the publish-results card.
- **`run_pipe` silent-failure guard:** `helpers.py:1209-1234` — already
  raises on `exit 0 + empty stdout + empty stderr`. The route's `except
  Exception` at line 289 must continue to catch this. The new code must
  preserve that behavior; do **not** swallow the exception.

### Institutional Learnings

- **`docs/solutions/`** (relevant entries):
  - `publish_history_invariant_helper` (memory feedback) — PR #87/#97
    introduced `_push_history_per_row`; reviewers reject any direct
    `_history_store.update` with `status="published"`. `/ce:publish` is the
    remaining violator.
  - Plan `2026-05-19-006-fix-webui-publish-history-truth-propagation` —
    same bug class on three other callsites. Its Unit 1 commit message and
    docstring are the reference rationale.
  - `grep_dofollow_map_before_shipping_adapter` (memory) — reminder that
    publish-side regressions tend to be silent at HTTP layer; rely on
    history + per-row badges for ground truth.

### External References

External research deliberately skipped — local patterns are dense and
directly transferable. Three migrated callsites in-repo plus the memory
feedback are sufficient grounding.

## Key Technical Decisions

- **Delegate to `_push_history_per_row`, do not patch the inline dict.**
  Rationale: the helper already encodes the right "no URL → failed" coercion
  and `*_unverified` preservation. Patching the inline dict re-implements
  this logic and is the same trap PR #87/#97 closed. Matches the
  `batch.py:134/174` exemplar exactly.

- **Split rendering into three states: `all_success` / `partial_success` /
  `all_failed`.** Rationale: a single boolean can't express partial outcome,
  and the current template's "any-row-truthy = green" is the literal source
  of the false success. Compute the three states once in the route (so the
  template stays declarative) and pass as named context keys. This avoids
  Jinja2 having to recompute `selectattr` filters multiple times.

- **Preserve `published` raw-output context key.** Rationale: the template
  already has a `<details>原始输出</details>` collapsible (line 1227-1230)
  that operators use for debugging. Keep it. The fix is purely additive in
  the rendering branch.

- **Pass `publish_error` (joined error strings from failed rows) as a
  separate context key, not as `error`.** Rationale: `error` already
  triggers the global red error card at `index.html:1235-1244`. We want the
  failure rendering co-located with the publish-results card (so operators
  see it next to the row that failed), not as a top-level page error. Reuse
  the error-card *styling* inside the publish-results card.

- **Log full stderr + row-status histogram on failure via `plan_logger`.**
  Rationale: current route logs nothing when publish fails. Operators have
  zero visibility into why the adapter returned no URL. `plan_logger`
  output goes to the webui log file and shows up in
  `[[webui-log-split-across-restart-files]]` — i.e., grep-able.

- **Test isolation: mock `run_pipe`, not the adapter.** Rationale: the four
  autouse conftest fixtures (per CLAUDE.md "Test isolation") already block
  the real CLI from touching the network. Mocking `run_pipe` at the
  pipeline-route boundary is cheapest and matches `test_webui_token_paste.py`
  / `test_webui_url_verify_routes.py` patterns.

## Open Questions

### Resolved During Planning

- **Q: Does `_push_history_per_row` need any signature change to support
  this callsite?** No — its existing kwargs (`target_url_fallback`,
  `platform_fallback`, `language_fallback`) cover everything the current
  inline dict at `pipeline.py:273-281` provides. Inspected helper signature
  at `helpers.py:435-441`.
- **Q: Should the velog-credential pre-check at `pipeline.py:248-254`
  also route through `_push_history_single_failure`?** Yes — for parity
  with the other adapter pre-check sites and so the failure shows up in
  history. Folded into U1.
- **Q: Does the `validated` branch (template lines 1166-1184) need the
  same fix?** No — it shares the same `/ce:publish` route, so the
  route-side fix covers it. The template branch in question (publish-results
  card at lines 1186-1233) renders identically whether triggered from
  `plans` or `validated` form.
- **Q: Will the 3-state template change break existing tests?** Audited
  `tests/test_webui_route_contract.py` — only verifies route existence.
  No template-content assertions exist for `/ce:publish`. Safe.

### Deferred to Implementation

- The exact `plan_logger` event names — pick names consistent with
  `plan_logger.warn("homepage_form_persist_failed", ...)` style at
  `pipeline.py:84`.
- Whether to factor the 3-state computation into a small helper in
  `helpers.py` or inline it in the route. Decide based on length when
  writing U1 — if computation exceeds ~15 lines, factor it out.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for
> review, not implementation specification. The implementing agent should
> treat it as context, not code to reproduce.*

State decision table — what the operator sees vs. CLI outcome:

| CLI outcome                                    | History entries        | Banner shown                | Per-row cards                  | publish_error |
|------------------------------------------------|------------------------|-----------------------------|--------------------------------|---------------|
| returncode != 0                                | 1× `failed` (synth)    | red, with stderr            | none                           | stderr        |
| returncode 0, empty stdout (silent-fail catch) | 1× `failed` (synth)    | red, with run_pipe diag     | none                           | run_pipe msg  |
| returncode 0, all rows fail / no URL           | N× `failed`            | red, "全部失败"             | N cards in `failed` style      | joined errors |
| returncode 0, mixed                            | N× per-row status      | yellow, "部分成功"           | N cards, status-colored        | joined fails  |
| returncode 0, all rows have URL                | N× `published`/`drafted`| green, `发布成功！` (existing)| N cards, success-colored       | none          |

Route-level flow:

```text
ce_publish (POST /ce:publish)
  ├─ velog precheck → if fail: _push_history_single_failure + render red banner
  ├─ run_pipe(['publish-backlinks', ...], plans)
  │     ├─ Exception → _push_history_single_failure + render red banner + log stderr
  │     └─ stdout → _parse_publish_results
  │           ├─ rows == [] → treat as failed (same as exception path)
  │           └─ rows → _push_history_per_row(rows, fallbacks=...)
  │                 ├─ compute (n_ok, n_failed) from rows  →  state ∈ {all_success, partial, all_failed}
  │                 ├─ log plan_logger.{info|warn}("publish_result", n_ok=, n_failed=, platform=)
  │                 └─ render publish-results card with state + publish_error context keys
  └─ template index.html branches on `publish_state`:
        all_success  → green `发布成功！` (unchanged)
        partial      → yellow `部分发布成功 (n_ok/N)` + publish_error block
        all_failed   → red `发布失败` + publish_error block
        (per-row cards render in all three states; only the banner differs)
```

## Implementation Units

- [ ] **Unit 1: Migrate `/ce:publish` to canonical history helper + 3-state outcome**

**Goal:** Replace the inline `entry = {...}` + `history_store.update(...)`
block in `ce_publish` with `_push_history_per_row` / `_push_history_single_failure`
calls. Compute `publish_state` ∈ `{all_success, partial_success, all_failed}` and
`publish_error` (joined error strings from failed rows). Pass both to the
template. Log result histogram via `plan_logger`.

**Requirements:** R1, R3, R5.

**Dependencies:** None — `_push_history_per_row` already exists.

**Files:**
- Modify: `backlink-publisher/webui_app/routes/pipeline.py` (lines 239-291)
- Test: `backlink-publisher/tests/test_webui_publish_route.py` (created in U3)

**Approach:**
- Import `_push_history_per_row` and `_push_history_single_failure` from
  `..helpers` alongside the existing `helpers` import block at lines 17-30.
- Replace the velog-precheck early-return at lines 248-254 — keep the
  precheck logic but route the failure through `_push_history_single_failure`
  and render with `publish_state="all_failed"`, `publish_error=detail`.
- Wrap `run_pipe(...)` — on `Exception`, call `_push_history_single_failure`
  with the exception string and render with `publish_state="all_failed"`.
  Preserve the existing `except Exception as e` at line 289 but route through
  the helper instead of the bare `error=...` render.
- After successful `run_pipe`, if `_parse_publish_results(published) == []`,
  treat as all-failed (silent-fail-with-output edge case).
- Replace lines 265-284 (`article_urls = ...` through `history_store.update`)
  with a single `_push_history_per_row(rows, target_url_fallback=...,
  platform_fallback=platform, language_fallback=...)` call.
- Compute `n_ok = sum(1 for r in rows if r.get('published_url') or r.get('draft_url'))`,
  `n_failed = len(rows) - n_ok`. Derive `publish_state` from the pair.
- Log via `plan_logger.warn` (failure / partial) or `plan_logger.info`
  (full success) with event name `webui_publish_result`, keys
  `platform`, `publish_mode`, `n_ok`, `n_failed`, `stderr_preview` (first
  200 chars), and `state`.
- Pass `publish_state`, `publish_error`, `publish_results`, `published` to
  `_render('index.html', ...)`. Keep `history_active=True`.

**Patterns to follow:**
- `webui_app/routes/batch.py:130-190` — the success-and-failure dual-helper
  shape.
- `webui_app/scheduler.py:99-211` — same pattern, secondary reference.
- `webui_app/_util/logger.py` `plan_logger.warn` event-naming convention
  (kebab-snake_case, short verb_noun events).

**Test scenarios:**
- Happy path — `run_pipe` returns 2 rows, both with `published_url` →
  `_push_history_per_row` called once with both rows, history shows 2
  `published` entries, response context has `publish_state == "all_success"`.
- Error path — `run_pipe` raises `Exception("simulated subprocess failure")`
  → `_push_history_single_failure` called once with the exception text,
  response context has `publish_state == "all_failed"` and `publish_error`
  contains the exception text.
- Error path — `run_pipe` returns 2 rows, both with empty
  `published_url`/`draft_url` and `status="failed"`, `error="auth expired"`
  → `_push_history_per_row` called, history has 2 `failed` entries,
  context `publish_state == "all_failed"`, `publish_error` contains
  "auth expired".
- Edge case — 2 rows, 1 with URL + 1 failed → `publish_state ==
  "partial_success"`, both history entries written, `publish_error` lists
  only the failed row's error.
- Edge case — velog platform with stale credentials triggers the precheck
  branch → `_push_history_single_failure` called, `publish_state ==
  "all_failed"`, `publish_error` contains the velog guide text.
- Edge case — `run_pipe` returns 0 rows (CLI emitted stdout but
  `_parse_publish_results` returned `[]`) → treated as all-failed,
  `_push_history_single_failure` called with a synthesized "CLI returned no
  parseable rows" message.
- Integration — after migrated route runs with mixed rows, calling
  `_history_store.load()` returns the per-row entries in expected order
  (newest first), proving the helper's `[*new_items, *hist][:100]` slicing
  works through this callsite.

**Verification:**
- `pipeline.py` no longer references `history_store.update` directly
  (`grep history_store.update webui_app/routes/pipeline.py` returns 0 lines).
- All four failure paths (subprocess exception, empty stdout, all-failed
  rows, velog precheck) end up in `_history_store` as `failed` entries.
- `pytest tests/test_webui_publish_route.py` passes.
- Full `pytest tests/` from `backlink-publisher/` exits 0 — no other route
  test regressed.

---

- [ ] **Unit 2: Template — 3-state publish-results banner**

**Goal:** Replace the unconditional `发布成功！` banner at `index.html:1192-1194`
with a 3-branch `{% if publish_state == ... %}` block. Render an inline
`publish_error` block (red `error-box` styling) when present. Keep the
per-row card iteration and the `<details>原始输出` collapsible unchanged.

**Requirements:** R2, R3, R5.

**Dependencies:** Unit 1 (route must pass `publish_state` / `publish_error`
context keys before this Unit can be verified end-to-end — though the
template changes themselves are safe to land in either order because the
template defensively defaults to `all_success` when keys are absent).

**Files:**
- Modify: `backlink-publisher/webui_app/templates/index.html` (lines 1186-1233)
- Test: `backlink-publisher/tests/test_webui_publish_route.py` (extended in U3)

**Approach:**
- Wrap the existing `{% if published %}` block with three Jinja branches
  on `publish_state`:
  - `'all_success'` (default if key missing): existing green
    `success-box` with `发布成功！` text — unchanged.
  - `'partial_success'`: yellow/warning box `部分发布成功 ({{ n_ok }}/{{ n_total }})`
    + an `error-box` listing `publish_error`.
  - `'all_failed'`: red `error-box` `发布失败` + `publish_error` body.
- Reuse the existing `.error-box` CSS class already used by `{% if error %}`
  at lines 1235-1244 — no new CSS.
- Reuse the existing `.success-box` class for `all_success`.
- Add a `.warning-box` rule **only if** not already defined in the page's
  `<style>` block; otherwise reuse. Grep the template first.
- Keep `{% if publish_results %}{% for r in publish_results %}` iteration
  unchanged — it already styles per-row cards based on `r.status`. The
  per-row badges and "查看文章" buttons stay as-is.
- Keep the `<details>原始输出</details>` collapsible unchanged.
- Default-safe: if neither `publish_state` nor `publish_error` is in
  context (e.g., from an old caller), render as `all_success` so any other
  route accidentally setting only `published` still works. This is belt-
  and-braces; U1 always sets `publish_state`.

**Patterns to follow:**
- Existing `.error-box` rendering at `index.html:1235-1244`.
- Existing success-box pattern at `index.html:1163-1165` (the `验证通过`
  card).
- Bootstrap alert color tokens already in use in the page — do not
  introduce new color variables.

**Test scenarios:**
- Render path — given `publish_state == "all_success"`, response body
  contains `发布成功！` and does **not** contain `发布失败` or
  `部分发布成功`.
- Render path — given `publish_state == "all_failed"` and `publish_error
  == "auth expired"`, response body contains `发布失败` and
  `auth expired`, does **not** contain `发布成功！`.
- Render path — given `publish_state == "partial_success"`,
  `n_ok=1`, `n_total=2`, response body contains `部分发布成功 (1/2)` and the
  `publish_error` text, does **not** contain `发布成功！`.
- Edge case — `publish_state` key absent (legacy caller) → falls back to
  `all_success`, response body contains `发布成功！` (defensive default
  preserved).
- Edge case — `publish_error` present but empty string → renders banner
  but suppresses the inline error block.
- Happy path — per-row cards still render correctly across all three
  states (the `{% for r in publish_results %}` loop is unchanged).

**Verification:**
- `grep -n "发布成功" webui_app/templates/index.html` shows the banner is
  now inside an `{% if publish_state == 'all_success' %}` (or equivalent
  default) branch — not free-floating.
- Visual smoke (manual): generate plans → click `发布` with an
  unauthenticated platform → page renders red banner with adapter error,
  no green check.
- Visual smoke (manual): happy path still renders green banner.
- `pytest tests/test_webui_publish_route.py` passes with U2 assertions
  added.

---

- [ ] **Unit 3: Regression test + log assertion**

**Goal:** Create `tests/test_webui_publish_route.py` covering the six
scenarios from U1 and the three render scenarios from U2. Lock the
invariant "`发布成功！` never appears when 0 rows produced a URL."

**Requirements:** R4, R5.

**Dependencies:** U1 + U2 (test asserts the behavior they introduce).

**Files:**
- Create: `backlink-publisher/tests/test_webui_publish_route.py`

**Approach:**
- Use Flask test client (`app.test_client()` pattern from
  `tests/test_webui_route_contract.py`).
- Seed session with `plans` JSONL and `config` dict via
  `with client.session_transaction()` block.
- Mock `webui_app.routes.pipeline.run_pipe` (not the lower-level
  subprocess) — at the import site. Use `unittest.mock.patch` against
  `webui_app.routes.pipeline.run_pipe`. This is the boundary the route
  sees.
- Use the four autouse conftest fixtures as-is — they sandbox config and
  block sockets, exactly the isolation this test wants.
- For history-write assertions, `_history_store` is sandboxed via the
  autouse config-dir fixture; load it via `webui_store.history_store.load()`
  after the POST and assert per-row entries.
- For template assertions, decode `resp.data` as UTF-8 and assert
  substring presence/absence on the Chinese banner strings.
- Add an assertion that `plan_logger.warn` was called with event name
  `webui_publish_result` and a `state` key — use a `caplog`-style or
  monkeypatch on the logger.

**Patterns to follow:**
- `tests/test_webui_token_paste.py` — Flask client + session setup.
- `tests/test_webui_helpers_medium_status.py` — `run_pipe` style mock.
- `tests/test_webui_route_contract.py` — route-existence baseline.

**Test scenarios:**

These are the assertions to write — each maps to one scenario above:

- Happy path — POST `/ce:publish` with 2 successful rows mocked, assert
  `200 OK`, body contains `发布成功！`, history has 2 `published` entries
  with URLs.
- Happy path — POST with `publish_mode='draft'` and rows containing
  `draft_url` only → body contains `发布成功！` (draft is success in the
  banner sense), history has 2 `drafted` entries.
- Error path — `run_pipe` raises `Exception("subprocess died")` → body
  contains `发布失败` and `subprocess died`, does **not** contain
  `发布成功！`, history has 1 synthetic `failed` entry.
- Error path — `run_pipe` returns JSONL of 2 rows both with
  `status='failed'` and empty URLs and `error='auth expired'` → body
  contains `发布失败` and `auth expired`, no `发布成功！`, history has 2
  `failed` entries.
- Edge case — `run_pipe` returns rows with mixed outcomes (1 with URL,
  1 failed) → body contains `部分发布成功`, does **not** contain
  `发布成功！` or `发布失败`, history has 1 `published` + 1 `failed`.
- Edge case — `run_pipe` returns 0 parseable rows (empty stdout reaches
  the post-parse branch — synthesized via a stdout that
  `_parse_publish_results` can't decode) → treated as all-failed, body
  contains `发布失败`.
- Edge case — velog platform with stale credentials (mock
  `_get_velog_status` to return `state='stale'`) → body contains
  `Velog 凭证无效` and `发布失败`.
- Integration — after all-failed POST, the history JSON endpoint
  (`GET /history.json` if it exists; otherwise direct
  `_history_store.load()`) returns entries with `status='failed'` and
  the error text preserved.
- Log assertion — for any failure case, `plan_logger.warn` was called
  at least once with event prefix `webui_publish_result` and a `state`
  key whose value matches the rendered banner.

**Verification:**
- `pytest tests/test_webui_publish_route.py -v` shows ≥8 test cases
  passing.
- `pytest tests/` from `backlink-publisher/` shows zero new failures.
- `python -m py_compile webui_app/routes/pipeline.py` returns clean.

## System-Wide Impact

- **Interaction graph:** `/ce:publish` is the only route changed.
  `_push_history_per_row` is unchanged but gains one more caller. The
  `index.html` template change is scoped to the publish-results card —
  other cards (validate, plans, history panel, settings panel) are
  untouched.
- **Error propagation:** Subprocess exceptions and silent-failure
  detections in `run_pipe` continue to surface — they now route through
  the helper instead of the bare exception path. The `except Exception`
  catch at `pipeline.py:289` is preserved so unknown failure modes still
  show a banner.
- **State lifecycle risks:** `_push_history_per_row` writes are
  atomic-via-`_history_store.update`; no partial-write risk introduced.
  The migration removes one `[entry, *hist][:100]` slicing site — the
  helper has its own `[*new_items, *hist][:_HISTORY_MAX_ITEMS]` slicing
  with the same `100`-cap constant. Cap behavior unchanged.
- **API surface parity:** Three sibling routes (`/ce:publish-real` in
  `batch.py`, `/ce:draft/publish-now` and `/ce:draft/bulk-publish-now` in
  `drafts.py`) already use the helper. After this fix, all four publish
  callsites are parity-aligned. No public CLI / JSON-endpoint contract
  changes.
- **Integration coverage:** Unit 3 tests cover the route ↔ helper ↔
  history-store integration. The end-to-end "click button → adapter call
  → history entry" chain is not testable headlessly here because the
  adapter call is mocked at the `run_pipe` boundary; live verification
  is a manual smoke test (see Verification on Unit 2).
- **Unchanged invariants:** (1) `/ce:publish` route URL, HTTP method,
  and form-field contract (`plans`, `platform`, `publish_mode`) are
  unchanged. (2) The `<details>原始输出</details>` debug block still
  contains the raw `published` stdout. (3) The `history_active=True`
  flag still flips the history tab visible on publish. (4) Session
  semantics — `session.get('plans', '') or request.form.get('plans',
  '')` — unchanged.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Template change accidentally hides the `<details>原始输出` debug block, losing operator escape hatch. | Visual smoke test in U2 verification explicitly checks the collapsible is still present. Test 3 in U3 also asserts the raw output substring. |
| `_push_history_per_row` `target_url_fallback` differs subtly from the inline dict's `config.get('target_url', 'unknown')` and history entries lose the URL. | Pass `target_url_fallback=config.get('target_url', 'unknown')` explicitly. Test 1/4 in U3 asserts the resulting history entry has `target_url` populated. |
| The 3-state classifier mis-labels `*_unverified` rows. `_push_history_per_row` preserves these statuses intentionally — for the **banner** state, treat `*_unverified` as "no URL → failed" (consistent with what the helper already does for history). | Document in code: `n_ok` counts only rows with a non-empty URL, regardless of status string. Edge case for `published_unverified` with URL is treated as `n_ok` (URL was returned, just couldn't be re-fetched). Test in U3 covers this. |
| `plan_logger.warn` call adds noise to webui logs on every partial-success. | Use `plan_logger.info` for `all_success`, `plan_logger.warn` only for `partial_success` / `all_failed`. Stderr_preview capped at 200 chars to limit log volume. |
| Migrating the velog precheck to `_push_history_single_failure` writes a synthetic history entry where previously no entry was written. | This is the desired behavior — current "no history entry on precheck failure" is a silent failure. The new entry has `status='failed'` and the velog guide as `error`. Documented in U1. |
| External agent concurrent edits on `pipeline.py` from sibling worktrees (see memory `external_agent_concurrent_edits_in_shared_worktree`). | Implement in a fresh `bp-fix-publish-false-success/` worktree with its own `.venv`, never in main. Commit only `pipeline.py` + `index.html` + the new test file. Do not `git add -A`. |

## Documentation / Operational Notes

- Add a one-paragraph entry in `docs/solutions/` after the fix lands, titled
  `pipeline-publish-route-helper-migration`, with these key points: the bug
  pattern (false green banner + collapsed history entry), the rule
  ("all WebUI publish callsites MUST route through `_push_history_per_row`"),
  and a `grep` recipe to detect future regressions
  (`grep -n "history_store.update" webui_app/routes/`).
- No rollout / monitoring changes — this is a UI / persistence-correctness
  fix, not a behavior expansion. Operator instructions for clicking `发布`
  are unchanged.

## Sources & References

- Defect site: `backlink-publisher/webui_app/routes/pipeline.py:239-291`
- Defect site: `backlink-publisher/webui_app/templates/index.html:1186-1233`
- Canonical helper: `backlink-publisher/webui_app/helpers.py:435-525`
- Reference migrated callsite: `backlink-publisher/webui_app/routes/batch.py:130-190`
- Reference migrated callsite: `backlink-publisher/webui_app/scheduler.py:99-211`
- Memory feedback: `publish_history_invariant_helper` (PR #87/#97 closed
  this bug class for three other callsites)
- Prior plan: `docs/plans/2026-05-19-006-fix-webui-publish-history-truth-propagation-plan.md`
  (Unit 1 — same root-cause fix, different callsites)
- Memory feedback: `webui_log_split_across_restart_files` — operator
  log-discovery context for the diagnostic logging in U1
