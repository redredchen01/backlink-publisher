"""Free-text secret scrubber for the event substrate (T1).

Independent of ``backlink_publisher.logger._SENSITIVE_KEYS`` — that path does
exact-key matching on structured ``extra`` dicts; this path scans arbitrary
free-text (error messages, response bodies) for embedded secrets via regex
plus a high-entropy detector. The two layers are complementary, not
overlapping.

Threshold for the high-entropy detector is documented as "deferred to
implementation" in the plan (target false-positive rate ≤ 5%); 4.5
per-character Shannon over tokens of length ≥ 32 is the starting value.
"""

from __future__ import annotations

import math
import re
from typing import Final

# Named patterns. Each entry is ``(name, compiled_regex)``. Names appear in
# the returned ``hit_counts`` dict so callers can route different secret
# classes to different alerts.
_PATTERNS: Final[list[tuple[str, re.Pattern[str]]]] = [
    # OAuth Bearer token (header or stringified). Bearer scheme keeps tokens
    # on the same line; char class includes ``+/=`` to cover standard
    # (non-url-safe) base64 padding so a token ending in ``==`` redacts in
    # full instead of leaving the padding chars behind.
    ("oauth_bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._+/=\-]+")),
    # JWT — base64url-encoded headers always start with ``eyJ`` (which is
    # ``{"`` base64'd). Include ``=`` for standard-base64 signatures.
    # Anchor with a negative lookbehind on identifier chars rather than
    # ``\b``: ``\b`` fails when the JWT is glued to an identifier (e.g.
    # ``access_tokeneyJhbGci…``) because both ``n`` and ``e`` are word
    # characters and no transition exists. The lookbehind correctly
    # admits start-of-string, whitespace, ``:``, ``=``, quotes, etc.
    ("jwt", re.compile(r"(?<![A-Za-z0-9_])eyJ[A-Za-z0-9._+/=\-]+")),
    # Google API key — canonical shape is ``AIza`` + exactly 35 alphanumeric
    # chars, but ``{35,}`` widens to catch longer real-shape variants (and
    # avoids dropping the secret to the high_entropy bucket, losing routing
    # signal). The trailing negative lookahead replaces ``\b`` (which fails
    # when a key ends in ``-`` because ``-`` is a non-word char and ``\b``
    # would require a word↔non-word transition).
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35,}(?![0-9A-Za-z\-_])")),
    # 64-char hex run, case-insensitive. Named after the sha256 shape (not
    # after Medium): the regex catches any sha256-shaped token, including
    # legitimate artifact digests. Some emitters (Windows certutil, Java
    # keystore CLIs, TLS fingerprint dumps) produce uppercase hex, so the
    # match is case-insensitive. Routing should treat this hit as "potential
    # secret, requires upstream confirmation" rather than as a confirmed
    # Medium credential.
    ("sha256_hex_token", re.compile(r"\b[0-9a-f]{64}\b", re.IGNORECASE)),
    # HTTP(S) URL with embedded basic-auth credentials. The first segment
    # after scheme is the user, second (after the colon) is the password,
    # both up to ``@``. The tail char class terminates on whitespace and
    # on the common log-format delimiters that no real URL contains —
    # quotes, angle brackets, backticks, closing parens/brackets/braces,
    # backslashes. Without these, a URL embedded in HTML (``"..."``),
    # markdown (``[txt](url)``), or JSON (``"url"``) would extend the
    # redaction span into the surrounding scaffold and lose observability
    # data. Chained credential URLs (``?next=https://u:p@b/x`` nested
    # inside another URL) remain a known limit — regex alone cannot split
    # them; the secret is still scrubbed but ``hit_counts.basic_auth_url``
    # undercounts.
    (
        "basic_auth_url",
        re.compile(r"https?://[^:/\s@]+:[^@/\s]+@[^\s\"'<>`)\]}\\]+"),
    ),
]

#: Minimum token length for the high-entropy fallback. Tokens shorter than
#: this can't carry a meaningful credential and would over-trigger on words.
_HIGH_ENTROPY_MIN_LEN: Final[int] = 32

#: Per-character Shannon entropy threshold. Random base64 over a 64-symbol
#: alphabet approaches log2(64) = 6.0; English prose stays below ~4.0 even
#: in long tokens. 4.5 is the documented starting point (plan §U6 deferred).
_HIGH_ENTROPY_THRESHOLD: Final[float] = 4.5

#: Minimum ASCII density to subject a token to the entropy heuristic. CJK
#: and other large-alphabet runs score mechanically high under per-codepoint
#: Shannon (e.g. 32 distinct Chinese chars → log2(32)=5.0), so we restrict
#: the redaction heuristic to ASCII-dense tokens and skip i18n prose.
_HIGH_ENTROPY_ASCII_RATIO_MIN: Final[float] = 0.9

#: Token shape for the high-entropy pass — runs of non-whitespace at least
#: ``_HIGH_ENTROPY_MIN_LEN`` long. Punctuation inside the run is preserved
#: (matches real secret formats: base64url, hex, dotted JWTs).
_HIGH_ENTROPY_TOKEN: Final[re.Pattern[str]] = re.compile(r"\S{%d,}" % _HIGH_ENTROPY_MIN_LEN)

#: Replacement marker. Kept human-readable so log readers can tell a value
#: was scrubbed (not silently dropped). Caller's ``hit_counts`` carries the
#: pattern name for routing.
_REDACTED: Final[str] = "<REDACTED>"

#: Maximum input size (in characters) that ``scrub_text`` will scan in full.
#: Inputs over this cap are truncated to ``_MAX_SCRUB_LEN`` and tagged with
#: ``<TRUNCATED:N more chars>``. A multi-megabyte response body slipping
#: into an exception traceback would otherwise do tens of MB of regex work
#: synchronously on the caller's thread. 64 KiB comfortably covers a real
#: log line; anything larger almost certainly should not be in a log.
_MAX_SCRUB_LEN: Final[int] = 65536

#: Marker appended to truncated inputs so log readers can tell the cap fired
#: (vs. a genuinely-short payload).
_TRUNCATED_TEMPLATE: Final[str] = "<TRUNCATED:{n} more chars>"


def _shannon_entropy(s: str) -> float:
    """Per-character Shannon entropy of ``s`` (base-2).

    Returns 0.0 for the empty string. Pure repeated patterns (e.g.
    ``"abcd" * 8``) score below ``log2(unique_chars)`` and stay well under
    the high-entropy threshold; near-uniform random alphanumeric runs score
    close to ``log2(alphabet_size)``.
    """
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    entropy = 0.0
    for c in counts.values():
        p = c / n
        entropy -= p * math.log2(p)
    return entropy


def scrub_text(s: str) -> tuple[str, dict[str, int]]:
    """Redact known secret shapes in ``s``; return ``(cleaned, hit_counts)``.

    Each named pattern in ``_PATTERNS`` runs once; matches are replaced with
    ``<REDACTED>`` and the pattern name is incremented in ``hit_counts``.
    A high-entropy fallback then walks remaining tokens of length ≥
    ``_HIGH_ENTROPY_MIN_LEN`` and redacts any whose Shannon entropy meets
    ``_HIGH_ENTROPY_THRESHOLD`` (recorded under ``high_entropy``).

    Contract: never raises on any ``str`` input. Non-string ``s`` (e.g.
    ``None`` from an optional field) will raise ``TypeError`` from the first
    ``pattern.findall`` call — callers in log-write hot paths must pre-coerce
    or wrap in ``str(...)`` to keep flush bulletproof.

    Inputs longer than ``_MAX_SCRUB_LEN`` are truncated before scanning and
    annotated with a ``<TRUNCATED:N more chars>`` suffix so the caller can
    see the cap fired. The cap bounds worst-case work to ~11 linear passes
    over ``_MAX_SCRUB_LEN`` chars regardless of attacker-controlled length.
    Secrets that live past the cap are dropped rather than scrubbed — this
    is a deliberate trade-off (log lines that long should not be reaching
    this code path in the first place).
    """
    if len(s) > _MAX_SCRUB_LEN:
        truncated = _TRUNCATED_TEMPLATE.format(n=len(s) - _MAX_SCRUB_LEN)
        s = s[:_MAX_SCRUB_LEN] + truncated
    hit_counts: dict[str, int] = {}
    cleaned = s
    for name, pattern in _PATTERNS:
        matches = pattern.findall(cleaned)
        if matches:
            hit_counts[name] = hit_counts.get(name, 0) + len(matches)
            cleaned = pattern.sub(_REDACTED, cleaned)

    # High-entropy fallback after named patterns so the regex layer claims
    # known shapes first (more useful routing signal) and the entropy pass
    # only sees what wasn't already redacted.
    #
    # Threshold tuning and URL/UUID-aware tokenisation are deferred to
    # implementation per plan §Open Questions §Deferred to Implementation
    # ("[Affects U6] regex scrubber 的 high-entropy 阈值 ... 实测调参；
    # false-positive 率超 5% 则放宽"). The current 4.5 threshold is the
    # starting point; refine once we have a real corpus.
    def _entropy_sub(match: re.Match[str]) -> str:
        token = match.group(0)
        # Rationale documented on ``_HIGH_ENTROPY_ASCII_RATIO_MIN``.
        ascii_ratio = sum(1 for ch in token if ord(ch) < 128) / len(token)
        if ascii_ratio < _HIGH_ENTROPY_ASCII_RATIO_MIN:
            return token
        if _shannon_entropy(token) >= _HIGH_ENTROPY_THRESHOLD:
            hit_counts["high_entropy"] = hit_counts.get("high_entropy", 0) + 1
            return _REDACTED
        return token

    cleaned = _HIGH_ENTROPY_TOKEN.sub(_entropy_sub, cleaned)
    return cleaned, hit_counts
