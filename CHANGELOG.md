# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Fixed

- `linkcheck.verify.verify_published` and `linkcheck.http.check_url` no longer
  crash with `'ascii' codec can't encode characters` when the URL carries
  legitimate non-ASCII bytes (Velog Korean `@username`, CJK `url_slug` from
  Hashnode / Velog). A new `_util.url.normalize_url_for_fetch` helper
  IDNA-encodes the host and percent-encodes the path/query at the fetch
  boundary; ASCII URLs pass through byte-identical. Previously such URLs
  demoted legitimately-published posts to `published_unverified`. Plan
  2026-05-21-005.

### Added

- `medium-login` CLI: thin alias for `bind-channel --channel medium`, matching
  the `velog-login` pattern (Plan 2026-05-19-005 Unit 1).
- `ChannelRecipe.post_persist` hook (optional): driver invokes after
  `_persist_storage_state` succeeds and before `mark_bound`, letting recipes
  derive secondary credential files. Used by the medium recipe to convert
  Playwright `storage_state.json` into a cookies-only `medium-cookies.json`
  + a `medium-meta.json` (UA + chromium version, captured live by the
  predicate). velog / blogger recipes leave `post_persist` `None` — no
  behavior change.

### Changed (**Breaking** for existing Medium operators)

- `MediumBrowserAdapter` now reads its credential from
  `<config_dir>/medium-cookies.json` via `context.add_cookies([...])`. The
  pre-Plan-005 path that read `medium-storage-state.json` via
  `new_context(storage_state=...)` is removed; no double-write window, no
  fallback. Operators upgrading across this release must run `medium-login`
  (or `bind-channel --channel medium`) once to populate the new file. The
  adapter's friendly `DependencyError` on first invocation spells out the
  exact command.
- `bind-channel medium` now writes `medium-cookies.json` (the new canonical
  bound credential) and unlinks `medium-storage-state.json` in the same
  bind cycle. The `channel_status_store["medium"]["storage_state_path"]`
  field now points at `medium-cookies.json` (the field name remains
  historical; the value reflects current canonical state).

### Notes

- Hard-cut chosen over a 60-day double-write window: this is a
  single-operator tool per AGENTS.md, so the 2-minute cost of running
  `medium-login` once is lower than the cost of maintaining a dual-format
  compatibility layer with a calendar-driven sunset PR.
- Future `MediumGraphQLAdapter` (Plan 2026-05-19-005 Unit 2, Phase 2,
  gated by spike) will consume the same `medium-cookies.json` +
  `medium-meta.json` for headless GraphQL publishing.

[Unreleased]: https://github.com/redredchen01/backlink-publisher/compare/main...HEAD
