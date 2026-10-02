# Case Prep Engine — Roadmap

Added 2026-08-01, split from PRD 2026-08-01 (see `docs/product/PRD.md` for
what the product is/for whom/scope — this file is only "where things
stand and what's next"). Mirrors the `docs/product/ROADMAP.md` convention
used in the author's other repos — read this before proposing new work.

## Architecture built so far (2026-07-30 → 2026-10-02, 170 tests)

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
12. **`claim_link_caveat` — prompt-visible evidence-link risk metadata**
    (2026-08-01) — direct response to a real manual-LLM-bridge finding: the
    first Track B evidence-linking pass (item 11's follow-up, done locally
    against `data/`) added a "candidate link" caveat as a plain
    `EvidencePayload.source_note` — but `source_note` is deliberately
    private and never reaches a prompt (see `render_payload_block`'s
    docstring), so four different real models (tested via the manual
    bridge) all summarized that candidate-link claim exactly as confidently
    as a fully-reviewed one, correctly reading its evidence but with no way
    to know the *link* itself was weaker than usual. New, separate field:
    `EvidenceRow.claim_link_caveat` — unlike `source_note` (operational,
    about the document), this is *about the payload-to-claim link* and is
    deliberately rendered into the prompt (a `caveat:` line under the
    relevant supporting-evidence block). `claim_summary.py` gained
    `EvidenceItem` (payload + `claim_link_caveat`) as what
    `ClaimSummaryRequest.supporting`/`negative_findings`/`contradictions`
    now actually carry, instead of bare `EvidencePayload` tuples.
    `validate_claim_summary()` gained two rules mirroring the existing
    contradiction/conflict rules: a caveat on any supporting evidence
    forbids `status='supported'` (use `supported_with_risks`, or something
    stronger if another rule also applies), and the caveat must be
    reflected in `must_not_say` or `open_risks`, not silently dropped.
    Behavior-preserving for every row with no caveat (the overwhelming
    majority) — verified against the real register and
    `examples/demo_register.csv`, both unaffected; the real B01/B03 rows
    don't have `claim_link_caveat` set yet (still on `source_note`),
    a deliberate follow-up decision left to the author, not done
    automatically by this PR. All 6 existing golden prompt fixtures
    regenerated (shared "Status meaning"/hard-rules text changed for every
    prompt, not only caveat-carrying ones) plus one new fixture
    (`claim_link_caveat.txt`) for the new scenario.
13. **First independent audit round (2026-10-02)** — a second engineer
    (Codex) audited a real, private staging register against
    its source PDFs and the code; each finding was re-verified before
    acting (some were confirmed, one was wrong, and the audit itself missed
    one real defect — see below). Code fixed in this round: (a)
    `source_location` was a field on `EvidencePayload` but not a CSV column,
    so *every* CSV-imported row silently lost its page/place — now imported
    (never rendered into a prompt, not part of `payload_hash`/`evidence_id`);
    (b) `parse_claim_summary_json` coerced with `tuple()`/`str()`, so a
    string where an array belonged (`"open_risks": "not-an-array"`) became a
    tuple of characters and satisfied the "risk must be disclosed" checks —
    now strict JSON types, rejecting wrong shapes. Process lessons worth
    keeping: **a "verbatim" quote is only as good as its extraction path** —
    scanned PDFs carry OCR text with its own errors, and Drive's text API
    returned some born-digital PDFs in visual (reversed) order, which a
    manual reassembly then had to guess at; extracting the local PDF's text
    layer directly (PyMuPDF) gives logical order and allows a mechanical,
    whitespace-insensitive "is this quote really in the source" check
    (justified-layout artifacts such as a displaced final letter still need
    a visual check). And an auditor's table is not itself verified: two
    quotes were corrected from it, a third (non-verbatim word order) it
    missed, and one proposed "correction" would have *introduced* an error
    into a quote that matched the scan.

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

1. **Structured risk coverage for `validate_claim_summary()`** (identified
   2026-08-01, during the `claim_link_caveat` PR's own review) — every
   "a risk must be disclosed" rule in the validator (unresolved conflict,
   contradiction, negative finding, and now `claim_link_caveat`) currently
   checks only that `must_not_say`/`open_risks` is *non-empty*, not that
   its content is actually about *that* risk. Confirmed empirically (not
   just reasoned): a `must_not_say` written for one risk factor silently
   satisfies the emptiness check for an unrelated one in the same request.
   Not a regression from any single PR — every risk-disclosure rule has
   shared this limitation since `ContradictionConflictAndMustNotSayTests`
   first shipped; the caveat rule is consistent with it, not worse.
   Deliberately not fixed with an LLM-based semantic judge (out of scope
   for a mechanically-checkable validator) — the fix is structural:
   represent each required disclosure as an explicit token (e.g.
   `unresolved_conflict`, `contradiction_present`, `negative_finding_present`,
   `` `claim_link_caveat:<payload_hash>` ``), add `required_disclosures` to
   `ClaimSummaryRequest`/the prompt, and require the response to name which
   ones it covered (`covered_risks`) rather than trusting freeform-field
   non-emptiness. Sequenced **before** item 2 (real provider) on purpose —
   a provider integration is exactly where a model could start writing
   generic, risk-non-specific boilerplate that passes today's non-emptiness
   checks without actually engaging with the specific risk.
1b. **Known gaps from the 2026-10-02 audit, confirmed by minimal
   reproduction, deliberately not fixed in that round** (each is a
   separate, reviewable change; (a)-(c) belong before item 2):
   (a) `staleness_status` is stored and documented
   (`fresh`/`stale`/`conflict_detected`/`superseded`) but nothing reads it —
   `validate_row`, the matrix, and the prompt all ignore `stale`, so a
   stale supporting row still reads as clean support. Needs a real gate
   plus a settled meaning for each value (and the register's own free-text
   values, e.g. `stale_by_default_no_check_yet`, normalized).
   (b) `claim_link_caveat` is only enforced on *supporting* evidence; a
   caveat on a contradiction/negative finding is rendered but need not be
   disclosed. Fold into item 1's required-disclosure tokens.
   (c) The model never sees a claim's *statement* — the prompt carries only
   `claim_id` plus evidence, so a narrowly-scoped claim definition kept in
   notes is invisible to it. Today's workaround is a `claim_link_caveat`
   per row; a `claim_text` on the request is the structural fix.
   (d) A caveat-only edit to an existing row has no timestamp of its own:
   `resolve_current_state` orders by `payload.verified_utc`, so two rows for
   one quote that differ only in caveat and share a verification date
   resolve as a *conflict*. Fine while registers are edited in place (the
   current practice); matters once the append-only store is the real
   history.
   (e) Frozen request JSON written before `5c19b5b` is rejected with a bare
   `KeyError('payload')`. Frozen requests are per-session artifacts
   (regenerate with `export-claim-prompt`), so no migration is planned —
   at most a clearer error message.
2. Real LLM provider (env-driven, explicit `--provider` flag, consent
   gate before first real call, timeout/retry, `--save-raw-response`) —
   waiting on real-model output from the manual bridge first, to learn
   whether the current prompt/schema contract actually holds up before
   building automation around it.
3. `narrative_timeline_summary` — an LLM layer over `timeline.py`,
   deliberately after the provider exists and after claim-level summaries
   have a track record, for the same causal-storytelling-risk reason
   `timeline.py` itself stayed separate from the matrix.
4. `committee_brief` generator — assembles validated claim summaries into
   an actual packet, only once the pieces under it are trusted.
5. Phase 2 packaging decision (Claude-Code-per-person vs. a real
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
