from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


# Status vocabularies from docs/case_prep_status_model_v2.md and v3_provenance.md,
# extended with the "mixed" / "n/a" values already used by real register rows.
TEXT_QUALITY_STATUSES = frozenset(
    {
        "file_found",
        "needs_ocr",
        "text_extracted_unverified",
        "text_qa_failed",
        "text_qa_passed",
        "external_text_qa_passed_pending_import",
        "mixed",
        "n/a",
    }
)

CLAIM_SUPPORT_STATUSES = frozenset(
    {
        "metadata_only",
        "provisional",
        "not_checked",
        "supported_by_paraphrase",
        "supported_by_quote",
        "external_supported_pending_evidence_import",
        "checked_not_supported",
        "contradicted",
        "n/a",
    }
)

OUTPUT_GATE_STATUSES = frozenset(
    {
        "blocked",
        "blocked_pending_evidence_import",
        "allowed_as_unverified",
        "allowed_as_synthesis",
        "allowed_as_quote",
        "allowed_as_negative_finding",
        "allowed_as_contradiction",
    }
)

# Which output_gate values a given claim_support_status licenses. Mirrors the
# "Output Rules" section of case_prep_status_model_v2.md. A row whose
# output_gate isn't in this set for its claim_support_status is promoting a
# claim further than its own evidence justifies.
ALLOWED_GATES_FOR_CLAIM_SUPPORT: dict[str, frozenset[str]] = {
    "metadata_only": frozenset({"blocked", "allowed_as_unverified"}),
    "provisional": frozenset({"blocked", "allowed_as_unverified"}),
    "not_checked": frozenset({"blocked", "allowed_as_unverified"}),
    "external_supported_pending_evidence_import": frozenset(
        {"blocked_pending_evidence_import"}
    ),
    "supported_by_paraphrase": frozenset(
        {"allowed_as_synthesis", "blocked", "allowed_as_unverified"}
    ),
    "supported_by_quote": frozenset(
        {"allowed_as_quote", "blocked", "allowed_as_unverified"}
    ),
    "checked_not_supported": frozenset({"allowed_as_negative_finding"}),
    "contradicted": frozenset({"allowed_as_contradiction"}),
    "n/a": frozenset({"allowed_as_unverified", "blocked"}),
}

# output_gate values that assert a specific finding as fact. These require:
# (a) a non-empty Hebrew payload backing them up (the "canonical payload"
#     rule from provenance_model_v3_addendum_inline_payload.md), and
# (b) real provenance (see GATES_REQUIRING_PROVENANCE below), and
# (c) text_quality_status == text_qa_passed -- you cannot assert a quote out
#     of a document whose own text isn't verified as readable yet (the
#     "file found -> text QA -> claim check -> generation permission"
#     pipeline in case_prep_status_model_v2.md's Design Note).
GATES_REQUIRING_PAYLOAD = frozenset(
    {
        "allowed_as_quote",
        "allowed_as_synthesis",
        "allowed_as_negative_finding",
        "allowed_as_contradiction",
    }
)

# Currently the same membership as GATES_REQUIRING_PAYLOAD -- kept as a
# separate name because payload and provenance are conceptually different
# requirements that happen to apply to the same gates today.
GATES_REQUIRING_PROVENANCE = GATES_REQUIRING_PAYLOAD

# Free-text values seen in real registers that mean "not actually filled in",
# for fields other than verified_utc (which has its own sentinel set below,
# since it also needs to reject non-timestamp text like "after-v4-snapshot").
PLACEHOLDER_TEXT_VALUES = frozenset({"", "unknown", "n/a", "—", "-"})

UNVERIFIED_TIMESTAMP_SENTINELS = PLACEHOLDER_TEXT_VALUES | frozenset(
    {"after-v4-snapshot"}
)

_CLAIM_ID_SPLIT_RE = re.compile(r"[;/]")


def _is_placeholder(value: str) -> bool:
    return value.strip().lower() in PLACEHOLDER_TEXT_VALUES


def parse_verified_utc(value: str) -> datetime | None:
    """Parse a verified_utc field, returning None for non-timestamp sentinels.

    Naive timestamps (no timezone, e.g. date-only "2026-07-29") are
    normalized to UTC-aware rather than left naive. A verified_utc field is
    UTC by convention, and mixing naive and aware datetimes in the same
    comparison/sort raises TypeError -- real registers already mix
    date-only and full-timestamp values, so this normalization isn't
    optional.
    """
    text = value.strip()
    if text.lower() in UNVERIFIED_TIMESTAMP_SENTINELS:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _split_claim_ids(raw: str) -> list[str]:
    """Split a free-text related_claims cell into individual claim ids.

    Real registers use inconsistent separators ("C14; C15", "C05 / C06").
    Falls back to a single (possibly empty) id if there's nothing to split,
    so a document not yet linked to any claim doesn't get silently dropped.
    """
    parts = [p.strip() for p in _CLAIM_ID_SPLIT_RE.split(raw)]
    parts = [p for p in parts if p]
    return parts or [raw.strip()]


@dataclass(frozen=True)
class EvidenceRow:
    """One claim about one document's read-status, at a point in time.

    Atomic at (source_ref, claim_id): a document supporting two different
    claims (e.g. one expert opinion backing both C14 and C15) is two
    EvidenceRow records, not one row with both ids in it -- otherwise a
    later update to one claim silently overwrites the other in
    resolve_current_state() (see the "claim collapse" bug this fixed).
    Never mutated in place once written to a store -- see EvidenceStore.
    """

    document: str
    source_ref: str
    claim_id: str
    text_quality_status: str
    claim_support_status: str
    output_gate: str
    staleness_status: str
    verified_by_actor: str
    verification_method: str
    verified_utc: str
    evidence_payload_hebrew_verbatim: str
    track: str = ""
    priority_in_track: str = ""
    row_written_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def key(self) -> tuple[str, str]:
        """Stable identity for grouping rows about "the same claim".

        (document identity, claim_id). Document identity prefers source_ref
        (a Drive file id or similar) since a title can be re-transliterated
        or re-punctuated between register versions; falls back to the
        document title when source_ref is a placeholder like "unknown".
        """
        ref = self.source_ref.strip()
        identity = ref if ref and not _is_placeholder(ref) else self.document.strip()
        return (identity, self.claim_id.strip())


def validate_row(row: EvidenceRow) -> list[str]:
    """Return consistency problems with a row; empty list means clean.

    This only checks internal consistency of the row's own statuses against
    the rules in case_prep_status_model_v2.md/v3_provenance.md -- it does
    not and cannot judge whether the underlying claim is actually true.
    That check belongs to a human or a claim-checking step, not this module
    (same module boundary the README already applies to
    hebrew_text_quality.py).
    """
    problems: list[str] = []

    if row.text_quality_status not in TEXT_QUALITY_STATUSES:
        problems.append(f"unknown text_quality_status: {row.text_quality_status!r}")
    if row.claim_support_status not in CLAIM_SUPPORT_STATUSES:
        problems.append(f"unknown claim_support_status: {row.claim_support_status!r}")
    if row.output_gate not in OUTPUT_GATE_STATUSES:
        problems.append(f"unknown output_gate: {row.output_gate!r}")

    allowed_gates = ALLOWED_GATES_FOR_CLAIM_SUPPORT.get(row.claim_support_status)
    if allowed_gates is not None and row.output_gate not in allowed_gates:
        problems.append(
            f"output_gate {row.output_gate!r} is not licensed by "
            f"claim_support_status {row.claim_support_status!r} "
            f"(allowed: {sorted(allowed_gates)})"
        )

    if row.output_gate in GATES_REQUIRING_PAYLOAD:
        if not row.evidence_payload_hebrew_verbatim.strip():
            problems.append(
                f"output_gate {row.output_gate!r} requires a non-empty "
                "evidence_payload_hebrew_verbatim"
            )
        if row.text_quality_status != "text_qa_passed":
            problems.append(
                f"output_gate {row.output_gate!r} asserts a finding from the "
                f"document's text, but text_quality_status is "
                f"{row.text_quality_status!r}, not 'text_qa_passed'"
            )

    if row.output_gate in GATES_REQUIRING_PROVENANCE:
        if _is_placeholder(row.source_ref):
            problems.append(
                f"output_gate {row.output_gate!r} requires a real source_ref, "
                f"got {row.source_ref!r}"
            )
        if _is_placeholder(row.verification_method):
            problems.append(
                f"output_gate {row.output_gate!r} requires a non-placeholder "
                f"verification_method, got {row.verification_method!r}"
            )
        if _is_placeholder(row.verified_by_actor):
            problems.append(
                f"output_gate {row.output_gate!r} requires a non-placeholder "
                f"verified_by_actor, got {row.verified_by_actor!r}"
            )
        if parse_verified_utc(row.verified_utc) is None:
            problems.append(
                f"output_gate {row.output_gate!r} requires a parseable "
                f"verified_utc, got {row.verified_utc!r}"
            )

    return problems


_CSV_COLUMNS = [
    "document",
    "source_ref",
    "related_claims",
    "text_quality_status",
    "claim_support_status",
    "output_gate",
    "staleness_status",
    "verified_by_actor",
    "verification_method",
    "verified_utc",
    "evidence_payload_hebrew_verbatim",
    "track",
    "priority_in_track",
]


def import_csv(path: str | Path) -> list[EvidenceRow]:
    """Load a v6-style ocr_gap_register CSV into typed EvidenceRow records.

    A CSV row whose related_claims cell names more than one claim (e.g.
    "C14; C15") becomes one EvidenceRow per claim id, all other fields
    shared -- see EvidenceRow's docstring for why. Missing optional columns
    default to empty string, so this tolerates earlier register versions
    (v2/v4/v5) that lack some fields.
    """
    rows: list[EvidenceRow] = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            values = {name: raw.get(name, "").strip() for name in _CSV_COLUMNS}
            related_claims_raw = values.pop("related_claims")
            for claim_id in _split_claim_ids(related_claims_raw):
                rows.append(EvidenceRow(claim_id=claim_id, **values))
    return rows


class EvidenceStore:
    """Append-only JSONL evidence log.

    A row is never overwritten in place -- every import/update appends a new
    record, and resolve_current_state() computes the current belief from the
    full history. This is a direct implementation of the project's own
    state-sync lesson (docs/case_prep_status_model_v3_provenance.md): a
    register that mutates in place can't tell a future reader whether
    "current" reflects reality or just the last write.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, rows: Iterable[EvidenceRow]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")

    def read_all(self) -> list[EvidenceRow]:
        if not self.path.exists():
            return []
        rows: list[EvidenceRow] = []
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                rows.append(EvidenceRow(**json.loads(line)))
        return rows


@dataclass(frozen=True)
class ResolvedEvidence:
    row: EvidenceRow
    has_verified_timestamp: bool
    conflict: bool
    candidates: tuple[EvidenceRow, ...]


def resolve_current_state(
    rows: Iterable[EvidenceRow],
) -> dict[tuple[str, str], ResolvedEvidence]:
    """Compute the current belief per (document, claim_id) from row history.

    Grouping is by EvidenceRow.key(), i.e. (document identity, claim_id) --
    NOT by document alone, so two different claims about the same document
    (e.g. C14 and C15 both about one expert opinion) resolve independently
    instead of one silently overwriting the other.

    Implements the Conflict Rule from case_prep_status_model_v3_provenance.md:
    prefer the row with the latest parseable verified_utc. If a winner can't
    be determined responsibly from timestamps alone -- none of the
    candidates have a real verified_utc, or the newest timestamp is tied --
    the group is flagged as a conflict instead of silently picking one, per
    the C04 lesson that "current" without real provenance is not a fact.
    """
    groups: dict[tuple[str, str], list[EvidenceRow]] = {}
    for row in rows:
        groups.setdefault(row.key(), []).append(row)

    resolved: dict[tuple[str, str], ResolvedEvidence] = {}
    for key, group in groups.items():
        dated = [(parse_verified_utc(r.verified_utc), r) for r in group]
        with_ts = [(ts, r) for ts, r in dated if ts is not None]

        if len(group) == 1:
            only = group[0]
            resolved[key] = ResolvedEvidence(
                row=only,
                has_verified_timestamp=bool(with_ts),
                conflict=False,
                candidates=tuple(group),
            )
            continue

        if not with_ts:
            resolved[key] = ResolvedEvidence(
                row=group[-1],
                has_verified_timestamp=False,
                conflict=True,
                candidates=tuple(group),
            )
            continue

        with_ts.sort(key=lambda pair: pair[0])
        newest_ts, newest_row = with_ts[-1]
        tied = [r for ts, r in with_ts if ts == newest_ts]
        resolved[key] = ResolvedEvidence(
            row=newest_row,
            has_verified_timestamp=True,
            conflict=len(tied) > 1,
            candidates=tuple(group),
        )

    return resolved
