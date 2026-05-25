---
title: "test: Dedicated route-level coverage for Blogger/Medium OAuth WebUI routes (O4)"
type: test
status: completed
date: 2026-05-25
deepened: 2026-05-25
origin: docs/ideation/2026-05-25-codebase-optimization-backlog.md  # backlog item O4
---

# test: Dedicated route-level coverage for OAuth WebUI routes (O4)

## Overview

`webui_app/routes/oauth.py` (235 lines on `origin/main`) exposes four security-adjacent
Flask routes for Blogger OAuth and legacy Medium-token revocation. They have **no
dedicated route-level test file** — only the env-scoping context manager
`_oauthlib_insecure_transport` is well covered (in `tests/test_webui_unit3_security.py`).
This plan adds `tests/test_webui_routes_oauth.py` to cover the four *route handlers*
themselves: request → response, redirect targets/flash types, session mutation, and the
error branches. Zero `src/` changes, new file only — collision-free, executable from a
clean worktree off `origin/main` regardless of the in-flight `feat/phase1-channel-expansion`
work.

## Problem Frame

The recurring pain class in this codebase is *silent / mis-reported failure* (PR #156
false-success, velog null-after-retry, the projector drop bug). OAuth routes are exactly
where that bites worst: most handlers end in a broad `except Exception` that converts
failures into a flash redirect. **Caveat (verified against source):** the exception
handling is *not* uniform — `oauth-start`'s Flow-building block catches only `RuntimeError`
(`oauth.py:182`), while `oauth-callback` wraps the same transport gate + token exchange in a
broad `except Exception` (`oauth.py:232`). This asymmetry directly shapes the tests: an
`oauth-start` error mock must raise `RuntimeError` (a generic `Exception` would escape as an
unhandled 500, not a danger flash), and the callback's broad catch *mis-reports* a security
refusal (non-loopback gate `RuntimeError`) as the generic "授权处理失败" — a concrete instance
of the mis-report class this plan exists to pin. Whether these branches surface the right
flash type / fragment and whether the happy paths persist the right session + token state
is currently **unverified by any test that drives the routes**. O4 closes that gap. The
helpers are security-relevant (TLS-bypass gate, loopback assertion), so a regression here
could silently downgrade transport security or mishandle credentials.

## Requirements Trace

- R1. Each of the four routes has happy-path coverage asserting the redirect target,
  flash type, and fragment (`channel-blogger` / `channel-medium`).
- R2. Each route's error/guard branches are covered: missing form fields, non-loopback
  refusal on **both** the `oauth-start` leg (caught as `RuntimeError` → "OAuth 启动失败") and
  the `oauth-callback` leg (swallowed by broad `except` → generic "授权处理失败"), expired/
  absent OAuth session, downstream exception → danger flash.
- R3. The blank-`client_secret`-preserves-stored-value behavior (P3) is verified for both
  `save-blogger-oauth` and `oauth-start`.
- R4. The OAuth-start happy path verifies `session` is populated with `oauth_state`,
  `oauth_client_config`, `oauth_code_verifier` and that the browser is redirected to the
  Google auth URL.
- R5. The callback happy path verifies the token is saved and asserts **all three** OAuth
  `session` keys are absent afterward: `oauth_state`, `oauth_client_config`, AND
  `oauth_code_verifier`. (Source pops only the first two — `oauth.py:226-227` — so the
  `oauth_code_verifier` assertion is *expected to fail* against current code; that failure is
  the desired signal surfacing a real session-cleanup/PKCE-leak gap. Per Scope Boundaries,
  record it as a finding + open a separate fix; do not weaken R5 to only the two keys the
  code happens to pop.)
- R7. The OAuth `state` CSRF defense is exercised: a callback whose returned `?state` does
  not match `session['oauth_state']` must not write a token. (Source currently checks only
  *presence* of session state — `oauth.py` never compares returned-vs-session `state` — so
  this scenario surfaces a missing canonical OAuth-CSRF check; assert current behavior and
  record the gap as a finding per Scope Boundaries.)
- R6. No duplication of `_oauthlib_insecure_transport` coverage already in
  `test_webui_unit3_security.py`.

## Scope Boundaries

- **No `src/` changes.** This is test-only. If a test exposes a real bug in `oauth.py`,
  record it as a finding and open a separate fix — do not bundle a code fix into this plan.
  **Three such bugs are already anticipated** (assert-current-behavior + file a finding, do
  not silently pin as correct): (a) `oauth_code_verifier` never popped from session (R5);
  (b) no returned-vs-session `state` comparison in the callback (R7); (c) the callback's
  broad `except` mis-reporting a non-loopback security refusal as a generic failure (R2).
  Tag these tests with a `# TODO(O4-followup):` referencing a follow-up fix so the
  regression-lock is intentional and visible, not a silent certification of the bug.
- Do **not** re-test `_oauthlib_insecure_transport` set/restore/refuse behavior — it is
  already covered. Only exercise it transitively through the routes.
- No live Google OAuth / network. `google_auth_oauthlib.flow.Flow` is fully mocked.
- Not touching `tests/test_blogger_*` (adapter-level) — those test the adapter, not the
  WebUI routes.

## Context & Research

### Relevant Code and Patterns

- **Target under test:** `webui_app/routes/oauth.py` — routes:
  - `POST /settings/clear-medium-oauth` → `settings_clear_medium_oauth` (deletes `medium-token.json`)
  - `POST /settings/save-blogger-oauth` → `settings_save_blogger_oauth` (creds only)
  - `POST /settings/blogger/oauth-start` → `settings_blogger_oauth_start` (builds auth URL, redirects)
  - `GET  /settings/blogger/oauth-callback` → `settings_blogger_oauth_callback` (token exchange)
- **Fixture-structure pattern:** `tests/test_medium_login_routes.py` — inlines its own
  `client` fixture (sets `BACKLINK_PUBLISHER_CONFIG_DIR` + `_CACHE_DIR` to tmp,
  `TESTING=True`, returns `webui.app.test_client()`) and an autouse `_webui_state_isolated`
  fixture redirecting `webui_store` paths. **Copy the `client` + `_webui_state_isolated`
  shape from here — but NOT its `csrf_client` (see CSRF warning below).**
- **CSRF pattern (correct sibling):** `tests/test_webui_url_verify_routes.py` — these routes
  are governed by the app-level `_global_csrf_guard`, which validates
  `session['csrf_token']` against form field `csrf_token` (or the `X-CSRFToken` header).
  Mirror *this* file's CSRF approach. **Do NOT copy `test_medium_login_routes.py:86`'s
  `csrf_client`** — it seeds `session['medium_csrf']` and posts `_csrf_token`, which satisfy
  the *Medium blueprint's own* before_request guard, not the global guard that governs
  `oauth.py`. Verified at runtime: a medium-style POST to `/settings/save-blogger-oauth`
  returns **403**; seeding `session['csrf_token']` + matching form `csrf_token` returns
  **302**. Copying the Medium fixture would make every OAuth POST test 403 instead of
  exercising the handler.
- **Existing partial coverage to NOT duplicate:** `tests/test_webui_unit3_security.py`
  → `TestOauthlibInsecureTransportContext` (set/restore unset, restore prior, restore on
  exception, refuse non-loopback, accept loopback variants).
- **Contract-level coverage that exists:** `tests/test_webui_route_contract.py` (route
  registration only — not behavior).
- Helpers the routes call: `helpers.security._oauth_callback_uri`,
  `helpers.security._safe_flash_redirect`; config: `config.load_config`, `config.save_config`,
  `config.save_blogger_token`, `config._config_dir`.

### Institutional Learnings

- **CSRF in tests** (`reference_webui_csrf_architecture`): `_global_csrf_guard` enforces a
  token on every POST/PUT/PATCH/DELETE, keyed on `session['csrf_token']` vs form
  `csrf_token` / `X-CSRFToken` header. Build a CSRF helper that seeds `session['csrf_token']`
  via `client.session_transaction()` and sends the same value on the three POST routes; the
  GET callback route needs the plain `client` (no token). The `medium_csrf` / `_csrf_token`
  pair is a *different* (Medium-blueprint-local) mechanism — do not use it here.
- **Lazy-import mock paths** (`feedback_mock_patch_paths_after_extraction`): `oauth.py`
  does `from google_auth_oauthlib.flow import Flow` and `from ...blogger_api import _SCOPES`
  *inside* the handler bodies. Because the import binds fresh at call time, `mock.patch`
  must target the **source** — `google_auth_oauthlib.flow.Flow` — not a name on the `oauth`
  module. Same for `save_blogger_token` / `json_from_creds` (imported inside the callback):
  patch them at `backlink_publisher.config.save_blogger_token` /
  `...blogger_api.json_from_creds` (their definition sites).
- **Config isolation** (`feedback_config_paths_must_respect_env_var`,
  `feedback_never_smoke_test_real_save_endpoints`): never hit real save endpoints; the
  `client` fixture's per-test `BACKLINK_PUBLISHER_CONFIG_DIR` keeps `save_config` /
  `os.remove(medium-token.json)` on tmp paths. `load_config`/`save_config` can be either
  exercised against the isolated dir or mocked — prefer mocking for the branch-specific
  assertions (e.g., forcing `save_config` to raise) and real-dir for the happy path.

### External References

- Not needed — repo has a strong local pattern (`test_medium_login_routes.py`,
  `test_webui_url_verify_routes.py`) for CSRF-aware WebUI route tests. Skipping external
  research.

## Key Technical Decisions

- **Inline all fixtures (no cross-test import).** The codebase has *zero* cross-test
  fixture-import precedent (stated explicitly in `test_medium_login_routes.py`'s header).
  Inline `client` + `_webui_state_isolated` (copied from `test_medium_login_routes.py`) and a
  **freshly-built** CSRF helper that seeds `session['csrf_token']` (per the global guard —
  NOT the Medium `csrf_client`). Rationale: consistency with the established inlining pattern
  beats DRY; but the CSRF token mechanism must match the guard that actually governs these
  routes, or every POST test 403s without exercising the handler.
- **Mock `Flow` at its source module.** Because of the lazy in-handler import, patching the
  `oauth` module attribute would not intercept the call. Patch `google_auth_oauthlib.flow.Flow`.
  Rationale: avoids a green-but-not-actually-mocked false pass.
- **Split into two units by mock surface.** Credential-save / medium-clear routes need only
  config-dir isolation; the OAuth-flow routes need a mocked `Flow` + session setup. Keeping
  them separate keeps each test's mock scaffolding minimal and the failures legible.
- **`_is_loopback_uri` gets at most a tiny micro-test block — verify non-duplication first.**
  `test_webui_unit3_security.py` already exercises loopback variants (`localhost` /
  `127.0.0.1` / `::1`) and non-loopback rejection transitively. Do **not** restate those
  True cases (violates R6). The only arguably-new path is an explicit non-loopback IP literal
  (`http://10.0.0.5/cb` → `False`). Note: the source `try/except Exception` around
  `urlparse(uri).hostname` (`oauth.py:42-45`) is effectively unreachable — `urlparse` does
  not raise on arbitrary strings; it returns a `ParseResult` whose `.hostname` is `None`,
  already handled by `(host or '')`. Do not write a test claiming to cover a "parse-exception
  path"; if anything, test the `None`-hostname input returning `False`. Keep this block to
  ≤2 assertions or omit it.

## Open Questions

### Resolved During Planning

- *Is the OAuth env-scoping logic already tested?* — Yes. `TestOauthlibInsecureTransportContext`
  in `test_webui_unit3_security.py` covers it; this plan must not duplicate it (R6). The real
  gap is the route handlers.
- *Which CSRF mechanism for the POST routes?* — the app-level `_global_csrf_guard`
  (`session['csrf_token']` + form `csrf_token` / `X-CSRFToken`), seeded the
  `test_webui_url_verify_routes.py` way — **not** the Medium-local `csrf_client`. Plain
  `client` for the GET callback.
- *What is the correct `Flow` mock target?* — `google_auth_oauthlib.flow.Flow` (source),
  because of the in-handler lazy import.

### Deferred to Implementation

- Exact `MagicMock` shape for `Flow.from_client_config(...).authorization_url(...)` return
  (`(auth_url, state)` tuple) and `.credentials` / `.code_verifier` attributes — settle by
  reading the real call sites while writing the test.
- Whether forcing `save_config` to raise is cleaner via `mock.patch` on
  `webui_app.routes.oauth.save_config` (imported at module top — *this* one is a top-level
  import, so the consumer-module target IS correct here, unlike `Flow`). Verify the import
  style per symbol before choosing the patch target.
- **Is the Flask session server-side or the default signed (not encrypted) client cookie?**
  `oauth-start` writes the full `client_config` — including `client_secret` — into
  `session['oauth_client_config']` (`oauth.py`). With the default client-side cookie session,
  the OAuth client secret is serialized to the browser (signed, not encrypted). This is a
  credential-confidentiality question the test cannot settle alone: confirm the session
  backend, and decide whether R4 should additionally assert/flag secret-in-cookie exposure or
  whether it is an accepted loopback-only-deployment risk. *(Surfaced by security-lens review;
  needs a human decision — not auto-resolved.)*

## Implementation Units

- [ ] **Unit 1: Credential-save + Medium-clear route tests**

**Goal:** Cover `settings_save_blogger_oauth` and `settings_clear_medium_oauth` end to end.

**Requirements:** R1, R2, R3, R6

**Dependencies:** None (new file, off `origin/main`).

**Files:**
- Create: `tests/test_webui_routes_oauth.py` (with inlined `client`, `_webui_state_isolated`
  fixtures + a global-guard CSRF helper that seeds `session['csrf_token']`)

**Approach:**
- Inline `client` + `_webui_state_isolated` from `tests/test_medium_login_routes.py`; build
  the CSRF helper the `_global_csrf_guard` way (seed `session['csrf_token']`, send matching
  form `csrf_token` / `X-CSRFToken`) — **not** the Medium `csrf_client`.
- POST the three routes with a valid global CSRF token. Assert HTTP 302 + `Location`/flash
  via the `_safe_flash_redirect` contract (query params encode flash type + msg + fragment —
  assert the fragment and the `flash_type` discriminator, not the full localized message
  string).
- For `save-blogger-oauth`: top-level imports `load_config`/`save_config` → patch at
  `webui_app.routes.oauth.save_config` / `...load_config` (consumer-module target is correct
  here *because* these are module-top imports — contrast the lazy `Flow` import in Unit 2).

**Patterns to follow:**
- `tests/test_medium_login_routes.py` (`client` + `_webui_state_isolated` fixture structure,
  redirect assertions)
- `tests/test_webui_url_verify_routes.py` (global-guard CSRF seeding + POST route + flash
  assertion shape — the authoritative CSRF pattern for these routes)

**Test scenarios:**
- Happy path — `save-blogger-oauth` with valid `client_id`+`client_secret` → `save_config`
  called with those creds; 302 redirect to `/settings`, flash `success`, fragment
  `channel-blogger`.
- Edge case — `save-blogger-oauth` with blank `client_secret` but a stored
  `cfg.blogger_oauth.client_secret` present → stored secret preserved and passed to
  `save_config` (mock `load_config` to return a cfg with an existing secret). (R3)
- Error path — `save-blogger-oauth` missing `client_id` (and no stored) → 302, flash
  `warning`, message-intent "请填写", fragment `channel-blogger`; `save_config` NOT called.
- Error path — `save_config` raises → 302, flash `danger`, intent "保存失败".
- Happy path — `clear-medium-oauth` when `medium-token.json` exists in the isolated config
  dir → file removed; 302, flash `success`, fragment `channel-medium`.
- Edge case — `clear-medium-oauth` when the token file is absent → still 302 flash
  `success` (no error raised).
- Error path — `clear-medium-oauth` where `os.remove` raises (patch to raise) → 302, flash
  `danger`, intent "清除失败".

**Verification:**
- New file runs green under `pytest tests/test_webui_routes_oauth.py` with the four-fixture
  conftest isolation intact; no real `~/.config/backlink-publisher` files touched (assert via
  the tmp config dir).

---

- [ ] **Unit 2: OAuth-flow route tests (start + callback)**

**Goal:** Cover `settings_blogger_oauth_start` and `settings_blogger_oauth_callback`,
including session mutation, the loopback-refusal → danger branch on **both** legs, the
`state`-CSRF and session-cleanup gaps, and the error branches.

**Requirements:** R1, R2, R3, R4, R5, R6, R7

**Dependencies:** Unit 1 (reuses the same fixtures in the same file).

**Files:**
- Modify: `tests/test_webui_routes_oauth.py`

**Approach:**
- Patch `google_auth_oauthlib.flow.Flow` (source target — see Key Decisions). Build a
  `MagicMock` where `Flow.from_client_config.return_value.authorization_url.return_value =
  ("https://accounts.google.com/o/oauth2/auth?...", "state-xyz")` and `.credentials` is a
  stub consumable by a patched `json_from_creds`.
- For `oauth-start`: drive with the global CSRF token, then read `session` via the Flask
  test client's session transaction to assert the three OAuth keys were set and the response
  redirects to the mocked auth URL. (R4) **Mock shaping caveat:** `oauth-start`'s Flow block
  catches only `RuntimeError` (`oauth.py:182`) — error-path mocks must raise `RuntimeError`,
  and the happy-path `Flow` mock must not leak a stray `AttributeError` (it would escape as a
  500, not a flash).
- To hit the **non-loopback refusal** on `oauth-start` without real env: patch
  `webui_app.routes.oauth._oauth_callback_uri` to return e.g. `https://prod.example.com/cb`
  → `_oauthlib_insecure_transport` raises `RuntimeError` → caught by the dedicated
  `except RuntimeError` → danger flash "OAuth 启动失败". (Exercises the gate through the route,
  satisfying R2 without duplicating the context-manager unit tests. First confirm
  `_oauth_callback_uri` is a patchable function on the `oauth` module, not an inlined call.)
- For `oauth-callback`: drive with the plain `client` (GET), seed session via a transaction,
  patch `Flow` + `save_blogger_token` (`backlink_publisher.config.save_blogger_token`) +
  `json_from_creds` (`...blogger_api.json_from_creds`) at their **definition sites** (lazy
  in-handler imports — see `feedback_mock_patch_paths_after_extraction`); assert token saved
  and **all three** OAuth session keys absent afterward (the `oauth_code_verifier` assertion
  is expected to fail — surface as a finding, R5). The callback wraps its transport gate +
  `fetch_token` in a broad `except Exception` (`oauth.py:232`), so a non-loopback refusal on
  the callback leg is mis-reported as the generic "授权处理失败" — assert that current behavior
  and flag it (R2/R7).

**Patterns to follow:**
- `tests/test_medium_login_routes.py` `_make_mock_pw` factory style (inlined mock builder)
- Flask test-client `with client.session_transaction() as sess:` for session seeding/reads
- `feedback_mock_patch_paths_after_extraction` for lazy-import patch targets

**Test scenarios:**
- Happy path — `oauth-start` with valid creds + loopback callback (mocked `Flow`) → session
  gets `oauth_state`/`oauth_client_config`/`oauth_code_verifier`; 302 redirect to the mocked
  auth URL. (R4)
- Edge case — `oauth-start` blank `client_secret` with stored secret → preserved before the
  Flow build. (R3)
- Error path — `oauth-start` missing creds → 302 flash `warning` "请填写...再登入"; `Flow`
  never constructed.
- Error path — `oauth-start` with non-loopback `_oauth_callback_uri` → `RuntimeError` from
  the transport gate caught → 302 flash `danger` "OAuth 启动失败". (R2)
- Error path — `oauth-start` where `save_config` raises before the Flow build → 302 flash
  `danger` "凭据保存失败".
- Happy path — `oauth-callback` with seeded session + mocked `Flow.fetch_token` +
  `save_blogger_token` → token persisted; assert `oauth_state`, `oauth_client_config`, AND
  `oauth_code_verifier` all absent from session; 302 flash `success` "授权成功". The
  `oauth_code_verifier` assertion fails against current source (never popped) → surface as a
  finding. (R5)
- Error path — `oauth-callback` with `?error=access_denied` query → 302 flash `danger`
  "Google 拒绝授权"; no token write.
- Edge case — `oauth-callback` with empty session (no `oauth_state`) → 302 flash `warning`
  "授权会话已过期".
- Error path — `oauth-callback` where `flow.fetch_token` raises → 302 flash `danger`
  "授权处理失败"; session not corrupted.
- Error path (R2, security) — `oauth-callback` with valid seeded session but non-loopback
  `_oauth_callback_uri` → the transport gate's `RuntimeError` is swallowed by the broad
  `except Exception` and mis-reported as generic "授权处理失败" (not distinguished from a
  network failure). Assert this current behavior and flag the mis-report as a finding — the
  credential-bearing leg should refuse the TLS bypass *legibly*.
- Error path (R7, security) — `oauth-callback` with seeded `session['oauth_state']='A'` but
  request `?state=B` (mismatch) → assert current behavior (source does **not** compare
  returned-vs-session `state`, so it proceeds to token exchange). This surfaces the missing
  OAuth-CSRF `state` check as a finding; do not assert a rejection the code doesn't perform.
- Edge case — `_is_loopback_uri` direct (≤2 assertions, non-duplicative): non-loopback IP
  `http://10.0.0.5/cb` → `False`; `None`-hostname input → `False`. Do **not** restate the
  `localhost`/`127.0.0.1`/`::1` True cases (already covered in `test_webui_unit3_security.py`,
  R6). Omit entirely if it adds nothing beyond the existing transitive coverage.

**Verification:**
- `pytest tests/test_webui_routes_oauth.py` green; both flow routes covered with `Flow`
  fully mocked (no network); session assertions pass; `OAUTHLIB_INSECURE_TRANSPORT` is not
  leaked into the test process env after the suite (sanity-assert it is unset/restored at
  module teardown, mirroring the existing context-manager test discipline).

## System-Wide Impact

- **Interaction graph:** Routes call `_safe_flash_redirect` (assert via its query-param
  contract, not localized strings — keeps tests resilient to copy changes), `load_config` /
  `save_config` / `save_blogger_token`, and the `_oauthlib_insecure_transport` gate. No new
  production wiring.
- **Error propagation:** The whole point of the suite is to pin that every failure mode lands
  as the *correct* flash type rather than a 500 or a false-success — directly advances the
  "UX honesty" theme this backlog item belongs to.
- **State lifecycle risks:** `oauth-callback` happy path must leave `session` clean (keys
  popped) — explicitly asserted (R5). Token file writes stay on the tmp config dir.
- **API surface parity:** none — test-only, no route signatures change.
- **Integration coverage:** the session-seeding + real `_safe_flash_redirect` exercise the
  request→session→redirect chain that pure helper unit tests cannot prove.
- **Unchanged invariants:** `oauth.py` behavior is not modified; if a test reveals a genuine
  bug (e.g., a swallowed error that should not be), it is logged as a finding and handled in a
  separate fix PR, preserving this plan's test-only scope.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Mocking `Flow` on the `oauth` module instead of its source → test passes without actually mocking | Patch `google_auth_oauthlib.flow.Flow` at the source; add an assertion that the mock was called to prove interception. |
| Asserting on full localized flash strings → brittle to copy edits | Assert on `flash_type` + `fragment` (the structural discriminators) and at most a short substring of intent. |
| **Copying the Medium `csrf_client` → every OAuth POST 403s without exercising the handler** (verified: medium-style POST = 403, global-token = 302) | Seed `session['csrf_token']` + send matching `csrf_token` / `X-CSRFToken` per `_global_csrf_guard` (mirror `test_webui_url_verify_routes.py`); do NOT seed `medium_csrf` / post `_csrf_token`. Plain `client` for the GET callback. |
| `_oauth_callback_uri` not patchable (inlined call vs module function) → non-loopback scenarios silently no-op | Before relying on the patch, confirm `_oauth_callback_uri` is imported as a module-level name on `oauth` (it is — `from ..helpers.security import _oauth_callback_uri`); patch `webui_app.routes.oauth._oauth_callback_uri`. |
| Asserting only the two session keys the code pops → silently certifies the `oauth_code_verifier` leak as correct | R5 asserts all three keys; the verifier assertion is *expected to fail* and surfaces the bug (TODO-tagged finding), not a green lie. |
| A test surfaces a real `oauth.py` bug mid-implementation | Out of scope to fix here — record as a finding, keep the test (xfail or assert current behavior with a `# TODO(O4-followup):` referencing a follow-up), open a separate fix. Three are pre-identified: verifier-not-popped (R5), no `state` comparison (R7), callback mis-report (R2). |
| `OAUTHLIB_INSECURE_TRANSPORT` leaking across tests | The route uses the scoped context manager; add a teardown sanity assertion that the env var is unset after the suite. |

## Documentation / Operational Notes

- Execute from a **clean worktree off `origin/main`** (e.g. `bp-oauth-route-tests`), not the
  `feat/phase1-channel-expansion` tree — keeps the new file collision-free with concurrent WIP.
- No docs/config/runbook changes. New test participates in the standard `pytest tests/` suite
  (Python 3.11+3.12 CI, `PYTHONHASHSEED=0`).

## Sources & References

- **Origin (backlog item O4):** `docs/ideation/2026-05-25-codebase-optimization-backlog.md`
- Target under test: `webui_app/routes/oauth.py` (`origin/main`, 235 LOC)
- Pattern: `tests/test_medium_login_routes.py` (fixtures, `csrf_client:86`),
  `tests/test_webui_url_verify_routes.py`
- Do-not-duplicate: `tests/test_webui_unit3_security.py` → `TestOauthlibInsecureTransportContext`
- Memory: `reference_webui_csrf_architecture`, `feedback_mock_patch_paths_after_extraction`,
  `feedback_never_smoke_test_real_save_endpoints`, `feedback_config_paths_must_respect_env_var`
