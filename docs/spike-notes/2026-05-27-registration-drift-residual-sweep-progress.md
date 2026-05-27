# Registration-Drift Residual Sweep — execution progress / handoff

**Branch:** `feat/registration-drift-residual-sweep` (worktree `bp-registration-drift`)
**Base:** clean `origin/main` `85d34df8` (post-#280)
**Plan:** `docs/plans/2026-05-27-008-feat-registration-drift-residual-sweep-plan.md` —
NOTE this plan + its origin brainstorm are **uncommitted in a *different* (dirty
main) worktree** authored by a concurrent agent; they are NOT in `origin/main`,
so this branch does not carry them. This note is the self-contained record.
**Suite:** full `PYTHONPATH=src PYTHONHASHSEED=0 pytest tests/` → **5787 passed, 6 skipped, 0 failed.**

## Done (4 commits)

| Unit | Commit | Summary |
|---|---|---|
| U1 | `a7f2948d` | `registry.platforms_by_auth_type(target)` — single live registry reverse-lookup (never cached → no import-time-assert trap) + `tests/test_platform_lookup.py` |
| U2 | `1dc39678` | `tests/test_channels_recipe_drift.py` — 3-way guard `CHANNELS == set(RECIPES) == {recipe files on disk}`; the filesystem leg was the unguarded one |
| U3 | `80452486` | `tests/test_credential_save_dispatch_drift.py` — `⊆` drift guard over all 6 credential-save maps keyed by `auth_type` |
| U6 | `964f37e9` | `tests/test_registration_drift_extension_proof.py` — registering+classifying a new platform auto-expands the guard authority with zero guard edits |

**Net production change: one additive function** (`platforms_by_auth_type` in
`registry.py`). Everything else is new test files. The registration-drift **data**
class (the #253 / dofollow-map / wire-5-sites recurrence) is now mechanically
guarded for the credential-save and browser-bind satellites.

## Census findings (U1) — load-bearing for anyone resuming

1. **The plan's U1 "bind-without-publish blocker" was stale/phantom.** It named
   `_PASTE_BLOB_CHANNELS = habr/pikabu/segmentfault/zhihu` and
   `_USERPASS_MODULES = cnblogs` — **none of those exist** on current main. The
   real maps are `_PASTE_BLOB_CHANNELS = csdn/juejin/note/substack` and
   `_USERPASS_MODULES = livejournal`, and **every** save-dispatch member is a
   registered, active platform. No product-defining ambiguity; no `ce:brainstorm`
   bounce needed.
2. **Guard authority is `⊆`, not `==`.** Save-dispatch maps are intentionally a
   subset of each `auth_type` bucket: `hashnode` is config-file-only, the
   dedicated-route channels (`ghpages`/`devto`/`notion`) live in their own
   handlers, `blogger` is oauth, `medium`/`velog`/`mastodon` are browser-bind.
   Requiring every bucket member to be wired would be a UI-completeness check,
   not registration drift.
3. **`CHANNELS` cannot be derived from `RECIPES`** (a cycle): `medium.py` imports
   `cli._bind.driver` + `config.loader`, and `channels/__init__` is a low-level
   traversal-defense primitive imported by the driver/`AuthExpiredError`/argparse.
   `channels → recipes → medium → driver → channels`. Kept `CHANNELS` literal +
   guarded (per the plan's explicit fallback).

## Side-finding (NOT fixed — separate from this plan's scope)

`tumblr` has a `token_fields` binding card (`_settings_binding_token_fields.html:36`)
but `_save_token_fields` returns **"保存未实现（待实现）"** because `tumblr` is not in
`_TOKEN_FIELDS_DISPATCH` (only `wordpresscom` is). So tumblr's WebUI credential
save is a known-pending UI gap (graceful flash, not a crash). This is
UI-completeness, not registration drift — worth a separate follow-up.

## Deferred — U4 and U5 (deliberately, for a fresh focused session)

These were paused because U4 touches production behavior and U5 is the contested
"forward guard" (the unit the originating brainstorm session recommended dropping
before plan-008 was adopted). Resume guidance:

- **U4 — reconcile `_verify_helpers.py`.** `_SETUP_CHECKS` has **10** keys
  (blogger, devto, ghpages, hashnode, medium, notion, substack, telegraph, velog,
  wordpresscom); plus **6** `if platform ==` branches across two functions
  (`verify_adapter_setup`: livejournal, mastodon; `_verify_live`: telegraph,
  ghpages, blogger, velog). **Characterization-first**: pin current
  `verify_adapter_setup` output for all platforms before moving any branch. The
  livejournal/mastodon branches exist because `available()` is unreliable for them
  — any promotion to a `Publisher.verify_setup()` ABC method **must preserve those
  probes**. First confirm the `_verify_live` 222-231 block isn't dead/duplicated.
  These are behavioral *callables*, a legitimate platform-keyed pattern — leaving
  them as-is (allow-listed in U5) is a valid outcome.

- **U5 — forward meta-guard (contested).** Runtime registry-membership scan
  (walk `vars()` of non-registry modules after lazy-importing
  `publishing.adapters`; flag dict/set/frozenset whose members ∩
  `registered_platforms()` has size > 1 and isn't allow-listed). Test-time + lazy
  import only (registry dicts are empty until adapters import). **Allow-list must
  include** the registry-internal `_*_BY_PLATFORM` family, `ROUTE_TIER_MATRIX`
  (already self-guarded by `_matrix_targets_registered_platforms()`),
  `_DOFOLLOW_BY_CHANNEL`, `NOFOLLOW_RATIONALES`, the 6 save-dispatch maps now
  guarded by U3, and the U4 verify-dispatch. **Open risk (from review):** the scan
  may false-positive on legitimate platform-keyed maps and miss the real drift
  shapes (`if platform ==` chains, size-1 cases); use a planted red→green
  self-test (model: `tests/test_cli_exit_code_literals.py`) and enumerate the
  residual catch-set before shipping, or the guard risks being inert.

## Coordination status

- `recurring-trap-eradication` reciprocal-edit prerequisite is **MOOT** — shipped
  as PR #280 (N=0 audit, "class already guarded"), now in `origin/main`.
- `channel-manifest-architecture` R5 (token-paste wire / `active_names`
  reverse-lookup) **still overlaps** U3's territory — coordinate before that plan
  edits `token_paste.py` / `contexts.py`.

## Suggested next steps

1. This branch (U1–U3+U6) is a coherent, green, low-risk increment — ready to
   push + open a PR on its own (the drift class is guarded + proven extensible).
2. Resume U4 (characterization-first) and U5 (with the allow-list + planted
   self-test above) in a focused session.
3. Have the concurrent agent commit/own plan-008 + the brainstorm doc (currently
   uncommitted in the main worktree), then tick U1/U2/U3/U6 and adjust U1's stale
   blocker text to match this census.
