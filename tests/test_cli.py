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
