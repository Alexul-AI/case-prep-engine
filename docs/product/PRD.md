# Case Prep Engine — PRD

Added 2026-08-01, split out of what was previously one combined
roadmap/PRD file. This is "what the product should do and for whom" —
see `docs/product/ROADMAP.md` for "where things stand and what's next".

## Target users

**Primary (now):** one person preparing their own נכי צה"ל case —
recognition, appeal, or a תקנה 9 secondary-disability claim — running the
tool against their own documents.

**Secondary (planned, not started):** that person's friends and family who
want to try the same tool on their own, unrelated cases. Each such person
is a separate `case_id`, running their own local instance against their
own documents — never a shared account or a shared data store.

Not a target user: anyone looking for legal or medical advice from the
tool itself. It organizes and gates evidence; a lawyer, physician, or
official body remains the actual authority.

## Core problem

A ועדה רפואית / תקנה 9 case is decided on documents — but a real case file
accumulates dozens of PDFs, protocols, and expert opinions over years,
and the two hardest parts aren't reading them once, they're:

1. **Knowing what's actually verified vs. still assumed.** A document
   being *readable* is not the same as a claim being *supported* by it,
   and a claim being supported by one document doesn't mean a later,
   possibly-contradicting document has been reconciled with it.
2. **Not letting confidence outrun evidence when producing something
   written** — a committee brief, a doctor letter, a summary — especially
   once an LLM is involved, which will produce fluent, confident-sounding
   text regardless of whether the underlying evidence actually supports it.

## Non-goals

- **Not legal or medical advice.** Every generated claim is gated behind
  explicit, citable evidence; the tool refuses to state anything it can't
  back with a citation, and states clearly when nothing has been checked
  yet.
- **Not a general document management or OCR product.** Hebrew-reversal
  detection and OCR-gap tracking exist because they were blocking this
  specific case-prep problem, not as standalone features.
- **Not a hosted service.** No shared backend, no cross-user data store,
  ever (see "Privacy model" below).
- **Not tied to one LLM provider.** See "Manual LLM bridge" below for why,
  and the author's own separate multi-tool workflow notes for how they
  personally develop the product — a different decision from what the
  shipped product itself depends on.

## Privacy model

- Every person's real case documents (Hebrew verbatim quotes, source
  references, verification notes) live only in their own local, gitignored
  `data/` folder — never in git history, never in a shared store, never
  uploaded anywhere by this codebase on its own initiative.
- Code, tests, and documentation are generic and public: synthetic Hebrew
  fixtures (same domain lexicon as real cases, not verbatim excerpts), a
  synthetic demo register (`examples/demo_register.csv`), no real names or
  dates.
- The manual LLM bridge (below) puts a human in the loop for every
  real-model call by design — nothing is sent to any LLM automatically.

## Evidence model

Three questions, kept structurally separate so a fast "yes" to one can
never be mistaken for a "yes" to another (`docs/case_prep_status_model_v2.md`,
`v3_provenance.md`):

1. **Can we read the document?** (`text_quality_status`)
2. **Does it support this specific claim?** (`claim_support_status`)
3. **May a claim be generated as fact from it?** (`output_gate`)

A claim lives inside a **case** (whose case: `case_id`) and a **track**
(which front of that case: `track_id`, e.g. `takana9_ptsd_ms` vs.
`ptsd_worsening`) — `claim_id` is only unique within one (case, track)
pair, on purpose, so the same claim_id used by two people, or by two
different fronts of the same person's case, never collides.

Evidence content (`EvidencePayload`: the Hebrew text, its source, how/when
it was verified) is independent of any particular claim — the same source
can support one claim, contradict another, and be irrelevant to a third,
without being copied three times. The link between one payload and one
claim (`EvidenceRow`) is where "supports/contradicts/not yet checked"
actually lives.

`resolve_current_state()` never silently trusts the newest-looking record
as "current" without real provenance (a timestamp, an actor, a method) —
if two records disagree and neither has better provenance than the other,
that's surfaced as an unresolved conflict, not guessed away. This is the
single most load-bearing design decision in the whole engine, driven by a
real incident early in the project (case_prep_status_model_v3_provenance.md's
"C04 lesson"): a register that mutates in place can't tell a later reader
whether "current" reflects reality or just the last write.

## User workflows

1. **Build/maintain a register** — a CSV of documents read, what they say,
   and what's been verified about them (currently hand-maintained; no UI
   for this yet).
2. **`summarize-claim --list-claims`** — see every claim across every
   case/track without needing to already know an id, or narrow it to one
   case/track with `--case-id`/`--track-id` once a register has more than
   one (e.g. checking only `ptsd_worsening`'s claims without `takana9_ptsd_ms`'s
   mixed in).
3. **`summarize-claim --claim-id ... --fake`** — get a structured,
   validated summary of one claim's evidence picture. `--fake` today
   (see "Manual LLM bridge"); a real model later, same command shape.
4. **`export-claim-prompt` + paste into any LLM chat + `validate-summary`**
   — get a real model's actual output validated against the same hard
   rules `--fake` already respects, without any API integration.

## CLI MVP

`python -m case_prep_engine summarize-claim` is the whole surface today:
`--claim-id`, `--case-id`/`--track-id` (for a single-claim lookup, default
to a single-case, single-track register so existing usage keeps working;
for `--list-claims`, an opt-in filter — omitted shows every case/track,
given narrows the listing to just that one), `--fake`
(required until a real provider exists), `--list-claims`, `--strict`
(opt-in non-zero exit for an unresolved/blocked result, for CI/batch use
— default stays exit-0-for-any-validated-result, since a correctly
reported "blocked" is the tool working, not failing), `--show-prompt`,
`--output`. `export-claim-prompt` / `validate-summary` are the same
pipeline with the completion step done by hand instead of `--fake`.

## Manual LLM bridge

Deliberately built *before* any real provider integration
(`docs/product/ROADMAP.md`, "Deferred"): `export-claim-prompt` writes the
exact prompt and a frozen copy of the request; a person pastes the prompt
into whichever LLM chat they already have (Claude, ChatGPT, Gemini, ...)
and saves the reply to a file; `validate-summary` checks that reply
against the *frozen* request (not a fresh one re-derived from a register
that may have changed since) through the exact same validator a real
provider integration will eventually use. This answers "does the current
prompt/schema contract actually hold up against a real model" without
writing a single line of network code, and keeps the product itself
provider-agnostic for as long as that question is still open.

## Future local app

Phase 2 (friends/family trying the tool) is decided at the level of "each
person runs their own local instance, no shared backend" — *how* that
instance runs (a Claude Code session per person vs. a real packaged,
installable application) is explicitly not decided yet
(`docs/product/ROADMAP.md`, "Deferred"). A packaged app would need its own
Google Drive OAuth per user and a UI a non-technical person can actually
use; a Claude-Code-per-person model needs neither, at the cost of everyone
needing Claude Code set up themselves. Revisit once Phase 1 is solid
enough to hand to someone else at all.

## Multi-case/multi-track requirements

Not hypothetical — the author's own single case already has four
genuinely parallel tracks in flight at once (a תקנה 9 causal claim, a
PTSD-percentage-increase claim, a chronic-pain track, and an MS-pain
track), which is exactly the scenario `case_id`/`track_id` scoping was
built for ahead of Phase 2, not just in anticipation of it. Requirements
that follow from that, already satisfied by the current schema:

- The same `claim_id` (e.g. `C01`) must never collide across two
  different cases or two different tracks of the same case.
- The same piece of evidence must be able to back claims in more than one
  track without being duplicated or re-verified twice.
- A register with no case/track information at all (every register before
  2026-08-01) must keep working exactly as before, defaulted into one
  case and one track — never break, never silently drop scope.

Not yet built, and deliberately not automated (a substance decision about
a real case, not an engineering one — see `docs/product/ROADMAP.md`,
"Next up"): re-linking existing evidence to additional tracks it plausibly
also supports.
