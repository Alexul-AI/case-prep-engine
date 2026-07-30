"""Shared test-construction helpers, not a test module itself."""

from case_prep_engine.evidence_store import EvidenceRow, build_evidence_payload

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
    "source_note",
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
