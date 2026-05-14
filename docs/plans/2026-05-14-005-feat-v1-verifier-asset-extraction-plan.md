---
title: V1 Verifier Asset Extraction (post-#1 close)
type: feat
status: active
date: 2026-05-14
origin: docs/plans/2026-05-14-004-refactor-pr-landing-roadmap-plan.md
---

# V1 Verifier Asset Extraction (post-#1 close)

## Overview

PR #1 (`feat/real-publish-verification`, opened 2026-05-12) introduced a V1 multi-channel post-publish verifier (`src/backlink_publisher/verifier.py`, 43KB, 331 tests) with SSRF defense, scoped HTML parsing, Blogger `posts.get` API channel, and the M1 `_ArticleScopedCollector` redesign. Between its open date and 2026-05-14, twelve PRs (#2–#15) landed on main, including the parallel lightweight `verify_publish.py` (3.5KB, from #7) and `link_attr_verifier.py`.

When PR #10's landing chain triggered the rebase attempt under Plan 2026-05-14-004 Unit 5, the measurement gate fired on **both** conditions:

| Gate signal | Threshold | Measured | Triggered |
|---|---|---|---|
| Source-file conflict count | ≤ 3 | **4** (`adapters/base.py`, `adapters/blogger_api.py`, `cli/publish_backlinks.py`, `config.py`) | yes |
| Semantic fork | no | yes — `verifier.py` (V1, 43KB) is a parallel implementation of post-publish verification alongside main's `verify_publish.py` (3.5KB) + `link_attr_verifier.py`; the `adapters/base.py` conflict is a typed-vs-untyped contract divergence on `_provider_meta` | yes |

Per the plan's fallback rule, PR #1 is closed and the V1 verifier's highest-value assets are extracted to land separately on top of current main rather than relitigating the parallel-implementation choice through merge conflicts.

This plan defines those three extractable assets and stages them as small standalone PRs against current main.

## Problem Frame

The V1 verifier shipped in PR #1 carried three security/correctness primitives that the merged lightweight verifier does **not** have:

1. **SSRF defense layer** — post-DNS IP allowlist (rejecting RFC1918, loopback, link-local, CGNAT 100.64/10, 6to4 anycast, Teredo, cloud metadata IPs), redirect-hop re-check, HTTPS→HTTP downgrade block, strict TLS context. Pre-PR-#1 `linkcheck.py` uses a lax TLS context and has no SSRF defense.
2. **`_ArticleScopedCollector`** — outermost-only article-scope HTML parser with EOF hard-reject, refusing re-entry on `<article>A</article><article>B</article>` union attacks. Closes the M1 stack-desync that three reviewers flagged as a P1 on the PR #1 verifier branch. Pre-PR-#1 HTML parsing in the codebase uses bs4 without scope discipline.
3. **3-layer fuzz harness for HTML scope parsing** — 2000 invariant streams, 2000 sidebar-exclusion security property streams, 200 mutations per regression seed. Deterministic seed; runs in ~1s. Documents the negative-shape contract better than example tests can.

The V1 verifier itself (multi-channel orchestrator, Blogger `posts.get` API channel, scoped HTML GET channel, exit-code 4 = ExternalServiceError) is **not** in scope — those depend on the V1 module's overall design choices that diverge from main's lightweight verifier and would re-trigger the same semantic-fork conflicts that closed PR #1.

## Requirements Trace

- R1. SSRF defense primitives (post-DNS IP allowlist, redirect-hop re-check, HTTPS→HTTP block, strict TLS context) are available on main as reusable functions, callable from `linkcheck.py`, `verify_publish.py`, or any future verifier without re-introducing the V1 module.
- R2. `_ArticleScopedCollector` is available as a standalone module on main, importable by `verify_publish.py` or `link_attr_verifier.py` if they choose to adopt scope-disciplined parsing later, without forcing the choice now.
- R3. The 3-layer fuzz harness from PR #1 (deterministic seed, ~1s runtime) lands as a property-style test that exercises `_ArticleScopedCollector` directly. Existing main tests for `verify_publish.py` are not modified.
- R4. PR #1 is closed with a structured comment linking this plan and the three assets so a future maintainer can find the original implementation.
- R5. Decision record exists (this plan + a one-line entry in `docs/ideation/2026-05-14-round3-fresh-pass-ideation.md`'s Session Log).

## Scope Boundaries

- **Not in scope:** Re-introducing PR #1's `verifier.py` module, its multi-channel orchestration, or the Blogger `posts.get` API channel. Those design choices are deferred to a separate decision; they are not lost — they remain visible in the closed PR #1 history if a future operator wants to revive them.
- **Not in scope:** Modifying `verify_publish.py` or `link_attr_verifier.py` semantics. The extraction adds *available primitives*; downstream adoption is a separate choice.
- **Not in scope:** Modifying `linkcheck.py` to adopt strict TLS. Its lax-TLS contract is documented; flipping it deserves its own PR with operator-visible behavior change notes.
- **Not in scope:** Reintroducing `_provider_meta: dict[str, str]` typing on `adapters/base.py`. PR #1's typed form was a verifier-coupled contract; main's `dict[str, Any] | None` is the canonical post-#1-close form.

## Context & Research

### Relevant Code and Patterns

- `src/backlink_publisher/verifier.py` (on `origin/feat/real-publish-verification`, ~43KB) — source of all three extracted assets.
- `src/backlink_publisher/verify_publish.py` (on main, 3.5KB, from #7) — the current canonical post-publish verifier. The extraction must coexist with this module, not replace it.
- `src/backlink_publisher/adapters/link_attr_verifier.py` (on main) — current attribute-verifier surface. Same coexistence requirement.
- `src/backlink_publisher/linkcheck.py` (on main) — lightweight HEAD/GET checker. Documented as using lax TLS; extraction must not change its existing call sites' TLS contract.
- `docs/plans/2026-05-12-005-feat-real-publish-verification-plan.md` and `docs/plans/2026-05-12-006-fix-article-scoped-collector-stack-desync-plan.md` — the original V1 design docs from PR #1's branch. These are not on main but the historical record is preserved in the closed PR.

### Institutional Learnings

- `feedback_plan-vs-code-drift.md` — confirms the gate's wisdom. PR #1's plan was authored 2026-05-12; by 2026-05-14 main had moved past it. Extraction (here) avoids relitigating a stale plan.
- `feedback_brainstorm-prompt-as-desired-state.md` — closing PR #1 with a structured comment is non-negotiable; otherwise the "extract later" intention rots.
- `docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md` — adjacent precedent: structural-property tests catch what example tests miss. The 3-layer fuzz harness in Unit 3 follows the same shape.

### External References

None gathered — the three primitives are repo-internal mechanics with established library precedent (Python `ssl.create_default_context`, `socket.getaddrinfo`, bs4 incremental parsing).

## Key Technical Decisions

- **Land as three small PRs, not one bundle.** Per `feedback_plan-vs-code-drift.md` (and `feedback_cereview-finds-latent-bugs.md` adjacent caution about bundled refactors), three sequenced PRs review faster, surface latent bugs faster, and let any one of them slip independently if review surfaces blockers.
- **Each asset lands as a new module, not edits to existing modules.** SSRF helpers in `src/backlink_publisher/net_safety.py`, scoped parser in `src/backlink_publisher/html_scope.py`, fuzz harness in `tests/test_html_scope_fuzz.py`. Rationale: zero risk to existing call sites; the unifying decision (which existing verifier adopts them) happens later, in a separate change.
- **No verifier module created.** This is the explicit non-restoration of PR #1's V1 verifier orchestrator. If a future operator wants the V1 architecture back, the extracted primitives make rebuilding cheaper than re-merging the dead branch.
- **Deterministic seeds preserved exactly.** PR #1's fuzz harness used specific seeds (per the PR body). Extraction copies them verbatim so any regression repros bit-for-bit.

## Open Questions

### Resolved During Planning

- **Should `_ArticleScopedCollector` be the new default for `verify_publish.py`?** No — that's a separate downstream decision. The extraction makes the primitive available; adoption is a follow-up.
- **Should `_provider_meta` typing change with this?** No — main's `dict[str, Any] | None` is canonical post-#1-close.
- **Three PRs or one bundle?** Three. See Key Technical Decisions.
- **Where do the new modules live?** `src/backlink_publisher/net_safety.py` and `src/backlink_publisher/html_scope.py` — peers of existing primitives.

### Deferred to Implementation

- **Whether `net_safety` should expose a single context-builder function or a small toolkit (allowlist + safe-redirect + TLS-context).** Decide when extracting; if PR #1's code already factored these, mirror the factoring.
- **Whether the fuzz harness needs a `slow` marker** (so the default `pytest -q` run skips it but CI runs it). Decide based on harness runtime in the extracted form; PR #1 measured ~1s so default-on is plausible.
- **Whether to copy PR #1's solution doc** at `docs/solutions/.../article-scoped-collector-stack-desync-2026-05-12.md` (if it exists in the closed branch) to main as a forensic record. Decide when extracting — if the doc has standalone value beyond the verifier context, copy it.

## Implementation Units

- [ ] **Unit 1: Extract SSRF defense layer to `net_safety.py`**

**Goal:** Make PR #1's SSRF primitives (post-DNS IP allowlist, redirect-hop re-check, HTTPS→HTTP block, strict TLS context, credential sanitizer) available on main as standalone helpers, with zero impact on existing call sites.

**Requirements:** R1

**Dependencies:** None.

**Files:**
- Create: `src/backlink_publisher/net_safety.py`
- Create: `tests/test_net_safety.py`

**Approach:**
- Read `src/backlink_publisher/verifier.py` on `origin/feat/real-publish-verification`. Identify the SSRF helper functions (the PR #1 body explicitly names them: post-DNS IP check, `_SafeRedirectHandler`, strict TLS context, credential sanitizer regex).
- Copy them verbatim into `net_safety.py`. Adapt only their imports (rename `verifier_internal_error` references to a public `NetSafetyError`).
- Existing call sites (`linkcheck.py`, `verify_publish.py`) are not touched — adoption is a follow-up.
- Write tests covering: (i) RFC1918/loopback/link-local/CGNAT/Teredo IPs are rejected, (ii) redirect chains have per-hop IP checks, (iii) HTTPS→HTTP downgrade is blocked, (iv) strict TLS context rejects expired/self-signed certs in test setup, (v) credential sanitizer strips `Bearer`, `Authorization`, `ya29.*`, `sk-*`, `AIza*`, JWT shapes from error strings.

**Patterns to follow:**
- PR #1's existing SSRF tests in `tests/test_verifier_html_channel.py` on the closed branch — same shape, just retargeted to the new module.

**Test scenarios:**
- Happy path: `is_ip_allowed("8.8.8.8")` → True.
- Edge case — RFC1918: `is_ip_allowed("10.0.0.1")` → False; `is_ip_allowed("192.168.1.1")` → False.
- Edge case — loopback: `is_ip_allowed("127.0.0.1")` → False; `is_ip_allowed("::1")` → False.
- Edge case — CGNAT: `is_ip_allowed("100.64.0.1")` → False.
- Edge case — cloud metadata: `is_ip_allowed("169.254.169.254")` → False; `is_ip_allowed("100.100.100.200")` (Alibaba metadata) → False.
- Error path — HTTPS→HTTP redirect: `_SafeRedirectHandler` raises on `https://x → http://x`.
- Error path — credential sanitizer: `sanitize("Bearer ya29.abc...")` strips token; `sanitize("Authorization: sk-...")` strips.
- Integration: a constructed redirect chain `https://safe.example/a → https://safe.example/b → http://leak/c` is rejected at hop 3, not hop 1.

**Verification:**
- `pytest tests/test_net_safety.py -q` is green.
- Full suite remains green (no existing call sites touched).
- `import backlink_publisher.net_safety` from a fresh interpreter works.

---

- [ ] **Unit 2: Extract `_ArticleScopedCollector` to `html_scope.py`**

**Goal:** Make the M1 outermost-only article-scope parser (with EOF hard-reject, single-article enforcement) available on main as a standalone module.

**Requirements:** R2

**Dependencies:** None (Unit 1 and Unit 2 can ship in parallel).

**Files:**
- Create: `src/backlink_publisher/html_scope.py`
- Create: `tests/test_html_scope.py`

**Approach:**
- Read `_ArticleScopedCollector` from `src/backlink_publisher/verifier.py` on `origin/feat/real-publish-verification`. Lift the class into `html_scope.py`. Promote its public methods to a stable API name (e.g., `ArticleScopedCollector`, no leading underscore).
- Adapt imports; remove verifier-internal coupling.
- Define `verification_error="article_container_unclosed"` as a public exception or sentinel exposed from this module — main's modules can import it without depending on the V1 verifier orchestrator.
- Do not modify `verify_publish.py` or `link_attr_verifier.py` in this PR. Adoption is a separate change.

**Patterns to follow:**
- PR #1's existing `test_verifier_html_channel.py` boundary tests for M1 — replay them as `test_html_scope.py` regression tests.

**Test scenarios:**
- Happy path: single `<article>X</article>` collects X.
- Edge case — sidebar leak refusal: `<article>main<aside>sidebar</aside></article>` collects only the article scope, not the aside.
- Edge case — out-of-scope anchor: `<a>before</a><article>...</article><a>after</a>` ignores both flanking anchors.
- Edge case — Blogger post-body div nesting: deeply-nested `<div>` chains inside the outermost article still resolve to the same scope.
- Edge case — attribute-conditional entry: `<section data-field="body">…</section>` is recognized as the article container when the configured selector matches.
- Error path — EOF hard-reject: truncated outermost scope raises `article_container_unclosed`, never silently degrades.
- Error path — two-articles attack: `<article>A</article><article>B</article>` refuses re-entry; raises or returns first-article-only with a structured signal (match PR #1's chosen semantic).
- Error path — no container: HTML with no `<article>` or configured selector returns a documented "no scope" sentinel.

**Verification:**
- `pytest tests/test_html_scope.py -q` is green.
- Full suite remains green.
- `from backlink_publisher.html_scope import ArticleScopedCollector` works from a fresh interpreter.

---

- [ ] **Unit 3: Extract the 3-layer fuzz harness as `test_html_scope_fuzz.py`**

**Goal:** Port PR #1's deterministic 3-layer fuzz harness for `_ArticleScopedCollector` onto main, exercising the Unit 2 module.

**Requirements:** R3

**Dependencies:** Unit 2 (the module under test must exist on main).

**Files:**
- Create: `tests/test_html_scope_fuzz.py`

**Approach:**
- Read the fuzz harness from PR #1's `tests/test_verifier_html_channel.py` (or wherever the 3-layer fuzz lived in the closed branch — locate by grepping the closed branch for `2000` and `200`, which were the PR-body-stated stream counts).
- Port the three layers: (i) 2000 invariant streams (structure-preserving), (ii) 2000 sidebar-exclusion security property streams, (iii) 200 mutations per regression seed.
- Preserve the deterministic seed values from PR #1 exactly — any regression repros bit-for-bit.
- Use Hypothesis only if PR #1 used it; otherwise keep the hand-rolled mutation harness as-is. Either way, runtime must remain ~1s (acceptable for default test runs).
- If runtime exceeds 5s on CI, mark with `@pytest.mark.slow` and add a `slow` marker pattern in `pyproject.toml`. Decide based on measurement, not preemption.

**Patterns to follow:**
- `tests/test_gate_properties.py` `test_language_matches_not_tautological` for the deterministic-seed structural-guard pattern (now on main from PR #17).
- PR #1's existing fuzz harness for the layer-by-layer breakdown.

**Test scenarios:**
- Layer 1 — invariants: over 2000 streams, every fuzz output respects `_ArticleScopedCollector`'s state machine (no torn writes, no leaked state).
- Layer 2 — sidebar-exclusion security property: over 2000 streams, no fuzz input causes the collector to leak content from outside the article scope.
- Layer 3 — mutation per regression seed: each regression fixture from Unit 2 generates 200 mutated variants; all must either parse correctly or raise `article_container_unclosed` — never silently corrupt.
- Determinism: a second run with the same seed produces byte-identical assertions.

**Verification:**
- `pytest tests/test_html_scope_fuzz.py -q` is green.
- Runtime ≤ 5s on CI (measured); if > 5s, marker gates default runs.
- A constructed regression (mutate the seed by 1 byte) produces a different but deterministic failure mode.

## System-Wide Impact

- **Interaction graph:** None — three new files, no edits to existing modules. Future adoption changes happen in separate PRs.
- **Error propagation:** New error types (`NetSafetyError`, `article_container_unclosed`) are exposed from their respective new modules. Existing modules are unchanged.
- **State lifecycle risks:** None — pure functions and a parser class. No persistent state.
- **API surface parity:** N/A — new modules. Future PRs that adopt them will need to consider parity across `verify_publish.py`, `link_attr_verifier.py`, and `linkcheck.py`.
- **Integration coverage:** Each unit has its own test file; integration with existing modules is deferred.
- **Unchanged invariants:** `linkcheck.py` lax-TLS contract, `verify_publish.py` and `link_attr_verifier.py` semantics, `adapters/base.py` `_provider_meta` typing — all unchanged.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| PR #1's branch is closed and `origin/feat/real-publish-verification` could be deleted by repo cleanup before Unit 1 starts. | Step 0 of Unit 1 is to clone the relevant file regions out of `origin/feat/real-publish-verification` into a local backup file (`/tmp/v1-verifier-snapshot.py`) before any other work. |
| Hand-rolling fuzz harness mutations diverges from PR #1's exact behavior. | Compare a sample of mutated outputs byte-for-byte against the closed branch; if any drift, fix or document. |
| `NetSafetyError` / `article_container_unclosed` naming conflicts with existing error classes on main. | Grep main for these names before declaring; rename if needed. Document chosen names in each PR. |
| Future adopter of `html_scope.ArticleScopedCollector` discovers the API needs adjustments not visible in isolation. | Keep the API surface minimal in Unit 2; expand only when first downstream PR motivates it. Avoid premature abstraction. |
| `playwright>=1.40` or other heavy deps surface during port. | Unlikely — these primitives are pure stdlib + bs4 (already a runtime dep). Verify in clean venv before push. |

## Documentation / Operational Notes

- **Per-PR:** Each unit's PR description must list which V1 verifier function it ports and link the PR #1 archive URL for the original implementation.
- **Cross-link:** After Unit 1, Unit 2, Unit 3 all merge, update this plan's `status:` to `completed` and add a session-log entry to `docs/ideation/2026-05-14-round3-fresh-pass-ideation.md`.
- **No operator-facing rollout:** New modules add capability; they don't change any current behavior. No CHANGELOG entries required until a downstream adoption PR ships.

## Sources & References

- Origin (closed): PR #1 — `feat: real-publish verification + M1 article-scope fix`. https://github.com/redredchen01/backlink-publisher/pull/1
- Source branch (snapshot before deletion): `origin/feat/real-publish-verification` at commit ~`4362214` (M1 redesign).
- Parent roadmap: `docs/plans/2026-05-14-004-refactor-pr-landing-roadmap-plan.md` (Unit 5 fallback path).
- Related on-main code: `src/backlink_publisher/verify_publish.py`, `src/backlink_publisher/adapters/link_attr_verifier.py`, `src/backlink_publisher/linkcheck.py`.
- Memory: `feedback_plan-vs-code-drift.md`, `feedback_brainstorm-prompt-as-desired-state.md`, `feedback_force-push-hook-workaround.md`.
- Adjacent precedent: `docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md` (structural-property pattern reused in Unit 3's fuzz harness).
