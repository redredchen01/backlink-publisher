"""URL validation and manipulation utilities for the work-themed backlinks path.

Shared by WebUI form validators, config TOML parsers, and the work_scraper.
Stdlib-only — no third-party deps. See Plan 2026-05-13-004 Unit 1.

Conventions:
- All validators return ``str | None``: normalized URL on success, ``None`` on
  failure. Callers attach domain-specific error messages.
- Normalization preserves scheme + host case as parsed; ``is_same_host`` does
  the case-insensitive comparison locally to keep validators idempotent.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse, urlunparse


def validate_main_domain_url(url: str | None) -> str | None:
    """Validate a main domain URL — https + host-root path + trailing slash.

    Rules:
    - Must be ``https://`` (http rejected)
    - Must have a non-empty host
    - Path must be empty or ``"/"`` (root only — no ``/foo``, ``/foo/``)
    - No fragment or query string
    - Trailing slash is added when missing

    Returns the normalized URL (always ends with ``/``) or ``None`` on failure.
    """
    if not url:
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        return None
    if not parsed.netloc:
        return None
    if parsed.fragment or parsed.query:
        return None
    if parsed.path not in ("", "/"):
        return None
    return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


def validate_https_url(url: str | None) -> str | None:
    """Validate any https URL — https only, path/query unrestricted.

    Used for ``list_url`` and ``work_urls`` where deep paths are expected.
    Drops the fragment on normalization (anchor fragments are never useful
    for outbound backlinks). Path defaults to ``"/"`` when empty.

    Returns the normalized URL or ``None`` on failure.
    """
    if not url:
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        return None
    if not parsed.netloc:
        return None
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path or "/",
        parsed.params,
        parsed.query,
        "",
    ))


def is_same_host(a: str, b: str) -> bool:
    """Compare hosts of two URLs (case-insensitive, ``www.`` prefix ignored).

    Port comparison is strict: ``https://site.com`` and ``https://site.com:8443``
    are NOT the same host. Returns ``False`` if either input is empty/None or
    cannot be parsed into a netloc.
    """
    if not a or not b:
        return False
    netloc_a = urlparse(a).netloc
    netloc_b = urlparse(b).netloc
    if not netloc_a or not netloc_b:
        return False
    return _normalize_host_for_compare(netloc_a) == _normalize_host_for_compare(netloc_b)


def _normalize_host_for_compare(netloc: str) -> str:
    """Lowercase host + strip leading ``www.``; preserve port."""
    host = netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def absolutize(base: str, href: str) -> str:
    """Resolve a possibly-relative ``href`` against ``base``.

    Wraps :func:`urllib.parse.urljoin` with empty-input safety. Returns
    ``""`` when ``href`` is empty so callers can filter cleanly.
    """
    if not href:
        return ""
    return urljoin(base, href)


def strip_fragment_query(url: str) -> str:
    """Return ``url`` with fragment AND query removed (path preserved)."""
    if not url:
        return ""
    parsed = urlparse(url)
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        "",
        "",
    ))
