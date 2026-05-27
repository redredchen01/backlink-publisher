---
date: 2026-05-27
topic: url-parse-never-raises
---

# URL-Parse Never-Raises Hardening Sweep

## Problem Frame

`urlparse()` / `urlsplit()` raise `ValueError` on a malformed authority —
most notably an unterminated IPv6 literal like `http://[invalid` or
`http://[::1` (the raise happens inside `urlparse` itself, not only on the
`.hostname` access). Several call sites in `linkcheck/` and `content/` sit on
code paths that are contractually supposed to **never raise** — they classify a
bad URL and return a typed verdict (`(False, "invalid URL")`,
`return False`, `raise InputValidationError`). On a malformed-IPv6 input these
sites instead leak a bare `ValueError`, defeating their own contract.

The sharpest instance is the **scraper loop** (`content/scraper.py`): for each
scraped `<a href>` it calls `strip_fragment_query(href)` → `is_same_host(href,
list_url)` → `urlparse(href).path`, all on **untrusted page-controlled input**.
A single malformed href in a scraped page raises `ValueError` and crashes the
entire scrape, not just the one bad link. The SSRF guard
`content/_http.py:_block_if_private` has the same flaw: a malformed-IPv6 URL
yields an unhandled `ValueError` instead of the intended `InputValidationError`,
so the wrong error path runs.

PR #258 already solved this **locally** inside `content/_preflight_fetch.py`
(`_safe_hostname`, `_is_http_url` — both `try/except ValueError`), but those
guards are private to that module. This sweep generalizes the pattern and
applies it everywhere a never-raises path parses a URL. See feedback
`[[feedback_urlparse_raises_on_malformed_ipv6]]`.

## Requirements

**Shared helper**
- R1. Add a malformed-input-safe parse helper to `_util/url.py` (the existing
  home of `is_same_host`, `canonicalize_url`, `normalize_url_for_fetch`, …): a
  `safe_urlparse(url) -> ParseResult | None` and/or `safe_hostname(url) -> str
  | None` that returns `None` on any `ValueError` rather than propagating it.
  The non-empty/`str` guard `_preflight_fetch._is_http_url` already performs is
  preserved by callers, not folded into the parser.

**Explicit call sites (never-raises classification paths)**
- R2. `linkcheck/http.py:_check_url_once` — a malformed URL must return
  `(False, "invalid URL: …")`, never raise.
- R3. `content/_http.py:_block_if_private` (SSRF guard) — a malformed-IPv6 URL
  must raise the intended `InputValidationError` ("no resolvable host"), never a
  bare `ValueError`.
- R4. `content/fetch.py:_is_valid_http_url` — must return `False` on malformed
  input, preserving its "deterministic invalid_url rather than flaky network
  error" contract.
- R5. `content/scraper.py` list-URL validation (`urlparse(list_url)`) — must
  raise the intended `InputValidationError("invalid list_url")`, never a bare
  `ValueError`.

**`_util/url.py` helpers reachable from untrusted (scraped) input**
- R6. `is_same_host(a, b)` — its docstring already promises "returns False if
  either input … cannot be parsed"; make that true for malformed IPv6 too
  (currently raises). Called on scraped hrefs.
- R7. `strip_fragment_query(url)` — called on scraped hrefs before any
  validation; must return a safe value (e.g. `""`) instead of raising.
- R8. The per-iteration `urlparse(cleaned).path` in the scraper link loop must
  not raise; a malformed scraped href is skipped, not fatal.

**End-to-end contract**
- R9. The scraper link-discovery loop must survive a malformed `<a href>` in a
  scraped page by skipping that one link and continuing, not aborting the scrape.

## Success Criteria

- A URL that makes `urlparse` raise (`http://[invalid`, `http://[::1`,
  `http://[`) flows through every path in R2–R9 and produces that path's
  **intended** outcome (typed verdict / skip), never an unhandled `ValueError`.
- A scraped page containing one malformed href still yields all the other valid
  discovered links.
- The SSRF guard's malformed-input rejection remains a rejection (no weakening
  of the private-IP block — only the failure *type/mode* changes).
- No regression in the existing `linkcheck` / `content` / `scraper` suites; new
  malformed-input cases added for each guarded site.

## Scope Boundaries

- **Not** the broad "wrap all 32 `urlparse`/`urlsplit` sites" sweep — only the
  never-raises classification paths and the helpers reachable from untrusted
  input. Sites that legitimately validate upstream or where raising is the
  correct behavior are left alone.
- **No SSRF policy change** — the set of blocked addresses and the allow/deny
  decision are unchanged; only the malformed-input failure mode is fixed.
- **Do not re-implement** `_preflight_fetch.py`'s already-correct guards; at most
  refactor them to call the shared helper, and only if it doesn't churn that
  recently-merged (#258) file meaningfully (decide in planning).
- WebUI, CLI argparse, and adapter code are out of scope — this is a
  `_util`/`linkcheck`/`content` library-level hardening.

## Dependencies / Assumptions

- Lives entirely in the **free zone** (`_util/url.py`, `linkcheck/`, `content/`)
  — no overlap with the active worktrees (typed-envelope touches only
  `_util/error_envelope.py` + `errors.py`; canary/config-sandbox/idempotency
  touch `cli/`/`config/`/`publishing/`). Verified 2026-05-27.
- Base: `origin/main` `7bbaf11` (includes #268, #269). Worktree
  `bp-url-never-raises`, branch `fix/url-parse-never-raises`.

## Outstanding Questions

### Deferred to Planning
- [Affects R1][Technical] Final helper shape: one `safe_urlparse` returning
  `ParseResult | None`, or also a `safe_hostname` convenience? Pick the minimal
  set the call sites actually need.
- [Affects R6/R7][Technical] Do `canonicalize_url` (events projector path on
  stored `live_url`) and `absolutize` (`urljoin` — verify whether it raises on
  malformed IPv6) also need guarding, or are they out of the untrusted-input
  reachable set? Confirm via call-graph during planning.
- [Affects R3][Needs research] Confirm whether any other SSRF/fetch entrypoint
  (e.g. `linkcheck` redirect handling, `content/fetch` post-redirect recheck)
  re-parses a server-controlled `Location` header that could be malformed.

## Next Steps
→ `/ce:plan` for structured implementation planning
