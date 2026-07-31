import contextlib
import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from case_prep_engine.cli import main

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


def _row(
    document,
    claim_id,
    source_ref="unknown",
    text_quality_status="text_qa_passed",
    claim_support_status="supported_by_quote",
    output_gate="allowed_as_quote",
    hebrew_verbatim="דוגמת טקסט לבדיקה",
    verified_utc="2026-07-29",
    source_note="",
):
    return {
        "document": document,
        "source_ref": source_ref,
        "source_note": source_note,
        "related_claims": claim_id,
        "text_quality_status": text_quality_status,
        "claim_support_status": claim_support_status,
        "output_gate": output_gate,
        "staleness_status": "fresh",
        "verified_by_actor": "tester",
        "verification_method": "manual_read",
        "verified_utc": verified_utc,
        "evidence_payload_hebrew_verbatim": hebrew_verbatim,
        "track": "test-track",
        "priority_in_track": "1",
    }


def write_register(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def run_cli(argv: list[str]):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


class CliSummarizeClaimTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.register = Path(self._tmp.name) / "register.csv"
        write_register(
            self.register,
            [
                _row("Supported doc", "C-SUP", source_ref="Drive fileId sup1"),
                _row(
                    "Supporting doc for contra claim", "C-CONTRA",
                    source_ref="Drive fileId contrasup",
                ),
                _row(
                    "Contradicting doc", "C-CONTRA", source_ref="Drive fileId contra1",
                    claim_support_status="contradicted", output_gate="allowed_as_contradiction",
                    hebrew_verbatim="הבדיקה שוללת קשר",
                ),
                _row(
                    "Negative finding doc", "C-NEG", source_ref="Drive fileId neg1",
                    claim_support_status="checked_not_supported",
                    output_gate="allowed_as_negative_finding",
                    hebrew_verbatim="לא נמצא תימוך",
                ),
                _row(
                    "Conflict doc A", "C-CONFLICT", source_ref="doc-conflict",
                    claim_support_status="not_checked", output_gate="blocked",
                    verified_utc="2026-07-29",
                ),
                _row(
                    "Conflict doc B", "C-CONFLICT", source_ref="doc-conflict",
                    claim_support_status="supported_by_quote", output_gate="allowed_as_quote",
                    verified_utc="2026-07-29",
                ),
                _row(
                    "Doc with a leaky note", "C-LEAKCHECK", source_ref="Drive fileId leak1",
                    source_note="internal note -- MUST NOT LEAK TO PROMPT",
                ),
            ],
        )

    def test_unknown_claim_id_is_a_clear_error(self):
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--claim-id", "C-DOES-NOT-EXIST",
             "--register", str(self.register), "--fake"]
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn("not found", stderr)
        self.assertEqual(stdout, "")

    def test_register_not_found_is_a_clear_error(self):
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--claim-id", "C-SUP",
             "--register", str(Path(self._tmp.name) / "missing.csv"), "--fake"]
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn("not found", stderr)

    def test_supported_claim_produces_valid_json_on_stdout(self):
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--claim-id", "C-SUP",
             "--register", str(self.register), "--fake"]
        )
        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["claim_id"], "C-SUP")
        self.assertEqual(data["status"], "supported")
        self.assertTrue(data["citations"])

    def test_contradiction_claim_includes_risk_and_must_not_say(self):
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--claim-id", "C-CONTRA",
             "--register", str(self.register), "--fake"]
        )
        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["status"], "contradicted")
        self.assertTrue(data["must_not_say"])
        self.assertTrue(data["open_risks"])

    def test_negative_finding_claim_is_not_supported(self):
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--claim-id", "C-NEG",
             "--register", str(self.register), "--fake"]
        )
        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["status"], "not_supported")

    def test_conflict_claim_exits_zero_but_status_is_never_supported(self):
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--claim-id", "C-CONFLICT",
             "--register", str(self.register), "--fake"]
        )
        # A conflict is an honest, validated answer (status="blocked"),
        # not a pipeline failure -- see cli.py's comment on this choice.
        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertNotEqual(data["status"], "supported")
        self.assertIn("unresolved conflict", stderr)

    def test_strict_makes_conflict_non_zero(self):
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--claim-id", "C-CONFLICT",
             "--register", str(self.register), "--fake", "--strict"]
        )
        self.assertNotEqual(exit_code, 0)
        data = json.loads(stdout)  # output is still produced, just a different exit code
        self.assertEqual(data["status"], "blocked")

    def test_strict_does_not_affect_a_clean_supported_result(self):
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--claim-id", "C-SUP",
             "--register", str(self.register), "--fake", "--strict"]
        )
        self.assertEqual(exit_code, 0)

    def test_list_claims_shows_claim_id_document_and_status(self):
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--register", str(self.register), "--list-claims"]
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("C-SUP", stdout)
        self.assertIn("supported", stdout)
        self.assertIn("Supported doc", stdout)
        self.assertIn("C-NEG", stdout)
        self.assertIn("not_supported", stdout)
        self.assertIn("C-CONFLICT", stdout)
        self.assertIn("conflict", stdout)

    def test_list_claims_does_not_require_claim_id_or_fake(self):
        # --list-claims must work without --claim-id/--fake at all -- it
        # never touches the completion pipeline.
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--register", str(self.register), "--list-claims"]
        )
        self.assertEqual(exit_code, 0)

    def test_missing_claim_id_without_list_claims_is_a_usage_error(self):
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--register", str(self.register), "--fake"]
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn("--claim-id", stderr)

    def test_show_prompt_does_not_leak_source_note(self):
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--claim-id", "C-LEAKCHECK",
             "--register", str(self.register), "--fake", "--show-prompt"]
        )
        self.assertEqual(exit_code, 0)
        self.assertNotIn("MUST NOT LEAK", stderr)
        self.assertNotIn("MUST NOT LEAK", stdout)

    def test_output_file_is_written_as_utf8(self):
        out_path = Path(self._tmp.name) / "out.json"
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--claim-id", "C-SUP",
             "--register", str(self.register), "--fake", "--output", str(out_path)]
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "")  # went to the file, not stdout
        content = out_path.read_text(encoding="utf-8")
        data = json.loads(content)
        self.assertEqual(data["claim_id"], "C-SUP")
        # round-trips real Hebrew, not escaped/mangled
        self.assertIn("קיים תימוך", data["summary_he"])


class ManualBridgeTests(unittest.TestCase):
    """export-claim-prompt + validate-summary: the manual bridge to any
    real LLM chat, no API integration and nothing sent anywhere by this
    codebase. Used before wiring up an automated provider, so real model
    output can inform whether the prompt/schema contract actually holds up
    -- see the commit message for why this comes before a provider PR.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.register = Path(self._tmp.name) / "register.csv"
        write_register(
            self.register,
            [_row("Supported doc", "C-SUP", source_ref="Drive fileId sup1")],
        )
        self.request_path = Path(self._tmp.name) / "request.json"
        self.prompt_path = Path(self._tmp.name) / "prompt.txt"

    def _export(self):
        return run_cli(
            ["export-claim-prompt", "--claim-id", "C-SUP", "--register", str(self.register),
             "--request-output", str(self.request_path), "--output", str(self.prompt_path)]
        )

    def test_export_writes_prompt_and_request_files(self):
        exit_code, stdout, stderr = self._export()
        self.assertEqual(exit_code, 0)
        self.assertTrue(self.request_path.exists())
        self.assertTrue(self.prompt_path.exists())
        prompt_text = self.prompt_path.read_text(encoding="utf-8")
        self.assertIn("C-SUP", prompt_text)
        request_data = json.loads(self.request_path.read_text(encoding="utf-8"))
        self.assertEqual(request_data["claim_id"], "C-SUP")
        self.assertTrue(request_data["supporting"])

    def test_export_unknown_claim_id_is_an_error(self):
        exit_code, stdout, stderr = run_cli(
            ["export-claim-prompt", "--claim-id", "C-NOPE", "--register", str(self.register),
             "--request-output", str(self.request_path)]
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn("not found", stderr)

    def test_validate_summary_accepts_a_correct_hand_written_reply(self):
        self._export()
        real_hash = json.loads(self.request_path.read_text(encoding="utf-8"))["supporting"][0]["payload_hash"]
        summary_path = Path(self._tmp.name) / "reply.json"
        summary_path.write_text(
            json.dumps({
                "claim_id": "C-SUP", "status": "supported",
                "summary_he": "יש תימוך", "citations": [real_hash],
            }),
            encoding="utf-8",
        )
        exit_code, stdout, stderr = run_cli(
            ["validate-summary", "--request", str(self.request_path), "--summary", str(summary_path)]
        )
        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["status"], "supported")

    def test_validate_summary_rejects_a_fabricated_citation(self):
        self._export()
        summary_path = Path(self._tmp.name) / "bad_reply.json"
        summary_path.write_text(
            json.dumps({
                "claim_id": "C-SUP", "status": "supported",
                "summary_he": "x", "citations": ["not-a-real-hash"],
            }),
            encoding="utf-8",
        )
        exit_code, stdout, stderr = run_cli(
            ["validate-summary", "--request", str(self.request_path), "--summary", str(summary_path)]
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn("not a real payload_hash", stderr)

    def test_validate_summary_request_file_not_found(self):
        exit_code, stdout, stderr = run_cli(
            ["validate-summary", "--request", str(Path(self._tmp.name) / "missing.json"),
             "--summary", str(Path(self._tmp.name) / "also-missing.json")]
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn("not found", stderr)

    def test_validate_summary_summary_file_not_found(self):
        self._export()
        exit_code, stdout, stderr = run_cli(
            ["validate-summary", "--request", str(self.request_path),
             "--summary", str(Path(self._tmp.name) / "missing-reply.json")]
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn("not found", stderr)

    def test_validate_uses_frozen_request_not_a_live_re_derived_one(self):
        # The whole point of saving --request-output: if the register
        # changes between export and validate, validation must still use
        # the exact snapshot the model actually saw, not a fresh
        # re-derivation that could now disagree with it.
        self._export()
        real_hash = json.loads(self.request_path.read_text(encoding="utf-8"))["supporting"][0]["payload_hash"]

        # Mutate the register after export -- add a contradiction for the
        # same claim. A live re-derivation would now see has_contradiction,
        # but the frozen request must not.
        write_register(
            self.register,
            [
                _row("Supported doc", "C-SUP", source_ref="Drive fileId sup1"),
                _row(
                    "New contradicting doc", "C-SUP", source_ref="Drive fileId newcontra",
                    claim_support_status="contradicted", output_gate="allowed_as_contradiction",
                ),
            ],
        )

        summary_path = Path(self._tmp.name) / "reply.json"
        summary_path.write_text(
            json.dumps({
                "claim_id": "C-SUP", "status": "supported",
                "summary_he": "יש תימוך", "citations": [real_hash],
            }),
            encoding="utf-8",
        )
        exit_code, stdout, stderr = run_cli(
            ["validate-summary", "--request", str(self.request_path), "--summary", str(summary_path)]
        )
        # Still valid against the frozen (pre-mutation) request.
        self.assertEqual(exit_code, 0)


class DemoRegisterTests(unittest.TestCase):
    """Exercises the actual examples/demo_register.csv shipped in the repo
    -- the file the README quickstart command points at. No personal data,
    safe for anyone who clones the repo (unlike data/, which is gitignored
    and only exists locally for whoever built their own register).
    """

    DEMO_REGISTER = Path(__file__).resolve().parents[1] / "examples" / "demo_register.csv"

    def test_demo_register_exists_and_is_tracked(self):
        self.assertTrue(self.DEMO_REGISTER.exists())

    def test_demo_register_has_the_three_advertised_scenarios(self):
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--register", str(self.DEMO_REGISTER), "--list-claims"]
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("DEMO_C01", stdout)
        self.assertIn("supported", stdout)
        self.assertIn("DEMO_C02", stdout)
        self.assertIn("not_supported", stdout)
        self.assertIn("DEMO_C03", stdout)
        self.assertIn("blocked", stdout)

    def test_readme_quickstart_command_works(self):
        exit_code, stdout, stderr = run_cli(
            ["summarize-claim", "--claim-id", "DEMO_C01",
             "--register", str(self.DEMO_REGISTER), "--fake"]
        )
        self.assertEqual(exit_code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["claim_id"], "DEMO_C01")
        self.assertEqual(data["status"], "supported")


class CliSubprocessSmokeTest(unittest.TestCase):
    """One real `python -m case_prep_engine ...` invocation, proving the
    actual entry point (__main__.py, console argv parsing, process exit
    code) works -- not just the in-process main() calls above.
    """

    def test_real_subprocess_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            register = Path(tmp) / "register.csv"
            write_register(
                register, [_row("Supported doc", "C-SUP", source_ref="Drive fileId sup1")]
            )
            repo_root = Path(__file__).resolve().parents[1]
            result = subprocess.run(
                [
                    sys.executable, "-m", "case_prep_engine", "summarize-claim",
                    "--claim-id", "C-SUP", "--register", str(register), "--fake",
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["status"], "supported")


if __name__ == "__main__":
    unittest.main()
