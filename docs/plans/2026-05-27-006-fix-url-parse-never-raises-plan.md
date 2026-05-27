---
title: "fix: URL-parse never-raises hardening sweep"
type: fix
status: completed
date: 2026-05-27
deepened: 2026-05-27
origin: docs/brainstorms/2026-05-27-url-parse-never-raises-requirements.md
claims: {}
---

# fix: URL-parse never-raises hardening sweep

## Overview

`urlparse` / `urlsplit` / `urljoin` all raise `ValueError` on a malformed
authority — most notably an unterminated IPv6 literal (`http://[invalid`,
`http://[::1`, `http://[`). Several code paths in `linkcheck/` and `content/`
are contractually supposed to **never raise** (they classify a bad URL and
return a typed verdict or raise a *typed* error), but on malformed-IPv6 input
they leak a bare `ValueError` instead. The sharpest case: the scraper crashes
the entire link-discovery run on a single malformed `<a href>` in an untrusted
scraped page.

This plan adds shared malformed-input-safe parse helpers and applies them at
every never-raises site, with a fail-closed rule for the SSRF/validation paths
so the hardening cannot weaken the private-IP block.

## Problem Frame

See origin: `docs/brainstorms/2026-05-27-url-parse-never-raises-requirements.md`.
PR #258 already solved this *locally* in `content/_preflight_fetch.py`
(`_safe_hostname`, `_is_http_url`), but those guards are private to that module.
This sweep generalizes the pattern. Feedback: `[[feedback_urlparse_raises_on_malformed_ipv6]]`.

## Requirements Trace

- R1. Shared malformed-input-safe parse helper(s) in `_util/url.py`.
- R2. `linkcheck/http.py:_check_url_once` returns `(False, "invalid URL: …")`, never raises.
- R3. `content/_http.py:_block_if_private` (requests-backend SSRF guard) raises the intended `InputValidationError` on malformed input, never a bare `ValueError`, and never skips the private-IP block.
- R3b. **`_util/net_safety.py:_check_url_for_ssrf`** (the urllib-backend SSRF gate, the main path via `fetch.py:_check_once`) returns its blocked reason `"invalid_host"` on malformed input, never a bare `ValueError`. Fail-closed: a non-`None` reason = blocked.
- R3c. **`_util/net_safety.py:_SSRFSafeRedirectHandler.redirect_request`** must not raise `ValueError` on a malformed (server-controlled) `Location` header or `req.full_url`; a malformed redirect target is treated as a blocked redirect (`URLError`), never a crash and never an allowed hop.
- R4. `content/fetch.py:_is_valid_http_url` returns `False` on malformed input.
- R5. `content/scraper.py:211` list-URL validation raises `InputValidationError("invalid list_url")` (fails loudly), never a bare `ValueError`.
- R6. `absolutize` (`scraper.py:283`) skips the one malformed scraped href, never aborts the scrape.
- R7. `is_same_host` returns `False` on malformed input (honors its docstring).
- R8. `strip_fragment_query` returns `""` on malformed input (link gets skipped).
- R9. The scraper survives a malformed href anywhere — collection (R6) and filtering (R7/R8).
- R10. `None`/`""` from a safe helper means "unvalidatable": SSRF/validation contexts (R3, R3b, R3c, R5) **block/reject**; scrape-discovery (R6–R8) **skip**. Fail-closed, never fail-open.

## Scope Boundaries

- Not the broad "wrap all 32 `urlparse`/`urlsplit` sites" sweep — only never-raises classification paths and helpers reachable from untrusted input.
- `canonicalize_url` and the `validate_*_url` validators are **out of scope** — call-graph confirms they only see pre-validated `live_url`/`target_url`/`published_url`, never raw scraped hrefs; the R6–R8 guards keep malformed URLs out of the discovered set (and thus out of the DB).
- No SSRF policy change — blocked-address set and allow/deny decision unchanged; only the malformed-input failure *mode* changes.
- WebUI, CLI argparse, and adapter code out of scope.
- In scope: `_util/url.py`, `_util/net_safety.py` (the urllib SSRF gate + redirect handler — added during the deepening pass), `linkcheck/`, `content/`. All in the free zone — no overlap with active worktrees (typed-envelope touches only `_util/error_envelope.py`+`errors.py`; verified 2026-05-27).

## Context & Research

### Relevant Code and Patterns

- **Reference guard pattern:** `content/_preflight_fetch.py` `_safe_hostname` (try/except ValueError → None) and `_is_http_url` (str precheck + scheme/netloc), merged in #258.
- **Helper home:** `_util/url.py` already hosts `is_same_host`, `strip_fragment_query`, `absolutize`, `canonicalize_url`, `validate_https_url`, `normalize_url_for_fetch`.
- **Scrape path (untrusted):** `content/scraper.py:283` `absolutize(list_url, href)` (collection) → `:377` `strip_fragment_query(url)` → `:378` `is_same_host(cleaned, list_url)` → `:382` `urlparse(cleaned).path`.
- **Classification sites:** `linkcheck/http.py:_check_url_once` (urlparse before scheme/netloc check), `content/fetch.py:_is_valid_http_url`, `content/scraper.py:211` (`urlparse(list_url)`).
- **Two SSRF backends (both need guarding):**
  - **urllib backend (main fetch path):** `_util/net_safety.py:_check_url_for_ssrf` (`urlparse(url)` at ~L71, unguarded) is called by `content/fetch.py:_check_once` (a *never-raises* function) on untrusted URLs, **and** by `_SSRFSafeRedirectHandler.redirect_request` (~L91-97) on the server-controlled redirect `newurl`/`req.full_url` (two more unguarded `urlparse(...).scheme` calls). This is the highest-value site — the main fetch gate and the redirect Location both flow through it.
  - **requests backend:** `content/_http.py:39 _block_if_private` (`urlparse(url).hostname`, unguarded).
- **#258 only half-covered this:** `_preflight_fetch.py:_safe_ssrf_check` already wraps `_check_url_for_ssrf` for the *preflight* path (and its docstring notes the raise), but `_check_once` and the redirect handler call `_check_url_for_ssrf` **directly**, unguarded.
- **Blast radius (verified):** `is_same_host` / `strip_fragment_query` / `absolutize` are called **only** from `scraper.py` — making them internally never-raise has no other-caller impact (`audit/diff.py` references `is_same_host` only in a comment).
- **Redirect/Location (R3c) — the two backends handle redirects differently, don't conflate them:** the **requests** backend (`_http.py`) sets `allow_redirects=False`, so it has no redirect handler and no R3c concern. The **urllib** backend (`fetch.py`/`net_safety.py`) *does* follow redirects (default cap 10) via the custom `_SSRFSafeRedirectHandler`, which re-checks each hop. So R3c is a **live, exercised path** (not dead code): a server returning a malformed `Location` reaches `_SSRFSafeRedirectHandler.redirect_request` on the urllib backend. It routes through `_check_url_for_ssrf` (R3b), *not* `_block_if_private`.

### Institutional Learnings

- `[[feedback_urlparse_raises_on_malformed_ipv6]]` — `urlparse("http://[invalid")` itself raises (not only `.hostname`); never-raises code must guard every urlparse site. PR #258.
- `[[feedback_urllib_request_non_ascii_must_normalize]]` — adjacent URL-handling fragility class in the same modules.

## Key Technical Decisions

- **One shared core helper `safe_urlparse(url) -> ParseResult | None`** in `_util/url.py`, plus a thin `safe_hostname(url) -> str | None` convenience (derives from `safe_urlparse`). Both return `None` on malformed input. **The guard must cover `ValueError` AND non-`str` input** — `urlparse(123)`/`urlparse(["x"])` raise `AttributeError`, not `ValueError` (verified). So `safe_urlparse` does `if not isinstance(url, str): return None` first, then `try: urlparse(url) except ValueError: return None`. This also lets Unit 5 fold in `_preflight_fetch._is_http_url`'s `isinstance` precheck. Rationale for sharing: 4 classification sites + 2 #258 locals justify a shared helper over per-site try/except; matches the existing `_util/url.py` helper-home pattern.
- **The three scrape-path `_util/url.py` helpers become internally safe** rather than adding parallel `safe_*` variants — `is_same_host`/`strip_fragment_query` already *promise* safe behavior in their docstrings, and their only caller is the scraper, so internal guarding is the lowest-surface fix. `absolutize` wraps `urljoin` in try/except → `""`.
- **R10 fail-closed contract:** `None`/`""` is "unvalidatable", not "allow". SSRF/validation sites (R3, R5) convert it to a *raised* `InputValidationError`; scrape-discovery sites (R6–R8) convert it to *skip this link*. This is what makes "no SSRF weakening" real.
- **Helpers are additive and behavior-preserving for valid input** — every guard only adds a `ValueError` branch; the success path is unchanged (idempotency / existing-test safety).

## Open Questions

### Resolved During Planning

- Helper shape → `safe_urlparse` core + `safe_hostname` convenience (above).
- `absolutize`/`urljoin` in scope → yes, it is the first crash point (R6).
- `canonicalize_url` guarding → no, out of untrusted-reachable set (call-graph).
- Malformed `Location` header → covered by R3b/R3c. The urllib backend's redirects route through `_SSRFSafeRedirectHandler` → `_check_url_for_ssrf` (NOT `_block_if_private`, which is the requests backend); both are hardened in Unit 4. (Corrected during the deepening security pass — the original plan had this flow backwards.)
- Is `_block_if_private` the only SSRF chokepoint? → No. There are two SSRF backends: `_check_url_for_ssrf` (urllib, main fetch path + redirect handler) and `_block_if_private` (requests). Both are in Unit 4. `#258` only guarded the preflight wrapper, not these.

### Deferred to Implementation

- **#258 dedup (Unit 5):** whether refactoring `_preflight_fetch._safe_hostname`/`_is_http_url` to import the shared helpers is low-churn enough to include. Drop the unit if the diff turns out invasive — the duplication is harmless.
- Exact helper signatures / whether `safe_hostname` is even needed once call sites are wired (some may only need `safe_urlparse`). Decide while wiring.

## Implementation Units

- [x] **Unit 1: Shared safe-parse helpers in `_util/url.py`**

**Goal:** Add `safe_urlparse(url) -> ParseResult | None` and `safe_hostname(url) -> str | None`, returning `None` on malformed/non-`str` input (never raising). Foundation for all later units.

**Requirements:** R1

**Dependencies:** None

**Files:**
- Modify: `src/backlink_publisher/_util/url.py`
- Test: `tests/test_url_utils.py`

**Approach:**
- `safe_urlparse`: `if not isinstance(url, str): return None`, then wrap `urlparse(url)` in `try/except ValueError: return None`. (Non-`str` raises `AttributeError`, not `ValueError` — verified — so the `isinstance` guard is load-bearing, not cosmetic.) Empty string → `None` for caller convenience.
- `safe_hostname` calls `safe_urlparse`; returns `.hostname` if a result, else `None`.
- Mirror `_preflight_fetch._safe_hostname` / `_is_http_url` semantics so Unit 5 can consolidate.

**Execution note:** Implement test-first — the helpers are pure functions with a crisp malformed-input contract.

**Patterns to follow:** `content/_preflight_fetch.py` `_safe_hostname`; existing `_util/url.py` helper style + type hints.

**Test scenarios:**
- Happy path: `safe_urlparse("https://example.com/p?q=1")` returns a `ParseResult` with expected scheme/netloc/path; `safe_hostname` returns `"example.com"`.
- Edge case: empty string / `None` → `None` (no raise).
- Error path: `safe_urlparse("http://[invalid")`, `"http://[::1"`, `"http://["` each return `None`, never raise. `safe_hostname` same → `None`.
- Error path (non-`str`): `safe_urlparse(123)` / `safe_urlparse(["x"])` / `safe_urlparse({})` → `None`, never `AttributeError`.
- Happy path: valid bracketed IPv6 `"http://[::1]:8080/"` parses successfully → `safe_hostname` returns `"::1"` (confirm a *well-formed* IPv6 is NOT swallowed).

**Verification:** Both helpers importable from `_util/url.py`; all malformed inputs return `None`; well-formed inputs (incl. valid IPv6) parse normally.

---

- [x] **Unit 2: Harden the scraper untrusted-input path (collection + filtering)**

**Goal:** Make `absolutize`, `is_same_host`, `strip_fragment_query` internally never-raise so a malformed scraped href is skipped, not fatal.

**Requirements:** R6, R7, R8, R9

**Dependencies:** Unit 1

**Files:**
- Modify: `src/backlink_publisher/_util/url.py`
- Test: `tests/test_url_utils.py`, `tests/test_work_scraper.py`

**Approach:**
- `absolutize(base, href)`: wrap the `urljoin` call in `try/except ValueError: return ""` (preserve the existing empty-`href` early return).
- `is_same_host(a, b)`: replace the two bare `urlparse(...).netloc` calls with `safe_urlparse`; if either is `None`, return `False` (the docstring already promises this).
- `strip_fragment_query(url)`: use `safe_urlparse`; on `None` return `""` (caller skips the link).
- The `:382` `urlparse(cleaned).path` site needs no change — once `strip_fragment_query` returns `""`, `is_same_host("", …)` returns `False` and `:382` is never reached for malformed input.

**Execution note:** Test-first. Add an end-to-end scraper test before changing the helpers.

**Patterns to follow:** existing `_util/url.py` helpers; the scraper loop at `scraper.py:283` and `:370-382`.

**Test scenarios:**
- Error path (`absolutize`): `absolutize("https://site.com/", "http://[invalid")` → `""`, never raises. Also `absolutize("http://[invalid", "/page")` (malformed **base**) → `""`, never raises. Valid relative href still resolves correctly.
- Error path (`is_same_host`): `is_same_host("http://[invalid", "https://site.com")` → `False`, never raises. Both-valid same/different host unchanged.
- Error path (`strip_fragment_query`): `strip_fragment_query("http://[::1")` → `""`. Valid URL still strips fragment+query.
- Integration (end-to-end, `test_work_scraper.py`): a scraped HTML page whose anchors include one malformed-IPv6 href among several valid ones → discovery returns all the valid links and silently skips the malformed one; no exception escapes.

**Verification:** Scraping a page with a malformed href yields the other valid links; no `ValueError` escapes any helper; valid-input behavior unchanged (existing scraper tests green).

---

- [x] **Unit 3: Harden never-raises classification sites (linkcheck + fetch)**

**Goal:** `_check_url_once` and `_is_valid_http_url` return their typed "invalid" verdict on malformed input instead of raising.

**Requirements:** R2, R4

**Dependencies:** Unit 1

**Files:**
- Modify: `src/backlink_publisher/linkcheck/http.py`, `src/backlink_publisher/content/fetch.py`
- Test: `tests/test_linkcheck.py`, `tests/test_content_fetch.py`

**Approach:**
- `linkcheck/http.py:_check_url_once`: parse via `safe_urlparse`; on `None` return `(False, f"invalid URL: {url}")` (same shape as the existing scheme/netloc failure).
- `content/fetch.py:_is_valid_http_url`: parse via `safe_urlparse`; on `None` return `False`. Preserves its "deterministic invalid_url rather than flaky network error" contract.

**Execution note:** Test-first.

**Patterns to follow:** existing return shapes in each function (`(bool, str|None)` for linkcheck; `bool` for fetch).

**Test scenarios:**
- Error path (`_check_url_once`): `"http://[invalid"` → `(False, "invalid URL: …")`, never raises.
- Error path (`_is_valid_http_url`): `"http://[::1"` → `False`, never raises.
- Happy path (both): a normal `https://` URL still passes; an already-handled bad case (empty scheme/netloc) still returns the same verdict (no regression).

**Verification:** Both functions return their invalid verdict on malformed IPv6; existing valid/invalid cases unchanged.

---

- [x] **Unit 4: Harden the SSRF gates (both backends) + list_url, fail-closed**

**Goal:** Every SSRF gate and the `list_url` validator convert malformed input into their intended *blocked/raised* outcome, never a bare `ValueError`, never a skipped check or allowed hop. This is the security-critical unit — it covers the urllib backend (the main fetch path + redirect handler), the requests backend, and the scraper entry validation.

**Requirements:** R3, R3b, R3c, R5, R10

**Dependencies:** Unit 1

**Files:**
- Modify: `src/backlink_publisher/_util/net_safety.py`, `src/backlink_publisher/content/_http.py`, `src/backlink_publisher/content/scraper.py`
- Test: `tests/test_content_fetch.py`, `tests/test_work_scraper.py`, `tests/test_net_safety.py`

**Approach:**
- **`_util/net_safety.py:_check_url_for_ssrf`** (R3b, the main urllib gate via `fetch.py:_check_once`): `parsed = safe_urlparse(url); if parsed is None: return "invalid_host"` (explicit None check before any `.hostname` deref), then `host = parsed.hostname` as today. The function *already* returns `"invalid_host"` for a host-less URL, and any non-`None` reason is treated as **blocked** by callers, so `None → "invalid_host"` is the natural fail-closed mapping (no caller change needed).
- **`_util/net_safety.py:_SSRFSafeRedirectHandler.redirect_request`** (R3c): the handler first does an https→http downgrade check using `urlparse(req.full_url).scheme` and `urlparse(newurl).scheme` — **both unguarded, and both run BEFORE `_check_url_for_ssrf(newurl)`**. So R3b alone does not save this path: a malformed `newurl` crashes the downgrade check first. Fix: parse `newurl` via `safe_urlparse` **at the very top of `redirect_request`**; if `None`, raise `URLError("ssrf_redirect:invalid_host")` immediately. `req.full_url` is also parsed via `safe_urlparse` as a belt-and-suspenders guard, but only uses empty-string fallback if `None` (it cannot actually be malformed: urllib's `Request()` validates on construction, so a `Request` with a bad `full_url` cannot exist). Only after both parse cleanly does the existing downgrade check + `_check_url_for_ssrf(newurl)` (already R3b-hardened) run. **Implementation order within Unit 4: do R3b first, then R3c can rely on it for the `_check_url_for_ssrf(newurl)` call.**
- **`content/_http.py:_block_if_private`** (R3, requests backend): replace `host = urlparse(url).hostname` with `host = safe_hostname(url)`; the existing `if not host: raise InputValidationError("URL has no resolvable host: …")` then catches malformed input (None) AND host-less URLs in one branch. Fail-closed: None → raise.
- **`content/scraper.py:211`** (R5): parse `list_url` via `safe_urlparse`; on `None` raise `InputValidationError("invalid list_url: …")` (same error the scheme/netloc check raises). Loud failure — operator config error, not a silent skip.

**Execution note:** Test-first. Security-critical fail-closed path — for the SSRF gates assert the outcome is *blocked/raised*, and assert the malformed URL never reaches the network (`getaddrinfo`/`urlopen`/`_resolve_addresses` not called for malformed input).

**Patterns to follow:** `_check_url_for_ssrf`'s existing `"invalid_host"` reason ladder + `_is_blocked_ip`; `_preflight_fetch._safe_ssrf_check` (the #258 wrapper that already proves the `None → blocked` mapping is correct); existing `InputValidationError` raises in `_block_if_private` and `scraper.py:212-213`; existing `URLError("ssrf_redirect:…")` raise in the redirect handler.

**Test scenarios:** *(network-isolation proof: every SSRF test must patch `socket.getaddrinfo` / `_resolve_addresses` and assert it is **not called** for malformed input — a return-value check alone does not prove fail-closed.)*
- Error path / security (`_check_url_for_ssrf`): `"http://[invalid"` returns `"invalid_host"` (blocked), never raises; `getaddrinfo` not called.
- Error path / security (`_check_once` integration): `_check_once("http://[::1", …)` returns the not-reachable verdict (its never-raises contract) instead of leaking `ValueError`.
- Error path / security (redirect handler, malformed `newurl`): a 30x whose `Location` is malformed-IPv6 raises `URLError` (blocked redirect), never `ValueError`, never follows the hop, `getaddrinfo` not called.
- Error path / security (redirect handler, malformed `req.full_url`): the downgrade check does not crash on a malformed original URL — it blocks (`URLError`) before the scheme comparison.
- Regression (redirect handler): a valid https→http downgrade still raises `ssrf_https_downgrade`; a valid redirect to a public URL still follows.
- Error path / security (`_block_if_private`): `"http://[invalid"` raises `InputValidationError` (not `ValueError`, not pass-through); `getaddrinfo` not called; private-IP block not bypassed.
- Error path (`list_url`): `fetch_*_from_list("http://[::1", …)` raises `InputValidationError("invalid list_url")`, never `ValueError`, never silently returns empty.
- Happy path: a valid private-IP URL is still blocked on both backends; a valid public URL still passes; a valid redirect to a public URL still follows.
- Edge case: host-less but parseable URL (`"https:///path"`) still hits the existing "no resolvable host"/`invalid_host` path (no behavior change).

**Verification:** Malformed input to any SSRF gate yields *blocked* (`"invalid_host"` / `InputValidationError` / `URLError`), never `ValueError`; tests prove `getaddrinfo`/`_resolve_addresses` is never reached for malformed input on every backend (urllib gate, redirect handler, requests gate); `list_url` fails loudly.

---

- [x] **Unit 5 (optional): Consolidate #258's local guards**

**Goal:** Refactor `_preflight_fetch._safe_hostname` / `_is_http_url` to call the shared `_util/url.py` helpers, removing the duplication.

**Requirements:** R1 (consolidation)

**Dependencies:** Unit 1

**Files:**
- Modify: `src/backlink_publisher/content/_preflight_fetch.py`
- Test: `tests/test_preflight_fetch.py` (existing — must stay green)

**Approach:**
- `_safe_hostname` → delegate to (or be replaced by) `safe_hostname`.
- `_is_http_url` → use `safe_urlparse` internally, keep the str-precheck + scheme/netloc logic.
- **Drop this unit if the diff is anything beyond a few lines** — the duplication is harmless and #258 is freshly merged.

**Execution note:** Characterization-first — the existing `test_preflight_fetch.py` cases are the contract; they must pass unchanged.

**Patterns to follow:** the new Unit 1 helpers.

**Test scenarios:**
- Integration: full existing `test_preflight_fetch.py` suite passes unchanged (no behavior change, pure consolidation).

**Verification:** `_preflight_fetch` behavior identical; no new duplication of the safe-parse pattern.

## System-Wide Impact

- **Interaction graph:** `_util/url.py` helpers feed the scraper (Unit 2), linkcheck/fetch verdicts (Unit 3), and the SSRF guard (Unit 4). The shared helper (Unit 1) is the single new dependency.
- **Error propagation:** malformed input now produces each path's *intended* typed outcome — `(False, msg)` / `False` / skip / `InputValidationError`. No new exception types introduced.
- **State lifecycle risks:** none — no persistence change. The R6–R8 guards prevent malformed URLs from entering the discovered-link set, so they cannot reach the DB / `canonicalize_url`.
- **API surface parity:** the three scrape-path helpers are used only by the scraper (verified), so internal guarding has no external-caller impact.
- **Security (fail-closed):** Unit 4 is the load-bearing security unit and spans **both** SSRF backends — `_check_url_for_ssrf` (urllib: main `_check_once` path + `_SSRFSafeRedirectHandler` on server-controlled `Location`) and `_block_if_private` (requests). `None`/unparseable must always become *blocked* (`"invalid_host"` / `InputValidationError` / `URLError`), never an allow or a crash. Tests assert no malformed URL reaches DNS/network on either backend.
- **Redirect trust boundary:** the redirect `Location` is server-controlled untrusted input on every fetch; R3c closes it so a malicious redirect target can neither crash the handler nor slip past the SSRF check.
- **Unchanged invariants:** valid-URL behavior (including well-formed bracketed IPv6) is unchanged across every helper and site; the SSRF blocked-address policy is unchanged.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| A "safe" helper swallows a *well-formed* IPv6 URL (over-broad except) | Unit 1 tests assert a valid `http://[::1]:8080/` parses normally; `except ValueError` is narrow (not bare `except`). |
| SSRF guard accidentally made fail-open (None → allow) on either backend | Unit 4 routes `None` to *blocked* on both backends (`"invalid_host"` reason / `InputValidationError` / `URLError`); tests assert no malformed URL reaches DNS/network on each. |
| A second/third SSRF site is missed (as the urllib backend nearly was) | Deepening security pass enumerated both backends + the redirect handler; Unit 4 covers `_check_url_for_ssrf`, `_SSRFSafeRedirectHandler`, and `_block_if_private`. Implementer should grep `urlparse`/`urlsplit` in `_util/net_safety.py` once more before closing the unit. |
| Guarding `is_same_host`/`strip_fragment_query` breaks a non-scraper caller | Blast-radius grep confirms scraper is the only caller; existing suites are the safety net. |
| Touching freshly-merged `_preflight_fetch.py` (#258) causes churn/conflict | Unit 5 is optional and explicitly droppable if non-trivial. |

## Sources & References

- **Origin document:** `docs/brainstorms/2026-05-27-url-parse-never-raises-requirements.md`
- Reference pattern: `content/_preflight_fetch.py` (`_safe_hostname`, `_is_http_url`), PR #258
- Feedback: `[[feedback_urlparse_raises_on_malformed_ipv6]]`, `[[feedback_urllib_request_non_ascii_must_normalize]]`
