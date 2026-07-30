import unittest
from pathlib import Path

from case_prep_engine.evidence_matrix import build_evidence_matrix
from case_prep_engine.evidence_store import EvidenceRow, build_evidence_payload, import_csv

REAL_REGISTER_CSV = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "ocr_gap_register_v6_hebrew_payload.csv"
)

_PAYLOAD_FIELD_NAMES = {
    "payload_type",
    "hebrew_verbatim",
    "source_ref",
    "claim_id",
    "verification_method",
    "verified_by_actor",
    "verified_utc",
    "source_location",
    "translation_ru",
}


def make_row(**overrides) -> EvidenceRow:
    row_defaults = dict(
        document="Test Document",
        text_quality_status="text_qa_passed",
        claim_support_status="supported_by_quote",
        output_gate="allowed_as_quote",
        staleness_status="fresh",
    )
    payload_defaults = dict(
        payload_type="quote",
        hebrew_verbatim="דוגמת טקסט",
        source_ref="drive-id-1",
        claim_id="C99",
        verification_method="manual_read",
        verified_by_actor="tester",
        verified_utc="2026-01-01T00:00:00+00:00",
    )
    for key, value in overrides.items():
        if key in _PAYLOAD_FIELD_NAMES:
            payload_defaults[key] = value
        else:
            row_defaults[key] = value
    return EvidenceRow(payload=build_evidence_payload(**payload_defaults), **row_defaults)


class BuildEvidenceMatrixTests(unittest.TestCase):
    def test_single_supporting_row(self):
        row = make_row(claim_id="C08", source_ref="doc-1")
        matrix = build_evidence_matrix([row])
        entry = matrix["C08"]
        self.assertTrue(entry.has_support)
        self.assertFalse(entry.has_contradiction)
        self.assertFalse(entry.has_negative_finding)
        self.assertFalse(entry.has_unresolved_conflict)
        self.assertEqual(len(entry.supporting), 1)

    def test_multiple_documents_support_the_same_claim(self):
        a = make_row(claim_id="C09", source_ref="doc-a")
        b = make_row(claim_id="C09", source_ref="doc-b")
        matrix = build_evidence_matrix([a, b])
        self.assertEqual(len(matrix["C09"].supporting), 2)

    def test_support_and_contradiction_both_surface_for_the_same_claim(self):
        # A claim can have a document that supports it AND a document that
        # contradicts it -- the matrix must show both, not collapse to one.
        support = make_row(claim_id="C05", source_ref="doc-a")
        contradiction = make_row(
            claim_id="C05",
            source_ref="doc-b",
            claim_support_status="contradicted",
            output_gate="allowed_as_contradiction",
            payload_type="contradiction",
        )
        matrix = build_evidence_matrix([support, contradiction])
        entry = matrix["C05"]
        self.assertTrue(entry.has_support)
        self.assertTrue(entry.has_contradiction)
        self.assertEqual(len(entry.supporting), 1)
        self.assertEqual(len(entry.contradictions), 1)

    def test_negative_finding_bucket(self):
        row = make_row(
            claim_id="C10",
            claim_support_status="checked_not_supported",
            output_gate="allowed_as_negative_finding",
            payload_type="negative_finding",
        )
        matrix = build_evidence_matrix([row])
        self.assertTrue(matrix["C10"].has_negative_finding)

    def test_blocked_row_is_unresolved_not_supporting(self):
        row = make_row(
            claim_id="C01",
            text_quality_status="needs_ocr",
            claim_support_status="metadata_only",
            output_gate="blocked",
            payload_type="none",
            hebrew_verbatim="",
            verification_method="",
            verified_by_actor="",
            verified_utc="unknown",
            source_ref="unknown",
        )
        matrix = build_evidence_matrix([row])
        entry = matrix["C01"]
        self.assertFalse(entry.has_support)
        self.assertEqual(len(entry.unresolved), 1)

    def test_conflicted_group_never_counts_as_support(self):
        # Two same-day date-only rows disagreeing -- resolve_current_state
        # flags this as a conflict. Even though the (arbitrarily chosen)
        # row inside the conflict entry has output_gate=allowed_as_quote,
        # the matrix must not count that as real support.
        a = make_row(
            claim_id="C04",
            source_ref="doc-1",
            verified_utc="2026-07-29",
            claim_support_status="not_checked",
            output_gate="blocked",
        )
        b = make_row(
            claim_id="C04",
            source_ref="doc-1",
            verified_utc="2026-07-29",
            claim_support_status="supported_by_quote",
            output_gate="allowed_as_quote",
        )
        matrix = build_evidence_matrix([a, b])
        entry = matrix["C04"]
        self.assertTrue(entry.has_unresolved_conflict)
        self.assertFalse(entry.has_support)
        self.assertEqual(len(entry.conflicts), 1)


@unittest.skipUnless(
    REAL_REGISTER_CSV.exists(),
    "real case register (data/, gitignored) not present on this machine",
)
class RealRegisterEvidenceMatrixTests(unittest.TestCase):
    def test_matrix_shape_against_real_register(self):
        rows = import_csv(REAL_REGISTER_CSV)
        matrix = build_evidence_matrix(rows)

        # C08 (Gour opinion), C09 (transcript + protocol -- two documents,
        # one claim), C04 (16.07.2025 protocol), C14/C15 (Greenhouse) are
        # all real, verified quotes as of the current register.
        for claim_id in ("C08", "C09", "C04", "C14", "C15"):
            self.assertIn(claim_id, matrix)
            self.assertTrue(matrix[claim_id].has_support, claim_id)

        # C09 specifically has two independent supporting documents.
        self.assertEqual(len(matrix["C09"].supporting), 2)

        # C01 (Rambam 2009 + IDF injury report): both rows are needs_ocr/
        # blocked and must never show up as support. They currently land in
        # `conflicts` rather than `unresolved`, for a real, separate reason
        # worth knowing about: both rows share the literal source_ref text
        # "needs_ocr per both parallel passes" (a status note, not a real
        # Drive id) -- key() treats that as a real, shared identity, so the
        # two different physical documents collapse into one group, and
        # since neither has a real verified_utc, resolve_current_state
        # correctly calls that a conflict rather than guessing. This is a
        # register data-quality gap (fix: give each row a real source_ref,
        # or a placeholder value that key() already recognizes as such,
        # e.g. "—"), not a bug in build_evidence_matrix.
        self.assertIn("C01", matrix)
        self.assertFalse(matrix["C01"].has_support)
        self.assertTrue(matrix["C01"].has_unresolved_conflict)


if __name__ == "__main__":
    unittest.main()
