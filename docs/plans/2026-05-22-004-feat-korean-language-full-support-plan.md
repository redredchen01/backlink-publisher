---
title: "feat: Korean language full support — corpus calibration, WebUI option, content templates"
type: feat
status: archived
superseded_by: docs/plans/2026-05-22-008-feat-korean-publishing-language-plan.md
date: 2026-05-22
origin: anchor/resolver.py:100, linkcheck/language.py:73, webui_app/templates/_shared_config_selects.html:27
completed: 2026-05-22
claims: {}
---

# feat: Korean Language Full Support

## Summary

Completed Korean (ko) language support across the backlink-publisher pipeline. Three units: corpus calibration, WebUI dropdown, and content templates. All 3788 tests pass.

## Units

### Unit 1 — Corpus calibration

Calibration script measured Hangul ratios against:
- 11 full-length Korean article samples (tech/lifestyle/news/travel/food)
- 30 Korean anchor-like short texts (pure Korean + mixed brand)
- Negative controls: zh-CN, en, ru articles and anchors

**Results:**

| Constant | File | Old | New | Rationale |
|---|---|---|---|---|
| `_RATIO_THRESHOLD` | `linkcheck/language.py:76` | 0.30 | **0.50** | Lowest Korean article was 0.513 (heavy English tech-terms article). 0.50 matches zh-CN CJK standard, provides equal false-positive protection. |
| `_MIN_KO_HANGUL_RATIO` | `anchor/resolver.py:103` | 0.30 | **0.20** | Mixed-brand anchors ("YouTube 채널"=0.222, "Apple 한국"=0.286) need lower threshold. Set 10% below the 0.222 floor. |

Both `# TODO(ko-corpus-calibration)` comments removed.

### Unit 2 — WebUI publishing language dropdown

Added `<option value="ko">한국어 (韩文)</option>` to `webui_app/templates/_shared_config_selects.html`.

### Unit 3 — Korean content templates

| Sub-unit | File | What |
|---|---|---|
| 3a | `_util/_body_templates.py` | `_ko_body_a`, `_ko_body_b`, `_ko_body_c` — each has 2 pool variants for variety |
| 3b | `cli/plan_backlinks/_templates.py` | `_TDK_TITLE_TMPL["ko"]`, `_TEMPLATES["ko"]` (title/excerpt/seo/seo_desc/tags/body) |
| 3c | `cli/plan_backlinks/_links.py` | `language == "ko"` branch with Korean link-density paragraph |
| 3d | `cli/plan_backlinks/_work_themed.py` | `language == "ko"` branch with Korean "further reading" text |

## Files changed

- `src/backlink_publisher/anchor/resolver.py` — threshold 0.30→0.20, updated docstrings, removed TODO
- `src/backlink_publisher/linkcheck/language.py` — threshold 0.30→0.50, updated docstrings, removed TODO
- `webui_app/templates/_shared_config_selects.html` — added ko option
- `src/backlink_publisher/_util/_body_templates.py` — added `_ko_body_a/b/c` with 2-variant pools
- `src/backlink_publisher/cli/plan_backlinks/_templates.py` — updated imports, added `_TDK_TITLE_TMPL["ko"]` and `_TEMPLATES["ko"]`
- `src/backlink_publisher/cli/plan_backlinks/_links.py` — added `language == "ko"` branch
- `src/backlink_publisher/cli/plan_backlinks/_work_themed.py` — added `language == "ko"` branch

## Verification

✅ 3788 tests passed, 4 skipped — zero regressions
