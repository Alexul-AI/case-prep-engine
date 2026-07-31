import unittest
from pathlib import Path

from case_prep_engine.claim_summary import (
    build_claim_summary_prompt,
    extract_allowed_payload_hashes,
    render_payload_block,
)
from case_prep_engine.evidence_matrix import build_evidence_matrix
from case_prep_engine.evidence_store import build_evidence_payload
from helpers import make_row

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def request_for(rows, claim_id="C-GOLDEN"):
    from case_prep_engine.claim_summary import build_claim_summary_request

    return build_claim_summary_request(build_evidence_matrix(rows)[claim_id])


def assert_matches_golden(test_case, prompt: str, golden_name: str):
    golden_path = GOLDEN_DIR / f"{golden_name}.txt"
    expected = golden_path.read_text(encoding="utf-8")
    test_case.assertEqual(
        prompt,
        expected,
        f"prompt no longer matches tests/golden/{golden_name}.txt -- if this "
        "wording change is intentional, regenerate the golden file and "
        "review the diff before committing it",
    )


class GoldenPromptTests(unittest.TestCase):
    """Fixed-scenario contract tests, per plan: not "can we call the LLM",
    but "what exactly do we give it and what schema do we demand back".

    Each golden file is the full, exact prompt text for one fixed
    scenario. A failure here means the prompt's actual wording changed --
    that's either an intentional improvement (regenerate and review the
    diff) or an accidental regression (the point of the test).
    """

    def test_support_only(self):
        request = request_for([
            make_row(
                claim_id="C-GOLDEN",
                source_ref="Drive fileId golden0000000000000support",
                hebrew_verbatim="המטופל דיווח על כאבים בגב התחתון וקושי בהליכה",
            )
        ])
        assert_matches_golden(self, build_claim_summary_prompt(request), "support_only")

    def test_support_and_contradiction(self):
        request = request_for([
            make_row(
                claim_id="C-GOLDEN",
                source_ref="Drive fileId golden0000000000000support",
                hebrew_verbatim="המטופל דיווח על כאבים בגב התחתון וקושי בהליכה",
            ),
            make_row(
                claim_id="C-GOLDEN",
                source_ref="Drive fileId golden0000000000000contra",
                hebrew_verbatim="הבדיקה שוללת קשר בין האירועים",
                claim_support_status="contradicted",
                output_gate="allowed_as_contradiction",
                payload_type="contradiction",
            ),
        ])
        assert_matches_golden(
            self, build_claim_summary_prompt(request), "support_and_contradiction"
        )

    def test_checked_not_supported(self):
        request = request_for([
            make_row(
                claim_id="C-GOLDEN",
                source_ref="Drive fileId golden0000000000negativefind",
                hebrew_verbatim="הועדה בדקה ולא מצאה תימוך לטענה",
                claim_support_status="checked_not_supported",
                output_gate="allowed_as_negative_finding",
                payload_type="negative_finding",
            )
        ])
        assert_matches_golden(
            self, build_claim_summary_prompt(request), "checked_not_supported"
        )

    def test_conflict(self):
        request = request_for([
            make_row(
                claim_id="C-GOLDEN", source_ref="doc-golden-conflict",
                verified_utc="2026-07-29",
                claim_support_status="not_checked", output_gate="blocked",
            ),
            make_row(
                claim_id="C-GOLDEN", source_ref="doc-golden-conflict",
                verified_utc="2026-07-29",
                claim_support_status="supported_by_quote", output_gate="allowed_as_quote",
            ),
        ])
        assert_matches_golden(self, build_claim_summary_prompt(request), "conflict")

    def test_causal_wording(self):
        request = request_for([
            make_row(
                claim_id="C-GOLDEN",
                source_ref="Drive fileId golden0000000000000causal",
                hebrew_verbatim="קיים קשר סיבתי ישיר בין הפגיעה בשירות לבין החמרת המחלה",
            )
        ])
        assert_matches_golden(self, build_claim_summary_prompt(request), "causal_wording")

    def test_quote_safety_dr_abbreviation(self):
        # A payload with embedded Hebrew gershayim (ד"ר, כ"הס -- the exact
        # pattern in the real Gour document) must render unmangled and
        # without visual ambiguity about where the payload text ends.
        request = request_for([
            make_row(
                claim_id="C-GOLDEN",
                source_ref="Drive fileId golden0000000000drtitlequote",
                hebrew_verbatim='לדעת ד"ר גור, קיימת זיקה סיבתית בהסתברות גבוהה, ראו גם כ"הס 62.20%',
            )
        ])
        assert_matches_golden(
            self, build_claim_summary_prompt(request), "quote_safety_dr_abbreviation"
        )


class RenderPayloadBlockPrivacyGuardTests(unittest.TestCase):
    """Mechanical checks that the prompt can only ever carry a narrow,
    fixed slice of a payload -- not the full case file, and not fields
    that exist for local bookkeeping only.
    """

    def test_render_payload_block_contains_only_hash_source_ref_and_text(self):
        payload = build_evidence_payload(
            payload_type="quote",
            hebrew_verbatim="דוגמת טקסט לבדיקה",
            source_ref="Drive fileId privacy-check-ref",
            claim_id="C-GOLDEN",
            verification_method="drive_fetch",
            verified_by_actor="tester",
            verified_utc="2026-07-29",
            source_location="עמוד 3, פסקה 2 -- MUST NOT LEAK",
            translation_ru="Пример текста -- MUST NOT LEAK",
            source_note="internal note -- MUST NOT LEAK",
        )
        block = render_payload_block(payload)
        self.assertIn(payload.payload_hash, block)
        self.assertIn(payload.source_ref, block)
        self.assertIn(payload.hebrew_verbatim, block)
        self.assertNotIn("MUST NOT LEAK", block)
        self.assertNotIn(payload.translation_ru, block)
        self.assertNotIn(payload.source_location, block)
        self.assertNotIn(payload.source_note, block)

    def test_prompt_never_leaks_hidden_payload_fields(self):
        row = make_row(
            claim_id="C-GOLDEN",
            hebrew_verbatim="טקסט ציטוט אמיתי",
            source_location="עמוד 1 -- HIDDEN LOCATION",
            translation_ru="ПЕРЕВОД -- HIDDEN TRANSLATION",
            source_note="ЗАМЕТКА -- HIDDEN NOTE",
        )
        request = request_for([row])
        prompt = build_claim_summary_prompt(request)
        self.assertNotIn("HIDDEN LOCATION", prompt)
        self.assertNotIn("HIDDEN TRANSLATION", prompt)
        self.assertNotIn("HIDDEN NOTE", prompt)

    def test_prompt_contains_no_evidence_outside_the_request(self):
        # A second claim's evidence, built with a real hash, must never
        # appear in claim A's prompt -- only what's in claim A's own
        # request.
        row_a = make_row(claim_id="C-A", source_ref="doc-a", hebrew_verbatim="תוכן תביעה א")
        row_b = make_row(claim_id="C-B", source_ref="doc-b", hebrew_verbatim="תוכן תביעה ב -- FOREIGN CLAIM")
        request_a = request_for([row_a, row_b], claim_id="C-A")

        self.assertNotIn(row_b.payload.payload_hash, extract_allowed_payload_hashes(request_a))

        prompt = build_claim_summary_prompt(request_a)
        self.assertIn(row_a.payload.hebrew_verbatim, prompt)
        self.assertNotIn("FOREIGN CLAIM", prompt)
        self.assertNotIn(row_b.payload.payload_hash, prompt)


class ExtractAllowedPayloadHashesTests(unittest.TestCase):
    def test_returns_hashes_from_all_three_checked_buckets(self):
        support = make_row(claim_id="C-GOLDEN", source_ref="doc-1")
        negative = make_row(
            claim_id="C-GOLDEN", source_ref="doc-2",
            claim_support_status="checked_not_supported",
            output_gate="allowed_as_negative_finding", payload_type="negative_finding",
        )
        contradiction = make_row(
            claim_id="C-GOLDEN", source_ref="doc-3",
            claim_support_status="contradicted",
            output_gate="allowed_as_contradiction", payload_type="contradiction",
        )
        request = request_for([support, negative, contradiction])
        allowed = extract_allowed_payload_hashes(request)
        self.assertEqual(
            allowed,
            {
                support.payload.payload_hash,
                negative.payload.payload_hash,
                contradiction.payload.payload_hash,
            },
        )

    def test_conflict_has_no_allowed_hashes(self):
        a = make_row(
            claim_id="C-GOLDEN", source_ref="doc-1", verified_utc="2026-07-29",
            claim_support_status="not_checked", output_gate="blocked",
        )
        b = make_row(
            claim_id="C-GOLDEN", source_ref="doc-1", verified_utc="2026-07-29",
            claim_support_status="supported_by_quote", output_gate="allowed_as_quote",
        )
        request = request_for([a, b])
        self.assertEqual(extract_allowed_payload_hashes(request), frozenset())


if __name__ == "__main__":
    unittest.main()
