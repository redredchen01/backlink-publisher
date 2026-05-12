"""Real-publish verification for backlink-publisher.

After each adapter returns `status="published"` with a `published_url`, the
publish-backlinks dispatcher calls `verify_published()` to independently assert
the article is actually live and contains the article's title + the expected
target-link hrefs. The verification status surfaces as three additive JSONL
fields (`verified`, `verified_at`, `verification_error`) and feeds the final
exit code.

This module is the public surface — channel-specific implementations land in
subsequent units. Unit 1 ships the dispatch skeleton with stubs that return
"not_implemented" outcomes.

Plan: docs/plans/2026-05-12-005-feat-real-publish-verification-plan.md
Brainstorm: docs/brainstorms/2026-05-12-real-publish-verification-requirements.md
"""

from __future__ import annotations

import ipaddress
import re
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .adapters.base import AdapterResult


# Per-channel retry budgets (origin R9 / R10). Hard-coded inline; do not build
# a configurable abstraction until a third adapter with distinct timing lands.
# Each element is the wait (seconds) before that attempt; first is always 0.
# len() = total attempts.
_HTML_RETRY_WAITS_S: tuple[int, ...] = (0, 5, 10, 15)   # 4 attempts, ≤30s wall-clock
_API_RETRY_WAITS_S: tuple[int, ...] = (0,)              # single attempt

# Error-string template for an exhausted retry budget. Pinned as a module
# constant so the dispatcher's lag-counting predicate (Unit 6) imports it
# rather than matching a literal — prevents cross-unit string drift.
_ERR_TRANSIENT_EXHAUSTED = "transient_exhausted: {n}/{n} attempts"

# Verifier_internal_error prefix. Rows whose verification_error starts with
# this prefix roll up into verified_false for exit-code purposes (P0
# resolution: prevents verifier bugs silently masking real failures).
_ERR_INTERNAL_PREFIX = "verifier_internal_error: "

# Response body cap for the HTML channel (bytes). Over-cap → verified=null,
# verification_error="body_too_large".
_MAX_BODY_BYTES = 2_000_000

# Wall-clock budget per HTML-channel fetch attempt (seconds), enforced inside
# the chunked-read loop. Prevents slow-drip DoS where a malicious server
# returns bytes slower than urllib's per-socket timeout.
_MAX_FETCH_WALL_CLOCK_S = 15

# Maximum redirect hops the HTML channel will follow.
_MAX_REDIRECT_HOPS = 5

# Per-socket timeout for HTML-channel fetches. The wall-clock budget above
# is a separate, additive guard enforced inside the chunked-read loop.
_HTML_REQUEST_TIMEOUT_S = 10

# Read chunk size for bounded body reads.
_READ_CHUNK_SIZE = 16_384

# User-Agent header for HTML-channel fetches.
_USER_AGENT = "backlink-publisher/0.2 verifier"

# Cloud-metadata IPv4/IPv6 addresses that fall outside Python's
# is_private/is_link_local/is_reserved detection on some platforms.
# (169.254.169.254 IS link_local for AWS/GCP IPv4, but Azure's WireServer
# at 168.63.129.16 is a routable address that must be explicitly rejected.)
_PRIVATE_METADATA_IPS = frozenset({
    "169.254.169.254",
    "168.63.129.16",
    "fd00:ec2::254",
    "fe80:a9fe:a9fe::fe",
})

# Article-container detection. The HTML-channel parser scopes visible-text
# title-matching and <a href> collection to inside these elements, defending
# against title-in-sidebar and href-in-JSON-blob false positives.
_ARTICLE_CONTAINER_TAGS = frozenset({"article", "main"})

# Tags whose text content is never user-visible — skipped during parsing.
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})

# Centralized per-adapter verification metadata. With only two platforms in
# scope, scattering declarations across adapter modules is premature
# abstraction (scope-guardian trim). When a third platform lands, the cost
# of migrating to per-adapter declarations is mechanical.
_ADAPTER_METADATA: dict[str, dict[str, Any]] = {
    "blogger-api": {
        "channel": "api",
        "allowed_hosts": ("*.blogspot.com", "blogger.com"),
        "allowed_path_patterns": (r"^/\d{4}/\d{2}/.+\.html$",),
        "args": lambda row, result: {
            "blog_id": result._provider_meta["blog_id"],
            "post_id": result._provider_meta["post_id"],
        },
    },
    "medium-api": {
        "channel": "html",
        "allowed_hosts": ("medium.com", "*.medium.com"),
        "allowed_path_patterns": (r"^/@[^/]+/[\w\-]+", r"^/p/[\w]+"),
        "args": lambda row, result: {"url": result.published_url},
    },
    "medium-browser": {
        "channel": "html",
        "allowed_hosts": ("medium.com", "*.medium.com"),
        "allowed_path_patterns": (r"^/@[^/]+/[\w\-]+", r"^/p/[\w]+"),
        "args": lambda row, result: {"url": result.published_url},
    },
    "medium-brave": {
        "channel": "html",
        "allowed_hosts": ("medium.com", "*.medium.com"),
        "allowed_path_patterns": (r"^/@[^/]+/[\w\-]+", r"^/p/[\w]+"),
        "args": lambda row, result: {"url": result.published_url},
    },
}


@dataclass(frozen=True)
class VerificationOutcome:
    """Result of a verification attempt.

    `verified`:
        True  — article asserted live with expected title and links
        False — article URL is wrong, gone, blocked, or content is missing
        None  — verification was skipped or could not be determined (transient
                failure, dry-run, non-published status)

    `verified_at`: ISO-8601 timestamp string when `verified` is bool; None
                   when `verified` is None.

    `verification_error`: short reason string. None on success or when no
                          reason applies. Format conventions:
                            - "dry_run"          (skipped, dry-run mode)
                            - "not_implemented"  (stub path; remove in later units)
                            - "host_not_allowed: <host>"
                            - "host_resolved_to_private_ip: <ip>"
                            - "http_404" / "http_410" / "http_451"
                            - "http_503" / "http_500" / ...
                            - "transient_exhausted: N/N attempts"
                            - "empty_body"
                            - "body_too_large"
                            - "non_article_url: <path>"
                            - "title_missing"
                            - "target_link_missing: <url>"
                            - "missing_provider_meta"
                            - "verifier_internal_error: <repr>"
    """

    verified: bool | None
    verified_at: str | None
    verification_error: str | None


def _now_iso() -> str:
    """Current UTC timestamp in ISO-8601 (matches the dispatcher convention)."""
    return datetime.now(timezone.utc).isoformat()


def _resolve_adapter_metadata(adapter_name: str) -> dict[str, Any]:
    """Look up centralized verification metadata for an adapter.

    Raises KeyError with a helpful message listing supported adapters when
    the name is unknown — the dispatcher should map this to verified=false
    with verification_error="missing_provider_meta" (or similar).
    """
    try:
        return _ADAPTER_METADATA[adapter_name]
    except KeyError:
        supported = ", ".join(sorted(_ADAPTER_METADATA))
        raise KeyError(
            f"no verification metadata for adapter {adapter_name!r}; "
            f"supported: {supported}"
        )


_CREDENTIAL_NEEDLES = ("Bearer", "Authorization", "access_token", "refresh_token")


def _sanitize_exception(exc: BaseException, *, max_len: int = 200) -> str:
    """Stable string form of an exception with credential strings stripped.

    The verifier wraps every internal exception with this so a transitive
    reference to a Google OAuth Credentials object (or similar) cannot leak
    a bearer or refresh token into the JSONL/stderr stream. Also strips
    CR/LF/TAB to defend against log injection.
    """
    cls = type(exc).__name__
    msg = str(exc)
    for needle in _CREDENTIAL_NEEDLES:
        msg = msg.replace(needle, "<redacted>")
    msg = msg.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    if len(msg) > max_len:
        msg = msg[: max_len - 3] + "..."
    return f"{cls}: {msg}"


def verify_published(
    row: dict[str, Any],
    result: AdapterResult,
    *,
    service: Any = None,
) -> VerificationOutcome:
    """Dispatch entry point.

    Returns a `VerificationOutcome` describing whether the article identified
    by `result.published_url` is actually live with the expected content.

    `row` is the source JSONL payload (carries `links`, `title`, `id`).
    `result` is the `AdapterResult` returned by the adapter's publish call.
    `service` is the adapter-built API client (for the Blogger API channel);
    `None` for HTML-channel adapters.

    Skip rules (R3, R4):
      - `_dry_run=True`     → outcome(None, None, "dry_run")
      - `status != published` → outcome(None, None, None)
      - otherwise dispatch by adapter channel.

    Defensive: any exception escaping a channel implementation is wrapped as
    a `verifier_internal_error:` outcome (verified=null) so a verifier bug
    can never abort the batch. The dispatcher rolls these into the
    verified=false count for exit-code purposes.
    """
    try:
        return _dispatch(row, result, service=service)
    except Exception as exc:  # noqa: BLE001 — defensive wrap by design
        return VerificationOutcome(
            verified=None,
            verified_at=None,
            verification_error=f"{_ERR_INTERNAL_PREFIX}{_sanitize_exception(exc)}",
        )


def _dispatch(
    row: dict[str, Any],
    result: AdapterResult,
    *,
    service: Any = None,
) -> VerificationOutcome:
    if result._dry_run:
        return VerificationOutcome(
            verified=None, verified_at=None, verification_error="dry_run"
        )
    if result.status != "published":
        return VerificationOutcome(
            verified=None, verified_at=None, verification_error=None
        )

    metadata = _resolve_adapter_metadata(result.adapter)
    channel = metadata["channel"]
    if channel == "api":
        return _verify_blogger_api(row, result, metadata=metadata, service=service)
    if channel == "html":
        return _verify_html_channel(row, result, metadata=metadata)
    # Defensive: unknown channel would have been caught by _resolve_adapter_metadata.
    return VerificationOutcome(
        verified=None,
        verified_at=None,
        verification_error=f"unknown_channel: {channel}",
    )


# --- Channel implementations (stubs land in Unit 1; real logic in Units 2, 3) ---


# --- HTML channel helpers (Unit 2) ---


def _safe_for_log(s: str | None, *, max_len: int = 256) -> str:
    """Strip control characters and cap length for safe inclusion in log strings.

    Defends against log injection where adapter-supplied or response-derived
    strings (published_url, verification_error, host names) might contain
    CR/LF or terminal control sequences that corrupt downstream log parsing.
    """
    if not s:
        return ""
    out = "".join(c for c in s if c.isprintable() and c not in ("\r", "\n", "\t"))
    if len(out) > max_len:
        out = out[: max_len - 3] + "..."
    return out


def _strict_ssl_context() -> ssl.SSLContext:
    """Strict TLS context with hostname + certificate verification.

    Deliberately NOT inheriting linkcheck.py's lax context: the verifier's
    whole purpose is to assert reality, so an on-path attacker satisfying a
    permissive context would defeat the defense entirely.
    """
    return ssl.create_default_context()


def _normalize_host(host: str | None) -> str | None:
    """Lowercase, strip trailing dot, IDNA-encode-check.

    Returns None for invalid hosts (empty, non-IDNA-encodable, etc.).
    """
    if not host:
        return None
    host = host.lower().rstrip(".")
    if not host:
        return None
    try:
        host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return None
    return host


def _check_host_allowed(host: str, allowlist: tuple[str, ...]) -> bool:
    """Match a normalized host against a wildcard allowlist.

    Wildcard semantics: `*.medium.com` matches `medium.com` exactly OR any
    subdomain (one or more non-empty labels before the base). The label-count
    check defends against tricks like ``attacker.medium.com.evil.com`` and
    ``evilmedium.com``.
    """
    for pattern in allowlist:
        if pattern.startswith("*."):
            base = pattern[2:]
            if host == base:
                return True
            suffix = "." + base
            if host.endswith(suffix):
                prefix = host[: -len(suffix)]
                if prefix and not prefix.startswith("."):
                    return True
        else:
            if host == pattern:
                return True
    return False


def _check_resolved_ip_safe(host: str) -> tuple[bool, str | None]:
    """Resolve `host` to A/AAAA addresses; reject any unsafe address.

    Returns ``(True, None)`` if every resolved address is a routable public
    IP. Returns ``(False, reason)`` on the first unsafe address. The reason
    starts with ``dns_failure:`` when name resolution itself failed (the
    caller treats that as transient, not as a definitive private-IP rejection).
    """
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return False, f"dns_failure: {_safe_for_log(exc.strerror or 'unknown', max_len=64)}"
    except OSError as exc:
        return False, f"dns_failure: {_safe_for_log(str(exc), max_len=64)}"

    for info in infos:
        addr = info[4][0]
        if "%" in addr:
            addr = addr.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, f"host_resolved_to_private_ip: {addr}"
        if addr in _PRIVATE_METADATA_IPS:
            return False, f"host_resolved_to_private_ip: {addr}"
    return True, None


def _check_path_shape(path: str, patterns: tuple[str, ...]) -> bool:
    """Check that `path` matches at least one of the allowed regex patterns.

    Empty patterns tuple → no constraint (returns True). Used as the final-URL
    path-shape allowlist (e.g. rejects ``medium.com/`` and ``medium.com/tag/foo``
    after redirect to confirm we're on an article page, not a homepage).
    """
    if not patterns:
        return True
    return any(re.search(p, path) for p in patterns)


class _RedirectRejected(Exception):
    """Raised by the redirect handler when a redirect target is unsafe."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class _BodyTooLarge(Exception):
    """Raised when the response body exceeds _MAX_BODY_BYTES."""


class _WallClockExceeded(Exception):
    """Raised when reading the body exceeds _MAX_FETCH_WALL_CLOCK_S."""


class _SafeRedirectHandler(HTTPRedirectHandler):
    """Counts redirect hops and re-validates host allowlist + SSRF per hop.

    Per-hop SSRF defense addresses DNS rebinding: an initial allowlisted host
    may resolve to a public IP, then a 301 redirect's target host may
    resolve to a private IP. We re-check on every hop.
    """

    def __init__(self, allowed_hosts: tuple[str, ...]) -> None:
        super().__init__()
        self._allowed_hosts = allowed_hosts
        self.hop_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hop_count += 1
        if self.hop_count > _MAX_REDIRECT_HOPS:
            raise _RedirectRejected("redirect_cap_exceeded")
        parsed = urlparse(newurl)
        if parsed.scheme not in ("http", "https"):
            raise _RedirectRejected(
                f"host_not_allowed: <invalid_scheme:{_safe_for_log(parsed.scheme, max_len=16)}>"
            )
        new_host = _normalize_host(parsed.hostname)
        if not new_host:
            raise _RedirectRejected("host_not_allowed: <unparseable>")
        if not _check_host_allowed(new_host, self._allowed_hosts):
            raise _RedirectRejected(f"host_not_allowed: {_safe_for_log(new_host)}")
        safe, ip_err = _check_resolved_ip_safe(new_host)
        if not safe and ip_err and not ip_err.startswith("dns_failure:"):
            raise _RedirectRejected(ip_err)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_body_bounded(resp: Any) -> bytes:
    """Read a response body up to _MAX_BODY_BYTES with a wall-clock budget.

    Defends against slow-drip DoS where a malicious server feeds bytes just
    under the per-socket timeout to stall the verifier indefinitely while
    staying under the size cap.
    """
    deadline = time.monotonic() + _MAX_FETCH_WALL_CLOCK_S
    buf = bytearray()
    while len(buf) < _MAX_BODY_BYTES:
        if time.monotonic() > deadline:
            raise _WallClockExceeded()
        remaining = _MAX_BODY_BYTES - len(buf)
        chunk = resp.read(min(_READ_CHUNK_SIZE, remaining))
        if not chunk:
            return bytes(buf)
        buf.extend(chunk)
    # Hit the cap. Probe one more byte to decide whether the body was
    # exactly at the cap (rare) or over.
    try:
        extra = resp.read(1)
    except Exception:
        extra = b""
    if extra:
        raise _BodyTooLarge()
    return bytes(buf)


def _fetch_html_once(url: str, allowed_hosts: tuple[str, ...]) -> tuple[int, bytes, str]:
    """Single-attempt HTML fetch.

    Returns ``(status, body, final_url)``. Raises ``_RedirectRejected``,
    ``_BodyTooLarge``, ``_WallClockExceeded``, ``HTTPError``, ``URLError``,
    or other transport-level exceptions for the caller to map.
    """
    redirect = _SafeRedirectHandler(allowed_hosts)
    https = HTTPSHandler(context=_strict_ssl_context())
    opener = build_opener(https, redirect)
    opener.addheaders = [("User-Agent", _USER_AGENT)]

    req = Request(url, method="GET")
    resp = opener.open(req, timeout=_HTML_REQUEST_TIMEOUT_S)
    try:
        status = resp.getcode() or 0
        final_url = resp.geturl() or url
        body = _read_body_bounded(resp)
    finally:
        try:
            resp.close()
        except Exception:
            pass
    return status, body, final_url


class _ArticleScopedCollector(HTMLParser):
    """Collect visible text + href set inside the article container.

    Always captures structural title candidates regardless of container:
    ``<title>`` text, ``<h1>`` text, and ``<meta property="og:title">``.
    Inside the article container (``<article>``, ``<main>``, or Medium's
    ``<section data-field="body">``), collects visible text and every
    ``<a href>`` value. Outside the container, anchors and visible text are
    ignored — this is what defends against title-in-sidebar and
    href-in-JSON-blob false positives.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._article_tag_stack: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._in_h1 = False
        self._h1_done = False

        self.visible_text_chunks: list[str] = []
        self.article_hrefs: set[str] = set()
        self.title_text: str = ""
        self.h1_text: str = ""
        self.og_title: str = ""

    @property
    def _in_article(self) -> bool:
        return bool(self._article_tag_stack)

    @staticmethod
    def _is_article_container(tag: str, attrs_dict: dict[str, str]) -> bool:
        if tag in _ARTICLE_CONTAINER_TAGS:
            return True
        if tag == "section" and attrs_dict.get("data-field") == "body":
            return True
        if tag == "div":
            classes = (attrs_dict.get("class") or "").split()
            if "post-body" in classes:
                return True
        return False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = {k.lower(): (v or "") for k, v in attrs if k}

        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return

        if self._is_article_container(tag, attrs_dict):
            self._article_tag_stack.append(tag)

        if tag == "title":
            self._in_title = True
        elif tag == "h1" and not self._h1_done:
            self._in_h1 = True
        elif tag == "meta":
            prop = (attrs_dict.get("property") or attrs_dict.get("name") or "").lower()
            if prop == "og:title" and not self.og_title:
                self.og_title = attrs_dict.get("content") or ""

        if self._in_article and tag == "a":
            href = attrs_dict.get("href")
            if href:
                self.article_hrefs.add(href)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return

        if tag == "title":
            self._in_title = False
        elif tag == "h1" and self._in_h1:
            self._in_h1 = False
            self._h1_done = True

        if self._article_tag_stack and self._article_tag_stack[-1] == tag:
            self._article_tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title_text += data
        if self._in_h1:
            self.h1_text += data
        if self._in_article:
            self.visible_text_chunks.append(data)


def _parse_and_match_html(
    body: str, expected_title: str, expected_hrefs: list[str]
) -> str | None:
    """Parse HTML body scoped to article container; return error reason or None.

    Title match: case-insensitive substring against ``<title>`` / ``<h1>`` /
    ``og:title`` / visible text inside the article container.

    Link match: every expected href must appear in the ``<a href>`` set
    inside the article container.

    Returns ``None`` on success, or a reason string on the first failure
    (``title_missing`` or ``target_link_missing: <url>``).
    """
    collector = _ArticleScopedCollector()
    try:
        collector.feed(body)
        collector.close()
    except Exception:
        # html.parser is lenient; only catastrophic parser failures land here.
        # Treat as title/link missing rather than re-raising.
        pass

    if expected_title:
        expected_lower = expected_title.lower()
        title_candidates = (
            collector.title_text,
            collector.h1_text,
            collector.og_title,
            "".join(collector.visible_text_chunks),
        )
        if not any(c and expected_lower in c.lower() for c in title_candidates):
            return "title_missing"

    if expected_hrefs:
        for href in expected_hrefs:
            if href not in collector.article_hrefs:
                return f"target_link_missing: {_safe_for_log(href)}"

    return None


def _verify_html_channel(
    row: dict[str, Any],
    result: AdapterResult,
    *,
    metadata: dict[str, Any],
) -> VerificationOutcome:
    """HTML channel verifier (Medium platform).

    Sequence:
      1. URL shape + scheme check.
      2. Host allowlist check (pre-flight, no HTTP yet).
      3. SSRF defense via DNS resolution + IP allowlist.
      4. Retry loop (4 attempts at 0/5/10/15s waits, total ≤30s).
      5. Definitive 4xx short-circuits to verified=false.
      6. After body fetch: re-verify final-URL host + path shape (catches
         redirect chains landing on homepage or off-platform).
      7. Parse + match scoped to article container.
    """
    raw_url = result.published_url or ""
    allowed_hosts: tuple[str, ...] = tuple(metadata.get("allowed_hosts", ()))
    allowed_paths: tuple[str, ...] = tuple(metadata.get("allowed_path_patterns", ()))

    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https"):
        return VerificationOutcome(
            verified=False,
            verified_at=_now_iso(),
            verification_error=(
                f"host_not_allowed: <invalid_scheme:"
                f"{_safe_for_log(parsed.scheme, max_len=16)}>"
            ),
        )

    host = _normalize_host(parsed.hostname)
    if host is None:
        return VerificationOutcome(
            verified=False,
            verified_at=_now_iso(),
            verification_error="host_not_allowed: <unparseable>",
        )
    if not _check_host_allowed(host, allowed_hosts):
        return VerificationOutcome(
            verified=False,
            verified_at=_now_iso(),
            verification_error=f"host_not_allowed: {_safe_for_log(host)}",
        )

    safe, ip_err = _check_resolved_ip_safe(host)
    if not safe and ip_err and not ip_err.startswith("dns_failure:"):
        return VerificationOutcome(
            verified=False,
            verified_at=_now_iso(),
            verification_error=ip_err,
        )
    # DNS failures fall through to the retry loop — they are transient.

    expected_title = (row.get("title") or "").strip()
    expected_hrefs = _verified_link_subset(row)

    last_error: str | None = None
    for attempt_idx, wait_s in enumerate(_HTML_RETRY_WAITS_S):
        if wait_s > 0:
            time.sleep(wait_s)
        try:
            status, body, final_url = _fetch_html_once(raw_url, allowed_hosts)
        except _RedirectRejected as exc:
            return VerificationOutcome(
                verified=False,
                verified_at=_now_iso(),
                verification_error=_safe_for_log(exc.reason),
            )
        except _BodyTooLarge:
            return VerificationOutcome(
                verified=None,
                verified_at=None,
                verification_error="body_too_large",
            )
        except _WallClockExceeded:
            last_error = "transient: wall_clock_exceeded"
            continue
        except HTTPError as exc:
            code = exc.code or 0
            if 400 <= code < 500:
                return VerificationOutcome(
                    verified=False,
                    verified_at=_now_iso(),
                    verification_error=f"http_{code}",
                )
            last_error = f"http_{code}"
            continue
        except URLError as exc:
            reason_name = type(getattr(exc, "reason", exc)).__name__
            last_error = f"transient: {reason_name}"
            continue
        except (TimeoutError, ConnectionError, OSError) as exc:
            last_error = f"transient: {type(exc).__name__}"
            continue

        # Got a 2xx (or unexpected 3xx that escaped the handler). Re-check
        # final URL host + path shape — defends against redirect chains
        # landing on the homepage or off-platform.
        final_parsed = urlparse(final_url)
        final_host = _normalize_host(final_parsed.hostname)
        if final_host is None or not _check_host_allowed(final_host, allowed_hosts):
            return VerificationOutcome(
                verified=False,
                verified_at=_now_iso(),
                verification_error=(
                    f"host_not_allowed: "
                    f"{_safe_for_log(final_host or '<unparseable>')}"
                ),
            )
        if not _check_path_shape(final_parsed.path or "/", allowed_paths):
            return VerificationOutcome(
                verified=False,
                verified_at=_now_iso(),
                verification_error=f"non_article_url: {_safe_for_log(final_parsed.path or '/')}",
            )

        if not (200 <= status < 300):
            # Unexpected non-redirect, non-error response. Treat as transient
            # and retry (e.g., a stray 304 or 1xx). HTTPError already handled
            # 4xx/5xx above.
            last_error = f"http_{status}"
            continue

        if not body:
            last_error = "empty_body"
            continue

        body_str = body.decode("utf-8", errors="replace")
        mismatch = _parse_and_match_html(body_str, expected_title, expected_hrefs)
        if mismatch is None:
            return VerificationOutcome(
                verified=True,
                verified_at=_now_iso(),
                verification_error=None,
            )
        return VerificationOutcome(
            verified=False,
            verified_at=_now_iso(),
            verification_error=mismatch,
        )

    # Retries exhausted. Map to a stable verification_error.
    n = len(_HTML_RETRY_WAITS_S)
    if last_error and (last_error.startswith("http_5") or last_error == "empty_body"):
        return VerificationOutcome(
            verified=None,
            verified_at=None,
            verification_error=last_error,
        )
    return VerificationOutcome(
        verified=None,
        verified_at=None,
        verification_error=last_error or _ERR_TRANSIENT_EXHAUSTED.format(n=n),
    )


def _verified_link_subset(row: dict[str, Any]) -> list[str]:
    """Return the URLs from row['links'] that must be present on the published page.

    Default subset: entries whose `kind` is `target` or `main_domain`. Other
    kinds (supporting, extra, category, detail) are not required because the
    project's core SEO value is the target backlinks. Empty subset means
    only the title check applies (R6 fallthrough).
    """
    links = row.get("links") or []
    subset: list[str] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        if link.get("kind") in ("target", "main_domain"):
            url = link.get("url")
            if isinstance(url, str) and url:
                subset.append(url)
    return subset


class _HrefCollector(HTMLParser):
    """Collect every <a href="..."> attribute value from an HTML body."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.hrefs.add(value)


def _extract_hrefs(html_body: str) -> set[str]:
    """Parse HTML and return the set of href values from <a> tags.

    Used by both the Blogger API channel (parsing `response['content']`) and
    the HTML channel (Unit 2). Malformed HTML does not raise — the parser
    salvages what it can. Empty/None input returns an empty set.
    """
    if not html_body:
        return set()
    collector = _HrefCollector()
    try:
        collector.feed(html_body)
        collector.close()
    except Exception:
        # html.parser is lenient by default but very malformed input can
        # still raise. Treat as "no anchors found" — the caller will
        # surface this as a verification failure if links were expected.
        pass
    return collector.hrefs


def _http_status_outcome(status: int, ts: str) -> VerificationOutcome:
    """Map an HTTP status from a verifier-side fetch to a VerificationOutcome.

    Definitive client failures (404/410/451 and other 4xx) → verified=false:
    the URL is wrong, gone, or blocked, which is evidence of fabrication or
    silent publish failure — not lag.

    Server failures (5xx) → verified=null: cannot distinguish lag from real
    failure; warrants human re-check rather than a hard fail.
    """
    if 400 <= status < 500:
        return VerificationOutcome(
            verified=False,
            verified_at=ts,
            verification_error=f"http_{status}",
        )
    if 500 <= status < 600:
        return VerificationOutcome(
            verified=None,
            verified_at=None,
            verification_error=f"http_{status}",
        )
    # Other unexpected statuses (e.g. unexpected 2xx with empty body, 1xx, 3xx
    # not following) — treat as transient.
    return VerificationOutcome(
        verified=None,
        verified_at=None,
        verification_error=f"http_{status}",
    )


def _verify_blogger_api(
    row: dict[str, Any],
    result: AdapterResult,
    *,
    metadata: dict[str, Any],
    service: Any,
) -> VerificationOutcome:
    """Blogger API channel: posts.get + structured title/content match.

    Reuses the existing Blogger API service (built by the dispatcher via
    blogger_api._get_service) to fetch the just-published post by its
    blogId/postId from the insert response. No retry budget — Blogger's
    read-after-write on the same API surface is consistent enough that a
    single attempt is the right policy.
    """
    if service is None:
        return VerificationOutcome(
            verified=None,
            verified_at=None,
            verification_error=f"{_ERR_INTERNAL_PREFIX}service_not_provided",
        )

    # Extract structured verifier args. KeyError here means
    # BloggerAPIAdapter failed to capture postId/blogId from posts.insert —
    # surface as missing_provider_meta (rolled up to verified=false by the
    # dispatcher), not as a verifier_internal_error.
    try:
        args = metadata["args"](row, result)
    except KeyError:
        return VerificationOutcome(
            verified=None,
            verified_at=None,
            verification_error="missing_provider_meta",
        )

    blog_id = args.get("blog_id", "")
    post_id = args.get("post_id", "")
    if not blog_id or not post_id:
        return VerificationOutcome(
            verified=None,
            verified_at=None,
            verification_error="missing_provider_meta",
        )

    try:
        post = service.posts().get(blogId=blog_id, postId=post_id).execute()
    except Exception as exc:
        # Map googleapiclient.errors.HttpError by status; other exceptions
        # are transient transport-level failures.
        try:
            from googleapiclient.errors import HttpError  # type: ignore
        except ImportError:  # pragma: no cover - googleapiclient is a project dep
            HttpError = ()  # type: ignore[assignment]
        if isinstance(exc, HttpError):  # type: ignore[arg-type]
            status = getattr(getattr(exc, "resp", None), "status", 0) or 0
            return _http_status_outcome(status, _now_iso())
        # Transport errors (TimeoutError, ConnectionError, etc.) → null.
        return VerificationOutcome(
            verified=None,
            verified_at=None,
            verification_error=f"transient: {type(exc).__name__}",
        )

    expected_title = (row.get("title") or "").strip()
    actual_title = post.get("title") or ""
    if expected_title and expected_title.lower() not in actual_title.lower():
        return VerificationOutcome(
            verified=False,
            verified_at=_now_iso(),
            verification_error="title_missing",
        )

    expected_hrefs = _verified_link_subset(row)
    if expected_hrefs:
        found_hrefs = _extract_hrefs(post.get("content") or "")
        for expected in expected_hrefs:
            if expected not in found_hrefs:
                return VerificationOutcome(
                    verified=False,
                    verified_at=_now_iso(),
                    verification_error=f"target_link_missing: {expected}",
                )

    return VerificationOutcome(
        verified=True,
        verified_at=_now_iso(),
        verification_error=None,
    )
