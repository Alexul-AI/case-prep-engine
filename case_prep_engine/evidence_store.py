from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
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

PAYLOAD_TYPES = frozenset(
    {"quote", "paraphrase", "negative_finding", "contradiction", "none"}
)

# claim_support_status -> the payload_type it asserts. Anything not listed
# here has no real payload yet ("none"), not an invalid/unknown type.
_PAYLOAD_TYPE_BY_CLAIM_SUPPORT: dict[str, str] = {
    "supported_by_quote": "quote",
    "supported_by_paraphrase": "paraphrase",
    "checked_not_supported": "negative_finding",
    "contradicted": "contradiction",
}

VERIFIED_PRECISIONS = frozenset({"instant", "date", "unknown"})

# Free-text values seen in real registers that mean "not actually filled in",
# for fields other than verified_utc (which has its own sentinel set below,
# since it also needs to reject non-timestamp text like "after-v4-snapshot").
PLACEHOLDER_TEXT_VALUES = frozenset({"", "unknown", "n/a", "—", "-"})

UNVERIFIED_TIMESTAMP_SENTINELS = PLACEHOLDER_TEXT_VALUES | frozenset(
    {"after-v4-snapshot"}
)

_CLAIM_ID_SPLIT_RE = re.compile(r"[;/]")
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_DRIVE_FILEID_PREFIX_RE = re.compile(r"^drive\s*file\s*id\s*", re.IGNORECASE)
_ID_LIKE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _is_placeholder(value: str) -> bool:
    return value.strip().lower() in PLACEHOLDER_TEXT_VALUES


def looks_like_stable_identifier(value: str) -> bool:
    """Heuristic: does this source_ref look like an id/URL, not a prose note?

    Not a strict format validator -- real registers use a few different
    conventions ("Drive fileId <id>", a bare Drive file id, a URL). The
    actual danger this guards against is prose accidentally ending up in
    an identity field: two unrelated documents both getting the free-text
    note "needs_ocr per both parallel passes" as their source_ref made
    EvidenceRow.key() treat them as the same document (a real bug found in
    this project's own register). A multi-word sentence is the signal to
    catch here, not a specific ID format to enforce.
    """
    text = value.strip()
    if not text:
        return False
    if _URL_RE.match(text):
        return True
    remainder = _DRIVE_FILEID_PREFIX_RE.sub("", text).strip()
    words = remainder.split()
    if len(words) != 1:
        return False
    return bool(_ID_LIKE_TOKEN_RE.match(words[0]))


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


def infer_verified_precision(verified_utc: str) -> str:
    """Classify a verified_utc string as 'instant', 'date', or 'unknown'.

    Policy (per docs/case_prep_status_model_v3_provenance.md discussion): a
    date-only verification like "2026-07-29" is real provenance and stays
    valid for allowed_as_quote etc. -- it is not rejected. It is *weaker*
    for ordering: resolve_current_state can't tell two same-day date-only
    verifications apart and must flag a conflict instead of picking one
    (they parse to the same UTC midnight and tie under existing sort
    logic). A future automated multi-agent sync path should require
    'instant', but nothing in this codebase enforces that yet.
    """
    if parse_verified_utc(verified_utc) is None:
        return "unknown"
    if _DATE_ONLY_RE.fullmatch(verified_utc.strip()):
        return "date"
    return "instant"


def infer_payload_type(claim_support_status: str) -> str:
    """Map a claim_support_status to the payload_type it asserts.

    "none" (not an unknown/invalid value) means this row hasn't reached a
    real payload yet -- e.g. metadata_only or not_checked.
    """
    return _PAYLOAD_TYPE_BY_CLAIM_SUPPORT.get(claim_support_status, "none")


def _split_claim_ids(raw: str) -> list[str]:
    """Split a free-text related_claims cell into individual claim ids.

    Real registers use inconsistent separators ("C14; C15", "C05 / C06").
    Falls back to a single (possibly empty) id if there's nothing to split,
    so a document not yet linked to any claim doesn't get silently dropped.
    """
    parts = [p.strip() for p in _CLAIM_ID_SPLIT_RE.split(raw)]
    parts = [p for p in parts if p]
    return parts or [raw.strip()]


def _normalize_for_hash(text: str) -> str:
    # NFC-normalize so Hebrew text extracted by different OCR/extraction
    # tools with different combining-character conventions still hashes the
    # same when the visible content is identical.
    return unicodedata.normalize("NFC", text.strip())


def compute_payload_hash(
    claim_id: str, source_ref: str, payload_type: str, hebrew_verbatim: str
) -> str:
    """Hash of a payload's identity-and-content core.

    Deliberately excludes provenance fields (actor, method, timestamp) --
    two different verification passes that land on the same quote for the
    same claim should hash identically, so this can eventually support
    dedup/agreement detection across sessions.
    """
    parts = [
        _normalize_for_hash(claim_id),
        _normalize_for_hash(source_ref),
        _normalize_for_hash(payload_type),
        _normalize_for_hash(hebrew_verbatim),
    ]
    normalized = "\x1f".join(parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidencePayload:
    """The actual evidentiary content: what was found, where, and how.

    This is the center of gravity, not an attachment on EvidenceRow -- the
    Hebrew verbatim text, its source, and its provenance travel together as
    one typed object. See docs/provenance_model_v3_addendum_inline_payload.md
    for why the canonical payload must travel with the row rather than live
    in a separate not-yet-built shared store.
    """

    payload_type: str  # quote | paraphrase | negative_finding | contradiction | none
    hebrew_verbatim: str
    source_ref: str
    claim_id: str
    verification_method: str
    verified_by_actor: str
    verified_utc: str
    verified_precision: str  # instant | date | unknown
    source_location: str = ""
    translation_ru: str = ""
    payload_hash: str = ""
    # Free-text notes about the source belong here, never in source_ref --
    # source_ref participates in EvidenceRow identity (see key()) and must
    # never be prose. This field exists specifically so a note like
    # "needs_ocr per both parallel passes" has somewhere honest to live.
    source_note: str = ""


def build_evidence_payload(
    *,
    payload_type: str,
    hebrew_verbatim: str,
    source_ref: str,
    claim_id: str,
    verification_method: str,
    verified_by_actor: str,
    verified_utc: str,
    source_location: str = "",
    translation_ru: str = "",
    source_note: str = "",
) -> EvidencePayload:
    """Construct an EvidencePayload, deriving verified_precision and payload_hash.

    Preferred over calling EvidencePayload(...) directly, so callers never
    have to remember to keep the derived fields in sync with the inputs
    themselves (a direct EvidencePayload(...) call is still useful in tests
    that deliberately want a stale/wrong hash).
    """
    return EvidencePayload(
        payload_type=payload_type,
        hebrew_verbatim=hebrew_verbatim,
        source_ref=source_ref,
        claim_id=claim_id,
        verification_method=verification_method,
        verified_by_actor=verified_by_actor,
        verified_utc=verified_utc,
        verified_precision=infer_verified_precision(verified_utc),
        source_location=source_location,
        translation_ru=translation_ru,
        payload_hash=compute_payload_hash(
            claim_id, source_ref, payload_type, hebrew_verbatim
        ),
        source_note=source_note,
    )


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
    text_quality_status: str
    claim_support_status: str
    output_gate: str
    staleness_status: str
    payload: EvidencePayload
    track: str = ""
    priority_in_track: str = ""
    row_written_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def key(self) -> tuple[str, str]:
        """Stable identity for grouping rows about "the same claim".

        (document identity, claim_id). Document identity prefers
        payload.source_ref (a Drive file id or similar) since a title can
        be re-transliterated or re-punctuated between register versions;
        falls back to the document title when source_ref is a placeholder
        like "unknown", or when it doesn't look like a real identifier at
        all -- a free-text note accidentally used as source_ref (the C01
        bug: two different documents both got the note "needs_ocr per both
        parallel passes" as their source_ref, and this identity check
        treated that as if they were the same document) must not be
        trusted as identity just because it's non-empty.
        """
        ref = self.payload.source_ref.strip()
        if ref and not _is_placeholder(ref) and looks_like_stable_identifier(ref):
            return (ref, self.payload.claim_id.strip())
        return (self.document.strip(), self.payload.claim_id.strip())


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
    payload = row.payload

    if row.text_quality_status not in TEXT_QUALITY_STATUSES:
        problems.append(f"unknown text_quality_status: {row.text_quality_status!r}")
    if row.claim_support_status not in CLAIM_SUPPORT_STATUSES:
        problems.append(f"unknown claim_support_status: {row.claim_support_status!r}")
    if row.output_gate not in OUTPUT_GATE_STATUSES:
        problems.append(f"unknown output_gate: {row.output_gate!r}")
    if payload.payload_type not in PAYLOAD_TYPES:
        problems.append(f"unknown payload_type: {payload.payload_type!r}")
    if payload.verified_precision not in VERIFIED_PRECISIONS:
        problems.append(f"unknown verified_precision: {payload.verified_precision!r}")

    # Applies unconditionally, not just for the strict output_gates below --
    # a garbled identity field breaks resolve_current_state's grouping
    # regardless of what gate the row happens to have.
    if not _is_placeholder(payload.source_ref) and not looks_like_stable_identifier(
        payload.source_ref
    ):
        problems.append(
            f"payload.source_ref {payload.source_ref!r} doesn't look like a "
            "Drive id/URL/stable identifier -- looks like a free-text note "
            "instead, which is not safe to use as document identity. Use a "
            "placeholder (e.g. '—') and put the note in payload.source_note."
        )

    allowed_gates = ALLOWED_GATES_FOR_CLAIM_SUPPORT.get(row.claim_support_status)
    if allowed_gates is not None and row.output_gate not in allowed_gates:
        problems.append(
            f"output_gate {row.output_gate!r} is not licensed by "
            f"claim_support_status {row.claim_support_status!r} "
            f"(allowed: {sorted(allowed_gates)})"
        )

    if row.output_gate in GATES_REQUIRING_PAYLOAD:
        if not payload.hebrew_verbatim.strip():
            problems.append(
                f"output_gate {row.output_gate!r} requires a non-empty "
                "payload.hebrew_verbatim"
            )
        if row.text_quality_status != "text_qa_passed":
            problems.append(
                f"output_gate {row.output_gate!r} asserts a finding from the "
                f"document's text, but text_quality_status is "
                f"{row.text_quality_status!r}, not 'text_qa_passed'"
            )

    if row.output_gate in GATES_REQUIRING_PROVENANCE:
        if _is_placeholder(payload.source_ref):
            problems.append(
                f"output_gate {row.output_gate!r} requires a real "
                f"payload.source_ref, got {payload.source_ref!r}"
            )
        if _is_placeholder(payload.verification_method):
            problems.append(
                f"output_gate {row.output_gate!r} requires a non-placeholder "
                f"payload.verification_method, got {payload.verification_method!r}"
            )
        if _is_placeholder(payload.verified_by_actor):
            problems.append(
                f"output_gate {row.output_gate!r} requires a non-placeholder "
                f"payload.verified_by_actor, got {payload.verified_by_actor!r}"
            )
        # date-only precision is deliberately still accepted here (see
        # infer_verified_precision's docstring) -- only "unknown" (no
        # parseable timestamp at all) is rejected.
        if payload.verified_precision == "unknown":
            problems.append(
                f"output_gate {row.output_gate!r} requires a parseable "
                f"payload.verified_utc, got {payload.verified_utc!r}"
            )

    if payload.payload_hash:
        expected_hash = compute_payload_hash(
            payload.claim_id, payload.source_ref, payload.payload_type,
            payload.hebrew_verbatim,
        )
        if payload.payload_hash != expected_hash:
            problems.append(
                "payload_hash does not match its own claim_id/source_ref/"
                "payload_type/hebrew_verbatim -- payload was edited after "
                "the hash was computed, or constructed inconsistently"
            )

    return problems


_CSV_COLUMNS = [
    "document",
    "source_ref",
    "source_note",
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

    Backward-compatible with the existing CSV schema -- there is no
    payload_type/verified_precision/payload_hash column, those are derived
    from claim_support_status and verified_utc via build_evidence_payload().

    A CSV row whose related_claims cell names more than one claim (e.g.
    "C14; C15") becomes one EvidenceRow per claim id, all other fields
    shared -- see EvidenceRow's docstring for why.
    """
    rows: list[EvidenceRow] = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            values = {name: raw.get(name, "").strip() for name in _CSV_COLUMNS}
            claim_support_status = values["claim_support_status"]
            for claim_id in _split_claim_ids(values["related_claims"]):
                payload = build_evidence_payload(
                    payload_type=infer_payload_type(claim_support_status),
                    hebrew_verbatim=values["evidence_payload_hebrew_verbatim"],
                    source_ref=values["source_ref"],
                    claim_id=claim_id,
                    verification_method=values["verification_method"],
                    verified_by_actor=values["verified_by_actor"],
                    verified_utc=values["verified_utc"],
                    source_note=values["source_note"],
                )
                rows.append(
                    EvidenceRow(
                        document=values["document"],
                        text_quality_status=values["text_quality_status"],
                        claim_support_status=claim_support_status,
                        output_gate=values["output_gate"],
                        staleness_status=values["staleness_status"],
                        payload=payload,
                        track=values["track"],
                        priority_in_track=values["priority_in_track"],
                    )
                )
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
                data = json.loads(line)
                data["payload"] = EvidencePayload(**data["payload"])
                rows.append(EvidenceRow(**data))
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
    candidates have a real verified_utc, or the newest timestamps tie -- the
    group is flagged as a conflict instead of silently picking one, per the
    C04 lesson that "current" without real provenance is not a fact. Two
    date-only verifications on the same calendar day parse to the same UTC
    midnight and tie under this same logic -- date precision alone cannot
    order same-day events, so that's a conflict too, not a coin flip.
    """
    groups: dict[tuple[str, str], list[EvidenceRow]] = {}
    for row in rows:
        groups.setdefault(row.key(), []).append(row)

    resolved: dict[tuple[str, str], ResolvedEvidence] = {}
    for key, group in groups.items():
        dated = [(parse_verified_utc(r.payload.verified_utc), r) for r in group]
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
