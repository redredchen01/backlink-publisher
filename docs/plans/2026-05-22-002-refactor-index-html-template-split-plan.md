---
title: "refactor(webui): Plan B — index.html template split (CSS + tab partials + JS)"
type: refactor
status: completed
date: 2026-05-22
origin: docs/plans/2026-05-21-006-refactor-webui-comprehensive-optimization-plan.md
claims: {}
---

# Plan B — index.html Template Split

## Overview

`index.html` is 1992 lines: 723 lines of inline `<style>`, 3 tab-pane sections (~409 + 315 + 87 = 811 lines), 307 lines of inline `<script>`, and a ~150-line shell. This plan splits all three chunks into separately-maintainable files.

After all three units land, `index.html` shrinks to ~160 lines (shell only). Tab partials, CSS, and JS become independently reviewable and branchable — the master plan's F3 motivation.

**Baseline**: `origin/main` HEAD `0413f65` (post-Plan 007 Unit 5). Plan 013 B-1.1 (PR #166) already merged — that's the current starting point.

## Problem Frame

F3 from the master plan: `index.html` at 2013→1992 SLOC is the last WebUI mono-template. Every PR that touches the publish flow, history panel, or batch panel creates a conflict surface in the same file. The inline CSS (723 lines, no Jinja) and inline JS (307 lines, 3 Jinja interpolations) compound the problem — designers can't iterate on styles without understanding Jinja scoping, and JS is un-lintable.

## Requirements Trace

- **R1** (master plan R3): Split ships as independently-shippable units; each PR lands cleanly on its own.
- **R2** (master plan R4): `_global_csrf_guard`, `0o600` secret writes, `HIDDEN_FROM_UI`, publish-history invariant — none altered. Template context vars remain unchanged.
- **R3** (master plan R5): After split, the next audit surface for F3 is three small partials rather than one 1992-line monolith.
- **R4**: Post-split `index.html` must be ≤200 lines (shell + includes + script bootstraps). Estimated outcome ~160 lines; ≤200 is the hard ceiling used for CI enforcement.

## Scope Boundaries

- In scope: `webui_app/templates/index.html`, new `_tab_*.html` partials, new `webui_app/static/css/index.css`, new `webui_app/static/js/index_main.js`.
- Out of scope: Behavior changes inside any tab pane or JS function. Settings page (`settings.html`). Any adapter or CLI code.
- Deferred: JS refactoring inside `index_main.js` (Plan E-perf). UX polish (Plan E-polish). Merging `bind_channel.js` and `channel-binding.js` (master plan Open Questions).

## Context & Research

### Relevant Code and Patterns

- `webui_app/templates/index.html:10-733` — inline `<style>` block, **zero Jinja expressions**. Can be moved verbatim to `static/css/index.css`.
- `webui_app/templates/index.html:835-1243` — `#newPanel` tab-pane (~409 lines). Uses Jinja vars: `history_active`, `published`, `validated`, `plans`, `config`, `plans_list`, `extra_urls`, `profiles`, `meta_info`, and more.
- `webui_app/templates/index.html:1244-1558` — `#historyPanel` tab-pane (~315 lines). Uses `history_active`, `draft_queue`, `history`, `platforms`, `history_html`.
- `webui_app/templates/index.html:1559-1645` — `#batchPanel` tab-pane (~87 lines). Uses `profiles`, `platforms`.
- `webui_app/templates/index.html:1646-1953` — inline `<script>` block. Three Jinja interpolations: `{{ plans_list | tojson }}`, `{{ profiles | tojson }}`, `{% for p in platforms %}{{ p.slug }}: 0, {% endfor %}`.
- `webui_app/templates/index.html:1969` — **established bootstrap-data pattern**: `<script>window.__batchTabHint = {{ batch_tab|tojson ... }};</script>`. Unit 3 extends this exact pattern for the three remaining vars.
- `webui_app/static/js/mode_toggle.js` — reads `window.__batchTabHint`; canonical consumer of the bootstrap-data pattern.
- `webui_app/templates/_tab_*.html` — naming convention to follow (matches existing `_settings_channel_*.html` partials).
- `webui_app/static/js/url_derive.js`, `mode_toggle.js` — externalized JS already in repo; no `static/css/` dir exists yet.
- `tests/test_history_template_rendering.py` — uses `webui.app.test_client()` (old pattern). Unit-specific tests should use `create_app()` factory (post-#87 pattern from `test_webui_publish_route.py`).

### Institutional Learnings

- `[[feedback-grep-before-writing-brainstorm-plan-claims]]` — re-verify line ranges at HEAD before implementation; this plan's line numbers reflect `0413f65`.
- `[[feedback-fetch-json-must-guard-content-type]]` — JS fetch hardening already applied in existing `index_main` functions; preserve in extracted file.
- `[[feedback-render-auto-inject-over-per-route]]` — `_render` auto-injects context; partials get the same vars as their parent template via `{% include %}`.
- `[[feedback-settings-channel-collapsed-expand-before-click]]` — Bootstrap collapse state; relevant to `_tab_history.html` accordion (draft-queue collapse).

### External References

Not needed. The CSS extraction is verbatim; the `{% include %}` behavior is standard Jinja2; the `window.__*` bootstrap pattern is already established in this repo.

## Key Technical Decisions

- **`{% include %}` over `{% extends %}` / macros for tab partials.** `{% include %}` shares the parent template's context automatically — all Jinja vars (`config`, `profiles`, `plans_list`, etc.) are available inside the partial without any signature changes. Macros would require threading every variable explicitly. Rationale: zero behavior change with minimum diff; mirrors `_shared_config_selects.html` inclusion already in the file.

- **`window.__indexBootstrap` single JSON blob for JS data injection.** The inline `<script>` has three Jinja vars (`plans_list`, `profiles`, `platform_slugs`). Rather than three separate `window.__*` scalars, inject as one object: `<script>window.__indexBootstrap = {{ {'plans_list': plans_list, 'profiles': profiles, 'platform_slugs': platforms | map(attribute='slug') | list} | tojson }};</script>`. Rationale: (a) one Jinja expression is easier to audit than three; (b) mirrors the structured bootstrap data pattern emerging in the codebase; (c) `index_main.js` destructures from one well-named object. The `window.__batchTabHint` scalar stays as-is (separate JS file, separate pattern).

- **CSS goes to `static/css/index.css`, NOT inlined via `url_for`.** Create `webui_app/static/css/` directory. Link via `<link rel="stylesheet" href="{{ url_for('static', filename='css/index.css') }}">`. No CDN or build step. Rationale: simple, follows existing `url_for('static', filename='js/...')` pattern.

- **Three units ship independently.** CSS (Unit 1) has zero risk. Tab partials (Unit 2) have medium risk (Jinja context). JS externalization (Unit 3) has the highest risk (Jinja-to-JS bridge). Shipping in sequence lets each PR be reviewed and reverted independently.

## Open Questions

### Resolved During Planning

- **Does `{% include %}` share Jinja context?** Yes. Jinja2's `{% include %}` renders the included file with the current template context. All vars from the parent's `_render()` call are available. No context threading needed.
- **Can CSS be extracted verbatim?** Yes. The inline `<style>` block (lines 10-733) contains zero Jinja expressions. It is pure CSS.
- **Are there other Jinja interpolations in the `<script>` block besides the 3 identified?** Verified at `0413f65`: only `{{ plans_list | tojson }}` (line ~1652), `{{ profiles | tojson }}` (line ~1705), and `{% for p in platforms %}{{ p.slug }}: 0, {% endfor %}` (line ~1808). The `window.__batchTabHint` injection (line 1969) is outside the main `<script>` block and stays in place.
- **Does `tests/test_history_template_rendering.py` use the old `webui.app` pattern?** Yes — it imports `webui` directly. This plan does not fix that test's fixture; it only checks that the test still passes after the split.

### Deferred to Implementation

- **Exact line numbers of the three tab-pane boundaries** — re-verify at HEAD before cutting. The numbers above are from `0413f65`; CI-failing behavior is the ground truth detector.
- **Loading Overlay HTML block placement** — the `#_loadingOverlay` div (lines ~1955-1965) sits between the `</script>` and external script tags. Implementer decides whether it stays in `index.html` shell or moves to `_tab_new.html`. Keeping it in the shell is recommended (it's global UI, not tab-specific).
- **`index_main.js` internal structure** — the JS can be reorganized (IIFE groupings, etc.) but this is out of scope. Extract verbatim, then iterate.

## High-Level Technical Design

> *Directional guidance for review, not implementation specification.*

```
index.html (1992 lines today)
  │
  ├─ Unit 1: Extract <style> block
  │    index.html: remove lines 10-733, add <link> tag
  │    webui_app/static/css/index.css: new file, verbatim CSS content
  │
  ├─ Unit 2: Extract tab panes
  │    index.html: replace each <div class="tab-pane" id="XPanel"> block
  │                with {% include '_tab_X.html' %}
  │    _tab_new.html:      #newPanel content (~409 lines)
  │    _tab_history.html:  #historyPanel content (~315 lines)
  │    _tab_batch.html:    #batchPanel content (~87 lines)
  │
  └─ Unit 3: Extract inline <script> block
       index.html: replace lines 1646-1953 with:
         <script>window.__indexBootstrap = {{ {...} | tojson }};</script>
         <script src="...index_main.js"></script>
       webui_app/static/js/index_main.js: new file, verbatim JS with
         3 Jinja vars replaced by window.__indexBootstrap reads

Post-split index.html skeleton (~160 lines):
  <!DOCTYPE html>
  <html>
  <head> ... <link css/index.css> </head>
  <body>
    <nav>...</nav>              ← ~100 lines (nav/alerts/toggle bar/tab nav)
    <div class="tab-content">
      {% include '_tab_new.html' %}
      {% include '_tab_history.html' %}
      {% include '_tab_batch.html' %}
    </div>
    <div id="_loadingOverlay">...</div>
    <script>window.__indexBootstrap = {{ ... | tojson }};</script>
    <script src=".../index_main.js"></script>
    <script>window.__batchTabHint = {{ ... }};</script>
    <script src=".../mode_toggle.js"></script>
    <script>...url_derive wiring...</script>
  </body>
  </html>
```

## Implementation Units

---

- [ ] **Unit 1: Extract inline `<style>` → `static/css/index.css`**

**Goal:** Remove the 723-line inline `<style>` block from `index.html` and serve it as a static CSS file. Zero behavior change.

**Requirements:** R1, R4

**Dependencies:** None.

**Files:**
- Create: `webui_app/static/css/` directory (add `.gitkeep` placeholder so git tracks the directory before the CSS file is committed).
- Modify: `webui_app/templates/index.html` — remove `<style>…</style>` block (lines 10-733 at `0413f65`); add `<link rel="stylesheet" href="{{ url_for('static', filename='css/index.css') }}">` in `<head>`.
- Create: `webui_app/static/css/index.css` — verbatim content of the removed `<style>` block (without the `<style>` tags).
- Test: `tests/test_webui_static_css_served.py` (new) — verify `GET /static/css/index.css` returns 200 with `content-type: text/css`.

**Approach:**
- Pre-step: verify CSS block is Jinja-free at HEAD: `python3 -c "import re,sys; t=open('webui_app/templates/index.html').read(); css=t[t.index('<style>')+7:t.index('</style>')]; sys.exit(bool(re.search(r'{%|{{', css)))"` — exits 0 if clean, exits 1 if Jinja found (stop and investigate).
- Cut lines 10-733 content (not the `<style>`/`</style>` tags themselves) into `index.css`.
- The CSS has zero Jinja expressions; the extraction is verbatim.
- The `<link>` tag goes at the same position (before `</head>`), after the Bootstrap CSS CDN links.
- No changes to any Python route, helper, or test fixture.

**Patterns to follow:**
- `url_for('static', filename='js/url_derive.js')` — existing static file serving in the same template.

**Test scenarios:**
- Happy path: `GET /static/css/index.css` returns 200, `Content-Type: text/css`.
- Happy path: `GET /` renders a page that includes a `<link>` tag pointing to `css/index.css` (assert via `assertIn('<link', resp.data.decode())`).
- Edge case: `GET /` does NOT contain `<style>` anywhere in the response body (assert inline style is fully removed).
- Integration: Full WebUI smoke — open `/` in browser; page renders without visual regression (no unstyled content).

**Verification:**
- `grep -n "<style>" webui_app/templates/index.html` returns zero matches.
- `GET /static/css/index.css` → 200.
- `wc -l webui_app/templates/index.html` drops by ~725 lines.

---

- [ ] **Unit 2: Extract 3 tab panes → `_tab_*.html` partials**

**Goal:** Move the three `<div class="tab-pane …" id="XPanel">` blocks into `_tab_new.html`, `_tab_history.html`, and `_tab_batch.html`. Replace each with `{% include '_tab_X.html' %}`.

**Requirements:** R1, R2, R4

**Dependencies:** Unit 1 (recommended to land first for a smaller diff; technically independent).

**Files:**
- Modify: `webui_app/templates/index.html` — replace each tab-pane div (including its closing `</div>`) with the corresponding `{% include %}` tag.
- Create: `webui_app/templates/_tab_new.html` — `#newPanel` content (~409 lines at `0413f65`).
- Create: `webui_app/templates/_tab_history.html` — `#historyPanel` content (~315 lines).
- Create: `webui_app/templates/_tab_batch.html` — `#batchPanel` content (~87 lines).
- Test: `tests/test_webui_index_template_structure.py` (new) — uses `create_app()` factory (post-#87 pattern); NOT the legacy `webui.app` fixture.

**Approach:**
- Each extracted partial starts with the opening `<div class="tab-pane …" id="…">` and ends with the matching `</div><!-- End XPanel tab -->`.
- `{% include %}` inherits the parent template context; no Jinja var threading required.
- The outer `<div class="tab-content" id="mainTabsContent">` wrapper stays in `index.html`.
- Preserve all existing HTML comments (they aid future developers navigating the split files).

**Patterns to follow:**
- `webui_app/templates/_shared_config_selects.html` — existing partial included by the same template.
- `webui_app/templates/_settings_channel_*.html` — naming convention.

**Test scenarios:**
- Happy path (create_app() fixture): `GET /` returns 200 and response body contains `id="newPanel"`, `id="historyPanel"`, `id="batchPanel"` — confirms all three `{% include %}` calls render successfully.
- Happy path: `GET /` with `?section=history` parameter → `historyPanel` is rendered as the active tab (verifies `history_active` Jinja var still reaches the partial via `{% include %}` context sharing).
- Edge case: Empty history store → `GET /` renders `id="historyPanel"` with empty-state placeholder (no crash from missing context vars in partial).
- Edge case: `profiles` list is empty → `GET /` still renders `#batchPanel` without error.
- Integration: `test_history_template_rendering.py` (legacy `webui.app` fixture) still passes unmodified — used as a smoke signal for any `UndefinedError` in partials; run immediately after Unit 2 merges.

**Verification:**
- `grep -n "tab-pane" webui_app/templates/index.html` returns zero matches (panes moved to partials).
- `python3 -m py_compile webui_app/templates/*.html` — n/a for templates, but Jinja2 parse check: `from jinja2 import Environment; env.parse(open('…').read())` succeeds for all four files.
- `wc -l webui_app/templates/index.html` drops by ~810 more lines.

---

- [ ] **Unit 3: Extract inline `<script>` → `static/js/index_main.js`**

**Goal:** Move the 307-line inline `<script>` block to `static/js/index_main.js`. Replace 3 Jinja interpolations inside the block with reads from `window.__indexBootstrap`, injected via a tiny bootstrap `<script>` tag in the template.

**Requirements:** R1, R2, R4

**Dependencies:** Unit 2 (recommended after partials land so Unit 3 PR is diff-minimal; technically independent).

**Files:**
- Modify: `webui_app/templates/index.html` — replace lines 1646-1953 (`<script>…</script>` block) with:
  ```
  <script>window.__indexBootstrap = {{ {'plans_list': plans_list, 'profiles': profiles, 'platform_slugs': platforms | map(attribute='slug') | list} | tojson }};</script>
  <script src="{{ url_for('static', filename='js/index_main.js') }}"></script>
  ```
- Create: `webui_app/static/js/index_main.js` — verbatim JS content with the 3 Jinja interpolations replaced (see Approach below).
- Test: `tests/test_webui_index_js_bootstrap.py` (new).

**Approach:**

- Pre-step: enumerate ALL Jinja expressions in the script block to confirm exactly the 3 known interpolations (catch drift since `0413f65`):
  ```
  python3 -c "
  import re
  t = open('webui_app/templates/index.html').read().splitlines()
  block = '\n'.join(t[1645:1953])
  for m in re.finditer(r'{{.*?}}|{%.*?%}', block, re.DOTALL):
      print(repr(m.group()))
  "
  ```
  Expected: exactly 3 matches — `{{ plans_list | tojson }}`, `{{ profiles | tojson }}`, and the `{% for p in platforms %}...{% endfor %}` block. If any additional expressions appear, add them to `window.__indexBootstrap` before extracting.

*Jinja-to-JS bridge:* The three Jinja expressions replaced by `window.__indexBootstrap` reads:

1. `{% if plans_list %}\nlet _plansData = {{ plans_list | tojson }};\n{% else %}\nlet _plansData = [];\n{% endif %}`
   → `const _plansData = (window.__indexBootstrap && window.__indexBootstrap.plans_list) || [];`

2. `const _PROFILES = {{ profiles | tojson }};`
   → `const _PROFILES = (window.__indexBootstrap && window.__indexBootstrap.profiles) || [];`

3. `platform: { all: 0, {% for p in platforms %}{{ p.slug }}: 0, {% endfor %}other: 0 }`
   → Build the platform counter object dynamically from `window.__indexBootstrap.platform_slugs`:
   ```js
   const _platformSlugs = (window.__indexBootstrap && window.__indexBootstrap.platform_slugs) || [];
   const _platCounts = Object.fromEntries([['all',0], ..._platformSlugs.map(s=>[s,0]), ['other',0]]);
   ```
   Then use `counts.platform = _platCounts` in the `initCounts` function.

*No other changes.* All function names, DOM selectors, fetch patterns, and CSRF header reads stay verbatim. The extraction is behavior-preserving.

> *Directional guidance for the bridge pattern — not copy-paste specification. Implementer should verify that `_PROFILES` and `_plansData` are read before any IIFE or event listener that consumes them, which they are (the inline block renders before DOMContentLoaded listeners).*

**Patterns to follow:**
- `window.__batchTabHint` / `mode_toggle.js` — established bootstrap-data pattern in this repo.
- `[[feedback-fetch-json-must-guard-content-type]]` — all `fetch()` calls in the block already guard content-type; preserve in extracted file.

**Test scenarios:**
- Happy path: `GET /` renders a `<script>` tag containing `window.__indexBootstrap` with `plans_list`, `profiles`, and `platform_slugs` keys (assert via response body parse).
- Happy path: `GET /` does NOT contain `let _plansData = ` or `const _PROFILES = ` (assert Jinja interpolation is fully removed from inline HTML).
- Happy path: `GET /static/js/index_main.js` returns 200 with `content-type: application/javascript`.
- Edge case: `plans_list` is `None` or absent in context → `window.__indexBootstrap.plans_list` is `null` → `_plansData` falls back to `[]` (no JS error on page load).
- Edge case: `platforms` list is empty → `window.__indexBootstrap.platform_slugs` is `[]` → history filter initializes with only `all` + `other` buckets.
- Integration: Manual smoke — trigger an `/ce:generate` call; verify the loading overlay fires (the submit event listener in `index_main.js` must still work). Verify history filter chips update counts correctly with a non-empty history.
- Integration: `csrf_client` fixture — verify that CSRF token is still read correctly from `<meta name="csrf-token">` inside `index_main.js` (the `saveProfile` function reads `document.querySelector('meta[name="csrf-token"]').content`).
- Integration equivalence: render `GET /` with `plans_list=[{"content_markdown": "x"}]` and `profiles=[{"name": "p"}]` injected into context (use `monkeypatch` on `_render`); parse `window.__indexBootstrap = ...` from the response body; assert `bootstrap["plans_list"]` equals the injected value and `bootstrap["profiles"]` equals the injected value. Confirms the Jinja→JS bridge carries the same data as the original inline interpolations.

**Verification:**
- `grep -n "<script>" webui_app/templates/index.html` returns only 3-4 lines (bootstrap var injection + external src tags + url_derive wiring).
- `GET /static/js/index_main.js` → 200.
- `wc -l webui_app/templates/index.html` final ≤200.
- Manual: open `/`, trigger publish flow, observe loading overlay fires; history filter works.

---

## System-Wide Impact

- **Interaction graph:** All three units are pure decomposition — no route logic, no Python helper, no CLI path is touched. The Flask static file serving (`send_from_directory`) is already configured for `webui_app/static/`.
- **Error propagation:** If `static/css/index.css` or `static/js/index_main.js` 404s, the WebUI degrades gracefully (unstyled or non-interactive) but does not 500. The bootstrap `<script>` tag is inline and always renders.
- **State lifecycle risks:** `{% include %}` renders at template-render time; it does not introduce lazy loading or deferred evaluation. Context vars available to `index.html` are fully available inside every partial.
- **API surface parity:** No route contracts change. The `window.__indexBootstrap` bootstrap var is client-side only; no server-side endpoint exposes it.
- **Bootstrap data safety:** `window.__indexBootstrap` carries operator-generated config data (`plans_list`, `profiles`, `platform_slugs`). The WebUI is localhost-only (trust boundary locked per master plan § Trust Boundary). DOM insertion in `index_main.js` must use `textContent`, not `innerHTML`, for any displayed string fields from the bootstrap object — the existing code already follows this pattern; preserve it on extraction.
- **Integration coverage:** The existing `test_history_template_rendering.py` renders the index via test client — it will exercise the `{% include %}` mechanism. The new Unit 2 and 3 tests complement this.
- **Unchanged invariants:** `_global_csrf_guard`, publish-history invariant, `HIDDEN_FROM_UI`, 0o600 secret writes — none of these are touched. The CSRF token meta tag (`<meta name="csrf-token" content="{{ csrf_token }}">`) stays in `index.html` shell; `index_main.js` reads it via `document.querySelector('meta[name="csrf-token"]')` exactly as today.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `{% include %}` context leak — partial references a var not injected by `_render` | Run existing `test_history_template_rendering.py` immediately after Unit 2 lands; any `UndefinedError` surfaces there |
| JS `window.__indexBootstrap` read-before-set race | Bootstrap `<script>` tag is before `<script src="index_main.js">` in DOM order; synchronous execution guarantees the var is set before the JS file runs |
| `static/css/` directory doesn't exist — Flask static serving silently fails | Create the directory as part of Unit 1; verify `GET /static/css/index.css` returns 200 in CI test |
| Platform slug iteration change in Unit 3 breaks history filter chip counts | Add explicit test scenario for `platform_slugs: []` and `platform_slugs: ['blogger', 'telegraph']`; compare chip counts before/after |
| PR conflicts between Unit 2 and Unit 3 if developed in parallel | Ship sequentially: Unit 1 → Unit 2 → Unit 3. Each PR is diff-minimal once the previous lands |

## Documentation / Operational Notes

- No CLAUDE.md or AGENTS.md update needed — the split is internal to `webui_app/templates/` and `webui_app/static/`.
- After Unit 3 lands, update the master plan's Finding F3 row from "Plan B" to "DONE: PR #NNN".
- The master plan deferred: "Whether to merge `bind_channel.js` and `channel-binding.js`" — still deferred, not in scope here.

## Sources & References

- **Origin document:** [docs/plans/2026-05-21-006-refactor-webui-comprehensive-optimization-plan.md](docs/plans/2026-05-21-006-refactor-webui-comprehensive-optimization-plan.md) — F3 finding, Plan B section.
- **Baseline commit:** `0413f65` (origin/main, post-Plan 007 Unit 5 / PR #180).
- **Prior plans:** Plan 012 (PR #162, WebUI IA Phase B-1), Plan 013 B-1.1 (PR #166, mode_toggle extensions).
- **Related code:** `webui_app/templates/index.html`, `webui_app/static/js/mode_toggle.js`, `webui_app/static/js/url_derive.js`.
