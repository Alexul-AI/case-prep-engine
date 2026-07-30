# Case Prep Engine

Tooling to help prepare a תיק ועדה (medical committee case file) for נכי צה"ל
disability recognition — currently scoped to one case: תקנה 9, claimed causal
connection PTSD -> MS.

This is a preparation and organization assistant. It is not legal advice,
medical advice, or a replacement for a lawyer, physician, or official body.

## Layout

- `case_prep_engine/` — the Python package. Tracked in git.
- `tests/` — unit tests, using synthetic Hebrew fixtures (same domain
  lexicon as the real case, but not verbatim excerpts). Tracked in git.
- `docs/` — generic methodology and reference material with no personal
  case content: the evidence status model, and de-identified process/
  document-checklist guides. Tracked in git.
- `data/` — case-specific research: the evidence matrix, OCR gap
  registers, and readiness report, all of which contain real Hebrew
  quotes from real medical/committee documents. **Gitignored on purpose**
  — see "Privacy" below.

## Module boundary

`hebrew_text_quality.py` may set text-quality statuses (`text_qa_passed`,
`text_qa_failed`, `text_extracted_unverified`). It must not set claim-support
statuses (`supported_by_quote`, `supported_by_paraphrase`,
`checked_not_supported`) — a readable document is not the same as a
document that supports a claim. See `docs/case_prep_status_model_v3_provenance.md`
for the full three-layer model (text quality / claim support / output gate)
and the provenance rules that govern when a claim may be promoted.

## Run tests

```powershell
python -m unittest discover -s tests
```

## Privacy

Real Hebrew quotes from actual medical opinions and committee protocols
(diagnoses, PTSD symptoms, dissociation/fugue states, disability
percentages) live only in `data/`, which is excluded from git via
`.gitignore`. Code and tests use synthetic fixtures built from the same
domain lexicon so the git history never carries personal medical content,
even if this repository is ever pushed somewhere.

## Source documents

The underlying medical/legal source documents live in Google Drive, folder
`מסמכים רפואים - נכות צה"ל` (not mirrored locally; `data/*.csv` tracks which
documents have been read, their text-quality/claim-support/output-gate
status, and a `source_ref` Drive file id for each).
