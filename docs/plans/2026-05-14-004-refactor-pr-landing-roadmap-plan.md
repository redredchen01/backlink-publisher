---
title: PR Landing Roadmap — #9 → #10 → #14 follow-up → #1 triage
type: refactor
status: completed
date: 2026-05-14
---

# PR Landing Roadmap — #9 → #10 → #14 follow-up → #1 triage

## Overview

Three PRs are open at session start: #9 (work-themed-backlinks, base=main, CI red), #10 (mandatory-linkcheck-lang-gate, **stacked on #9**, no CI yet), and #1 (V1 verifier with SSRF defense + M1 article-scope fix, base=main, 2026-05-12 old, CI red). PR #14 (Round-3 property test infra) is already merged but currently has NO `test_language_matches_*` tests — by design, because R1 (the `language_matches` always-True fix) only ships in #10. This plan sequences merge work so the dependency chain holds.

Core appeal from the user: **merge is the goal**, conflict resolution is in scope, and the planner makes the call on PR #1's disposition.

## Problem Frame

Four entangled questions need answers in order:

1. **#9 is one line away from green.** CI fails with `ModuleNotFoundError: No module named 'flask'` because `webui.py` and `tests/test_webui_three_url.py` import Flask but `[project.optional-dependencies].dev` does not list it. Same class of bug as the `hypothesis` dev-dep trap captured in `feedback_hypothesis-dev-dep-ci-trap.md`. Local pytest hides this because the developer's venv has Flask from an earlier round.

2. **#10 cannot merge to main while stacked on #9.** Its `baseRefName` is `feat/work-themed-backlinks`, not `main`. PR body says 1057 tests pass locally on that stack. After #9 merges, the #10 branch needs `git rebase --onto main feat/work-themed-backlinks feat/mandatory-linkcheck-lang-gate` (or equivalent), the base swapped to `main`, and a fresh CI run.

3. **#14 was deliberately scoped to non-language gates** (`test_title_in_body_*`, `test_link_in_body_*`, `test_normalize_*`) — its docstring says "`language_check.language_matches` — currently tautological; fix in PR". After #10's R1 fix lands, a follow-up PR extends `tests/test_gate_properties.py` with `test_language_matches_*` properties using the existing hypothesis dep. This is the lowest-risk piece in the chain.

4. **PR #1 is not superseded.** It contains assets not on main: `verifier.py` (V1 multi-channel verifier with SSRF defense + scoped HTML parser), Blogger `posts.get` API integration, the M1 `_ArticleScopedCollector` redesign (`4362214`), 3-layer fuzz harness, 331 tests. Main has `verify_publish.py` (a different, lighter verifier from #7) and `link_attr_verifier.py`. The two implementations have overlapping but distinct semantics, and PR #1's branch is ~12 PRs behind main with significant conflict surface on `adapters/base.py`, `adapters/blogger_api.py`, `adapters/medium_brave.py`, `config.py`, `cli/publish_backlinks.py`.

## Requirements Trace

- R1. #9 lands on main with CI green on Python 3.11 + 3.12.
- R2. #10 lands on main with CI green and all R1-R13 behaviors (language_matches fix, validate-time gate, publish-time reachability, --resume re-validation) intact post-rebase.
- R3. After #10 lands, a follow-up PR extends `tests/test_gate_properties.py` with `language_matches` property tests that would have caught the always-True bug (`docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md`).
- R4. PR #1 reaches a terminal state — merged, or closed-with-extraction-plan — without leaving SSRF-defense + scoped-parser assets stranded indefinitely.
- R5. Decision gates on #1 are explicit and evidence-backed; the plan does not silently commit to a 1-2 day rebase slog if the conflict surface dominates.

## Scope Boundaries

- **Not in scope:** Round-3 ideation survivors #6 (Logger Redactor) and #7 (Config Echo Chamber). Those are tracked in `docs/ideation/2026-05-14-round3-fresh-pass-ideation.md` and get their own plans after this roadmap clears.
- **Not in scope:** Reopening product decisions in #10's body (e.g., should `--skip-publish-time-check` be opt-in vs opt-out — already settled in `docs/plans/2026-05-14-001-feat-mandatory-linkcheck-lang-gate-plan.md`).
- **Not in scope:** Touching `verify_publish.py` or `link_attr_verifier.py` semantics. PR #1 triage decides whether V1 verifier assets graft onto these or stay separate; it does not redesign them.
- **Not in scope:** CHANGELOG / README polish unrelated to the PRs in flight.

## Context & Research

### Relevant Code and Patterns

- `pyproject.toml` `[project.optional-dependencies].dev` — already lists `pytest-timeout`, `hypothesis>=6.0` (added in commit `96c36f6`). Flask must follow the same pattern. **Pattern for #9 fix.**
- `webui.py:4` — `from flask import Flask, request, render_template_string, jsonify, session, redirect, url_for`. Import surface that breaks CI.
- `tests/conftest.py` — top-level autouse `pytest-socket` `disable_socket()` net safety net (added in #10). Already present on #10 branch; rebase must preserve this file unchanged.
- `tests/test_gate_properties.py` — file header comment: `language_check.language_matches — currently tautological; fix in PR`. The `language_matches` row in the property suite is intentionally absent. Extension point: add a `test_language_matches_*` group near the existing `test_title_in_body_*` block, mirroring its property shape (positive verbatim, negative mismatch, empty-input, known-negative fixture).
- `src/backlink_publisher/language_check.py` — body keyword-hint heuristic. Post-#10, `language_matches(target_lang, body_text)` returns `True/False` per ZH/RU/EN hint coverage. Property test inputs: build adversarial body samples per hint-set (all-EN body vs target=`zh-CN`, all-ZH body vs target=`en`, etc.).
- `src/backlink_publisher/verify_publish.py` (main, from #7) and `src/backlink_publisher/adapters/link_attr_verifier.py` (main) — the **current** post-publish verification surface. PR #1's `verifier.py` is parallel, not a replacement.

### Institutional Learnings

- `feedback_hypothesis-dev-dep-ci-trap.md` — "本地 pytest 绿不代表 CI 绿". Exact pattern matches #9's flask omission. Pre-flight check before merging #9: a clean-venv smoke pass.
- `feedback_plan-vs-code-drift.md` — "plan 对'现有 API'描述可能 stale；动既有模块前重读源". Applies directly to PR #1: its 2026-05-12 plan describes adapters/config that have since shifted under it. Reading source before rebasing each conflicting file is mandatory.
- `feedback_test-autouse-verify-mock.md` — Adding HTTP-call functionality needs autouse mock fixture in publish tests. PR #10 already added top-level `tests/conftest.py` with `publish_backlinks.check_url` autouse mock. Rebase must keep this exactly.
- `feedback_brainstorm-prompt-as-desired-state.md` — User's "merge 优先" is a desired state. Don't lock in the V1-verifier-lands outcome before evidence shows it can. Use a gate.
- `feedback_force-push-hook-workaround.md` — `pre-bash-safety` blocks `git push --force`. After rebases, push to a sibling branch (`feat/mandatory-linkcheck-lang-gate-rebased`) and switch the PR's head ref, instead of force-pushing the existing branch.
- `docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md` — bug capture for R3's property test design. The class of failure: example tests pass, gate is structurally tautological. Property test must assert "for at least one constructed mismatched input, gate returns False" plus "Shannon entropy of outputs over N=10000 random inputs > non-trivial floor".

### External References

None gathered — all four units are repo-internal mechanics; external research adds no value here.

## Key Technical Decisions

- **#9 fix is `pyproject.toml`-only.** Flask is the missing piece, not a deeper refactor. Add `"flask>=2.0"` to `[project.optional-dependencies].dev`. Rationale: matches the established hypothesis/pytest-timeout pattern; no install step on import surface.
- **#10 rebases onto main, not "merges to feat/work-themed-backlinks first then ff to main".** Rationale: GitHub PR base swaps are first-class; rebasing onto main directly preserves linear history and lets us run CI on the actual landing state. The stacked-base form was an authoring convenience.
- **#14 follow-up is a new branch, not a re-open of #14.** Rationale: #14 is merged; modifying it would require revert+re-merge. A separate branch `feat/property-tests-language-matches` is cheaper and leaves #14's audit trail clean.
- **PR #1 disposition is gated on rebase conflict measurement, not pre-committed.** Primary path: full rebase + merge. Fallback gate: if conflicts span >50% of the 6 overlapping production source files OR require resolving a semantic fork between `verifier.py` and `verify_publish.py`/`link_attr_verifier.py`, close #1 and open a fresh "extract M1 + SSRF" follow-up plan against current main. Rationale: user explicitly said merge is the goal, but `feedback_plan-vs-code-drift.md` warns the 2026-05-12 base may have drifted past surgical-rebase territory. The gate makes the fallback evidence-driven, not pessimistic guesswork.
- **No force-push.** All rebases land on a fresh sibling branch; PR head ref is swapped via `gh pr edit --head <new-branch>` if needed, or a new replacement PR is opened. Rationale: `feedback_force-push-hook-workaround.md`.
- **Clean-venv smoke check is mandatory before merging #9.** Rationale: `feedback_hypothesis-dev-dep-ci-trap.md`. Verifying CI green is necessary but not sufficient — if the smoke also passes in a fresh venv, the dep is correctly declared in all relevant extras.

## Open Questions

### Resolved During Planning

- **Is #1 superseded by #7?** No. PR #7 merged `verify_publish.py` (lightweight); PR #1 has `verifier.py` (V1 multi-channel with SSRF). Different modules, different scopes.
- **Does #14 already cover `language_matches`?** No. `tests/test_gate_properties.py` deliberately excludes `language_matches` per its own header comment. R3 is a genuine follow-up, not a rewrite.
- **Should #10 land before or after #9?** After. Cannot land before because base=`feat/work-themed-backlinks`. Cannot run CI cleanly on a stacked base either.
- **Is `git push --force` needed for the rebases?** No, use sibling branches. `pre-bash-safety` hook blocks `--force` anyway.

### Deferred to Implementation

- **Exact flask version pin.** `>=2.0` is the floor; the implementer can tighten if `pip install -e '.[dev]'` resolves a too-old transitive. Decide at rebase time, not now.
- **Whether the `language_matches` property tests need a custom Hypothesis strategy.** Plausible that `hypothesis.strategies.text(alphabet=...)` combined with hint-set sampling is enough. Decide when writing the tests against the actual `language_matches` API post-#10.
- **PR #1 conflict count.** Measured at the rebase attempt, not now. The gate threshold (>50% of overlapping production files) is set; the measurement is execution-time.
- **Whether to graft `_ArticleScopedCollector` onto `verify_publish.py` or keep it in a standalone scoped-parser module if #1's fallback path is taken.** Depends on where article-scoped HTML parsing actually lives post-#9/#10 merge — those PRs may shift the verifier surface.

## Implementation Units

- [ ] **Unit 1: Fix #9 CI by declaring Flask as a dev dep**

**Goal:** Make `pytest -q --timeout=30` pass on a clean-venv CI runner against the `feat/work-themed-backlinks` branch by declaring its actual import surface.

**Requirements:** R1

**Dependencies:** None.

**Files:**
- Modify: `pyproject.toml` (`[project.optional-dependencies].dev`)
- Test: existing `tests/test_webui_three_url.py` (no edit; it already exercises the import)

**Approach:**
- Add `"flask>=2.0"` to the dev extras list, in alphabetical position relative to existing entries.
- Commit on `feat/work-themed-backlinks`, push, wait for CI.
- Before merging, run a clean-venv smoke locally to verify the same fix works against a fresh install (`feedback_hypothesis-dev-dep-ci-trap.md`).
- Do not introduce a separate `[project.optional-dependencies].webui` extra; keeping it under `dev` matches the established convention for test-time imports (e.g., `pytest-asyncio`, `hypothesis`). A future cleanup could split UI deps if `webui.py` ever gains production-runtime semantics, but that's not the bar for this fix.

**Patterns to follow:**
- Commit `96c36f6` (`build(deps): add hypothesis>=6.0 to dev deps for property-based gate tests`).
- `pyproject.toml:[project.optional-dependencies].dev` existing entry order.

**Test scenarios:**
- Happy path: `pip install -e '.[dev]'` in a clean Python 3.11 venv → `python -c "import flask"` succeeds.
- Integration: GitHub Actions `test (3.11)` and `test (3.12)` jobs both turn green on the next push.
- Regression guard: `pytest tests/test_webui_three_url.py -x` collects without `ModuleNotFoundError`.

**Verification:**
- PR #9 statusCheckRollup shows both `test (3.11)` and `test (3.12)` as `SUCCESS`.
- Clean-venv smoke pass locally (fresh `python -m venv .venv-smoke && source .venv-smoke/bin/activate && pip install -e '.[dev]' && pytest -q --timeout=30 tests/test_webui_three_url.py`).
- `mergeStateStatus: CLEAN`.

---

- [ ] **Unit 2: Merge PR #9 to main**

**Goal:** Get `feat/work-themed-backlinks` (three-URL form, CSRF webui, dispatcher, scrapers, verifier) onto main with green CI.

**Requirements:** R1

**Dependencies:** Unit 1.

**Files:**
- No code changes in this unit — it is a merge action.

**Approach:**
- Confirm Unit 1 has produced green CI.
- Resolve any merge conflicts against current main (none expected since `mergeable` was reported but only because CI was failing, not because of code conflicts — verify at merge time).
- Use a merge commit (not squash) only if the existing branch's commit history is part of the audit value (the 9 commits in #9 each map to a Unit of `docs/plans/2026-05-13-004`). Otherwise prefer squash. Decide based on team convention; default to **squash** since main's history shows squashed PRs.
- Land via `gh pr merge 9 --squash --auto` once CI is green.

**Patterns to follow:**
- PR #11-#15 merge style (per `gh pr list --state merged`, all recent merges appear squashed based on the linear `git log` output).

**Test scenarios:**
- Test expectation: none — merge action, no behavioral change beyond integrating already-tested code.

**Verification:**
- `gh pr view 9 --json state` returns `MERGED`.
- `git log origin/main --oneline -5` shows the new merge commit at HEAD.
- `git fetch && git checkout main && pip install -e '.[dev]' && pytest -q` is green locally.

---

- [ ] **Unit 3: Rebase PR #10 onto main and land**

**Goal:** Re-base `feat/mandatory-linkcheck-lang-gate` from `feat/work-themed-backlinks` onto `main` (now containing #9's content), verify R1-R13 still hold, land.

**Requirements:** R2

**Dependencies:** Unit 2 (PR #9 merged to main).

**Files:**
- No source edits expected; this is a rebase action with conflict resolution if any. Files at risk: `tests/conftest.py` (autouse mock), `pyproject.toml` (if Unit 1 raced an alphabetical-position conflict), `src/backlink_publisher/cli/validate_backlinks.py`, `src/backlink_publisher/cli/publish_backlinks.py`, `src/backlink_publisher/language_check.py`, `src/backlink_publisher/anchor_lang.py`.

**Approach:**
- Create sibling branch: `git checkout -b feat/mandatory-linkcheck-lang-gate-rebased feat/mandatory-linkcheck-lang-gate`.
- `git rebase --onto main feat/work-themed-backlinks` (drops #9's commits that are now in main, keeps the 11 R1-R13 commits).
- Resolve any conflicts file by file. Expected zero-or-low conflict because #9's commits are now identical bytes on main.
- Push: `git push -u origin feat/mandatory-linkcheck-lang-gate-rebased`.
- Re-point PR: `gh pr edit 10 --base main` and either swap head with `gh pr edit 10 --head feat/mandatory-linkcheck-lang-gate-rebased` or open replacement PR if head swap is not supported.
- Wait for fresh CI run on the new base.
- Merge once green: `gh pr merge 10 --squash --auto`.

**Patterns to follow:**
- Sibling-branch rebase per `feedback_force-push-hook-workaround.md`.
- Top-level `tests/conftest.py` autouse mock pattern (already in branch; do not re-author).

**Test scenarios:**
- Happy path: post-rebase `pytest -q --timeout=30` runs all 1057 tests green locally.
- Integration: `language_matches("en", "zh-CN")` returns `False` after rebase (sanity check that R1 fix survives).
- Integration: `validate-backlinks` exits non-zero for a zh-CN row with English `content_markdown` (R2-R5 still triggers).
- Integration: `publish-backlinks --skip-publish-time-check` bypasses Unit 5 gate (R10 flag still works).
- Edge case: `--resume` over a pre-buggy checkpoint reclassifies items as `retro_language_failed` (R13 still works).
- Regression guard: PR #10's `statusCheckRollup` shows `test (3.11)` and `test (3.12)` both `SUCCESS` after rebase.

**Verification:**
- `gh pr view 10 --json baseRefName` returns `main`.
- `gh pr view 10 --json state` returns `MERGED`.
- `git log origin/main --oneline -15` shows the R1-R13 commits or their squashed equivalent at/near HEAD.
- The first-run banner from R12 fires once on a fresh `~/.cache/backlink-publisher/v0.3-gate-banner-seen` (manual smoke).

---

- [ ] **Unit 4: Follow-up PR — extend `test_gate_properties.py` with `language_matches` property tests**

**Goal:** Backfill the property tests that #14 deliberately omitted, now that R1's fix in #10 is on main.

**Requirements:** R3

**Dependencies:** Unit 3 (PR #10 merged to main).

**Files:**
- Modify: `tests/test_gate_properties.py` (add `test_language_matches_*` block; remove the "currently tautological; fix in PR" line from the file header).
- Test: same file (self-contained property suite).
- New branch: `feat/property-tests-language-matches` off updated `main`.

**Approach:**
- Mirror the structure of the existing `test_title_in_body_*` block: one positive (constructed match per supported lang), one negative (constructed mismatch per supported lang), one empty-input, one known-mismatched-fixture.
- Use Hypothesis strategies bounded by the hint-sets in `src/backlink_publisher/language_check.py` (`ZH_HINTS`, `RU_HINTS`, `EN_HINTS`) so generated inputs stay relevant rather than random Unicode (`feedback_floating-point-tiebreak.md` adjacent caution: random inputs without bounded shape generate false bug reports).
- Add a **structural-tautology guard property**: assert that over N=10000 sampled `(target_lang, body)` pairs, the False rate exceeds a non-trivial floor (e.g., >5%). This is the property that would have caught the always-True bug regardless of the specific positive/negative examples.
- Open small PR; expected diff: ~80 LOC added, 1 line removed from file header.

**Patterns to follow:**
- `tests/test_gate_properties.py` existing `test_title_in_body_*`, `test_link_in_body_*`, `test_normalize_*` blocks.
- Bug capture write-up at `docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md` for shape of adversarial inputs.

**Test scenarios:**
- Happy path: `test_language_matches_positive_when_body_in_target_lang(target_lang, body)` — for each of `["en", "zh-CN", "ru"]` plus a constructed lang-appropriate body, `language_matches(target_lang, body)` is True.
- Edge case: `test_language_matches_empty_body_accepts()` — empty body string returns the documented "skip" value (verify what current implementation returns; align test to spec, not the other way around).
- Negative: `test_language_matches_negative_when_lang_mismatch(target, mismatched_body)` — for constructed lang mismatches, returns False.
- Structural guard: `test_language_matches_not_tautological` — over 10000 sampled inputs, False rate ≥ 5% AND True rate ≥ 5% (rules out both always-True and always-False). This is the property whose absence let R1's bug ship.
- Known-fixture: `test_language_matches_known_negative_fixture()` — at least one explicit pair from the bug capture document returns False.
- Adversarial input: at least one BMP-edge / mixed-script body that should not flip the gate either way (e.g., ASCII-only English body with a sprinkle of zh-CN hint characters in a code block) — document the expected behavior with a comment.

**Verification:**
- New PR's CI is green on both 3.11 and 3.12.
- Mutation check (manual): temporarily revert `bc10dae` (the R1 fix) on a scratch branch; the new `test_language_matches_not_tautological` property should fail. Restore.
- `tests/test_gate_properties.py` header no longer contains the "currently tautological; fix in PR" line.

---

- [ ] **Unit 5: Triage PR #1 with measurement-gated decision**

**Goal:** Resolve PR #1 to a terminal state (merged or closed-with-extraction-plan) by attempting a rebase and applying the conflict-surface gate.

**Requirements:** R4, R5

**Dependencies:** Unit 3 (PR #10 merged to main, so all anchor/language/verifier surfaces are at their post-roadmap shape before measuring #1's drift).

**Files:**
- Source files at risk on rebase: `src/backlink_publisher/adapters/base.py`, `adapters/blogger_api.py`, `adapters/medium_brave.py`, `cli/publish_backlinks.py`, `config.py`, plus `verifier.py` itself.
- Test files at risk: `tests/test_adapter_base.py`, `test_adapter_blogger_api.py`, `test_publish_backlinks.py`, `test_publish_backlinks_verification.py`, `test_verifier_*`.
- New branch (if rebase is attempted): `feat/real-publish-verification-rebased`.
- New plan file (if fallback path is taken): `docs/plans/2026-05-15-NNN-feat-v1-verifier-asset-extraction-plan.md`.

**Approach:**
- **Step A — measure first.** Create sibling branch `feat/real-publish-verification-rebased` from `feat/real-publish-verification`. Attempt `git rebase main`. Without resolving anything, count: (i) number of source files with conflict markers, (ii) whether any conflict requires choosing between `verifier.py` semantics and `verify_publish.py`/`link_attr_verifier.py` semantics (semantic fork), (iii) whether `_ArticleScopedCollector` lands cleanly or fights existing HTML parsing on main.
- **Step B — apply the gate.**
  - **Primary path (chosen if conflicts touch ≤ 3 of the 6 overlapping production source files AND no semantic-fork resolution is required):** finish the rebase, fix pytest-timeout-style CI traps, resolve conflicts, push, verify CI green, merge. Keep the V1 verifier as an additional module alongside `verify_publish.py` — they have non-overlapping responsibilities (V1 = full SSRF + scoped article parse, current = lightweight post-publish check + link-attr).
  - **Fallback path (chosen if conflicts span > 3 files OR any conflict is a semantic fork):** abort the rebase. Close PR #1 with a comment linking to (a) `docs/plans/2026-05-12-005-feat-real-publish-verification-plan.md` for design reference, (b) the new asset-extraction plan written in this same unit, and (c) a TODO list of the specific assets to extract: SSRF defense layer (post-DNS IP allowlist, redirect-hop re-check, HTTPS→HTTP block), `_ArticleScopedCollector` outermost-only + EOF hard-reject, the 3-layer fuzz harness.
- **Step C — record the decision.** Whichever path is taken, append a session-log line to `docs/ideation/2026-05-14-round3-fresh-pass-ideation.md` (or a new `docs/decisions/2026-05-14-pr1-v1-verifier-disposition.md`) capturing the conflict count, the chosen path, and the rationale. This is the audit trail user-feedback memory warns is otherwise lost.

**Execution note:** Step A is read-only measurement. Do not resolve any conflicts before applying the gate in Step B, to avoid sunk-cost pressure.

**Patterns to follow:**
- `feedback_plan-vs-code-drift.md` — re-read the conflicting source files on `main` before deciding how to resolve, not just the conflict markers.
- `feedback_brainstorm-prompt-as-desired-state.md` — user said "merge", but the gate's job is to validate "merge" is achievable cheaply.
- Existing solution-doc pattern in `docs/solutions/` for the disposition write-up if the fallback path is taken.

**Test scenarios:**
- Primary-path completion: full pytest suite green; `verifier.py` is imported by `cli/publish_backlinks.py` (or wherever the integration lives in #1) without breaking the existing `verify_publish.py` call sites; manual smoke runs publish dry-run on a Blogger draft and observes the V1 verifier's `verified=true/false` JSONL fields.
- Primary-path regression: `language_matches` still returns False on `("en", "zh-CN")` (R1 from Unit 3 is preserved); `validate-backlinks` exit codes unchanged; `verify_publish.py`'s existing call sites still pass their tests.
- Fallback-path completeness: closed PR has a structured comment listing the three extraction targets; new plan file exists; ideation/decision log entry written.
- Adversarial: under the primary path, run #1's existing SSRF test suite (`tests/test_verifier_html_channel.py` if surviving) against the rebased branch — RFC1918/loopback/link-local rejects must still trigger.

**Verification:**
- `gh pr view 1 --json state` returns either `MERGED` (primary) or `CLOSED` (fallback).
- If fallback: the asset-extraction plan exists at the named path with at least three named implementation units (SSRF layer, scoped parser, fuzz harness).
- If primary: a fresh `git log origin/main --oneline | grep -i "real-publish\|verifier\|article-scope"` shows the V1 commits or their squash.
- Decision write-up exists capturing conflict count and path choice.

## System-Wide Impact

- **Interaction graph:** Unit 3's rebase affects `validate-backlinks` and `publish-backlinks` CLI entry points; Unit 5's primary path additionally touches `cli/publish_backlinks.py`. Both share the autouse `publish_backlinks.check_url` mock in `tests/conftest.py` — that fixture must keep working after every merge.
- **Error propagation:** Unit 5 primary path layers V1 verifier exit codes (exit 4 = ExternalServiceError) alongside #10's exit codes (exit 2 = validate-time gate, etc.). The `max()` rule must continue to choose the most-severe code; no exit-code namespace collisions across `validate-backlinks`, `publish-backlinks`, `verifier`.
- **State lifecycle risks:** Unit 3 preserves R13 (`--resume` hard re-validate). If Unit 5's primary path adds V1 verifier persistence to the checkpoint, it must do so additively — no schema break for in-flight checkpoints. Unit 5 fallback path has no checkpoint impact.
- **API surface parity:** `validate-backlinks --no-check-urls` deprecated alias must keep emitting WARN through all rebases (R10). `publish-backlinks --skip-publish-time-check` must remain a checkpoint-persisted flag.
- **Integration coverage:** The autouse `pytest-socket disable_socket()` net safety net must remain active through all merges. Any test that needs HTTP must mock at the consumer reference per `feedback_test-autouse-verify-mock.md`.
- **Unchanged invariants:** `verify_publish.py` and `link_attr_verifier.py` semantics on main are unchanged by this roadmap. PR #1's primary path adds `verifier.py` alongside them; fallback path leaves both untouched and defers V1 assets to a future extraction plan.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Flask version pin in Unit 1 conflicts with a transitive dep (e.g., a Werkzeug ABI) on CI runners. | Open Unit 1 with `flask>=2.0` floor; if CI install fails, tighten to a known-working version (`flask==3.x`). Roll forward in Unit 1 itself, do not block roadmap. |
| Unit 3 rebase reveals that #9's main-merged state subtly diverges from the bytes seen on the stacked base (squash flattens commits, paths may diverge if files were renamed during PR review). | Sibling-branch rebase keeps the old branch intact; can re-attempt. If the second rebase also fails, open a fresh replacement PR sourced from a hand-replayed series of cherry-picks. |
| Unit 4 property test catches an unrelated regression in `language_matches` (e.g., the keyword-hint heuristic is genuinely tautological for certain language combinations the bug capture didn't enumerate). | Treat as a real find: file a follow-up plan rather than weakening the property. The structural guard's job is exactly this. |
| Unit 5 primary path appears achievable in Step A but Step C reveals a semantic conflict only after CI runs (e.g., `verifier.py` and `verify_publish.py` race the same JSONL fields). | Step B gate has a re-evaluation clause: if CI is red after rebase for reasons that look semantic, fall back to the extraction plan from Step C even though Step A's measurement was favorable. Document the late switch. |
| Unit 5 fallback closes #1 but the asset-extraction plan stalls indefinitely (a known anti-pattern: "we'll do it later" never lands). | The extraction plan must be **written in Step C of Unit 5, not deferred**. Plan exists → eventual scheduling is at least possible. If no extraction plan is authored, Unit 5 is not complete. |
| Force-push hook (`pre-bash-safety`) blocks rebase publication. | Sibling-branch + `gh pr edit --head` swap pattern, per `feedback_force-push-hook-workaround.md`. No `--force` invocations needed. |
| Clean-venv smoke in Unit 1 fails for reasons unrelated to flask (e.g., playwright browser install). | Document the additional missing setup step in `pyproject.toml` or `tests/conftest.py`; do not paper over by reusing the dirty venv. Same class of bug as flask. |

## Documentation / Operational Notes

- **Post-Unit 3:** Update `docs/plans/2026-05-14-001-feat-mandatory-linkcheck-lang-gate-plan.md` `status:` field to `completed` once #10 merges, per the project convention of marking plans complete on landing.
- **Post-Unit 4:** Cross-link the new property test PR description back to `docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md` so the bug-to-property-test trail is preserved.
- **Post-Unit 5:** Append a session-log entry to `docs/ideation/2026-05-14-round3-fresh-pass-ideation.md` capturing the disposition of PR #1, since the ideation doc tracks the broader "what shipped vs what didn't" map.
- **Operator-facing:** First-run banner from R12 fires on a fresh `~/.cache/backlink-publisher/v0.3-gate-banner-seen` after Unit 3 merges. Existing cron operators will see the WARN once and can suppress with the sentinel file.

## Sources & References

- PR #1: https://github.com/redredchen01/backlink-publisher/pull/1 (V1 verifier + M1 fix, base 2026-05-12)
- PR #9: https://github.com/redredchen01/backlink-publisher/pull/9 (work-themed backlinks, base main)
- PR #10: https://github.com/redredchen01/backlink-publisher/pull/10 (mandatory-linkcheck-lang-gate, **base feat/work-themed-backlinks**)
- PR #14 (merged): https://github.com/redredchen01/backlink-publisher/pull/14 (property test infra)
- Ideation: `docs/ideation/2026-05-14-round3-fresh-pass-ideation.md` (2 unexplored survivors #6, #7 — out of scope here, sequenced after this roadmap)
- Origin plan referenced by PR #10: `docs/plans/2026-05-14-001-feat-mandatory-linkcheck-lang-gate-plan.md`
- Origin plans referenced by PR #1: `docs/plans/2026-05-12-005-feat-real-publish-verification-plan.md`, `docs/plans/2026-05-12-006-fix-article-scoped-collector-stack-desync-plan.md`
- Bug capture: `docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md`
- Relevant feedback memory: `feedback_hypothesis-dev-dep-ci-trap.md`, `feedback_plan-vs-code-drift.md`, `feedback_force-push-hook-workaround.md`, `feedback_test-autouse-verify-mock.md`, `feedback_brainstorm-prompt-as-desired-state.md`
- CI failure (PR #9): https://github.com/redredchen01/backlink-publisher/actions/runs/25838152234/job/75917558828
- CI failure (PR #1): https://github.com/redredchen01/backlink-publisher/actions/runs/25728881949/job/75548671649
