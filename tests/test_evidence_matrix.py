import unittest
from pathlib import Path

from case_prep_engine.evidence_matrix import build_evidence_matrix
from case_prep_engine.evidence_store import import_csv
from helpers import make_row

REAL_REGISTER_CSV = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "ocr_gap_register_v6_hebrew_payload.csv"
)


def mkey(claim_id: str) -> tuple[str, str, str]:
    """Matches helpers.make_row's default case_id/track_id."""
    return ("personal", "test_track", claim_id)


def real_key(claim_id: str) -> tuple[str, str, str]:
    """Matches the real register's default (no case_id/track_id columns)."""
    return ("personal", "takana9_ptsd_ms", claim_id)


class BuildEvidenceMatrixTests(unittest.TestCase):
    def test_single_supporting_row(self):
        row = make_row(claim_id="C08", source_ref="doc-1")
        matrix = build_evidence_matrix([row])
        entry = matrix[mkey("C08")]
        self.assertTrue(entry.has_support)
        self.assertFalse(entry.has_contradiction)
        self.assertFalse(entry.has_negative_finding)
        self.assertFalse(entry.has_unresolved_conflict)
        self.assertEqual(len(entry.supporting), 1)

    def test_multiple_documents_support_the_same_claim(self):
        a = make_row(claim_id="C09", source_ref="doc-a")
        b = make_row(claim_id="C09", source_ref="doc-b")
        matrix = build_evidence_matrix([a, b])
        self.assertEqual(len(matrix[mkey("C09")].supporting), 2)

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
        entry = matrix[mkey("C05")]
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
        self.assertTrue(matrix[mkey("C10")].has_negative_finding)

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
        entry = matrix[mkey("C01")]
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
        entry = matrix[mkey("C04")]
        self.assertTrue(entry.has_unresolved_conflict)
        self.assertFalse(entry.has_support)
        self.assertEqual(len(entry.conflicts), 1)

    # --- regression: case/track scoping ---
    def test_same_claim_id_in_different_cases_produces_separate_entries(self):
        alex = make_row(case_id="alex_personal", claim_id="C01", source_ref="doc-1")
        brother = make_row(case_id="brother_case", claim_id="C01", source_ref="doc-1")
        matrix = build_evidence_matrix([alex, brother])
        self.assertEqual(len(matrix), 2)
        self.assertIn(("alex_personal", "test_track", "C01"), matrix)
        self.assertIn(("brother_case", "test_track", "C01"), matrix)
        self.assertTrue(matrix[("alex_personal", "test_track", "C01")].has_support)
        self.assertTrue(matrix[("brother_case", "test_track", "C01")].has_support)


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
            self.assertIn(real_key(claim_id), matrix)
            self.assertTrue(matrix[real_key(claim_id)].has_support, claim_id)

        # C09 specifically has two independent supporting documents.
        self.assertEqual(len(matrix[real_key("C09")].supporting), 2)

        # C01 (Rambam 2009 + IDF injury report): both rows are needs_ocr/
        # blocked and must never show up as support. This used to land in
        # `conflicts` instead of `unresolved`, because both rows shared the
        # literal source_ref text "needs_ocr per both parallel passes" (a
        # status note, not a real Drive id) -- a real register data bug,
        # now fixed (each row has its own real Drive fileId, the note moved
        # to source_note). Two genuinely separate unresolved documents now
        # resolve as two separate, non-conflicting entries.
        self.assertIn(real_key("C01"), matrix)
        self.assertFalse(matrix[real_key("C01")].has_support)
        self.assertFalse(matrix[real_key("C01")].has_unresolved_conflict)
        self.assertEqual(len(matrix[real_key("C01")].unresolved), 2)


if __name__ == "__main__":
    unittest.main()
