# ce:review (autofix) — Plan 2026-05-25-005 events projector correctness

- Scope: branch `feat/events-projector-fix` vs `origin/main` (b037364)
- Mode: autofix
- Reviewers (haiku): correctness, testing, maintainability, project-standards,
  reliability, adversarial, api-contract, kieran-python, learnings-researcher
- Verdict: **Ready to merge** — all applied; 4472 passed / 0 failed.

## Applied fixes (this run)

| # | Severity | Finding | Reviewer(s) | Fix |
|---|----------|---------|-------------|-----|
| 1 | P1 | Plan missing `claims:` block (post-2026-05-20 cutoff → plan-check exit 8) | project-standards (0.95) | Added `claims: {}` opt-out; `plan-check` now exits 0 |
| 2 | P1 | Multi/no-op resume drops `_unverified` suffix (output used transient set, ignored persisted `verified`) | correctness (0.88) | Output loops (main + early-exit) now OR the transient set with `not item.verified` |
| 3 | P1 | No end-to-end test for verify-FAILURE through the CLI | testing (0.80) + api-contract + correctness | Added `test_publish_cli_verification_failure_projects_unverified` (asserts publish.unverified, no confirmed, exit 5) |
| 4 | P2 | Cross-module private import `_checkpoint_path` | maintainability + kieran-python | Added public `checkpoint.checkpoint_path()`; projector uses it |
| 6 | P2 | No-op resume skips `project_run_safe` (no recovery for crash-before-projection) | correctness (0.71) | Early-exit path now calls `project_run_safe`; added `test_resume_noop_reemits_unverified_suffix_and_projects` |

## Reviewed, not changed (with rationale)

- **#5 (adversarial, 0.92/0.85): `verified` not in `_OPTIONAL_ITEM_FIELDS`; stale
  event kind on re-projection.** Mitigated by domain logic — resume only
  reprocesses `pending`/`failed`; a `done` item is terminal and its `verified`
  flag is never overwritten (api-contract reviewer, 0.85, independently judged the
  omission correct). Left as-is; documented as residual risk. Changing checkpoint
  reset semantics late carries more risk than the theoretical cascade.
- **kieran: narrow `except Exception` in `project_run_safe`.** Kept broad on
  purpose: this is a fail-safe boundary whose contract is "no projection error
  ever fails the publish." `KeyboardInterrupt`/`SystemExit` are `BaseException`
  (not caught), so they still propagate correctly. Narrowing would risk an
  unanticipated type failing the publish — the opposite of the goal.
- **`_HEALTH_SOURCE` reuse of `projection_cursor` (low, maintainability/kieran).**
  Pragmatic, no schema migration; queried by exact source key (no iteration that
  would hit `_detect_source`). Comment documents the reserved key.

## Residual risks (carried to the dashboard plan)

- History reducer's D2/D3 fixes stay UNWIRED in production until the dashboard adds
  a project-on-read (documented in plan Scope Boundaries).
- `publish.failed` has no cross-source dedup; U4 projects checkpoint-only, U5
  reconciles single-source.
- Fail-safe projection swallows errors; the `last_projection_ok_at`/`last_error`
  health marker is the visibility mechanism the dashboard must surface.
- Lost-write window: a success whose checkpoint write was lost is invisible to the
  projection (events.db is checkpoint-faithful, not reality-faithful).

## Learnings applied (learnings-researcher)

- `negative-assertion-locks-in-bug-2026-05-15.md` — the masking fixtures used
  `succeeded`; new tests exercise the production `done` path.
- `publish-history-helper-invariant-2026-05-20.md` — projection is read-only of
  canonical JSON; no direct history writes.
- `python-m-needs-main-module` / monolith budget — ceiling re-measured and bumped
  with rationale in the same PR.
