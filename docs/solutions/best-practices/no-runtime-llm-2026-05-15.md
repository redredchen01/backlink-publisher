---
title: "Hard project rule: no LLM at runtime — LLM is a development-time tool only"
date: 2026-05-15
category: best-practices
module: backlink-publisher / project-policy
problem_type: best_practice
component: project_policy
severity: high
applies_when:
  - "Designing any new feature for the publish / plan / validate / generation path"
  - "Reviewing brainstorms or ideation docs that propose RAG, LLM-generated body content, prompt design, or LLM-PR loops"
  - "Evaluating an existing optional LLM dependency (e.g., `llm_anchor_provider`) for promotion to a default code path"
tags:
  - project-policy
  - llm-free
  - runtime-constraint
  - architecture-rule
  - hard-constraint
---

# No LLM at runtime — `backlink-publisher` is LLM-free in shipped code

## Guidance

**Backlink-Publisher's runtime / installed product does not call any LLM.** No publish path, plan path, validate path, anchor-generation path, or content-generation path may depend on an external LLM call. This is a **hard constraint** stated by the project owner — not a preference, not an aspiration, not a budget concern.

LLM use is permitted **only** during development, when an AI agent (e.g. Claude during a `ce:work` session) is helping draft selector candidates, generate test fixtures, or debug. Anything authored by an LLM during development must ship as static code/config in the repo — never as a runtime dependency.

## When to Apply

- **Brainstorm/ideation triage**: any idea whose core value depends on LLM at runtime — RAG-grounded body generation, "LLM selector self-healing", LLM-driven anchor diversification, prompt-tuned templates loaded at publish time, "LLM cost ceiling" trade-off framing — should be marked ❌ at the ideation stage and either redesigned LLM-free or rejected.
- **Plan review**: if a plan proposes adding an LLM call inside any module under `src/backlink_publisher/`, surface this as a P0 blocker. The fix is upstream (find the LLM-free shape) not downstream (add a feature flag).
- **Code review**: existing optional providers (e.g. `OpenAICompatibleProvider`, `llm_anchor_provider`) may continue to exist as opt-in side paths. They MUST NOT become a default. New code MUST NOT make them load-bearing for any feature that ships.
- **Documentation**: when writing operator-facing docs, do not list LLM API keys as required environment variables. The project must remain installable and runnable without any LLM credential.

## Why This Works

The constraint is a project-design choice that cascades to several downstream invariants:

- **Cost determinism**: cron runs cannot be priced out by LLM API bills. The user installs the tool and runs it; no metered third party stands between input and output.
- **Privacy / data sovereignty**: the operator's content (which targets they are linking to, which articles they are publishing, which anchor pools they have curated) never leaves the local machine — there is no LLM provider in the trust boundary.
- **Reproducibility**: the publish pipeline is deterministic given the same inputs; an LLM call would inject non-determinism that breaks `--replay`, breaks property tests, breaks audit reproducibility.
- **Footprint resistance**: AI-generated body content is identifiable and increasingly penalized. The project's whole anti-footprint posture (anchor diversification, schema variation) presumes the body content itself was author-curated, not LLM-generated.

Designing LLM-free from the start produces structurally different solutions than designing LLM-first and removing the LLM later. The constraint shapes architecture (sliding-window pool sizing for anchor selection, deterministic templated content generation, config-driven URL synthesis) — see related learnings.

## Authorized Exceptions

The owner may authorize a specific **opt-in, development/operator-time tool** that
calls an LLM, provided it preserves every shipped-product invariant above. Such a
tool is permitted only under all of these guardrails:

- It is an explicitly operator-invoked verb — never on any default/cron/`--replay`
  path, never imported by publish/plan/validate.
- The product remains installable and runnable with no LLM credential; the LLM
  key is never a required env var. Absent config → the verb errors clearly, the
  rest of the tool is unaffected.
- Its output is a **human-reviewed artifact**, not auto-published content. A human
  curates before anything reaches a publish path (preserves footprint resistance).
- It reuses the existing opt-in OpenAI-compatible primitives + allowlist + redaction;
  it does not make the LLM load-bearing for any *shipped pipeline* feature.

**Authorized exceptions:**

- 2026-05-27 — `generate-backlink-text` CLI verb (origin:
  `docs/brainstorms/2026-05-27-llm-backlink-text-generation-requirements.md`,
  plan: `docs/plans/2026-05-27-006-feat-generate-backlink-text-plan.md`). Owner
  authorized as an opt-in, human-reviewed content-drafting tool decoupled from the
  shipped pipeline. Do not re-flag this verb as a P0 no-runtime-LLM violation.

## Related Issues

- `feedback_llm-free-pool-sizing.md` (auto memory [claude], first encountered 2026-05-13) — concrete pool-sizing formula for sliding-window selectors that the LLM-free constraint forced into being.
- `feedback_plan-time-url-hallucination.md` (auto memory [claude], first encountered 2026-05-14) — sibling reasoning: "synthesize URLs from heuristics" arose because LLM URL guessing was off the table; but synthesis without verification is the worst-of-both — see `plan-time-url-validation-prevents-publish-404-2026-05-15.md`.
- Provenance: `feedback_no-runtime-llm.md` (auto memory [claude], first encountered 2026-05-14).
