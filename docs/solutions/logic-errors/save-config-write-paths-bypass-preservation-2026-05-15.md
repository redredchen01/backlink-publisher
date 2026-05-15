---
title: "TOML write paths that bypass save_config — partial serializers silently drop unmanaged sections"
date: 2026-05-15
category: logic-errors
module: backlink-publisher / config_persistence
problem_type: logic_error
component: config_layer
symptoms:
  - "Sections written by hand into config.toml (e.g. `[anchor.proportions]`, `[llm.anchor_provider]`, `[sites.*]`) silently disappear after the next `save_config()` call"
  - "Operator-curated keys under `[sites.\"<host>\".url_categories]` (`hot`, `animate`, `topic`) get overwritten when a new feature writes its own subkeys to the same namespace"
  - "Test suite reports green because no test asserts unknown sections survive a write — see related test-failures entry on the inverted negative-shape assertion that enshrined this exact bug"
root_cause: logic_error
resolution_type: code_fix
severity: high
related_components:
  - config_persistence
  - test_failures
tags:
  - config-persistence
  - toml
  - data-loss
  - serialization
  - silent-drop
  - raw-text-preservation
  - narrow-merge
applies_when:
  - "Adding a new field/section to config that another existing function writes back to disk"
  - "Writing under a namespace that `save_config` does not own (e.g. preserved-by-default sections)"
---

# TOML write paths that bypass `save_config` — partial serializers silently drop unmanaged sections

## Problem

`save_config()` historically used a **full-rewrite** persistence pattern: load the config, hand-roll TOML output for a few known section roots (`[blogger]`, `[medium]`, `[targets]`, plus oauth), write the file back. **Every other section on disk was silently dropped.** New fields landed via PRs that didn't touch `save_config`. The next save quietly erased them. The bug class then recurred when a new feature needed to write under an *already-preserved* namespace and naively considered extending `save_config` to own that namespace too — which would have re-introduced the same drop-on-rewrite hazard.

This entry covers two scenarios in one family because they share the underlying invariant: **anything `save_config` doesn't explicitly preserve will be lost on the next write.**

## Symptoms

- A user adds keys to `config.toml` by hand (e.g. `[targets].anchor_keywords` after a feature PR exposed it as a config knob); next OAuth refresh through the WebUI overwrites the file and the keys are gone.
- A new feature ships a write to `[anchor.proportions]` or `[llm.anchor_provider]`; later, an unrelated `save_config()` call (any path that touches blogger/medium config — every refresh, every settings save) silently drops the new section.
- A new feature wants to write `[sites."<host>".url_categories]` keys; if the chosen path is "extend `save_config` to own the `[sites.*]` namespace", the implementer must also serialize *every* sub-key existing operators already use under `[sites.*]` (e.g. anchor-pool data structures), or those sub-keys vanish on the next write.

## What Didn't Work

- **Trusting the test suite.** Tests existed, all green. The load-bearing test was a negative-shape assertion (`assert "[sites." not in rewritten`) whose docstring rationalized the data loss as a "contract" — see related entry `negative-assertion-locks-in-bug-2026-05-15.md`.
- **Catching it in code review by reading `save_config` alone.** The drop is silent; nothing in `save_config`'s code path raises or warns when a section is dropped. The bug only shows up when an operator notices their hand-edited keys disappear.
- **Solving Scenario 2 by extending `save_config`'s known-roots set.** Tempting for symmetry — "if `save_config` owns `[sites.*]`, then it knows how to write the new keys too." Trap: now `save_config` must serialize every existing `[sites.*]` sub-key (`anchor_pools`, `target_anchor_pools_v2`, etc.) or those vanish on the next write. The fix is bigger than the feature.

## Solution

Two complementary fixes; both have shipped.

**Scenario 1 fix — `_preserve_unknown_sections` (raw-text walk)**: `save_config` now reads the existing file as raw text, identifies sections whose root is in `_SAVE_CONFIG_KNOWN_ROOTS = {"blogger", "medium", "targets"}`, and copies every other section verbatim into the output. New fields ship without needing `save_config` to know about them; operator-curated content survives every write. Plus snapshot-before-write to `.config-history/` (rolling 20) for recoverability and `_atomic_write_text` to prevent partial-write corruption.

**Scenario 2 fix — narrow merge helper (string-level focused write)**: when a feature needs to write under an already-preserved namespace, ship a **focused helper** that lives next to `save_config`, not an extension of it. Example shape (`merge_site_url_categories(main_url, additions)`):

1. Read existing TOML text.
2. Locate the target section header (e.g. `[sites."<host>".url_categories]`) by exact match.
3. Scan body lines: overwrite matching keys, preserve non-matching keys verbatim (comments, blank lines, ordering all survive).
4. Append new keys before any trailing blank line.
5. Snapshot existing file to `.config-history/` (same safety net as `save_config`).
6. Atomic write via `_atomic_write_text`.

The division-of-labor — `save_config` for the three managed roots, narrow merge helpers for everything else — is itself a feature: operator-curated keys stay byte-identical even after the feature writes under the same parent.

## Why This Works

The full-rewrite pattern's failure mode is structural: any field not in the serializer's switch statement is data-loss-by-default. The structural fix swaps the default: instead of "drop unknown", default to "preserve unknown verbatim". The serializer no longer needs to know every field — it only needs to know which roots it **does** own.

The narrow-merge alternative is the same principle applied to a different scope: rather than asking one global function to know everything, give each feature a focused helper that knows only the small section it writes. The set of features grows freely; the failure surface stays bounded.

## Prevention

1. **Audit grep on every config-touching PR**: `rg -n 'save_config|write_text.*toml|tomli_w\.dumps' src/` — every match is a candidate write path. For each, ask: "If a hand-edited section appeared in the file between two calls to this function, would it survive?"
2. **Round-trip test for any new managed field**: `save → load → save → load`, assert all sections survive. Not just the new one — assert *every previously preserved section* still round-trips. This is the structural defense against re-introducing the drop-by-default pattern.
3. **Avoid negative-shape tests for serializer contracts**. `assert "<section>" not in output` is the exact pattern that enshrined this bug for weeks. Use positive assertions paired with semantic round-trip (`assert cfg2.field == cfg.field`) instead. See `negative-assertion-locks-in-bug-2026-05-15.md`.
4. **When tempted to extend `save_config` to own a new namespace, first list every sub-key that namespace currently holds in real operator configs.** If the list has fields the new feature doesn't know how to serialize, prefer the narrow-merge helper instead.
5. **Reject control characters in section identifiers passed to narrow-merge helpers** — string-level TOML editing is brittle against injection. Quote new values via the same `_toml_str` helper `config.py` already uses.

## Related Issues

- `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` — the test-side counterpart of this bug; the test that enshrined the data loss in the suite for weeks. Read together for the full pre/post picture.
- `docs/solutions/test-failures/negative-assertion-locks-in-bug-2026-05-15.md` — the general anti-pattern (this bug is one of two recorded instances).
- Provenance: `feedback_config-save-overwrite-pattern.md` (auto memory [claude], first encountered 2026-05-14); `feedback_narrow-toml-merge-bypasses-save_config.md` (auto memory [claude], first encountered 2026-05-15).
