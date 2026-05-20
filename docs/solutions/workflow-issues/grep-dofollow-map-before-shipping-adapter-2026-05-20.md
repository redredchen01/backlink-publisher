---
title: "Grep `_DOFOLLOW_BY_CHANNEL` before shipping any new publishing adapter"
date: 2026-05-20
category: docs/solutions/workflow-issues
module: publishing/adapters + webui_app/binding_status
problem_type: workflow_issue
component: development_workflow
severity: high
applies_when:
  - "Adding a new publishing adapter under publishing/adapters/ (R9 extension recipe)"
  - "Reviewing a PR that lights up a previously inert platform in the registry"
  - "Evaluating whether a new channel is worth shipping before writing tests + ~1k lines of integration"
related_components:
  - tooling
  - documentation
tags:
  - dofollow
  - nofollow
  - r9-extension
  - value-validation
  - ship-revert
  - registry-vs-value
---

# Grep `_DOFOLLOW_BY_CHANNEL` before shipping any new publishing adapter

## Context

The whole reason `backlink-publisher` exists is to publish **dofollow** backlinks. Nofollow links from `rel="nofollow"` / `rel="ugc"` / `rel="nofollow noopener noreferrer"` carry no SEO link-equity and are negative engineering value (maintenance burden with no upside).

On 2026-05-20 PR #108 shipped Phase 4 (Dev.to + WP.com + Mastodon) — 60 tests, ~1635 lines, fully passing R9 extension contract — and was reverted via PR #109 nine minutes later because the three platforms were already documented as nofollow in `webui_app/binding_status.py:38-48`. The registry validated. The value did not.

```python
# webui_app/binding_status.py (post-revert state preserved)
_DOFOLLOW_BY_CHANNEL = {
    "telegraph": True,
    "blogger": True,
    "medium": True,
    "ghpages": True,
    "writeas": True,
    "velog": True,
    "hashnode": None,    # CF-blocked, cannot empirically verify
    "devto": False,      # rel="nofollow ugc" since 2022
    "mastodon": False,   # hardcoded rel="nofollow noopener noreferrer"
    "wpcom": False,      # free tier nofollow
}
```

The map already encoded prior empirical knowledge. Shipping ignored it.

## Guidance

Before opening a PR that adds a new platform to `publishing.registry.registered_platforms()`, run two greps and read the output:

```bash
# 1. Is this channel already classified?
grep -n "^\s*\"<channel>\":" webui_app/binding_status.py

# 2. What's the dofollow verdict for the candidate channel?
grep -nB1 -A1 '_DOFOLLOW_BY_CHANNEL' webui_app/binding_status.py
```

If `_DOFOLLOW_BY_CHANNEL["<channel>"]` is:

- `True` → ship. The platform earns its keep.
- `False` → **do not ship**. The platform is documented negative-value. If you believe the verdict is stale, the PR description must show fresh empirical evidence (rendered HTML + `rel` attribute screenshot from at least two sampled posts) and the change must be its own PR — not bundled with the new adapter.
- `None` → flag in the PR description. `None` means "we tried and couldn't verify" (e.g., Cloudflare-blocked rendering). Either provide fresh evidence or ship as a known-unknown with an explicit caveat in the adapter docstring.

For the inverse direction (lighting up a platform whose `_DOFOLLOW_BY_CHANNEL` entry is missing), add the entry in the same PR as the adapter.

## Why This Matters

The R9 registry-driven extension contract (`register("x", XAdapter)` is the only edit point) optimizes for the right pattern but creates a blind spot: it validates that the adapter wires correctly, never that the platform is worth wiring.

In the PR #108 case, the agent followed the contract perfectly — argparse choices, schema validation, throttle gating, tier matrix all came along for free — but never asked the prerequisite question. The shipping cost was real (revert PR, dirtied main commit log, second-guessing for downstream agents). The cost would have been zero if the grep had run once before plan-time.

This generalizes: clean architectural contracts narrow the agent's attention to mechanical correctness. Value validation has to be an explicit step, not an emergent property of "the tests pass."

## When to Apply

- Opening any PR that touches `publishing/adapters/__init__.py` `register(...)` calls.
- Planning a Phase N expansion that adds platforms to the publishing rotation.
- Reviewing a Tier-2 `/ce:review` on adapter work — `_DOFOLLOW_BY_CHANNEL` grep is a checklist item, not a discretionary look.

This does **not** apply to non-adapter work in `publishing/` (throttle, retry, scheduling) — the negative-value risk is specific to platforms publishing nofollow markup.

## Examples

**Right (Phase 3, PR #102/#103/#107):**

Before opening the Hashnode/Writeas/Ghpages PRs, the dofollow status was verified by hand on sampled posts and recorded:

```
ghpages    = "Bearer <pat>"      → 2 sampled posts, 10+ external <a>, no rel attr → True
writeas    = "Token <tok>"       → 2 sampled posts, all <a> no rel              → True
hashnode   = bare PAT, no prefix → CF-blocked rendering; recorded as None
```

The verdicts went into `_DOFOLLOW_BY_CHANNEL` in the same PR as the adapter. Phase 3 platforms shipped clean.

**Wrong (Phase 4, PR #108 → #109):**

```
Plan-time:   "Phase 4 = Dev.to + WP.com + Mastodon, all support tokens, R9 contract"
Code:        60 tests, 1635 lines, 3 adapters, full registry wiring
Pre-merge:   skipped grep
9 min after: revert
```

Negative knowledge (`False` / `None` entries) was preserved in `_DOFOLLOW_BY_CHANNEL` post-revert so the next agent inherits the verdict rather than rediscovering it.

## Related

- `docs/solutions/best-practices/banner-image-gen-pipeline-2026-05-20.md` — image-gen pipeline shipped the same day; survived because the per-platform dimension was independently verified.
- AGENTS.md → "Adding a new publisher adapter" recipe (canonical R9 contract).
- PRs #108 (ship) and #109 (revert) for the full diff.
