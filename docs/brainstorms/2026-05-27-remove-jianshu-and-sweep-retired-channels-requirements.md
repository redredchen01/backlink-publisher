---
date: 2026-05-27
topic: remove-jianshu-and-sweep-retired-channels
---

# Remove jianshu Channel + Sweep ghost/cnblogs/zhihu Dead References

## Problem Frame

Operator asked to "徹底移除" four channels: **jianshu, ghost, cnblogs, zhihu**. Verification
against the latest `origin/main` — **not** the operator's local tree — shows the situation is
asymmetric:

- **ghost / cnblogs / zhihu** — adapter modules, `register()` blocks, manifests, and rationales
  were hard-removed by **PR #253** (`f35f09f`). The adapter modules no longer exist.
- **jianshu** — still fully live: `register()` block, manifest, nofollow rationale, adapter
  module, auth-type map entry, WebUI bind-save mapping, and three hardcoded sample tests.
  Registered `dofollow=False` (link.jianshu.com/go redirect interstitial strips equity).
- **Stragglers introduced after #253** — PR #257 (`channel_bind_save.py`) hardcoded a live
  `zhihu` credential-map row and a `cnblogs` `_USERPASS_MODULES` dispatch row pointing at the
  deleted `cnblogs_api`, plus a `cnblogs` docstring mention. The binding templates still render
  dead UI: a `ghost` token-field block (+ adjacent `beehiiv`) in
  `_settings_binding_token_fields.html`, and `cnblogs` references in
  `_settings_binding_userpass.html`.
- **Already resolved — do not re-do:** PR #259 deleted the stale `test_userpass_cnblogs_stores_plaintext`.

This is the reverse of the documented "1 dict + 1 register line" add recipe, identical in shape
to plan `2026-05-26-007-refactor-remove-four-channels`.

## Requirements

**Jianshu hard removal**
- R1. `registered_platforms()` no longer contains `jianshu`.
- R2. Adapter module `jianshu_api.py` deleted.
- R3. `register("jianshu", …)` block + `JianshuAPIAdapter`/`JIANSHU_MANIFEST` imports removed.
- R4. `JIANSHU_MANIFEST` definition removed.
- R5. `"jianshu"` rationale removed; sibling csdn/juejin prose copy-edited (no dangling fragment).
- R5b. `_REJECTED_PLATFORMS["jianshu"]` added (≥80-char rationale) preserving the nofollow
  discovery and arming the re-add tripwire.
- R6. `"jianshu"` removed from `_AUTH_TYPE_BY_PLATFORM`.
- R7. jianshu removed from the bind-save map + paste_blob template docstring.
- R8. jianshu removed from the three hardcoded sample tests.

**Sweep ghost/cnblogs/zhihu dead references**
- R9. Dead `zhihu` / `cnblogs` references removed from `channel_bind_save.py`.
- R10. Dead UI template blocks removed (`ghost` in token_fields, `cnblogs` in userpass).
- R10d. One-shot, self-disabling unlink of orphaned `{jianshu,zhihu,cnblogs}-credentials.json`
  files (ghost has no credentials file); failures logged, not silently swallowed.

**Verification**
- R11. Full pytest passes after rebasing onto latest `origin/main`.
- R12. `grep -i` for the four slugs returns nothing in `src/`, `webui_app/`, active tests
  (excluding historical `docs/` + known false positives).

## Success Criteria
- `registered_platforms()` minus jianshu; ghost/cnblogs/zhihu remain absent; count drops by one.
- No dangling import, manifest, rationale, auth-type entry, bind-save row, dead UI block, or test
  references any of the four slugs.
- Full pytest green on a branch rebased onto current `origin/main`.

## Scope Boundaries
- **Non-goal:** editing `cli/*.py`, `schema.py`, `content_negotiation.py` — registry-driven.
- **Non-goal:** rewriting historical `docs/plans/*` / `docs/requirements/*`.
- **Non-goal:** the `HIDDEN_FROM_UI` soft-retire path — this is a hard removal.
- **Non-goal:** false-positive `ghost` substrings (test var names, `ghost.png`, `HostFilter`).

## Key Decisions
- **Hard-delete the adapter, but park the rationale in `_REJECTED_PLATFORMS` (R5b):** preserve the
  equity-stripping discovery + arm the re-add tripwire. Deliberate departure from #253.
- **One-shot, self-disabling credential cleanup in code (R10d):** sentinel-guarded, symlink-safe,
  log-on-failure — not a permanent startup routine.
- **Fold the post-#253 stragglers into this task:** the only way R12's grep gate passes.

## Dependencies / Assumptions
- Branch off **latest `origin/main`**; preserve operator spike WIP (do not clobber).
- Removing a channel must prune `_AUTH_TYPE_BY_PLATFORM` (R6) **and** the three hardcoded sample
  tests (R8), or CI ("merge into latest main") goes red on a stale base → rebase first.
  (See `[[feedback_channel_removal_must_prune_auth_type_map]]`.)

## Outstanding Questions

### Deferred to Planning / Implementation
- [Affects R5] After the prose copy-edit, re-read juejin/csdn rationale strings for grammaticality.
- [Affects R10d] Choose the unlink trigger (one-shot sentinel chosen during planning).

## Next Steps
→ `/ce:plan` (done: docs/plans/2026-05-27-001-refactor-remove-jianshu-channel-plan.md)
