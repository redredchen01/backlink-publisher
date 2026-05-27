---
title: "refactor: Remove jianshu channel + sweep ghost/cnblogs/zhihu dead refs"
type: refactor
status: active
date: 2026-05-27
origin: docs/brainstorms/2026-05-27-remove-jianshu-and-sweep-retired-channels-requirements.md
claims: {}  # opt-out: pure-removal refactor, no origin/main-reachable SHAs pre-merge
---

# refactor: Remove jianshu Channel + Sweep ghost/cnblogs/zhihu Dead References

## Overview

Hard-remove the `jianshu` publishing channel from the registry-driven pipeline (the reverse of
the documented "1 dict + 1 register line" add recipe), preserve its nofollow discovery as a
`_REJECTED_PLATFORMS` negative-knowledge entry, and sweep the dead references to the
already-removed `ghost` / `cnblogs` / `zhihu` channels that linger in the post-#253 WebUI
credential-save route and binding templates. After this change, `grep -i` for the four slugs
returns nothing in `src/`, `webui_app/`, and active tests (excluding historical docs and known
false positives).

This mirrors the completed sibling plan `2026-05-26-007-refactor-remove-four-channels`
(shipped as PR #253, which removed beehiiv/cnblogs/habr/ghost/zhihu/segmentfault/pikabu). The
delta now is jianshu (the only still-live channel of the requested four) plus the stragglers
that two later PRs (#257 credential-save route; the binding templates) reintroduced.

## Problem Frame

Operator asked to "徹底移除" four channels: jianshu, ghost, cnblogs, zhihu. Verification (see
origin) shows ghost/cnblogs/zhihu adapters were already hard-removed by #253, and #259 already
deleted the stale cnblogs test — but `jianshu` is still fully live, and dead references to the
other three survive in `channel_bind_save.py` and the binding templates. The operator's local
`main` is several commits behind with uncommitted spike WIP, so all four still appear locally.
(See origin: `docs/brainstorms/2026-05-27-remove-jianshu-and-sweep-retired-channels-requirements.md`.)

## Requirements Trace

- R1. `registered_platforms()` no longer contains `jianshu`.
- R2. Adapter module `jianshu_api.py` deleted; no dangling import.
- R3. `register("jianshu", …)` block, `JianshuAPIAdapter` import, and `JIANSHU_MANIFEST` import
  removed from `adapters/__init__.py`.
- R4. `JIANSHU_MANIFEST` definition removed from `_manifests.py`.
- R5. `"jianshu"` rationale removed from `_nofollow_rationales.py`; sibling csdn/juejin prose
  copy-edited so no slug reference and no dangling `"as csdn/"` fragment survives.
- R5b. `_REJECTED_PLATFORMS["jianshu"]` added (≥80-char rationale) preserving the nofollow
  discovery and arming the re-add tripwire.
- R6. `"jianshu": "paste_blob"` removed from `_AUTH_TYPE_BY_PLATFORM`.
- R7. jianshu removed from the bind-save map and the paste_blob template docstring.
- R8. jianshu removed from the three hardcoded sample tests.
- R9. Dead `zhihu` / `cnblogs` references removed from `channel_bind_save.py`.
- R10. Dead UI template blocks removed (`ghost` in token_fields, `cnblogs` in userpass).
- R10d. One-shot, self-disabling unlink of orphaned `{jianshu,zhihu,cnblogs}-credentials.json`
  files (ghost has no credentials file). Failures logged, not silently swallowed.
- R11. Full pytest passes after rebasing onto latest `origin/main`.
- R12. `grep -i` for the four slugs returns nothing in `src/`, `webui_app/`, active tests
  (excluding historical `docs/` + known false positives).

## Scope Boundaries

- **Non-goal:** editing `cli/*.py`, `schema.py`, `content_negotiation.py` — registry-driven,
  verified zero hardcoded references to the four slugs.
- **Non-goal:** rewriting historical `docs/plans/*` and `docs/requirements/*`.
- **Non-goal:** the `HIDDEN_FROM_UI` soft-retire path — this is a hard removal.
- **Non-goal:** false-positive `ghost` substrings (`ghost = tmp_path/…`, `ghost.png`,
  `HostFilter`, "ghost option removal" comment in `test_webui_platforms_context.py`).
- **Optional adjacent cleanup (not required):** `_PASTE_BLOB_CHANNELS` in `channel_bind_save.py`
  also carries dead rows for `habr` / `pikabu` / `segmentfault` (same #253 removal). Sweeping
  them while editing the file is a cheap win but is outside the four-slug grep gate — implementer
  may include or skip.

## Context & Research

### Relevant Code and Patterns

Touch points confirmed against `origin/main` (path roots: adapter/registry/manifest under
`src/backlink_publisher/`; `webui_app/` and `tests/` are repo-root-relative):

| Artifact | Location |
|---|---|
| `register("jianshu", …)` block | `src/…/publishing/adapters/__init__.py` (~L171–176) |
| `JianshuAPIAdapter` + `JIANSHU_MANIFEST` imports | `src/…/publishing/adapters/__init__.py` (~L76, ~L36) |
| Adapter module to delete | `src/…/publishing/adapters/jianshu_api.py` |
| `JIANSHU_MANIFEST` def | `src/…/publishing/_manifests.py` (~L411) |
| `"jianshu"` rationale + csdn/juejin cross-refs | `src/…/publishing/adapters/_nofollow_rationales.py` (~L104, L106–113, L121) |
| `_AUTH_TYPE_BY_PLATFORM` jianshu row | `src/…/publishing/registry.py` (~L204) |
| `_REJECTED_PLATFORMS` (empty `dict[str,str]`, direct-literal mutation, no helper) | `src/…/publishing/registry.py` (~L135) |
| bind-save map (`_PASTE_BLOB_CHANNELS`) | `webui_app/routes/channel_bind_save.py` (jianshu, zhihu rows) |
| bind-save dispatch (`_USERPASS_MODULES`) | `webui_app/routes/channel_bind_save.py` (cnblogs row → deleted module) + docstring |
| paste_blob template docstring | `webui_app/templates/_settings_binding_paste_blob.html` (L4) |
| dead ghost token-field block (+ adjacent beehiiv) | `webui_app/templates/_settings_binding_token_fields.html` (ghost L36–48, beehiiv L52–65, docstring L4) |
| dead cnblogs userpass refs | `webui_app/templates/_settings_binding_userpass.html` (L4, L9) |
| sample test rows | `tests/test_auth_type_classification.py` (~L73), `tests/test_offline_bound_registry_dispatch.py` (~L87), `tests/test_phase1_cookie_adapters.py` (~L27) |
| `_REJECTED_PLATFORMS` test | `tests/test_registry_rejected_platforms.py` (`assert len == 0` → `== 1`; rationale-floor loop unchanged) |
| existing best-effort unlink pattern | `webui_app/routes/bind.py` `_execute_replace` (~L178–197) |
| idiomatic startup-hook block | `webui_app/__init__.py` `create_app()` (~L196–222, gated by `start_scheduler`) + `webui_store/channel_status.py::reconcile_on_load` (~L187–215) |

- The "add a channel" recipe (`AGENTS.md` → "Adding a new publisher adapter") is the mirror of
  this removal — reverse it for jianshu.
- **Confirmed NOT sweep sites for our four slugs:** `webui_app/binding_status.py`
  (`_DOFOLLOW_BY_CHANNEL` / `HIDDEN_FROM_UI`) and `tests/test_r9_extension_readiness.py` —
  grep returned zero hits for jianshu/ghost/cnblogs/zhihu on `origin/main`.

### Institutional Learnings

- `docs/solutions/best-practices/wip-branch-ff-merge-instead-of-reset-2026-05-26.md` — removing a
  channel **deletes adapter files**, producing the exact divergence shape where `git stash pop`
  conflicts and `reset --hard` is hook-blocked. Preserve the operator's spike WIP via
  `git switch -c wip/… && add -A && commit`, then branch off latest `origin/main`.
- `docs/solutions/logic-errors/invert-drift-check-when-invariant-becomes-dynamic-2026-05-18.md` —
  keep registry-set drift assertions test-time, not module-level; don't add a new module-level
  assertion enforcing the post-removal set (import-order fragility).
- `docs/solutions/workflow-issues/grep-dofollow-map-before-shipping-adapter-2026-05-20.md` —
  precedent for retaining a channel's verdict as negative knowledge rather than silently purging
  it; conceptual basis for R5b's `_REJECTED_PLATFORMS` entry.
- Auto-memory `[[feedback_channel_removal_must_prune_auth_type_map]]` — removing a channel
  requires pruning `_AUTH_TYPE_BY_PLATFORM` **and** the hardcoded sample tests; CI tests
  "merged into latest main", so a stale local base passes locally then fails CI → rebase first.

## Key Technical Decisions

- **Hard-delete the adapter, but park the rationale in `_REJECTED_PLATFORMS` (R5b):** preserve the
  equity-stripping discovery and arm the re-add tripwire. Departs deliberately from #253 (which
  left `_REJECTED_PLATFORMS` empty). Rationale: a real SEO finding worth keeping
  (`[[feedback_grep_dofollow_map_before_shipping_adapter]]`).
- **One-shot, self-disabling credential cleanup (R10d):** extract a standalone
  `purge_removed_channel_credentials()` (testable with a sandboxed config dir), guarded by a
  sentinel/version-stamp so it runs once and then no-ops on every future boot, wired as a 4th
  fail-soft `try/except` in the `create_app()` startup block. This deliberately departs from a
  permanent `reconcile_on_load`-style routine — those re-derive live state every boot, whereas
  this is a one-time migration. The sentinel also removes the re-introduction hazard: a future
  re-registration of a swept slug cannot have its new credentials silently deleted. No migration
  runner or maintenance CLI verb exists, so the startup hook is the attach point. Target
  `<slug>-credentials.json` (paste_blob/userpass channels, **not** the storage-state files
  `_execute_replace` handles); validate containment + reject symlinks; log unlink failures.
- **Do not pin a base SHA:** `origin/main` HEAD drifted three times during planning
  (`97401e4`→`2ab669c`→`77ff53b`) — there is active concurrent merging. Branch off **latest**
  `origin/main` re-fetched at execution time; rebase before trusting any green run.
- **Registry auth-type map is the security chokepoint:** `save_channel_credential` gates every
  write on `registry.auth_type(channel)` being non-`None` before consulting dispatch dicts. R6
  is the load-bearing step that makes the bind-save row unreachable.

## Open Questions

### Resolved During Planning

- *Where does R10d's unlink live?* → `purge_removed_channel_credentials()` in
  `webui_store/channel_status.py`, wired into `create_app()` startup block (no migration/CLI
  infra exists). Resolved via repo research.
- *What does adding to `_REJECTED_PLATFORMS` break?* → only `test_registry_rejected_platforms.py`'s
  `assert len == 0` (→ `== 1`) + its docstring; the rationale-floor loop already validates any
  entry. No alphabetization/field requirements. Resolved via repo research.
- *Are `binding_status.py` / `test_r9_extension_readiness.py` sweep sites?* → No; zero hits for
  the four slugs. Resolved via grep.

### Deferred to Implementation

- The R5 csdn/juejin prose copy-edit needs a human read for grammaticality after the slug is
  dropped (no automated tripwire covers prose quality for registered rationales).
- Exact current line numbers may shift on the rebased base — locate by symbol/pattern, not by the
  approximate line numbers above.

## Implementation Units

- [ ] **Unit 1: Hard-remove jianshu from the registry + adapter (atomic core)**

**Goal:** Remove jianshu from the publishing registry and delete its adapter, while parking its
rationale as negative knowledge — landing all coupled edits together so the package still imports.

**Requirements:** R1, R2, R3, R4, R5, R5b, R6, R8

**Dependencies:** Branch off latest `origin/main` (see Risks); operator WIP preserved first.

**Files:**
- Delete: `src/backlink_publisher/publishing/adapters/jianshu_api.py`
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py` (drop `JianshuAPIAdapter`
  import, `JIANSHU_MANIFEST` import, `register("jianshu", …)` block)
- Modify: `src/backlink_publisher/publishing/_manifests.py` (drop `JIANSHU_MANIFEST`)
- Modify: `src/backlink_publisher/publishing/adapters/_nofollow_rationales.py` (drop `"jianshu"`
  entry; copy-edit csdn/juejin prose)
- Modify: `src/backlink_publisher/publishing/registry.py` (drop jianshu from
  `_AUTH_TYPE_BY_PLATFORM`; add `_REJECTED_PLATFORMS["jianshu"]` with the lifted ≥80-char rationale)
- Test: `tests/test_auth_type_classification.py` (delete the `("jianshu","paste_blob")` row)
- Test: `tests/test_offline_bound_registry_dispatch.py` (remove `"jianshu"` from the param list)
- Test: `tests/test_phase1_cookie_adapters.py` (delete the `("jianshu","JianshuAPIAdapter")` row)
- Test: `tests/test_registry_rejected_platforms.py` (`assert len == 0` → `== 1`; update docstring)

**Approach:**
- **Atomicity is mandatory.** Adding `_REJECTED_PLATFORMS["jianshu"]` makes `register("jianshu")`
  raise `RegistryError` at import — so the rejected-add and the register-delete must be in the
  same change. Likewise, deleting `jianshu_api.py` without removing the
  `test_phase1_cookie_adapters` row → `ModuleNotFoundError` (it dynamically imports the module),
  and removing jianshu from `register()` without removing it from `_AUTH_TYPE_BY_PLATFORM` →
  `test_classification_covers_all_27_no_extras` fails. Note: that test asserts
  `set(_AUTH_TYPE_BY_PLATFORM) - set(active_platforms()) == set()` (a stale-extra-entry
  set-difference) — the `27` in its name is **decorative**, there is no hardcoded count to update.
  Its sibling `test_every_active_platform_has_known_auth_type` stays green under both-sided
  removal (no edit needed).
- Lift the jianshu rationale verbatim from `_nofollow_rationales.py` into the
  `_REJECTED_PLATFORMS` value (it already exceeds 80 stripped chars).
- `_REJECTED_PLATFORMS` is a **static literal**, never mutated by `register()`, so it is exempt
  from the conftest registry snapshot-isolation fixtures that bind `_REGISTRY` /
  `_DOFOLLOW_BY_PLATFORM` (same exemption the code comments grant `_AUTH_TYPE_BY_PLATFORM`) — a
  single new literal entry needs no fixture changes.
- Keep the registry-set drift assertion test-time; do not add a module-level set assertion.

**Patterns to follow:** the `register(...)` signature and `_REJECTED_PLATFORMS` literal in
`registry.py`; #253's removal diff as the mirror template.

**Test scenarios:**
- Happy path: `registered_platforms()` does not contain `jianshu`; `active_platforms()` likewise.
- Happy path: `auth_type("jianshu")` returns `None`.
- Edge case: calling `register("jianshu", …)` now raises `RegistryError` (rejected-platform guard).
- Edge case: `_REJECTED_PLATFORMS["jianshu"]` exists and its `.strip()` length ≥ 80 (exercised by
  the existing rationale-floor loop).
- Integration: `import backlink_publisher.publishing.adapters` succeeds (no `ModuleNotFoundError`,
  no `RegistryError` at import).
- Regression: the three edited sample tests pass; `test_classification_covers_all_27_no_extras`
  reports no stale auth-type entries.

**Verification:** Full registry/adapter test files green; package imports cleanly; jianshu absent
from `registered_platforms()` and present in `_REJECTED_PLATFORMS`.

---

- [ ] **Unit 2: Sweep WebUI bind-save route + binding templates**

**Goal:** Remove jianshu's bind-save wiring and the dead ghost/cnblogs/zhihu references in the
credential-save route and the three binding templates.

**Requirements:** R7, R9, R10

**Dependencies:** None (independent of Unit 1; ships in the same PR).

**Files:**
- Modify: `webui_app/routes/channel_bind_save.py` — remove `jianshu` and `zhihu` rows from
  `_PASTE_BLOB_CHANNELS`; remove the `cnblogs` row from `_USERPASS_MODULES` (targets the deleted
  `cnblogs_api`); drop the `cnblogs` docstring mention. *(Optional: also drop the dead
  `habr`/`pikabu`/`segmentfault` rows — see Scope Boundaries.)*
- Modify: `webui_app/templates/_settings_binding_paste_blob.html` — drop `jianshu` from docstring.
- Modify: `webui_app/templates/_settings_binding_token_fields.html` — delete the dead `ghost`
  field-spec block (required: ghost is in the four-slug gate) and docstring mention. The adjacent
  dead `beehiiv` block is swept opportunistically because it sits right beside the ghost block in
  the same file (the "while editing the file" cheap win) — it is not in the four-slug grep gate.
- Modify: `webui_app/templates/_settings_binding_userpass.html` — drop `cnblogs` docstring
  references; confirm the `{% if channel == 'livejournal' %}` branch still reads correctly with
  livejournal as the sole userpass channel.
- Test: `tests/test_channel_bind_save.py` (existing; must stay green)

**Approach:**
- These maps are **not** registry-driven, so a green suite will not catch leftover entries —
  rely on the R12 grep gate plus an explicit assertion.
- After Unit 1 + R6, posting a removed slug to the save route hits the registry `None`-check and
  returns the "unknown channel" path; no credential write occurs.

**Patterns to follow:** existing `_PASTE_BLOB_CHANNELS` / `_USERPASS_MODULES` dict shape; the
other channels' template blocks.

**Test scenarios:**
- Happy path: `_PASTE_BLOB_CHANNELS` and `_USERPASS_MODULES` contain none of the four slugs
  (assert directly).
- Error path: POST to the save route with `channel="jianshu"` (or zhihu/cnblogs) returns the
  unknown-channel rejection (registry `None`-gate), not a 500 and not a credential write.
- Integration: settings page renders without the ghost/beehiiv token-field blocks and without the
  cnblogs userpass references (no template/Jinja error).
- Regression: existing `tests/test_channel_bind_save.py` passes.

**Verification:** Grep of `channel_bind_save.py` and the three templates returns no jianshu/
ghost/cnblogs/zhihu; settings binding UI renders cleanly.

---

- [ ] **Unit 3: One-shot, self-disabling purge of orphaned credential files (R10d)**

**Goal:** Delete orphaned `{jianshu,zhihu,cnblogs}-credentials.json` 0600 secret files once,
since the UI "clear" path is unreachable for removed slugs — without leaving a permanent
every-boot routine or a re-introduction hazard.

**Requirements:** R10d

**Dependencies:** None as a code path, but **logically pairs with Units 1–2**: it only makes sense
once those make the slugs' UI clear-path unreachable. Ship in the same PR; do not land Unit 3
alone.

**Files:**
- Modify: `webui_store/channel_status.py` — add module-level `purge_removed_channel_credentials()`:
  resolve `_config_dir()` (honoring `BACKLINK_PUBLISHER_CONFIG_DIR`); if a **sentinel/version-stamp**
  file (e.g. `<config_dir>/.removed-channel-purge-v1.done`) already exists, return immediately;
  otherwise iterate the closed literal set `{jianshu,zhihu,cnblogs}` × `<slug>-credentials.json`,
  validating each path is contained in `_config_dir()` and is **not a symlink** before unlinking,
  log a per-file **WARNING** (not silent `pass`) on any `OSError`, then write the sentinel so the
  routine self-disables on all future boots.
- Modify: `webui_app/__init__.py` — add a 4th fail-soft `try/except` in the `create_app()` startup
  block (~L196–222) calling the new function, wrapped in `_log.warning` on failure.
- Test: `tests/test_channel_status.py` (or a new `tests/test_purge_removed_credentials.py` if no
  fitting module exists) — unit-test the standalone function directly.

**Approach:**
- **One-shot via sentinel** addresses two review findings at once: it stops the permanent
  every-boot dead weight, and it eliminates the re-introduction hazard — once the stamp is
  written, a future re-registration of a swept slug (the `_REJECTED_PLATFORMS` map shows channels
  do get un-rejected and re-added) can never have its freshly-saved credentials silently deleted.
- **Symlink/traversal guard:** reuse or factor `channel_status._validate_storage_state_path`
  (`.resolve()` + `relative_to(_config_dir().resolve())`, reject symlinks) so an
  attacker/operator-planted symlink at `<slug>-credentials.json` cannot redirect the unlink
  outside config_dir. The slug set is a closed literal (no glob), which bounds the blast radius.
- **Log on failure, don't silently swallow:** a 0600 secret that fails to unlink (EACCES, EBUSY,
  read-only mount) must surface a WARNING with the path, so the residual secret is discoverable
  rather than falsely assumed cleaned.
- The startup block is gated by `start_scheduler` (false under pytest), so the wiring itself is
  not exercised in the app-factory test path — that is why the logic lives in a standalone,
  directly-unit-tested function.
- `ghost` is intentionally **not** in the purge set: it is token-field-only with no bind-save row,
  hence no `ghost-credentials.json`.

**Execution note:** Implement the purge function test-first against a sandboxed
`BACKLINK_PUBLISHER_CONFIG_DIR`.

**Patterns to follow:** `bind.py._execute_replace` (best-effort unlink loop);
`channel_status._validate_storage_state_path` (containment/symlink guard);
`channel_status.reconcile_on_load` (startup-walk shape); the existing fail-soft `try/except`
wrappers in `create_app()`.

**Test scenarios:** *(standalone unit tests of `purge_removed_channel_credentials()` — the
`create_app` wiring is not exercised under pytest and is verified by reasoning.)*
- Happy path: with `{jianshu,zhihu,cnblogs}-credentials.json` present and no sentinel, the
  function deletes exactly those three and writes the sentinel.
- Edge case: sentinel already present → no files touched, returns immediately (one-shot).
- Edge case: none of the files exist → no error, sentinel still written.
- Edge case: only some exist → deletes present ones, ignores absent.
- Edge case: an unrelated channel's `*-credentials.json` (e.g. `csdn-credentials.json`) is left
  untouched.
- Edge case: a swept slug later re-added as a live channel with a fresh credentials file — after
  the sentinel exists, that file is NOT deleted (re-introduction safety).
- Security: a `<slug>-credentials.json` that is a symlink pointing outside config_dir is refused
  (not followed/unlinked).
- Error path: an `OSError` on unlink emits a per-file WARNING with the path; the function still
  returns and writes the sentinel.

**Verification:** Unit test green; orphaned files gone after first run, sentinel written, no-op on
subsequent runs; unlink failures are logged, not silent.

---

- [ ] **Unit 4: Rebase, full-suite, and grep-gate verification**

**Goal:** Prove the removal is complete and green on the volatile base.

**Requirements:** R11, R12

**Dependencies:** Units 1–3.

**Files:** none (verification only).

**Approach:**
- **Idempotency / coordinate-or-abort first:** before applying, diff against latest `origin/main`
  for `jianshu` / `_REJECTED_PLATFORMS`. A sibling brainstorm touches the same channels — if a
  concurrent PR already removed jianshu's `register()` block or bind-save rows, this branch's job
  shrinks; do **not** blindly re-apply (avoid double-removal conflicts or a duplicate
  `_REJECTED_PLATFORMS` entry). If a sibling PR touching these slugs is open, pick one owner.
- Rebase the branch onto **latest** re-fetched `origin/main` (it drifts — see Risks), resolving
  conflicts from concurrently-landed channel work.
- **Re-run the gate on every rebase, voiding prior green:** the full pytest suite + the package
  import check + the R12 grep gate must pass *on the rebased base*, not just on the original base
  (CI tests "merged into latest main", so stale-base green is meaningless).
- **Explicit security-invariant assertion** (the registry `None`-gate is what makes the
  credential route fail-closed): assert `registry.auth_type("jianshu") is None` and `"jianshu" not
  in registered_platforms()`, and promote Unit 2's "POST channel=jianshu → unknown-channel
  rejection" scenario to a hard gate that must pass on the rebased base — so a conflict resolution
  that drops the bind-save row but re-keeps the auth-type entry cannot silently re-open the write
  path.

**Test scenarios:** *Test expectation: none — this is a verification/integration unit, not a
behavioral change.* Its checks are R11 (full suite green on the rebased base), R12 (grep gate
clean), and the security-invariant assertions above.

**Verification:** Full suite passes on the rebased base; `grep -i` for the four slugs returns
nothing outside historical docs and known false positives; `registered_platforms()` count is one
less than before; `auth_type("jianshu") is None` and the save route rejects a jianshu POST.

## System-Wide Impact

- **Interaction graph:** the publish CLI argparse choices, `schema.validate_publish_payload`, the
  dofollow/content-tier matrices, and the WebUI publish platform list all read
  `registered_platforms()` dynamically and auto-shrink — no edits required there.
- **Error propagation:** posting a removed slug to the credential-save route now resolves to the
  registry `None`-gate (unknown-channel rejection) instead of dispatching — fail-closed.
- **State lifecycle risks:** orphaned `*-credentials.json` files for removed slugs lose their UI
  clear path; Unit 3's best-effort purge addresses this. Files are 0600 secrets — purge must be
  fail-soft and must not touch other channels' files.
- **API surface parity:** no public API/CLI flag changes; the only contract change is the platform
  set shrinking by one (jianshu) — already a dynamic, advertised set.
- **Unchanged invariants:** `_REJECTED_PLATFORMS` gains exactly one entry (jianshu); all other
  registered platforms, their auth-type classifications, and the bind-save behavior for live
  channels are unchanged.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `origin/main` drifts mid-work (active concurrent merges) | Do not pin a SHA; branch off latest re-fetched `origin/main`; rebase before trusting green (CI merges into latest main). |
| Operator's uncommitted spike WIP clobbered by branch switch | Preserve via `git switch -c wip/… && add -A && commit` first; removal deletes adapter files so `stash pop` conflicts and `reset --hard` is hook-blocked (see learnings). |
| Partial/atomicity failure in Unit 1 → import crash | Land all coupled edits (register-delete + rejected-add; adapter-delete + sample-test edits + auth-type-map delete) in one commit; verify package import. |
| Leftover bind-save/template entries pass a green suite | Maps are not registry-driven; enforce via explicit assertion + the R12 grep gate. |
| R10d startup wiring untestable under pytest (`start_scheduler` false) | Extract logic to a standalone function and unit-test it directly with a sandboxed config dir. |
| Concurrent parallel work on the same channels (a sibling brainstorm exists) | Audit worktrees/branches before starting; coordinate so the sweep isn't done twice. |

## Documentation / Operational Notes

- Consider promoting the channel-removal knowledge (currently only in auto-memory
  `[[feedback_channel_removal_must_prune_auth_type_map]]`) into `docs/solutions/` — there is no
  removal-specific solution doc despite #253 and this plan both doing it.
- Optional follow-up: sweep the remaining dead `_PASTE_BLOB_CHANNELS` rows
  (`habr`/`pikabu`/`segmentfault`) in a separate change if desired.

## Sources & References

- **Origin document:** `docs/brainstorms/2026-05-27-remove-jianshu-and-sweep-retired-channels-requirements.md`
- Sibling plan: `docs/plans/2026-05-26-007-refactor-remove-four-channels-plan.md` (shipped as PR #253)
- Related PRs: #253 (removed 7 channels), #257 (credential-save route — introduced stragglers),
  #259 (removed stale cnblogs test)
- Learnings: `docs/solutions/best-practices/wip-branch-ff-merge-instead-of-reset-2026-05-26.md`,
  `docs/solutions/logic-errors/invert-drift-check-when-invariant-becomes-dynamic-2026-05-18.md`,
  `docs/solutions/workflow-issues/grep-dofollow-map-before-shipping-adapter-2026-05-20.md`
