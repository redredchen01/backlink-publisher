---
date: 2026-05-14
topic: mandatory-linkcheck-lang-gate
---

# Mandatory Pre-Publish linkcheck + language_check Gate

## Problem Frame

Backlink-Publisher already imports `linkcheck.py` and `language_check.py` into `validate-backlinks`, but the resulting quality gate is partially fake:

1. **`language_matches` always returns `True`** — every branch of `language_check.py:57-71` falls through to `return True`, including the "detected something clearly different" branch (commented "Allow some flexibility"). The warning emitted in `validate_backlinks._enhance_payload` is therefore unreachable. Language-mismatched articles publish silently today.
2. **TOCTOU window between validate and publish** — `linkcheck.check_urls_strict` is invoked in `validate-backlinks`, but `publish-backlinks` never re-checks. A target URL reachable at validate-time can be 404 or redirected to a marketing fallback by publish-time (hours/days later), and the pipeline will still happily publish a dead-link backlink.
3. **Anchor text is never language-checked** — `detect_language` is only applied to `content_markdown`. A Chinese article whose anchor reads "learn more" passes; a Chinese-themed pipeline whose `branded_pool` was misconfigured with English anchors gets no signal.

Backlinks pointing to dead/redirected URLs are the lowest-bar quality failure and the most embarrassing to the operator. Language-mismatched anchors are the documented [feedback_llm-free-pool-sizing](../../../../.claude/projects/-Users-dex-YDEX-INPORTANT-WORK----0511-opencli-backlink-by-opencode/memory/feedback_llm-free-pool-sizing.md) failure mode that the work-themed generator's pool sizing helped mitigate but did not eliminate.

The work is to **make the gate actually fail**, close the TOCTOU window with a publish-time re-check, and add per-anchor language verification — while preserving the existing `--no-check-urls` escape hatch for dev/dry-run workflows and adding a separate, explicit publish-time escape hatch (`--skip-publish-time-check`) so the operator must opt-in to publishing without a fresh reachability check.

## Requirements

**Bug fix (prerequisite — must land before any gate-tightening below)**
- R1. Fix `language_check.language_matches` so it returns `False` when `detected` is a known language different from `requested`. Preserve the `detected == "unknown"` → `True` branch (intentional design — see R3).

**Language gate (body)**
- R2. `validate-backlinks` MUST mark the row as failed (`validation.status = "failed"`, see R11) when `detect_language(row.content_markdown)` returns a known language that does not match `row.language`. **Row-level abort** (not whole-batch): the row does not advance to subsequent stages. After processing all rows, `validate-backlinks` exits non-zero (suggested exit code 2 to match existing schema-validation behavior, see R11) if any row failed. Failed rows are NOT written to stdout (consistent with today's behavior — `validate_backlinks.py` calls `SystemExit(2)` before `write_jsonl` on schema failure).
- R3. `detect_language` returning `"unknown"` for the body remains a pass (consistent with prior design — "can't disprove, allow through"). Also: `language_matches("unknown", X)` returns `True` for any `X` (R1's fix preserves this branch). If `row.language` itself is `"unknown"` or a value outside the R4 enum (e.g., `"ja"`, `"de"`), R2 and R4 are skipped for that row with a WARN log line; the row passes.

**Language gate (anchor text)**
- R4. Every `link.anchor` (flat string per `schema.py:125-127`) on every row MUST be validated against `row.language` using a **codepoint-set heuristic**, not `detect_language`. The check fires unconditionally — independent of R2's body-language outcome — so a row can fail R4 even when R2 passes. **Exemption**: anchors classified as `branded` (`link.kind == "branded"`, or — if `link.kind` is not set on a link — anchors that match an entry in `branded_pool` for the row's target) skip R4 entirely. This is required because day-1 zh-CN runs legitimately use Latin brand names ("Apple", "Notion", "iPhone 15") as branded anchors.
  - `row.language == "zh-CN"` → non-branded anchor MUST contain at least one CJK codepoint in **BMP block `U+4E00..U+9FFF` only**. Extension A and beyond are out of v1.
  - `row.language == "ru"` → non-branded anchor MUST contain at least one Cyrillic codepoint (`U+0400..U+04FF`).
  - `row.language == "en"` → non-branded anchor MUST contain at least one Latin letter AND MUST NOT contain CJK or Cyrillic codepoints (strict: rejects mixed-script English anchors like `Travel to 東京 guide`; legitimate foreign-place-name anchors are expected to live in `branded_pool` and thus be exempted). Digits, standard ASCII punctuation, and Unicode general punctuation (em-dash, curly quotes) are allowed.
- R5. Anchors that fail R4 cause row-level abort with the same `validation.errors` shape as R2 (see R11). Whole-batch exit semantics: same as R2 — exit non-zero at end if any row failed.

**Reachability gate (validate-time — existing)**
- R6. `validate-backlinks`'s existing `check_urls_strict` behavior is preserved unchanged. Whole-batch abort with exit 4 on any unreachable URL remains the validate-time semantic.
- R7. The `--no-check-urls` flag is preserved as the explicit escape hatch.

**Reachability gate (publish-time — new)**
- R8. In `publish-backlinks`, **immediately before calling the adapter's `publish()` method for that row**, re-check the row's `target_url` and each URL in `row.links[*].url` for reachability. Because the existing `linkcheck.check_urls_strict` aborts on the **first** failure (incompatible with per-row continue semantics in R9), R8 MUST NOT call `check_urls_strict`. Use `linkcheck.check_urls(urls_for_this_row)` and inspect the returned dict, or add a small additive helper to `linkcheck.py` — keep R6 (validate-time `check_urls_strict`) unchanged.
- R9. The check uses `linkcheck._check_url_with_retry` (which already does up to **3 attempts total** with progressive 1s + 2s delays, against `ACCEPTABLE_CODES = {200, 301, 302}`). Worst-case ~3s wall time per dead URL. **No additional wrapping retry layer.** Per-URL failures are retried individually; if **any** of the row's URLs (target_url or any `row.links[*].url`) exhausts its retries, the **entire row** is skipped (no partial publishes). The row's output JSONL line is written with `status: "skipped_unreachable"` plus the failing URL. The corresponding checkpoint item (see Plan 2026-05-13-003) is left in status `pending` so a subsequent `--resume` retries it when upstream may have recovered (do NOT mark `failed` — that surfaces in `--list-runs` and the next run wouldn't retry). The batch continues to the next row; **no whole-batch abort**.
- R10. **Two independent flags** (the original "single symmetric flag" was rejected during brainstorm review as a silent dead-link footgun):
  - `--no-check-urls` continues to gate **validate-time** reachability (R6) only. Existing behavior preserved.
  - `publish-backlinks` gains a **new** `--skip-publish-time-check` flag (it does not exist today — verified). Default: **False** (checks ON — i.e., the new R8/R9 gate is opt-out, not opt-in). When `True`, R8/R9 are skipped. The flag's value is persisted into the checkpoint metadata so a `--resume` invocation honors the original posture.
  - Setting `--no-check-urls` does **NOT** also disable publish-time. Setting `--skip-publish-time-check` does **NOT** also disable validate-time. Neither flag affects language checks (R2, R5), which always fire.

**Error shape & observability**
- R11. Validation errors (R2, R5) populate a new `row.validation.errors: list[str]` array and set `validation.status = "failed"`. Failed rows are NOT written to stdout (mirrors today's schema-validation `SystemExit(2)` path). Validate-backlinks completion log includes count of passed/failed rows. Existing `row.validation.warnings` field is preserved as an empty list for backward compatibility with any reader (e.g., webui) that expects the key — actual emission of warnings is out of v1; whether to deprecate/remove the field entirely is an open question for the planner.
- R12. Publish-time skipped rows (R9) emit one structured WARN line in the format `[publish-backlinks] row_id=<id> status=skipped_unreachable url=<failing-url>`. The output JSONL row carries `status: "skipped_unreachable"` plus the failing URL field. Publish-backlinks completion log also emits an aggregate: count of `skipped_unreachable` rows + distinct unreachable hostnames (so a multi-row outage on one host pattern is glanceable, not buried in N separate WARN lines).

**Migration**
- R13. **Checkpoint re-validation on `--resume`.** When `publish-backlinks --resume` is invoked against a checkpoint created before R1-R12 lands, hard re-run R2 (body language match) and R5 (anchor R4 check) over every `pending` and `failed` item before resuming dispatch. Items that now fail are reclassified to `failed` in the checkpoint with `error_class = "retro_language_failed"` or `"retro_anchor_failed"` and skipped from this run. A one-shot INFO log line summarizes the reclassification count. This catches rows that were silently `validation.warnings` under the buggy `language_matches` and are now contractually `validation.errors`. Out-of-scope: re-checking already-`done` rows or rewriting their output JSONL — those have already been published; the harm is done.

## Success Criteria

- A test case with `row.language = "zh-CN"` and `content_markdown` written in English exits `validate-backlinks` non-zero (today: passes with a swallowed warning).
- A test case with a Chinese body whose anchor text reads "learn more" exits `validate-backlinks` non-zero (today: passes silently).
- A target URL that returns 200 at validate-time but 404 at publish-time results in **that row being skipped**, the rest of the batch publishing, and a clear WARN log naming the dead URL (today: dead backlink publishes).
- `--no-check-urls` disables only validate-time reachability (R6); the new `--skip-publish-time-check` flag (default off) is required to disable publish-time reachability (R8/R9). Neither flag disables language checks. Today: only `--no-check-urls` exists and only affects validate-time.
- `language_matches("en", "zh-CN")` returns `False`; `language_matches("unknown", "zh-CN")` returns `True` (today: both return `True`).
- The skipped row's checkpoint item is left in `pending` so the next `--resume` retries it; no partial-row publishes happen (if any single URL in a row fails, the whole row is skipped).
- **Test-suite health**: `pytest -q` exits 0 on the feat branch after R1–R12 land. Tests that asserted the buggy `language_matches==True` behavior on what should now be failures are updated with an explicit rationale comment naming this brainstorm. Planner runs a grep before plan finalization to count affected tests; the count belongs in the plan's effort estimate.

## Scope Boundaries

- **Out of v1**: canonical-URL consistency check (`<link rel=canonical>` parsing vs `target_url`). Deferred — needs HTML parse path that today's `linkcheck.py` doesn't have.
- **Out of v1**: `final URL == target URL` after redirect-chain following. Deferred. Today's `linkcheck` accepts `{200, 301, 302}` without inspecting the final-hop URL. (User explicitly chose "不进 v1，以后再说" for this group.)
- **Out of v1**: removing the `--no-check-urls` escape hatch.
- **Out of v1**: replacing the keyword-based `detect_language` with a dependency (e.g., `langdetect`, `lingua`). The R1 bug fix + R3 unknown-allowance keeps it serviceable.
- **Out of v1**: any LLM-driven anchor or body checking. Per [feedback_no-runtime-llm](../../../../.claude/projects/-Users-dex-YDEX-INPORTANT-WORK----0511-opencli-backlink-by-opencode/memory/feedback_no-runtime-llm.md), runtime path stays LLM-free.

## Key Decisions

- **`language_matches` is a real bug, not a scope choice.** Surfaced during brainstorm code-read. Fix lands first (R1) — without it, R2 has nothing to fail against. (Whether R1 ships alone as a hotfix or bundled with R2-R12 is an open product question — see Outstanding Questions.)
- **Codepoint-set heuristic for anchors, not `detect_language`.** Anchor surface forms are often 2-4 characters; the keyword-list scorer scores 0 on most anchors and falls into the "unknown → allow through" branch. Codepoint set is unambiguous and cheap (`any(0x4E00 <= ord(c) <= 0x9FFF for c in text)`).
- **CJK range locked to BMP only** (`U+4E00..U+9FFF`). Extension A and beyond deferred — adding them now is framework-ahead-of-need; widen on first real-world false-negative.
- **Row-level abort for new gates (R2, R5, R9); whole-batch abort kept only for R6 (existing).** New language gates at validate-time (R2 body, R5 anchor) and new reachability gate at publish-time (R8/R9) all use row-level abort — the row drops from downstream processing, the batch continues, and validate-backlinks exits non-zero at the end if any row failed. R6 (the **existing** `check_urls_strict` reachability check at validate-time) retains its whole-batch `SystemExit(4)` for now — touching it is out of v1 scope. The asymmetry between R6 (whole-batch) and R9 (per-row) for the *same* reachability check is acknowledged; whether to unify is in Outstanding Questions.
- **Reachability retry primitive locked to `linkcheck._check_url_with_retry`.** No additional wrapping retry layer. `adapters/retry.py` is reserved for adapter publish calls. Closes the prior "planner picks" deferred question.
- **Checkpoint state on publish-time skip = `pending`** (not `failed`, not a new `skipped_unreachable`). This lets the existing `--resume` retry the row when the upstream may have recovered, and avoids touching `--list-runs` / `--cleanup` semantics established in Plan 2026-05-13-003.
- **Failed validate rows are not written to stdout.** Consistent with today's `SystemExit(2)` path on schema-validation failure. The pipeline contract stays "validate's stdout is publish's stdin only for *valid* rows."
- **Two independent flags for reachability**: `--no-check-urls` (validate-time only, existing) + new `--skip-publish-time-check` (publish-time only, default off). Neither disables language checks. The original "single symmetric flag" plan was rejected during document-review as a silent dead-link footgun — splitting forces the operator to acknowledge that disabling publish-time reachability is a distinct, riskier action.
- **Branded-anchor exemption from R4** (added during document-review). zh-CN articles using "Apple"/"Notion"/"iPhone 15" are a day-1 reality; `branded_pool` membership exempts the anchor from codepoint check. Foreign place names in English anchors (e.g., 東京) are expected to live in `branded_pool` for the same reason.
- **`--resume` hard re-validates checkpoint** (R13). Closes retroactive-corruption risk for rows that passed the buggy `language_matches` and are still in `pending`/`failed`. Already-`done` rows untouched.
- **`detect_language("unknown")` continues to pass.** Heavy-markdown or code-leaning content legitimately scores zero on the keyword lists. Rejecting "unknown" would impose a "certify language" burden the operator can't always satisfy. Extended: `row.language == "unknown"` or out-of-enum (e.g., `ja`, `de`) skips R4 anchor check too.

## Dependencies / Assumptions

- The row payload already carries `row.language` (set by `plan-backlinks` via `detect_language` on `content_markdown`). Verified — `plan_backlinks.py:41` imports `detect_language`.
- The row payload structure: `row.links[*].anchor` is a **flat string** per `schema.py:125-127` (`for req in ("url", "anchor", "kind", "required"):`). Fixtures confirm: `{"url": ..., "anchor": "Example", "kind": ..., "required": True}`. R4 applies to this string field directly.
- Reachability re-check at publish-time uses `linkcheck._check_url_with_retry` — actual schedule: `MAX_RETRIES = 2` means **3 attempts total** with progressive `1s` then `2s` delays (`time.sleep(RETRY_DELAY * (attempt + 1))`), against `ACCEPTABLE_CODES = {200, 301, 302}`. `adapters/retry.py` is reserved for adapter `publish()` calls and is NOT extended for URL checks.
- 999 existing tests on the feat branch — current behavior of `validate-backlinks` (the "warning, never fails") may be implicitly relied upon by tests asserting `validation.status == "passed"` on payloads that *should* fail under R2/R5. Two confirmed assertion sites already located: `test_validate_backlinks.py:80` and `test_validate_backlinks.py:187`. The R1 bug fix alone will likely flip additional tests. Planner must grep + count before plan finalization.
- Per [feedback_test-autouse-verify-mock](../../../../.claude/projects/-Users-dex-YDEX-INPORTANT-WORK----0511-opencli-backlink-by-opencode/memory/feedback_test-autouse-verify-mock.md), publish-time HTTP introduces an autouse fixture retrofit across publish test files; this work belongs in the plan's test scope, not as overhead.

## Resolved During Brainstorm Review

All six "Resolve Before Planning" questions raised by document-review were closed before this artifact landed. Each decision below is now reflected in the requirements above and the Key Decisions block.

- **Branded-anchor exemption (R4)** — anchors classified as `branded` (`link.kind == "branded"`, or membership in the row's `branded_pool`) skip R4 entirely. Closes the day-1 false-reject risk on legitimate Latin brand names in zh-CN articles.
- **R4 en rule asymmetry** — kept strict for en (any-Latin AND none-of CJK/Cyrillic). Mixed-script English anchors like `Travel to 東京 guide` fail under R4 unless they live in `branded_pool` (which exempts them). Operator's expected workflow: foreign place names go in `branded_pool`.
- **`--no-check-urls` split** — two independent flags. `--no-check-urls` validate-time only; new `--skip-publish-time-check` publish-time only (default off). Neither affects language checks. (See R10.)
- **R6/R9 reachability asymmetry** — kept. R6 (validate-time) stays whole-batch abort; R9 (publish-time) is per-row skip. Operator must accept two slightly different mental models per command for v1; revisit if pain surfaces.
- **R1 hotfix split** — rejected. R1-R13 ship as a single PR. Operator accepts the bundling risk in exchange for one merge / one test sweep / one CHANGELOG entry.
- **Historical-data migration** — option (iii): hard re-validate `pending`/`failed` checkpoint items on `--resume`. Encoded as R13. Already-`done` rows are out of scope (already published).

## Outstanding Questions

### Deferred to Planning

- [Affects R11][Technical] Should `row.validation.warnings` (existing field) be hard-removed from `_enhance_payload`, or kept as an empty list for back-compat? Test `test_validate_backlinks.py:189` asserts `isinstance(output["validation"]["warnings"], list)` — keeping empty list is one-line change; removing requires test update. v1 default per R11: keep as empty list. Planner can promote to removal if it surfaces no downstream consumer.
- [Affects R4][Technical] Exact mechanism to identify a "branded" anchor at validate-time. Two paths: (a) `link.kind` field carries `"branded"` and validate trusts it; (b) `branded_pool` membership lookup from `row.target_config`. Planner picks based on what's already in the row payload after `plan-backlinks` and which is more robust against direct JSONL edits.

## Next Steps

All blocking questions resolved during brainstorm review. → `/ce:plan` for structured implementation planning.
