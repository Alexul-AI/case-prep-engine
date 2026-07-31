from __future__ import annotations

import re
from dataclasses import dataclass

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


def _format_payload_block(label: str, payloads: tuple[EvidencePayload, ...]) -> str:
    if not payloads:
        return f"{label}: (none)"
    lines = [f"{label}:"]
    for p in payloads:
        lines.append(f'  - hash={p.payload_hash} source_ref={p.source_ref}')
        lines.append(f'    text: "{p.hebrew_verbatim}"')
    return "\n".join(lines)


def build_claim_summary_prompt(request: ClaimSummaryRequest) -> str:
    """Deterministic prompt text for claim_id, built only from request.

    No LLM call happens here or anywhere in this module yet -- this
    function's own output is what gets tested. The instructions below are
    the model-facing mirror of validate_claim_summary()'s rules: stating
    the constraint in the prompt doesn't make the model obey it, which is
    exactly why validate_claim_summary() exists as a separate, mandatory
    check on the output, not a replacement for one.
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
        "Rules:",
        "- Only reference the evidence listed above. Nothing else exists for this claim.",
        "- Every citation must be one of the hash values listed above, exactly.",
        "- Do not quote text that does not appear verbatim in the evidence above.",
        "- Do not use causal language (e.g. \"caused\", \"led to\", \"as a result of\") "
        "unless the cited evidence itself uses causal language -- do not "
        "strengthen a causal claim beyond what the source actually says.",
        "- If a contradiction is listed above, it must be reflected in the "
        "status or in open_risks, never silently dropped.",
        "- If negative findings are listed above, cite them -- they are not "
        "a weaker version of missing evidence, they are a real finding.",
        "- If an unresolved conflict is listed above, the status must not be 'supported'.",
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


def validate_claim_summary(summary: ClaimSummary, entry: ClaimMatrixEntry) -> list[str]:
    """Return consistency problems between a claim summary and its source entry.

    This is the actual safety boundary of this module -- build_claim_summary_prompt()
    only *asks* a model to behave; this is what refuses to accept the answer
    if it didn't. Every rule here is mechanically checkable against
    ClaimMatrixEntry, deliberately not requiring any judgment call this
    module isn't equipped to make (see the docstring notes on the causal-
    wording rule and the negative-findings rule for the specific
    interpretations chosen where the original spec left more than one
    reasonable reading).
    """
    problems: list[str] = []

    if summary.claim_id != entry.claim_id:
        problems.append(
            f"summary.claim_id {summary.claim_id!r} does not match "
            f"entry.claim_id {entry.claim_id!r}"
        )

    if summary.status not in CLAIM_SUMMARY_STATUSES:
        problems.append(f"unknown status: {summary.status!r}")

    if entry.has_unresolved_conflict and summary.status == "supported":
        problems.append(
            "entry has an unresolved conflict; status cannot be 'supported' "
            "(it may be 'supported_with_risks', 'blocked', etc.)"
        )

    if entry.has_contradiction and summary.status != "contradicted" and not summary.open_risks:
        problems.append(
            "entry has a contradiction; it must be reflected in "
            "status='contradicted' or listed in open_risks, never dropped"
        )

    # "checked_not_supported doesn't disappear": interpreted literally as
    # "must be cited", not just mentioned in status/open_risks -- a
    # stricter reading than the contradiction rule above, chosen because a
    # negative finding is easy to omit silently (nothing else forces the
    # summary to even acknowledge it exists) unlike a contradiction, which
    # at least has a dedicated status value pulling attention to it.
    if entry.has_negative_finding:
        negative_hashes = {r.row.payload.payload_hash for r in entry.negative_findings}
        if not negative_hashes & set(summary.citations):
            problems.append(
                "entry has a negative finding (checked_not_supported); at "
                "least one of its payload_hash values must appear in "
                "citations, not be silently dropped"
            )

    # Citations may only point at evidence that was actually checked --
    # supporting/negative_findings/contradictions. Deliberately excludes
    # `unresolved` (blocked/needs_ocr rows have no real payload text to
    # cite) and `conflicts` (an unresolved conflict is exactly the kind of
    # row that must never be cited as if it were settled).
    citable_payloads = [
        *(r.row.payload for r in entry.supporting),
        *(r.row.payload for r in entry.negative_findings),
        *(r.row.payload for r in entry.contradictions),
    ]
    valid_hashes = {p.payload_hash for p in citable_payloads}
    for citation in summary.citations:
        if citation not in valid_hashes:
            problems.append(
                f"citation {citation!r} is not a real payload_hash from this "
                "entry's checked evidence (supporting/negative_findings/contradictions)"
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
