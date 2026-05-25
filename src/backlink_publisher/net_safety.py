"""Network safety primitives — SSRF defense, TLS hardening, credential sanitizer.

Extracted from the closed PR #1 ``verifier.py`` (2026-05-14) as standalone
helpers so ``linkcheck.py``, ``verify_publish.py``, or any future verifier can
adopt them without re-introducing the V1 module.

Plan: ``docs/plans/2026-05-14-005-feat-v1-verifier-asset-extraction-plan.md``
Source: ``origin/pr/1:src/backlink_publisher/verifier.py``
"""

from __future__ import annotations

import ipaddress
import re
import socket
import ssl
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler


# ── module-level constants ────────────────────────────────────────────────

# Cloud-metadata IPs that fall outside Python's ipaddress private/link-local
# detection on some platforms.
_PRIVATE_METADATA_IPS = frozenset({
    "169.254.169.254",
    "168.63.129.16",
    "fd00:ec2::254",
    "fe80:a9fe:a9fe::fe",
})

# Network ranges classified as "public" by Python's stdlib but practically
# internal (carrier-grade NAT, IPv6 6to4 anycast, Teredo).
_UNSAFE_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),    # RFC 6598 CGNAT
    ipaddress.ip_network("192.88.99.0/24"),   # 6to4 relay anycast (deprecated)
    ipaddress.ip_network("2002::/16"),         # 6to4 prefix
    ipaddress.ip_network("2001::/32"),         # Teredo
)

_MAX_REDIRECT_HOPS = 5

# User-Agent for SSRF-protected fetches.
_USER_AGENT = "backlink-publisher/0.2 safety"

# Per-socket timeout for SSRF-protected fetches.
_REQUEST_TIMEOUT_S = 10


# ── exception sentinels ───────────────────────────────────────────────────


class RedirectRejected(Exception):
    """Raised by ``SafeRedirectHandler`` when a redirect target is unsafe."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


# ── safe-for-log helpers ──────────────────────────────────────────────────


# Literal substrings to redact case-insensitively from exception messages.
_CREDENTIAL_NEEDLES = ("bearer", "authorization", "access_token", "refresh_token")

# Token-shape regexes to redact. Matches are replaced with "<redacted>".
_CREDENTIAL_PATTERNS = (
    re.compile(r"ya29\.[A-Za-z0-9._\-]+"),                        # Google OAuth access token
    re.compile(r"1//[A-Za-z0-9_\-]+"),                            # Google refresh token
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # JWT
    re.compile(r"sk-[A-Za-z0-9\-_]{16,}"),                        # OpenAI / Stripe-style key
    re.compile(r"AIza[A-Za-z0-9_\-]{30,}"),                       # Google API key
)


def safe_for_log(s: str | None, *, max_len: int = 256) -> str:
    """Strip control characters and cap length for safe log inclusion.

    Defends against log injection where adapter-supplied or response-derived
    strings might contain CR/LF or terminal control sequences.
    """
    if not s:
        return ""
    out = "".join(c for c in s if c.isprintable() and c not in ("\r", "\n", "\t"))
    if len(out) > max_len:
        out = out[: max_len - 3] + "..."
    return out


def sanitize_exception(exc: BaseException, *, max_len: int = 200) -> str:
    """Stable string form of an exception with credential strings stripped.

    Redacts known token shapes (Google OAuth, JWT, sk-prefixed keys, Google
    API keys) plus a case-insensitive substring sweep for English credential
    needles. Also strips CR/LF/TAB to defend against log injection.
    """
    cls = type(exc).__name__
    msg = str(exc)
    for pattern in _CREDENTIAL_PATTERNS:
        msg = pattern.sub("<redacted>", msg)
    for needle in _CREDENTIAL_NEEDLES:
        msg = re.sub(re.escape(needle), "<redacted>", msg, flags=re.IGNORECASE)
    msg = msg.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    if len(msg) > max_len:
        msg = msg[: max_len - 3] + "..."
    return f"{cls}: {msg}"


# ── SSRF primitives ──────────────────────────────────────────────────────


def strict_ssl_context() -> ssl.SSLContext:
    """Strict TLS context with hostname + certificate verification.

    NOT inherited from ``linkcheck.py``'s lax context: the verifier's purpose
    is to assert reality, so an on-path attacker satisfying a permissive
    context would defeat the defense entirely.
    """
    return ssl.create_default_context()


def normalize_host(host: str | None) -> str | None:
    """Lowercase, strip trailing dot, IDNA-encode-check.

    Returns ``None`` for invalid hosts (empty, non-IDNA-encodable, etc.).
    """
    if not host:
        return None
    host = host.strip().lower().rstrip(".")
    if not host:
        return None
    try:
        host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return None
    return host


def check_host_allowed(host: str, allowlist: tuple[str, ...]) -> bool:
    """Match a normalized host against a wildcard allowlist.

    Wildcard semantics: ``*.medium.com`` matches ``medium.com`` exactly OR
    any subdomain (one or more non-empty labels before the base). The
    label-count check defends against tricks like
    ``attacker.medium.com.evil.com`` and ``evilmedium.com``.
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


def check_resolved_ip_safe(host: str) -> tuple[bool, str | None]:
    """Resolve ``host`` to A/AAAA addresses; reject any unsafe address.

    Returns ``(True, None)`` if every resolved address is routable public IP.
    Returns ``(False, reason)`` on the first unsafe address. ``reason``
    starts with ``dns_failure:`` when name resolution itself failed (the
    caller treats that as transient, not a definitive private-IP rejection).
    """
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return False, f"dns_failure: {safe_for_log(exc.strerror or 'unknown', max_len=64)}"
    except OSError as exc:
        return False, f"dns_failure: {safe_for_log(str(exc), max_len=64)}"

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
        for net in _UNSAFE_NETWORKS:
            if ip.version == net.version and ip in net:
                return False, f"host_resolved_to_private_ip: {addr}"
    return True, None


def check_path_shape(path: str, patterns: tuple[str, ...]) -> bool:
    """Check that ``path`` matches at least one of the allowed regex patterns.

    Empty patterns tuple → no constraint (returns True).
    """
    if not patterns:
        return True
    return any(re.search(p, path) for p in patterns)


class SafeRedirectHandler(HTTPRedirectHandler):
    """Count redirect hops and re-validate host allowlist + SSRF per hop.

    Per-hop SSRF defense addresses DNS rebinding: an initial allowlisted host
    may resolve to a public IP, then a 301 redirect's target host may
    resolve to a private IP. We re-check on every hop. Also refuses
    HTTPS → HTTP downgrades.
    """

    def __init__(self, allowed_hosts: tuple[str, ...]) -> None:
        super().__init__()
        self._allowed_hosts = allowed_hosts
        self.hop_count = 0
        self._initial_scheme: str | None = None

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hop_count += 1
        if self.hop_count > _MAX_REDIRECT_HOPS:
            raise RedirectRejected("redirect_cap_exceeded")
        parsed = urlparse(newurl)
        if parsed.scheme not in ("http", "https"):
            raise RedirectRejected(
                f"host_not_allowed: "
                f"<invalid_scheme:{safe_for_log(parsed.scheme, max_len=16)}>"
            )
        if self._initial_scheme is None:
            self._initial_scheme = urlparse(req.full_url).scheme
        if self._initial_scheme == "https" and parsed.scheme == "http":
            raise RedirectRejected("scheme_downgrade: https_to_http")
        new_host = normalize_host(parsed.hostname)
        if not new_host:
            raise RedirectRejected("host_not_allowed: <unparseable>")
        if not check_host_allowed(new_host, self._allowed_hosts):
            raise RedirectRejected(f"host_not_allowed: {safe_for_log(new_host)}")
        safe, ip_err = check_resolved_ip_safe(new_host)
        if not safe and ip_err and not ip_err.startswith("dns_failure:"):
            raise RedirectRejected(ip_err)
        return super().redirect_request(req, fp, code, msg, headers, newurl)
