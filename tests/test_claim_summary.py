import unittest
from pathlib import Path

from case_prep_engine.claim_summary import (
    ClaimSummary,
    build_claim_summary_prompt,
    build_claim_summary_request,
    validate_claim_summary,
)
from case_prep_engine.evidence_matrix import build_evidence_matrix
from case_prep_engine.evidence_store import import_csv
from helpers import make_row

REAL_REGISTER_CSV = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "ocr_gap_register_v6_hebrew_payload.csv"
)


def entry_for(rows, claim_id="C99", case_id="personal", track_id="test_track"):
    return build_evidence_matrix(rows)[(case_id, track_id, claim_id)]


def request_for(rows, claim_id="C99", case_id="personal", track_id="test_track"):
    return build_claim_summary_request(entry_for(rows, claim_id, case_id, track_id))


class SupportedCaseTests(unittest.TestCase):
    def setUp(self):
        self.row = make_row(
            claim_id="C99",
            hebrew_verbatim="קיימת זיקה סיבתית בהסתברות גבוהה בין הפגיעה למחלה",
        )
        self.request = request_for([self.row])
        self.hash = self.row.payload.payload_hash

    def test_clean_summary_with_real_citation_passes(self):
        summary = ClaimSummary(
            claim_id="C99",
            status="supported",
            summary_he='המסמך קובע "קיימת זיקה סיבתית בהסתברות גבוהה" בין המצבים',
            summary_ru="Документ утверждает высокую вероятность причинной связи",
            allowed_uses=("brief",),
            must_not_say=(),
            citations=(self.hash,),
            open_risks=(),
        )
        self.assertEqual(validate_claim_summary(summary, self.request), [])

    def test_fake_citation_hash_is_rejected(self):
        summary = ClaimSummary(
            claim_id="C99", status="supported", summary_he="תקציר", summary_ru="",
            allowed_uses=(), must_not_say=(), citations=("not-a-real-hash",), open_risks=(),
        )
        problems = validate_claim_summary(summary, self.request)
        self.assertTrue(any("not a real payload_hash" in p for p in problems))

    def test_quote_not_present_in_any_payload_is_rejected(self):
        summary = ClaimSummary(
            claim_id="C99", status="supported",
            summary_he='המסמך אומר "משהו שלא נאמר באמת בשום מקום"',
            summary_ru="", allowed_uses=(), must_not_say=(),
            citations=(self.hash,), open_risks=(),
        )
        problems = validate_claim_summary(summary, self.request)
        self.assertTrue(any("not found verbatim" in p for p in problems))

    def test_dr_abbreviation_gershayim_does_not_false_positive_as_a_quote(self):
        # ד"ר (mid-word gershayim) must not be mistaken for a quotation.
        summary = ClaimSummary(
            claim_id="C99", status="supported",
            summary_he='לפי חוות דעתו של ד"ר גור וד"ר גרינהאוז, קיימת זיקה סיבתית בהסתברות גבוהה',
            summary_ru="", allowed_uses=(), must_not_say=(),
            citations=(self.hash,), open_risks=(),
        )
        problems = validate_claim_summary(summary, self.request)
        self.assertEqual(problems, [])

    def test_claim_id_mismatch_is_rejected(self):
        summary = ClaimSummary(
            claim_id="C-WRONG", status="supported", summary_he="", summary_ru="",
            allowed_uses=(), must_not_say=(), citations=(), open_risks=(),
        )
        problems = validate_claim_summary(summary, self.request)
        self.assertTrue(any("does not match" in p for p in problems))


class CausalWordingTests(unittest.TestCase):
    def setUp(self):
        self.causal_row = make_row(
            claim_id="C99",
            source_ref="doc-causal",
            hebrew_verbatim="קיים קשר סיבתי ישיר בין האירוע לבין המחלה",
        )
        self.noncausal_row = make_row(
            claim_id="C99",
            source_ref="doc-noncausal",
            hebrew_verbatim="המטופל דיווח על כאבים בגב התחתון",
        )

    def test_causal_wording_backed_by_cited_causal_payload_passes(self):
        request = request_for([self.causal_row])
        summary = ClaimSummary(
            claim_id="C99", status="supported",
            summary_he="קיים קשר סיבתי בין האירוע למחלה",
            summary_ru="", allowed_uses=(), must_not_say=(),
            citations=(self.causal_row.payload.payload_hash,), open_risks=(),
        )
        self.assertEqual(validate_claim_summary(summary, request), [])

    def test_causal_wording_not_backed_by_any_cited_payload_is_rejected(self):
        request = request_for([self.noncausal_row])
        summary = ClaimSummary(
            claim_id="C99", status="supported",
            summary_he="המחלה נגרמה כתוצאה מהאירוע",
            summary_ru="", allowed_uses=(), must_not_say=(),
            citations=(self.noncausal_row.payload.payload_hash,), open_risks=(),
        )
        problems = validate_claim_summary(summary, request)
        self.assertTrue(any("causal wording" in p for p in problems))

    def test_causal_payload_must_be_cited_not_merely_present_in_request(self):
        # The causal-supporting document exists in the request but isn't in
        # citations -- must still fail. Citing an uncited source doesn't count.
        request = request_for([self.causal_row, self.noncausal_row])
        summary = ClaimSummary(
            claim_id="C99", status="supported",
            summary_he="קיים קשר סיבתי בין האירוע למחלה",
            summary_ru="", allowed_uses=(), must_not_say=(),
            citations=(self.noncausal_row.payload.payload_hash,),  # wrong one cited
            open_risks=(),
        )
        problems = validate_claim_summary(summary, request)
        self.assertTrue(any("causal wording" in p for p in problems))


class ContradictionConflictAndMustNotSayTests(unittest.TestCase):
    def test_contradiction_must_be_reflected_in_status_or_open_risks(self):
        support = make_row(claim_id="C99", source_ref="doc-a")
        contradiction = make_row(
            claim_id="C99", source_ref="doc-b",
            claim_support_status="contradicted", output_gate="allowed_as_contradiction",
            payload_type="contradiction",
        )
        request = request_for([support, contradiction])

        silent = ClaimSummary(
            claim_id="C99", status="supported", summary_he="", summary_ru="",
            allowed_uses=(), must_not_say=("state MS is definitively linked",),
            citations=(support.payload.payload_hash,), open_risks=(),
        )
        self.assertTrue(
            any("contradiction" in p for p in validate_claim_summary(silent, request))
        )

        as_status = ClaimSummary(
            claim_id="C99", status="contradicted", summary_he="", summary_ru="",
            allowed_uses=(), must_not_say=("state MS is definitively linked",),
            citations=(contradiction.payload.payload_hash,), open_risks=(),
        )
        self.assertEqual(validate_claim_summary(as_status, request), [])

        as_open_risk = ClaimSummary(
            claim_id="C99", status="supported_with_risks", summary_he="", summary_ru="",
            allowed_uses=(), must_not_say=("state MS is definitively linked",),
            citations=(support.payload.payload_hash, contradiction.payload.payload_hash),
            open_risks=("a contradicting document exists",),
        )
        self.assertEqual(validate_claim_summary(as_open_risk, request), [])

    def test_negative_finding_must_be_cited_not_just_acknowledged(self):
        negative = make_row(
            claim_id="C99", claim_support_status="checked_not_supported",
            output_gate="allowed_as_negative_finding", payload_type="negative_finding",
        )
        request = request_for([negative])

        uncited = ClaimSummary(
            claim_id="C99", status="not_supported", summary_he="", summary_ru="",
            allowed_uses=(), must_not_say=("claim is supported",), citations=(), open_risks=(),
        )
        self.assertTrue(
            any("negative finding" in p for p in validate_claim_summary(uncited, request))
        )

        cited = ClaimSummary(
            claim_id="C99", status="not_supported", summary_he="", summary_ru="",
            allowed_uses=(), must_not_say=("claim is supported",),
            citations=(negative.payload.payload_hash,), open_risks=(),
        )
        self.assertEqual(validate_claim_summary(cited, request), [])

    def test_unresolved_conflict_forbids_supported_status(self):
        a = make_row(
            claim_id="C99", source_ref="doc-1", verified_utc="2026-07-29",
            claim_support_status="not_checked", output_gate="blocked",
        )
        b = make_row(
            claim_id="C99", source_ref="doc-1", verified_utc="2026-07-29",
            claim_support_status="supported_by_quote", output_gate="allowed_as_quote",
        )
        request = request_for([a, b])
        self.assertTrue(request.has_unresolved_conflict)

        supported = ClaimSummary(
            claim_id="C99", status="supported", summary_he="", summary_ru="",
            allowed_uses=(), must_not_say=("claim is settled",), citations=(), open_risks=(),
        )
        self.assertTrue(
            any("unresolved conflict" in p for p in validate_claim_summary(supported, request))
        )

        blocked = ClaimSummary(
            claim_id="C99", status="blocked", summary_he="", summary_ru="",
            allowed_uses=(), must_not_say=("claim is settled",), citations=(), open_risks=(),
        )
        self.assertEqual(validate_claim_summary(blocked, request), [])

    # --- dedicated tests for the must_not_say rule itself ---
    def test_must_not_say_required_when_conflict_present(self):
        a = make_row(
            claim_id="C99", source_ref="doc-1", verified_utc="2026-07-29",
            claim_support_status="not_checked", output_gate="blocked",
        )
        b = make_row(
            claim_id="C99", source_ref="doc-1", verified_utc="2026-07-29",
            claim_support_status="supported_by_quote", output_gate="allowed_as_quote",
        )
        request = request_for([a, b])
        summary = ClaimSummary(
            claim_id="C99", status="blocked", summary_he="", summary_ru="",
            allowed_uses=(), must_not_say=(), citations=(), open_risks=(),
        )
        problems = validate_claim_summary(summary, request)
        self.assertTrue(any("must_not_say cannot be empty" in p for p in problems))

    def test_must_not_say_not_required_for_clean_support(self):
        row = make_row(claim_id="C99")
        request = request_for([row])
        summary = ClaimSummary(
            claim_id="C99", status="supported", summary_he="", summary_ru="",
            allowed_uses=(), must_not_say=(),
            citations=(row.payload.payload_hash,), open_risks=(),
        )
        self.assertEqual(validate_claim_summary(summary, request), [])


class BuildClaimSummaryPromptTests(unittest.TestCase):
    def test_prompt_contains_claim_id_and_hash_and_rules(self):
        row = make_row(claim_id="C08", hebrew_verbatim="דוגמת ציטוט")
        request = request_for([row], claim_id="C08")
        prompt = build_claim_summary_prompt(request)
        self.assertIn("C08", prompt)
        self.assertIn(row.payload.payload_hash, prompt)
        self.assertIn("causal language", prompt)
        self.assertIn("Unresolved conflict on this claim: False", prompt)

    def test_prompt_reflects_unresolved_conflict_flag(self):
        a = make_row(
            claim_id="C08", source_ref="doc-1", verified_utc="2026-07-29",
            claim_support_status="not_checked", output_gate="blocked",
        )
        b = make_row(
            claim_id="C08", source_ref="doc-1", verified_utc="2026-07-29",
            claim_support_status="supported_by_quote", output_gate="allowed_as_quote",
        )
        request = request_for([a, b], claim_id="C08")
        prompt = build_claim_summary_prompt(request)
        self.assertIn("Unresolved conflict on this claim: True", prompt)


@unittest.skipUnless(
    REAL_REGISTER_CSV.exists(),
    "real case register (data/, gitignored) not present on this machine",
)
class RealRegisterClaimSummaryTests(unittest.TestCase):
    def test_real_gour_quote_validates_against_real_request(self):
        rows = import_csv(REAL_REGISTER_CSV)
        request = request_for(rows, "C08", track_id="takana9_ptsd_ms")
        gour_hash = request.supporting[0].payload_hash

        summary = ClaimSummary(
            claim_id="C08",
            status="supported",
            summary_he='חוות הדעת קובעת כי "קיימת זיקה סיבתית ... בהסתברות גבוהה"',
            summary_ru="Экспертное заключение утверждает высокую вероятность причинной связи",
            allowed_uses=("committee brief",),
            must_not_say=("that MS is officially recognized under תקנה 9",),
            citations=(gour_hash,),
            open_risks=(),
        )
        self.assertEqual(validate_claim_summary(summary, request), [])


if __name__ == "__main__":
    unittest.main()
