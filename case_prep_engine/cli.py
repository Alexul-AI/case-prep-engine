from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Callable

from .claim_summary import (
    ClaimSummaryRequest,
    build_claim_summary_prompt,
    build_claim_summary_request,
)
from .evidence_matrix import ClaimMatrixEntry, build_evidence_matrix
from .evidence_store import import_csv
from .llm_adapter import JsonOnlyClaimSummaryLLM, LLMResponseError

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REGISTER_NOT_FOUND = 3
EXIT_CLAIM_NOT_FOUND = 4
EXIT_INVALID_RESPONSE = 5
EXIT_STRICT_BLOCKED = 6  # --strict only: status="blocked" (conflict or nothing checked)


def make_fake_completion(request: ClaimSummaryRequest) -> Callable[[str], str]:
    """Deterministic stand-in for a real LLM call.

    Captures `request` (the exact object build_claim_summary_prompt() was
    given) in a closure, so the returned function still matches
    JsonOnlyClaimSummaryLLM's completion_fn signature (prompt: str) -> str
    -- this exercises the *real* prompt -> completion -> parse -> validate
    pipeline end to end, not a shortcut around it.

    Behaves differently per scenario on purpose (conflict -> blocked,
    contradiction -> contradicted, negative finding -> not_supported,
    supporting -> supported, nothing checked -> blocked) -- a fake that
    always returned the same safe-looking answer wouldn't actually
    smoke-test validate_claim_summary()'s harder rules (must_not_say,
    open_risks, the conflict-forbids-supported rule). Summary text is
    fixed, generic prose deliberately free of quote marks and
    causal-language keywords, so it can never accidentally trip the
    quote-verbatim or causal-wording checks regardless of what the real
    payload text says. Every "blocked" path also fills open_risks with a
    concrete reason -- not required by validate_claim_summary() (neither
    branch has a contradiction/conflict/negative_finding by itself needing
    it), but a "blocked" result with no stated reason is a bad UX result
    even when it's a technically valid one.
    """

    def _fake(prompt: str) -> str:
        del prompt  # a real provider reads this; the fake reads `request` directly

        if request.has_unresolved_conflict:
            payload = {
                "status": "blocked",
                "summary_he": "קיים קונפליקט בלתי פתור לגבי תביעה זו; לא ניתן לקבוע מסקנה בשלב זה.",
                "summary_ru": "По этому утверждению есть неразрешённый конфликт; сделать вывод пока нельзя.",
                "citations": [],
                "must_not_say": ["claim is settled or resolved"],
                "open_risks": ["unresolved conflict on this claim"],
            }
        elif request.contradictions:
            hashes = [p.payload_hash for p in (*request.contradictions, *request.supporting)]
            payload = {
                "status": "contradicted",
                "summary_he": "קיימת ראיה שנבדקה הסותרת את התביעה.",
                "summary_ru": "Есть проверенное доказательство, противоречащее утверждению.",
                "citations": hashes,
                "must_not_say": ["claim is supported without qualification"],
                "open_risks": ["a contradicting document exists for this claim"],
            }
        elif request.negative_findings:
            hashes = [p.payload_hash for p in request.negative_findings]
            payload = {
                "status": "not_supported",
                "summary_he": "הראיה נבדקה ולא נמצא בה תימוך לתביעה.",
                "summary_ru": "Доказательство проверено, подтверждения утверждению не найдено.",
                "citations": hashes,
                "must_not_say": ["claim is supported"],
                "open_risks": [],
            }
        elif request.supporting:
            hashes = [p.payload_hash for p in request.supporting]
            payload = {
                "status": "supported",
                "summary_he": "קיים תימוך בראיה שנבדקה לתביעה זו.",
                "summary_ru": "В проверенном доказательстве есть подтверждение этому утверждению.",
                "citations": hashes,
                "must_not_say": [],
                "open_risks": [],
            }
        else:
            payload = {
                "status": "blocked",
                "summary_he": "לא קיימת עדיין ראיה שנבדקה לתביעה זו.",
                "summary_ru": "Проверенных доказательств по этому утверждению пока нет.",
                "citations": [],
                "must_not_say": [],
                "open_risks": ["no checked evidence exists yet for this claim"],
            }

        payload["claim_id"] = request.claim_id
        payload["allowed_uses"] = []
        return json.dumps(payload, ensure_ascii=False)

    return _fake


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m case_prep_engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summarize = subparsers.add_parser(
        "summarize-claim",
        help="run one claim through the full register -> ... -> ClaimSummary "
        "pipeline, or list available claim ids with --list-claims",
    )
    summarize.add_argument(
        "--claim-id", help="required unless --list-claims is passed"
    )
    summarize.add_argument("--register", required=True, type=Path)
    summarize.add_argument(
        "--fake",
        action="store_true",
        help="use the deterministic fake completion -- required (unless "
        "--list-claims), since no real LLM provider is wired up yet",
    )
    summarize.add_argument(
        "--show-prompt",
        action="store_true",
        help="print the exact prompt sent to the completion function to stderr",
    )
    summarize.add_argument(
        "--output", type=Path, default=None, help="write JSON to this path (UTF-8) instead of stdout"
    )
    summarize.add_argument(
        "--list-claims",
        action="store_true",
        help="list every claim_id in the register with its document and "
        "status bucket, instead of summarizing one claim",
    )
    summarize.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when the result is status='blocked' (conflict "
        "or nothing checked yet) -- for CI/batch use. Default is exit 0 "
        "for any validated result, including 'blocked'.",
    )

    return parser


_LIST_CLAIMS_BUCKETS: tuple[tuple[str, str], ...] = (
    ("supporting", "supported"),
    ("negative_findings", "not_supported"),
    ("contradictions", "contradicted"),
    ("unresolved", "blocked"),
    ("conflicts", "conflict"),
)


def _run_list_claims(matrix: dict[str, ClaimMatrixEntry]) -> int:
    if not matrix:
        print("(register has no claims)", file=sys.stderr)
        return EXIT_OK
    print(f"{'claim_id':<14} {'status':<14} document")
    for claim_id in sorted(matrix):
        entry = matrix[claim_id]
        for attr, label in _LIST_CLAIMS_BUCKETS:
            for resolved in getattr(entry, attr):
                print(f"{claim_id:<14} {label:<14} {resolved.row.document}")
    return EXIT_OK


def _run_summarize_claim(args: argparse.Namespace) -> int:
    if not args.register.exists():
        print(f"error: register file not found: {args.register}", file=sys.stderr)
        return EXIT_REGISTER_NOT_FOUND

    rows = import_csv(args.register)
    matrix = build_evidence_matrix(rows)

    if args.list_claims:
        return _run_list_claims(matrix)

    if not args.claim_id:
        print("error: --claim-id is required unless --list-claims is passed", file=sys.stderr)
        return EXIT_USAGE
    if not args.fake:
        print("error: --fake is required -- no real LLM provider is wired up yet", file=sys.stderr)
        return EXIT_USAGE

    entry = matrix.get(args.claim_id)
    if entry is None:
        known = ", ".join(sorted(matrix)) or "(none)"
        print(
            f"error: claim_id {args.claim_id!r} not found in register. "
            f"Known claim ids: {known}",
            file=sys.stderr,
        )
        return EXIT_CLAIM_NOT_FOUND

    request = build_claim_summary_request(entry)
    prompt = build_claim_summary_prompt(request)

    if args.show_prompt:
        print(prompt, file=sys.stderr)
        print("---", file=sys.stderr)

    llm = JsonOnlyClaimSummaryLLM(make_fake_completion(request))
    try:
        summary = llm.summarize_claim(request)
    except LLMResponseError as exc:
        print(f"error: completion response failed validation: {exc}", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        if exc.raw_response:
            print(f"raw response was: {exc.raw_response}", file=sys.stderr)
        return EXIT_INVALID_RESPONSE

    if summary.status == "blocked":
        if request.has_unresolved_conflict:
            reason = "an unresolved conflict"
        else:
            reason = "; ".join(summary.open_risks) or "no checked evidence yet"
        print(f"note: claim {args.claim_id!r} is blocked -- {reason}", file=sys.stderr)

    output_json = json.dumps(dataclasses.asdict(summary), ensure_ascii=False, indent=2)

    if args.output:
        args.output.write_text(output_json, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(output_json)

    # Default exit 0 covers any successfully validated ClaimSummary,
    # including status="blocked" -- that is the system correctly
    # reporting an honest, validated answer, not a pipeline failure. Only
    # --strict (opt-in, for CI/batch callers that specifically want
    # "blocked" treated as actionable) makes it non-zero.
    if args.strict and summary.status == "blocked":
        return EXIT_STRICT_BLOCKED
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    # This CLI's entire job is printing Hebrew JSON. A real console on
    # Windows commonly defaults stdout/stderr to a legacy codepage (e.g.
    # cp1252) that cannot encode Hebrew at all -- print() would crash with
    # UnicodeEncodeError before any real bug in the pipeline gets a chance
    # to matter (found by the subprocess smoke test: it passed in-process,
    # where stdout is already an io.StringIO, and crashed for real).
    # reconfigure() only exists on TextIOWrapper-backed streams (a real
    # console/file), not on things like io.StringIO used in tests, hence
    # the guard.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if args.command == "summarize-claim":
        return _run_summarize_claim(args)
    parser.error(f"unknown command: {args.command}")
    return EXIT_USAGE  # pragma: no cover -- parser.error() already exits


if __name__ == "__main__":
    sys.exit(main())
