"""Tests for anchor_lang.check_anchor_language (Unit 2 of plan 2026-05-14-001).

Covers the R4 codepoint heuristic + branded-pool exemption + kind-scoping +
non-enum language behavior.
"""

from __future__ import annotations

import pytest

from backlink_publisher.anchor_lang import check_anchor_language


# --- zh-CN: happy path & branded carve-out ---


def test_zh_cn_main_domain_pass_with_cjk() -> None:
    ok, reason = check_anchor_language("苹果官网", "zh-CN", "main_domain", [])
    assert ok is True
    assert reason is None


def test_zh_cn_main_domain_branded_latin_passes_via_pool() -> None:
    ok, reason = check_anchor_language("Apple", "zh-CN", "main_domain", ["Apple"])
    assert ok is True
    assert reason is None


def test_zh_cn_main_domain_unbranded_latin_fails() -> None:
    ok, reason = check_anchor_language("Apple", "zh-CN", "main_domain", [])
    assert ok is False
    assert reason == "anchor missing CJK codepoint"


def test_zh_cn_main_domain_generic_english_fails() -> None:
    ok, reason = check_anchor_language("learn more", "zh-CN", "main_domain", [])
    assert ok is False
    assert reason == "anchor missing CJK codepoint"


def test_zh_cn_target_kind_subject_to_rule() -> None:
    """target is in _GATED_KINDS alongside main_domain."""
    ok, _ = check_anchor_language("Apple", "zh-CN", "target", [])
    assert ok is False


# --- kind exemption: supporting/extra/category/detail ---


@pytest.mark.parametrize("kind", ["supporting", "extra", "category", "detail"])
def test_kind_exempt_supporting_etc(kind: str) -> None:
    """Auxiliary citations (Wiki, MDN) in zh-CN articles must pass."""
    ok, reason = check_anchor_language("MDN", "zh-CN", kind, [])
    assert ok is True
    assert reason is None


# --- ru ---


def test_ru_main_domain_pass_with_cyrillic() -> None:
    ok, _ = check_anchor_language("Главная страница", "ru", "main_domain", [])
    assert ok is True


def test_ru_main_domain_latin_only_fails() -> None:
    ok, reason = check_anchor_language("home page", "ru", "main_domain", [])
    assert ok is False
    assert reason == "anchor missing Cyrillic codepoint"


def test_ru_branded_latin_passes() -> None:
    ok, _ = check_anchor_language("Yandex", "ru", "main_domain", ["Yandex"])
    assert ok is True


# --- en: strict (any-Latin AND none-of CJK/Cyrillic) ---


def test_en_main_domain_pass_with_latin_only() -> None:
    ok, _ = check_anchor_language("Apple Store", "en", "main_domain", [])
    assert ok is True


def test_en_main_domain_punctuation_and_digits_allowed() -> None:
    ok, _ = check_anchor_language("Apple — Inc. iPhone 15", "en", "main_domain", [])
    assert ok is True


def test_en_main_domain_mixed_script_with_cjk_fails() -> None:
    """Mixed-script English anchors fail strict-en. Use branded_pool exemption."""
    ok, reason = check_anchor_language("在线 Apple 体验店", "en", "main_domain", [])
    assert ok is False
    assert reason == "en anchor contains CJK codepoint"


def test_en_main_domain_mixed_script_with_cyrillic_fails() -> None:
    ok, reason = check_anchor_language("Apple магазин", "en", "main_domain", [])
    assert ok is False
    assert reason == "en anchor contains Cyrillic codepoint"


def test_en_main_domain_no_latin_letter_fails() -> None:
    ok, reason = check_anchor_language("12345", "en", "main_domain", [])
    assert ok is False
    assert reason == "anchor missing Latin letter"


def test_en_branded_mixed_script_passes() -> None:
    """branded_pool exemption applies regardless of language."""
    ok, _ = check_anchor_language("在线 Apple", "en", "main_domain", ["在线 Apple"])
    assert ok is True


# --- empty anchor ---


def test_empty_anchor_fails_main_domain() -> None:
    ok, _ = check_anchor_language("", "zh-CN", "main_domain", [])
    assert ok is False


def test_empty_anchor_exempt_when_kind_exempt() -> None:
    ok, _ = check_anchor_language("", "zh-CN", "supporting", [])
    assert ok is True


# --- BMP boundary: Extension A NOT counted ---


def test_zh_cn_bmp_only_extension_a_not_counted() -> None:
    """U+3400 (Ext A) is OUT of the BMP block we check; must fail."""
    ok, _ = check_anchor_language("㐀", "zh-CN", "main_domain", [])
    assert ok is False


def test_zh_cn_extension_b_not_counted() -> None:
    """U+20000 (Ext B) is also out."""
    ok, _ = check_anchor_language("\U00020000", "zh-CN", "main_domain", [])
    assert ok is False


# --- non-enum row_language ---


def test_japanese_row_language_exempt_with_no_check() -> None:
    """row.language outside SUPPORTED_LANGUAGES is exempt (R3 contract)."""
    ok, reason = check_anchor_language("Tokyo", "ja", "main_domain", [])
    assert ok is True
    assert reason is None


def test_german_row_language_exempt() -> None:
    ok, _ = check_anchor_language("Berlin", "de", "main_domain", [])
    assert ok is True


def test_unknown_row_language_exempt() -> None:
    ok, _ = check_anchor_language("anything", "unknown", "main_domain", [])
    assert ok is True
