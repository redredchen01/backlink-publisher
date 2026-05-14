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


def test_supported_languages_contains_exactly_three_languages() -> None:
    assert SUPPORTED_LANGUAGES == frozenset({"zh-CN", "ru", "en"})


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
