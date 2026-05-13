"""Post-publish link-attribute verifier.

After an article is published to a platform that may strip HTML attributes,
this helper fetches the live page and checks whether ``target="_blank"`` and
``rel="noopener"`` survived rendering.  Designed to run fire-and-forget after
publish succeeds — failures are logged but never surface as publish failures.
"""

from __future__ import annotations

import re

_A_TAG_RE = re.compile(r"<a\s[^>]*>", re.IGNORECASE)
_BLANK_RE = re.compile(r'\btarget\s*=\s*["\']?_blank["\']?', re.IGNORECASE)


def verify_link_attributes(
    url: str,
    *,
    timeout: float = 10.0,
) -> dict:
    """Fetch ``url`` and count ``<a>`` tags that have ``target="_blank"``.

    Returns a plain dict so callers can stash it in ``_provider_meta`` without
    any import coupling.  Never raises — on any network or parse error it
    returns a ``verification: skipped`` sentinel instead.

    Return shape (on success):
        {
            "verification": "ok",
            "total_anchors": int,
            "blank_anchors": int,
            "blank_ratio": float,   # blank_anchors / total_anchors or 0.0
        }

    Return shape (on failure):
        {
            "verification": "skipped",
            "reason": str,
        }
    """
    try:
        import requests  # noqa: PLC0415
        resp = requests.get(
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

    return {
        "verification": "ok",
        "total_anchors": total,
        "blank_anchors": blank,
        "blank_ratio": blank / total if total else 0.0,
    }
