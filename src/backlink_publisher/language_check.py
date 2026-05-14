"""Language detection helpers for the backlink pipeline."""

from __future__ import annotations


#: Languages the gate semantically distinguishes. Anything outside this set is
#: treated as ``"unknown"`` for matching purposes (R3, see plan
#: ``docs/plans/2026-05-14-001-feat-mandatory-linkcheck-lang-gate-plan.md``).
SUPPORTED_LANGUAGES = frozenset({"zh-CN", "ru", "en"})


# Simple keyword-based language hints (no external dependency)
# This is a rough heuristic — good enough for validation purposes.

ZH_HINTS = [
    "的", "是", "在", "了", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
    "它", "们", "里", "那", "个", "么", "什么", "怎么", "为什么",
]

RU_HINTS = [
    "и", "в", "не", "на", "я", "с", "что", "он", "к", "а",
    "то", "она", "так", "по", "но", "его", "для", "нет", "из",
    "это", "как", "у", "же", "за", "что", "если", "может",
    "также", "только", "уже", "всё", "все", "где", "ещё",
]

EN_HINTS = [
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i",
    "it", "for", "not", "on", "with", "he", "as", "you", "do", "at",
    "this", "but", "his", "by", "from", "they", "we", "say", "her", "she",
    "or", "an", "will", "my", "one", "all", "would", "there", "their",
    "what", "so", "up", "out", "if", "about", "who", "get", "which", "go",
]


def _score_language(text: str, hints: list[str]) -> int:
    """Count occurrences of language hints in text."""
    score = 0
    lower = text.lower()
    for hint in hints:
        score += lower.count(hint.lower())
    return score


def detect_language(text: str) -> str:
    """Roughly detect the language of a text.

    Returns one of: 'zh-CN', 'ru', 'en', or 'unknown'.
    """
    zh_score = _score_language(text, ZH_HINTS)
    ru_score = _score_language(text, RU_HINTS)
    en_score = _score_language(text, EN_HINTS)

    scores = {"zh-CN": zh_score, "ru": ru_score, "en": en_score}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "unknown"
    return best


def language_matches(detected: str, requested: str) -> bool:
    """Check if the detected language matches the requested language.

    Contract (R1, see plan 2026-05-14-001):
    - ``"unknown"`` on either side is the escape valve — returns True (the
      caller can't disprove a mismatch when one side is undetermined).
    - Two known, equal languages match.
    - Two known, different languages do NOT match — return False so the
      validate-time gate (R2) can fail the row.

    Languages outside :data:`SUPPORTED_LANGUAGES` are coerced to ``"unknown"``
    semantics: the gate cannot speak for them, so they pass.
    """
    if detected == "unknown" or requested == "unknown":
        return True
    if detected not in SUPPORTED_LANGUAGES or requested not in SUPPORTED_LANGUAGES:
        # Treat out-of-enum values as unknown — same "can't disprove" branch.
        return True
    return detected == requested