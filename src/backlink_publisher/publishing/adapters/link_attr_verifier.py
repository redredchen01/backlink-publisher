"""Post-publish link-attribute verifier.

After an article is published to a platform that may strip HTML attributes,
this helper fetches the live page and checks whether ``target="_blank"`` and
``rel`` survived rendering — and, since Plan 2026-05-13-004 Unit 6, whether
the platform silently injected ``rel="nofollow"`` (which collapses dofollow
weight to zero). Designed to run fire-and-forget after publish succeeds —
failures are logged but never surface as publish failures.
"""

from __future__ import annotations

import re

_A_TAG_RE = re.compile(r"<a\s[^>]*>", re.IGNORECASE)
_BLANK_RE = re.compile(r'\btarget\s*=\s*["\']?_blank["\']?', re.IGNORECASE)
# Capture the value of the rel attribute on a single <a> tag, single or
# double quoted. Used per-tag to tokenise and look for the "nofollow" keyword.
_REL_VALUE_RE = re.compile(
    r'\brel\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE
)


def verify_link_attributes(
    url: str,
    *,
    timeout: float = 10.0,
) -> dict:
    """Fetch ``url`` and audit ``<a>`` tags for surviving link attributes.

    Returns a plain dict so callers can stash it in ``_provider_meta`` without
    any import coupling. Never raises — on any network or parse error it
    returns a ``verification: skipped`` sentinel instead.

    Return shape (on success):
        {
            "verification": "ok",
            "total_anchors": int,
            "blank_anchors": int,
            "blank_ratio": float,             # blank_anchors / total_anchors or 0.0
            "nofollow_anchors": int,          # count with rel containing "nofollow"
            "nofollow_detected": bool,        # True iff nofollow_anchors > 0
            "nofollow_reason": str | None,    # human-readable warning when detected
        }

    Return shape (on failure):
        {
            "verification": "skipped",
            "reason": str,
        }

    Nofollow detection is a defence against silent platform behaviour
    (Medium and similar): a backlink with rel="nofollow" passes zero SEO
    weight even though it renders identically. ``nofollow_detected=True``
    is a warning signal — callers should record it for trend analysis but
    are NOT expected to fail the publish over it.
    """
    try:
        from backlink_publisher.http import get as http_get
        resp = http_get(
            url,
            timeout=timeout,
            headers={"User-Agent": "backlink-publisher-verifier/0.1"},
        )
    except Exception as exc:
        return {"verification": "skipped", "reason": str(exc)}

    if not resp.ok:
        return {
            "verification": "skipped",
            "reason": f"HTTP {resp.status_code}",
        }

    html = resp.text
    tags = _A_TAG_RE.findall(html)
    total = len(tags)
    blank = sum(1 for t in tags if _BLANK_RE.search(t))
    nofollow = sum(1 for t in tags if _tag_has_nofollow(t))

    result: dict = {
        "verification": "ok",
        "total_anchors": total,
        "blank_anchors": blank,
        "blank_ratio": blank / total if total else 0.0,
        "nofollow_anchors": nofollow,
        "nofollow_detected": nofollow > 0,
        "nofollow_reason": None,
    }
    if nofollow > 0:
        result["nofollow_reason"] = (
            f"platform injected rel=nofollow on {nofollow}/{total} anchor(s); "
            "dofollow weight transfer is zero — check the publish adapter or "
            "the target platform's link policy"
        )
    return result


def _tag_has_nofollow(tag_html: str) -> bool:
    """True iff the rel attribute on ``tag_html`` contains the literal token
    ``nofollow`` (case-insensitive, whitespace-tokenised — so ``nofollowed``
    or ``not-nofollow`` do NOT trigger)."""
    match = _REL_VALUE_RE.search(tag_html)
    if not match:
        return False
    tokens = match.group(1).lower().split()
    return "nofollow" in tokens
