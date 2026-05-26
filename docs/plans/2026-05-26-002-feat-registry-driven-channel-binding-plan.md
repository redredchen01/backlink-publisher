---
title: "feat: Registry-driven, auth-type-templated channel binding surface"
type: feat
status: active
date: 2026-05-26
deepened: 2026-05-26
origin: docs/brainstorms/2026-05-26-overview-inline-channel-binding-requirements.md
---

# feat: Registry-driven, auth-type-templated channel binding surface

## Overview

The `/settings` 渠道綁定總覽 lists every `active_platforms()` channel (currently 27) but the binding UI is hardcoded for 6 of them. ~21 channels show a dead `Configure ↓` anchor and ~20 misreport `bound=False / "No adapter configured"`. This plan **roots out the drift**: the binding surface becomes registry-driven, rendering a per-channel binding UI selected by the channel's **auth type**, so adding a channel needs no new hardcoded HTML. It also fixes the offline `bound` misreport at its source.

Mastodon and the live-browser/OAuth channels keep their existing bespoke flows (see Scope Boundaries); they are surfaced in the panel but not rewritten.

## Problem Frame

Two desynchronized sources (see origin):
- **渠道綁定總覽** (`#section-dashboard`) — registry-driven via `active_platforms()` → 27 channels.
- **发布渠道** (`#section-channels`) — 6 hardcoded cards.

Two stacked defects:
1. **Dead anchors / no binding surface** for ~21 channels.
2. **`bound` misreport**: `binding_status.get_channel_status()` derives `bound` from `verify_adapter_setup(mode='offline')`, an if/elif chain in `adapters/__init__.py` that only knows 7 platforms; the other 20 hit the terminal `raise DependencyError("No adapter configured for platform: X")` despite being registered and (mostly) implementing `available(config)`.

Root cause: registry-driven list vs hardcoded binding UI. Fix = make the binding UI and the offline check registry-driven.

## Requirements Trace

- R1. Binding surface driven by `active_platforms()`; new/retired channel auto-syncs, no new hardcoded HTML (origin R1).
- R2. Per-channel binding UI selected by auth type (origin R2, Approach table).
- R3. Eliminate dead `Configure ↓`; bind/configure inline (origin R3).
- R4. Existing 6 working channels + blogger OAuth + velog/medium live-browser: zero regression (origin R4).
- R5. Fix offline `bound` so no registered channel misreports "No adapter configured" (origin R5).
- R6. ANON channels (txtfyi/rentry/telegraph) render "免绑定·就绪", no bind button (origin R6).
- R7. dofollow/nofollow labels from registry `dofollow_status()` (origin R7).
- R8. Offline `bound` (and thus the per-card "Verify Token" gating) is correct for all channels once R5 lands. Live connectivity verify via `/api/<channel>/verify` is covered for ANON in Unit 5; non-ANON live-verify coverage is asserted in Unit 7's coverage test (offline-correct), with deeper live probes out of scope (origin R8).
- R9. ANON connectivity probe is **non-destructive** — never a real public publish (origin R9).
- R10-R13. Interaction states: loading/disabled, inline error, bound summary + partial refresh, clear-credentials confirm (origin R10-R13).
- R14-R17. Security: SSRF-validate user URL inputs, CSRF via global guard, channel whitelist (path traversal), secrets never logged; credential files `0o600` via `atomic_write` (origin R14-R17).
- R18. livejournal USERPASS inline form via `store_credentials`, throwaway-account warning (origin R18).

## Scope Boundaries

- **mastodon deferred** — its live-browser bind has unresolved `storage_state`↔`real-chrome-profile` incompatibility + instance-aware login; separate round (origin).
- **OAUTH (blogger) and live-browser (velog, medium) not rewritten** — surfaced in the panel, reuse existing flows (origin R4).
- **No publish-pipeline / adapter logic changes** — only WebUI binding surface, `binding_status`, the offline-check dispatch, and new credential save routes. *Caveat (review):* the **save-time** SSRF validation of `site_url`/`site` is in scope; the **connect-time resolved-IP re-validation** (DNS-rebinding defense at fetch) touches the publish/verify fetch path and is therefore carved into a **separate `_util/net_safety` change/PR** — name the single fetch chokepoint all stored-URL fetches pass through. Save-time validation ships here; connect-time hardening tracked as a dependency.
- Retiring channels (hashnode/writeas, per memory PR #204) are handled by `visibility()`/`HIDDEN_FROM_UI` filtering, not special-cased here. **Verify their `visibility()` is actually flipped** before relying on the 24-count (they may still be `active`, which would inflate the loop and disagree with the drift test).
- **Trust model**: loopback-only operator, no per-user auth; the new credential save/clear routes inherit the loopback + bind-origin posture of `bind.py`. The R13 clear-confirm dialog is UX only, not an authz control.
- **mastodon stays surfaced but non-actionable** (deferred stub, see Unit 3) — not rewritten; its live-browser bind (storage_state↔real-chrome-profile incompatibility + instance-aware login) is a separate round.

## Context & Research

### Auth-type classification (all 27 — drives template selection)

| Auth type | Binding UI | Channels | In scope |
|---|---|---|---|
| **ANON** | "免绑定·就绪" badge + non-destructive probe | telegraph, txtfyi, rentry | ✅ |
| **TOKEN** | single-secret paste form | devto, writeas | ✅ |
| **TOKEN+FIELDS** | multi-field paste form (token + config fields) | ghpages(repo), beehiiv(pub_id), ghost(site_url), notion(db_id), wordpresscom(site), hashnode, tumblr(5 OAuth1 strings) | ✅ |
| **PASTE-BLOB** (cookie-export) | `{"cookies":[...]}` JSON paste form | csdn, habr, jianshu, juejin, note, pikabu, segmentfault, substack, zhihu | ✅ |
| **USERPASS** | username+password form → `store_credentials` | livejournal, cnblogs | ✅ |
| **OAUTH** | reuse existing redirect flow | blogger | ⏸ surface only |
| **LIVE-BROWSER** | reuse existing flow / defer | velog, medium, **mastodon** | ⏸ velog/medium reuse; mastodon deferred |

Coverage: registry-driven templates mechanically cover **24** channels; 3 keep bespoke flows.

### Relevant Code and Patterns

- `src/backlink_publisher/publishing/adapters/__init__.py` — registrations (L120-335) + offline check if/elif chain (L391-476, terminal raise L476). **The file to refactor for R5.** ~16 of the 20 missing platforms already implement `available(config)`.
- `webui_app/binding_status.py` — `get_channel_status` (L105), `bound` computed L126-132; `_publish_backend_for`, `_identity_for`, `hidden_from_ui()`/`HIDDEN_FROM_UI` (L64/L83 PEP-562), `_DOFOLLOW_BY_CHANNEL`.
- `webui_app/routes/token_paste.py` — `_ALLOWED` dict (L34, only ghpages/devto), `save_channel_token` (L40, form-POST+PRG), notion separate route (L108, 2-field precedent). Saves via per-platform `save_fn` (which use `atomic_write` 0600) + defensive `chmod 0600` re-check (L93-96). Empty field = "leave as-is".
- `webui_app/templates/_settings_channel_token_paste.html` — TOKEN/TOKEN+FIELDS template precedent; context vars at header (L3-14); hidden `csrf_token` field (L62); hardcodes `-token.json` basename (L37, must parameterize for `-credentials.json`).
- `webui_app/templates/_channel_card_macro.html` — `dashboard_channel_card(name, status, bindable)`; Verify/Bind buttons + `Configure ↓`→`#channel-<name>`; `data-field` hooks for partial refresh.
- `webui_app/static/js/channel-binding.js` — `callJson` (6s timeout, content-type guard, 404/403 handling), `X-CSRFToken` header, 1s debounce, `renderResult` partial refresh + live bound-badge update, `_triggerChannelBind` (velog→`runVelogLogin()`, else opens collapse + clicks `.bind-channel-btn`).
- `src/backlink_publisher/publishing/adapters/livejournal_api.py:92` & `cnblogs_api.py:27` — same-signature `store_credentials(config, username, password)`; livejournal stores `hpassword=md5`, cnblogs stores plaintext. Both `atomic_write` 0600 + stat/chmod re-check.

### Institutional Learnings

- **Invert drift check when invariant becomes dynamic** (`docs/solutions/logic-errors/invert-drift-check-when-invariant-becomes-dynamic-2026-05-18.md`): never module-level `assert SET == registered_platforms()` — fires mid-import on half-loaded registry. Put any coverage assertion at registry bottom-of-file or test-time only.
- **Grep `_DOFOLLOW_BY_CHANNEL` before enabling a channel** (`grep-dofollow-map-before-shipping-adapter-2026-05-20.md`): nofollow channels must be surfaced with the nofollow label (per origin R7), not as dofollow targets.
- **save_config bypass drops sections** (`save-config-write-paths-bypass-preservation-2026-05-15.md`): persist TOKEN+FIELDS config via a **narrow-merge helper** (read raw TOML → overwrite exact keys → snapshot → atomic write); do NOT extend `_SAVE_CONFIG_KNOWN_ROOTS`. `save_config` does not round-trip `[targets.*]`/`[sites.*]`/`[anchor_alarm]`/`[anchor.proportions]`/`[llm.anchor_provider]`.
- **Never smoke-test live `/save-*`** (`never-smoke-test-real-save-endpoints-2026-05-19.md`): absent fields = "clear". Verify only via sandboxed `BACKLINK_PUBLISHER_CONFIG_DIR` or pytest autouse config sandbox.
- **Credential rotation covers all mutation sites** (`credential-rotation-tests-cover-bootstrap-race-2026-05-19.md`): enumerate bootstrap/rotation/rebind/clear; per-site `threading.Barrier` test where concurrency reachable.
- **CSRF**: `_global_csrf_guard` (PR #143) auto-covers every POST. In route tests seed `session['csrf_token']` (pattern: `test_webui_url_verify_routes`); do NOT copy `medium_login`'s `csrf_client` (≠ global guard → 403). See memory `reference_webui_csrf_architecture`.
- **fetch().json() content-type guard** + **wire-at-all-sites fragility** (memory `feedback_wire_token_paste_channel_five_sites`): the registry-driven surface is precisely the fix for the 5-site `UndefinedError` fragility; confirm live wiring sites in `webui_app/` rather than trusting the count.
- **URL input upstream validation**: `is_placeholder_url()` (RFC 2606) + `normalize_url_for_fetch`; sockets blocked by autouse conftest, live SSRF opt-in via `real_ssrf_check`.

## Key Technical Decisions

- **Fix the offline check by auth-type dispatch + credential-artifact probe — NOT by blanket `available()` delegation** (R5). ⚠️ Correction from review (feasibility/adversarial, grep-verified): base `Publisher.available()` `return True` unconditionally; velog/medium/mastodon/**livejournal** do NOT override it, so delegating to `available()` would mark them `bound=True` with zero credentials — a false-positive worse than the current bug, and it would regress velog/medium (which today have bespoke offline cookie/token checks). The dispatch must be: ANON → always-ok; **preserve the existing bespoke branches for blogger/medium/velog/telegraph/ghpages/notion/devto** (they encode load-bearing semantics — e.g. medium's deliberate Brave-exclusion, blogger's oauth-object check); for the ~20 previously-unhandled channels, probe the **credential/cookie/profile artifact presence** (or call `available()` ONLY where the adapter genuinely overrides it — detect via `type(adapter).__dict__`/MRO, and handle `available()` arity variance, e.g. `instant_web.available(cls)` takes no config); else (unregistered) → raise. *Rationale:* `available()==True` from the base means "environment OK", not "bound"; the bound probe must check artifacts.
- **`bound` feeds the publish-select gate, not just the dashboard badge** (R5 ripple): `webui_app/__init__.py:128` → `registry.py:476` registers a `bound`-predicate lambda over `get_channel_status`. A false-positive `bound=True` admits an uncredentialed channel into publish-select (then crashes at publish). Unit 1 must treat this predicate as a first-class consumer and regression-test it.
- **Auth-type derives from the EXISTING `BindDescriptor.backend`, not a new parallel field** (R2). ⚠️ Correction from review: `_manifest_types.py` already defines `BindBackend = Literal['chrome','token-paste','oauth','cookie','cdp']` and 9 manifests declare it; inventing a second `auth_type` axis is the exact drift this plan exists to kill. Reconcile to one source of truth — extend/refine `backend` (it needs finer granularity: token vs token_fields vs paste_blob vs userpass vs anon) and expose `auth_type(channel)` derived from it + `extras.credential_shape`. If a distinct field is truly required, add a drift test asserting `backend`↔`auth_type` agree. **Either way, `register()` and `_manifest_types` must gain/extend the kwarg** — `register()` has a fixed signature with no `**kwargs`, so adding a raw key to a `*_MANIFEST` dict (which is `**`-splatted) raises `TypeError` at import. ~18 manifests are `UiMeta`-only stubs with no bind descriptor, so this is ~18 net-new classifications, not a one-field touch.
- **Retrofit the existing overview block, but extract per-auth-type partials into their own templates** (origin; learning #3): the feature *is* the existing 渠道綁定總覽, so a sibling page is wrong — but new binding bodies live in `_settings_binding_<authtype>.html` partials to avoid growing the settings.html monolith (monolith budget). Record: the hardcoded `#section-channels` block is retired/absorbed for the 24 templated channels; blogger/velog/medium keep their existing partials referenced from the panel.
- **One generic save route, dispatch by module not symbol** (R18, collision): a registry-driven `save-channel-credential` route maps channel→auth-type→saver, calling the adapter module's own `store_credentials`/`save_*` (preserves livejournal hpassword vs cnblogs plaintext divergence). Import modules, never the bare `store_credentials` symbol (would shadow).
- **Reuse the form-POST + hidden `csrf_token` (PRG) flow** for new save routes (matches token_paste precedent, auto-covered by `_global_csrf_guard`); keep the JSON `X-CSRFToken` flow for verify/probe.
- **Narrow-merge for TOKEN+FIELDS config keys** (ghost `site_url`, wordpresscom `site`, ghpages `repo`, beehiiv `publication_id`): never extend `save_config` known-roots. The read-modify-write must hold an **flock** around the whole window (reuse `telegraph_api.py` flock-with-jitter; atomic_write's rename does NOT prevent stale-read lost-update across two save routes / a racing CLI), and needs an explicit **key-delete mode** for the clear path.
- **SSRF validated at save AND use, not just save** (R14): host-string validation at save-time is necessary but DNS-rebinding-bypassable; `_util/net_safety` must also re-validate the *resolved IP at connect-time* on every verify/publish fetch of `site_url`/`site`. *Rationale:* ghost/wordpresscom URLs are stored then fetched later — save-time-only checks are a TOCTOU hole.
- **TOKEN+FIELDS is a two-file write (credential file + config key); validate-all-before-write, write the bound-authoritative artifact last** (data integrity): a crash mid-pair must leave the channel reading as *unbound*, never *falsely bound*. Bound state (Unit 1 `available()`) derives from one authoritative artifact.

## Open Questions

### Resolved During Planning

- *Which channels can the unified surface cover?* — 24 (ANON/TOKEN/TOKEN+FIELDS/PASTE-BLOB/USERPASS); blogger OAUTH + velog/medium live-browser reuse existing; mastodon deferred. (research auth-type audit)
- *Is there a `_SETUP_CHECKS` dict to extend?* — No; it's an if/elif chain. Replace with registry delegation. (research)
- *Where does auth-type metadata live?* — manifest field + registry accessor. (decision above)
- *Sibling page vs retrofit?* — retrofit the overview, extract partials. (decision above)
- *store_credentials collision?* — dispatch by module. (research)

### Deferred to Implementation

- Exact field schema per TOKEN+FIELDS channel (which fields are config.toml vs token-file) — derive per-adapter at implementation from each `available()`/`_load_*`; tumblr's 5-string OAuth1 blob may warrant its own sub-shape.
- Whether ANON non-destructive probe reuses `/api/<channel>/verify` (offline-only after R5) or needs a lightweight GET of the form page — confirm no `/api/<channel>/dry-run` route is introduced (it does not exist today).
- For PASTE-BLOB channels lacking a `save_<channel>_credentials` helper, whether to add per-channel savers or one generic cookie-blob saver keyed by channel.
- Final identity backfill (`_identity_for` only populates telegraph today) — out of scope unless trivial per auth type.
- The empty / whitespace / clear matrix per auth type: for TOKEN+FIELDS decide whether "clear" is whole-credential (file + all config keys) or field-level; a legitimately-empty optional config field is indistinguishable from "unset" under the leave-as-is contract — accept the limitation or add an explicit sentinel/checkbox.
- TOML comment/ordering preservation through narrow-merge: declare in-scope (requires a comment-preserving parser like `tomlkit`) or an explicit non-goal. The round-trip survival test proves keys survive, not comments.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

Data flow for one channel card:

```
active_platforms() ──► for each channel:
    auth_type(channel) ─┐
                        ├─► binding_status.get_channel_status(channel)
                        │       └─ bound = registry-delegated available() probe (R5 fix)
                        ▼
   panel renders _settings_binding_<authtype>.html partial inside #channel-<name>
        ANON         → ready badge + "测试连通" (non-destructive)
        TOKEN        → 1 secret field        ─┐
        TOKEN+FIELDS → secret + config fields  ├─► POST /settings/save-channel-credential
        PASTE-BLOB   → cookies JSON textarea   │       └─ dispatch channel→authtype→adapter.<saver>()
        USERPASS     → username + password    ─┘            (atomic_write 0600; module-dispatch)
        OAUTH/LIVE   → embed/link existing partial (blogger/velog/medium); mastodon deferred
                        ▼
   channel-binding.js: verify/save → renderResult() partial refresh (no full reload)
```

Offline `bound` dispatch (R5) — auth-type-driven, preserving bespoke branches (NOT blanket available() delegation):

```
verify_adapter_setup(channel, config, mode='offline'):
    if auth_type(channel) == ANON:                 return ok    # txtfyi/rentry/telegraph
    if channel in BESPOKE_OFFLINE {blogger, medium, velog, telegraph,
                                   ghpages, notion, devto}:      keep existing bespoke branch  # zero-regression
    if adapter genuinely OVERRIDES available(config):            return ok if available() else raise(reason)
    if credential/cookie/profile artifact present for channel:   return ok
    if channel registered (no creds yet):          raise DependencyError(specific "not bound: <what's missing>")
    else:                                          raise DependencyError("not registered")  # only truly unknown
```
⚠ Base `Publisher.available()` returns True unconditionally — never treat inherited-base available() as "bound". Detect a real override (MRO/`__dict__`) and tolerate arity variance (`available(cls)` vs `available(cls, config)`).

## Implementation Units

### Phase 1 — Backend correctness foundation

- [ ] **Unit 1: Registry-delegated offline `bound` check (fix misreport)**

**Goal:** Replace the 7-branch if/elif + terminal raise in `verify_adapter_setup(mode='offline')` with registry-delegating dispatch so no registered channel reports "No adapter configured".

**Requirements:** R5, R8

**Dependencies:** None

**Files:**
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py` (offline branch L391-476)
- Modify: `webui_app/binding_status.py` (only if blocker text needs per-auth-type wording)
- Test: `tests/test_binding_status.py` (extend; create if absent) and/or `tests/test_adapter_verify_setup.py`

**Approach:**
- Auth-type-driven dispatch (see High-Level Technical Design): ANON → pass; **preserve the existing bespoke branches for blogger/medium/velog/telegraph/ghpages/notion/devto verbatim** (zero-regression — they encode bespoke semantics base `available()` does not); for the ~20 previously-unhandled channels probe credential/cookie/profile **artifact presence** (call `available()` only where the adapter truly overrides it, via MRO/`__dict__` detection + arity tolerance); only genuinely unregistered → terminal raise.
- ⚠️ Never treat base `Publisher.available()==True` (the unconditional default) as bound — that false-positives velog/medium/mastodon/livejournal (no override) and admits them to the publish-select gate.
- Keep `DependencyError` messages specific (not the generic "No adapter configured") so blockers stay meaningful.
- Do NOT add a module-level drift assertion here (learning #1).

**Patterns to follow:** existing `NotionAPIAdapter.available()` / `DevtoAPIAdapter.available()` (real overrides taking `config`); contrast `instant_web.available(cls)` (no config arg) for arity variance.

**Test scenarios:**
- Happy path: `get_channel_status` returns `bound=True` for each of the 24 templated channels when credentials present (parametrized).
- Edge: ANON (txtfyi, rentry, telegraph) → `bound=True` with no credentials, no blocker.
- Error path: a TOKEN channel with no token file → `bound=False` with a specific blocker (not "No adapter configured").
- **False-positive guard (P0): velog with NO cookies / medium with NO token+oauth+playwright / livejournal with NO credentials → `bound=False`** (proves base-available() is not mistaken for bound).
- **Publish-select gate (P0): an uncredentialed browser-backed channel (velog, no cookies) is NOT admitted to publish-select** (the `webui_app/__init__.py:128` / `registry.py:476` bound-predicate consumer).
- Regression: characterization test capturing current bound + blocker text for the existing 7 channels (incl. medium's Brave-exclusion, blogger's oauth-object check) → byte-equal after refactor.
- Edge: truly unregistered channel name → still raises terminal `DependencyError`.
- Edge: multi-entry chain (velog=[GraphQL, BrowserDispatcher], medium=[API, Brave, Browser]) — assert the bound probe keys on the right entry, not the first.

**Verification:** No active registered channel yields blocker text "No adapter configured"; the 24 in-scope channels report correct bound state offline; no uncredentialed channel reports bound=True or reaches publish-select.

- [ ] **Unit 2: `auth_type` manifest field + registry accessor**

**Goal:** Classify every platform's auth type as registry metadata driving template selection.

**Requirements:** R2

**Dependencies:** None (parallel with Unit 1)

**Files:**
- Modify: `src/backlink_publisher/publishing/_manifest_types.py` (extend `BindBackend` Literal granularity, or add `auth_type` to `BindDescriptor`)
- Modify: `src/backlink_publisher/publishing/registry.py` (`register()` signature to accept/store the field; add `auth_type(channel)` accessor deriving from `backend` + `extras.credential_shape`)
- Modify: `src/backlink_publisher/publishing/adapters/_manifests.py` (~18 `UiMeta`-only stubs need the classification added)
- Test: `tests/test_registry.py` (extend) + `tests/test_r9_extension_readiness.py` (drift)

**Approach:**
- ⚠️ Do NOT add a raw `auth_type` key to a `*_MANIFEST` dict — `register()` has a fixed kwarg signature with no `**kwargs` and manifests are `**`-splatted, so an unknown key raises `TypeError` at import. The field must be a real `register()`/`_manifest_types` parameter (mirror the `visibility=` gate at `registry.py:287`).
- **Reconcile with the existing `BindBackend` enum** (`chrome/token-paste/oauth/cookie/cdp` — already on 9 manifests). Derive `auth_type` from `backend` + `extras` (finer granularity: token vs token_fields vs paste_blob vs userpass vs anon) rather than introducing a parallel axis. If both fields must coexist, add a drift test asserting they agree (no two-source drift).
- ~18 manifests are `UiMeta`-only stubs (csdn/habr/jianshu/… ) — classify all of them; first-ship drift test parametrizes over all `active_platforms()`.
- Drift assertion lives in the test (or registry bottom-of-file), never module-level (learning #1).

**Test scenarios:**
- Happy path: `auth_type(c)` returns the classified type for each of the 27 (parametrized against the audit table).
- Drift: every `active_platforms()` channel resolves a known auth_type (fails loudly if a new channel ships without one).
- Consistency: where both `backend` and derived `auth_type` exist, assert they agree.
- Import smoke: package imports without `TypeError` after the new field is added to all manifests (catches the splat-kwarg trap).

**Verification:** `auth_type()` resolves for all active platforms; adapter package imports clean; drift + consistency tests green.

### Phase 2 — Registry-driven binding UI

- [ ] **Unit 3: Per-auth-type binding partials + registry-driven panel**

**Goal:** Render, per channel, the auth-type partial inside `#channel-<name>` so the panel covers all 24 templated channels; eliminate dead `Configure ↓`.

**Requirements:** R1, R2, R3, R6, R7

**Dependencies:** Unit 2

**Files:**
- Create: `webui_app/templates/_settings_binding_token.html`, `_settings_binding_token_fields.html`, `_settings_binding_paste_blob.html`, `_settings_binding_userpass.html`, `_settings_binding_anon.html`
- Modify: `webui_app/templates/settings.html` (`#section-dashboard` loop renders partial by `auth_type`; retire/absorb hardcoded `#section-channels` for templated channels, keep blogger/velog/medium partials referenced)
- Modify: `webui_app/templates/_channel_card_macro.html` (Configure↓ → inline expand; ANON ready badge; nofollow label from status)
- Modify: `webui_app/helpers/contexts.py` (inject `auth_type` per channel into `dashboard_channels`)
- Test: `tests/test_settings_dashboard_rendering.py` (extend)

**Approach:**
- Generalize the token-paste template into auth-type partials; parameterize token-file basename (`-token.json` vs `-credentials.json`).
- ANON → ready badge, no bind button (R6). nofollow channels → existing nofollow warning row (R7, `_DOFOLLOW_BY_CHANNEL`).
- Respect `HIDDEN_FROM_UI`/`visibility()`; drift test subtracts `len(HIDDEN_FROM_UI)`.
- **DOM-id invariant (zero-regression, P1)**: `channel-binding.js` `_triggerChannelBind` hard-depends on `getElementById('channel-'+name)` + `.bind-channel-btn` + the global `runVelogLogin()`. Retiring `#section-channels` must **preserve `#channel-<name>` nodes and `.bind-channel-btn` for blogger/velog/medium**, and new inline partials must not collide with those ids. Add a JS-level regression check that bind resolves a panel for every bespoke channel post-retirement.
- **mastodon dead-card guard (P1)**: mastodon is `visibility='active'` but has no bespoke partial and no working bind (deferred). Rendering the loop over it must NOT produce a dead `Configure↓`/Jinja-include error. Render an explicit disabled **"浏览器登录 — 即将支持(需单独评估)"** stub with no actionable anchor (or flip mastodon to a non-active visibility so `active_platforms()` excludes it). Either way: no dead anchor (satisfies R3).
- **Information architecture**: the single overview must define ordering/grouping for 24+ cards — group by bind-state (unbound-actionable first → bound → ANON ready) and within group by dofollow/referral value; do not ship raw `active_platforms()` order. Add a bound/total count summary for the first-time (all-unbound) entry state.
- **Bespoke-vs-templated coexistence**: bespoke flows (OAuth redirect, live-browser) get a distinct affordance label telegraphing the interaction model — e.g. "Connect via Google ↗" (OAuth), "Bind in browser →" (live-browser) — vs inline forms for templated cards, so the operator can predict the action before clicking.

**Patterns to follow:** `_settings_channel_token_paste.html` + notion 2-field; `dashboard_channel_card` macro; existing `_settings_channel_{blogger,velog,medium}.html` (the bespoke partials to keep referenced); `_render` auto-inject (memory `feedback_render_auto_inject_over_per_route`).

**Test scenarios:**
- Happy path: rendering settings produces a binding body for each of the 24 templated channels; no `Configure ↓` dead anchor remains.
- Edge: ANON channels render ready badge, no bind button / credential form.
- Edge: nofollow channel renders the nofollow warning.
- Edge: a `visibility='hidden'/'retired'` channel does not render.
- Edge (P1): mastodon renders the deferred stub with NO dead anchor / no Jinja error; bespoke channels (blogger/velog/medium) keep `#channel-<name>` + `.bind-channel-btn` resolvable by the JS.
- Integration: adding a stub channel to the registry with a known auth_type auto-renders its partial with no template edit (root-cause verification).

**Verification:** Every in-scope channel has an inline binding body; mastodon shows a non-actionable deferred stub (no dead anchor); bespoke bind buttons still resolve; new registry channel auto-appears.

- [ ] **Unit 4: Generic credential save route (TOKEN / TOKEN+FIELDS / PASTE-BLOB / USERPASS)**

**Goal:** One registry-driven save route dispatching channel→auth-type→adapter saver, preserving per-adapter credential shape.

**Requirements:** R1, R14, R15, R16, R17, R18

**Dependencies:** Unit 2

**Files:**
- Modify: `webui_app/routes/token_paste.py` (generalize `_ALLOWED` to registry-derived) or Create: `webui_app/routes/channel_bind_save.py`
- Modify: `src/backlink_publisher/config.py` (narrow-merge helper for TOKEN+FIELDS config keys, if not already present) — reuse existing `save_*` savers where they exist
- Test: `tests/test_token_paste_routes.py` (extend) + `tests/test_channel_bind_save.py` (new)

**Execution note:** Add characterization coverage for the existing ghpages/devto/notion save paths before generalizing `_ALLOWED`, to lock zero-regression (R4).

**Approach:**
- **Validate everything before any write** (channel whitelist, SSRF, JSON parse/schema, field presence), then dispatch.
- Dispatch by **module**, not symbol (livejournal vs cnblogs `store_credentials` collision). Call adapter's own saver so hpassword/plaintext divergence is preserved.
- TOKEN+FIELDS config keys → narrow-merge (learning #6), never extend `_SAVE_CONFIG_KNOWN_ROOTS`. The narrow-merge RMW holds an **flock** (reuse `telegraph_api.py` flock-with-jitter) and supports **key-delete** (for clear). For the two-file pair (credential file + config key), order writes so a crash leaves the channel **unbound, not falsely bound** (write whatever `available()` keys on last). Token/credential files → `atomic_write` 0600; prefer `fchmod` on the temp fd before rename over re-stat-by-path (TOCTOU).
- `channel` validated against `registered_platforms()` whitelist; reject `/`, `..`, null bytes before any path/file construction — applies to **both save and clear/unlink** (R16).
- SSRF-validate user URL inputs (ghost `site_url`, wordpresscom `site`): `is_placeholder_url()` + normalize + reject private/loopback/link-local/metadata hosts; https-enforce — at save **and** re-validate resolved IP at fetch time (R14).
- **PASTE-BLOB cookie blob**: enforce `MAX_CREDENTIAL_BYTES` cap, schema-validate (list of objects, allowlisted keys name/value/domain/path/expiry, reject unknown/extra keys), and reject cookies whose `domain` does not match the channel's expected host — before write.
- Never log password/hpassword/token/cookies; error回显 names the **field, never the value**; bound-summary mask reveals at most a fixed prefix and **never the suffix** of short secrets (R17).
- Empty field = "leave as-is"; whitespace-only → strip → "leave as-is" (never persist `"  "`). Clear is a coherent teardown: unlink credential file **and** narrow-merge-delete the channel's config keys under the same flock.
- CSRF via `_global_csrf_guard`, form-POST + hidden `csrf_token` (R15) — covers both save and clear.
- **Off-loopback hard-disable (P0 security)**: the save AND clear routes must call `_refuse_when_allow_network()` + `_check_bind_origin_or_abort()` (matching `bind.py`/`url_verify.py`). Without them, `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` exposes an unauthenticated credential-write/clear endpoint (the global CSRF guard's own comment notes Lax loses effective protection off-loopback). The generalized `token_paste.py` precedent also lacks these — fixing it here closes the gap at scale.
- **Exception-flash leak**: the `token_paste.py` precedent interpolates raw `{e}` into the flash redirect (`f"保存 {channel} token 失败: {e}"`). The catch-all must reduce saver/`atomic_write` exceptions to a generic message before `_safe_flash_redirect` — a raised exception whose `str()` contains a token-shaped string must never reach the response.

**Patterns to follow:** `save_channel_token` (L40) + notion route (L108); livejournal `store_credentials`; `safe_write.atomic_write` (memory `feedback_atomic_write_canonical_for_secrets`).

**Test scenarios:**
- Happy path: saving a TOKEN (devto), TOKEN+FIELDS (ghost token+site_url), PASTE-BLOB (csdn cookies JSON), USERPASS (livejournal) each persists correctly to a sandboxed config dir.
- Integration: livejournal save → `livejournal-credentials.json` contains `{username, hpassword}` (no plaintext), `0o600`; cnblogs save → plaintext password (divergence preserved via module dispatch).
- Edge: empty token field → no change ("leave as-is"); whitespace-only field → stripped, treated as leave-as-is, never persisted as `"  "`.
- Edge: clear flag on TOKEN+FIELDS → credential file removed **and** its config.toml keys removed **and** all other sections survive (clear-path round-trip).
- Error path: `channel` = `../etc` or unregistered → rejected on **both** save and clear/unlink, no filesystem write.
- Error path (SSRF): ghost `site_url` = `http://169.254.169.254/` / `http://127.0.0.1/` / non-https → rejected, not saved.
- Error path: malformed cookies JSON → user-facing error, no write; oversized blob (> cap) → rejected before write; cookie `domain` mismatching channel host → rejected; non-conforming shape (`{"cookies":"x"}`, unknown top-level keys) → rejected.
- CSRF tripwire: POST to the new save route AND the clear route **without** a CSRF token → 403 (proves `_global_csrf_guard` intercepts the new blueprint); **with** seeded `session['csrf_token']` → succeeds. Mirror `test_webui_url_verify_routes`, NOT `medium_login`'s `csrf_client`.
- Round-trip: save TOKEN+FIELDS config key → reload → every prior config.toml section **and a pre-existing unrelated key's byte-identical value** survive (narrow-merge proof, catches re-serialize/type-coercion).
- Concurrency (lost-update): two `threading.Barrier`-synced saves to different keys (`ghost.site_url`, `wordpresscom.site`) → both survive (flock proof; distinct from the per-channel rotation barrier).
- Partial-failure: mock the second write (config narrow-merge) to raise after the credential file is written → channel reports `bound=False` (no false bind); re-save recovers idempotently.
- Security: assert no secret value appears in logs/flash/exception text, including value-bearing validation errors and the masked bound-summary path.

**Verification:** All four auth types save through one route to sandboxed config; zero-regression on ghpages/devto/notion; no secret leakage; URL/channel inputs validated.

- [ ] **Unit 5: ANON ready-state + non-destructive connectivity probe**

**Goal:** txtfyi/rentry/telegraph render "免绑定·就绪" and offer a connectivity check that never publishes.

**Requirements:** R6, R9

**Dependencies:** Unit 1, Unit 3

**Files:**
- Modify: `webui_app/templates/_settings_binding_anon.html`
- Modify: `webui_app/routes/settings_basic.py` (verify path) — confirm no real publish; Test: `tests/test_settings_basic_api.py`

**Approach:**
- "测试连通" reuses `/api/<channel>/verify` (offline-correct after R5) or a side-effect-free GET of the form page; explicitly NOT a publish. Confirm `/api/<channel>/dry-run` is NOT introduced.
- Guard txtfyi form-POST so any future probe cannot reach `edit.php` (txtfyi `publish()` submits before the draft check).

**Test scenarios:**
- Happy path: ANON verify returns ready without creating a paste (assert no outbound publish call / `dry_run_intercept` fires).
- Edge: telegraph (auto-bootstrap) shows ready.
- Error path: probe failure surfaces inline without claiming success.

**Verification:** ANON probe is provably non-destructive; ready badge shown.

- [ ] **Unit 6: Interaction states (loading / error / bound summary / clear-confirm)**

**Goal:** Define every async action's in-progress, error, success-summary, and destructive-confirm behavior.

**Requirements:** R10, R11, R12, R13

**Dependencies:** Unit 3, Unit 4

**Files:**
- Modify: `webui_app/static/js/channel-binding.js` (disable+spinner on trigger, debounce already exists, content-type guard already exists; extend `renderResult` for the new partials)
- Modify: the auth-type partials (bound summary fields per type; clear-credentials confirm)
- Test: `tests/test_settings_dashboard_rendering.py` (server-rendered states) + JS behavior asserted via existing patterns

**Approach:**
- Each async action: disable button + in-progress label, block concurrent re-trigger until result (R10) — prevents duplicate `store_credentials` / duplicate probe.
- Inline error in the card (`data-field="result"`), user-facing text not raw stderr (R11; memory `feedback_webui_stderr_preview_truncated`).
- Bound summary per auth type (enumerated): USERPASS→`username`; TOKEN→fixed-prefix mask revealing **no suffix** of short secrets; TOKEN+FIELDS→primary config field (e.g. "Ghost (example.com)"); PASTE-BLOB→site + cookie count; ANON→"就绪"; all + `last_verified_at`; partial refresh (R12).
- **Multi-field dirty state**: for TOKEN+FIELDS/cookie forms, already-set fields show "currently set — leave blank to keep" placeholder so the leave-as-is contract is visible, not a surprise. Decide tumblr's 5-OAuth1-string shape (5 labeled inputs vs one paste-blob).
- Clear-credentials → confirm dialog that **names what is destroyed per auth type** (TOKEN+FIELDS: enumerate the config fields removed alongside the token, e.g. ghost site_url; USERPASS/PASTE-BLOB: credential removed, re-bind required); on success card returns to unbound (R13). Dialog is UX, not a security control.
- **ANON two-state**: the static "免绑定·就绪" badge (no creds needed) vs the transient probe result — a failed probe shows "就绪,但站点暂时不可达 · 上次探测失败" without retracting ready-by-design status; probe reuses the Unit 6 disable+spinner pattern.

**Test scenarios:**
- Happy path: save triggers disabled+spinner, success refreshes bound badge without full reload.
- Edge: rapid double-click debounced (no duplicate save).
- Error path: backend 4xx → inline user-facing error, inputs retained, retry available; HTML (non-JSON) response handled by content-type guard.
- Edge: bound summary shows correct identity field per auth type.
- Edge: clear-credentials requires confirm; cancel leaves credentials intact.

**Verification:** No duplicate-submit; errors readable inline; bound summary + clear-confirm behave per type.

### Phase 3 — Hardening & guards

- [ ] **Unit 7: Coverage / drift test + scope guard**

**Goal:** Lock the registry-driven contract so future channels can't silently fall through.

**Requirements:** R1, R2

**Dependencies:** Units 1-4

**Files:**
- Test: `tests/test_r9_extension_readiness.py` (extend) and/or `tests/test_binding_surface_coverage.py` (new)

**Approach:**
- Test-time (not module-level) assertions: every `active_platforms()` channel has a known `auth_type` and either an in-scope template or an allowlisted bespoke flow (blogger/velog/medium/mastodon); none yields "No adapter configured".
- Assert the dashboard drift test accounts for `len(HIDDEN_FROM_UI)`.

**Test scenarios:**
- Drift: a new registry channel with no `auth_type` fails the coverage test loudly.
- Drift: no active channel maps to a missing template.
- Regression: bespoke-flow channels (blogger/velog/medium/mastodon) are explicitly allowlisted, not flagged.

**Verification:** Coverage test green; adding an unclassified channel fails fast.

## System-Wide Impact

- **Interaction graph:** `active_platforms()` → `contexts.py` dashboard injection → `_channel_card_macro` + auth-type partials → `channel-binding.js`; new save route → adapter savers → config/credential files. `inject_platforms()` feeds two consumers (publish select = bound-only, history chips = all) — do not in-place filter (memory `feedback_platforms_vs_bound_platforms_split`).
- **Error propagation:** offline-check `DependencyError` → blocker list (specific text). Save-route errors → user-facing flash/inline, never raw secret-bearing exceptions.
- **State lifecycle risks:** credential writes via `atomic_write` 0600; narrow-merge preserves config.toml sections; clear path unlinks file. Concurrent bootstrap/rotation/rebind → single mutation path per channel (learning #5).
- **API surface parity:** `/api/<channel>/verify` and `/api/<channel>/status` already generic; the new save route is the only added surface. `bound` semantics change affects every consumer of `get_channel_status` — verify publish-select and history-chip consumers unaffected.
- **Integration coverage:** registry→template auto-render; save→file→reload round-trip; ANON probe non-destructiveness; module-dispatch credential-shape divergence.
- **Unchanged invariants:** publish pipeline and adapter `publish()` logic unchanged; blogger OAuth / velog / medium flows unchanged (surfaced only); dofollow values from registry unchanged.

## Risks & Dependencies

| Risk | Mitigation |
|---|---|
| Refactoring offline check regresses the 7 known channels | Characterization tests on existing 7 before refactor; parametrized bound test (Unit 1) |
| `bound` semantic change ripples to other `get_channel_status` consumers | Enumerate consumers; regression test publish-select vs history-chip paths (memory split lesson) |
| `store_credentials` symbol collision (livejournal/cnblogs) | Dispatch by module, never bare-symbol import; per-shape assertion test (Unit 4) |
| Generalizing `_ALLOWED` breaks ghpages/devto/notion saves | Characterization coverage first (Unit 4 execution note) |
| TOKEN+FIELDS config write drops other config.toml sections | Narrow-merge helper + round-trip survival test (learning #6) |
| SSRF via ghost/wordpresscom URL fields (incl. DNS-rebinding) | Validate at save AND re-validate resolved IP at fetch time; https-enforce; placeholder/private-host reject (Unit 4) |
| Module-level drift assert fires on half-loaded registry | Test-time only (learning #1) |
| Scope creep into mastodon/live-browser | Allowlisted bespoke flows; mastodon explicitly deferred (Scope Boundaries) |
| TOKEN+FIELDS split-write (token file + config key) crash → half-bound channel | Validate-all-before-write; write bound-authoritative artifact last so crash reads as unbound; partial-failure test |
| Concurrent config.toml narrow-merge lost-update (two routes / racing CLI) | flock the full read-modify-write window; re-read under lock; cross-key concurrency test |
| Clear leaves orphaned config keys (file deleted, `[ghost] site_url` remains) | Clear is coherent teardown: unlink file + narrow-merge-delete config keys under one flock |
| Hostile/oversized PASTE-BLOB cookie blob persisted and replayed | Size cap + schema-validate (allowlisted keys) + cookie-domain-matches-channel-host before write |
| `.config-history` unbounded growth + secret sprawl (snapshots carry tokens; may be 0644) | Bounded retention, μs-collision-safe names, 0600, gitignored (Operational Notes) |
| New save blueprint not actually covered by `_global_csrf_guard` (medium_login trap) | CSRF tripwire test: tokenless POST → 403 on save AND clear routes |
| Bound-summary mask / validation errors leak partial secret | Mask reveals fixed prefix only, never suffix; errors name field not value; secret-leak assertion |
| **Base `available()` returns True → uncredentialed velog/medium/mastodon/livejournal false-positive bound** (and admitted to publish-select) | Auth-type dispatch + artifact probe; preserve bespoke branches; never trust base-available; false-positive + publish-select gate tests (Unit 1) |
| **`auth_type` added to manifest dict crashes `register()` at import** (`**`-splat, no `**kwargs`) | Make it a real `register()`/`_manifest_types` param derived from existing `BindBackend`; import-smoke test (Unit 2) |
| **New save/clear route reachable unauthenticated off-loopback** | `_refuse_when_allow_network()` + `_check_bind_origin_or_abort()` on save AND clear; off-loopback rejection test (Unit 4) |
| **mastodon renders a dead card / Jinja error** (active, no partial, no working bind) | Deferred-stub partial with no actionable anchor (or non-active visibility); no-dead-anchor test (Unit 3) |
| **Retiring `#section-channels` breaks `channel-binding.js` bind** (DOM-id/`.bind-channel-btn`/`runVelogLogin` contract) | Preserve `#channel-<name>` + `.bind-channel-btn` for blogger/velog/medium; JS resolves-panel regression test (Unit 3) |
| Two-source drift: new `auth_type` vs existing `BindBackend` | Derive auth_type from backend+extras (one source); consistency drift test (Unit 2) |

## Documentation / Operational Notes

- Update AGENTS.md "Adding a new publisher adapter" recipe to note the new `auth_type` manifest field drives the binding UI automatically (no settings.html edit needed) — this retires the memory `feedback_wire_token_paste_channel_five_sites` 5-site fragility.
- No migration; existing credential files unchanged in shape. hashnode/writeas retirement (visibility) is a separate concern.
- **`.config-history` snapshots** (written by narrow-merge before each config.toml write) carry secrets: enforce bounded retention (ring buffer of N or age-prune), μs-collision-safe filenames (telegraph orphan-archive precedent), `0600` perms matching source, and confirm the dir is gitignored.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-26-overview-inline-channel-binding-requirements.md](docs/brainstorms/2026-05-26-overview-inline-channel-binding-requirements.md)
- Refactor target: `src/backlink_publisher/publishing/adapters/__init__.py` (offline chain L391-476)
- Binding status: `webui_app/binding_status.py`; token-paste precedent: `webui_app/routes/token_paste.py` + `_settings_channel_token_paste.html`
- Credential collision: `livejournal_api.py:92` / `cnblogs_api.py:27`
- Learnings: `docs/solutions/logic-errors/invert-drift-check-when-invariant-becomes-dynamic-2026-05-18.md`, `save-config-write-paths-bypass-preservation-2026-05-15.md`, `best-practices/never-smoke-test-real-save-endpoints-2026-05-19.md`, `credential-rotation-tests-cover-bootstrap-race-2026-05-19.md`; memory `reference_webui_csrf_architecture`, `feedback_wire_token_paste_channel_five_sites`, `feedback_atomic_write_canonical_for_secrets`, `feedback_platforms_vs_bound_platforms_split`
