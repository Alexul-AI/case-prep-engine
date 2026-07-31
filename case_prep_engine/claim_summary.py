from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .evidence_matrix import ClaimMatrixEntry
from .evidence_store import EvidencePayload

CLAIM_SUMMARY_STATUSES = frozenset(
    {"supported", "supported_with_risks", "contradicted", "not_supported", "blocked"}
)

# Real quotation in prose sits at a word boundary (whitespace or
# start/end-of-string on both sides of the quote mark). Deliberately does
# NOT match Hebrew gershayim used mid-word for abbreviations (ד"ר = "Dr.",
# כ"הס, etc.) -- those appear in essentially every real document title and
# payload in this project, and a naive quote-detector would false-positive
# on nearly every summary that names a doctor. This is a best-effort
# heuristic layered on top of the citations check (the actually-reliable
# mechanism) -- it can miss real violations, it is not a substitute for
# citations.
_QUOTE_SPAN_RE = re.compile(r'(?<!\S)"([^"]{6,})"(?!\S)')

# Non-exhaustive Hebrew causal-language markers. Deliberately biased toward
# over-matching (flagging non-causal uses of e.g. "בעקבות" as if causal) --
# for this specific check, a false positive just means a valid summary
# needs a small rewording; a false negative means unfounded causal wording
# reaches a committee packet. That asymmetry is the opposite of the
# quote-detection heuristic above, which is deliberately conservative.
_CAUSAL_KEYWORDS_HE = (
    "קשר סיבתי",
    "זיקה סיבתית",
    "קשר סיבתי-רפואי",
    "גרם ל",
    "גורם ל",
    "הביא ל",
    "הביאה ל",
    "כתוצאה מ",
    "בעקבות",
    "החמיר",
    "החמירה",
    "תרם ל",
    "תרמה ל",
    "השפיע על",
    "השפיעה על",
)


def _contains_causal_wording(text: str) -> bool:
    return any(keyword in text for keyword in _CAUSAL_KEYWORDS_HE)


def _extract_quoted_spans(text: str) -> list[str]:
    return _QUOTE_SPAN_RE.findall(text)


@dataclass(frozen=True)
class ClaimSummaryRequest:
    """Everything an LLM (or a human) needs to summarize one claim, and
    nothing else -- no raw ungated evidence, no other claims' evidence, no
    access to documents this entry didn't already resolve as relevant.
    """

    claim_id: str
    supporting: tuple[EvidencePayload, ...]
    negative_findings: tuple[EvidencePayload, ...]
    contradictions: tuple[EvidencePayload, ...]
    has_unresolved_conflict: bool
    has_unresolved_evidence: bool  # entry.unresolved non-empty: documents not yet checked at all


def build_claim_summary_request(entry: ClaimMatrixEntry) -> ClaimSummaryRequest:
    return ClaimSummaryRequest(
        claim_id=entry.claim_id,
        supporting=tuple(r.row.payload for r in entry.supporting),
        negative_findings=tuple(r.row.payload for r in entry.negative_findings),
        contradictions=tuple(r.row.payload for r in entry.contradictions),
        has_unresolved_conflict=entry.has_unresolved_conflict,
        has_unresolved_evidence=bool(entry.unresolved),
    )


def request_to_dict(request: ClaimSummaryRequest) -> dict:
    """Serialize a request to a plain, JSON-ready dict. Round-trips through
    request_from_dict().

    Exists so a request can be frozen to a file between
    export-claim-prompt and validate-summary (the manual bridge to a real
    model, used before any automated provider exists). Re-deriving the
    request from the register at validate time instead would silently
    validate a real model's response against a *different* state if the
    register changed in between export and validate -- the same class of
    staleness bug this project's own provenance model
    (docs/case_prep_status_model_v3_provenance.md) exists to catch, just
    at the request/response boundary instead of the evidence-row boundary.
    """
    return asdict(request)


def request_from_dict(data: dict) -> ClaimSummaryRequest:
    return ClaimSummaryRequest(
        claim_id=data["claim_id"],
        supporting=tuple(EvidencePayload(**p) for p in data["supporting"]),
        negative_findings=tuple(EvidencePayload(**p) for p in data["negative_findings"]),
        contradictions=tuple(EvidencePayload(**p) for p in data["contradictions"]),
        has_unresolved_conflict=data["has_unresolved_conflict"],
        has_unresolved_evidence=data["has_unresolved_evidence"],
    )


def _citable_payloads(request: ClaimSummaryRequest) -> tuple[EvidencePayload, ...]:
    """Every payload a response is allowed to cite -- the single definition
    of "citable" shared by extract_allowed_payload_hashes(), the prompt
    builder, and validate_claim_summary(), so they can never quietly drift
    apart. Deliberately excludes unchecked evidence (has_unresolved_evidence
    is a bool, not a payload list -- there is nothing behind it to cite)
    and unresolved conflicts (citing one as if it were settled is exactly
    the failure mode this module exists to prevent).
    """
    return (*request.supporting, *request.negative_findings, *request.contradictions)


def extract_allowed_payload_hashes(request: ClaimSummaryRequest) -> frozenset[str]:
    """The exact set of payload_hash values a response may legally cite."""
    return frozenset(p.payload_hash for p in _citable_payloads(request))


def render_payload_block(payload: EvidencePayload) -> str:
    """Render one payload as the fixed two-line block used in the prompt.

    Deliberately minimal: hash, source_ref, and hebrew_verbatim only --
    never translation_ru, source_location, or source_note (those exist on
    EvidencePayload for local bookkeeping, not for a model to see), and
    never a document title or any other field from the wider case file.
    This is the actual privacy boundary: a model summarizing one claim
    sees exactly what's rendered here and nothing else.

    Does not wrap hebrew_verbatim in ASCII quote marks: real payload text
    routinely contains its own embedded Hebrew gershayim mid-word (ד"ר,
    כ"הס -- see the real Gour payload), which would visually look like the
    wrapping quote closed early. The "text:" label plus indentation is
    enough to mark the boundary without introducing that ambiguity.
    """
    return f"  - hash={payload.payload_hash} source_ref={payload.source_ref}\n    text: {payload.hebrew_verbatim}"


def _format_payload_block(label: str, payloads: tuple[EvidencePayload, ...]) -> str:
    if not payloads:
        return f"{label}: (none)"
    lines = [f"{label}:"]
    lines.extend(render_payload_block(p) for p in payloads)
    return "\n".join(lines)


_JSON_SCHEMA_BLOCK = """\
Respond with exactly one JSON object matching this schema, and nothing else -- no prose before or after it:
{
  "claim_id": string, must equal the Claim id above exactly,
  "status": one of "supported" | "supported_with_risks" | "contradicted" | "not_supported" | "blocked",
  "summary_he": string, a Hebrew summary of this claim,
  "summary_ru": string, a Russian summary (may be empty string),
  "allowed_uses": array of strings (may be empty),
  "must_not_say": array of strings -- things this summary must not be used to claim,
  "citations": array of strings -- payload_hash values from the evidence above, and nothing else,
  "open_risks": array of strings (may be empty)
}

Status meaning:
- "supported": clean supporting evidence exists, with no contradiction, no negative finding, and no unresolved conflict.
- "supported_with_risks": supporting evidence exists, but so does a contradiction, a negative finding, or an unresolved conflict that must be disclosed.
- "contradicted": a contradiction is the evidence that matters most here.
- "not_supported": a negative finding (checked, not supported) exists and there is no real support.
- "blocked": nothing usable has been checked for this claim yet, or an unresolved conflict makes any conclusion unsafe."""


def build_claim_summary_prompt(request: ClaimSummaryRequest) -> str:
    """Deterministic prompt text for claim_id, built only from request.

    No LLM call happens here or anywhere in this module -- this function's
    own output is what gets tested (see the golden-prompt tests). The
    instructions below are the model-facing mirror of
    validate_claim_summary()'s rules: stating the constraint in the prompt
    doesn't make the model obey it, which is exactly why
    validate_claim_summary() exists as a separate, mandatory check on the
    output, not a replacement for one.
    """
    parts = [
        f"Claim: {request.claim_id}",
        "",
        _format_payload_block("Supporting evidence", request.supporting),
        "",
        _format_payload_block("Negative findings (checked, not supported)", request.negative_findings),
        "",
        _format_payload_block("Contradictions", request.contradictions),
        "",
        f"Unresolved conflict on this claim: {request.has_unresolved_conflict}",
        f"Unchecked/blocked documents also exist for this claim: {request.has_unresolved_evidence}",
        "",
        _JSON_SCHEMA_BLOCK,
        "",
        "Hard rules:",
        "- Only reference the evidence listed above. Nothing else exists for this claim.",
        "- citations must be payload_hash values from the lists above, exactly -- "
        "never a source_ref, never a document title, never anything not listed above.",
        "- Do not quote text that does not appear verbatim in the evidence above.",
        "- Do not use causal language (e.g. \"caused\", \"led to\", \"as a result of\") "
        "unless the cited evidence itself uses causal language -- do not "
        "strengthen a causal claim beyond what the source actually says.",
        "- If a contradiction is listed above, it must be reflected in "
        "status='contradicted' or in open_risks, never silently dropped.",
        "- If negative findings are listed above, cite them -- they are not "
        "a weaker version of missing evidence, they are a real finding.",
        "- If an unresolved conflict is listed above, status must not be 'supported'.",
        "- If a contradiction, unresolved conflict, or negative finding "
        "applies, must_not_say cannot be empty.",
    ]
    return "\n".join(parts)


@dataclass(frozen=True)
class ClaimSummary:
    claim_id: str
    status: str  # supported | supported_with_risks | contradicted | not_supported | blocked
    summary_he: str
    summary_ru: str
    allowed_uses: tuple[str, ...]
    must_not_say: tuple[str, ...]
    citations: tuple[str, ...]  # payload_hash values only
    open_risks: tuple[str, ...]


def validate_claim_summary(summary: ClaimSummary, request: ClaimSummaryRequest) -> list[str]:
    """Return consistency problems between a claim summary and its request.

    Validates against ClaimSummaryRequest, not ClaimMatrixEntry -- the
    request is exactly what the model (or a human) was actually given, and
    validation must check the answer against the same thing the question
    was built from, not a broader object (ClaimMatrixEntry also carries
    `unresolved`, which never reaches the model at all). This also matches
    ClaimSummaryLLM.summarize_claim(self, request)'s own signature: an
    implementation needs to be able to call this using only what it has.

    This is the actual safety boundary of this module --
    build_claim_summary_prompt() only *asks* a model to behave; this is
    what refuses to accept the answer if it didn't. Every rule here is
    mechanically checkable against ClaimSummaryRequest, deliberately not
    requiring any judgment call this module isn't equipped to make (see
    the docstring notes on the causal-wording rule and the
    negative-findings rule for the specific interpretations chosen where
    the original spec left more than one reasonable reading).
    """
    problems: list[str] = []

    if summary.claim_id != request.claim_id:
        problems.append(
            f"summary.claim_id {summary.claim_id!r} does not match "
            f"request.claim_id {request.claim_id!r}"
        )

    if summary.status not in CLAIM_SUMMARY_STATUSES:
        problems.append(f"unknown status: {summary.status!r}")

    if request.has_unresolved_conflict and summary.status == "supported":
        problems.append(
            "request has an unresolved conflict; status cannot be "
            "'supported' (it may be 'supported_with_risks', 'blocked', etc.)"
        )

    has_contradiction = bool(request.contradictions)
    if has_contradiction and summary.status != "contradicted" and not summary.open_risks:
        problems.append(
            "request has a contradiction; it must be reflected in "
            "status='contradicted' or listed in open_risks, never dropped"
        )

    # "checked_not_supported doesn't disappear": interpreted literally as
    # "must be cited", not just mentioned in status/open_risks -- a
    # stricter reading than the contradiction rule above, chosen because a
    # negative finding is easy to omit silently (nothing else forces the
    # summary to even acknowledge it exists) unlike a contradiction, which
    # at least has a dedicated status value pulling attention to it.
    has_negative_finding = bool(request.negative_findings)
    if has_negative_finding:
        negative_hashes = {p.payload_hash for p in request.negative_findings}
        if not negative_hashes & set(summary.citations):
            problems.append(
                "request has a negative finding (checked_not_supported); at "
                "least one of its payload_hash values must appear in "
                "citations, not be silently dropped"
            )

    # New rule: whenever there's something risky about this claim (a
    # contradiction, an unresolved conflict, or a negative finding),
    # must_not_say cannot be empty -- the model has to say what it should
    # not claim, not just produce a clean-looking summary that omits the
    # risk by silence.
    if (
        (has_contradiction or request.has_unresolved_conflict or has_negative_finding)
        and not summary.must_not_say
    ):
        problems.append(
            "request has a contradiction, unresolved conflict, or negative "
            "finding; must_not_say cannot be empty"
        )

    # Citations may only point at evidence that was actually checked. See
    # _citable_payloads()'s docstring for what's deliberately excluded.
    citable_payloads = _citable_payloads(request)
    valid_hashes = extract_allowed_payload_hashes(request)
    for citation in summary.citations:
        if citation not in valid_hashes:
            problems.append(
                f"citation {citation!r} is not a real payload_hash from this "
                "request's checked evidence (supporting/negative_findings/contradictions)"
            )

    for quoted in _extract_quoted_spans(summary.summary_he):
        if not any(quoted in p.hebrew_verbatim for p in citable_payloads):
            problems.append(
                f"summary_he contains a quoted span not found verbatim in "
                f"any payload: {quoted!r}"
            )

    # "causal wording only if the claim is about causation": this module
    # has no signal anywhere for which claims "are about" causation, so
    # applying that qualifier would require inventing an is_causal concept
    # that doesn't exist in the data model. Generalized instead to apply
    # unconditionally: causal language in the summary must be backed by
    # causal language in a *cited* payload, for every claim, not only
    # causal-sounding ones. Strictly covers the stated case and is simpler.
    if _contains_causal_wording(summary.summary_he):
        cited_causal_support = any(
            p.payload_hash in summary.citations and _contains_causal_wording(p.hebrew_verbatim)
            for p in citable_payloads
        )
        if not cited_causal_support:
            problems.append(
                "summary_he uses causal wording, but no cited payload's own "
                "hebrew_verbatim contains causal wording -- causal language "
                "must come from the cited evidence, not be introduced by the summary"
            )

    return problems
