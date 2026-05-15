---
title: "Writing about sanitization recurringly leaks the things being sanitized — final grep gate is mandatory"
date: 2026-05-15
category: best-practices
module: workflow / docs/solutions/ promotion
problem_type: best_practice
component: documentation_workflow
severity: medium
applies_when:
  - "Authoring any plan, brainstorm, or solution doc that describes how to scrub operator-private content"
  - "Writing R2.x-style requirements (token mappings, sanitization rules) that include 'before/after' examples"
  - "Adding a Risks-table entry that demonstrates an anti-pattern by quoting the literal anti-pattern"
  - "Reviewing a doc whose ostensible purpose is to teach sanitization discipline"
tags:
  - sanitization
  - documentation-workflow
  - meta-pattern
  - recurring-leak
  - grep-gate
  - private-tokens
  - self-documentation-hazard
  - ce-compound
---

# Writing about sanitization recurringly leaks the things being sanitized

## Context

Authoring documentation that explains *how* to sanitize operator-private content (real domains, emails, session UUIDs, fix-window dates) creates a recurring leak: the most natural way to teach the rule is to give a before/after example, and the "before" half of that example **is** the literal token the rule says not to commit. The same reviewer who flagged the leak in the requirements doc was the only thing standing between the same leak and PR-merge twice more in the plan and the plan-review cycle.

This entry was written *because* PR #33's brainstorm + plan + plan-review cycle hit the recurrence three times in one session — every time a previous fix was applied, the next written artifact (which referenced the fix) reintroduced the same literal tokens via "for example, `<real-domain>` → `example.com`" or via a Risks-table entry that attempted to demonstrate the anti-pattern by quoting the anti-pattern.

## Guidance

When the doc you're writing is *about* sanitization (rather than about a code module or runtime behavior):

1. **Use placeholders in mapping tables, not literal tokens.** Write `real target domain → example.com`, not `<real-host> → example.com`. The literal tokens live in `~/.local/share/backlink-publisher/private-tokens.txt` (per-operator, gitignored, outside the repo); your committed doc should reference the methodology, not the values.
2. **Don't quote anti-patterns to demonstrate them.** If you need to write "X tends to happen when…", phrase the X in abstract terms or point at the per-operator token file. Never write `rg -niE '<real-token>|<other>'` even as a "bad example" — `rg -nF -f ~/.local/share/backlink-publisher/private-tokens.txt` is the correct shape both as advice and as a Risks-table example.
3. **Run the gate against the doc itself before commit, not just against `docs/solutions/`.** The standard sanitization gate (`rg -nF -f ~/.local/share/backlink-publisher/private-tokens.txt`) must be run against every committed file in the diff — plan, brainstorm, ideation, and the solution entries. The act of writing about the gate does not exempt the document.
4. **In multi-pass review, never trust prior passes' fix coverage.** A prior security-lens review may have applied an auto-fix to one occurrence of a literal token while leaving four more in unrelated paragraphs. The next dispatched security-lens reviewer should re-grep independently and not assume "the previous pass already cleaned this."

## Why This Matters

The recurrence has a specific cognitive shape: when an author is trying to *teach* a sanitization discipline, the most natural pedagogical move is to show a vivid example of the thing-to-not-do. That move recreates the leak the discipline exists to prevent. Without an explicit guard, the doc-author is structurally biased toward demonstrating the very anti-pattern they're forbidding. The guard is mechanical (token file + grep), not vigilance — vigilance failed three times in one session even with the author actively trying to prevent it.

The damage of one leak isn't always recoverable: once a literal operator-private token lands on `git push`, scrubbing it from history requires force-push (which the project's `pre-bash-safety` hook explicitly blocks per [`feedback_force-push-amend-blocked.md`](../../.claude/projects/<project-memory-slug>/memory/feedback_force-push-amend-blocked.md)). Better to never let it land.

## When to Apply

| Apply when... | Skip when... |
|---|---|
| Writing any doc whose primary topic is sanitization, redaction, or scrubbing | Writing docs about runtime behavior of unrelated subsystems |
| Adding a "before/after" example to a sanitization requirement | Adding code examples to a feature plan with no operator-private content |
| Demonstrating an anti-pattern via quoted text | Documenting a successful refactor where the diff itself is the example |
| Reviewing a plan/brainstorm/solution that someone else wrote about sanitization | Reviewing pure code changes |
| Any final pre-commit step on a doc that has touched the `<private-tokens>` topic | Any final pre-commit step on a doc that hasn't touched it |

The mandatory final step is the same regardless: `rg -nF -f ~/.local/share/backlink-publisher/private-tokens.txt <every-file-in-diff>` returns empty before commit, no exceptions.

## Examples

### Example 1: mapping table that leaks vs. mapping table that doesn't

**Leaky** (caught and fixed in PR #33's R2.2):

```
- R2.2. Lesson bodies must neutralize operator-specific identifiers:
  real target domains (e.g. https://<real-host>) → https://example.com;
  concrete run IDs (e.g. <YYYYMMDDTHHMMSS-real-hash>) → <run-id>; ...
```

**Non-leaky** (the version that shipped):

```
- R2.2. Lesson bodies must neutralize operator-specific identifiers:
  real target domains → https://example.com;
  concrete run IDs (timestamped IDs of the form YYYYMMDDTHHMMSS-<hash>) → <run-id>;
  ... (Concrete tokens live in the gitignored private-tokens file referenced
  in Success Criteria; do not inline them here.)
```

The difference: name the *category* of token, not the *value* of the token.

### Example 2: Risks-table entry that leaks vs. one that doesn't

**Leaky** (a recurrence-3 instance from this session — caught immediately by the next pass):

```
| Risk | Mitigation |
| Plan / requirements docs themselves leak the literal grep tokens | Switched
  to `rg -F -f <gitignored-token-file>` ... require a final
  `rg -niE '<real-operator-string>|<other-tokens>'` pass against ANY committed doc |
```

The "require a final ... `<real-operator-string>`" example string IS the token to scrub. Recurrence-3 was caught and fixed by changing the example to:

**Non-leaky**:

```
... require a final `rg -niE -f ~/.local/share/backlink-publisher/private-tokens.txt`
pass against ANY committed doc — plan, brainstorm, or solution — before merging.
```

The non-leaky version uses the same regex shape (`rg -niE`) but supplies the patterns from the gitignored file rather than inlining them.

### Example 3: pre-commit gate as a habit

```bash
# Before every commit on a doc that touches sanitization
rg -nF -f ~/.local/share/backlink-publisher/private-tokens.txt $(git diff --name-only --cached)

# Returns empty (exit 1)? OK to commit.
# Returns hits? Stop. Fix the doc. Re-run. Then commit.
```

This shape is the mandatory final step. It's mechanical, not judgment-based, which is exactly why it works after vigilance fails.

## Related Issues

- `docs/solutions/best-practices/document-review-catches-runtime-errors-at-plan-time-2026-05-14.md` — same security-lens reviewer that catches sanitization leaks also catches runtime-breaking signature errors at plan time. Different problem classes; same mechanism (multi-persona pre-implementation review). Whenever you invoke `document-review` on a plan, the security lens covers both surfaces.
- `docs/solutions/test-failures/negative-assertion-locks-in-bug-2026-05-15.md` — pattern-family sibling: both entries codify a lesson that emerged from **recurrence within one short window** (negative-assertion bug recurred 2× in one week; sanitization leak recurred 3× in one session). The recurrence-as-signal methodology is shared; the audit mechanisms differ (grep for negative assertions vs. grep for private tokens).
- `docs/solutions/logic-errors/save-config-write-paths-bypass-preservation-2026-05-15.md` — the bug whose fix-write-up triggered the sanitization-leak recurrence in this same session. Asymmetric dependency: writing this entry was occasioned by writing that one. If `save_config` had been a less example-heavy lesson, the recurrence would not have surfaced as a codified pattern.
- Provenance: `feedback_self-doc-token-leak-recurrence.md` (auto memory [claude], first encountered 2026-05-15). Companion auto-memory entry: `feedback_lessons-kit-promotion-convention.md` (auto memory [claude]) records the dual-track promotion convention this entry exemplifies.
