from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .evidence_store import EvidenceRow, ResolvedEvidence, resolve_current_state

# output_gate -> which ClaimMatrixEntry bucket it belongs in, for entries
# resolve_current_state resolved without a conflict. A conflicted entry
# never lands in one of these buckets regardless of what its (arbitrarily
# chosen) row's own gate looks like -- see build_evidence_matrix.
_SUPPORTING_GATES = frozenset({"allowed_as_quote", "allowed_as_synthesis"})
_NEGATIVE_FINDING_GATES = frozenset({"allowed_as_negative_finding"})
_CONTRADICTION_GATES = frozenset({"allowed_as_contradiction"})


@dataclass(frozen=True)
class ClaimMatrixEntry:
    """The mechanical, generation-safe evidence picture for one claim.

    Deliberately structure, not prose: bucketing resolved evidence into
    supporting / negative_findings / contradictions / unresolved /
    conflicts is a pure function of each row's own gate and status -- no
    judgment about what the claim "means" or whether it helps or hurts the
    case. Writing an actual claim summary from this structure is a
    separate, later (LLM) layer that reads ClaimMatrixEntry -- it must
    never see raw ungated evidence, and it must not be able to quietly
    promote a weak claim past what its own gates already allow.
    """

    claim_id: str
    supporting: tuple[ResolvedEvidence, ...]
    negative_findings: tuple[ResolvedEvidence, ...]
    contradictions: tuple[ResolvedEvidence, ...]
    unresolved: tuple[ResolvedEvidence, ...]
    conflicts: tuple[ResolvedEvidence, ...]

    @property
    def has_support(self) -> bool:
        return bool(self.supporting)

    @property
    def has_contradiction(self) -> bool:
        return bool(self.contradictions)

    @property
    def has_negative_finding(self) -> bool:
        return bool(self.negative_findings)

    @property
    def has_unresolved_conflict(self) -> bool:
        return bool(self.conflicts)


def build_evidence_matrix(rows: Iterable[EvidenceRow]) -> dict[str, ClaimMatrixEntry]:
    """Group resolved evidence by claim_id across all of its source documents.

    Reuses resolve_current_state() rather than re-deriving "current" per
    document -- a claim can be evidenced by more than one document (e.g. a
    real committee transcript and a formal protocol both bearing on the
    same claim), so this is a second grouping pass over
    resolve_current_state's own output, by the claim_id half of its
    (document, claim_id) key.

    A conflicted (document, claim_id) entry always lands in `conflicts`,
    never in supporting/negative_findings/contradictions -- even though
    ResolvedEvidence still carries *some* row for a conflicted group (see
    resolve_current_state's docstring), that row was not responsibly
    chosen as "current" and must not be silently counted as if it were.

    Does not call validate_row(): matrix-building and row-validity are
    separate concerns. A caller who only wants validated rows should
    filter with validate_row() before calling this, not rely on this
    function to do it implicitly.
    """
    resolved = resolve_current_state(rows)

    grouped: dict[str, list[ResolvedEvidence]] = {}
    for (_, claim_id), entry in resolved.items():
        grouped.setdefault(claim_id, []).append(entry)

    matrix: dict[str, ClaimMatrixEntry] = {}
    for claim_id, entries in grouped.items():
        supporting: list[ResolvedEvidence] = []
        negative_findings: list[ResolvedEvidence] = []
        contradictions: list[ResolvedEvidence] = []
        unresolved: list[ResolvedEvidence] = []
        conflicts: list[ResolvedEvidence] = []

        for entry in entries:
            if entry.conflict:
                conflicts.append(entry)
                continue
            gate = entry.row.output_gate
            if gate in _SUPPORTING_GATES:
                supporting.append(entry)
            elif gate in _NEGATIVE_FINDING_GATES:
                negative_findings.append(entry)
            elif gate in _CONTRADICTION_GATES:
                contradictions.append(entry)
            else:
                unresolved.append(entry)

        matrix[claim_id] = ClaimMatrixEntry(
            claim_id=claim_id,
            supporting=tuple(supporting),
            negative_findings=tuple(negative_findings),
            contradictions=tuple(contradictions),
            unresolved=tuple(unresolved),
            conflicts=tuple(conflicts),
        )

    return matrix
