"""URL reachability checker with retry logic for the backlink pipeline."""

from __future__ import annotations

import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from backlink_publisher._util.errors import ExternalServiceError
from backlink_publisher._util.logger import opencli_logger

REQUEST_TIMEOUT = 10  # seconds
MAX_CONCURRENT = 10
ACCEPTABLE_CODES = {200, 301, 302}
MAX_RETRIES = 2
RETRY_DELAY = 1  # seconds


def _ssl_context() -> ssl.SSLContext:
    return _SSL_CTX

_SSL_CTX: ssl.SSLContext = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _check_url_once(url: str) -> tuple[bool, str | None]:
    """Single attempt to check a URL. Returns (reachable, error_message)."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False, f"invalid URL: {url}"

    # Try HEAD first
    try:
        req = Request(url, method="HEAD")
        req.add_header("User-Agent", "backlink-publisher/0.1 linkcheck")
        resp = urlopen(req, timeout=REQUEST_TIMEOUT, context=_ssl_context())
        code = resp.getcode()
        if code in ACCEPTABLE_CODES:
            return True, None
    except Exception:
        pass

    # Fallback to GET
    try:
        req = Request(url, method="GET")
        req.add_header("User-Agent", "backlink-publisher/0.1 linkcheck")
        resp = urlopen(req, timeout=REQUEST_TIMEOUT, context=_ssl_context())
        code = resp.getcode()
        if code in ACCEPTABLE_CODES:
            return True, None
        return False, f"HTTP {code}"
    except Exception as exc:
        return False, str(exc)


def _check_url_with_retry(url: str) -> tuple[str, bool, str | None]:
    """Check a URL with retry logic. Returns (url, reachable, error_message)."""
    # Indirect lookup via the legacy ``backlink_publisher.linkcheck`` module
    # so ``patch("backlink_publisher.linkcheck._check_url_once", ...)`` in
    # tests intercepts. The Unit 6 split moved ``_check_url_once`` from
    # ``linkcheck.py`` (where the patch worked module-internally) into
    # ``linkcheck/http.py`` (where a captured reference in
    # ``linkcheck/__init__.py`` no longer routes through the patched
    # attribute). Same shim pattern as Unit 5 ``config/writer.py``.
    from backlink_publisher import linkcheck as _legacy
    last_error = "unknown error"
    for attempt in range(MAX_RETRIES + 1):
        reachable, error = _legacy._check_url_once(url)
        if reachable:
            return url, True, None
        last_error = error or "unknown error"
        if attempt < MAX_RETRIES:
            opencli_logger.debug(
                f"Retry {attempt + 1}/{MAX_RETRIES} for {url}: {last_error}"
            )
            time.sleep(RETRY_DELAY * (attempt + 1))

    return url, False, last_error


def check_url(url: str) -> tuple[bool, str | None]:
    """Check a single URL with retry; return ``(reachable, error_message)``.

    Additive public wrapper around :func:`_check_url_with_retry`. Unlike
    :func:`check_urls_strict`, this never raises — callers (e.g. the publish-
    time per-row reachability gate in plan 2026-05-14-001 Unit 5) get a
    tuple and decide their own continue/abort policy.
    """
    _, reachable, error = _check_url_with_retry(url)
    return reachable, error


def check_urls(urls: list[str]) -> dict[str, tuple[bool, str | None]]:
    """Check reachability of multiple URLs concurrently with retries.

    Returns a dict mapping URL -> (reachable, error_message).
    """
    results: dict[str, tuple[bool, str | None]] = {}
    deduplicated = list(dict.fromkeys(urls))  # preserve order, deduplicate

    with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT, len(deduplicated) or 1)) as pool:
        futures = {pool.submit(_check_url_with_retry, url): url for url in deduplicated}
        for future in as_completed(futures):
            url, reachable, error = future.result()
            results[url] = (reachable, error)
    return results


def check_urls_strict(urls: list[str]) -> None:
    """Check reachability and raise on any unreachable URL.

    Skips obviously invalid URLs (non-HTTP) without failing.
    """
    if not urls:
        return
    # Filter out non-http URLs for checking
    http_urls = [u for u in urls if u.startswith("http://") or u.startswith("https://")]
    if not http_urls:
        return

    results = check_urls(http_urls)
    failures = [(url, err) for url, (ok, err) in results.items() if not ok]
    if failures:
        url, err = failures[0]
        raise ExternalServiceError(f"unreachable URL: {url}" + (f" ({err})" if err else ""))