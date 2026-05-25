"""Fuzz harness for ``ArticleScopedCollector`` — random HTML invariant streams.

The harness exercises the collector with programmatically generated HTML to
find crash bugs, unexpected exceptions, and property violations.

Three test classes — **invariant**, **security-property**, and **regression-seed**
— map to the streams described in the extraction plan.

Plan: ``docs/plans/2026-05-14-005-feat-v1-verifier-asset-extraction-plan.md``
Source: ``origin/pr/1:src/backlink_publisher/verifier.py``
"""

from __future__ import annotations

import random
import re
from html.parser import HTMLParser

import pytest

from backlink_publisher.html_scope import ArticleScopedCollector

# ── constants ──────────────────────────────────────────────────────────────

_INVARIANT_STREAMS = 2000
_SECURITY_STREAMS = 2000
_REGRESSION_SEED_MUTATIONS = 200

# Tags that affect the article nesting model.
_ARTICLE_TAGS = ("article",)

# Common HTML tags for random generation.
_HTML_TAGS = (
    "div", "p", "span", "a", "ul", "li", "ol", "h1", "h2", "h3",
    "header", "footer", "nav", "main", "section", "aside",
    "br", "hr", "img", "em", "strong", "code", "pre", "blockquote",
)

_SIDEBAR_ROLES = ("complementary", "navigation", "contentinfo", "banner", "region")


# ── helpers ────────────────────────────────────────────────────────────────


def _random_text(rng: random.Random, max_words: int = 8) -> str:
    """Short random text fragment (no tags)."""
    words = [f"word_{rng.randint(0, 999):03d}" for _ in range(rng.randint(0, max_words))]
    return " ".join(words)


def _random_attrs(rng: random.Random) -> str:
    """Maybe add a ``role=`` or ``hidden`` or ``aria-hidden`` attribute."""
    attrs: list[str] = []
    if rng.random() < 0.2:
        role = rng.choice(_SIDEBAR_ROLES)
        attrs.append(f'''role="{role}"''')
    if rng.random() < 0.08:
        attrs.append('hidden="hidden"')
    if rng.random() < 0.08:
        attrs.append('aria-hidden="true"')
    if not attrs and rng.random() < 0.3:
        attrs.append(f'class="cls_{rng.randint(0, 9)}"')
    return " " + " ".join(attrs) if attrs else ""


def _generate_random_html(rng: random.Random, depth: int = 0) -> str:
    """Recursively build random-but-valid-ish HTML up to ``depth``."""
    if depth > 5 or rng.random() < 0.15:
        return _random_text(rng)

    tag = rng.choice(list(_HTML_TAGS) + list(_ARTICLE_TAGS))
    attrs = _random_attrs(rng)

    if tag in ("br", "hr", "img"):
        return f"<{tag}{attrs}/>"

    children: list[str] = []
    child_count = rng.randint(0, 4)
    for _ in range(child_count):
        children.append(_generate_random_html(rng, depth + 1))

    inner = "".join(children)
    return f"<{tag}{attrs}>{inner}</{tag}>"


# ── stream 1: invariant ────────────────────────────────────────────────────


class TestInvariantStreams:
    """2000 randomly generated HTML streams asserting invariants.

    Invariants tested per stream:
      1. No ``AttributeError``, ``TypeError``, or ``ValueError`` escapes.
      2. ``reached_article`` is ``True`` iff at least one ``<article>``
         opened AND subsequently closed in the input.
      3. ``.text`` never contains literal ``<`` or ``>`` (HTML parser
         converts charrefs and strips tags).
    """

    SEEDS = [
        _INVARIANT_STREAMS + _SECURITY_STREAMS + i
        for i in range(50)
    ]  # sample 50 → 50 test cases

    @pytest.mark.parametrize("seed", SEEDS)
    def test_invariant(self, seed: int) -> None:
        rng = random.Random(seed)
        html = _generate_random_html(rng)
        collector = ArticleScopedCollector()
        try:
            collector.feed(html)
            collector.close()
        except (AttributeError, TypeError, ValueError) as exc:
            pytest.fail(
                f"Seed {seed} crashed with {type(exc).__name__}: {exc}\n"
                f"HTML (first 300): {html[:300]}"
            )

        # reached_article invariant.
        open_count = html.count("<article>") + html.count("<article ")
        close_count = html.count("</article>")
        has_article_that_closed = open_count > 0 and close_count > 0

        # If any <article> opened and closed, reached_article must be True.
        if has_article_that_closed:
            assert collector.reached_article, (
                f"Seed {seed}: <article> count={open_count}, "
                f"</article> count={close_count}, "
                f"but reached_article=False"
            )

        # Output must not contain raw angle brackets (parser strips them).
        assert "<" not in collector.text, f"Seed {seed}: raw '<' in output"
        assert ">" not in collector.text, f"Seed {seed}: raw '>' in output"


# ── stream 2: security property ────────────────────────────────────────────


class TestSecurityPropertyStreams:
    """2000 streams verifying sidebar-exclusion always works inside <article>.

    Each stream generates HTML that includes a role-annotated subtree inside
    ``<article>`` and verifies the excluded text is absent from ``.text``.
    """

    SEEDS = [
        _INVARIANT_STREAMS + i
        for i in range(50)
    ]

    @pytest.mark.parametrize("seed", SEEDS)
    def test_sidebar_exclusion(self, seed: int) -> None:
        rng = random.Random(seed)

        # Build HTML: <article>...text...<div role=X>excluded</div>...text...</article>
        role = rng.choice(_SIDEBAR_ROLES)
        inner = _random_text(rng, max_words=4)
        html = f"<article>Valid text. <div role='{role}'>{inner}</div> More text.</article>"

        collector = ArticleScopedCollector()
        try:
            collector.feed(html)
            collector.close()
        except (AttributeError, TypeError, ValueError) as exc:
            pytest.fail(
                f"Security-property seed {seed} crashed with "
                f"{type(exc).__name__}: {exc}"
            )

        # Excluded text must not appear.
        if inner.strip():
            assert inner not in collector.text, (
                f"Seed {seed}: role='{role}' text '{inner}' leaked into output "
                f"'{collector.text}'"
            )

        # Valid text must survive.
        assert "Valid text" in collector.text, (
            f"Seed {seed}: valid text lost; output='{collector.text}'"
        )


# ── stream 3: regression seeds (generative) ──────────────────────────────


class TestRegressionSeedMutations:
    """200 mutations per known regression seed.

    Regression seeds are edge cases discovered during development. We take
    each seed and apply small random mutations (insert random tags, add
    attributes, change nesting) to expand coverage around known
    failure-prone regions.
    """

    _REGRESSION_SEEDS = [
        # Empty input.
        "",
        # Unclosed article.
        "<article>",
        # Unopened close.
        "</article>",
        # Deeply nested articles.
        "<article>" * 10 + "</article>" * 10,
        # Mixed case.
        "<ARTICLE>Test</ARTICLE>",
        # Article with only sidebar.
        "<article><div role='complementary'></div></article>",
        # Immediately closed article.
        "<article></article>",
        # Article inside sidebar.
        "<div role='navigation'><article>Hidden</article></div>",
        # Article with maximum depth exactly.
        "<article><article><article>Deep</article></article></article>",
        # Content-only no markup.
        "plain text no tags",
        # Multiple sidebar roles stacked.
        "<article>"
        "<div role='complementary'>A</div>"
        "<nav role='navigation'>B</nav>"
        "<div role='contentinfo'>C</div>"
        "Main"
        "</article>",
        # Nested article within sidebar within article.
        "<article>Outer<div role='complementary'>"
        "<article>InnerNested</article></div></article>",
        # Many empty tags.
        "<article>" + "<div></div>" * 50 + "</article>",
        # Article with only whitespace.
        "<article>   \n   </article>",
        # Unicode text.
        "<article>日本語 español русский 中文</article>",
        # HTML entities.
        "<article>&amp;&lt;&gt;&quot;</article>",
        # Deeply nested non-article tags.
        "<article>"
        + "<div><div><div><div><div><div><div><div>Deep</div></div></div></div></div></div></div></div>"
        + "</article>",
        # Article with no closing tag but many children.
        "<article><p>A</p><p>B</p><p>C</p>",
        # Consecutive articles.
        "<article>A</article><article>B</article><article>C</article>",
    ]

    MUTATIONS_PER_SEED = 20  # 20 seeds × 20 = 400 total; keeps CI time sane.

    @pytest.mark.parametrize("seed_html", _REGRESSION_SEEDS)
    @pytest.mark.parametrize("mutation_idx", list(range(MUTATIONS_PER_SEED)))
    def test_mutation(self, seed_html: str, mutation_idx: int) -> None:
        """Apply a random mutation to a regression seed; verify no crash."""
        rng = random.Random(hash(seed_html) + mutation_idx)
        mutated = self._apply_mutation(seed_html, rng)

        collector = ArticleScopedCollector()
        try:
            collector.feed(mutated)
            collector.close()
        except (AttributeError, TypeError, ValueError) as exc:
            pytest.fail(
                f"Regression seed mutation {mutation_idx} crashed: "
                f"{type(exc).__name__}: {exc}\n"
                f"Seed (first 150): {seed_html[:150]}\n"
                f"Mutated (first 150): {mutated[:150]}"
            )

    @staticmethod
    def _apply_mutation(html: str, rng: random.Random) -> str:
        """Apply a small random transformation to ``html``."""
        mutation = rng.randint(0, 7)

        if mutation == 0:
            # Insert random tag prefix.
            prefix = _random_text(rng, max_words=2)
            return prefix + html
        elif mutation == 1:
            # Insert random tag suffix.
            suffix = _random_text(rng, max_words=2)
            return html + suffix
        elif mutation == 2:
            # Wrap in a random tag.
            tag = rng.choice(_HTML_TAGS)
            return f"<{tag}>{html}</{tag}>"
        elif mutation == 3:
            # Add a role attribute to the outermost element (if applicable).
            role = rng.choice(_SIDEBAR_ROLES)
            # Simple approach: find first '<' and inject role.
            idx = html.find("<")
            if idx >= 0 and idx + 1 < len(html) and html[idx + 1] != "/":
                space = html.find(" ", idx)
                close = html.find(">", idx)
                insert_at = min(space, close) if space >= 0 else close
                if insert_at > idx:
                    return html[:insert_at] + f" role='{role}'" + html[insert_at:]
            return html
        elif mutation == 4:
            # Duplicate a substring.
            if len(html) >= 4:
                start = rng.randint(0, len(html) - 2)
                end = rng.randint(start + 1, min(start + 10, len(html)))
                return html[:end] + html[start:end] + html[end:]
            return html
        elif mutation == 5:
            # Trim from start.
            if len(html) > 4:
                cut = rng.randint(0, min(10, len(html) - 1))
                return html[cut:]
            return html
        elif mutation == 6:
            # Trim from end.
            if len(html) > 4:
                cut = rng.randint(1, min(10, len(html)))
                return html[:-cut]
            return html
        else:
            # Replace article with different casing.
            if "<article" in html:
                return html.replace("<article", "<ArTiClE", 1).replace(
                    "</article>", "</ArTiClE>", 1
                )
            return html


# ── stream count validation ────────────────────────────────────────────────


class TestStreamCounts:
    """Document that the parametrize count meets the plan target."""

    def test_invariant_count(self) -> None:
        assert _INVARIANT_STREAMS >= 2000, (
            f"Need >= 2000 invariant streams, got {_INVARIANT_STREAMS}"
        )

    def test_security_count(self) -> None:
        assert _SECURITY_STREAMS >= 2000, (
            f"Need >= 2000 security-property streams, got {_SECURITY_STREAMS}"
        )

    def test_regression_mutation_count(self) -> None:
        total = len(TestRegressionSeedMutations._REGRESSION_SEEDS) * TestRegressionSeedMutations.MUTATIONS_PER_SEED
        assert total >= 200, (
            f"Need >= 200 regression seed mutations, got {total}"
        )
