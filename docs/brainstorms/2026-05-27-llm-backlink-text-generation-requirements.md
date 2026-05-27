---
date: 2026-05-27
topic: llm-backlink-text-generation
---

# LLM Backlink Text Generation Stage (`generate-backlink-text`)

## Problem Frame

The operator has backlink *candidate* records (a target URL + a desired anchor
text + a placement style) but no quality-controlled way to turn them into
publishable backlink text. Today the only LLM content path is
`generate_article_body()` buried inside the publish pipeline's anchor provider —
it can't be run standalone, doesn't accept arbitrary candidate records, and
produces no reviewable artifact before publishing.

This adds a **standalone content-generation stage**: read candidate records,
ask an OpenAI-compatible LLM to write higher-quality backlink text, validate the
output deterministically, and emit a reviewable JSONL/JSON artifact. It is
content generation only — a human (or a later step) reviews before anything is
published.

Pipeline position (conceptual — this stage does **not** wire into the existing
`seeds → plan → validate → publish` chain):

```
crawl/score candidates → generate-backlink-text → review → publish → verify
```

## Requirements

**CLI Surface**

- R1. New terminal command `generate-backlink-text`, registered in
  `[project.scripts]` and `python -m` runnable, following the repo's
  stdin/stdout-JSONL + stderr-diagnostics + exit-0-on-success convention.
- R2. Flags: `--input/-i` (file; default stdin), `--endpoint`, `--api-key-env`
  (default `BACKLINK_LLM_API_KEY` — the repo's canonical name; `LLM_API_KEY` is
  not read by the reused config), `--model`, `--temperature` (0.4), `--timeout` (60),
  `--retries` (1), `--output-format` (`jsonl|json`, default `jsonl`),
  `--max-input-bytes` (2_000_000), `--max-records` (200), `--dry-run`.
- R3. `--dry-run` emits the constructed prompts only — no API key required, no
  HTTP call made. This is the offline inspection / CI-safe path.

**Input Contract**

- R4. Accept a single JSON object, a JSON array, or JSONL. Each record requires
  `target_url`, `anchor_text`, and `mode`. `target_url` must pass the existing
  `_util.url.validate_https_url` (https-only, non-empty host) at the input
  boundary **before** it is ever embedded as a link (R9) — a non-https scheme
  (`javascript:`/`data:`/`file:`) is rejected, not emitted.
- R4b. An unsupported or unknown `mode` (including `profile`/`bio` while
  deferred) yields a per-record `status: rejected` with
  `rejection_reason: unsupported_mode:<value>` — it never aborts the batch.
- R5. Enforce `--max-input-bytes` and `--max-records` **before any LLM POST**
  (fail-closed reject, not silent truncation) — they bound both memory and
  paid-API cost-amplification.
- R5b. Empty input (zero records / empty file / empty stdin) exits 0 with empty
  output and a stderr summary of `0` — it is not a usage error.

**Generation**

- R6. Reuse the existing `OpenAICompatibleProvider`
  (`publishing/adapters/llm_anchor_provider.py`) and the existing config/env
  surface (`[llm.anchor_provider]`, `BACKLINK_LLM_*`) rather than building a
  parallel HTTP/retry/redaction stack. CLI flags override config.
- R7. Support two modes for the MVP. Generation is **not** thin reuse — the
  provider needs work for both:
  - `article` — 200–400 word SEO body. **Adapt** `generate_article_body`: a
    single-link prompt built from the candidate's `target_url` + `anchor_text`
    (the current method hard-codes "≥2 links" and `anchors[0]/anchors[1]`, which
    conflicts with R9/R10 and must change).
  - `comment` — short, natural, imperfect text (forum/blog-comment style) via a
    **new** provider method (no comment generator exists today).
  `profile`/`bio` is a deliberate later addition (see Scope Boundaries).
- R8. `--retries` applies **only to transient transport failures** (network /
  429 / 5xx / timeout). Deterministic validation failures (R9–R12) are **not**
  retried — re-calling the same prompt would reproduce the same defect — they go
  straight to `rejected`. Note the reused provider already wraps calls in
  `retry_transient_call`; planning must define whether `--retries` is in addition
  to or instead of that internal retry, and how non-transient `DependencyError`s
  count toward the per-record budget.

**Deterministic Validation** (no LLM call; reproducible)

- R9. The output must embed a link pointing at `target_url` (Markdown or HTML)
  whose **link text contains `anchor_text`**, matched case- and
  whitespace-normalized (not byte-exact — LLMs case-fold/pluralize). The anchor
  must appear in the `<a>` text specifically, not merely anywhere in the body.
- R10. Length must fall within the per-mode bounds (short cap for `comment`,
  200–400 words for `article`); out-of-bounds is rejected.
- R11. Two distinct boundaries — do not conflate them:
  - **Input sanitization (before prompt construction):** every untrusted field
    (`anchor_text`, `target_url`, `mode`) passes through the provider's
    `_sanitize_input` equivalent (length cap + control/bidi strip +
    XML-attribute escaping) and is wrapped in the `<input>` data-not-instructions
    block. Output filters do **not** cover this boundary; an unescaped
    `anchor_text` could break out of the data block before the model sees it.
  - **Output filtering (after generation):** reject control characters,
    bidi-override characters, extra/unexpected links (the model must not inject
    *other* domains beyond `target_url`), and obvious LLM refusal/jailbreak
    phrasing. Reuse the existing anchor output filters where they apply.
- R12. Output language should match the requested target language. The target
  language comes from an optional input `language` field (else a config default).
  Reuse the existing dependency-light codepoint detector in
  `linkcheck/language.py` (already covers zh-CN/en) — this is not new research.
  A mismatch is an **advisory flag only** (never `rejected`) — the human review
  step is the real language gate, and an imperfect detector must not drop good
  records. The flag rides in the output record alongside `status: ok`.

**Output Contract**

- R13. Every input record appears in the output with a `status` field
  (`ok | rejected`) and, when rejected, a `rejection_reason` drawn from a fixed
  enumeration: `unsupported_mode`, `missing_anchor`, `missing_link`,
  `bad_target_url_scheme`, `length_out_of_bounds`, `unsafe_chars`,
  `extra_links`, `llm_refusal`, `transport_error`. (Language mismatch is an
  advisory flag on `ok` records per R12, not a rejection reason.) Records are
  never silently dropped (matches the repo's no-silent-drop posture).
- R14. `ok` records carry the generated text; `rejected` records carry enough
  context for a human to decide whether to re-run. stderr carries a summary
  count (generated / rejected / skipped).
- R14b. Per-record rejections do **not** change the exit code: a run that
  processes all records exits 0 (stdout = data, consistent with R1). Non-zero
  exit is reserved for usage / IO / config / allowlist errors (i.e. the run
  could not proceed), per the documented 0–6 contract.

**Security**

- R15. Honor the existing LLM host allowlist (`_util/llm_allowlist.py`) and the
  `BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST=1` opt-in. **This cannot be assumed from
  provider reuse** — `is_allowlisted()` is currently called only in the WebUI
  route; `OpenAICompatibleProvider` POSTs the bearer token without checking it.
  The new CLI MUST call `is_allowlisted()` on the resolved endpoint host (after
  flag-over-config resolution) and reject with `host_not_allowlisted` **before**
  instantiating the provider, unless the opt-in env is set.
- R16. No credentials in any log line, error text, or output record — reuse the
  provider's existing redaction.

## Modes Comparison

| Mode | Length target | Style | Validation focus |
|---|---|---|---|
| `comment` | short (≤ ~60 words) | natural, conversational, slightly imperfect | anchor + 1 link, no extra links |
| `article` | 200–400 words | SEO body, Markdown | anchor + ≥1 link, structure, no injected domains |
| `profile`/`bio` | *(deferred)* | — | — |

## Success Criteria

- A candidate JSONL stream piped into `generate-backlink-text` yields a
  reviewable JSONL/JSON stream where every `ok` record contains the anchor and a
  link to its target, and every `rejected` record states why.
- `--dry-run` produces inspectable prompts with no API key and no network call.
- A malicious candidate (injection payload in `anchor_text`/`target_url`) cannot
  cause an extra domain to appear in output or leak the API key.
- No new env/HTTP/redaction/allowlist code path duplicates what the anchor
  provider already does.

## Scope Boundaries

- No platform login, browser automation, Playwright/CDP publishing, captcha, or
  anti-bot bypass.
- No auto-comment spamming — `mode: "comment"` controls *text style only*; this
  stage never posts anything.
- No UI, scheduler, or database migration.
- `profile`/`bio` mode is out of MVP scope (add after comment + article land).
- Does not wire into `seeds → plan → validate → publish`; output is a review
  artifact, not direct input to `validate-backlinks`/`publish-backlinks`.

## Key Decisions

- **Reuse over rebuild**: thin CLI wrapper over `OpenAICompatibleProvider` +
  existing config/env/allowlist/redaction. Rationale: zero duplication, inherits
  SSRF defense and key redaction for free, stays consistent with
  `[llm.anchor_provider]`.
- **Standalone tool, not pipeline-wired**: custom candidate format
  (`target_url`/`anchor_text`/`mode`) decoupled from the plan/validate schema.
  Rationale: matches the operator's `crawl → generate → review` mental model and
  avoids forcing a `schema.py` change.
- **Emit-with-status, never drop**: every record surfaces in output with
  `status` + `rejection_reason`. Rationale: "reviewable" requires the operator
  to see what failed and why; consistent with the repo's no-silent-drop rule.
- **Env-name reconciliation**: `--api-key-env` defaults to `BACKLINK_LLM_API_KEY`
  (the repo's canonical name; `LLM_API_KEY` is not read by the reused config).
  Base-URL/model/config also reuse `BACKLINK_LLM_*` / `[llm.anchor_provider]`.
  CLI flags take precedence; config fills the gaps. (Exact precedence ladder →
  planning.)
- **Generation is not thin reuse**: `article` adapts `generate_article_body` to a
  single-link / normalized-anchor prompt; `comment` is a new provider method.
  Transport/config/redaction/allowlist are still reused.
- **Anchor match**: `anchor_text` must appear in the `<a>` link text,
  case/whitespace-normalized (not byte-exact) — balances SEO intent against
  reject rate.
- **Language is advisory**: R12 flags mismatches on `ok` records; it never
  rejects. The human review step is the real language gate.

## Dependencies / Assumptions

- "Reuse over rebuild" holds for **transport, config, redaction, and the
  allowlist module** — but NOT fully for generation: the provider has no
  comment-generation method, and `generate_article_body` must be adapted for the
  single-link / verbatim-anchor contract (see Resolve-Before-Planning).
- Input sanitization (`_sanitize_input` + XML escaping) is the *input* boundary
  defense; the anchor *output* filters are body-level reusable only in spirit
  (they are hard-capped at 2–30 chars and cannot validate 200–400 word bodies, and
  no existing code detects extra-links or refusal phrasing — those are new).
- `linkcheck/language.py` provides the dependency-light language detector R12
  needs (no new dependency).
- `_util.url.validate_https_url` provides the https-only `target_url` scheme gate
  R4 needs.

## Outstanding Questions

### Deferred to Planning

- [Affects R6][Technical] Exact env/flag precedence ladder and how `--endpoint`
  normalization (`/chat/completions` vs `/v1` vs bare base) maps onto the
  provider's existing `base_url.rstrip("/") + "/chat/completions"` behavior —
  including the parser's hard `https://` requirement (blocks localhost/Ollama).
- [Affects R2/R6][Technical] Ensure `--temperature` (0.4) / `--timeout` (60) are
  passed into the provider constructor; its defaults (0.7 / 30) differ and would
  silently win if not wired.
- [Affects R14] Any CLI-level stderr/summary output must route through the
  provider's `_redact_for_log` (R16 redaction covers provider lines, not new CLI
  log lines).

## Next Steps

→ `/ce:plan` for structured implementation planning. All blocking product
decisions are resolved (see Key Decisions). Two optional scope simplifications
remain at the operator's discretion (JSONL-only input; trimming the flag set).
