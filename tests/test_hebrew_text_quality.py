import unittest

from case_prep_engine.hebrew_text_quality import (
    assess_hebrew_payload_quality,
    assess_hebrew_text_quality,
    reverse_lines,
)


# Synthetic fixtures: built from the same domain lexicon as the real case
# documents (see data/*.csv, gitignored), but not verbatim excerpts from any
# real committee protocol or expert opinion. Kept out of real personal
# content on purpose so this test file can live in git history.
COMMITTEE_STYLE_QUOTE = (
    "הוועדה הרפואית ציינה כי קיים שיפור תפקודי במספר מישורים\n"
    "אולם מאידך מצוין כי ההחמרה במצבו הנפשי ובהפרעות השינה נמשכת"
)

PSYCH_EXPERT_STYLE_QUOTE = (
    "תועדו מצבים של דיסוציאציה משנית לפלאשבקים בעקבות אירוע טראומטי\n"
    "קיימת זיקה סיבתית בהסתברות גבוהה בין ההפרעה הנפשית לבין הנכות"
)

TAKANA9_OBJECTION_STYLE_QUOTE = (
    "הניסוח בחוות הדעת נמצא מתפתל מדי לטעם הוועדה\n"
    "תקנה 9 דורשת קשר סיבתי ישיר ולא רק זיקה עקיפה"
)


class HebrewTextQualityTests(unittest.TestCase):
    def test_does_not_reverse_clean_committee_quote(self):
        result = assess_hebrew_payload_quality(COMMITTEE_STYLE_QUOTE)
        self.assertFalse(result.should_reverse)
        self.assertEqual(result.corrected_text, COMMITTEE_STYLE_QUOTE)
        self.assertEqual(result.status, "text_qa_passed")

    def test_reverses_reversed_committee_quote(self):
        broken = reverse_lines(COMMITTEE_STYLE_QUOTE)
        result = assess_hebrew_payload_quality(broken)
        self.assertTrue(result.should_reverse)
        self.assertEqual(result.corrected_text, COMMITTEE_STYLE_QUOTE)
        self.assertEqual(result.status, "text_qa_passed")

    def test_does_not_reverse_clean_expert_quote(self):
        result = assess_hebrew_payload_quality(PSYCH_EXPERT_STYLE_QUOTE)
        self.assertFalse(result.should_reverse)
        self.assertEqual(result.status, "text_qa_passed")

    def test_reverses_reversed_takana9_objection(self):
        broken = reverse_lines(TAKANA9_OBJECTION_STYLE_QUOTE)
        result = assess_hebrew_payload_quality(broken)
        self.assertTrue(result.should_reverse)
        self.assertEqual(result.corrected_text, TAKANA9_OBJECTION_STYLE_QUOTE)

    def test_empty_text_needs_ocr(self):
        result = assess_hebrew_text_quality("")
        self.assertEqual(result.status, "needs_ocr")
        self.assertFalse(result.should_reverse)


if __name__ == "__main__":
    unittest.main()
