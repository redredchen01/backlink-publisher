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

The sharpest instance is the **scraper** (`content/scraper.py`), and the crash
arrives *earlier* than first assumed. For each scraped `<a href>` the collection
step calls `absolutize(list_url, href)` (`scraper.py:283`) — which wraps
`urllib.parse.urljoin`, and **`urljoin` raises `ValueError` on malformed IPv6
too** (verified). So the very first malformed href crashes during *collection*,
before the later filter loop (`strip_fragment_query` → `is_same_host` →
`urlparse(cleaned).path`, `scraper.py:377-382`) is even reached. Every one of
those steps is on **untrusted page-controlled input**, and a single malformed
href aborts the entire scrape, not just the one bad link. The SSRF guard
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
- R1. Add malformed-input-safe parse helpers to `_util/url.py` (the existing
  home of `is_same_host`, `canonicalize_url`, `normalize_url_for_fetch`, …):
  minimally a `safe_urlparse(url) -> ParseResult | None` that returns `None` on
  any `ValueError` rather than propagating it (a `safe_hostname` convenience is
  optional — derivable from `safe_urlparse`). Because **`urljoin` raises on the
  same malformed input** (R6), the helper set must also cover the absolutize
  path — either a `safe_absolutize`/`safe_urljoin` helper or a guarded call site
  (decide in planning, see Key Decisions). The non-empty/`str` precheck
  `_preflight_fetch._is_http_url` performs stays with callers, not folded in.
- R11. **None means "unvalidatable", not "allow".** Define the contract for the
  safe helpers explicitly: returning `None` signals a URL that *cannot be
  parsed*. SSRF/validation contexts (R3, R5) must treat `None` as a **rejection**
  (raise the intended `InputValidationError`) — never fail-open by parsing to
  `None` and proceeding. Only scrape-discovery contexts (R6–R9) treat `None`/`""`
  as **skip this one link**. This is what makes the "no SSRF weakening" guarantee
  real rather than asserted.

**Explicit call sites (never-raises classification paths)**
- R2. `linkcheck/http.py:_check_url_once` — a malformed URL must return
  `(False, "invalid URL: …")`, never raise. (`urlparse` at the top of the
  function currently runs before the scheme/netloc check, so it raises first.)
- R3. `content/_http.py:_block_if_private` (SSRF guard, `urlparse(url).hostname`
  at `:39` is unguarded and raises *before* the intended
  `InputValidationError`) — a malformed-IPv6 URL must raise the intended
  `InputValidationError` ("no resolvable host"), never a bare `ValueError`, and
  must never skip the private-IP block (see R11).
- R4. `content/fetch.py:_is_valid_http_url` — must return `False` on malformed
  input, preserving its "deterministic invalid_url rather than flaky network
  error" contract.
- R5. `content/scraper.py:211` list-URL validation (`urlparse(list_url)`, runs
  before the explicit scheme/netloc check) — must raise the intended
  `InputValidationError("invalid list_url")`, never a bare `ValueError`. A
  malformed `list_url` fails **loudly** here (operator config error), it is not
  silently skipped (see R11).

**`_util/url.py` helpers reachable from untrusted (scraped) input**
- R6. `absolutize(list_url, href)` (`scraper.py:283`, the **first** failure
  point — runs during URL collection before any filtering). `urljoin` raises
  `ValueError` on malformed IPv6, so a malformed scraped href crashes collection.
  Must skip the one malformed href and continue, not abort the scrape.
- R7. `is_same_host(a, b)` — its docstring already promises "returns False if
  either input … cannot be parsed"; make that true for malformed IPv6 too
  (currently raises). Called on scraped hrefs (`scraper.py:378`).
- R8. `strip_fragment_query(url)` (`scraper.py:377`, called on scraped hrefs
  before any validation) — must return `""` instead of raising; the empty result
  causes the scraper to skip that link. (This subsumes the later
  `urlparse(cleaned).path` at `:382`: once `strip_fragment_query` returns `""`,
  `is_same_host("", …)` returns `False` and the link is skipped before `:382` is
  reached.)

**End-to-end contract**
- R9. The scraper must survive a malformed `<a href>` anywhere in a scraped page
  — collection (R6) *and* filtering (R7, R8) — by skipping that one link and
  continuing, never aborting the scrape.

## Success Criteria

- A URL that makes `urlparse` raise (`http://[invalid`, `http://[::1`,
  `http://[`) flows through every path in R2–R9 and produces that path's
  **intended** outcome (typed verdict / skip), never an unhandled `ValueError`.
- A scraped page containing one malformed href still yields all the other valid
  discovered links — proven at both the collection step (R6 `absolutize`) and the
  filter step (R7/R8).
- The SSRF guard's malformed-input rejection remains a rejection and stays
  **fail-closed**: a malformed-IPv6 URL is blocked (raises `InputValidationError`),
  never parsed-to-`None`-and-allowed (R11). No weakening of the private-IP block —
  only the failure *type/mode* changes.
- No regression in the existing `linkcheck` / `content` / `scraper` suites; new
  malformed-input cases added for each guarded site.

## Scope Boundaries

- **Not** the broad "wrap all 32 `urlparse`/`urlsplit` sites" sweep — only the
  never-raises classification paths and the helpers reachable from untrusted
  input. Sites that legitimately validate upstream or where raising is the
  correct behavior are left alone.
- **`canonicalize_url` is OUT of scope.** Call-graph confirms its only inputs are
  pre-validated `live_url` / `target_url` / `published_url` (events projector,
  CLI plan target, post-publish) — never raw scraped hrefs. The R6–R8 scrape-path
  guards keep malformed URLs out of the discovered-link set, so malformed input
  cannot reach `canonicalize_url` via the DB either. Same for the
  `validate_*_url` validators (they run on operator config, which fails loudly,
  not silently). See Key Decisions.
- **No SSRF policy change** — the set of blocked addresses and the allow/deny
  decision are unchanged; only the malformed-input failure mode is fixed (R11).
- **Do not re-implement** `_preflight_fetch.py`'s already-correct guards; at most
  refactor them to call the shared helper, and only if it doesn't churn that
  recently-merged (#258) file meaningfully (decide in planning).
- WebUI, CLI argparse, and adapter code are out of scope — this is a
  `_util`/`linkcheck`/`content` library-level hardening.

## Key Decisions

- **`absolutize`/`urljoin` is in scope (R6).** Verified: `urljoin` raises
  `ValueError` on malformed IPv6 exactly like `urlparse`, and `absolutize` runs
  at `scraper.py:283` *before* the filter loop — it is the first crash point on
  untrusted input, so guarding only `strip_fragment_query`/`is_same_host` would
  leave R9 unmet.
- **`None` is fail-closed in validation/SSRF contexts (R11).** The safe helper's
  `None` return means "unvalidatable" → reject (raise), never proceed. Only
  scrape-discovery skips. This keeps the SSRF guarantee real.
- **`canonicalize_url` left unguarded** — call-graph shows it is unreachable from
  untrusted input (see Scope Boundaries). Revisit only if a future caller feeds
  it scraped data.

## Dependencies / Assumptions

- Lives entirely in the **free zone** (`_util/url.py`, `linkcheck/`, `content/`)
  — no overlap with the active worktrees (typed-envelope touches only
  `_util/error_envelope.py` + `errors.py`; canary/config-sandbox/idempotency
  touch `cli/`/`config/`/`publishing/`). Verified 2026-05-27.
- Base: `origin/main` `7bbaf11` (includes #268, #269). Worktree
  `bp-url-never-raises`, branch `fix/url-parse-never-raises`.

## Outstanding Questions

### Deferred to Planning
- [Affects R1][Technical] Final helper shape + the `absolutize` guard form: is
  one `safe_urlparse(url) -> ParseResult | None` enough (callers derive hostname
  and guard the urljoin call themselves), or also a `safe_absolutize`/
  `safe_hostname`? Enumerate each of R2–R8's actual need and pick the minimal set
  — avoid shipping two helpers where one suffices.
- [Affects R1][Technical] `_preflight_fetch.py` dedup: extract its existing
  `_safe_hostname`/`_is_http_url` into the new `_util/url.py` helper and import
  back (cleaner, but touches the just-merged #258 file), or leave them as local
  duplicates with a cross-reference comment? Decide based on how invasive the
  extraction is.
- [Affects R3][Needs research] Confirm whether any other SSRF/fetch entrypoint
  (e.g. `linkcheck` redirect handling, `content/fetch` post-redirect recheck)
  re-parses a server-controlled `Location` header that could be malformed. If
  yes, add it as an explicit call site; if no, close with a one-line note in the
  plan rather than deferring to implementation.

## Next Steps
→ `/ce:plan` for structured implementation planning
