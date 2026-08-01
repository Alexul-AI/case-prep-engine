from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .evidence_store import EvidenceRow, resolve_current_state

# output_gate values meaning "this evidence was actually checked" (support,
# contradiction, or negative finding), as opposed to not-yet-checked
# (blocked / blocked_pending_evidence_import / allowed_as_unverified).
_CHECKED_GATES = frozenset(
    {
        "allowed_as_quote",
        "allowed_as_synthesis",
        "allowed_as_negative_finding",
        "allowed_as_contradiction",
    }
)

DATE_PRECISIONS = frozenset({"day", "month", "year", "unknown"})
DATE_SOURCES = frozenset({"filename", "payload", "manual", "unknown"})
EVENT_TYPES = frozenset(
    {
        "service_event",
        "diagnosis",
        "committee",
        "expert_opinion",
        "treatment",
        "functional_change",
        "unknown",
    }
)
EVIDENCE_STATUSES = frozenset(
    {"verified_events", "date_unresolved", "conflicts", "blocked_or_unverified"}
)

_DATE_DAY_RE = re.compile(r"(\d{1,2})[._](\d{1,2})[._](\d{4})")
_DATE_MONTH_RE = re.compile(r"(\d{1,2})[._](\d{4})")
_DATE_YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")


def extract_date_from_document_title(title: str) -> tuple[str, str]:
    """Best-effort (event_date, date_precision) from a free-text title.

    Assumes DD.MM.YYYY / DD_MM_YYYY (Israeli convention -- matches every
    real title in this register, e.g. "22.06.2025" = 2025-06-22, confirmed
    against the real Drive filename for that document) over MM.DD.YYYY.
    This is a real, consequential assumption for this specific dataset,
    not a general-purpose date parser.

    Returns ("", "unknown") when nothing date-like is found. This is a
    heuristic over a title string, not a verified fact -- callers must
    track date_source alongside the result (see TimelineEvent) and never
    treat an extracted title-date as equivalent to a date confirmed from
    the document's actual content.
    """
    day_match = _DATE_DAY_RE.search(title)
    if day_match:
        day, month, year = (int(g) for g in day_match.groups())
        if 1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2100:
            return f"{year:04d}-{month:02d}-{day:02d}", "day"

    month_match = _DATE_MONTH_RE.search(title)
    if month_match:
        month, year = (int(g) for g in month_match.groups())
        if 1 <= month <= 12 and 1900 <= year <= 2100:
            return f"{year:04d}-{month:02d}", "month"

    year_match = _DATE_YEAR_RE.search(title)
    if year_match:
        year = int(year_match.group(1))
        if 1900 <= year <= 2100:
            return f"{year:04d}", "year"

    return "", "unknown"


# Keyword -> event_type, checked in order (first match wins). A rough
# heuristic over Hebrew/English document titles, not a reliable classifier
# -- "unknown" is a legitimate, expected outcome, not a gap to guess past.
_EVENT_TYPE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("פרוטוקול ועדה", "committee"),
    ("תמלול ועדה", "committee"),
    ("ועדה", "committee"),
    ("חוות דעת", "expert_opinion"),
    ("אבחון", "diagnosis"),
    ("אבחנה", "diagnosis"),
    ("סיכום אשפוז", "service_event"),
    ("injury report", "service_event"),
    ("פציעה", "service_event"),
    ("סיכום ביקור", "treatment"),
    ("מעקב", "treatment"),
    ("טיפול", "treatment"),
)


def infer_event_type(document: str) -> str:
    """Rough keyword classification of a document title into an event_type.

    Deterministic and cheap, not a reliable classifier -- a first pass,
    not a substitute for a human (or a later LLM layer) actually reading
    the document. "unknown" is the correct, honest result for anything
    that doesn't match, not something to guess past.
    """
    for keyword, event_type in _EVENT_TYPE_KEYWORDS:
        if keyword in document:
            return event_type
    return "unknown"


@dataclass(frozen=True)
class TimelineEvent:
    """One resolved (case, track, evidence_id) entry, placed in time.

    event_date/date_precision/date_source describe *how confidently* this
    event is placed in time -- event_date being non-empty is not itself a
    guarantee of anything. date_source is currently always "filename"
    (extracted from the document title -- the only date signal that
    exists anywhere in this data model right now) or "unknown". "payload"
    and "manual" are reserved for when a real structured event-date field
    or a human annotation exists to populate them from -- not implemented
    yet, so build_timeline() never produces them.
    """

    event_date: str
    date_precision: str  # day | month | year | unknown
    date_source: str  # filename | payload | manual | unknown
    event_type: str  # service_event | diagnosis | committee | expert_opinion | treatment | functional_change | unknown
    document: str
    source_ref: str
    case_id: str
    track_id: str
    claim_id: str
    payload_hash: str
    evidence_status: str  # verified_events | date_unresolved | conflicts | blocked_or_unverified


def build_timeline(rows: Iterable[EvidenceRow]) -> tuple[TimelineEvent, ...]:
    """Build a chronological read-model over resolved evidence.

    A parallel projection to build_evidence_matrix(), not built on top of
    it: the matrix answers "what do we know about claim X", the timeline
    answers "what happened when" -- two different groupings over the same
    resolve_current_state() output, neither derived from the other.

    One TimelineEvent per resolved (case, track, evidence_id) entry -- a
    document supporting two claims (e.g. Greenhouse backing both C14 and
    C15) produces two events, same date, different claim_id, matching
    EvidenceRow's own atomicity; so does a single document/claim backed by
    two genuinely different quotes (two distinct evidence_ids). The same
    claim_id in a different case or track produces its own separate event
    too, never merged.

    evidence_status bucketing precedence (checked in this order):
    1. conflict -> "conflicts", regardless of date.
    2. output_gate not in _CHECKED_GATES -> "blocked_or_unverified",
       regardless of date -- there's little value debating an unknown date
       for evidence that hasn't even been checked yet.
    3. no date could be extracted -> "date_unresolved".
    4. otherwise -> "verified_events".

    Sorted chronologically by event_date; events with no extractable date
    sort last, not first (an empty string must not look like "the
    earliest date").
    """
    resolved = resolve_current_state(rows)

    events: list[TimelineEvent] = []
    for entry in resolved.values():
        row = entry.row
        case_id, track_id, claim_id = row.case_id, row.track_id, row.claim_id
        document = row.document
        event_date, date_precision = extract_date_from_document_title(document)
        date_source = "filename" if date_precision != "unknown" else "unknown"

        if entry.conflict:
            evidence_status = "conflicts"
        elif row.output_gate not in _CHECKED_GATES:
            evidence_status = "blocked_or_unverified"
        elif date_precision == "unknown":
            evidence_status = "date_unresolved"
        else:
            evidence_status = "verified_events"

        events.append(
            TimelineEvent(
                event_date=event_date,
                date_precision=date_precision,
                date_source=date_source,
                event_type=infer_event_type(document),
                document=document,
                source_ref=row.payload.source_ref,
                case_id=case_id,
                track_id=track_id,
                claim_id=claim_id,
                payload_hash="" if entry.conflict else row.payload.payload_hash,
                evidence_status=evidence_status,
            )
        )

    events.sort(key=lambda e: (e.event_date == "", e.event_date))
    return tuple(events)
