---
date: 2026-05-15
topic: lessons-kit-curation
---

# Lessons Kit: Curate 7 MEMORY Feedback Entries → docs/solutions/

## Problem Frame

27+ project-specific feedback memories live only at `~/.claude/projects/.../memory/feedback_*.md` — invisible to PRs, new contributors, ce:review personas, learnings-researcher agents, and any future agent. Some recur across multiple sessions (e.g. "tests can lock-in bugs" appeared 2x in one week). The project already has `docs/solutions/` with 5 entries and a working frontmatter convention (`title / date / category / module / problem_type / component / severity / applies_when[] / tags[]`), so this is **promotion**, not greenfield infrastructure.

Goal: turn the highest-leverage private memories into a queryable institutional asset that compounding agents and humans both consume — without bloating signal/noise or building infrastructure no one needs.

## Migration Map

| # | MEMORY entry (private) | Target docs/solutions category | Rationale for promotion |
|---|---|---|---|
| 1 | `feedback_config-save-overwrite-pattern` + `feedback_narrow-toml-merge-bypasses-save_config` (combined) | `logic-errors/` | Recurring class of silent data loss; structural fix in PR #12 + workaround pattern from a later incident |
| 2 | `feedback_no-runtime-llm` | `best-practices/` | Hard architectural rule every contributor / agent must respect |
| 3 | `feedback_standalone-page-vs-retrofit` | `best-practices/` | webui.py architectural rule; recurring relevance for any new UI surface |
| 4 | `feedback_test-locks-in-bug` | `test-failures/` | 2× recurrence within one week; negative-assertion anti-pattern |
| 5 | `feedback_floating-point-tiebreak` | `logic-errors/` | Anchor scheduler ordering bug; affects production behavior |
| 6 | `feedback_recon-level-for-always-on-signals` | `best-practices/` | Operator-visible signal pattern; cross-cuts logger usage |
| 7 | `feedback_plan-time-url-hallucination` | `best-practices/` | Real publish-gate failure source; plan-time validation rule |

**Excluded from migration**: 27 total MEMORY entries − 7 promoted = 20 remain in MEMORY only. The 9 explicitly listed below are the most clearly out-of-scope (workflow/git tooling, not project code); the remaining 11 (`api-idempotency-lesson`, `python-mock-datetime-patterns`, `jinja2-banner-text-collision`, `macos-adapter-test-isolation`, `publish-tests-autouse-verify-mock`, `recon` family adjacent entries, `ssrf-port-into-fetcher`, `dogfood-diagnostic-on-self`, `exit-code-pre-flight-grep`, `autouse-mock-with-marker-opt-out`, `llm-free-pool-sizing`) are project-touching but lower-priority — they may be promoted later via the dual-track path (R4) when recurrence or contributor pain warrants.

**Explicitly excluded — workflow/git only** (not project code; never promote): `force-push-hook-workaround`, `force-push-amend-blocked`, `gh-pr-head-swap-limitation`, `plan-duplicate-sub-blocks`, `cereview-finds-latent-bugs`, `measurement-gated-merge`, `brainstorm-prompt-as-desired-state`, `hypothesis-dev-dep-ci-trap`, `plan-vs-code-drift`.

## Requirements

**Pre-flight (blocks R1)**
- R0. **Validate the indexing assumption before writing any new files.** Pick one existing `docs/solutions/` entry (recommend `inverted-negative-assertion-...` since it has high specificity), dispatch `learnings-researcher` against the trigger topic that entry covers, and confirm: (a) the agent returns the entry, (b) what frontmatter fields it actually matches on. If the agent does not surface the existing entry, this entire plan changes shape — the right fix becomes "configure the agent" not "migrate files". Pre-flight cost: ~30 minutes. Do not proceed to R1 if pre-flight fails.

**Migration (one-shot)**
- R1. Promote the 7 source entries into `backlink-publisher/docs/solutions/<category>/<slug>-2026-05-15.md`, one file per source entry (6 destination files after R3 merge), **mirroring the schema of the same-category sibling file**:
  - `test-failures/` → follow `inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` (uses `symptoms[]`, `root_cause`, `resolution_type`, `related_components[]`)
  - `logic-errors/` → follow `language-matches-always-true-no-op-gate-2026-05-14.md` (same expanded shape)
  - `best-practices/` → follow `document-review-catches-runtime-errors-at-plan-time-2026-05-14.md` (uses `applies_when[]`)
  - Resolve the existing `category` style inconsistency at planning time (existing siblings split between path-style `docs/solutions/best-practices` and slug-style `best-practices` / `test-failures`) and pick one across the new 6 entries; do not entrench the drift.
- R2. Each promoted entry must contain three sections at minimum: **Context** (when/where it bit), **Guidance** (the rule, with concrete code/path examples), **Provenance** (link back to source MEMORY filename + first-encounter date for traceability). **Provenance format is locked**: `Provenance: feedback_<slug>.md (auto memory [claude], first encountered <YYYY-MM-DD>)` — matching the precedent already set by `inverted-negative-assertion-...md`. Absolute paths, `~/.claude` prefixes, and project-slug-encoded directory names are prohibited.
- R2.1. **Frontmatter is regenerated from scratch** using only the docs/solutions schema. Source MEMORY frontmatter fields (`originSessionId`, `node_type`, `name`, etc.) MUST NOT appear in any migrated file. Verify with `rg -n 'originSessionId|node_type: memory' docs/solutions/` returning empty.
- R2.2. **Lesson bodies must neutralize operator-specific identifiers** before publication: real target domains → `https://example.com`; concrete run IDs (timestamped IDs of the form `YYYYMMDDTHHMMSS-<hash>`) → `<run-id>`; real config keys containing customer/target hostnames (e.g. `[sites."<real-host>".url_categories]`) → `[sites."<target-host>".url_categories]`; operator email addresses → `<operator>@example.com`. The teaching value is the pattern, not the customer. (Concrete tokens live in the gitignored private-tokens file referenced in Success Criteria; do not inline them here.)
- R2.3. **Pre-commit sanitization checklist** — every promoted file passes all 5 checks before commit: (1) no absolute paths or home-dir references, (2) no session UUIDs or origin-side metadata, (3) no real third-party domains (replaced with `example.com`), (4) no verbatim user quotes that identify the user, (5) no run IDs or specific PR-date pairs that map a fix-window. The implementer lists each of the 6 files against the 5-item checklist in the PR description.
- R3. The two `save_config` memories (config-save-overwrite + narrow-toml-merge) collapse into a single family entry titled around "TOML write paths that bypass save_config" — both incidents appear as scenarios within the one document. The new entry must **cross-link** to the existing `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` (which is the test-side counterpart) and stay focused on the **write-path** (save_config + narrow-toml-merge), not restate the test-inversion story already covered there. When R2.2 is applied, the OAuth-refresh trigger detail and "PR #12 / 2026-05-14" specifics generalize to "partial serializers silently drop unmanaged config sections" so the public lesson does not map a recent fix window.

**Sustainability (ongoing)**
- R4. Future high-value lessons follow a **dual-track** capture: Claude auto-memory continues writing private `feedback_*.md` for fast capture; promotion to `docs/solutions/` happens manually via `/ce:compound` when a lesson proves recurring or warrants project-wide visibility. No automation, no hook into Claude memory.
- R5. Document the dual-track convention briefly in `backlink-publisher/AGENTS.md` (or create the file if absent) so contributors and agents know the promotion path exists. Single short section (~10 lines), not a treatise. **Must include three things**:
  1. The dual-track path (auto-memory → manual `/ce:compound` → docs/solutions/)
  2. The sanitization line so future promotions don't re-litigate this review's findings: *"Promotion is rewriting, not copy-paste. Strip session UUIDs, real domains, absolute paths, and user-identifying quotes; teach the pattern, not the incident."*
  3. **Next curation review date** (recommend 2026-08-15 — quarterly cadence). The honest forcing function isn't a hook, it's an explicit calendar item: scan `~/.claude/projects/.../memory/feedback_*` since last review and `/ce:compound` anything recurring or high-leverage. Update this date when each review completes.

**Cleanup (drift)**
- R6. The outer `docs/` directory contains a parallel doc tree (`brainstorms/`, `ideation/`, `plans/`) plus loose files (e.g. `MEDIUM_OAUTH_SETUP.md`) that drift from the canonical `backlink-publisher/docs/`. The Phase-1 ideation doc misplacement (`docs/ideation/2026-05-15-open-ideation.md`) is one known instance; expect more. **During planning, audit outer `docs/{brainstorms,ideation,plans}` plus loose files in one pass and produce a per-file move/keep/delete list.** Apply the resulting moves as part of this migration so nested-docs drift doesn't grow.

**Scope Constraints**
- R7. No new code in `src/backlink_publisher/`. No new CLI subcommand. No new hooks. The deliverable is markdown writes + file moves from the R6 audit + an AGENTS.md note.

## Success Criteria

- 6 new entries land under `backlink-publisher/docs/solutions/{best-practices,logic-errors,test-failures}/` with frontmatter matching the same-category sibling schema (per R1)
- `rg -n '\.claude/projects' backlink-publisher/docs/solutions/` returns empty (Provenance lockdown gate per R2)
- `rg -n 'originSessionId|node_type: memory' backlink-publisher/docs/solutions/` returns empty (frontmatter-regenerated gate per R2.1)
- `rg -nF -f <private-token-file> backlink-publisher/docs/solutions/` returns empty (operator-identifier neutralization gate per R2.2). The token file is `~/.local/share/backlink-publisher/private-tokens.txt`, gitignored; do NOT inline the literal tokens (real domains, operator email) in this committed requirements doc — the methodology is public, the tokens are not.
- The 5-item sanitization checklist (R2.3) is filled out for each of the 6 files in the PR description
- The R6 audit produces a per-file move/keep/delete list for outer `docs/`; resulting moves are applied; outer `docs/` retains only what genuinely belongs there (or is empty)
- `backlink-publisher/AGENTS.md` mentions the dual-track convention in <15 lines, including the rewriting-vs-copy-paste line from R5
- Original `~/.claude/projects/.../memory/feedback_*.md` files are **left untouched** — promotion is additive; auto-memory remains authoritative for itself

## Scope Boundaries

- **Out**: `bp lessons` CLI (any form — list, grep, filter), feedback-writer hook, automatic cross-linking, PR templates that cite lessons, CI gates that require lesson references, migrating any of the 20 explicitly-excluded MEMORY entries
- **Out**: rewriting / refining the *existing* 5 docs/solutions entries — only adding the 7 new ones
- **Out**: changing `learnings-researcher` agent behavior — relying on it as-is
- **Out**: building `/ce:compound` integration glue — using it as the existing manual command

## Key Decisions

- **Curated 7 (→ 6 after merge), not all 27**: Signal/noise. Promoting workflow-only or git-tooling memories pollutes a project-code knowledge base.
- **No CLI surface**: The `learnings-researcher` agent (assumed to) auto-scan `docs/solutions/`; humans use `rg`. At 5–7 entries a dedicated CLI is over-engineering with ongoing maintenance cost.
- **Pre-flight before migrate (R0)**: Validate the indexing assumption with one existing entry first. If it fails, the right move is "fix the agent" not "migrate files" — this could collapse the entire plan, so spend 30 minutes before committing 6 files of work.
- **Dual-track future capture**: Auto-memory's strength is zero-friction capture; `ce:compound` adds curation cost only when it's worth it. Forcing all lessons through `ce:compound` adds friction at the moment of insight (worst time).
- **Quarterly curation as the explicit forcing function (R5.3)**: Adversarial review correctly noted "humans will manually promote when it matters" is the same behavior that just demonstrably failed for 27 entries. Honest fix: a calendar item, not a hook. Accept the periodic-sprint cost; document the next date so it doesn't get forgotten.
- **Combine the save_config family**: Two incidents, one pattern. One document with two scenarios beats two near-duplicate documents.
- **Fix outer-docs drift opportunistically**: The accidental misplacement is the kind of thing R10 in the ideation doc flagged. Fix it now while we're touching docs anyway.
- **Sanitization is mandatory, not optional (R2.1/R2.2/R2.3)**: Source MEMORY entries contain operator-private content (real target domains, session UUIDs, fix-window-mapping PR/date pairs, user-identifying quotes). Promotion is rewriting, not copy-paste. Three explicit requirements + grep gates + 5-item checklist.
- **Investigate "read MEMORY directly" before committing to file migration (deferred to planning)**: ADV-3 raised a load-bearing alternative — if the agent can be pointed at MEMORY.md, the migration becomes redundant. Check first.

## Dependencies / Assumptions

- `backlink-publisher/docs/solutions/` continues to be the canonical home (verified — already has 5 entries using consistent schema)
- `learnings-researcher` agent is already configured to scan this path (assumption based on observed behavior; smoke test in success criteria validates)
- `~/.claude/projects/.../memory/MEMORY.md` index continues to point to the original feedback files; no changes required there

## Outstanding Questions

### Resolve Before Planning
*(none)*

### Deferred to Planning
- [Affects R0][Needs investigation] **First 30 minutes of planning**: investigate whether `learnings-researcher` could be configured to read `~/.claude/projects/.../memory/MEMORY.md` directly (or a sanitized export). If yes, the entire migration may collapse to a one-shot config change — this is the most leveraged thing to check before committing to file moves. If the answer is "no" (path hardcoded / privacy / no agent config surface), record the reason in the plan so the path-dependency to docs/solutions becomes a deliberate choice, not a default.
- [Affects R1] Resolve the existing `category` style inconsistency in current `docs/solutions/` siblings (path-style `docs/solutions/best-practices` vs slug-style `best-practices` / `test-failures`). Pick one for the new 6 entries; do not entrench drift.
- [Affects R5] Does an `AGENTS.md` already exist anywhere in the project tree? If yes, append; if no, create a minimal one whose only initial content is the dual-track convention + a pointer to README. Check during planning.
- [Affects R6][Needs investigation] Outer `docs/` audit: enumerate every file/dir under outer `docs/` and assign each a verdict (move-to-canonical / keep-at-outer / delete). Output the audit table as part of the plan, not during execution.

## Next Steps
→ `/ce:plan` for structured implementation planning (light plan; mostly file moves + 6 markdown writes + 1 AGENTS.md note)
