from __future__ import annotations

import json
from typing import Callable, Protocol

from .claim_summary import (
    ClaimSummary,
    ClaimSummaryRequest,
    build_claim_summary_prompt,
    validate_claim_summary,
)


class LLMResponseError(Exception):
    """Raised when a model's response cannot be trusted as a ClaimSummary.

    Carries the raw response text and the specific validation problems (if
    any), so a caller can log/inspect what the model actually said instead
    of seeing only "something went wrong".
    """

    def __init__(
        self, message: str, *, raw_response: str = "", problems: tuple[str, ...] = ()
    ):
        super().__init__(message)
        self.raw_response = raw_response
        self.problems = problems


_REQUIRED_STRING_FIELDS = ("claim_id", "status", "summary_he")
_ARRAY_OF_STRING_FIELDS = ("allowed_uses", "must_not_say", "citations", "open_risks")


def parse_claim_summary_json(raw: str, request: ClaimSummaryRequest) -> ClaimSummary:
    """Parse and validate a model's raw JSON text into a trusted ClaimSummary.

    This -- not build_claim_summary_prompt() -- is the actual contract
    enforcement point: asking a model nicely in the prompt doesn't make it
    comply. Every failure mode raises LLMResponseError rather than
    returning a partially-trusted object: invalid JSON, a missing required
    field, or any problem validate_claim_summary() finds once the object
    is built (fabricated citations, an unearned status, a dropped negative
    finding, unbacked causal wording, missing must_not_say alongside a
    real risk).

    Deliberately lenient about which fields are structurally required
    (claim_id/status/summary_he only -- everything else defaults to
    empty) versus which are semantically required. citations being empty,
    for example, isn't rejected here; if the request demands a citation
    (e.g. a negative finding must be cited), validate_claim_summary() will
    catch that on its own. Two different kinds of "missing" shouldn't be
    special-cased twice.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(
            f"response is not valid JSON: {exc}", raw_response=raw
        ) from exc

    if not isinstance(data, dict):
        raise LLMResponseError("response JSON must be an object", raw_response=raw)

    missing = [name for name in _REQUIRED_STRING_FIELDS if name not in data]
    if missing:
        raise LLMResponseError(
            f"response JSON is missing required field(s): {missing}", raw_response=raw
        )

    # Strict JSON types, not Python coercion: str(x)/tuple(x) would turn a
    # list into its repr, or a string ("not-an-array") into a tuple of its
    # characters, and then let that satisfy the "non-empty" risk checks in
    # validate_claim_summary().
    string_values: dict[str, str] = {}
    for name in (*_REQUIRED_STRING_FIELDS, "summary_ru"):
        value = data.get(name, "" if name == "summary_ru" else None)
        if not isinstance(value, str):
            raise LLMResponseError(
                f"response JSON field {name!r} must be a string, got {type(value).__name__}",
                raw_response=raw,
            )
        string_values[name] = value

    array_values: dict[str, tuple[str, ...]] = {}
    for name in _ARRAY_OF_STRING_FIELDS:
        value = data.get(name, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise LLMResponseError(
                f"response JSON field {name!r} must be an array of strings",
                raw_response=raw,
            )
        array_values[name] = tuple(value)

    summary = ClaimSummary(
        claim_id=string_values["claim_id"],
        status=string_values["status"],
        summary_he=string_values["summary_he"],
        summary_ru=string_values["summary_ru"],
        allowed_uses=array_values["allowed_uses"],
        must_not_say=array_values["must_not_say"],
        citations=array_values["citations"],
        open_risks=array_values["open_risks"],
    )

    problems = validate_claim_summary(summary, request)
    if problems:
        raise LLMResponseError(
            "response failed claim-summary validation",
            raw_response=raw,
            problems=tuple(problems),
        )

    return summary


class ClaimSummaryLLM(Protocol):
    def summarize_claim(self, request: ClaimSummaryRequest) -> ClaimSummary: ...


class MockClaimSummaryLLM:
    """Test double for ClaimSummaryLLM -- returns a fixed or computed response.

    Bypasses the prompt/JSON round-trip entirely (unlike
    JsonOnlyClaimSummaryLLM below), for tests that want to inject a
    ClaimSummary directly. Does no validation itself, matching a real LLM
    in that respect -- whatever it returns still has to survive
    validate_claim_summary() downstream, same as any other implementation.
    """

    def __init__(
        self, respond: ClaimSummary | Callable[[ClaimSummaryRequest], ClaimSummary]
    ):
        self._respond = respond

    def summarize_claim(self, request: ClaimSummaryRequest) -> ClaimSummary:
        if callable(self._respond):
            return self._respond(request)
        return self._respond


class JsonOnlyClaimSummaryLLM:
    """Provider-agnostic ClaimSummaryLLM: prompt -> raw text -> parsed+validated.

    No network call happens in this class, and none is added by this PR --
    per plan, the next risk to close is the contract (can a model return a
    well-formed but illegal object), not generation itself. completion_fn
    is the seam where a real provider plugs in later: a future
    OpenAIClaimSummaryLLM (or Anthropic, etc.) is just this class with
    completion_fn wired to a real API call. Not built as its own class
    yet, deliberately -- its exact shape (SDK, model, retries/timeouts)
    depends on requirements not yet decided, and guessing at that shape
    now would likely just be redone once those are real.
    """

    def __init__(self, completion_fn: Callable[[str], str]):
        self._completion_fn = completion_fn

    def summarize_claim(self, request: ClaimSummaryRequest) -> ClaimSummary:
        prompt = build_claim_summary_prompt(request)
        raw = self._completion_fn(prompt)
        return parse_claim_summary_json(raw, request)
