---
title: "Don't synthesize URLs at plan time without config sourcing or live verification"
date: 2026-05-15
category: best-practices
module: backlink-publisher / plan_backlinks
problem_type: best_practice
component: plan_time_validation
severity: medium
applies_when:
  - "Writing code that derives URLs by string concatenation (e.g. `main_domain + '/some-path'`)"
  - "Marking a synthesized URL as `required: true` in the plan output payload"
  - "Adding a link-density / link-quota calculation that credits a synthesized link toward a downstream invariant"
tags:
  - plan-time-validation
  - url-synthesis
  - publish-time-gate
  - 404-prevention
  - config-sourcing
  - link-density
---

# Don't synthesize URLs at plan time without config sourcing or live verification

## Guidance

When `plan-backlinks` (or any payload builder) appends URLs derived by concatenation — `something_url = base_url + "/literal-path"` — the path must either:

(a) be sourced from per-domain config that the operator owns (e.g. `[sites."<host>".url_categories]`), or
(b) be live-verified via `check_url` before inclusion.

**Never** mark a synthesized URL as `required: true` without one of these. The publish-time reachability gate (R8/R9 from the linkcheck PR) exists to catch broken backlinks before publish; it correctly catches a fictional URL — but the right fix is upstream (don't emit the URL) rather than soft-failing the gate.

**Companion rule for callers**: when you remove a synthesized link, also revisit any density / quota calculation that was crediting the synthesized link toward a downstream invariant. The link-density paragraph generator can have the same hardcoded assumption (`url_mode == "B" → base += 1` for the would-be link) and would silently produce under-dense articles after the link is dropped if it isn't updated at the same time.

## When to Apply

- **Plan/payload code** that builds URLs from a pattern. Audit grep: `rg -n '\+ "/' src/backlink_publisher/cli/` and `rg -n 'main_domain.*\+' src/`. Each match is a candidate.
- **`required: true` flags** on any URL the system did not load from config or verify live. Audit: `rg -n 'required.*True' src/`. Each `required` flag should be paired with either a config-load or a `check_url` call.
- **Quota / density / count code** that hardcodes the same path or mode-specific link count. Audit: when removing a synthesized URL, grep for any string-or-mode-specific count that referenced it.

## Why This Works

The publish-time reachability gate is a backstop, not a primary defense. It catches the synthesis bug only after the row has burned planning cost (anchor selection, language detection, body generation), and only then by failing the row entirely — wasting all that work. Catching the same problem at plan time, before any downstream cost is incurred, costs nothing extra and produces actionable diagnostics (which target host, which mode, why the synthesis was rejected).

Sourcing URLs from per-host config is also the structurally correct division of responsibility: the operator knows what their target sites actually serve; the planner should ask, not guess. Config-sourced URLs survive target-site URL changes (operator updates the config); guessed URLs go stale silently.

## Examples

A run failed at publish time with `target unreachable at publish-time: https://example.com/categories`. The URL was synthesized by `_build_links()` in `cli/plan_backlinks.py` (B mode, line ~212 historically) and (C mode, line ~220), marked `required: true`. The target site doesn't actually serve `/categories` (it returns HTTP 404), and the operator's config had no `[sites."<target-host>".url_categories]` table to override the synthesis. The publish-time gate correctly caught it and rejected the row — but the bug was that plan-time emitted a known-broken URL in the first place.

The fix:

1. Source the category / detail URLs from the existing `[sites."<host>".url_categories]` config table.
2. Omit the link entirely when config has no entry for that host.
3. Emit `category_link_skipped_no_config` via `plan_logger.recon(...)` (always-on signal — see `recon-log-level-for-always-on-signals-2026-05-15.md`) so the operator sees the downgrade without raising `--log-level`.
4. Update the link-density paragraph generator to read from the same config table; remove any hardcoded `+1` count for the now-conditional link.

## Prevention

1. **Code-review smell**: `something_url = base_url + "/literal"`. Ask: is `literal` an SEO guess, or a real path verified to exist on every target site? If a guess, don't ship it without config sourcing or runtime verification.
2. **Required-flag synthesized URLs are a footgun**. If you must synthesize, mark `required: false` so the publish-time gate logs but doesn't fail the row. Better: don't synthesize, just configure.
3. **Thread the config kwarg through helper functions**, don't copy-paste constants. `plan-backlinks` and any downstream link-quota / link-density code must read from the same config table.
4. **When removing a "ghost" URL synthesis**, grep for any quota / count code that hardcoded the same path or mode-specific count. Update both at once or accept silently-under-dense output.
5. **Always-on downgrade signal**: when the planner skips a previously-synthesized link due to missing config, emit a RECON event (not INFO) so it's visible at default log level. Operators need to know they need to populate `[sites."<host>".url_categories]` if they want the link back.

## Related Issues

- `docs/solutions/best-practices/recon-log-level-for-always-on-signals-2026-05-15.md` — the always-on log channel used for the `category_link_skipped_no_config` downgrade event.
- `docs/solutions/best-practices/no-runtime-llm-2026-05-15.md` — the no-LLM constraint that historically led to "synthesize URLs from heuristics" instead of "ask an LLM" — but synthesis without verification is the worst-of-both.
- `docs/solutions/logic-errors/save-config-write-paths-bypass-preservation-2026-05-15.md` — the narrow-merge helper for `[sites."<host>".url_categories]` lives alongside this lesson; together they form the operator-facing config story for per-host URL data.
- Provenance: `feedback_plan-time-url-hallucination.md` (auto memory [claude], first encountered 2026-05-14).
