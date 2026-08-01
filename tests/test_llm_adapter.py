import json
import unittest
from pathlib import Path

from case_prep_engine.claim_summary import (
    ClaimSummary,
    build_claim_summary_request,
)
from case_prep_engine.evidence_matrix import build_evidence_matrix
from case_prep_engine.evidence_store import import_csv
from case_prep_engine.llm_adapter import (
    JsonOnlyClaimSummaryLLM,
    LLMResponseError,
    MockClaimSummaryLLM,
    parse_claim_summary_json,
)
from helpers import make_row

REAL_REGISTER_CSV = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "ocr_gap_register_v6_hebrew_payload.csv"
)


def request_for(rows, claim_id="C99", case_id="personal", track_id="test_track"):
    return build_claim_summary_request(
        build_evidence_matrix(rows)[(case_id, track_id, claim_id)]
    )


class MockClaimSummaryLLMTests(unittest.TestCase):
    def test_fixed_response(self):
        fixed = ClaimSummary(
            claim_id="C99", status="blocked", summary_he="", summary_ru="",
            allowed_uses=(), must_not_say=(), citations=(), open_risks=(),
        )
        mock = MockClaimSummaryLLM(fixed)
        row = make_row(claim_id="C99")
        request = request_for([row])
        self.assertEqual(mock.summarize_claim(request), fixed)

    def test_computed_response(self):
        mock = MockClaimSummaryLLM(
            lambda request: ClaimSummary(
                claim_id=request.claim_id, status="blocked", summary_he="",
                summary_ru="", allowed_uses=(), must_not_say=(), citations=(), open_risks=(),
            )
        )
        row = make_row(claim_id="C42")
        request = request_for([row], claim_id="C42")
        self.assertEqual(mock.summarize_claim(request).claim_id, "C42")


class ParseClaimSummaryJsonTests(unittest.TestCase):
    def setUp(self):
        self.row = make_row(claim_id="C99", hebrew_verbatim="קיימת זיקה סיבתית בהסתברות גבוהה")
        self.request = request_for([self.row])
        self.hash = self.row.payload.payload_hash

    def test_valid_response_parses_and_validates(self):
        raw = json.dumps(
            {
                "claim_id": "C99",
                "status": "supported",
                "summary_he": "קיימת זיקה סיבתית בהסתברות גבוהה",
                "summary_ru": "",
                "citations": [self.hash],
            },
            ensure_ascii=False,
        )
        summary = parse_claim_summary_json(raw, self.request)
        self.assertEqual(summary.status, "supported")
        self.assertEqual(summary.citations, (self.hash,))

    def test_invalid_json_raises_llm_response_error(self):
        with self.assertRaises(LLMResponseError) as ctx:
            parse_claim_summary_json("{not valid json", self.request)
        self.assertIn("not valid JSON", str(ctx.exception))
        self.assertEqual(ctx.exception.raw_response, "{not valid json")

    def test_json_array_instead_of_object_raises(self):
        with self.assertRaises(LLMResponseError) as ctx:
            parse_claim_summary_json("[1, 2, 3]", self.request)
        self.assertIn("must be an object", str(ctx.exception))

    def test_missing_required_field_raises(self):
        raw = json.dumps({"claim_id": "C99", "status": "supported"})
        with self.assertRaises(LLMResponseError) as ctx:
            parse_claim_summary_json(raw, self.request)
        self.assertIn("summary_he", str(ctx.exception))

    def test_fabricated_citation_raises_with_problems_populated(self):
        raw = json.dumps(
            {
                "claim_id": "C99",
                "status": "supported",
                "summary_he": "תקציר",
                "citations": ["not-a-real-hash"],
            }
        )
        with self.assertRaises(LLMResponseError) as ctx:
            parse_claim_summary_json(raw, self.request)
        self.assertTrue(
            any("not a real payload_hash" in p for p in ctx.exception.problems)
        )

    def test_unearned_supported_status_on_conflict_raises(self):
        a = make_row(
            claim_id="C99", source_ref="doc-1", verified_utc="2026-07-29",
            claim_support_status="not_checked", output_gate="blocked",
        )
        b = make_row(
            claim_id="C99", source_ref="doc-1", verified_utc="2026-07-29",
            claim_support_status="supported_by_quote", output_gate="allowed_as_quote",
        )
        request = request_for([a, b])
        raw = json.dumps(
            {"claim_id": "C99", "status": "supported", "summary_he": "הכל תקין"}
        )
        with self.assertRaises(LLMResponseError) as ctx:
            parse_claim_summary_json(raw, request)
        self.assertTrue(
            any("unresolved conflict" in p for p in ctx.exception.problems)
        )

    def test_unknown_extra_json_fields_are_ignored_not_rejected(self):
        raw = json.dumps(
            {
                "claim_id": "C99",
                "status": "supported",
                "summary_he": "קיימת זיקה סיבתית בהסתברות גבוהה",
                "citations": [self.hash],
                "some_field_the_model_invented": "ignored",
            }
        )
        summary = parse_claim_summary_json(raw, self.request)
        self.assertEqual(summary.status, "supported")


class JsonOnlyClaimSummaryLLMTests(unittest.TestCase):
    def test_happy_path_round_trip(self):
        row = make_row(claim_id="C99", hebrew_verbatim="קיימת זיקה סיבתית בהסתברות גבוהה")
        request = request_for([row])

        def fake_completion(prompt: str) -> str:
            self.assertIn("C99", prompt)  # proves the real prompt was built and used
            return json.dumps(
                {
                    "claim_id": "C99",
                    "status": "supported",
                    "summary_he": "קיימת זיקה סיבתית בהסתברות גבוהה",
                    "citations": [row.payload.payload_hash],
                }
            )

        llm = JsonOnlyClaimSummaryLLM(fake_completion)
        summary = llm.summarize_claim(request)
        self.assertEqual(summary.claim_id, "C99")
        self.assertEqual(summary.status, "supported")

    def test_bad_model_output_propagates_as_llm_response_error(self):
        row = make_row(claim_id="C99")
        request = request_for([row])
        llm = JsonOnlyClaimSummaryLLM(lambda prompt: "not json at all")
        with self.assertRaises(LLMResponseError):
            llm.summarize_claim(request)


@unittest.skipUnless(
    REAL_REGISTER_CSV.exists(),
    "real case register (data/, gitignored) not present on this machine",
)
class RealRegisterLLMAdapterTests(unittest.TestCase):
    def test_json_only_llm_round_trip_against_real_c08_request(self):
        rows = import_csv(REAL_REGISTER_CSV)
        request = request_for(rows, "C08", track_id="takana9_ptsd_ms")
        gour_hash = request.supporting[0].payload.payload_hash

        def fake_completion(prompt: str) -> str:
            return json.dumps(
                {
                    "claim_id": "C08",
                    "status": "supported",
                    "summary_he": "חוות הדעת תומכת בקשר סיבתי",
                    "citations": [gour_hash],
                    "must_not_say": [],
                }
            )

        llm = JsonOnlyClaimSummaryLLM(fake_completion)
        summary = llm.summarize_claim(request)
        self.assertEqual(summary.claim_id, "C08")
        self.assertEqual(summary.citations, (gour_hash,))


if __name__ == "__main__":
    unittest.main()
