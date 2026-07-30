import unittest
from pathlib import Path

from case_prep_engine.evidence_store import import_csv
from case_prep_engine.timeline import (
    build_timeline,
    extract_date_from_document_title,
    infer_event_type,
)
from helpers import make_row

REAL_REGISTER_CSV = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "ocr_gap_register_v6_hebrew_payload.csv"
)


class ExtractDateFromDocumentTitleTests(unittest.TestCase):
    def test_day_precision_dot_separated(self):
        date, precision = extract_date_from_document_title(
            "חוות דעת ד\"ר אביב גור, נוירולוג (22.06.2025)"
        )
        self.assertEqual(date, "2025-06-22")
        self.assertEqual(precision, "day")

    def test_day_precision_underscore_separated(self):
        date, precision = extract_date_from_document_title(
            "IDF injury report 07_02_2009"
        )
        self.assertEqual(date, "2009-02-07")
        self.assertEqual(precision, "day")

    def test_year_only_precision(self):
        date, precision = extract_date_from_document_title("מסמכי אבחון MS 2019")
        self.assertEqual(date, "2019")
        self.assertEqual(precision, "year")

    def test_no_date_found(self):
        date, precision = extract_date_from_document_title(
            "C12 — general OCR-gap warning"
        )
        self.assertEqual(date, "")
        self.assertEqual(precision, "unknown")

    def test_israeli_day_month_convention_not_us_month_day(self):
        # 22.06.2025 must be June 22nd, not (invalid as MM.DD) month 22.
        date, _ = extract_date_from_document_title("(22.06.2025)")
        self.assertEqual(date, "2025-06-22")


class InferEventTypeTests(unittest.TestCase):
    def test_committee_protocol(self):
        self.assertEqual(
            infer_event_type("פרוטוקול ועדה מחוזית 16.07.2025"), "committee"
        )

    def test_expert_opinion(self):
        self.assertEqual(
            infer_event_type("חוות דעת ד\"ר אביב גור, נוירולוג (22.06.2025)"),
            "expert_opinion",
        )

    def test_service_event(self):
        self.assertEqual(infer_event_type("IDF injury report 07_02_2009"), "service_event")

    def test_unclassifiable_title_is_unknown_not_a_guess(self):
        self.assertEqual(infer_event_type("C12 — general OCR-gap warning"), "unknown")


class BuildTimelineTests(unittest.TestCase):
    def test_verified_row_with_extractable_date(self):
        row = make_row(document="פרוטוקול ועדה מחוזית (22.06.2025)", claim_id="C08")
        events = build_timeline([row])
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_date, "2025-06-22")
        self.assertEqual(event.date_precision, "day")
        self.assertEqual(event.date_source, "filename")
        self.assertEqual(event.event_type, "committee")
        self.assertEqual(event.evidence_status, "verified_events")
        self.assertEqual(event.claim_id, "C08")
        self.assertTrue(event.payload_hash)

    def test_verified_row_without_extractable_date_is_date_unresolved(self):
        row = make_row(document="Undated expert opinion", claim_id="C08")
        events = build_timeline([row])
        self.assertEqual(events[0].evidence_status, "date_unresolved")
        self.assertEqual(events[0].date_precision, "unknown")

    def test_blocked_row_is_blocked_or_unverified_even_with_a_clean_date(self):
        row = make_row(
            document="Rambam summary (07.02.2009)",
            claim_id="C01",
            text_quality_status="needs_ocr",
            claim_support_status="metadata_only",
            output_gate="blocked",
        )
        events = build_timeline([row])
        # Date extraction still runs (visible for debugging/UI purposes),
        # but the evidence itself being unchecked takes precedence.
        self.assertEqual(events[0].date_precision, "day")
        self.assertEqual(events[0].evidence_status, "blocked_or_unverified")

    def test_conflict_takes_precedence_over_everything_else(self):
        a = make_row(
            document="Doc A (01.01.2026)",
            claim_id="C04",
            source_ref="doc-1",
            verified_utc="2026-07-29",
            claim_support_status="not_checked",
            output_gate="blocked",
        )
        b = make_row(
            document="Doc A (01.01.2026)",
            claim_id="C04",
            source_ref="doc-1",
            verified_utc="2026-07-29",
            claim_support_status="supported_by_quote",
            output_gate="allowed_as_quote",
        )
        events = build_timeline([a, b])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].evidence_status, "conflicts")
        self.assertEqual(events[0].payload_hash, "")

    def test_two_claims_on_one_document_produce_two_events(self):
        c14 = make_row(document="Greenhouse opinion (12.11.2025)", claim_id="C14")
        c15 = make_row(document="Greenhouse opinion (12.11.2025)", claim_id="C15")
        events = build_timeline([c14, c15])
        self.assertEqual(len(events), 2)
        self.assertEqual({e.claim_id for e in events}, {"C14", "C15"})
        self.assertTrue(all(e.event_date == "2025-11-12" for e in events))

    def test_sorted_chronologically_with_undated_events_last(self):
        early = make_row(document="Early (01.01.2020)", claim_id="C01", source_ref="d1")
        late = make_row(document="Late (01.01.2026)", claim_id="C02", source_ref="d2")
        undated = make_row(document="Undated event", claim_id="C03", source_ref="d3")
        events = build_timeline([undated, late, early])
        self.assertEqual(
            [e.document for e in events],
            ["Early (01.01.2020)", "Late (01.01.2026)", "Undated event"],
        )


@unittest.skipUnless(
    REAL_REGISTER_CSV.exists(),
    "real case register (data/, gitignored) not present on this machine",
)
class RealRegisterTimelineTests(unittest.TestCase):
    def test_timeline_shape_against_real_register(self):
        rows = import_csv(REAL_REGISTER_CSV)
        events = build_timeline(rows)
        self.assertTrue(events)

        # Sorted: every event_date must be non-decreasing, with any
        # unknown-date ("") events pushed to the end.
        dated = [e.event_date for e in events if e.event_date]
        self.assertEqual(dated, sorted(dated))
        undated_positions = [i for i, e in enumerate(events) if e.event_date == ""]
        dated_positions = [i for i, e in enumerate(events) if e.event_date != ""]
        if undated_positions and dated_positions:
            self.assertGreater(min(undated_positions), max(dated_positions))

        # C08's real event (Gour opinion, 22.06.2025) is a verified event.
        gour_events = [e for e in events if e.claim_id == "C08"]
        self.assertEqual(len(gour_events), 1)
        self.assertEqual(gour_events[0].event_date, "2025-06-22")
        self.assertEqual(gour_events[0].evidence_status, "verified_events")

        # C01 (Rambam + IDF report, needs_ocr/blocked) must be
        # blocked_or_unverified, never verified_events, even though both
        # titles contain a clean, extractable 07.02.2009 date.
        c01_events = [e for e in events if e.claim_id == "C01"]
        self.assertEqual(len(c01_events), 2)
        self.assertTrue(all(e.evidence_status == "blocked_or_unverified" for e in c01_events))


if __name__ == "__main__":
    unittest.main()
