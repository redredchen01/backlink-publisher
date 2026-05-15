---
title: "Add new functionality as a sibling page, not a child retrofit, when the host file is monolithic"
date: 2026-05-15
category: best-practices
module: backlink-publisher / webui
problem_type: best_practice
component: ui_architecture
severity: medium
applies_when:
  - "Adding a new form / route / panel to a Flask/Jinja `webui.py` whose existing `HTML` template constant exceeds ~500 lines"
  - "Touching any monolithic template that mixes inline CSS, embedded JS, and a state machine (Rails `application.html.erb`, Django `base.html`, large React `App.tsx`)"
  - "Estimating the diff size and review effort for a planned feature against an existing god-file"
tags:
  - webui
  - jinja2
  - architecture
  - god-file
  - retrofit
  - sibling-page
  - review-friendliness
---

# Sibling page over retrofit — keep new UI surfaces self-contained when the host is monolithic

## Guidance

When adding a new feature to a large monolithic template (e.g. `webui.py`'s ~1400-line `HTML` constant), prefer a **self-contained sibling page** over **retrofitting the existing template**. The first question to ask of any new UI surface is:

> Can this be a sibling page rather than a child of the existing template?

Default to "yes" unless the new feature genuinely needs to share an in-memory state component (nav, auth middleware, global JS state) with the host. Routes get their own template literal at the end of the file; new fixtures get their own autouse scope; failures get a bounded blast radius.

## When to Apply

**Apply when**:

- The host template exceeds ~500 lines.
- Inline CSS, embedded JS, and jinja context are intertwined.
- The new feature can be reached at its own URL (`/sites`, `/scheduler`, `/replay`) rather than embedded in the existing page.
- The existing template has been growing by "append a new route + template literal at the end" rather than "insert into the existing structure" — that pattern signals the project has already adopted sibling-by-default; follow it.

**Exception (use retrofit) when**:

- The new feature must reuse a stateful host component: navigation bar with active-route highlighting, an auth middleware that the host page configures, a global JS state machine the new feature must subscribe to.
- Evaluate retrofit cost vs duplication cost case-by-case; duplicating ~30 lines of nav HTML is fine, duplicating 200 lines of state-machine JS is not.

## Why This Works

The cost of touching a 1400-line template comes from the **invariants the file's size has accumulated** — class names referenced by inline JS, CSS selectors depending on DOM order, jinja context shape baked into multiple panels, JS event listeners attached on DOMContentLoaded. None of those invariants are visible from the diff. Retrofitting forces the implementer to scan the full host file mentally; reviewers must do the same. A 200-line sibling file the reviewer can read in one screen is structurally easier to validate than a 200-line diff distributed across an existing 1400-line file.

Sibling pages also future-proof refactoring. When the team eventually splits `webui.py` into a Flask Blueprints layout, sibling pages are already independent units — they migrate cleanly. Retrofits get tangled with the host's other panels and are migration debt.

## Examples

A recent feature (`/sites` mini-page with 7 inputs, 3 fieldsets, CSRF token, aria-describedby error labeling) had two candidate paths:

- **A — Retrofit the home template**: locate the right insertion point inside the 1400-line `HTML` constant, navigate the inline CSS conflicts, avoid breaking the home JS state machine, avoid triggering unrelated regressions.
- **B — Add a `/sites` mini-page**: self-contained template literal appended at end of `webui.py`, default Bootstrap styling (no inline-CSS matching needed), autouse fixture scoped to the new routes only, blast-radius bounded to `/sites`.

Path B shipped. The PR diff was entirely additive — reviewers explicitly skipped the home-template section because the commit message stated *"does NOT touch the N-line template; reviewable in isolation"*. Total reviewer attention budget needed: minutes. Path A would have required line-by-line inspection of every edited region across the 1400-line host.

## Prevention

1. **At plan time**, before any code, ask the sibling question once per new UI surface. Record the answer in the plan's Key Decisions so reviewers and future contributors know it was considered.
2. **Look at the last 3 PRs that touched the same host file**. If they all appended new routes + templates at the end, the sibling pattern is already in use — follow it. If they all inserted into the host template, evaluate whether to break the pattern.
3. **In the commit message and PR description**, state explicitly when the change is "additive at end of file, host template untouched". Reviewer attention is finite; spend it where the risk is.
4. **Don't share a Jinja banner-string constant across host and sibling templates** — `feedback_jinja2-banner-text-collision.md` (auto memory [claude]) records a concrete case where shared banner text collided across the host page and a new self-contained tester surface, leading to a confusing test failure. If the sibling needs similar UI affordances, copy/paraphrase rather than import.

## Related Issues

- `docs/solutions/ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md` — sibling lesson about `webui.py` specifically; same host file, different bug class.
- Provenance: `feedback_standalone-page-vs-retrofit.md` (auto memory [claude], first encountered 2026-05-14).
