---
title: Dofollow canary verdict silently dropped at the publish-output serialization seam
date: 2026-05-25
category: integration-issues
module: backlink_publisher.publishing
problem_type: integration_issue
component: service_object
symptoms:
  - "link_attr_verification verdict computed by verify_link_attributes never appears in publish-backlinks JSONL output"
  - "Operators running the dofollow canary loop cannot observe the verdict despite it being stored on _provider_meta"
  - "The canary feedback loop (publish -> verify rel -> flip dofollow flag) is unobservable end to end; no error, no warning"
root_cause: missing_workflow_step
resolution_type: code_fix
severity: medium
related_components:
  - tooling
tags:
  - dofollow-canary
  - link-attr-verification
  - publish-output
  - serialization-seam
  - resume-path
  - provider-meta
---

# Dofollow canary verdict silently dropped at the publish-output serialization seam

## Problem

The post-publish "dofollow canary" verdict from `verify_link_attributes` was computed and attached to the adapter result, but **both** `publish-backlinks` output serializers silently dropped it — leaving the R4 canary loop unobservable to operators. Fixed in PR #217 (squash `cb3dfeb`, merged origin/main 2026-05-25).

## Symptoms

- `verify_link_attributes` (`publishing/adapters/link_attr_verifier.py`) inspects a live published page's `<a>` tags and returns a verdict dict, which adapters attach to `AdapterResult._provider_meta["link_attr_verification"]` — yet the key never appeared in any line of the `publish-backlinks` JSONL output.
- The canary loop could not be closed: the operator had no way to read the `rel` verdict from pipeline output, so no dofollow/nofollow decision could be made.
- A pure silent-drop. No error, no warning — the output shape simply lacked the key.

## What Didn't Work

The verdict only ever lived on `_provider_meta`. The conversion from `AdapterResult` to the emitted JSONL dict happened in **two independent serializers**, and the verdict was wired into **neither**:

- `publishing/adapters/base.py::AdapterResult.to_publish_output()` — the fresh-publish path. Built a fixed dict and returned it; `_provider_meta` was never consulted.
- `cli/_resume.py::item_to_publish_output()` — the resume-from-checkpoint path. Built its own parallel dict from a checkpoint `item`; also never carried the verdict.

This is the repo's recurring **"missed one dispatch path"** bug class. Fixing only the fresh emitter would still leave resumed runs blind. A review pass also caught a worsening factor: the first draft fixed the two seams with **divergent idioms** (`"key" in meta` on one, `.get() is not None` on the other), risking inconsistent emit behavior between the paths.

## Solution

Extract a single shared carry-helper in `base.py` and call it as the final step of *both* emitters.

`publishing/adapters/base.py` (new helper):

```python
_LINK_ATTR_VERIFICATION_KEY = "link_attr_verification"

def carry_link_attr_verification(
    out: dict[str, Any], source: dict[str, Any] | None
) -> dict[str, Any]:
    """Copy the post-publish link-attribute verdict into ``out`` when present.

    ``source`` is the metadata holder -- ``AdapterResult._provider_meta`` on the
    fresh path or a checkpoint item on the resume path. Shared by both
    publish-output emitters so the two paths stay byte-identical.
    """
    if source:
        verdict = source.get(_LINK_ATTR_VERIFICATION_KEY)
        if verdict is not None:
            out[_LINK_ATTR_VERIFICATION_KEY] = verdict
    return out
```

Fresh path — `to_publish_output` (before/after):

```python
# BEFORE
return {"id": ..., "adapter": self.adapter, "error": self.error}

# AFTER
out = {"id": ..., "adapter": self.adapter, "error": self.error}
return carry_link_attr_verification(out, self._provider_meta)
```

Resume path — `cli/_resume.py::item_to_publish_output`:

```python
from backlink_publisher.publishing.adapters.base import carry_link_attr_verification
# ...
out = {"id": ..., "adapter": ..., "error": None}
return carry_link_attr_verification(out, item)   # checkpoint item is the source
```

The emitted key is additive and optional; `schema.validate_publish_payload` tolerates unknown keys, so draft mode and non-verifying adapters keep an unchanged output shape.

**6 new tests** lock both paths against regression:

- `tests/test_adapter_base.py`: verdict present → emitted; `skipped` verdict → emitted; `_provider_meta is None` → key omitted; meta without the key → key omitted.
- `tests/test_publish_backlinks_resume.py`: checkpoint without verdict → omitted; checkpoint with verdict → emitted.

## Why This Works

A single chokepoint (`carry_link_attr_verification`) is the last line of both emitters, so the fresh and resume paths produce byte-identical handling of the verdict — idiom drift cannot reappear. The key is emitted only when `source` carries a non-`None` value, so draft mode and non-verifying adapters are unaffected. Because the key is additive and the schema validator tolerates unknown keys, no downstream consumer breaks and the change is fully backward-compatible.

## Prevention

1. **Enumerate ALL emit/dispatch paths when a value must survive serialization.** This repo has a recurring "missed one dispatch path" bug class (see also the `target_language` schema+dispatcher trap and credential rotation-vs-bootstrap). For publish output the live set is *fresh* (`base.py::to_publish_output`) + *resume* (`_resume.py::item_to_publish_output`) + any future *migration* path — grep every dict-construction site that becomes a JSONL line before assuming one fix is enough.
2. **Extract a shared carry-helper rather than duplicating copy logic at each seam.** Two hand-written copy snippets drifted in idiom; one helper called from both seams makes divergence structurally impossible. (Same pattern as the WebUI publish-history invariant — see Related.)
3. **Write a test per emit path asserting BOTH presence and absence.** Each serializer needs a "verdict present → key emitted" and a "verdict absent → key omitted" test, so a future refactor that drops the call at one seam fails immediately.
4. **The verdict surfaces data; it does not make the decision.** `verify_link_attributes` scans **every** `<a>` on the page and aggregates — `nofollow_detected = (nofollow_anchors > 0)` counts ANY `rel="nofollow"` anywhere (nav, footer, related-posts widgets), so it is noisy. The operator must inspect the **target backlink's own `rel`**, not the page-wide flag, before flipping any adapter's dofollow tier. The canary also must be a single **fresh** publish: the checkpoint does not persist `_provider_meta`, so a resumed run carries no verdict (the resume-path wiring is forward-compatible only).

## Related Issues

- `docs/solutions/best-practices/publish-history-helper-invariant-2026-05-20.md` — sibling "shared-emit-helper invariant" pattern: a field silently dropped because writes bypassed a shared helper; same fix shape (route every emitter through one canonical carry-point). Different module (`webui_app` vs `publishing/adapters`), same prevention rule.
- `docs/solutions/workflow-issues/grep-dofollow-map-before-shipping-adapter-2026-05-20.md` — adjacent dofollow/SEO guidance (validate dofollow value before shipping an adapter); complementary, not contradicted.
- Operator follow-up for the canary loop lives in `docs/runbooks/2026-05-25-dofollow-canary-closeout.md` (fresh-publish from throwaway account → inspect target rel → flip PR).
