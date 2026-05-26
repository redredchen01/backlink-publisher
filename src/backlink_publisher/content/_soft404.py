"""Soft-404 title detection for content-fetch gate.

Extracted from ``fetch.py`` in Plan 2026-05-26-002 Phase 3 to free
monolith-budget headroom. Self-contained: ``re`` only.

A "soft 404" is an HTTP 200 whose page title advertises a "page not found"
state — the site serves a friendly-looking "Not Found" page with 200 status.
"""

from __future__ import annotations

import re

_SOFT_404_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"^{phrase}(\s*[-|–—:|·]\s*.*)?$")
    for phrase in (
        # English
        r"404",
        r"404\s+not\s+found",
        r"page\s+not\s+found",
        r"not\s+found",
        r"page\s+does\s+not\s+exist",
        r"error\s+404",
        r"this\s+page\s+(can'?t|cannot|could\s+not)\s+be\s+found",
        # Chinese (simplified + traditional)
        r"页面不存在",
        r"页面未找到",
        r"找不到页面",
        r"頁面不存在",
        r"頁面未找到",
        r"找不到頁面",
        r"404\s*错误",
        r"404\s*錯誤",
        # Japanese
        r"ページが見つかりません",
        r"お探しのページは見つかりません",
        # Russian
        r"страница\s+не\s+найдена",
    )
)


def is_soft_404_title(title: str) -> bool:
    """Return True if ``title`` looks like a soft-404 placeholder.

    The check is case-insensitive, anchored at the start, and tolerates the
    common ``"<phrase> - <SiteName>"`` suffix pattern that sites attach.
    """
    if not title:
        return False
    casefolded = title.casefold().strip()
    for pat in _SOFT_404_TITLE_PATTERNS:
        if pat.match(casefolded):
            return True
    return False
