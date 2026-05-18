"""Tests for language_check.detect_language and language_matches.

Plan reference: docs/plans/2026-05-14-001-feat-mandatory-linkcheck-lang-gate-plan.md
Unit 1 — R1 (language_matches bug fix) and R3 (unknown handling).
"""

from __future__ import annotations

import pytest

from backlink_publisher.language_check import (
    SUPPORTED_LANGUAGES,
    detect_language,
    language_matches,
)


# --- SUPPORTED_LANGUAGES constant ---


def test_supported_languages_contains_canonical_four_languages() -> None:
    # Plan 2026-05-18-006 Unit 1: ko added. ja / zh-TW deferred to follow-up
    # brainstorms (require harder detection algorithms — Hiragana/Katakana for
    # ja, 繁简 lexicon for zh-TW — that this PR explicitly does not template).
    assert SUPPORTED_LANGUAGES == frozenset({"zh-CN", "ru", "en", "ko"})


# --- detect_language: happy paths ---


def test_detect_language_english_body() -> None:
    text = "This is a test article about https://example.com and some content here."
    assert detect_language(text) == "en"


def test_detect_language_chinese_body() -> None:
    text = "这是一个关于人工智能的文章，我们在这里讨论一些技术细节。"
    assert detect_language(text) == "zh-CN"


def test_detect_language_russian_body() -> None:
    text = "Это статья о машинном обучении, и мы обсуждаем здесь некоторые детали."
    assert detect_language(text) == "ru"


def test_detect_language_unknown_for_zero_score() -> None:
    # No EN/ZH/RU hints anywhere — code blocks or pure punctuation.
    text = "```\n    \n  ===\n```"
    assert detect_language(text) == "unknown"


# --- language_matches: R1 contract (post-fix) ---


@pytest.mark.parametrize("known", ["zh-CN", "ru", "en"])
def test_language_matches_self(known: str) -> None:
    assert language_matches(known, known) is True


def test_language_matches_mismatch_en_vs_zh() -> None:
    """R1: this was the bug — previously returned True; now must return False."""
    assert language_matches("en", "zh-CN") is False


def test_language_matches_mismatch_zh_vs_en() -> None:
    assert language_matches("zh-CN", "en") is False


def test_language_matches_mismatch_ru_vs_en() -> None:
    assert language_matches("ru", "en") is False


def test_language_matches_mismatch_en_vs_ru() -> None:
    assert language_matches("en", "ru") is False


def test_language_matches_mismatch_zh_vs_ru() -> None:
    assert language_matches("zh-CN", "ru") is False


def test_language_matches_mismatch_ru_vs_zh() -> None:
    assert language_matches("ru", "zh-CN") is False


# --- language_matches: R3 unknown handling ---


@pytest.mark.parametrize("requested", ["zh-CN", "ru", "en"])
def test_language_matches_unknown_detected_passes(requested: str) -> None:
    """detected='unknown' is the escape valve — caller can't disprove."""
    assert language_matches("unknown", requested) is True


@pytest.mark.parametrize("detected", ["zh-CN", "ru", "en"])
def test_language_matches_unknown_requested_passes(detected: str) -> None:
    """Symmetric: if requested itself is unknown we also allow through."""
    assert language_matches(detected, "unknown") is True


def test_language_matches_both_unknown() -> None:
    assert language_matches("unknown", "unknown") is True


# --- Noise stripping: URLs, HTML tags, markdown link syntax (regression
# for the work-themed-link-count fix that inflated en-score by appending
# Latin-anchor "Further reading" paragraphs to zh-CN articles)


def test_detect_zh_body_with_latin_urls_does_not_misclassify_as_en() -> None:
    """A zh-CN body whose only Latin content is URL strings + HTML anchor
    tags must still detect as zh-CN. Pre-fix the substring-based EN_HINTS
    counter inflated on "a"/"i"/"in"/"on" matches inside URLs + attributes.
    """
    text = (
        '在论坛上看到有人推荐 <a href="https://51acgs.com/animate/14529" '
        'target="_blank" rel="noopener">51漫畫</a>，自己跟着看了一阵子。\n\n'
        '<a href="https://51acgs.com/animate" target="_blank" rel="noopener">'
        '51acgs</a> 是日常会扫一眼的页面。'
    )
    assert detect_language(text) == "zh-CN"


def test_detect_zh_body_with_markdown_link_and_latin_anchors_stays_zh() -> None:
    """zh-CN body with an appended "延伸阅读" paragraph containing markdown
    links pointing at en.wikipedia.org / github.com etc must still detect
    as zh-CN. This is the exact shape the work-themed branch emits.
    """
    text = (
        "在论坛上看到有人推荐 51漫畫，自己跟着看了一阵子下来感觉不错。"
        "更新频率比较稳定，整理也算细致。\n\n"
        "延伸阅读：[Wikipedia](https://en.wikipedia.org), "
        "[MDN](https://developer.mozilla.org), "
        "[Stack Overflow](https://stackoverflow.com), "
        "[GitHub](https://github.com)。"
    )
    assert detect_language(text) == "zh-CN"


def test_detect_ru_body_with_latin_urls_stays_ru() -> None:
    """Symmetric: a Russian body with Latin URLs must still detect as ru."""
    text = (
        "Это статья о машинном обучении, мы обсуждаем здесь детали. "
        "<a href=\"https://github.com\">пример</a> и "
        "[Wikipedia](https://en.wikipedia.org) — внешние ссылки."
    )
    assert detect_language(text) == "ru"


def test_detect_en_body_unchanged_after_noise_strip() -> None:
    """Stripping URLs + HTML must not break English detection — the
    visible markdown anchor text + prose still carries the en signal."""
    text = (
        "This is an article about [GitHub](https://github.com) and "
        "<a href=\"https://example.com\">how</a> you can use it. "
        "The supporting links should not be the only signal."
    )
    assert detect_language(text) == "en"


def test_detect_pure_url_only_text_returns_unknown() -> None:
    """A body that is ENTIRELY noise (URLs + HTML, no prose) strips to
    empty and falls into the unknown branch — not silently mis-classified.
    """
    text = (
        '<a href="https://en.wikipedia.org">x</a> '
        '<a href="https://github.com">y</a>'
    )
    # After stripping HTML the visible text is "x y" — both 1-char tokens
    # match neither ZH_HINTS, RU_HINTS, nor multi-char EN_HINTS substrings
    # → all scores zero → unknown. (The 1-char "x"/"y" don't appear as
    # standalone hints in any list.)
    assert detect_language(text) == "unknown"
