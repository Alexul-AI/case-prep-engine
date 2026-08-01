import tempfile
import unittest
from pathlib import Path

from case_prep_engine.evidence_store import (
    EvidencePayload,
    EvidenceStore,
    build_evidence_payload,
    compute_payload_hash,
    import_csv,
    infer_payload_type,
    infer_verified_precision,
    looks_like_stable_identifier,
    parse_verified_utc,
    resolve_current_state,
    validate_row,
)
from helpers import make_row

REAL_REGISTER_CSV = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "ocr_gap_register_v6_hebrew_payload.csv"
)

DEFAULT_KEY_PREFIX = ("personal", "test_track")  # matches helpers.make_row's defaults


def key(source_ref_or_document: str, claim_id: str) -> tuple[str, str, str, str]:
    return (*DEFAULT_KEY_PREFIX, source_ref_or_document, claim_id)


class EvidencePayloadTests(unittest.TestCase):
    def test_build_evidence_payload_derives_precision_and_hash(self):
        payload = build_evidence_payload(
            hebrew_verbatim="טקסט לדוגמה",
            source_ref="drive-1",
            verification_method="drive_fetch",
            verified_by_actor="tester",
            verified_utc="2026-07-29",
        )
        self.assertEqual(payload.verified_precision, "date")
        self.assertEqual(
            payload.payload_hash,
            compute_payload_hash("drive-1", "טקסט לדוגמה"),
        )

    def test_identical_core_content_hashes_the_same_regardless_of_provenance(self):
        a = build_evidence_payload(
            hebrew_verbatim="טקסט", source_ref="drive-1",
            verification_method="drive_fetch",
            verified_by_actor="agent-a", verified_utc="2026-07-29",
        )
        b = build_evidence_payload(
            hebrew_verbatim="טקסט", source_ref="drive-1",
            verification_method="manual_read",
            verified_by_actor="agent-b", verified_utc="2026-08-01T10:00:00+00:00",
        )
        self.assertEqual(a.payload_hash, b.payload_hash)

    def test_same_content_hashes_the_same_regardless_of_claim(self):
        # The whole point of moving claim_id off the payload: the same
        # Greenhouse-style quote backing two different claims is still one
        # piece of evidence, not two.
        a = build_evidence_payload(
            hebrew_verbatim="טקסט", source_ref="drive-1",
            verification_method="x", verified_by_actor="x", verified_utc="2026-07-29",
        )
        row_c14 = make_row(claim_id="C14", hebrew_verbatim="טקסט", source_ref="drive-1")
        row_c15 = make_row(claim_id="C15", hebrew_verbatim="טקסט", source_ref="drive-1")
        self.assertEqual(row_c14.payload.payload_hash, row_c15.payload.payload_hash)
        self.assertEqual(row_c14.payload.payload_hash, a.payload_hash)

    def test_different_verbatim_text_hashes_differently(self):
        a = build_evidence_payload(
            hebrew_verbatim="טקסט א", source_ref="drive-1",
            verification_method="x", verified_by_actor="x", verified_utc="2026-07-29",
        )
        b = build_evidence_payload(
            hebrew_verbatim="טקסט ב", source_ref="drive-1",
            verification_method="x", verified_by_actor="x", verified_utc="2026-07-29",
        )
        self.assertNotEqual(a.payload_hash, b.payload_hash)

    def test_tampered_hash_is_detected_by_validate_row(self):
        payload = build_evidence_payload(
            hebrew_verbatim="טקסט מקורי", source_ref="drive-1",
            verification_method="x", verified_by_actor="x", verified_utc="2026-07-29",
        )
        # Simulate the hebrew_verbatim being edited after the hash was computed.
        tampered = EvidencePayload(**{**payload.__dict__, "hebrew_verbatim": "טקסט שונה"})
        row = make_row()
        object.__setattr__(row, "payload", tampered)
        problems = validate_row(row)
        self.assertTrue(any("payload_hash does not match" in p for p in problems))


class InferPrecisionAndPayloadTypeTests(unittest.TestCase):
    def test_date_only_is_date_precision(self):
        self.assertEqual(infer_verified_precision("2026-07-29"), "date")

    def test_full_timestamp_is_instant_precision(self):
        self.assertEqual(
            infer_verified_precision("2026-07-29T14:30:00+00:00"), "instant"
        )

    def test_sentinel_is_unknown_precision(self):
        self.assertEqual(infer_verified_precision("unknown"), "unknown")

    def test_payload_type_inferred_from_claim_support_status(self):
        self.assertEqual(infer_payload_type("supported_by_quote"), "quote")
        self.assertEqual(infer_payload_type("supported_by_paraphrase"), "paraphrase")
        self.assertEqual(infer_payload_type("checked_not_supported"), "negative_finding")
        self.assertEqual(infer_payload_type("contradicted"), "contradiction")
        self.assertEqual(infer_payload_type("not_checked"), "none")
        self.assertEqual(infer_payload_type("metadata_only"), "none")


class ValidateRowTests(unittest.TestCase):
    def test_clean_quote_row_has_no_problems(self):
        self.assertEqual(validate_row(make_row()), [])

    def test_unknown_status_is_flagged(self):
        problems = validate_row(make_row(text_quality_status="totally_made_up"))
        self.assertTrue(any("text_quality_status" in p for p in problems))

    def test_output_gate_not_licensed_by_claim_support(self):
        problems = validate_row(
            make_row(claim_support_status="not_checked", output_gate="allowed_as_quote")
        )
        self.assertTrue(any("not licensed" in p for p in problems))

    def test_quote_gate_requires_payload(self):
        problems = validate_row(
            make_row(output_gate="allowed_as_quote", hebrew_verbatim="")
        )
        self.assertTrue(any("requires a non-empty" in p for p in problems))

    def test_negative_finding_is_a_valid_gate(self):
        problems = validate_row(
            make_row(
                claim_support_status="checked_not_supported",
                output_gate="allowed_as_negative_finding",
                payload_type="negative_finding",
                hebrew_verbatim="לא נמצא תימוך בטענה",
            )
        )
        self.assertEqual(problems, [])

    def test_quote_gate_requires_real_provenance_not_just_payload(self):
        problems = validate_row(
            make_row(
                source_ref="unknown",
                verification_method="",
                verified_by_actor="",
                verified_utc="unknown",
            )
        )
        self.assertTrue(any("source_ref" in p for p in problems))
        self.assertTrue(any("verification_method" in p for p in problems))
        self.assertTrue(any("verified_by_actor" in p for p in problems))
        self.assertTrue(any("verified_utc" in p for p in problems))

    def test_quote_gate_requires_text_qa_passed(self):
        problems = validate_row(make_row(text_quality_status="needs_ocr"))
        self.assertTrue(
            any("text_quality_status" in p and "allowed_as_quote" in p for p in problems)
        )

    # --- date-only is accepted, not rejected, for allowed_as_quote ---
    def test_date_only_verified_utc_alone_is_not_a_problem(self):
        problems = validate_row(make_row(verified_utc="2026-07-29"))
        self.assertEqual(problems, [])

    # --- regression: source-identity hygiene (the C01 bug class) ---
    def test_prose_source_ref_is_flagged_even_on_a_blocked_row(self):
        # Applies unconditionally, not just to the strict output gates --
        # a blocked/unresolved row can still corrupt identity grouping.
        problems = validate_row(
            make_row(
                claim_support_status="metadata_only",
                output_gate="blocked",
                source_ref="needs_ocr per both parallel passes",
            )
        )
        self.assertTrue(any("doesn't look like" in p for p in problems))

    def test_placeholder_source_ref_is_not_flagged_as_prose(self):
        problems = validate_row(
            make_row(
                claim_support_status="metadata_only",
                output_gate="blocked",
                source_ref="—",
            )
        )
        self.assertFalse(any("doesn't look like" in p for p in problems))

    # --- regression: case/track/claim scoping must not be silently empty ---
    def test_empty_case_id_is_flagged(self):
        problems = validate_row(make_row(case_id=""))
        self.assertTrue(any("case_id" in p for p in problems))

    def test_empty_track_id_is_flagged(self):
        problems = validate_row(make_row(track_id=""))
        self.assertTrue(any("track_id" in p for p in problems))


class LooksLikeStableIdentifierTests(unittest.TestCase):
    def test_drive_fileid_prefixed_form_is_an_identifier(self):
        self.assertTrue(
            looks_like_stable_identifier("Drive fileId 1USNHDNiERb6Mg6UCra9pNDOabbwi5uCL")
        )

    def test_bare_id_is_an_identifier(self):
        self.assertTrue(looks_like_stable_identifier("1USNHDNiERb6Mg6UCra9pNDOabbwi5uCL"))

    def test_url_is_an_identifier(self):
        self.assertTrue(looks_like_stable_identifier("https://drive.google.com/file/d/abc123"))

    def test_multiword_note_is_not_an_identifier(self):
        # The actual real-world bug: this exact string was used as
        # source_ref for two different documents in the real register.
        self.assertFalse(
            looks_like_stable_identifier("needs_ocr per both parallel passes")
        )

    def test_empty_string_is_not_an_identifier(self):
        self.assertFalse(looks_like_stable_identifier(""))


class ParseVerifiedUtcTests(unittest.TestCase):
    def test_parses_iso_timestamp(self):
        self.assertIsNotNone(parse_verified_utc("2026-07-29T21:34:17Z"))

    def test_sentinels_return_none(self):
        for sentinel in ("", "unknown", "n/a", "—", "after-v4-snapshot"):
            self.assertIsNone(parse_verified_utc(sentinel))

    def test_date_only_and_full_timestamp_are_both_aware_and_comparable(self):
        date_only = parse_verified_utc("2026-07-29")
        full = parse_verified_utc("2026-07-30T00:00:00+00:00")
        self.assertIsNotNone(date_only.tzinfo)
        self.assertIsNotNone(full.tzinfo)
        self.assertLess(date_only, full)  # must not raise TypeError


class EvidenceStoreRoundtripTests(unittest.TestCase):
    def test_append_then_read_all_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp) / "evidence.jsonl")
            row = make_row()
            store.append([row])
            loaded = store.read_all()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].document, row.document)
            self.assertEqual(loaded[0].case_id, row.case_id)
            self.assertEqual(loaded[0].track_id, row.track_id)
            self.assertEqual(loaded[0].claim_id, row.claim_id)
            self.assertEqual(
                loaded[0].payload.hebrew_verbatim, row.payload.hebrew_verbatim
            )
            self.assertEqual(loaded[0].payload.payload_hash, row.payload.payload_hash)

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
        entry = resolved[key("doc-1", "C99")]
        self.assertEqual(entry.row, row)
        self.assertFalse(entry.conflict)
        self.assertTrue(entry.has_verified_timestamp)

    def test_newer_timestamp_wins_without_conflict(self):
        older = make_row(
            source_ref="doc-1",
            verified_utc="2026-01-01T00:00:00+00:00",
            claim_support_status="not_checked",
            output_gate="blocked",
        )
        newer = make_row(source_ref="doc-1", verified_utc="2026-06-01T00:00:00+00:00")
        resolved = resolve_current_state([older, newer])
        self.assertEqual(resolved[key("doc-1", "C99")].row, newer)
        self.assertFalse(resolved[key("doc-1", "C99")].conflict)

    def test_no_timestamps_at_all_is_a_conflict(self):
        a = make_row(source_ref="doc-1", verified_utc="unknown")
        b = make_row(source_ref="doc-1", verified_utc="after-v4-snapshot")
        resolved = resolve_current_state([a, b])
        self.assertTrue(resolved[key("doc-1", "C99")].conflict)

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
        self.assertTrue(resolved[key("doc-1", "C99")].conflict)

    def test_groups_by_source_ref_not_document_title(self):
        a = make_row(source_ref="drive-id-123", document="Old Title")
        b = make_row(source_ref="drive-id-123", document="Renamed Title")
        resolved = resolve_current_state([a, b])
        self.assertEqual(len(resolved), 1)

    # --- regression: source-identity hygiene, the exact C01 scenario ---
    def test_shared_prose_source_ref_does_not_collapse_different_documents(self):
        # Two genuinely different documents that both happen to carry the
        # same free-text note as source_ref (the real C01 bug: "needs_ocr
        # per both parallel passes" on both a hospitalization summary and
        # a separate injury report) must resolve as two separate entries,
        # falling back to their own document titles -- not one entry that
        # silently drops one of them.
        a = make_row(
            document="סיכום אשפוז",
            claim_id="C01",
            source_ref="needs_ocr per both parallel passes",
            claim_support_status="metadata_only",
            output_gate="blocked",
        )
        b = make_row(
            document="IDF injury report",
            claim_id="C01",
            source_ref="needs_ocr per both parallel passes",
            claim_support_status="metadata_only",
            output_gate="blocked",
        )
        resolved = resolve_current_state([a, b])
        self.assertEqual(len(resolved), 2)
        self.assertIn(key("סיכום אשפוז", "C01"), resolved)
        self.assertIn(key("IDF injury report", "C01"), resolved)
        self.assertFalse(resolved[key("סיכום אשפוז", "C01")].conflict)
        self.assertFalse(resolved[key("IDF injury report", "C01")].conflict)

    def test_different_claims_on_same_document_do_not_collapse(self):
        c14 = make_row(
            source_ref="drive-xyz",
            claim_id="C14",
            verified_utc="2026-07-29T00:00:00+00:00",
        )
        c15 = make_row(
            source_ref="drive-xyz",
            claim_id="C15",
            verified_utc="2026-07-30T00:00:00+00:00",
        )
        resolved = resolve_current_state([c14, c15])
        self.assertEqual(len(resolved), 2)
        self.assertIn(key("drive-xyz", "C14"), resolved)
        self.assertIn(key("drive-xyz", "C15"), resolved)
        self.assertFalse(resolved[key("drive-xyz", "C14")].conflict)
        self.assertFalse(resolved[key("drive-xyz", "C15")].conflict)

    # --- explicit policy check: same-day date-only, different conclusions ---
    def test_same_day_date_only_rows_with_different_conclusions_conflict(self):
        first_read = make_row(
            source_ref="doc-1",
            verified_utc="2026-07-29",  # date-only
            claim_support_status="not_checked",
            output_gate="blocked",
        )
        second_read_same_day = make_row(
            source_ref="doc-1",
            verified_utc="2026-07-29",  # date-only, same day
            claim_support_status="supported_by_quote",
            output_gate="allowed_as_quote",
        )
        resolved = resolve_current_state([first_read, second_read_same_day])
        entry = resolved[key("doc-1", "C99")]
        self.assertTrue(entry.conflict)
        self.assertEqual(
            {r.payload.verified_precision for r in entry.candidates}, {"date"}
        )

    # --- regression: case/track scoping prevents claim_id collisions ---
    def test_same_claim_id_in_different_cases_does_not_collide(self):
        alex = make_row(case_id="alex_personal", source_ref="doc-1", claim_id="C01")
        brother = make_row(case_id="brother_case", source_ref="doc-1", claim_id="C01")
        resolved = resolve_current_state([alex, brother])
        self.assertEqual(len(resolved), 2)
        self.assertIn(("alex_personal", "test_track", "doc-1", "C01"), resolved)
        self.assertIn(("brother_case", "test_track", "doc-1", "C01"), resolved)

    def test_same_claim_id_in_different_tracks_does_not_collide(self):
        a = make_row(track_id="takana9_ptsd_ms", source_ref="doc-1", claim_id="C01")
        b = make_row(track_id="ptsd_worsening", source_ref="doc-1", claim_id="C01")
        resolved = resolve_current_state([a, b])
        self.assertEqual(len(resolved), 2)


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
                problems,
                [],
                f"{row.document!r}/{row.claim_id!r} failed: {problems}",
            )

    def test_real_register_defaults_to_personal_case_and_takana9_track(self):
        # The real register predates case_id/track_id -- confirms the CSV
        # fallback (not an empty string, which validate_row would reject).
        rows = import_csv(REAL_REGISTER_CSV)
        self.assertTrue(rows)
        self.assertTrue(all(r.case_id == "personal" for r in rows))
        self.assertTrue(all(r.track_id == "takana9_ptsd_ms" for r in rows))

    def test_multi_claim_row_splits_into_separate_rows(self):
        rows = import_csv(REAL_REGISTER_CSV)
        greenhouse_claim_ids = {
            r.claim_id for r in rows if "גרינהאוז" in r.document
        }
        self.assertEqual(greenhouse_claim_ids, {"C14", "C15"})

    def test_real_rows_use_date_precision_not_instant(self):
        # Documents the current, real state of the register: this is why
        # validate_row must not require 'instant' precision today.
        rows = import_csv(REAL_REGISTER_CSV)
        real_verifications = [
            r for r in rows if r.payload.verified_precision != "unknown"
        ]
        self.assertTrue(real_verifications)
        self.assertTrue(
            all(r.payload.verified_precision == "date" for r in real_verifications)
        )


if __name__ == "__main__":
    unittest.main()
