---
title: "fix: update test mock paths after http.py extraction (PR #194)"
type: fix
status: completed
date: 2026-05-22
claims: {}
---

# fix: update test mock paths after http.py extraction (PR #194)

## Overview

The `fix/publish-slim-and-test-fixes` branch (PR #194) bundles the `fix/http-session-helper`
refactor: `backlink_publisher/http.py` was introduced as a shared session helper, and
`llm_anchor_provider.py` + `image_gen/adapter.py` were updated to import and call
`http_post`/`http_get` from it rather than calling `requests.post`/`requests.get` directly.

The test files for these two adapters were not updated to match — they still mock
`...requests.post` / `...requests.get` at the module level, so the mocks no longer intercept
the actual HTTP call, and tests hit the real network. This plan fixes the 17 currently
failing tests:

- 13 in `tests/test_llm_anchor_provider.py`
- 3 in `tests/test_plan_backlinks_banner.py`
- 1 in `tests/test_no_monolith_regrowth.py` (projector.py ceiling became too loose
  after this branch trimmed its SLOC from 548 → 528)

## Problem Frame

`backlink_publisher.http.post` (aliased as `http_post` at the call site) is the name that
resolves when the adapter executes its HTTP call. Patching `...requests.post` patches a
name that is no longer called, leaving the real `requests.Session.post` reachable through
`http_post`. Every affected test therefore attempts a live DNS resolution, times out through
3 retry cycles, and either raises `DependencyError` or `AssertionError`.

The monolith-budget failure is unrelated to HTTP mocking but was introduced by the same
branch: `events/projector.py` shrank 20 SLOC on this branch while the ceiling wasn't
tightened, so headroom (52) exceeded the 50-SLOC policy maximum.

## Requirements Trace

- R1. All 3812 currently-passing tests continue to pass.
- R2. 17 currently-failing tests pass after the fix.
- R3. No test makes a real HTTP call; autouse guard fixtures intercept misses.
- R4. `monolith_budget.toml` ceiling for `projector.py` satisfies `headroom ≤ 50`.

## Scope Boundaries

- Do **not** remove `import requests` from `llm_anchor_provider.py` or
  `image_gen/adapter.py` — both still use `requests.exceptions.*` /
  `requests.Timeout` / `requests.ConnectionError` for retry exception matching.
- Do **not** change any source adapter logic (HTTP call sites, retry logic, exception
  handling) — this plan is test-only except for the budget toml.
- `test_bind_channel_cli.py::TestHappyPath` intermittent EPERM failure is NOT in scope —
  it was absent from the full-suite run (17 failures only); root cause is likely an OS-level
  lock on `~/.config/backlink-publisher/` and unrelated to mock paths.

## Context & Research

### Relevant Code and Patterns

- `src/backlink_publisher/http.py` — exposes `get`, `post`, `put` as module-level functions
  wrapping a shared `requests.Session`. Adapters import these as `http_get`/`http_post`.
- `src/backlink_publisher/publishing/adapters/llm_anchor_provider.py:38` —
  `from backlink_publisher.http import post as http_post`; call site is line 213.
- `src/backlink_publisher/publishing/adapters/image_gen/adapter.py:28` —
  `from backlink_publisher.http import get as http_get, post as http_post`; call sites
  are lines 95 and 192.
- `tests/test_adapter_hashnode.py` — canonical already-correct pattern:
  `patch("backlink_publisher.publishing.adapters.hashnode.http_post", ...)`.
- `tests/test_ghpages_banner.py` — canonical already-correct pattern for `http_get`:
  `"backlink_publisher.publishing.adapters.ghpages.http_get"`.

### Institutional Learnings

- `feedback_mock_patch_paths_after_extraction.md`: patch at the consumer reference — the
  dotted path must follow the name to where it was imported, not where it was defined.
  After `from backlink_publisher.http import post as http_post`, the correct patch target is
  `<adapter_module_path>.http_post`, not `backlink_publisher.http.post` or
  `...requests.post`.

## Key Technical Decisions

- **Patch `http_post` at the adapter module level, not at `backlink_publisher.http.post`**:
  Patching the local alias in the adapter module prevents any other consumer of `http.py`
  from being affected. This matches the established pattern in hashnode and ghpages tests.
- **Replace guard fixture target, not remove it**: The `block_real_network` autouse fixture
  in `test_llm_anchor_provider.py` provides belt-and-suspenders coverage if a test forgets
  its mock. The guard must be updated to block `http_post`, not `requests.post`, so it
  actually intercepts missed mocks.

## Implementation Units

- [ ] **Unit 1: Fix `test_llm_anchor_provider.py` mock paths**

**Goal:** All 13 failing llm tests pass by intercepting `http_post` instead of `requests.post`.

**Requirements:** R1, R2, R3

**Dependencies:** None

**Files:**
- Modify: `tests/test_llm_anchor_provider.py`

**Approach:**
- Replace the `block_real_network` autouse fixture's `monkeypatch.setattr` target from
  `"backlink_publisher.publishing.adapters.llm_anchor_provider.requests.post"` →
  `"backlink_publisher.publishing.adapters.llm_anchor_provider.http_post"`.
- New guard target string: `"backlink_publisher.publishing.adapters.llm_anchor_provider.http_post"`.
  Update the guard in the same commit as the per-test changes — updating the guard first
  leaves migrated tests without protection; updating per-test first leaves the guard
  intercepting a dead name.
- Apply the same substitution to every per-test `monkeypatch.setattr` call in the file
  (17 occurrences total: 1 autouse guard + 16 per-test monkeypatch calls — search for
  `llm_anchor_provider.requests.post`).
- The mock callable signatures (`fake_post`, `_never`, etc.) stay unchanged — `http_post`
  accepts the same call signature as `requests.post` at the adapter's call sites
  (`http_post(url, json=body, headers=headers, timeout=self.timeout_s)`).

**Patterns to follow:**
- `tests/test_adapter_hashnode.py` — `monkeypatch.setattr("...hashnode.http_post", fake_post)`

**Test scenarios:**
- Happy path: mocked `http_post` returns 200 JSON → `generate_candidates()` returns candidate list
- Error path: mock raises `requests.exceptions.ConnectionError` → retry logic fires, DependencyError on exhaustion
- Error path: mock returns 429 → retry logic fires
- Guard scenario: a test with no explicit mock patch should raise `AssertionError` from the
  autouse `block_real_network` fixture (guard works)

**Verification:**
- `PYTHONPATH=src pytest tests/test_llm_anchor_provider.py -q` passes (0 failures, ~25 tests)
- Elapsed time is < 5s (no network calls, no retries)

---

- [ ] **Unit 2: Fix `test_plan_backlinks_banner.py` mock paths**

**Goal:** 3 failing banner tests pass by intercepting `http_post`/`http_get` in the image_gen adapter.

**Requirements:** R1, R2, R3

**Dependencies:** None (parallel to Unit 1)

**Files:**
- Modify: `tests/test_plan_backlinks_banner.py`

**Approach:**
- Search for `image_gen.adapter.requests.post` (7 occurrences) and replace with
  `image_gen.adapter.http_post`.
- Search for `image_gen.adapter.requests.get` (1 occurrence) and replace with
  `image_gen.adapter.http_get`.
- The mock return values (`_post_ok(...)`, `_get_ok_bytes(...)`) stay unchanged —
  `http_post`/`http_get` return a `requests.Response`, same as before.
- `test_banner_none_when_use_image_gen_false` and `test_banner_none_when_token_file_missing`
  both patch `requests.post` to verify zero calls; update them to patch `http_post` for the
  same assertion. The test logic is unaffected since they only check `call_count == 0`.
- Note: `test_plan_backlinks_banner.py` has no `block_real_network` autouse guard fixture.
  The pytest-socket conftest block is the only network backstop for missed mocks in this
  file. R3 is satisfied for currently-failing tests; a future guard fixture is out of scope.

**Patterns to follow:**
- `tests/test_ghpages_banner.py` — `"backlink_publisher.publishing.adapters.ghpages.http_get"`

**Test scenarios:**
- Happy path (b64_json mode): mock `http_post` → adapter returns banner dict with `path`, `mime`, `sha`, `alt`, `source_url=None`
- Happy path (url mode): mock `http_post` returns URL, mock `http_get` fetches PNG bytes → `source_url` set to upstream URL
- Error path: mock `http_post` returns 401 → adapter returns `banner_status=auth_failed`
- Non-call verification: `use_image_gen=False` → `http_post.call_count == 0`

**Verification:**
- `PYTHONPATH=src pytest tests/test_plan_backlinks_banner.py -q` passes (0 failures)
- Elapsed time is < 5s (no network calls)

---

- [ ] **Unit 3: Tighten `monolith_budget.toml` ceiling for `events/projector.py`**

**Goal:** `test_policy_to_seed_drift[src/backlink_publisher/events/projector.py]` passes.

**Requirements:** R1, R4

**Dependencies:** None (independent of Units 1–2)

**Files:**
- Modify: `monolith_budget.toml`

**Approach:**
- Current: `ceiling = 580`, current SLOC = 528 (measured via `python -m radon raw -s`),
  headroom = 52 > 50 (fails policy).
- Policy formula: `ceiling = round_up_to_10(SLOC + 30) = round_up_to_10(558) = 560`.
- Update `ceiling` from `580` → `560`. Resulting headroom = 32 ≤ 50, satisfying R4.
- Append a note to `rationale` explaining the tightening: this branch trimmed 20 SLOC
  from `projector.py` (548 at seed → 528 post-branch) while the ceiling was inherited
  unchanged from main.

**Patterns to follow:**
- Existing rationale format in `monolith_budget.toml` — single-sentence amendment
  appended to the existing rationale string.

**Test scenarios:**
- Monolith drift test: `ceiling=560, SLOC=528, headroom=32 ≤ 50` → passes

**Verification:**
- `PYTHONPATH=src pytest tests/test_no_monolith_regrowth.py -k projector -q` passes

## System-Wide Impact

- **Unchanged invariants:** No adapter source changes — retry logic, exception handling,
  and call signatures in `llm_anchor_provider.py` and `image_gen/adapter.py` are untouched.
- **Other test files:** All other test files that already mock `http_post`/`http_get`
  correctly are unaffected.
- **CI socket block:** After this fix, the conftest autouse socket block + the updated
  `block_real_network` guard in `test_llm_anchor_provider.py` provide double coverage
  for that file. `test_plan_backlinks_banner.py` relies on socket block only.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| A test's mock callable doesn't match `http_post` call signature | `http_post(url, json=..., headers=..., timeout=...)` matches `requests.post` kwargs; existing `fake_post` callables already accept `**kwargs`, so no signature change needed |
| Missing a `requests.post` call site in the test file | After the change, run the full test file with `--timeout=10` to catch any test that still reaches the network |
| `projector.py` SLOC grows again next sprint | The new ceiling 560 provides 32 SLOC headroom; any growth ≤ 30 stays under ceiling; PRs adding to projector.py must bump ceiling in the same commit per existing policy |

## Sources & References

- Related PR: #194 (`fix/publish-slim-and-test-fixes`)
- Canonical correct mock pattern: `tests/test_adapter_hashnode.py`, `tests/test_ghpages_banner.py`
- Institutional learning: `[[mock-patch-paths-after-extraction]]`
- Monolith policy: `tests/test_no_monolith_regrowth.py` + `monolith_budget.toml`
