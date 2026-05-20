---
title: "`embed_banner` lazy `load_config()` — config access lives inside the method, banner failures raise `BannerUploadError`"
date: 2026-05-20
category: docs/solutions/best-practices
module: publishing/adapters (embed_banner contract)
problem_type: best_practice
component: image_gen_pipeline
severity: high
applies_when:
  - "Implementing `embed_banner` on an adapter that needs `[targets.<channel>]` config keys (repo, branch, token, ...)"
  - "Reviewing a PR that adds config parameters to `embed_banner`'s signature"
  - "Diagnosing a publish run where banner failure caused the row to exit with code 3 instead of being logged-and-skipped"
related_components:
  - rails_controller
  - service_object
tags:
  - embed-banner
  - lazy-config
  - banner-upload-error
  - dependency-error
  - publish-exit-code
  - sec-3
  - contract-purity
---

# `embed_banner` lazy `load_config()` — config access lives inside the method

## Context

`embed_banner` was pinned at Unit 1 of the banner-image-gen plan to the signature `embed_banner(self, artifact_path: str, alt: str) -> str | None`. **No `config` argument.** The contract is intentionally narrow — the dispatcher passes only the artifact path and alt text, and the adapter is responsible for any per-channel state it needs.

This creates a real implementation question for adapters that genuinely need config: the GitHub Pages adapter (PR #123) needs `[targets.ghpages].repo`, `.branch`, `.token`, `.collection_alias`. The naive options are both wrong:

- **Cache config on `self`** at `__init__` time → makes the adapter stateful and breaks rotation when operators edit `config.toml` between runs.
- **Widen the signature** → violates the U1-pinned contract; cascades to every other adapter that doesn't need config.

The right answer is **lazy `load_config()` inside the method body**, with strict failure mapping.

## Guidance

Inside `embed_banner`, call `load_config()` to read the freshly current config:

```python
# publishing/adapters/ghpages.py
def embed_banner(self, artifact_path: str, alt: str) -> str | None:
    try:
        cfg = load_config()  # honors BACKLINK_PUBLISHER_CONFIG_DIR; reads disk fresh
        target = cfg.targets["ghpages"]
        repo = target.repo
        branch = target.branch
        token = target.token
    except (KeyError, AttributeError, FileNotFoundError) as exc:
        raise BannerUploadError(
            f"ghpages banner upload: config missing or incomplete: {exc!r}"
        ) from exc

    # ... actual upload using repo / branch / token ...
```

Two non-negotiable rules:

1. **All config / token failures must re-raise as `BannerUploadError`** — never as `DependencyError`, `AuthExpiredError`, or any other exception type the publish pipeline treats as "abort the row."
2. **`BannerUploadError` is caught by the dispatcher and logged-and-skipped** — the underlying publish proceeds with the JSONL row's original `source_url` as the banner. Banner failure must not fail the post.

The dispatcher contract (see `publishing/pipeline.py`):

| Exception raised inside `embed_banner` | Dispatcher behavior | Publish row exit code |
|----------------------------------------|---------------------|------------------------|
| `BannerUploadError` | Log, swallow, fall back to `source_url` | 0 (success) |
| `DependencyError` | Abort publish run | 3 |
| `AuthExpiredError` | Abort publish run, mark channel rotated | 3 |
| Unhandled exception | Abort publish run | 3 |

Raising `DependencyError` because "the token isn't configured" looks principled but breaks the publishing run for an optional feature. Banner is enrichment, not infrastructure.

## Why This Matters

`load_config()` lazy-reads honors `BACKLINK_PUBLISHER_CONFIG_DIR`:

- Tests run with isolated config sandboxes (the autouse conftest fixture). A `self.config` cached at `__init__` time captures the test sandbox; lazy load picks up the real per-test override.
- Operators rotating tokens mid-session (e.g., GitHub PAT expired) get the new token on the next publish row, no restart needed.
- The CLI `BACKLINK_PUBLISHER_CONFIG_DIR=/path/to/throwaway python -m backlink_publisher.cli.publish_backlinks ...` workflow works without per-adapter coordination.

The `BannerUploadError`-only failure rule matters because the project's contract is that exit-3 means "publish actually failed" — operator playbooks, monitoring alerts, and retry logic all key off exit-3. Banner failures are noise, not failures. If we raise `DependencyError` on a missing banner token, we conflate "no banner" with "publish broken" and break the retry semantics.

## When to Apply

- Implementing `embed_banner` on any new adapter that requires per-channel config or tokens (ghpages, future hosted-repo adapters).
- Reviewing a PR that adds a config parameter to `embed_banner`'s call site — push back: lazy load instead.
- Diagnosing why a publish run with a misconfigured banner channel exits 3 — the adapter is likely raising the wrong exception type.

Skip when:

- Adapter's `embed_banner` returns `None` (writeas / hashnode / velog pivot) — no config to load.
- Adapter is stateless w.r.t. banner upload (the artifact is uploaded to a global CDN, not a per-channel target).

## Examples

**Right (PR #123, GitHub Pages, 2026-05-20):**

```python
def embed_banner(self, artifact_path: str, alt: str) -> str | None:
    try:
        cfg = load_config()
        target = cfg.targets["ghpages"]
    except (KeyError, AttributeError, FileNotFoundError) as exc:
        raise BannerUploadError(f"ghpages config missing: {exc!r}") from exc

    sha16 = _sha16_of_file(artifact_path)
    ext = pathlib.Path(artifact_path).suffix.lstrip(".")
    asset_path = f"assets/banners/{sha16}.{ext}"

    # Idempotent: GET first, only PUT if missing
    if self._asset_exists(target.repo, target.branch, asset_path, target.token):
        return self._cdn_url(target.repo, target.branch, asset_path)

    self._put_binary_contents(
        target.repo, target.branch, asset_path,
        pathlib.Path(artifact_path).read_bytes(), target.token,
    )
    return self._cdn_url(target.repo, target.branch, asset_path)
```

Content-addressed `assets/banners/<sha16>.<ext>` path means re-runs are idempotent (same banner → same URL → GET probe short-circuits). `_put_binary_contents` is distinct from the text-PUT path used for the actual post body (Base64 + binary-safe).

**Wrong (counterfactual):**

```python
def __init__(self, ...):
    cfg = load_config()
    self._target = cfg.targets["ghpages"]  # frozen at adapter construction

def embed_banner(self, artifact_path, alt):
    if not self._target.token:
        raise DependencyError("ghpages token not configured")  # WRONG TYPE
    ...
```

Two problems: stale config + wrong exception type → publish row exits 3 on every run even though everything else works.

## Related

- `docs/solutions/best-practices/banner-image-gen-pipeline-2026-05-20.md` — overall banner architecture.
- `docs/solutions/best-practices/probe-then-pivot-when-api-unverifiable-2026-05-20.md` — the `None`-return pivot branch.
- PR #123 (ghpages U6) for the canonical implementation.
- `publishing/pipeline.py` — dispatcher exception-handling for the contract above.
- `_util/config.py:load_config` — lazy loader that honors `BACKLINK_PUBLISHER_CONFIG_DIR`.
