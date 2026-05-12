---
date: 2026-05-12
mode: autofix
plan: docs/plans/2026-05-12-005-feat-real-publish-verification-plan.md
branch: feat/real-publish-verification
base: 4b3252f57cd074499a78427957cbdda2d3a9518a
---

# Code Review Run — Real-Publish Verification

## Scope

7 commits on `feat/real-publish-verification` (3830 insertions / 24 deletions across 13 files). Adds:
- `src/backlink_publisher/verifier.py` (new)
- `src/backlink_publisher/cli/publish_backlinks.py` (refactored — verifier integration + exit-code max)
- `src/backlink_publisher/adapters/base.py` (additive JSONL fields + `_provider_meta`)
- `src/backlink_publisher/adapters/blogger_api.py` (`_get_service` + insert-response capture)
- 6 test files (~150 new tests; full suite 301 passing post-fix)

## Reviewers

Dispatched in parallel (haiku-tier model):
- correctness-reviewer (always)
- testing-reviewer (always)
- maintainability-reviewer (always)
- security-reviewer — verifier IS a security control (SSRF, TLS, redirect, credential sanitizer, log injection)
- reliability-reviewer — retry budgets, exception wrapping, exit-code rule
- adversarial-reviewer — large diff (>3000 lines), external API surface
- kieran-python-reviewer — Python-only stack
- api-contract-reviewer — additive JSONL fields, exit-code semantics drift

## Applied Safe Auto-Fixes (7)

| # | Finding | Reviewer | Confidence | File |
|---|---------|----------|------------|------|
| F1 | `_get_blogger_service` no longer caches None — transient OAuth failure won't poison rest of batch | correctness/reliability/adversarial | 0.85+ | `cli/publish_backlinks.py` |
| F2 | `_sanitize_exception` extended with regex patterns for ya29.*, JWT, sk-*, AIza*, case-insensitive needles | security | 0.82 | `verifier.py` |
| F3 | SSRF check now rejects CGNAT (100.64/10), 6to4 anycast (192.88.99/24), 6to4 prefix (2002::/16), Teredo (2001::/32) | security | 0.78 | `verifier.py` |
| F4 | `_read_body_bounded` no longer does unguarded probe-byte read — cap-exactly is treated as too-large to preserve wall-clock guarantee | reliability | 0.85 | `verifier.py` |
| F7 | R17 summary line gated on `outputs` truthiness — empty-batch no longer emits "0/0/0" before exit-5 | correctness/adversarial | 0.70/0.95 | `cli/publish_backlinks.py` |
| F8 | `verified_null_count` uses `else` instead of guarded `elif err_str:` — drops silent-skip path | correctness | 0.75 | `cli/publish_backlinks.py` |
| F9 | `_SafeRedirectHandler` blocks HTTPS → HTTP downgrade redirects | adversarial | 0.72 | `verifier.py` |

## Validation Tests Added

6 new tests cover the fix paths:
- `tests/test_verifier_core.py::test_sanitize_strips_google_oauth_access_token`
- `tests/test_verifier_core.py::test_sanitize_strips_jwt_shape`
- `tests/test_verifier_core.py::test_sanitize_strips_sk_prefix_key`
- `tests/test_verifier_core.py::test_sanitize_case_insensitive_needles`
- `tests/test_verifier_html_channel.py::test_resolved_ip_rejects_cgnat`
- `tests/test_verifier_html_channel.py::test_resolved_ip_rejects_6to4_anycast`

Full suite (excluding pre-existing AppleScript test): **301 passed in 6.31s**.

## Residual Actionable Work (manual, P1 — surfaced but not auto-fixed)

### M1 — `_ArticleScopedCollector` stack desync on unbalanced inner-container tags
Three reviewers flagged this (correctness 0.78, adversarial 0.82, maintainability 0.74). Real Medium HTML often has mismatched `<div>`/`<section>` tags; the article-tag stack can stay open for the rest of the document, weakening the title-in-sidebar and href-in-state-blob defenses. **Recommendation:** redesign as depth-counter or "outermost only" tracking + add fuzz test feeding random tag streams. Deferred to follow-up because the fix is a design change, not deterministic.

### M2 — URL canonicalization mismatch in target_link comparison
Adversarial 0.84. Markdown's `https://example.com` may render as `<a href="https://example.com/">` (trailing slash) — strict set equality fails, marks verified=false on legitimately-published content. Needs operator-input policy: how lenient? **Recommendation:** normalize host + path before comparison, drop tracking query params. Deferred because the right relaxation is policy, not auto-applicable.

### M3 — Cross-module underscore-prefixed imports
Four reviewers flagged (`_safe_for_log`, `_ERR_INTERNAL_PREFIX`, `_get_service`, `_provider_meta`). Naming convention violation. Cheap rename across module boundary but touches public surface — better as a deliberate "promote to public API" commit. Deferred.

### M4 — README exit-code table not updated for new precedence
api-contract 0.9. Exit code 3 now log-and-continues; 4 saturates 3. Cron consumers branching on exit code need to know. **Recommendation:** update README in a follow-up doc-only commit before downstream consumers rely on the new behavior.

### M5 — `_get_blogger_service` OAuth interactive-flow timeout risk
reliability 0.82. `_build_credentials` may invoke `InstalledAppFlow.run_local_server` which blocks indefinitely. Pre-existing risk (publisher path already had it) but verifier increased the attack surface. **Recommendation:** add `--non-interactive` flag or detect `not sys.stdin.isatty()` in a follow-up.

## Advisory (low priority, surface only)

- TLS minimum version pin (security 0.60) — `ssl.create_default_context()` defaults to 1.2 on supported Python (3.10+). Add `ctx.minimum_version = TLSv1_2` for belt-and-suspenders.
- Wall-clock retry budget docs misleading (reliability 0.85) — comment claims ≤30s; actual worst case ~90s with retry waits + per-attempt fetch budgets. Update comment in a follow-up.
- `_dispatch` unreachable defensive branch (maintainability/kieran) — replace with `assert_never`.
- Three Medium adapter entries in `_ADAPTER_METADATA` are byte-identical — consolidate.
- DNS-rebinding TOCTOU between pre-flight `_check_resolved_ip_safe` and urllib's connect — mitigated by host allowlist; documented residual risk.
- Medium paywall/cookie-wall produces verified=false on legitimate articles (adversarial 0.66) — plan-acknowledged tradeoff.

## Coverage

- 8 reviewers ran, all returned valid JSON.
- 12 P0/P1 findings surfaced; 7 applied as safe-auto, 5 deferred as manual residuals.
- Pre-existing finding: env-var int() parse crash (adversarial 0.92) — flagged but pre-dates this branch.
- Suppressed: 4 findings below 0.60 confidence (TLS pin, file-split structure, comment density nits, test file split).

## Verdict

**Ready with fixes applied. Safe to push and open PR.** Five P1 residuals are surfaced and tracked here; M1 (article-scope stack desync) is the most operationally impactful and should be the first follow-up. The PR description should note that the article-scoped parser is best-effort against malformed HTML pending M1's depth-counter redesign.
