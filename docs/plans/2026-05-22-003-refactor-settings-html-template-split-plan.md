---
title: "refactor(webui): Plan B2 — settings.html template split (CSS + JS + card partials)"
type: refactor
status: completed
date: 2026-05-22
origin: docs/plans/2026-05-21-006-refactor-webui-comprehensive-optimization-plan.md
---

# Plan B2 — settings.html Template Split

## Overview

`settings.html` is 1060 lines: 69-line inline `<style>`, 416-line inline `<script>` (2 Jinja interpolations), and a ~575-line HTML shell. The shell is already well-factored (9 existing `{% include %}` calls for channel cards), but three large inline card sections remain: LLM integration (~124 lines), diagnostics (~22 lines), and AI Banner (~82 lines).

After all three units land, `settings.html` shrinks to ~345 lines (67% reduction). The `{% include %}`-based decomposition pattern established by Plan B (`index.html`) applies directly.

**Baseline**: `origin/main` HEAD `0413f65`. Simpler than Plan B — only 2 Jinja vars in the JS block (vs 3 in index.html) and no platform-slug loop.

**Dependency on Plan B**: `webui_app/static/css/` directory is created by Plan B Unit 1. If Plan B Unit 1 has not yet landed when this plan's Unit 1 executes, create the directory here too.

## Problem Frame

`settings.html` is the second-largest WebUI template after `index.html`. It causes the same PR-conflict problems on any change touching channel binding, LLM config, or banner generation. The inline CSS is unlintable, the JS is unreviewable without understanding Jinja scoping, and the LLM/banner card sections are large enough that a bug fix requires reading 100+ lines of unrelated context.

## Requirements Trace

- **R1**: Split ships as independently-shippable units; each PR lands cleanly on its own.
- **R2**: `_global_csrf_guard`, publish-history invariant, `HIDDEN_FROM_UI` — none altered. Template context vars remain unchanged.
- **R3**: After split, settings.html ≤400 lines (estimated ~345 lines; hard ceiling ≤400).

## Scope Boundaries

- In scope: `webui_app/templates/settings.html`, new `_settings_*.html` partials (3 cards), `webui_app/static/css/settings.css`, `webui_app/static/js/settings_main.js`.
- Out of scope: Changes to any channel card partial already extracted (`_settings_channel_*.html`, `_settings_global_*.html`, `_settings_channel_binding.html`). Settings route logic. LLM endpoint logic.
- Deferred: Extracting the 9 channel card **wrappers** (the collapse scaffold around existing includes) — each is ~28 lines with per-channel conditional logic; ROI is low given they're already tiny.

## Context & Research

### Relevant Code and Patterns

- `webui_app/templates/settings.html:10-79` — inline `<style>` block, **zero Jinja expressions**. Verbatim extraction.
- `webui_app/templates/settings.html:628-1044` — inline `<script>` block. Exactly **two** Jinja interpolations: `{{ plans_list | tojson }}` (line ~824) and `{{ profiles | tojson }}` (line ~881). Both are the same vars as Plan B's index.html JS — no platform-slug loop complication.
- `webui_app/templates/settings.html:384-507` — `<!-- ⑥ 进阶 LLM 整合 -->` card (~124 lines, no Jinja that isn't context vars); extract to `_settings_llm_integration.html`.
- `webui_app/templates/settings.html:508-529` — `<!-- ⑦ 生成诊断控制台 -->` card (~22 lines); extract to `_settings_diagnostics.html`.
- `webui_app/templates/settings.html:530-611` — `<!-- ⑦ AI Banner 图生成 -->` card (~82 lines); extract to `_settings_banner.html`.
- `webui_app/templates/settings.html:1046-1047` — already externalized: `<script src="/static/js/bind_channel.js" defer>` and `channel-binding.js`. The inline JS block is for other settings logic (LLM test, profile management, blog-id row management) — these are separate from the bind JS.
- `webui_app/static/js/mode_toggle.js`, `url_derive.js` — existing externalized JS; same `url_for('static', ...)` serving pattern.
- `webui_app/templates/_settings_channel_blogger.html` — existing partial; naming convention to follow.
- `webui_app/templates/index.html:1969` — `window.__batchTabHint` bootstrap pattern; `window.__indexBootstrap` bridge being added by Plan B; Plan B2 adds `window.__settingsBootstrap` using the same shape.

### Institutional Learnings

- `[[feedback-grep-before-writing-brainstorm-plan-claims]]` — re-verify line numbers at HEAD before cutting.
- `[[feedback-fetch-json-must-guard-content-type]]` — JS fetch calls; settings.html has the LLM test-connection `fetch()` in the inline block; preserve content-type guard in extracted file.
- `[[feedback-render-auto-inject-over-per-route]]` — `_render` auto-injects context; partials inherit parent context via `{% include %}`.
- `[[feedback-settings-channel-collapsed-expand-before-click]]` — Bootstrap collapse state is central to settings.html; channel card collapse wrappers remain inline and are not touched by this plan.

### External References

None needed. Identical technology and patterns to Plan B.

## Key Technical Decisions

- **Same `{% include %}` + `window.__settingsBootstrap` approach as Plan B.** `{% include %}` shares parent context; the 2 Jinja vars in the JS block become `window.__settingsBootstrap = {{ {'plans_list': plans_list, 'profiles': profiles} | tojson }};`. This is simpler than Plan B (no platform-slug loop). The `window.__indexBootstrap` and `window.__settingsBootstrap` are independent objects on separate pages — no reuse attempt.

- **CSS to `static/css/settings.css`, NOT merged with `index.css`.** The two pages' styles are distinct and independently maintainable. A future design pass can consolidate if warranted; premature merge adds risk for zero gain.

- **Three card partials in one Unit 3 PR.** LLM integration, diagnostics, and banner are adjacent sections with no Jinja dependency between them. Extracting all three in one PR reduces PR count without coupling risk surfaces — each partial is independently `{% include %}`-able.

- **Three units ship independently.** CSS (Unit 1) zero risk. JS (Unit 2) low risk (only 2 Jinja vars). Card partials (Unit 3) medium risk (Jinja context + Bootstrap collapse state proximity). Sequential shipping.

## Open Questions

### Resolved During Planning

- **Do `plans_list` and `profiles` appear in both index.html and settings.html JS blocks?** Yes — verified at `0413f65`. Same var names, same Jinja filter (`| tojson`). Both pages' JS use them for the campaign-profile sidebar. Settings.html does NOT have the `{% for p in platforms %}` loop that index.html has.
- **Are `bind_channel.js` / `channel-binding.js` already external?** Yes — lines 1046-1047 in settings.html. The inline JS block is LLM/profile/blog-id logic only.
- **Does `webui_app/static/css/` directory exist before this plan runs?** Plan B Unit 1 creates it. This plan's Unit 1 should check and create if absent.

### Deferred to Implementation

- **Exact line numbers** — re-verify at HEAD before cutting; this plan's numbers reflect `0413f65`.
- **Whether `settings_main.js` and `index_main.js` share any extractable functions** — both will have `loadProfile()` variants; a future shared-utils extraction is YAGNI until both exist.

## Implementation Units

---

- [ ] **Unit 1: Extract inline `<style>` → `static/css/settings.css`**

**Goal:** Remove the 69-line inline `<style>` block and serve it as a static CSS file.

**Requirements:** R1, R3

**Dependencies:** None. (Verify `webui_app/static/css/` exists from Plan B Unit 1; create with `.gitkeep` if not.)

**Files:**
- Create: `webui_app/static/css/settings.css` — verbatim CSS content (without `<style>` tags).
- Modify: `webui_app/templates/settings.html` — remove `<style>…</style>` block (lines 10-79); add `<link rel="stylesheet" href="{{ url_for('static', filename='css/settings.css') }}">` in `<head>`.
- Test: `tests/test_webui_static_css_served.py` — add test case for `GET /static/css/settings.css` → 200 `text/css` (extend existing test file from Plan B Unit 1, or create if Plan B Unit 1 hasn't landed).

**Approach:**
- Pre-step: `python3 -c "import re,sys; t=open('webui_app/templates/settings.html').read(); css=t[t.index('<style>')+7:t.index('</style>')]; sys.exit(bool(re.search(r'{%|{{', css)))"` — exits 0 if clean.
- Cut content (not the `<style>`/`</style>` tags) into `settings.css`. Verbatim extraction.
- `<link>` tag goes after the Bootstrap CDN link, before `</head>`.

**Patterns to follow:**
- Plan B Unit 1 (`static/css/index.css`) — identical mechanics.

**Test scenarios:**
- Happy path: `GET /static/css/settings.css` → 200, `Content-Type: text/css`.
- Happy path: `GET /settings` response body contains `<link` pointing to `css/settings.css`.
- Edge case: `GET /settings` does NOT contain `<style>` in response body.

**Verification:**
- `grep -n "<style>" webui_app/templates/settings.html` → zero matches.
- `wc -l webui_app/templates/settings.html` drops by ~71 lines.

---

- [ ] **Unit 2: Extract inline `<script>` → `static/js/settings_main.js`**

**Goal:** Move the 416-line inline `<script>` block to `static/js/settings_main.js`. Replace 2 Jinja interpolations with `window.__settingsBootstrap` reads.

**Requirements:** R1, R2, R3

**Dependencies:** Unit 1 (recommended; technically independent).

**Files:**
- Modify: `webui_app/templates/settings.html` — replace inline `<script>…</script>` block with:
  ```
  <script>window.__settingsBootstrap = {{ {'plans_list': plans_list, 'profiles': profiles} | tojson }};</script>
  <script src="{{ url_for('static', filename='js/settings_main.js') }}"></script>
  ```
- Create: `webui_app/static/js/settings_main.js` — verbatim JS with 2 Jinja replacements (see Approach).
- Test: `tests/test_webui_settings_js_bootstrap.py` (new).

**Approach:**

Pre-step: enumerate ALL Jinja expressions in the inline `<script>` block to confirm exactly 2:
```
python3 -c "
import re
t = open('webui_app/templates/settings.html').read().splitlines()
# Find script block start/end
s_start = next(i for i,l in enumerate(t) if re.match(r'\s*<script>\s*$',l))
s_end = next(i for i,l in enumerate(t) if '</script>' in l and i > s_start)
block = '\n'.join(t[s_start:s_end+1])
for m in re.finditer(r'{{.*?}}|{%.*?%}', block, re.DOTALL):
    print(repr(m.group()))
"
```
Expected: exactly 2 matches — `{{ plans_list | tojson }}` and `{{ profiles | tojson }}`.

*Jinja-to-JS bridge (2 replacements):*

1. `{% if plans_list %}\nlet _plansData = {{ plans_list | tojson }};\n{% else %}\nlet _plansData = [];\n{% endif %}`
   → `const _plansData = (window.__settingsBootstrap && window.__settingsBootstrap.plans_list) || [];`

2. `const _PROFILES = {{ profiles | tojson }};`
   → `const _PROFILES = (window.__settingsBootstrap && window.__settingsBootstrap.profiles) || [];`

> *Directional guidance — not implementation specification. Verify `_PROFILES` and `_plansData` are read before any event listener that consumes them.*

**Patterns to follow:**
- Plan B Unit 3 (`window.__indexBootstrap` bridge in `static/js/index_main.js`) — identical pattern, simpler (2 vars vs 3).
- `window.__batchTabHint` / `mode_toggle.js` — established bootstrap-data pattern.

**Test scenarios:**
- Happy path: `GET /settings` response body contains `window.__settingsBootstrap` with `plans_list` and `profiles` keys.
- Happy path: `GET /settings` does NOT contain `let _plansData = ` or `const _PROFILES = ` (Jinja removed from HTML).
- Happy path: `GET /static/js/settings_main.js` → 200 `application/javascript`.
- Edge case: `plans_list` is `None` in context → `window.__settingsBootstrap.plans_list` is `null` → `_plansData` falls back to `[]`.
- Integration equivalence: render `GET /settings` with `plans_list=[{"content_markdown":"x"}]` and `profiles=[{"name":"p"}]` injected into context; parse `window.__settingsBootstrap` from response; assert both keys match injected values.
- Integration: LLM test-connection `fetch()` in `settings_main.js` still fires correctly — `saveSettings()` / `testLlmConnection()` functions are exercised via manual smoke test.

**Verification:**
- `grep -n "<script>" webui_app/templates/settings.html` → only 2-3 lines (bootstrap tag + external src tags).
- `GET /static/js/settings_main.js` → 200.
- `wc -l webui_app/templates/settings.html` drops by ~418 more lines (post-Unit 2 total ≈ ~571 lines).

---

- [ ] **Unit 3: Extract 3 large card sections → `_settings_llm_integration.html`, `_settings_diagnostics.html`, `_settings_banner.html`**

**Goal:** Extract the three remaining large inline card sections (LLM integration ~124 lines, diagnostics ~22 lines, AI Banner ~82 lines) into `{% include %}` partials. Saves ~228 lines from the HTML shell.

**Requirements:** R1, R2, R3

**Dependencies:** Unit 2 (recommended; technically independent).

**Files:**
- Create: `webui_app/templates/_settings_llm_integration.html` — `<!-- ⑥ 进阶 LLM 整合 -->` card, lines 384-507 at `0413f65`.
- Create: `webui_app/templates/_settings_diagnostics.html` — `<!-- ⑦ 生成诊断控制台 -->` card, lines 508-529 at `0413f65`.
- Create: `webui_app/templates/_settings_banner.html` — `<!-- ⑦ AI Banner 图生成 -->` card, lines 530-611 at `0413f65`.
- Modify: `webui_app/templates/settings.html` — replace each card block with the corresponding `{% include %}` tag.
- Test: `tests/test_webui_settings_template_structure.py` (new) — uses `create_app()` factory; NOT legacy `webui.app` fixture.

**Approach:**
- Each extracted partial starts with the opening `<!-- comment -->` and `<div class="card">` and ends with the matching `</div>`.
- `{% include %}` inherits parent context — `llm_settings`, `image_gen_status`, `config_path`, and all other template vars are available.
- Preserve all existing HTML comments.
- The `<!-- ⑦ AI Banner -->` section contains `imageGenTestResult` div and references JS functions from `settings_main.js` (Unit 2) — no additional wiring needed since JS runs at page load.

**Patterns to follow:**
- `_settings_channel_blogger.html`, `_settings_global_keywords.html` — existing settings partials.
- Plan B Unit 2 (`_tab_*.html` for index.html) — same `{% include %}` extraction mechanics.

**Test scenarios:**
- Happy path (create_app() fixture): `GET /settings` → 200 with body containing LLM card header text (`进阶 LLM 整合`), diagnostics card header text (`生成诊断控制台`), and banner card element (`AI Banner 图生成`).
- Edge case: `llm_settings` is `None` → `GET /settings` renders without `UndefinedError` (LLM card must handle missing `llm_settings` gracefully — verify existing conditional logic is preserved in partial).
- Edge case: `image_gen_status` is empty dict → `GET /settings` renders banner card without error.
- Integration: Existing `test_webui_unit3_security.py` still passes (covers `settings_test_llm` route which is tied to the LLM card).

**Verification:**
- `grep -n "⑥ 进阶 LLM\|⑦ 生成诊断\|⑦ AI Banner" webui_app/templates/settings.html` → zero matches (content moved to partials).
- `wc -l webui_app/templates/settings.html` final ≤400 (estimated ~343 lines).

---

## System-Wide Impact

- **Interaction graph:** All three units are pure decomposition — no route logic, no Python helper, no CLI path is touched. The bind JS (`bind_channel.js`, `channel-binding.js`) remains externalized and is not affected.
- **Error propagation:** If `settings.css` or `settings_main.js` 404s, settings page degrades gracefully (unstyled or partially non-interactive) but does not 500. The inline `<script>window.__settingsBootstrap...>` renders before the external JS file.
- **State lifecycle risks:** `{% include %}` partials render at template-render time. The Bootstrap collapse state for channel cards (lines 127-382) is NOT touched by Unit 3; only lines 384+ are extracted.
- **Bootstrap data safety:** `window.__settingsBootstrap` carries operator-generated config (`plans_list`, `profiles`). Localhost-only trust boundary applies (master plan § Trust Boundary). `settings_main.js` must use `textContent` not `innerHTML` for any displayed string fields.
- **API surface parity:** No route contracts change. `window.__settingsBootstrap` is client-side only.
- **Unchanged invariants:** `_global_csrf_guard` not altered. CSRF token meta tag stays in `settings.html` shell. All 9 existing `{% include %}` calls unaffected.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `webui_app/static/css/` doesn't exist if Plan B Unit 1 hasn't landed | Unit 1 Approach includes a creation check; add `.gitkeep` if creating the directory |
| `settings_main.js` shares function names with `index_main.js` (e.g., `loadProfile`) — collision if both loaded | They're on separate pages; no collision possible. If a shared utils file is created later, that's a separate plan |
| LLM card partial references `llm_settings` var that may be `None` | Existing inline code handles this with `{% if llm_settings and ... %}`; the partial preserves that conditional exactly |
| Plan B Unit 2 / Unit 3 and Plan B2 Unit 2 / Unit 3 race in the same worktree | Ship Plan B fully before starting Plan B2; or use separate `bp-settings-split` worktree |

## Documentation / Operational Notes

- After Unit 3 lands, `settings.html` partial count grows from 9 to 12 includes.
- No CLAUDE.md or AGENTS.md update needed.
- Update master plan F3 note: "settings.html split: DONE PR #NNN" alongside the index.html entry.

## Sources & References

- **Origin document:** [docs/plans/2026-05-21-006-refactor-webui-comprehensive-optimization-plan.md](docs/plans/2026-05-21-006-refactor-webui-comprehensive-optimization-plan.md) — F3 finding (settings.html is the second large mono-template).
- **Parallel plan:** [docs/plans/2026-05-22-002-refactor-index-html-template-split-plan.md](docs/plans/2026-05-22-002-refactor-index-html-template-split-plan.md) — Plan B (index.html), establishes all patterns.
- **Baseline commit:** `0413f65` (origin/main, post-Plan 007 Unit 5 / PR #180).
- **Related code:** `webui_app/templates/settings.html`, `webui_app/static/js/bind_channel.js`, `webui_app/static/js/channel-binding.js`.
