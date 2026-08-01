"""Shared test-construction helpers, not a test module itself."""

from case_prep_engine.evidence_store import (
    EvidenceRow,
    build_evidence_payload,
    compute_default_evidence_id,
)

_PAYLOAD_FIELD_NAMES = {
    "hebrew_verbatim",
    "source_ref",
    "verification_method",
    "verified_by_actor",
    "verified_utc",
    "source_location",
    "translation_ru",
    "source_note",
}


def make_row(**overrides) -> EvidenceRow:
    """Build a test EvidenceRow, defaulting evidence_id from content.

    Leaving evidence_id unset (the common case) derives it from
    case_id/track_id/document/source_ref/claim_id/payload_hash, same as
    import_csv() does for a CSV row with a blank evidence_id cell -- so two
    make_row() calls that differ only in hebrew_verbatim naturally get
    distinct evidence_ids, matching real behavior instead of accidentally
    colliding. Pass evidence_id explicitly to force a specific value (e.g.
    to simulate two re-verifications of the *same* quote, which must
    resolve to the *same* evidence_id).
    """
    row_defaults = dict(
        document="Test Document",
        case_id="personal",
        track_id="test_track",
        claim_id="C99",
        evidence_id="",
        text_quality_status="text_qa_passed",
        claim_support_status="supported_by_quote",
        output_gate="allowed_as_quote",
        payload_type="quote",
        staleness_status="fresh",
    )
    payload_defaults = dict(
        hebrew_verbatim="דוגמת טקסט",
        source_ref="drive-id-1",
        verification_method="manual_read",
        verified_by_actor="tester",
        verified_utc="2026-01-01T00:00:00+00:00",
    )
    for key, value in overrides.items():
        if key in _PAYLOAD_FIELD_NAMES:
            payload_defaults[key] = value
        else:
            row_defaults[key] = value
    payload = build_evidence_payload(**payload_defaults)
    if not row_defaults["evidence_id"]:
        row_defaults["evidence_id"] = compute_default_evidence_id(
            case_id=row_defaults["case_id"],
            track_id=row_defaults["track_id"],
            document=row_defaults["document"],
            source_ref=payload.source_ref,
            claim_id=row_defaults["claim_id"],
            payload_hash=payload.payload_hash,
        )
    return EvidenceRow(payload=payload, **row_defaults)
