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

from urllib.parse import (
    ParseResult,
    parse_qsl,
    quote,
    urlencode,
    urljoin,
    urlparse,
    urlsplit,
    urlunparse,
    urlunsplit,
)


def safe_urlparse(url: object) -> ParseResult | None:
    """``urlparse`` that never raises — returns ``None`` on malformed/non-str input.

    ``urlparse`` raises ``ValueError`` on a malformed authority (an unterminated
    IPv6 literal like ``http://[invalid``) and ``AttributeError`` on a non-``str``
    argument. Both are folded into a ``None`` return so callers on never-raises
    code paths can branch instead of crashing. See
    ``[[feedback_urlparse_raises_on_malformed_ipv6]]``.
    """
    if not isinstance(url, str) or not url:
        return None
    try:
        return urlparse(url)
    except ValueError:
        return None


def safe_hostname(url: object) -> str | None:
    """``urlparse(url).hostname`` that never raises (malformed/non-str → ``None``)."""
    parsed = safe_urlparse(url)
    return parsed.hostname if parsed is not None else None


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
    parsed_a = safe_urlparse(a)
    parsed_b = safe_urlparse(b)
    if parsed_a is None or parsed_b is None:
        return False
    netloc_a = parsed_a.netloc
    netloc_b = parsed_b.netloc
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
    ``""`` when ``href`` is empty so callers can filter cleanly. ``urljoin``
    raises ``ValueError`` on a malformed authority (unterminated IPv6) in either
    ``base`` or ``href``; that is folded to ``""`` so a single malformed scraped
    href is skipped, not fatal to the whole scrape (Plan 2026-05-27-006 R6).
    """
    if not href:
        return ""
    try:
        return urljoin(base, href)
    except ValueError:
        return ""


def strip_fragment_query(url: str) -> str:
    """Return ``url`` with fragment AND query removed (path preserved).

    Malformed input (unterminated IPv6) returns ``""`` instead of raising, so a
    scraped href that cannot be parsed is skipped downstream (the empty result
    makes ``is_same_host`` return ``False``) — Plan 2026-05-27-006 R8.
    """
    if not url:
        return ""
    parsed = safe_urlparse(url)
    if parsed is None:
        return ""
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        "",
        "",
    ))


# Default ports stripped during canonicalization (R17 dedup key support).
_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}


def canonicalize_url(url: str) -> str:
    """Return a canonical form of ``url`` for use as a dedup key.

    Plan ref: ``docs/plans/2026-05-18-004-feat-event-substrate-corpus-plan.md`` U3 + R17.

    Used by the event-substrate projector (U4) and ``bp-events-rebuild`` (U7) to
    decide whether two ``live_url`` strings refer to the same published article.
    Aggressive enough to collapse common formatting drift; conservative enough
    not to silently merge URLs that point to different resources.

    Rules:
    - Lowercase ``scheme`` and ``host``
    - Strip default ports (``:80`` for http, ``:443`` for https)
    - Strip the trailing ``/`` from the path EXCEPT when the path is the root ``/``
    - Drop all ``utm_*`` query parameters; keep other query keys, sorted by key,
      preserving the original order of duplicate values within a single key
    - Drop the fragment entirely

    Non-http(s) schemes (e.g. ``mailto:``, ``ftp://``) are returned unchanged —
    the dedup-key use case is not meaningful for them and there is no agreed
    canonicalization rule in this codebase.

    The function is idempotent: ``canonicalize_url(canonicalize_url(u)) ==
    canonicalize_url(u)`` for any input.
    """
    if not url:
        return url

    parts = urlsplit(url)
    scheme = parts.scheme.lower()

    # Non-http(s): pass through. We don't want to touch mailto:, ftp://, etc.
    if scheme not in _DEFAULT_PORTS:
        return url

    # netloc = userinfo@host:port. We lowercase host but preserve userinfo as-is
    # (basic-auth credentials in URLs are a Threat-Model T1 concern, not a
    # canonicalization concern — scrubber removes them in U6).
    host = parts.hostname or ""
    host_lower = host.lower()
    port = parts.port
    userinfo_at = ""
    if "@" in parts.netloc:
        userinfo_at = parts.netloc.split("@", 1)[0] + "@"

    if port is None or port == _DEFAULT_PORTS[scheme]:
        netloc = f"{userinfo_at}{host_lower}"
    else:
        netloc = f"{userinfo_at}{host_lower}:{port}"

    # Path: strip trailing slash except for root "/".
    path = parts.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    elif path == "":
        # Empty path is treated equivalently to "" by urlunsplit — leave alone.
        pass

    # Query: drop utm_*, sort the remaining by key, preserve duplicate-value order.
    if parts.query:
        # keep_blank_values=True so "?b=" survives — semantically distinct from
        # "?b" (which still parses to b=""), and we don't want to silently drop.
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        kept = [(k, v) for k, v in pairs if not k.lower().startswith("utm_")]
        # Stable sort by key only — within a key, original order survives.
        kept.sort(key=lambda kv: kv[0])
        query = urlencode(kept) if kept else ""
    else:
        query = ""

    return urlunsplit((scheme, netloc, path, query, ""))


# RFC 3986 safe-char sets for the two transport-time encoders below.
# Path keeps the pchar-compatible reserved chars; both keep ``%`` so that
# already-percent-encoded inputs survive a second pass unchanged (idempotency).
_FETCH_PATH_SAFE = "/:@%"
_FETCH_QUERY_SAFE = "=&?/:@,+%"


def normalize_url_for_fetch(url: str) -> str:
    """Return *url* in an ASCII-safe form ``urllib.request.urlopen`` will accept.

    Defends ``linkcheck.verify`` and ``linkcheck.http`` against URLs that
    legitimately carry non-ASCII characters — Velog Korean ``@username``
    handles, CJK ``url_slug`` values from Hashnode / Velog — which crash the
    stdlib HTTP client at request-line encoding time
    (``'ascii' codec can't encode characters``).

    Transformation:

    - Hostname → IDNA (``xn--…``) when it contains non-ASCII labels.
      Falls back to the original host if IDNA refuses (e.g. label too long,
      reserved character) so callers still get a structured fetch failure
      from the network layer rather than an exception here.
    - Userinfo and port → preserved byte-for-byte.
    - Path → percent-encoded with ``%`` in the safe set, so already-encoded
      sequences like ``%E1%84%82`` are not double-encoded to ``%25E1%2584%2582``.
    - Query → percent-encoded with reserved query delimiters
      (``=``, ``&``, ``?``, ``,``, ``+``) in the safe set, plus ``%`` for the
      same idempotency reason.
    - Fragment → dropped. Fragments never travel over the wire and the two
      affected fetch sites both ignore them today.

    Non-``http(s)`` schemes and empty strings pass through unchanged, matching
    the convention :func:`canonicalize_url` follows in this module.

    The function is idempotent:
    ``normalize_url_for_fetch(normalize_url_for_fetch(u)) == normalize_url_for_fetch(u)``
    for any input that is itself a valid ASCII-safe URL produced by this
    function.
    """
    if not url:
        return url

    # Fast path: already ASCII-clean URLs round-trip byte-for-byte. Avoids
    # touching anything Velog/Hashnode/Medium did not actually break, and
    # keeps the common case allocation-free past one encode attempt.
    try:
        url.encode("ascii")
        return url
    except UnicodeEncodeError:
        pass

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return url

    host = parts.hostname or ""
    try:
        ascii_host = host.encode("idna").decode("ascii") if host else ""
    except UnicodeError:
        ascii_host = host

    userinfo_at = ""
    if "@" in parts.netloc:
        userinfo_at = parts.netloc.split("@", 1)[0] + "@"

    netloc = f"{userinfo_at}{ascii_host}"
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"

    path = quote(parts.path, safe=_FETCH_PATH_SAFE)
    query = quote(parts.query, safe=_FETCH_QUERY_SAFE)

    # Fragment intentionally dropped — never reaches the wire and the two
    # affected fetch sites do not depend on it.
    return urlunsplit((parts.scheme, netloc, path, query, ""))
