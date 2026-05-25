"""HTML scoping — ``ArticleScopedCollector`` for text gathering within
outermost-<article> elements.

Extracted from the closed PR #1 ``verifier.py`` (2026-05-14) as a standalone
utility so any verifier / checker that needs to limit text extraction to
article-scoped content can reuse it without importing V1 module internals.

Plan: ``docs/plans/2026-05-14-005-feat-v1-verifier-asset-extraction-plan.md``
Source: ``origin/pr/1:src/backlink_publisher/verifier.py``
"""

from __future__ import annotations

from html.parser import HTMLParser

__all__ = ["ArticleScopedCollector", "collect_article_text"]


# ── constants ─────────────────────────────────────────────────────────────

# Role values that, when present on an element inside <article>, cause that
# element and its children to be excluded (sidebar/ad-like).
_SKIP_MIDARTICLE_ROLES = frozenset({
    "complementary",
    "navigation",
    "contentinfo",
    "banner",
    "region",
    "article",
})

# Role values on any element that mark the subtree as a sidebar to skip
# altogether.
_SKIP_ARTICLE_ROLES = frozenset({
    "complementary",
    "navigation",
    "contentinfo",
    "banner",
})


# ── collector ──────────────────────────────────────────────────────────────


class ArticleScopedCollector(HTMLParser):
    """HTMLParser that yields text within outermost-<article> elements only.

    Semantics (outermost-only article):
      * ``_article_depth == 1`` → we are directly inside ``<article>``.
      * ``_article_depth >= 2`` → a nested ``<article>``; its **interior**
        text is dropped (the outer one still captures its own direct text).
      * Once any ``<article>`` closes, ``_closed_once = True`` and all
        subsequent text is ignored (single-article page contract).
      * Inside the active article, elements whose ``role`` attribute
        matches ``_SKIP_MIDARTICLE_ROLES`` are excluded, along with their
        children (tracked via ``_inner_depth``).

    Args:
        max_article_depth: Maximum nesting depth to consider (default 3).
                             Beyond this, deeper articles are ignored.
    """

    def __init__(self, *, max_article_depth: int = 3) -> None:
        super().__init__(convert_charrefs=True)
        self._max_article_depth = max_article_depth

        # Article state.
        self._article_depth = 0
        self._in_article = False
        self._passed_first_article = False
        self._closed_once = False

        # Subtree-skip state inside article.
        self._skip_sidebar = frozenset()
        self._skip_article_sidebar = frozenset()
        self._inner_depth = 0

        # Output buffer.
        self._text_buffer: list[str] = []

    # --- public accessors ---

    @property
    def text(self) -> str:
        """Collected text, stripped of leading/trailing whitespace."""
        t = "".join(self._text_buffer)
        return t.strip()

    @property
    def reached_article(self) -> bool:
        """True if at least one <article> was entered and closed."""
        return self._passed_first_article

    # --- override handlers ---

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = dict(attrs)
        if tag == "article":
            self._article_depth += 1
            if self._article_depth == 1:
                self._in_article = True
                self._passed_first_article = True
            return

        # Deeply nested article — do not register starttag effects.
        if self._article_depth > self._max_article_depth:
            return

        # If we are past the first article and it has closed, drop everything.
        if self._closed_once:
            return

        if self._in_article and self._article_depth == 1:
            role = attrs_dict.get("role", "")
            if role in _SKIP_ARTICLE_ROLES:
                self._skip_article_sidebar |= {role}
            if role in _SKIP_MIDARTICLE_ROLES:
                # Exclude this subtree.
                self._inner_depth += 1
                if self._inner_depth == 1:
                    if role in _SKIP_ARTICLE_ROLES:
                        self._skip_sidebar |= {role}
                    self._skip_sidebar |= {role}
                return
            # Check for ARIA hidden / hidden attribute.
            hidden = attrs_dict.get("hidden", "")
            aria_hidden = attrs_dict.get("aria-hidden", "")
            if hidden == "hidden" or aria_hidden == "true":
                self._inner_depth += 1
                return

            # Track nested elements inside excluded sidebar.
            if self._inner_depth > 0:
                self._inner_depth += 1
        else:
            # Outside the active article: ignore.
            pass

    def handle_endtag(self, tag: str) -> None:
        if tag == "article":
            if self._article_depth > 0:
                self._article_depth -= 1
                if self._article_depth == 0:
                    self._in_article = False
                    self._closed_once = True
            return

        if self._closed_once:
            return

        if self._in_article and self._article_depth == 1:
            if self._inner_depth > 0:
                self._inner_depth -= 1
        else:
            pass

    def handle_data(self, data: str) -> None:
        if self._closed_once:
            return
        if self._in_article and self._article_depth == 1 and self._inner_depth == 0:
            self._text_buffer.append(data)


# ── convenience wrapper ────────────────────────────────────────────────────


def collect_article_text(html: str, *, max_article_depth: int = 3) -> str:
    """Parse ``html`` and return text from the first outermost <article>.

    This is the primary entry point for caller convenience.  Internally
    constructs an ``ArticleScopedCollector``, feeds the HTML, and returns
    ``.text``.

    >>> collect_article_text("<article>Hello</article>")
    'Hello'
    """
    collector = ArticleScopedCollector(max_article_depth=max_article_depth)
    collector.feed(html)
    collector.close()
    return collector.text
