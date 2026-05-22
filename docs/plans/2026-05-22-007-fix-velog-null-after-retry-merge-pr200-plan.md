---
title: "fix: Merge velog null-after-retry fix (PR #200)"
type: fix
status: done
date: 2026-05-22
claims: {}
---

# fix: Merge velog null-after-retry fix (PR #200)

## Overview

Merge the already-complete PR #200 (`fix/velog-null-after-retry-diagnostics`) into
main. The fix is fully implemented, tested, and CI-clean. No new code is needed.

## Problem Frame

`velog_graphql.py:530` (main branch) raises `AuthExpiredError` unconditionally when
`writePost` returns `null` after retry. In reality, `writePost` returns null for every
failure class — auth expiry, content rejection, rate-limit, slug collision. The single
exit path fires `mark_expired("velog")`, halts the entire batch, and the log line is
truncated to 200 chars, leaving operators with no diagnosis signal.

Observed error:
```
{"adapter": "velog-graphql", "phase": "null-after-retry", "id": "25c6db66a02d33a5",
 "resp": "{'data': {'writePost': None}}"}
```

## Requirements Trace

- R1. Cookie-alive failures must NOT trigger `mark_expired` or batch halt.
- R2. Operators must be able to distinguish auth expiry from content rejection without
  rebinding the channel.
- R3. Batch continues after a single-row content rejection.

## Scope Boundaries

- No new code changes. Fix is in PR #200.
- Only action: merge PR #200 into main.

## Context & Research

### Relevant Code and Patterns

- `src/backlink_publisher/publishing/adapters/velog_graphql.py:522–533` — current null path (main)
- `src/backlink_publisher/_util/errors.py` — `BannerUploadError` is the precedent for DependencyError siblings that skip mark_expired

### What PR #200 Delivers

| File | Change |
|---|---|
| `_util/errors.py` | `ContentRejectedError(DependencyError)` — sibling (not subclass) of `AuthExpiredError`; exit code 3; docstring explicitly forbids `mark_expired` |
| `adapters/velog_graphql.py` | `_probe_session_alive(session)` + `_save_null_artifact()`; null-after-retry block replaced with probe→classify→raise |
| `cli/publish_backlinks.py` | `except ContentRejectedError` clause (between `BannerUploadError` and `DependencyError`) so batch continues on single-row rejection |
| `templates/_tab_history.html` | Amber "内容被拒（Cookie 有效）" badge when `error_class == "content_rejected"` |
| `AGENTS.md` | Diagnostic runbook under "What about Velog?" |
| `tests/test_adapter_velog_graphql.py` | 19 new tests, 45 total velog tests |

### Merge Readiness

| Check | Status |
|---|---|
| plan-claims-gate CI | ✅ pass |
| test (3.11) | ✅ pass |
| test (3.12) | ✅ pass |
| File conflicts with main (`920f5a6`) | ✅ none |
| Base branch delta since PR creation | settings UI only (`920f5a6`): no overlap |

## Key Technical Decisions

- **`ContentRejectedError` is DependencyError sibling, not AuthExpiredError subclass**: avoids MRO routing through `_handle_auth_expired`; matches BannerUploadError precedent.
- **Fail-safe direction is AuthExpiredError**: probe network errors fall through to existing auth-expiry path; no silent swallowing.
- **Probe timeout = 10s, single attempt**: minimises latency on genuine auth failures.

## Open Questions

### Resolved During Planning

- *Do we need new code?* No — PR #200 is complete.
- *Is there a merge conflict?* No — `920f5a6` only touches `settings.*` files; zero overlap with PR #200.

### Deferred to Implementation

None.

## Implementation Units

- [ ] **Unit 1: Merge PR #200**

**Goal:** Land the velog null-after-retry fix on main.

**Requirements:** R1, R2, R3

**Dependencies:** None

**Files:**
- No edits required — all changes are already in PR #200.

**Approach:**
- `gh pr merge 200 --squash --delete-branch` (or equivalent merge strategy used by repo convention)
- Verify CI checks on the merge commit pass
- Confirm `ContentRejectedError` exists in `src/backlink_publisher/_util/errors.py` on main post-merge

**Test scenarios:**
- Test expectation: none — this unit is a merge action, not a code change. All test coverage ships in PR #200 (19 new velog tests, full 3807-test suite passing).

**Verification:**
- `grep ContentRejectedError src/backlink_publisher/_util/errors.py` returns a match on main
- `grep _probe_session_alive src/backlink_publisher/publishing/adapters/velog_graphql.py` returns a match on main
- `gh pr view 200 --json state` shows `MERGED`

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Squash merge creates SHA drift in linked worktrees | No other open PRs depend on PR #200 code paths |
| Main CI regression post-merge | CI runs on merge commit; monitor before closing the worktree |

## Sources & References

- Branch: `fix/velog-null-after-retry-diagnostics`
- PR: [#200](https://github.com/redredchen01/backlink-publisher/pull/200)
- Institutional pattern: `docs/solutions/` → ContentRejectedError sibling pattern (BannerUploadError precedent)
