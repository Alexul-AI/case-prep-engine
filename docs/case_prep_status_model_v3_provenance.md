# Case-Prep Evidence Status Model v3: Provenance And Time

This version adds the missing rule exposed by the C04 register conflict:

`current` without a timestamp is not a fact. It is only one snapshot's opinion.

## Core Principle

Every evidence row must separate:

1. document readability;
2. claim support;
3. output permission;
4. provenance;
5. time.

If multiple agents, tools, or sessions work on the same case, a row is not authoritative unless its verification event is timestamped and traceable.

## Required Time And Provenance Fields

Each row should include:

- `snapshot_utc`: when this register snapshot was produced.
- `row_updated_utc`: when this row was last changed.
- `verified_utc`: when the underlying source was actually verified.
- `verified_by_actor`: person/agent/tool that performed the verification.
- `verification_session_id`: stable id for the session/thread/run, if available.
- `verification_method`: how it was verified, for example `drive_fetch`, `download_raw_ocr`, `manual_read`, `external_agent_report`.
- `source_ref`: Drive id, local file path, URL, or other stable document reference.
- `evidence_payload_ref`: pointer to the extracted quote/paraphrase/check note.
- `evidence_payload_hash`: optional hash of the evidence payload, once available.
- `imported_at_utc`: when an external verification was imported into this register.
- `staleness_status`: `fresh`, `stale`, `conflict_detected`, or `superseded`.

## Status Fields

### Text Quality Status

- `file_found`
- `needs_ocr`
- `text_extracted_unverified`
- `text_qa_failed`
- `text_qa_passed`
- `external_text_qa_passed_pending_import`

### Claim Support Status

- `metadata_only`
- `provisional`
- `not_checked`
- `supported_by_paraphrase`
- `supported_by_quote`
- `external_supported_pending_evidence_import`
- `checked_not_supported`
- `contradicted`

### Output Gate

- `blocked`
- `blocked_pending_evidence_import`
- `allowed_as_unverified`
- `allowed_as_synthesis`
- `allowed_as_quote`
- `allowed_as_negative_finding`
- `allowed_as_contradiction`

## Import Rule

An external agent/user may report that a document was read and a claim was supported. That event can update provenance immediately, but it should not unlock full factual generation unless one of these is present:

- exact quote;
- paraphrase note with source location;
- evidence payload file;
- reproducible source reference and extraction method.

Until then, use:

- text quality: `external_text_qa_passed_pending_import`
- claim support: `external_supported_pending_evidence_import`
- output gate: `blocked_pending_evidence_import`

This prevents the engine from replacing one unverified confidence error with another.

## Conflict Rule

When two registers disagree, do not ask which file says `current`.

Compare:

1. `verified_utc`
2. `row_updated_utc`
3. `imported_at_utc`
4. `verification_session_id`
5. `evidence_payload_ref`

The newer claim wins only if it has adequate provenance for its output gate.

## C04 Lesson

The v4 register marked C04 as blocked because, at that snapshot time, it had not been independently read.

The user later reported that C04 was read directly before the v4 file was uploaded. Therefore, v4 was not lying; it was stale.

The correct fix is not just to edit C04. The correct fix is to make stale state observable.
