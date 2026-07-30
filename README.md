# Case Prep Engine

Tooling to help נכי צה"ל prepare a תיק ועדה (medical committee case file) for
disability recognition, appeal, or a תקנה 9 secondary-disability claim: read
and structure medical/committee documents, track what's actually verified
vs. still assumed, and build a readiness picture before a ועדה רפואית.

This is a preparation and organization assistant, for personal use by
whoever runs it on their own documents. It is **not** legal advice, medical
advice, or a replacement for a lawyer, physician, or official body — always
verify anything it produces against your own case before relying on it.

## Layout

- `case_prep_engine/` — the Python package. Tracked in git.
- `tests/` — unit tests, using synthetic Hebrew fixtures (same domain
  lexicon as the real case, but not verbatim excerpts). Tracked in git.
- `docs/` — generic methodology and reference material with no personal
  case content: the evidence status model, and de-identified process/
  document-checklist guides. Tracked in git.
- `data/` — one person's case-specific research: their evidence matrix, OCR
  gap registers, and readiness notes, which contain real Hebrew quotes from
  real medical/committee documents. **Gitignored on purpose, always** — see
  "Privacy" below. If this ever runs for more than one person, each
  person's `data/` is theirs alone and is never read, uploaded, or shared
  by this codebase on anyone else's behalf.

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

Real Hebrew quotes from actual medical and committee documents (whoever's
case this is run on) live only in `data/`, excluded from git via
`.gitignore`. Code and tests use synthetic fixtures built from the same
domain lexicon so the git history never carries anyone's personal medical
content, even though this repository is public.

This tool currently reads source documents out of the runner's own Google
Drive via a locally-authorized connection. There is no shared backend yet.
If this ever runs for people other than the original author, the
deployment model and any use of outside services on real medical text need
a deliberate design pass first, not an assumption carried over from how
the single-user prototype works today.
