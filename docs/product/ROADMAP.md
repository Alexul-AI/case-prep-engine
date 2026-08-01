# Case Prep Engine — PRD & Roadmap

Added 2026-08-01. Mirrors the product-doc convention used in the author's
other repos (a single `docs/product/ROADMAP.md` as the up-to-date plan of
record) — read this before proposing new work or discussing scope.

## What this is

A preparation and organization assistant for נכי צה"ל disability
recognition/appeal/תקנה 9 work: read and structure medical/committee
documents, track what's actually verified vs. still assumed, and build a
readiness picture before a ועדה רפואית. Not legal or medical advice —
every claim it can generate is gated behind explicit evidence, and it
refuses to state anything it can't back with a citation.

## Who it's for

**Phase 1 (current): one person, their own case.** The author, running it
against their own documents.

**Phase 2 (planned, not started): friends and family who ask to try it.**
Explicitly deferred until Phase 1 is solid — see "Not yet done" below.
Already decided (2026-07-30): each person runs their own local instance
against their own Google Drive; there is no shared backend, and none is
planned. What "runs their own instance" means in practice — a Claude Code
session per person vs. a real packaged, installable app — is still an
open question, revisit when Phase 1 is actually ready to hand off.

## Architecture built so far (2026-07-30 → 2026-07-31, 131 tests)

Each stage below was built directly on the previous one, in order, and
each one deliberately stayed *simpler* than the temptation to skip ahead
— mock/deterministic before real, manual before automated:

1. **`hebrew_text_quality.py`** — detects and fixes line-reversed Hebrew
   text extraction (a real, recurring OCR/Drive-extraction failure mode).
2. **`evidence_store.py`** — the typed, append-only evidence log.
   `EvidenceRow`/`EvidencePayload` are atomic per (document, claim);
   `resolve_current_state()` picks the current belief per claim from
   history and **flags an unresolved conflict instead of guessing** when
   provenance doesn't clearly support one answer over another;
   `looks_like_stable_identifier()` stops free-text notes from being used
   as document identity (a real bug this caught: two different documents
   silently merged into one because both had the same placeholder-ish
   `source_ref`).
3. **`evidence_matrix.py`** — groups resolved evidence by claim into
   supporting / contradicting / negative-finding / unresolved / conflict
   buckets. Purely mechanical, no narrative, no verdict — deliberately
   stops short of writing anything that reads like a conclusion.
4. **`timeline.py`** — a *separate* date-based projection over the same
   evidence, not built on top of the matrix (a chronological narrative
   invites causal storytelling — "X happened, then Y, therefore..." — in
   a way a per-claim matrix doesn't, and causal storytelling is exactly
   the contested legal question in a תקנה 9 case, so this boundary is
   deliberate, not incidental).
5. **`claim_summary.py`** — the LLM-facing contract. `ClaimSummaryRequest`
   is exactly what a model may see for one claim (nothing else exists to
   it); `validate_claim_summary()` is the actual safety boundary — no
   fabricated citations, no quote that isn't verbatim in the evidence, no
   causal language beyond what a *cited* source itself uses, no silent
   dropping of a contradiction or a negative finding.
6. **`llm_adapter.py`** — `ClaimSummaryLLM` Protocol, a `MockClaimSummaryLLM`
   for tests, and `JsonOnlyClaimSummaryLLM` (prompt → completion → parse →
   validate) that takes a plain `completion_fn` — provider-agnostic by
   construction. **No real network call anywhere in this codebase yet.**
7. **`tests/golden/*.txt`** — exact-text prompt-contract fixtures for
   support / contradiction / negative-finding / conflict / causal-wording
   / Hebrew-abbreviation-safety scenarios.
8. **`cli.py`** — `summarize-claim` (`--fake`, `--list-claims`, `--strict`,
   `--show-prompt`, `--output`), plus a manual bridge to any real model a
   person already has access to: `export-claim-prompt` (freezes the exact
   request to a file) and `validate-summary` (checks a hand-pasted reply
   against that frozen request, not a live re-derivation that could have
   drifted).
9. **`examples/demo_register.csv`** — synthetic, safe-to-publish register
   backing the README quickstart, so anyone can try the tool with zero
   setup and zero personal data.

## Design rules that have held since day one

- **Text readability ≠ claim support ≠ permission to generate.** Three
  separate gates, never collapsed (`docs/case_prep_status_model_v2.md`,
  `v3_provenance.md`).
- **Never silently pick "current" without real provenance.** A conflict
  gets surfaced, not resolved by guessing which of two disagreeing
  records is right.
- **De-risk before automating, at every layer.** Deterministic before
  generative, mock before real network, manual copy-paste bridge before
  API integration. A real external review already caught 4 concrete bugs
  in `evidence_store.py` this way before any of it reached a real model.
- **Personal case content never enters git history.** Code, tests, and
  this file are generic and public; anything containing a real document's
  actual text lives only in the gitignored `data/`, local to whoever runs
  it — including for a future Phase 2 user.

## Next up

**Multi-track support** (near-term, blocks nothing else but is now a real
gap): the schema and CLI currently treat all claims as one flat
namespace. In practice, one person's case can span several genuinely
parallel, only-partially-overlapping matters at once — not just one
תקנה 9 causal argument. The register/CLI need an explicit notion of which
track a claim belongs to (`--track` filtering in `list-claims` and
`summarize-claim`, a `track` grouping in the evidence matrix), and a way
for one piece of evidence to serve more than one track's claims without
duplicating it. Concrete requirements exist now (see the author's own
local case), not hypothetical — this is worth doing before Phase 2, not
after, since one person with several tracks is already today's real use
case, not a future one.

## Deferred (planned, not started, in this order)

1. Real LLM provider (env-driven, explicit `--provider` flag, consent
   gate before first real call, timeout/retry, `--save-raw-response`) —
   waiting on real-model output from the manual bridge first, to learn
   whether the current prompt/schema contract actually holds up before
   building automation around it.
2. `narrative_timeline_summary` — an LLM layer over `timeline.py`,
   deliberately after the provider exists and after claim-level summaries
   have a track record, for the same causal-storytelling-risk reason
   `timeline.py` itself stayed separate from the matrix.
3. `committee_brief` generator — assembles validated claim summaries into
   an actual packet, only once the pieces under it are trusted.
4. Phase 2 packaging decision (Claude-Code-per-person vs. a real
   installable app) — revisit once Phase 1 is genuinely done, not before.

## Explicitly not doing

- Choosing a winning LLM provider inside the product. `completion_fn` /
  the `ClaimSummaryLLM` Protocol stay provider-agnostic; which model the
  author personally develops with (see their own workflow notes) is a
  separate decision from what a shipped tool uses for real medical data.
- Any shared backend or cross-user data store. Every person's evidence
  stays local to them, always.
