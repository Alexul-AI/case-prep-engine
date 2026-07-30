from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


HEBREW_WORD_RE = re.compile(r"[\u0590-\u05FF]{2,}")

# Domain-skewed common words observed in Israeli medical-committee records.
# Keep this explicit and testable; it is a heuristic, not OCR truth.
DEFAULT_HEBREW_LEXICON = frozenset(
    {
        "אני",
        "אתה",
        "הוא",
        "היא",
        "של",
        "עם",
        "על",
        "לא",
        "כן",
        "יש",
        "אין",
        "גם",
        "או",
        "כי",
        "אם",
        "זה",
        "זו",
        "הזה",
        "במהלך",
        "כאשר",
        "מאידך",
        "מחד",
        "מנסה",
        "מציין",
        "הרושם",
        "לטובה",
        "להרשים",
        "הוועדה",
        "ועדה",
        "רפואית",
        "רפואיות",
        "נכות",
        "נכה",
        "הנבדק",
        "המערער",
        "הנבדקת",
        "בבדיקה",
        "בדיקה",
        "דיון",
        "החלטת",
        "החלטה",
        "מסמכים",
        "מסמך",
        "פרוטוקול",
        "חוות",
        "דעת",
        "סיכום",
        "ביקור",
        "אבחנה",
        "אבחנות",
        "טיפול",
        "מעקב",
        "המשך",
        "המלצות",
        "מומחה",
        "נוירולוג",
        "פסיכיאטר",
        "טרשת",
        "נפוצה",
        "פוסט",
        "טראומה",
        "פלאשבקים",
        "משנית",
        "מצבים",
        "מצב",
        "כפי",
        "התרחש",
        "בפולין",
        "מזכיר",
        "דיסוציאציה",
        "קשר",
        "סיבתי",
        "זיקה",
        "סיבתית",
        "גבוהה",
        "בהסתברות",
        "תפקודי",
        "תפקוד",
        "מישורים",
        "במספר",
        "קיים",
        "זאת",
        "שככל",
        "שיפור",
        "החמרה",
        "מצבו",
        "נפשי",
        "ובהפרעות",
        "הפרעות",
        "שינה",
        "עבודה",
        "לעבוד",
        "חולשה",
        "כאבים",
        "הליכה",
        "ראייה",
        "קוגניטיבית",
        "זיכרון",
        "ריכוז",
        "אחוז",
        "אחוזי",
        "לצמיתות",
        "זמני",
        "תקנה",
    }
)


@dataclass(frozen=True)
class HebrewTextQualityResult:
    original_score: int
    reversed_score: int
    should_reverse: bool
    confidence: float
    status: str
    warning: str | None
    corrected_text: str


def reverse_lines(text: str) -> str:
    """Reverse each line independently, preserving line count."""
    return "\n".join(line[::-1] for line in text.splitlines())


def score_hebrew_readability(
    text: str, lexicon: Iterable[str] = DEFAULT_HEBREW_LEXICON
) -> int:
    words = HEBREW_WORD_RE.findall(text)
    known = set(lexicon)
    return sum(1 for word in words if word in known)


def assess_hebrew_text_quality(
    text: str,
    *,
    lexicon: Iterable[str] = DEFAULT_HEBREW_LEXICON,
    low_confidence_delta: int = 8,
    min_score_for_pass: int = 5,
) -> HebrewTextQualityResult:
    """Assess whether Hebrew text likely needs line-level reversal.

    The detector compares common-word counts in the text as extracted and after
    reversing each line. It intentionally returns confidence and warning fields
    because small margins should not silently unlock evidence gates.
    """
    if not text.strip():
        return HebrewTextQualityResult(
            original_score=0,
            reversed_score=0,
            should_reverse=False,
            confidence=0.0,
            status="needs_ocr",
            warning="empty text",
            corrected_text=text,
        )

    fixed_candidate = reverse_lines(text)
    original_score = score_hebrew_readability(text, lexicon)
    reversed_score = score_hebrew_readability(fixed_candidate, lexicon)
    diff = abs(reversed_score - original_score)
    denominator = max(original_score, reversed_score, 1)
    confidence = diff / denominator
    should_reverse = reversed_score > original_score

    best_score = max(original_score, reversed_score)
    if best_score < min_score_for_pass:
        status = "text_extracted_unverified"
        warning = "too few recognized Hebrew words for reliable QA"
    elif diff == 0:
        status = "text_extracted_unverified"
        warning = "as-is and reversed scores tie"
    else:
        status = "text_qa_passed"
        warning = None

    if status == "text_qa_passed" and diff < low_confidence_delta:
        warning = (
            f"low reversal margin: as-is={original_score}, "
            f"fixed={reversed_score}"
        )

    corrected_text = fixed_candidate if should_reverse else text
    return HebrewTextQualityResult(
        original_score=original_score,
        reversed_score=reversed_score,
        should_reverse=should_reverse,
        confidence=confidence,
        status=status,
        warning=warning,
        corrected_text=corrected_text,
    )


def assess_hebrew_payload_quality(text: str) -> HebrewTextQualityResult:
    """Assess a short evidence payload, such as a quote in the register.

    Short payloads have fewer repeated common words than full documents, so the
    pass threshold is intentionally lower. Direction and confidence are still
    returned for review.
    """
    return assess_hebrew_text_quality(
        text,
        low_confidence_delta=3,
        min_score_for_pass=2,
    )


def maybe_fix_hebrew_reversal(text: str) -> str:
    return assess_hebrew_text_quality(text).corrected_text
