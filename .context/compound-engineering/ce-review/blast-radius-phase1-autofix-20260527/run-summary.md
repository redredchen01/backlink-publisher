# ce:review autofix — blast-radius Phase 1
**Run ID:** blast-radius-phase1-autofix-20260527  
**Date:** 2026-05-27  
**Mode:** autofix  
**Plan:** docs/plans/2026-05-27-003-feat-blast-radius-phase1-plan.md  
**Reviewers dispatched:** 9  
**Suite after fixes:** 5109 passed, 6 skipped ✅

---

## Auto-fixes Applied (13 files, ~80 insertions / 24 deletions)

### P0 — Critical correctness
- **`cli/plan_backlinks/core.py`** `_cell_gate_drop`: Add `domain = main_domain.rstrip("/")` before dict lookup — without this, a seed row with `main_domain="https://example.com/"` would not match the config key `"https://example.com"`, silently bypassing the cell gate entirely (correctness-reviewer + adversarial-reviewer consensus)
- **`tests/test_cli_plan_backlinks_cell_gate.py`**: Add `test_trailing_slash_in_row_does_not_bypass_gate` regression test

### P1 — Reliability: empty registry on config load
- **`cli/report_anchors.py`**: Add `import backlink_publisher.publishing.adapters  # noqa: F401` — without this, `_registered_platforms()` returned `[]` at parse time, causing every cell channel to raise `InputValidationError: unknown channel`
- **`cli/audit_state.py`**: Same fix
- **`cli/equity_ledger.py`**: Same fix
- **`cli/validate_backlinks.py`**: Add `from backlink_publisher._util.errors import InputValidationError` + `except InputValidationError: raise` before broad `except Exception` — cells.py fail-loud contract was silently swallowed

### P1 — Docs / standards
- **`docs/plans/2026-05-27-003-feat-blast-radius-phase1-plan.md`**: Add `claims: {}` to frontmatter (required for plan docs dated ≥ 2026-05-20 per AGENTS.md)
- **`docs/plans/2026-05-27-003-feat-blast-radius-phase1-plan.md`** line 253: Fix cull-candidate predicate — was `dofollow_status in (False, "uncertain")` (wrong), now `dofollow_status is False` with `"uncertain"` mapping to `"unverifiable"` (matches implementation)
- **`AGENTS.md`**: Update CLI entrypoints table from stale "(7)" count to accurate listing including `cull-channels`, `audit-state`, `preflight-targets`

### P2 — Code quality
- **`config/parsers/cells.py`**: `if not cells_section:` → `if cells_section is None:` — falsy non-None values (e.g. empty list `[]`) would have been incorrectly treated as "no cells"
- **`config/parsers/cells.py`** + **`config/types.py`**: Fix docstring examples from bare `example.com` to full `https://example.com` key (operators would copy the wrong format)
- **`cli/cull_channels.py`** `_classify`: Remove dead `or status is None` branch (all registered platforms have explicit dofollow status; None only for unregistered names)
- **`cli/cull_channels.py`** `_build_row`: Cache `df_status = dofollow_status(name)` to avoid redundant registry lookup; fix `_render_markdown` parameter shadowing (`rows` → `sorted_rows`)
- **`cli/plan_backlinks/core.py`**: `cell_gate_summary` guard `if cells or rows:` → `if cells:` — the old guard fired on every plan-backlinks run even with no cells configured
- **`tests/test_config_cells.py`**: Weak `or` assertion on overlap error message → `and` (both conflicting sites must be named)
- **`tests/test_cli_cull_channels.py`**: Add `test_invalid_log_level_raises_usage_error` (mirror of existing `test_invalid_format_raises_usage_error`)
- **`tests/test_cli_plan_backlinks_cell_gate.py`**: Replace `https://gated.com` (real registered domain) with `https://gated.example.com` (RFC-2606 safe)

---

## Residual Findings (require human judgment)

### P2 — advisory
- **`cull_channels.py` `_build_row`**: Still calls `dofollow_status` twice — once directly for `df_status`, once inside `_classify`. Minor; `_classify` is a reusable helper and the registry lookup is a cheap dict access. Suggested fix if desired: refactor `_classify` to accept pre-fetched status.
- **`cells.py` error message**: Unknown-channel error message includes `sorted(known)` — verbose at 21 channels (~300 chars). Suggested fix: truncate to first 10 + "...".

### P3 — advisory
- **`tests/test_cli_plan_backlinks_cell_gate.py`**: No test for `--from-csv` path through cell gate (low priority; `--from-csv` rows use same loop, covered implicitly).
