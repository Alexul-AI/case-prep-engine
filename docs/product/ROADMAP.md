# Case Prep Engine — Roadmap

Added 2026-08-01, split from PRD 2026-08-01 (see `docs/product/PRD.md` for
what the product is/for whom/scope — this file is only "where things
stand and what's next"). Mirrors the `docs/product/ROADMAP.md` convention
used in the author's other repos — read this before proposing new work.

## Architecture built so far (2026-07-30 → 2026-08-01, 149 tests)

Each stage below was built directly on the previous one, in order, and
each one deliberately stayed *simpler* than the temptation to skip ahead
— mock/deterministic before real, manual before automated:

1. **`hebrew_text_quality.py`** — detects and fixes line-reversed Hebrew
   text extraction (a real, recurring OCR/Drive-extraction failure mode).
2. **`evidence_store.py`** — the typed, append-only evidence log.
   Atomic at (case_id, track_id, source_ref, claim_id); `EvidencePayload`
   is pure content (what a source says, independent of any claim) and
   `EvidenceRow` is the case/track-scoped link saying what that content is
   being used to support — the same payload can back a claim in one track
   and be irrelevant to another without being duplicated.
   `resolve_current_state()` picks the current belief per claim from
   history and **flags an unresolved conflict instead of guessing** when
   provenance doesn't clearly support one answer over another;
   `looks_like_stable_identifier()` stops free-text notes from being used
   as document identity (a real bug this caught: two different documents
   silently merged into one because both had the same placeholder-ish
   `source_ref`).
3. **`evidence_matrix.py`** — groups resolved evidence by (case, track,
   claim) into supporting / contradicting / negative-finding / unresolved
   / conflict buckets. Purely mechanical, no narrative, no verdict —
   deliberately stops short of writing anything that reads like a
   conclusion.
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
8. **`cli.py`** — `summarize-claim` (`--fake`, `--case-id`/`--track-id`,
   `--list-claims`, `--strict`, `--show-prompt`, `--output`), plus a
   manual bridge to any real model a person already has access to:
   `export-claim-prompt` (freezes the exact request to a file) and
   `validate-summary` (checks a hand-pasted reply against that frozen
   request, not a live re-derivation that could have drifted).
9. **`examples/demo_register.csv`** — synthetic, safe-to-publish register
   backing the README quickstart, so anyone can try the tool with zero
   setup and zero personal data.
10. **Case/track scoping** (2026-08-01) — `case_id`/`track_id` added
    ahead of `claim_id` throughout the whole pipeline, not only in the
    register: the same claim_id in two different cases (Phase 2: two
    different people) or two different tracks of the same case (today's
    real need, not hypothetical — see PRD) never collides. Backward
    compatible: a register with no case_id/track_id column defaults to
    `personal`/`takana9_ptsd_ms` (`DEFAULT_CASE_ID`/`DEFAULT_TRACK_ID` in
    `evidence_store.py`), never to an empty scope. Same PR moved
    `claim_id`/`payload_type` off `EvidencePayload` onto `EvidenceRow` and
    dropped `claim_id` from `payload_hash`'s inputs — content identity
    (what a source says) and claim identity (what it's being used to
    support) are different things, so the same Greenhouse-style quote
    backing two different tracks' claims is one piece of evidence, not
    two hashed differently.
11. **`evidence_id` hardening** (2026-08-01) — fixes a claim-collapse bug
    one layer deeper than item 10 already fixed: `resolve_current_state()`
    was grouping by `(case_id, track_id, document, claim_id)`, so two
    genuinely *different* quotes from the same document, backing the same
    claim (e.g. two separate excerpts from Dr. Gour's opinion both cited
    for C08), silently collapsed into one — the newer-timestamped quote
    replacing the older one with no conflict raised, discarding real
    evidence. Confirmed empirically before fixing (two synthetic rows,
    same case/track/claim/source_ref, different `hebrew_verbatim`, and
    `resolve_current_state()` returned 1 entry instead of 2). Fix: a new
    `evidence_id` field on `EvidenceRow` becomes the true grouping key
    (`EvidenceRow.key()` is now `(case_id, track_id, evidence_id)`, not the
    old 4-tuple). `compute_default_evidence_id()` derives it from
    `case_id + track_id + document_identity + claim_id + payload_hash`, so
    an old-style CSV with no `evidence_id` column still gets distinct ids
    for distinct quotes automatically, while a genuine re-verification of
    the *same* quote (same `payload_hash`) still gets the *same*
    `evidence_id` and correctly competes under the existing
    newest-timestamp/conflict logic — re-verification and new-evidence stay
    distinguishable, on purpose. `evidence_matrix.py`/`timeline.py` updated
    to read `(case_id, track_id, claim_id)` off each resolved row directly
    rather than unpacking `resolve_current_state()`'s key (which no longer
    carries a claim_id component at all). Also added, same PR: a visible
    (non-error) CLI note — `register_has_explicit_case_track_columns()` +
    `cli.py`'s `_warn_if_scope_defaulted()` — printed to stderr whenever a
    register has no `case_id`/`track_id` columns at all, so silently
    defaulting every row to `personal`/`takana9_ptsd_ms` is visible instead
    of invisible. New regression tests lock in both the fix (two distinct
    quotes never collapse) and the pre-existing behavior it must not break
    (a same-quote re-verification still competes as one group).

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
- **Content identity and claim identity are different things.** A source's
  own hash/content lives on `EvidencePayload`; which claim (within which
  case/track) it's being used to support is a property of the *link*
  (`EvidenceRow`), not the content itself.

## Next up

With item 11's `evidence_id` hardening landed, the structural edge case
that was blocking this is closed — re-classification can proceed:

1. **Re-classify which existing evidence also applies to the newer
   tracks** (e.g. the Greenhouse psychiatric opinion likely bears on both
   the `takana9_ptsd_ms` causal claim it already backs and a
   `ptsd_worsening` claim it hasn't been linked to yet). A **substance
   decision about the case**, not an engineering one — deliberately left
   to the author to do by hand (add a new register row with the existing
   source_ref, a new claim_id, and the new track_id), not silently
   inferred during any migration.
2. **Manual LLM bridge testing on the new case_id/track_id scoping** —
   run `export-claim-prompt`/`validate-summary` against claims that
   actually use a non-default case/track, once step 1 has produced some.

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
- Inventing a `Claim`/`EvidenceLink` entity separate from `EvidenceRow`.
  Considered and rejected in favor of moving `claim_id`/`payload_type`
  onto `EvidenceRow` directly (see "Architecture built so far", item 10)
  — `EvidenceRow` already *is* the case/track/claim-scoped link once those
  fields live there; a rename/new class would have touched the same files
  for no behavior change.
