"""URL content-fetch gate for the backlink pipeline.

Verifies that a URL returns HTTP 200 and parses out a non-empty ``<title>`` or
``og:title`` element before the URL is allowed into a published backlink
article. Catches the failure class generalised by PR #19 / plan
``docs/plans/2026-05-14-007-feat-url-content-fetch-gate-plan.md``: synthesized
or stale URLs that look reachable to the HEAD-only ``linkcheck.check_url`` but
serve a 4xx, soft empty body, CAPTCHA interstitial, or Cloudflare challenge
page on full GET.

Sibling to ``linkcheck`` (reachability only) and ``work_scraper`` (deeper
scrape with SSRF defence). Not a replacement for either — this module is
deliberately the smallest "real GET + title check" surface and stays
process-scope in-memory only.

Public surface
--------------
- :func:`verify_url_has_content` — single URL check with retry, returns
  ``(ok, reason, title)``.
- :func:`verify_urls_batch` — concurrent batch (default 5 workers) with
  in-run cache; same return shape per URL.
- :func:`reset_cache` — test hook; clears the process-scope memoization.

Cache semantics: results (success AND failure) are cached for the lifetime of
the importing process. A 404'd URL does not get re-fetched within the same
plan-backlinks invocation. Operators must either restart the process or call
:func:`reset_cache` (tests) to invalidate.
"""

from __future__ import annotations

import socket
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .logger import opencli_logger

#: Wall-clock budget per single GET attempt. Roughly matches ``linkcheck``'s
#: REQUEST_TIMEOUT so a row's combined plan-time HTTP doesn't drift wildly.
FETCH_TIMEOUT: int = 10

#: Retries on transient failures (timeout / 5xx / network). 4xx and
#: ``http_200_no_title`` are not retried — the result is structurally stable.
MAX_RETRIES: int = 2

#: Per-attempt body cap. Larger responses are rejected with ``body_too_large``
#: rather than parsed — protects against accidental binary downloads.
MAX_BODY_BYTES: int = 1_000_000

#: User-Agent identifies this fetcher distinctly from ``linkcheck``'s probe so
#: target sites can rate-limit / allowlist the two independently.
USER_AGENT: str = "backlink-publisher/0.1 content-fetch"

#: Loose TLS context (matches ``linkcheck``'s default — self-signed and
#: expired certs are tolerated because backlink targets historically include
#: rough indie sites).
_SSL_CTX: ssl.SSLContext = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


CheckResult = tuple[bool, Optional[str], Optional[str]]
#: ``(ok, reason, title)``. ``reason`` is ``None`` on success and one of the
#: stable strings documented in the module docstring otherwise. ``title`` is
#: the extracted text on success (stripped, non-empty) or ``None`` on failure.

_CACHE: dict[str, CheckResult] = {}


def reset_cache() -> None:
    """Clear the in-run cache. Tests call this between scenarios; production
    code should not need it (process restart clears the cache naturally).
    """
    _CACHE.clear()


def _is_transient(reason: str) -> bool:
    """Return True for failure reasons safe to retry. 4xx and 200-no-title
    are not transient — the page state is structurally stable.
    """
    return reason in {"timeout", "network_error", "http_5xx"}


def _extract_title(body: bytes) -> Optional[str]:
    """Parse ``body`` as HTML and return the first non-empty title element.

    Looks for ``<meta property="og:title">`` first (typically richer / more
    accurate on modern sites), then falls back to ``<title>``. Returns
    ``None`` if neither element is present or both are empty after strip.
    """
    try:
        soup = BeautifulSoup(body, "html.parser")
    except Exception:  # noqa: BLE001 — bs4 is permissive but a malformed
        # binary payload can still trip the underlying parser.
        return None

    og = soup.find("meta", attrs={"property": "og:title"})
    if og is not None:
        content = og.get("content", "")
        if content and content.strip():
            return content.strip()

    title_tag = soup.find("title")
    if title_tag is not None and title_tag.text:
        stripped = title_tag.text.strip()
        if stripped:
            return stripped

    return None


def _check_once(url: str) -> CheckResult:
    """Single GET attempt. Returns the canonical CheckResult; never raises."""
    req = Request(url, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    try:
        resp = urlopen(req, timeout=FETCH_TIMEOUT, context=_SSL_CTX)
    except HTTPError as exc:
        code = exc.code
        if 400 <= code < 500:
            return False, f"http_{code}", None
        if 500 <= code < 600:
            return False, "http_5xx", None
        return False, f"http_{code}", None
    except socket.timeout:
        return False, "timeout", None
    except URLError as exc:
        reason_obj = getattr(exc, "reason", None)
        if isinstance(reason_obj, socket.timeout):
            return False, "timeout", None
        return False, "network_error", None
    except Exception:  # noqa: BLE001
        return False, "network_error", None

    code = resp.getcode()
    if code != 200:
        if 400 <= code < 500:
            return False, f"http_{code}", None
        if 500 <= code < 600:
            return False, "http_5xx", None
        return False, f"http_{code}", None

    try:
        body = resp.read(MAX_BODY_BYTES + 1)
    except Exception:  # noqa: BLE001
        return False, "network_error", None
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass

    if len(body) > MAX_BODY_BYTES:
        return False, "body_too_large", None

    title = _extract_title(body)
    if not title:
        return False, "http_200_no_title", None
    return True, None, title


def _is_valid_http_url(url: str) -> bool:
    """Cheap structural check: scheme is http/https and netloc is non-empty.
    Run before any network attempt so callers get a deterministic
    ``invalid_url`` rather than a flaky network error for malformed input.
    """
    if not isinstance(url, str) or not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    return True


def verify_url_has_content(url: str) -> CheckResult:
    """Verify ``url`` returns HTTP 200 and a parseable non-empty title.

    Cached: subsequent calls with the same URL return the cached result
    (positive or negative) without re-fetching. Use :func:`reset_cache` to
    invalidate during tests.

    Returns
    -------
    (ok, reason, title)
        ``ok`` is ``True`` only when HTTP status is 200, the body parses, and
        either ``<meta property="og:title">`` or ``<title>`` resolves to a
        non-empty stripped string. ``reason`` carries the failure category
        on ``ok=False`` and is ``None`` on success. ``title`` is the
        extracted string on success and ``None`` otherwise.
    """
    cached = _CACHE.get(url)
    if cached is not None:
        return cached

    if not _is_valid_http_url(url):
        result: CheckResult = (False, "invalid_url", None)
        _CACHE[url] = result
        return result

    last_result: CheckResult = (False, "network_error", None)
    for attempt in range(MAX_RETRIES + 1):
        ok, reason, title = _check_once(url)
        if ok:
            last_result = (True, None, title)
            break
        last_result = (False, reason, None)
        if reason is None or not _is_transient(reason):
            break
        if attempt < MAX_RETRIES:
            opencli_logger.debug(
                f"content_fetch retry {attempt + 1}/{MAX_RETRIES} for {url}: {reason}"
            )

    _CACHE[url] = last_result
    return last_result


def verify_urls_batch(
    urls: list[str], max_workers: int = 5,
) -> dict[str, CheckResult]:
    """Verify a batch of URLs concurrently and return a per-URL result dict.

    Deduplicates the input, consults the cache, submits cache-miss URLs to a
    bounded ``ThreadPoolExecutor``, and merges the results. Each call to
    :func:`verify_url_has_content` inside the workers updates the shared cache,
    so a subsequent batch (or single-URL call) seeing the same URL hits the
    cached result.

    Parameters
    ----------
    urls : list[str]
        Candidate URLs. Order is not preserved in the dict; callers map
        results back to their positions via the URL keys.
    max_workers : int, default 5
        Concurrency cap. The default matches ``linkcheck.check_urls`` and is
        gentle enough that batches of 6–10 URLs overlap without overwhelming
        target sites.

    Returns
    -------
    dict[str, CheckResult]
        One entry per distinct URL. Caller-side duplicates collapse to a
        single entry.
    """
    if not urls:
        return {}

    distinct = list(dict.fromkeys(urls))
    misses = [u for u in distinct if u not in _CACHE]
    if misses:
        workers = min(max_workers, max(1, len(misses)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(verify_url_has_content, u): u for u in misses}
            for fut in as_completed(futures):
                # verify_url_has_content writes to _CACHE; we just need to
                # drain the future so exceptions (which shouldn't escape)
                # surface in tests rather than swallow.
                try:
                    fut.result()
                except Exception:  # noqa: BLE001
                    url = futures[fut]
                    _CACHE.setdefault(url, (False, "network_error", None))

    return {u: _CACHE[u] for u in distinct}
