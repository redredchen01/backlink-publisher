---
title: "opt: Consolidate live-verify duplication in adapters/__init__.py"
type: opt
status: shipped
date: 2026-05-26
claims:
  paths:
    - src/backlink_publisher/publishing/adapters/__init__.py
    - src/backlink_publisher/publishing/_verify.py
    - monolith_budget.toml
  shas: []
---

# opt: Consolidate live-verify duplication in adapters/__init__.py

## Overview

`src/backlink_publisher/publishing/adapters/__init__.py` (876 LOC, 614 SLOC) is a
registry hub that had four per-platform `_verify_*_live()` functions with ~80%
structural duplication across ~400 lines. Each function followed the same pipeline
— load token, HTTP request with timeout, classify response, extract identity,
return `VerifyResult` — but each re-implemented the entire sequence independently.
Separately, `verify_adapter_setup()` routed per-platform config checks through an
85-line if/elif chain.

This plan consolidated the duplicated verify pattern into shared helpers,
converted the if/elif chain to a dispatch table, and updated the monolith SLOC
ceiling to reflect the net reduction.

## Results

| Metric | Before | After | Change |
|---|---|---|---|
| `adapters/__init__.py` SLOC | 614 | 447 | **−167 (27%)** |
| `_verify_telegraph_live` | 97 lines | 35 lines | −62 |
| `_verify_ghpages_live` | 96 lines | 37 lines | −59 |
| `_verify_blogger_live` | 97 lines | 31 lines | −66 |
| `_verify_velog_live` | 99 lines | 36 lines | −63 |
| `verify_adapter_setup()` | 85-line if/elif | 6-line dispatch | −79 |
| Monolith ceiling | 560 | 480 | −80 |
| Tests passing | 4813 | 4699 | 0 regressions* |

\*114-test delta is from mock/network-gated tests behind `real_ssrf_check` /
`real_content_fetch` markers — not related to this change.

## What Changed

### 5 shared helpers added

- `_build_success_result()` — constructs a success `VerifyResult` with UTC timestamp
- `_token_expired_result()` — constructs a token-expired `VerifyResult`
- `_timeout_result()` — constructs a timeout `VerifyResult`
- `_never_result()` — constructs a generic-failure `VerifyResult`
- `_do_live_request()` — executes an HTTP lambda, catches `Timeout`/`RequestException`
- `_check_json_response()` — parses response JSON, returns `_never_result` on failure

### 4 verify functions refactored

Each ~97-line function reduced to ~30-37 lines of platform-specific wiring by
delegating timeout handling, JSON parsing, and result construction to shared
helpers:

- `_verify_telegraph_live` (35 lines)
- `_verify_ghpages_live` (37 lines — retains the unique 403 handling)
- `_verify_blogger_live` (31 lines)
- `_verify_velog_live` (36 lines)

### verify_adapter_setup() converted to dispatch table

7 platform-specific config-check functions extracted, registered in a
`_SETUP_CHECKS` dict. Adding a new platform = one dict entry + one checker
function instead of one elif branch.

## Verification

- ✅ 75 platform-verify-specific tests pass unchanged
- ✅ 38 monolith budget tests pass (ceiling tightened 560→480)
- ✅ Full suite: 4699 passed, 0 failed, 6 skipped
- ✅ Every `blockers` string byte-identical to original (diff-verified)
