---
title: "Editable install in sibling worktrees: per-worktree `.venv` (preferred) vs `PYTHONPATH=src` (quick)"
date: 2026-05-19
category: docs/solutions/developer-experience
module: bp-*/ worktree dev setup
problem_type: developer_experience
component: development_workflow
severity: medium
applies_when:
  - "Running pytest in a `bp-<topic>/` sibling worktree of `backlink-publisher/`"
  - "Working on multiple feature branches concurrently and needing each to test against its own `src/`"
  - "Setting up a new sibling worktree and deciding how to isolate the editable install"
related_components:
  - testing_framework
  - tooling
tags:
  - editable-install
  - pip-install-e
  - worktree
  - pythonpath
  - venv-per-worktree
  - sibling-isolation
---

# Editable install in sibling worktrees: two isolation strategies

## Context

`backlink-publisher` is installed in development mode via `pip install -e ".[dev]"` from the canonical `backlink-publisher/` worktree. `pip install -e` binds the import path to **one tree** — the one the install command was run from. Every `bp-*/` sibling worktree, when running `pytest`, will read from the canonical `backlink-publisher/src/` directory regardless of which sibling's `src/` you actually modified.

This silently produces wrong test results: you edit `bp-velog/src/backlink_publisher/foo.py`, run pytest from `bp-velog/`, and pytest tests the canonical `backlink-publisher/src/backlink_publisher/foo.py` — your edits are invisible.

Two strategies isolate sibling worktrees from the canonical install. Choose based on how long-lived the worktree is.

## Guidance

### Strategy A — `PYTHONPATH=src` (quick, ephemeral siblings)

For short-lived `bp-*/` worktrees (spikes, one-off branches, scratch work):

```bash
cd bp-<topic>/
PYTHONPATH=src pytest tests/
PYTHONPATH=src python -m backlink_publisher.cli.plan_backlinks ...
```

`PYTHONPATH=src` forces Python to find `backlink_publisher/` in the **current worktree's `src/`** before the canonical install. The canonical editable install is shadowed for this command only.

Pros:

- No setup. Works immediately in any sibling.
- No `.venv` to manage.

Cons:

- Easy to forget the prefix. One un-prefixed pytest run silently tests the wrong tree.
- Per-command burden; harder to apply consistently in long-lived sessions.
- IDE / language server integration usually points at the canonical install, so editor diagnostics don't match what pytest tests.

### Strategy B — Per-worktree `.venv` (preferred, long-lived siblings)

For sibling worktrees that will live more than a day:

```bash
cd bp-<topic>/
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# All subsequent commands in this shell use this worktree's editable install
pytest tests/
python -m backlink_publisher.cli.plan_backlinks ...
deactivate    # when done
```

Each worktree has its own Python environment with its own editable install pointing at its own `src/`. Switching worktrees is `cd <worktree>/ && source .venv/bin/activate`.

Pros:

- No per-command prefix to remember.
- IDE/LSP integration is correct (point each editor window at the worktree's `.venv/bin/python`).
- Pytest, mypy, ruff, etc. all read the right source automatically.
- Pip dependency drift between branches is isolated (one branch can update `pyproject.toml` dependencies without breaking siblings).

Cons:

- One-time setup per worktree (~30 seconds for `python -m venv` + `pip install -e .`).
- `.venv` takes ~50MB per worktree. With 20+ siblings, that's ~1GB of `.venv` directories.
- Manual cleanup when sibling is deleted (delete `.venv/` along with the worktree).

### Decision rule

| Sibling lifetime | Strategy |
|------------------|----------|
| <1 day, single feature | `PYTHONPATH=src` |
| ≥1 day, real branch with reviews | per-worktree `.venv` |
| Spike / scratch, will be deleted | `PYTHONPATH=src` |
| Long-lived parallel feature track | per-worktree `.venv` |
| Touching `pyproject.toml` dependencies | per-worktree `.venv` (must, otherwise install state diverges from imported state) |

### Combining: per-worktree `.venv` AS the default, `PYTHONPATH=src` as a fallback

For most multi-day work, the per-worktree `.venv` is worth the 30-second setup. The `PYTHONPATH=src` fallback is useful for ad-hoc one-shot commands or when reviewing a sibling you didn't set up.

## Why This Matters

Without isolation, sibling worktrees give wrong test results silently. This is the worst kind of test failure: the tests pass, but they were running against the wrong code. The agent ships a "passing" PR that broke the test it claimed to add.

Concrete failure modes the isolation prevents:

- Editing `bp-velog/src/...`, running `pytest tests/test_velog.py` → tests pass because they ran against the canonical (un-edited) `src/`.
- Adding a new module in `bp-feat/src/backlink_publisher/new_thing.py`, importing it from `bp-feat/tests/...` → `ImportError` because the canonical `src/` doesn't have it yet.
- Updating dependencies in `bp-newdep/pyproject.toml` → import paths still resolve through canonical `.venv`; new dep is invisible.

The "tests pass but they tested the wrong tree" failure mode has wasted multi-hour debugging sessions. The isolation cost is trivial.

## When to Apply

- Creating any `bp-*/` sibling worktree that will run tests or CLIs.
- When pytest from a sibling produces results that don't match the code you just edited.
- When the IDE/LSP shows different diagnostics than pytest reports — usually because they're reading different trees.
- When introducing new files, modules, or dependencies in a sibling.

Skip when:

- Working only in the canonical `backlink-publisher/` worktree (no isolation needed; it owns the editable install).
- Read-only operations (browsing code, reading tests) — no need to isolate.

## Examples

**Right — per-worktree venv (long-lived):**

```bash
# Set up once
cd bp-banner-u6-ghpages/
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Subsequent sessions
cd bp-banner-u6-ghpages/
source .venv/bin/activate
pytest tests/test_ghpages_adapter.py  # reads bp-banner-u6's src/
```

**Right — PYTHONPATH=src (one-shot):**

```bash
cd bp-quick-spike/
PYTHONPATH=src pytest tests/test_spike.py
PYTHONPATH=src python -c "from backlink_publisher.spike import foo; foo()"
```

**Wrong — bare pytest in sibling:**

```bash
cd bp-velog/
# edited bp-velog/src/backlink_publisher/cli/_bind/recipes/velog.py
pytest tests/test_velog_recipe.py
# → tests pass because they ran against backlink-publisher/src/... (un-edited)
# → ship PR, reviewers find the bug, look bad
```

## Related

- CLAUDE.md → "Sibling worktrees and editable installs" — canonical guidance, points at both strategies.
- `docs/solutions/workflow-issues/multi-agent-turf-check-before-claiming-work-2026-05-20.md` — adjacent: managing concurrent work across siblings.
- `docs/solutions/workflow-issues/foreign-agent-wip-spreads-across-worktrees-2026-05-20.md` — adjacent: WIP attribution across siblings.
- `pip install -e` documentation — the primitive whose one-tree binding makes the isolation necessary.
