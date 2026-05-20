---
title: "Probe before write: pivot to None-return when an adapter's upload API is unverifiable"
date: 2026-05-20
category: docs/solutions/best-practices
module: publishing/adapters (embed_banner contract)
problem_type: best_practice
component: image_gen_pipeline
severity: medium
applies_when:
  - "Implementing `embed_banner` for a new adapter whose host has no documented image-upload primitive"
  - "Plan calls a specific GraphQL mutation or REST endpoint, but the docs are silent or paywalled"
  - "Discovering at runtime that introspection is disabled or all candidate endpoints return 4xx/404"
related_components:
  - tooling
tags:
  - probe-first
  - dispatcher-fallback
  - banner-upload
  - none-return
  - writeas-style
  - hashnode
  - velog
---

# Probe before write: pivot to None-return when an adapter's upload API is unverifiable

## Context

The `embed_banner(self, artifact_path, alt) -> str | None` contract supports two return shapes:

- **`str`** — a CDN URL the dispatcher will rewrite into the article body's `![alt](<url>)` prefix.
- **`None`** — the dispatcher falls back to the original `source_url` already encoded in the JSONL row (the writeas pattern), no extra upload step.

When the plan assumes a specific upload mutation but runtime probing shows the mutation is **absent, paywalled, or introspection-disabled with every candidate returning 4xx/404**, the right move is to **pivot to `None`-return** rather than ship dead upload code that emits `BannerUploadError` every row.

## Guidance

Before writing the upload logic, probe the API:

1. **Introspection check** — if GraphQL, `POST {query: "{__schema{types{name}}}"}` against the host. If `200 + parseable schema` → enumerate available mutations. If `400 / "introspection disabled"` → bypass.
2. **Candidate enumeration** — list every plausible mutation name (`uploadImage`, `createAsset`, `uploadFile`, `imageUpload`, `mediaUpload`, `createMedia`) and every plausible REST endpoint (`/api/upload`, `/api/v1/media`, `/upload`, `/files`, `/assets`, `/images`). Send one probe each with a valid auth header.
3. **Verdict**:
   - **Any probe returns 2xx with an asset-shaped response** → proceed with upload implementation.
   - **All probes return 4xx/404** AND the platform has no documented image API → pivot to `None`-return.
   - **Paywalled** (e.g., "Pro feature") → pivot to `None`-return.
4. **Document the findings** in the adapter docstring so the next reader doesn't repeat the probe.
5. **Add a regression guard**: `test_<adapter>_has_embed_banner_attribute` that asserts the method exists AND returns `None` for the dummy artifact. This locks in the pivot decision against accidental flips back to upload mode.

```python
# publishing/adapters/hashnode.py (illustrative, post-pivot shape)
def embed_banner(self, artifact_path: str, alt: str) -> str | None:
    # Probe history (2026-05-20): introspection 200 + schema enumerated,
    # no upload mutation. Pro-only Storyblok integration paywalled
    # 2026-05-13. Pivoted to dispatcher source_url fallback.
    return None
```

```python
# tests/test_hashnode_adapter.py
def test_hashnode_has_embed_banner_attribute():
    adapter = HashnodeAdapter(...)
    assert hasattr(adapter, "embed_banner")
    assert adapter.embed_banner("/tmp/x.png", "alt") is None
```

## Why This Matters

Shipping dead upload code costs more than the upload code itself:

- Every publish row emits a `BannerUploadError` log line + suppressed exception (per the [[embed_banner_lazy_config_load]] contract).
- Operators reading the logs assume the adapter is broken and open issues.
- The next agent reading the source sees "upload code looks complete, just needs debugging" and spends hours probing — same probes already done at plan time.

The `None`-return pivot is **not a failure mode** — it is a documented contract value. The writeas adapter shipped this way from day one, and the dispatcher fallback path (use the JSONL row's `source_url` as banner) is exercised every publish run.

The cost of probing is ~5 minutes. The cost of shipping dead code is days of compounding confusion.

## When to Apply

- New adapter PR where the host's image-upload API is not in the official docs (or docs are paywalled).
- Plan that names a specific mutation but the agent has never run that mutation against a live test account.
- Mid-implementation discovery: "the mutation I planned to use doesn't exist on this host's GraphQL endpoint."

Skip when:

- Host has documented + tested image upload (Medium GraphQL, Blogger REST).
- Plan explicitly carves out banner support as out-of-scope (`embed_banner` not implemented, dispatcher takes the `AttributeError` path).

## Examples

**Right (Hashnode U4, PR #121, 2026-05-20):**

```
Plan:   "Hashnode GraphQL uploadImage mutation"
Probe:  introspection 200 → enumerate → no upload mutation → Pro-only Storyblok
        integration paywalled 2026-05-13
Pivot:  embed_banner returns None; dispatcher uses source_url
Doc:    findings recorded in hashnode.py docstring
Test:   test_hashnode_has_embed_banner_attribute asserts None-return
Ship:   CI green same day
```

**Right (Velog U5, PR #122, 2026-05-20):**

```
Plan:   "Velog GraphQL image upload"
Probe:  introspection disabled (400) → 4 candidate mutations all 4xx → 6 REST
        endpoints all 4xx/404
Pivot:  embed_banner returns None
Doc:    docstring lists the 10 probes attempted (4 mutations + 6 REST) so the
        next agent doesn't redo them
Test:   test_velog_has_embed_banner_attribute
Ship:   CI green same day
```

**Wrong (counterfactual):**

```
Plan-time:    "use uploadImage mutation"
Code:         GraphQL POST with multipart payload, error handling, retries
Runtime:      every publish row → "uploadImage" returns 400 "Field not found"
              → BannerUploadError suppressed → operator log spam
Next session: agent re-probes, rediscovers the API doesn't exist, deletes code
```

## Related

- `docs/solutions/best-practices/banner-image-gen-pipeline-2026-05-20.md` — overarching banner pipeline architecture (this doc is the "what to do when upload isn't possible" branch).
- PR #121 (Hashnode pivot) and PR #122 (Velog pivot) for the empirical evidence.
- Writeas adapter (`publishing/adapters/writeas.py`) — the original `None`-return pattern.
