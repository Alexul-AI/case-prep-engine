import tempfile
import unittest
from pathlib import Path

from case_prep_engine.evidence_store import (
    EvidenceRow,
    EvidenceStore,
    import_csv,
    parse_verified_utc,
    resolve_current_state,
    validate_row,
)

REAL_REGISTER_CSV = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "ocr_gap_register_v6_hebrew_payload.csv"
)


def make_row(**overrides) -> EvidenceRow:
    defaults = dict(
        document="Test Document",
        source_ref="unknown",
        related_claims="C99",
        text_quality_status="text_qa_passed",
        claim_support_status="supported_by_quote",
        output_gate="allowed_as_quote",
        staleness_status="fresh",
        verified_by_actor="tester",
        verification_method="manual_read",
        verified_utc="2026-01-01T00:00:00+00:00",
        evidence_payload_hebrew_verbatim="דוגמת טקסט",
    )
    defaults.update(overrides)
    return EvidenceRow(**defaults)


class ValidateRowTests(unittest.TestCase):
    def test_clean_quote_row_has_no_problems(self):
        self.assertEqual(validate_row(make_row()), [])

    def test_unknown_status_is_flagged(self):
        problems = validate_row(make_row(text_quality_status="totally_made_up"))
        self.assertTrue(any("text_quality_status" in p for p in problems))

    def test_output_gate_not_licensed_by_claim_support(self):
        # not_checked cannot license allowed_as_quote -- that's exactly the
        # false-promotion bug the C04 lesson is about.
        problems = validate_row(
            make_row(claim_support_status="not_checked", output_gate="allowed_as_quote")
        )
        self.assertTrue(any("not licensed" in p for p in problems))

    def test_quote_gate_requires_payload(self):
        problems = validate_row(
            make_row(output_gate="allowed_as_quote", evidence_payload_hebrew_verbatim="")
        )
        self.assertTrue(any("requires a non-empty" in p for p in problems))

    def test_negative_finding_is_a_valid_gate(self):
        problems = validate_row(
            make_row(
                claim_support_status="checked_not_supported",
                output_gate="allowed_as_negative_finding",
                evidence_payload_hebrew_verbatim="לא נמצא תימוך בטענה",
            )
        )
        self.assertEqual(problems, [])


class ParseVerifiedUtcTests(unittest.TestCase):
    def test_parses_iso_timestamp(self):
        self.assertIsNotNone(parse_verified_utc("2026-07-29T21:34:17Z"))

    def test_sentinels_return_none(self):
        for sentinel in ("", "unknown", "n/a", "—", "after-v4-snapshot"):
            self.assertIsNone(parse_verified_utc(sentinel))


class EvidenceStoreRoundtripTests(unittest.TestCase):
    def test_append_then_read_all_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp) / "evidence.jsonl")
            row = make_row()
            store.append([row])
            loaded = store.read_all()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].document, row.document)
            self.assertEqual(
                loaded[0].evidence_payload_hebrew_verbatim,
                row.evidence_payload_hebrew_verbatim,
            )

    def test_read_all_on_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp) / "does-not-exist.jsonl")
            self.assertEqual(store.read_all(), [])

    def test_append_is_additive_not_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp) / "evidence.jsonl")
            store.append([make_row(document="A")])
            store.append([make_row(document="B")])
            loaded = store.read_all()
            self.assertEqual([r.document for r in loaded], ["A", "B"])


class ResolveCurrentStateTests(unittest.TestCase):
    def test_single_row_is_current_no_conflict(self):
        row = make_row(source_ref="doc-1")
        resolved = resolve_current_state([row])
        self.assertEqual(resolved["doc-1"].row, row)
        self.assertFalse(resolved["doc-1"].conflict)
        self.assertTrue(resolved["doc-1"].has_verified_timestamp)

    def test_newer_timestamp_wins_without_conflict(self):
        older = make_row(
            source_ref="doc-1",
            verified_utc="2026-01-01T00:00:00+00:00",
            claim_support_status="not_checked",
            output_gate="blocked",
        )
        newer = make_row(source_ref="doc-1", verified_utc="2026-06-01T00:00:00+00:00")
        resolved = resolve_current_state([older, newer])
        self.assertEqual(resolved["doc-1"].row, newer)
        self.assertFalse(resolved["doc-1"].conflict)

    def test_no_timestamps_at_all_is_a_conflict(self):
        # This is the C04 case: two candidate rows, neither with a real
        # verified_utc -- must not silently trust "the last one".
        a = make_row(source_ref="doc-1", verified_utc="unknown")
        b = make_row(source_ref="doc-1", verified_utc="after-v4-snapshot")
        resolved = resolve_current_state([a, b])
        self.assertTrue(resolved["doc-1"].conflict)

    def test_tied_newest_timestamps_is_a_conflict(self):
        a = make_row(
            source_ref="doc-1",
            verified_by_actor="agent-a",
            verified_utc="2026-06-01T00:00:00+00:00",
        )
        b = make_row(
            source_ref="doc-1",
            verified_by_actor="agent-b",
            verified_utc="2026-06-01T00:00:00+00:00",
        )
        resolved = resolve_current_state([a, b])
        self.assertTrue(resolved["doc-1"].conflict)

    def test_groups_by_source_ref_not_document_title(self):
        a = make_row(source_ref="drive-id-123", document="Old Title")
        b = make_row(source_ref="drive-id-123", document="Renamed Title")
        resolved = resolve_current_state([a, b])
        self.assertEqual(len(resolved), 1)


@unittest.skipUnless(
    REAL_REGISTER_CSV.exists(),
    "real case register (data/, gitignored) not present on this machine",
)
class ImportRealRegisterCsvTests(unittest.TestCase):
    """Integration check against this runner's own real register, if present.

    Skipped for anyone who clones this repo without their own data/ folder
    -- data/ is gitignored on purpose (see README), so this file only
    exists for whoever generated it locally.
    """

    def test_imports_all_rows_and_they_all_validate(self):
        rows = import_csv(REAL_REGISTER_CSV)
        self.assertGreater(len(rows), 0)
        for row in rows:
            problems = validate_row(row)
            self.assertEqual(
                problems, [], f"{row.document!r} failed validation: {problems}"
            )


if __name__ == "__main__":
    unittest.main()
