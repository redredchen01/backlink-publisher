---
date: 2026-05-18
topic: codebase-hygiene-and-extension-readiness
---

# Codebase Hygiene + Extension Readiness

## Problem Frame

After 7+ days of parallel unit work, the workspace root contains 10 directories — 1 main checkout + 9 `bp-*` worktrees + a stale `core/` monorepo experiment + a root `README.md` describing a `core/` + `packages/` monorepo that does not exist. The owner perceives the codebase as fragmented and worries that **future extension** (specifically, adding new publishing platforms like WordPress, Substack, Tumblr) will be difficult.

The picture is more nuanced than a pure documentation problem:

1. The `bp-*` directories are **git worktrees of the same repo**, not separate modules. Several are stale (merged PRs #49/#47) and can be removed today. Worktree sprawl is a **recurring** failure mode (parallel agent sessions create them faster than they're cleaned up — memory `[Worktree Concurrent Switching]` and `[Multi-agent turf-check]` document past incidents). One-shot cleanup is necessary but not sufficient.
2. The publishing layer has a **Publisher ABC + table-driven registry** (`src/backlink_publisher/publishing/registry.py`, landed in Plan 2026-05-18-001 Unit 7) — but **the registry only generalises dispatch**. Adversarial review of this brainstorm verified with `grep` that the CLI entry points still hardcode platform names in three places: `publish_backlinks.py:387` and `plan_backlinks.py:1488` (`argparse choices=["blogger","medium"]`), `publish_backlinks.py:13` (`_MEDIUM_ADAPTERS` set + per-platform throttle branches at lines 119/253/273/326/759), and the literal error string `"Supported platforms: blogger, medium"` at line 539. `validate_backlinks.py:202` has `if platform == "linkedin":`. There is **no Telegraph adapter today** — `telegraph_node.py` is a Markdown→Node converter, not a Publisher subclass.

The real friction is therefore both architectural (one targeted decoupling) and hygiene:

- The owner does not feel confident the registry pattern is enough — and they're partly right. Adding WordPress today forces edits to two CLI files plus `validate_backlinks.py`. The doc-first version of this plan would have shipped a walkthrough whose first user discovered the hardcoded `choices` list and lost the confidence the walkthrough was supposed to build.
- Stale worktrees + a stale `core/` + a wrong README create noise that masks the working architecture, and parallel-agent worktree creation will reaccumulate the mess unless cleanup is automated.
- No published walkthrough exists for "how to add a new adapter end-to-end" (CLI choices source, config schema, optional deps, test fixture, registry registration).
- The root `README.md` lives in a workspace directory that is **not a git repo** (`git rev-parse --is-inside-work-tree` → fatal). Editing it produces an unversioned, no-PR-review artifact that will drift again. The fix is relocation into the actual repo, not in-place rewrite.

This brainstorm scopes the **minimum work that makes extension actually true**: surgical CLI de-coupling so the registry's promise is real, a discoverable walkthrough proving it, hygiene cleanup, and one piece of automation to keep worktree state honest going forward.

It deliberately does NOT propose:
- Splitting into a `core/` + `packages/` monorepo (overkill for current adapter dep weight; revisit if a real adapter triggers a resolver conflict — see R5).
- Replacing Jinja2 webui with a JS frontend (no concrete GUI pain reported; `webui_app/` is 2,343 LOC and already routes-by-feature).
- Decomposing the CLI monoliths broadly (`plan_backlinks.py` 1744 LOC, `publish_backlinks.py` 840 LOC). The F7 SLOC-ceiling plan installs the ratchet; surgical extractions F2/F3/F5 are separate. R9 in this plan touches only the platform-coupling surfaces (argparse choices, the `_MEDIUM_ADAPTERS` branches, `if platform == "linkedin"`) — it is **scoped CLI decoupling**, not monolith decomposition.

## Current vs. Target Workspace

```
BEFORE (verified via `git worktree list`, 9 bp-* worktrees + main)
0511_backlink publisher/
├── backlink-publisher/   ← main worktree (docs/bp-sweep-plan)
├── bp-events-u1/         ← stale (squash-merged as 08d0b7e via PR #49)
├── bp-events-u6/         ← stale (squash-merged as a8345ef via PR #47)
├── bp-f7-monolith/       ← active (feat/monolith-sloc-ceiling)
├── bp-footprint-gate/    ← active (feat/footprint-regression-gate)
├── bp-ko-html/           ← active (feat/ko-language-and-html-input)
├── bp-local-unit2/       ← rehearsal (local/telegraph-unit2-staged)
├── bp-local-unit4/       ← rehearsal (local/telegraph-unit4-staged)
├── bp-local-unit5/       ← rehearsal (local/telegraph-unit5-staged)
├── bp-local-unit6/       ← rehearsal (local/telegraph-unit6-staged)
├── core/                 ← stale monorepo experiment
└── README.md             ← wrong (describes nonexistent monorepo)

AFTER (post-R1/R2/R3)
0511_backlink publisher/
├── backlink-publisher/   ← main worktree (unchanged)
├── bp-f7-monolith/       ← active
├── bp-footprint-gate/    ← active
├── bp-ko-html/           ← active
├── bp-local-unit{2,4,5,6}/  ← keep iff per-worktree triage says rehearsal still in flight
└── README.md             ← rewritten: accurate single-repo + worktree-convention framing
```

## Requirements

Each requirement is tagged **[P0]** (load-bearing for the outcome), **[P1]** (visible-noise reduction), or **[P2]** (per-judgment cleanup). Planning should unit P0s first.

**Extension Readiness (architectural)**

- **R9 [P0]**. Surgically de-couple the CLI from hardcoded platform names so the registry's "add a platform" promise is reachable from the CLI without monolith decomposition. Specifically: (a) replace `argparse choices=["blogger","medium"]` at `publish_backlinks.py:387` and `plan_backlinks.py:1488` with a call into `publishing.registry` (e.g. `registered_platforms()`) so the choice list is registry-driven; (b) replace the literal error string `"Supported platforms: blogger, medium"` at `publish_backlinks.py:539` with the same registry call; (c) extract the "30 s wait after medium publish" and the medium-throttle resume logic (`_MEDIUM_ADAPTERS` branches at `publish_backlinks.py:13,119,253,273,326,759`) into adapter-declared metadata so the CLI loop honors per-adapter throttle without hardcoded `if adapter in _MEDIUM_ADAPTERS` branches; (d) remove or migrate the `if platform == "linkedin":` branch at `validate_backlinks.py:202` and the symmetric one at `publish_backlinks.py:536`. As an acceptance proof: a test-scoped `FakeAdapter(Publisher)` fixture-registration makes the platform CLI-invocable with no CLI-file edit.

**Documentation Truth**

- **R5 [P0]**. Add a new section to `backlink-publisher/AGENTS.md` titled "Adding a new publisher adapter". Document the exact end-to-end steps a contributor takes today (post-R9), including the full surface area: subclass `Publisher`, implement `publish()` returning `AdapterResult`, optionally override `available()` for env gates, set `post_publish_delay_seconds` if throttling is needed; register via `from backlink_publisher.publishing.registry import register; register("<platform>", NewAdapterCls)` in `src/backlink_publisher/publishing/adapters/__init__.py` (import-side-effect convention); add config dataclass in `config/loader.py` and section key in `_SAVE_CONFIG_KNOWN_ROOTS` if platform needs auth; add optional dependency under `[project.optional-dependencies].<platform>` if needed (resolver-conflict escalation path: package-split discussion as separate plan); add a fixture-backed test under `tests/`. Link the section from `backlink-publisher/README.md`.
- **R6 [P0]**. The AGENTS.md walkthrough must **cite** a real existing adapter (Blogger API recommended — simplest auth surface) as the concrete reference at each step. "Cite" means quote-and-link — no new or refactored adapter required.

**Workspace Cleanup**

- **R1 [P1]**. Remove the two worktrees whose feature branches have already squash-merged into `main`: `bp-events-u1` (PR #49 → squash commit `08d0b7e`) and `bp-events-u6` (PR #47 → squash commit `a8345ef`). Verify via `gh pr view <num> --json state,mergeCommit`. Do **not** use `git merge-base --is-ancestor <worktree HEAD>` — the worktree tip is the pre-squash branch tip and will not be an ancestor of the squash commit on `main`.
- **R3 [P1]**. Delete the root `core/` directory. It is an abandoned `backlink-publisher-core` v0.2.0 monorepo experiment whose `src/` mirrors the live `backlink-publisher/src/` and whose paired `packages/` was never created. Confirm no live tooling, dev aliases, or shell wrappers read from `../core/src` before deletion. (CI workflow already verified clean: `.github/workflows/ci.yml` has zero `core/` references.)
- **R4 [P1]**. **Relocate** the canonical project README into `backlink-publisher/README.md` (where it lives under git). Replace the workspace root `README.md` with a one-line pointer or delete it. Rationale: the workspace root is not a git repo (verified — `git rev-parse --is-inside-work-tree` returns fatal); editing the in-place README produces an unversioned, no-PR-review artifact that will drift again.
- **R2 [P2]**. Triage the four `bp-local-unit{2,4,5,6}` telegraph rehearsal worktrees as four separate per-worktree decisions. For each worktree run this decision tree: (a) `git status` — if dirty, do **not** remove; either stash with a descriptive message and push the stash ref, or commit to a `wip/` branch first; (b) `git branch --contains <worktree HEAD>` — confirm the branch tip is preserved somewhere reachable; (c) only when both (a) is clean and (b) confirms preservation, run `git worktree remove`. If ambiguous, keep and surface to the owner.

**Workflow Sustainability**

- **R10 [P2]**. Add post-merge worktree auto-cleanup so worktree sprawl does not reaccumulate after R1/R2. Two pieces: (a) extend the `/ship` skill (or add a post-PR-merge hook) to run `git worktree remove <current-worktree-dir>` after the branch's PR is verified merged — guarded by the same dirty-state / branch-preservation checks as R2; (b) add a small `scripts/prune-stale-worktrees.sh` helper that lists worktrees whose HEAD is reachable from `origin/main` and prompts before removal, runnable on demand or via a weekly cron.

## Success Criteria

**Objective (acceptance gates):**

- Workspace root contains the main checkout + only active worktrees + a one-line pointer (or no) root README. No `core/`. No worktree for an already-merged branch.
- Canonical README lives at `backlink-publisher/README.md` (versioned, PR-reviewable) and explains the `bp-*` worktree convention concisely.
- After R9, `grep -E 'choices=\["blogger","medium"\]|_MEDIUM_ADAPTERS|Supported platforms: blogger, medium|platform == "linkedin"' src/backlink_publisher/cli/` returns zero matches. A FakeAdapter test-fixture registration is invocable via the CLI without any CLI-file edit.
- A contributor (human or agent) reading `AGENTS.md` can add a hypothetical "Substack" adapter end-to-end.
- R10's auto-cleanup runs at least once successfully on the next PR merged after this plan lands.

**Outcome (not an acceptance gate):**

- Owner sentiment shift: "fragmented and hard to extend" → "I know exactly what to do when WordPress / Substack lands on the roadmap." Validated by the owner pairing through the AGENTS.md walkthrough once on a short call after landing.

## Scope Boundaries

**In scope:** worktree removal (R1, R2); `core/` removal (R3); README relocation into the repo (R4); `AGENTS.md` adapter walkthrough including the optional-deps convention as one sub-step of R5 (R5, R6); surgical CLI de-coupling of the three named hardcoding surfaces (R9); post-merge worktree auto-cleanup (R10); light docstring refresh in `src/backlink_publisher/publishing/registry.py` so it matches the AGENTS.md walkthrough.

**Out of scope, never (this plan):**
- Splitting `src/backlink_publisher/` into a `core/` + `packages/` monorepo.
- Broad decomposition of `plan_backlinks.py` (1744 LOC) or `publish_backlinks.py` (840 LOC). R9 touches only the platform-coupling lines; F7 plan owns the SLOC ratchet.
- Replacing Jinja2 templates with React/Vue.
- Adding a real new platform adapter (WordPress, Substack, Telegraph, etc).
- Migrating existing Blogger / Medium / Telegraph dependencies into extras.
- Running a `pip install --dry-run` spike against WordPress / Substack libraries. R5 documents the escalation path.

## Key Decisions

- **Decision**: Both documentation **and** scoped CLI de-coupling (R9) are load-bearing. **Rationale**: Initial framing assumed the registry alone solved extension. Adversarial review proved otherwise via `grep` — three concrete CLI hardcoding surfaces would force any new-adapter PR to edit the same monoliths the doc punts to F7. Without R9, the AGENTS.md walkthrough would be paper-true and code-false.
- **Decision**: R9 is **surgical**, not monolith decomposition. **Rationale**: Touching only the platform-coupling lines is a small, falsifiable change. Broad refactoring of `plan_backlinks.py` / `publish_backlinks.py` stays with F7.
- **Decision**: Keep one Python distribution, do not split into `core/` + `packages/`. **Rationale**: Current adapters all have light, common deps. Monorepo split adds overhead with no install-time benefit. Re-evaluate iff a real future adapter triggers a resolver conflict.
- **Decision**: Optional-deps convention is stated **inside the R5 walkthrough**, not as a standalone requirement. **Rationale**: A standalone requirement for a convention with no shipping artifact is framework-ahead-of-need.
- **Decision**: Relocate the canonical README into `backlink-publisher/README.md`. **Rationale**: Workspace root is not a git repo (verified).
- **Decision**: Add R10 post-merge auto-cleanup. **Rationale**: Worktree sprawl is a recurring failure mode (memory `[Worktree Concurrent Switching]`, `[Multi-agent turf-check]`).

## Dependencies / Assumptions

- The Publisher ABC + registry pattern in `src/backlink_publisher/publishing/registry.py` is the intended long-term contract for adapters. R9 closes the gap between this contract and the CLI surface.
- CLI hardcoding surfaces enumerated and verified via grep (2026-05-18): `publish_backlinks.py:13` (`_MEDIUM_ADAPTERS`), `:119,253,273,326,759` (per-platform branches), `:387` (argparse choices), `:536-539` (platform=="linkedin" + literal error string), `plan_backlinks.py:1488` (argparse choices), `validate_backlinks.py:202` (platform=="linkedin"). R9 targets exactly this set, plus the missed `schema.py:26 SUPPORTED_PLATFORMS` surface (added during planning).
- Workspace root is not a git repo (verified: `git rev-parse --is-inside-work-tree` returns fatal). R4 relocation is the correct response.
- CI workflow has zero `core/` references (verified). R3 only needs to grep dev aliases / shell wrappers, not edit CI.
- PRs #49 and #47 squash-merged as commits `08d0b7e` and `a8345ef` respectively; worktree HEADs `e23ea1f` and `5ad89d8` are pre-squash branch tips and will NOT pass `git merge-base --is-ancestor` against `origin/main`. R1 uses `gh pr view --json state,mergeCommit` instead.
- `bp-local-unit{2,4,5,6}` worktrees show uncommitted modifications. R2's decision tree includes a dirty-state guard.
- R9 coordinates with the in-flight F7 plan via memory `[Multi-agent turf-check]` (pre-flight check), not by enforced ordering.

## Outstanding Questions

### Resolve Before Planning
(none — owner has decided direction and the requirements above are concrete.)

### Deferred to Planning
- [Affects R9][Technical] Are the `if platform == "linkedin":` branches live (user-facing rejection contract) or dead? Planning Phase A investigation determines.
- [Affects R9][Technical] What contract for adapter throttle metadata — classmethod on Publisher, AdapterResult-carries-delay, or dispatch returns class? Planning decides.
- [Affects R9][Coordination] F7 monolith plan is in-flight in `bp-f7-monolith` worktree. Planning coordinates via pre-flight turf-check.
- [Affects R2][User decision] For each of `bp-local-unit2/4/5/6`, is the branch still being iterated on? Planning runs the decision tree.
- [Affects R3][Technical] Any active scripts / dev aliases that import from `../core/src`? Planning greps before deletion.
- [Affects R5, R6][Technical] AGENTS.md walkthrough location — inline or extracted file?
- [Affects R5][Technical] Does `publishing/registry.py` docstring cover the import-side-effect registration pattern and extras escalation?
- [Affects R10][Technical] Where does the post-merge hook live — extending `/ship`, git post-merge hook, or `gh pr merge` wrapper?
- [Affects R10][User decision] Weekly prune script opt-in via cron, or fire-on-demand only?

## Next Steps

→ `/ce:plan` for structured implementation planning
